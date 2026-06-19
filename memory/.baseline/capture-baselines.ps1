#!/usr/bin/env pwsh
# Phase 0: Capture golden baselines for refactor diff gates
# Run from repo root (C:\AgentMemoryEngine)
# NOTE: Only captures stdout. Stderr goes to console for visibility but isn't diffed.

$ErrorActionPreference = "Continue"

function Restore-Fixture {
    Copy-Item memory\.baseline\fixture-learnings.jsonl memory\learnings.jsonl -Force
}

$python = "python"
$filter = "src\filter.py"

Write-Host "=== Phase 0.1: Read-only baselines ===" -ForegroundColor Cyan

Restore-Fixture
& $python $filter --stats > memory\.baseline\stats.txt 2>$null
Write-Host "  stats.txt captured"

Restore-Fixture
& $python $filter --step 5 --components Player,Enemy --domain physics > memory\.baseline\retrieval-component.txt 2>$null
Write-Host "  retrieval-component.txt captured"

Restore-Fixture
& $python $filter --step 10 --domain tooling > memory\.baseline\retrieval-domain.txt 2>$null
Write-Host "  retrieval-domain.txt captured"

Restore-Fixture
& $python $filter --step 1 --components NonExistent --domain nonexistent > memory\.baseline\retrieval-empty.txt 2>$null
Write-Host "  retrieval-empty.txt captured"

Restore-Fixture
& $python $filter --step 5 --components Player,Enemy > memory\.baseline\retrieval-no-domain.txt 2>$null
Write-Host "  retrieval-no-domain.txt captured"

# Step alone (no qualifiers) — prints help to stdout, exit 1
Restore-Fixture
& $python $filter --step 5 > memory\.baseline\step-alone.txt 2>$null
Write-Host "  step-alone.txt captured"

# Escalation — entry #2 step=0 + critical, current_step=30
Restore-Fixture
& $python $filter --step 30 --domain tooling > memory\.baseline\retrieval-escalation.txt 2>$null
Write-Host "  retrieval-escalation.txt captured"

# File mutation (access_count bump)
Restore-Fixture
& $python $filter --step 5 --components Player,Enemy --domain physics > $null 2>$null
Copy-Item memory\learnings.jsonl memory\.baseline\learnings-after-retrieval.jsonl -Force
Write-Host "  learnings-after-retrieval.jsonl captured"

# Review-agents
Restore-Fixture
if (!(Test-Path AGENTS.md)) { Copy-Item memory\.baseline\fixture-agents.md AGENTS.md -Force }
& $python $filter --review-agents --step 5 > memory\.baseline\review-agents.txt 2>$null
Write-Host "  review-agents.txt captured"

Write-Host "`n=== Phase 0.2: Write-path baselines ===" -ForegroundColor Cyan

# Log new entry
Restore-Fixture
& $python $filter --log-file memory\.baseline\test-entry.json > memory\.baseline\log-new.txt 2>$null
Copy-Item memory\learnings.jsonl memory\.baseline\learnings-after-log.jsonl -Force
Write-Host "  log-new.txt + learnings-after-log.jsonl captured"

# Duplicate (log same twice)
Restore-Fixture
& $python $filter --log-file memory\.baseline\test-entry.json > $null 2>$null
& $python $filter --log-file memory\.baseline\test-entry.json > memory\.baseline\log-dup.txt 2>$null
Write-Host "  log-dup.txt captured"

# Quarantine (stdout only — QUARANTINED message goes to stderr)
Restore-Fixture
Remove-Item memory\quarantine.jsonl -ErrorAction SilentlyContinue
& $python $filter --log-file memory\.baseline\test-invalid.json > memory\.baseline\log-quarantine.txt 2>$null
if (Test-Path memory\quarantine.jsonl) {
    Copy-Item memory\quarantine.jsonl memory\.baseline\quarantine-after-log.jsonl -Force
    Write-Host "  log-quarantine.txt + quarantine-after-log.jsonl captured"
} else {
    Write-Host "  log-quarantine.txt captured (no quarantine.jsonl created)" -ForegroundColor Yellow
}

# Version (goes to stderr — capture it differently)
& $python $filter --version 2>&1 | Out-File memory\.baseline\version-stderr.txt -Encoding utf8
Write-Host "  version-stderr.txt captured"

Write-Host "`n=== Phase 0.3: Consolidation baselines ===" -ForegroundColor Cyan

Restore-Fixture
Copy-Item memory\.baseline\fixture-agents.md AGENTS.md -Force -ErrorAction SilentlyContinue
Remove-Item memory\archive -Recurse -Force -ErrorAction SilentlyContinue
& $python $filter --consolidate --sprint 1 > memory\.baseline\consolidate.txt 2>$null
Copy-Item memory\learnings.jsonl memory\.baseline\learnings-after-consolidate.jsonl -Force
if (Test-Path memory\archive\sprint-1.jsonl) {
    Copy-Item memory\archive\sprint-1.jsonl memory\.baseline\sprint-1.jsonl -Force
    Write-Host "  consolidate.txt + sprint-1.jsonl captured"
} else {
    Write-Host "  consolidate.txt captured (no archive created)" -ForegroundColor Yellow
}

# Force overwrite
Restore-Fixture
Copy-Item memory\.baseline\fixture-agents.md AGENTS.md -Force -ErrorAction SilentlyContinue
& $python $filter --consolidate --sprint 1 --force > memory\.baseline\consolidate-force.txt 2>$null
Write-Host "  consolidate-force.txt captured"

Write-Host "`n=== Phase 0 COMPLETE ===" -ForegroundColor Green
Write-Host "Baseline files:" -ForegroundColor Green
Get-ChildItem memory\.baseline\*.txt, memory\.baseline\*.jsonl | ForEach-Object { Write-Host "  $($_.Name) ($($_.Length) bytes)" }
