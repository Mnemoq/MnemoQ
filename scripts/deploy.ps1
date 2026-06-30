param(
    [switch]$DryRun,
    [switch]$SkipTests,
    [string]$CanaryProject = ""
)

$ErrorActionPreference = "Stop"

$DevRoot = Split-Path -Parent $PSScriptRoot
$LiveEngine = "$env:USERPROFILE\.agent-memory\engine"
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"

Write-Host "Validating dev project..."
$requiredFiles = @("src\mnemoq\cli.py", "src\mnemoq\scaffold.py", "src\mnemoq\update.py", "src\mnemoq\engine_version.py", "src\mnemoq\shim.py", "pyproject.toml", "VERSION")
foreach ($f in $requiredFiles) {
    if (-not (Test-Path "$DevRoot\$f")) {
        Write-Error "Missing required file: $f"
        exit 1
    }
}

if ($SkipTests) {
    Write-Host "Skipping tests (-SkipTests)"
} else {
    Write-Host "Running tests with coverage..."
    Push-Location $DevRoot
    $testOutput = python -m pytest tests/ --tb=short --cov=src --cov-report=term-missing *>&1
    $testExitCode = $LASTEXITCODE
    Pop-Location

    # Check test exit code
    if ($testExitCode -ne 0) {
        Write-Error "Tests failed (exit code $testExitCode). Aborting deploy."
        Write-Host $testOutput
        exit 1
    }

    # Extract coverage percentage from TOTAL line
    # Format: "TOTAL                      499   1197    29%"
    # Use line-by-line matching for robustness
    $totalLine = $testOutput | Where-Object { $_ -match "^TOTAL\s+" } | Select-Object -First 1
    if ($totalLine -match "(\d+)\s*%") {
        $currentCoverage = [int]$Matches[1]
        Write-Host "  Coverage: $currentCoverage%"

        # Read baseline
        $baselinePath = "$DevRoot\coverage-baseline.txt"
        if (Test-Path $baselinePath) {
            try {
                $baselineContent = Get-Content $baselinePath
                $baselineTotalLine = $baselineContent | Where-Object { $_ -match "^TOTAL\s+" } | Select-Object -First 1
                if ($baselineTotalLine -match "(\d+)\s*%") {
                    $baselineCoverage = [int]$Matches[1]
                    $regression = $baselineCoverage - $currentCoverage

                    if ($regression -gt 5) {
                        Write-Error "Coverage regression: $baselineCoverage% -> $currentCoverage% ($regression point drop)"
                        Write-Error "Deploy aborted. Improve tests before deploying."
                        exit 1
                    } elseif ($regression -gt 0) {
                        Write-Host "  Coverage: $currentCoverage% (baseline: $baselineCoverage%, regression: $regression points - within tolerance)"
                    } else {
                        $gain = -$regression
                        Write-Host "  Coverage: $currentCoverage% (baseline: $baselineCoverage%, gain: +$gain points)"
                    }
                } else {
                    Write-Warning "Baseline file exists but TOTAL line not found. Skipping coverage gate."
                }
            } catch {
                Write-Warning "Could not read baseline file: $_. Skipping coverage gate."
            }
        } else {
            Write-Host "  No baseline found. Run: pytest --cov=src > coverage-baseline.txt"
        }
    } else {
        Write-Warning "Could not extract coverage from test output. Skipping coverage gate."
    }
}

$BackupDir = "$LiveEngine\backups\$Timestamp"
if (-not $DryRun) {
    Write-Host "Backing up live engine to $BackupDir..."
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
    Get-ChildItem "$LiveEngine\*.py" -ErrorAction SilentlyContinue | ForEach-Object { Copy-Item $_.FullName "$BackupDir\" -Force }
    if (Test-Path "$LiveEngine\VERSION") { Copy-Item "$LiveEngine\VERSION" "$BackupDir\" -Force }
    if (Test-Path "$LiveEngine\templates") { Copy-Item "$LiveEngine\templates" "$BackupDir\templates" -Recurse -Force }
    if (Test-Path "$LiveEngine\mnemoq") { Copy-Item "$LiveEngine\mnemoq" "$BackupDir\mnemoq" -Recurse -Force }
}

Write-Host "Copying dev files to live engine..."
# ponytail: mnemoq package + thin backward-compat wrappers around the new entry points.
# Wrappers let existing shim-based projects keep exec'ing ~/.agent-memory/engine/filter.py.
$filterWrapper = @'
#!/usr/bin/env python3
"""Backward-compat wrapper - delegates to installed mnemoq package."""
import sys
from mnemoq.cli import main
if __name__ == "__main__":
    sys.exit(main())
'@

$scaffoldWrapper = @'
#!/usr/bin/env python3
"""Backward-compat wrapper - delegates to installed mnemoq package."""
import sys
from mnemoq.scaffold import main
if __name__ == "__main__":
    sys.exit(main())
'@

$updateWrapper = @'
#!/usr/bin/env python3
"""Backward-compat wrapper - delegates to installed mnemoq package."""
import sys
from mnemoq.update import main
if __name__ == "__main__":
    sys.exit(main())
'@

$wrappers = @(
    @{ Name = "filter.py";   Body = $filterWrapper },
    @{ Name = "scaffold.py"; Body = $scaffoldWrapper },
    @{ Name = "update.py";  Body = $updateWrapper }
)

if ($DryRun) {
    Write-Host "  [DRY-RUN] Would copy: src\mnemoq\ -> $LiveEngine\mnemoq\"
    Write-Host "  [DRY-RUN] Would write wrappers: filter.py, scaffold.py, update.py"
    Write-Host "  [DRY-RUN] Would copy: VERSION -> $LiveEngine\VERSION"
    Write-Host "  [DRY-RUN] Would copy: templates\ -> $LiveEngine\templates\"
} else {
    # Fresh copy of the mnemoq package
    if (Test-Path "$LiveEngine\mnemoq") { Remove-Item "$LiveEngine\mnemoq" -Recurse -Force }
    Copy-Item "$DevRoot\src\mnemoq" "$LiveEngine\mnemoq" -Recurse -Force
    Write-Host "  Copied: mnemoq/"

    # Clean stale paths the current layout has absorbed — incl. the pre-rename agent_memory/ package dir
    foreach ($stale in @("agent_memory", "engine", "engine_version.py", "shim.py")) {
        $stalePath = Join-Path $LiveEngine $stale
        if (Test-Path $stalePath) { Remove-Item $stalePath -Recurse -Force }
    }

    foreach ($w in $wrappers) {
        Set-Content -Path "$LiveEngine\$($w.Name)" -Value $w.Body -Encoding utf8
        Write-Host "  Wrote wrapper: $($w.Name)"
    }

    Copy-Item "$DevRoot\VERSION" "$LiveEngine\VERSION" -Force
    Write-Host "  Copied: VERSION"
    Copy-Item "$DevRoot\templates" "$LiveEngine\templates" -Recurse -Force
    Write-Host "  Copied: templates/"
}

Write-Host "Verifying live engine..."
if (-not $DryRun) {
    $result = python "$LiveEngine\filter.py" --stats 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Verification failed. Restoring from backup..."
        if (Test-Path "$BackupDir\mnemoq") { Copy-Item "$BackupDir\mnemoq" "$LiveEngine\mnemoq" -Recurse -Force }
        Get-ChildItem "$BackupDir\*.py" -ErrorAction SilentlyContinue | ForEach-Object { Copy-Item $_.FullName "$LiveEngine\" -Force }
        if (Test-Path "$BackupDir\VERSION") { Copy-Item "$BackupDir\VERSION" "$LiveEngine\" -Force }
        if (Test-Path "$BackupDir\templates") { Copy-Item "$BackupDir\templates" "$LiveEngine\templates" -Recurse -Force }
        exit 1
    }
    Write-Host "  Verification passed."
}

if ($CanaryProject) {
    Write-Host "Updating canary project: $CanaryProject"
    if (-not $DryRun) {
        python "$LiveEngine\update.py" --project "$CanaryProject" --yes
    }
} else {
    Write-Host "Updating all registered projects..."
    if (-not $DryRun) {
        python "$LiveEngine\update.py" --yes
    }
}

Write-Host "`nDeploy complete."
if ($DryRun) {
    Write-Host "(Dry-run mode - no changes made)"
}