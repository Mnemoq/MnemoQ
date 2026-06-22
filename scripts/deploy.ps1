param(
    [switch]$DryRun,
    [string]$CanaryProject = ""
)

$ErrorActionPreference = "Stop"

$DevRoot = Split-Path -Parent $PSScriptRoot
$LiveEngine = "$env:USERPROFILE\.agent-memory\engine"
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"

Write-Host "Validating dev project..."
$requiredFiles = @("src\filter.py", "src\scaffold.py", "src\update.py", "src\engine_version.py", "src\shim.py", "VERSION")
foreach ($f in $requiredFiles) {
    if (-not (Test-Path "$DevRoot\$f")) {
        Write-Error "Missing required file: $f"
        exit 1
    }
}

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

$BackupDir = "$LiveEngine\backups\$Timestamp"
if (-not $DryRun) {
    Write-Host "Backing up live engine to $BackupDir..."
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
    Get-ChildItem "$LiveEngine\*.py" -ErrorAction SilentlyContinue | ForEach-Object { Copy-Item $_.FullName "$BackupDir\" -Force }
    if (Test-Path "$LiveEngine\VERSION") { Copy-Item "$LiveEngine\VERSION" "$BackupDir\" -Force }
    if (Test-Path "$LiveEngine\templates") { Copy-Item "$LiveEngine\templates" "$BackupDir\templates" -Recurse -Force }
    if (Test-Path "$LiveEngine\engine") { Copy-Item "$LiveEngine\engine" "$BackupDir\engine" -Recurse -Force }
}

Write-Host "Copying dev files to live engine..."
$filesToCopy = @(
    @{ Src = "src\filter.py"; Dst = "filter.py" },
    @{ Src = "src\scaffold.py"; Dst = "scaffold.py" },
    @{ Src = "src\update.py"; Dst = "update.py" },
    @{ Src = "src\engine_version.py"; Dst = "engine_version.py" },
    @{ Src = "src\shim.py"; Dst = "shim.py" },
    @{ Src = "VERSION"; Dst = "VERSION" }
)

foreach ($f in $filesToCopy) {
    $srcPath = "$DevRoot\$($f.Src)"
    $dstPath = "$LiveEngine\$($f.Dst)"
    if ($DryRun) {
        Write-Host "  [DRY-RUN] Would copy: $srcPath -> $dstPath"
    } else {
        Copy-Item $srcPath $dstPath -Force
        Write-Host "  Copied: $($f.Dst)"
    }
}

if ($DryRun) {
    Write-Host "  [DRY-RUN] Would copy: templates/ -> $LiveEngine\templates\"
    Write-Host "  [DRY-RUN] Would copy: engine/ -> $LiveEngine\engine\"
} else {
    Copy-Item "$DevRoot\templates" "$LiveEngine\templates" -Recurse -Force
    Write-Host "  Copied: templates/"
    Copy-Item "$DevRoot\src\engine" "$LiveEngine\engine" -Recurse -Force
    Write-Host "  Copied: engine/"
}

Write-Host "Verifying live engine..."
if (-not $DryRun) {
    $result = python "$LiveEngine\filter.py" --stats 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Verification failed. Restoring from backup..."
        Copy-Item "$BackupDir\*.py" "$LiveEngine\" -Force
        Copy-Item "$BackupDir\VERSION" "$LiveEngine\" -Force
        if (Test-Path "$BackupDir\templates") { Copy-Item "$BackupDir\templates" "$LiveEngine\templates" -Recurse -Force }
        if (Test-Path "$BackupDir\engine") { Copy-Item "$BackupDir\engine" "$LiveEngine\engine" -Recurse -Force }
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
