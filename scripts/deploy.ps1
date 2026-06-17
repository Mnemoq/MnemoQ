param(
    [switch]$DryRun,
    [string]$CanaryProject = ""
)

$ErrorActionPreference = "Stop"

$DevRoot = Split-Path -Parent $PSScriptRoot
$LiveEngine = "$env:USERPROFILE\.agent-memory\engine"
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"

Write-Host "Validating dev project..."
$requiredFiles = @("src\filter.py", "src\profile.py", "src\scaffold.py", "src\update.py", "VERSION")
foreach ($f in $requiredFiles) {
    if (-not (Test-Path "$DevRoot\$f")) {
        Write-Error "Missing required file: $f"
        exit 1
    }
}

Write-Host "Running tests..."
Push-Location $DevRoot
python -m pytest tests/ --tb=short
if ($LASTEXITCODE -ne 0) {
    Write-Error "Tests failed. Aborting deploy."
    exit 1
}
Pop-Location

$BackupDir = "$LiveEngine\backups\$Timestamp"
if (-not $DryRun) {
    Write-Host "Backing up live engine to $BackupDir..."
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
    Copy-Item "$LiveEngine\*.py" "$BackupDir\" -Force
    Copy-Item "$LiveEngine\VERSION" "$BackupDir\" -Force
    Copy-Item "$LiveEngine\templates" "$BackupDir\templates" -Recurse -Force
}

Write-Host "Copying dev files to live engine..."
$filesToCopy = @(
    @{ Src = "src\filter.py"; Dst = "filter.py" },
    @{ Src = "src\profile.py"; Dst = "profile.py" },
    @{ Src = "src\scaffold.py"; Dst = "scaffold.py" },
    @{ Src = "src\update.py"; Dst = "update.py" },
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
} else {
    Copy-Item "$DevRoot\templates" "$LiveEngine\templates" -Recurse -Force
    Write-Host "  Copied: templates/"
}

Write-Host "Verifying live engine..."
if (-not $DryRun) {
    $result = python "$LiveEngine\filter.py" --stats 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Verification failed. Restoring from backup..."
        Copy-Item "$BackupDir\*.py" "$LiveEngine\" -Force
        Copy-Item "$BackupDir\VERSION" "$LiveEngine\" -Force
        Copy-Item "$BackupDir\templates" "$LiveEngine\templates" -Recurse -Force
        exit 1
    }
    Write-Host "  Verification passed."
}

if ($CanaryProject) {
    Write-Host "Updating canary project: $CanaryProject"
    if (-not $DryRun) {
        python "$LiveEngine\update.py" --project $CanaryProject --yes
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
