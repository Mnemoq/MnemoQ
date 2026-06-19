#!/usr/bin/env pwsh
# Diff gate: re-run all baselines and compare against Phase 0 captures
# Usage: powershell -ExecutionPolicy Bypass -File memory\.baseline\diff-gate.ps1 [-Phase "P1"]

param([string]$Phase = "gate")

$ErrorActionPreference = "Continue"
$script:allPass = $true

function Restore-Fixture {
    Copy-Item memory\.baseline\fixture-learnings.jsonl memory\learnings.jsonl -Force
}

function Normalize-Lines($lines) {
    # Replace ISO timestamps (2026-06-19T06:42:41Z) with a placeholder
    return $lines | ForEach-Object { $_ -replace '\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', 'TIMESTAMP' }
}

function Assert-NoDiff($name, $expected, $actual) {
    if (!(Test-Path $expected)) { Write-Host "  SKIP: $name (no baseline)" -ForegroundColor Yellow; return }
    if (!(Test-Path $actual))   { Write-Host "  FAIL: $name (no output)" -ForegroundColor Red; $script:allPass = $false; return }
    $eContent = Get-Content $expected
    $aContent = Get-Content $actual
    # Handle empty files
    if ($null -eq $eContent -and $null -eq $aContent) { Write-Host "  PASS: $name (both empty)" -ForegroundColor Green; return }
    if ($null -eq $eContent) { $eContent = @() }
    if ($null -eq $aContent) { $aContent = @() }
    $eNorm = Normalize-Lines $eContent
    $aNorm = Normalize-Lines $aContent
    
    try {
        $diff = Compare-Object $eNorm $aNorm -ErrorAction Stop
        if ($diff) {
            Write-Host "  FAIL: $name (diff found)" -ForegroundColor Red
            $diff | Format-Table -AutoSize
            $script:allPass = $false
        } else {
            Write-Host "  PASS: $name" -ForegroundColor Green
        }
    } catch {
        Write-Host "  FAIL: $name (Compare-Object error: $_)" -ForegroundColor Red
        $script:allPass = $false
    }
}

$python = "python"
$filter = "src\filter.py"
$b = "memory\.baseline"
$t = "memory\.baseline\_tmp"
New-Item -ItemType Directory -Path $t -Force | Out-Null

Write-Host "=== Diff Gate ($Phase) ===" -ForegroundColor Cyan

# Read-only baselines
Restore-Fixture; & $python $filter --stats > "$t\stats.txt" 2>$null
Assert-NoDiff "stats" "$b\stats.txt" "$t\stats.txt"

Restore-Fixture; & $python $filter --step 5 --components Player,Enemy --domain physics > "$t\retrieval-component.txt" 2>$null
Assert-NoDiff "retrieval-component" "$b\retrieval-component.txt" "$t\retrieval-component.txt"

Restore-Fixture; & $python $filter --step 10 --domain tooling > "$t\retrieval-domain.txt" 2>$null
Assert-NoDiff "retrieval-domain" "$b\retrieval-domain.txt" "$t\retrieval-domain.txt"

Restore-Fixture; & $python $filter --step 1 --components NonExistent --domain nonexistent > "$t\retrieval-empty.txt" 2>$null
Assert-NoDiff "retrieval-empty" "$b\retrieval-empty.txt" "$t\retrieval-empty.txt"

Restore-Fixture; & $python $filter --step 5 --components Player,Enemy > "$t\retrieval-no-domain.txt" 2>$null
Assert-NoDiff "retrieval-no-domain" "$b\retrieval-no-domain.txt" "$t\retrieval-no-domain.txt"

Restore-Fixture; & $python $filter --step 30 --domain tooling > "$t\retrieval-escalation.txt" 2>$null
Assert-NoDiff "retrieval-escalation" "$b\retrieval-escalation.txt" "$t\retrieval-escalation.txt"

# File mutation
Restore-Fixture; & $python $filter --step 5 --components Player,Enemy --domain physics > $null 2>$null
Assert-NoDiff "learnings-after-retrieval" "$b\learnings-after-retrieval.jsonl" "memory\learnings.jsonl"

# Review-agents
Restore-Fixture
Copy-Item "$b\fixture-agents.md" AGENTS.md -Force -ErrorAction SilentlyContinue
& $python $filter --review-agents --step 5 > "$t\review-agents.txt" 2>$null
Assert-NoDiff "review-agents" "$b\review-agents.txt" "$t\review-agents.txt"

# Write-path
Restore-Fixture; & $python $filter --log-file "$b\test-entry.json" > "$t\log-new.txt" 2>$null
Assert-NoDiff "log-new" "$b\log-new.txt" "$t\log-new.txt"

Restore-Fixture; & $python $filter --log-file "$b\test-entry.json" > $null 2>$null
& $python $filter --log-file "$b\test-entry.json" > "$t\log-dup.txt" 2>$null
Assert-NoDiff "log-dup" "$b\log-dup.txt" "$t\log-dup.txt"

Restore-Fixture; Remove-Item memory\quarantine.jsonl -ErrorAction SilentlyContinue
& $python $filter --log-file "$b\test-invalid.json" > "$t\log-quarantine.txt" 2>$null
Assert-NoDiff "log-quarantine" "$b\log-quarantine.txt" "$t\log-quarantine.txt"
if (Test-Path memory\quarantine.jsonl) {
    Assert-NoDiff "quarantine-file" "$b\quarantine-after-log.jsonl" "memory\quarantine.jsonl"
}

# Consolidation
Restore-Fixture; Copy-Item "$b\fixture-agents.md" AGENTS.md -Force -ErrorAction SilentlyContinue
Remove-Item memory\archive -Recurse -Force -ErrorAction SilentlyContinue
& $python $filter --consolidate --sprint 1 > "$t\consolidate.txt" 2>$null
Assert-NoDiff "consolidate" "$b\consolidate.txt" "$t\consolidate.txt"

Restore-Fixture; Copy-Item "$b\fixture-agents.md" AGENTS.md -Force -ErrorAction SilentlyContinue
& $python $filter --consolidate --sprint 1 --force > "$t\consolidate-force.txt" 2>$null
Assert-NoDiff "consolidate-force" "$b\consolidate-force.txt" "$t\consolidate-force.txt"

# Cleanup
Remove-Item $t -Recurse -Force -ErrorAction SilentlyContinue

if ($script:allPass) {
    Write-Host "`n=== ALL DIFFS ZERO ($Phase) ===" -ForegroundColor Green
    exit 0
} else {
    Write-Host "`n=== DIFF FAILURES DETECTED ($Phase) ===" -ForegroundColor Red
    exit 1
}
