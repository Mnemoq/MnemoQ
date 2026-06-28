# Git Session Lock Snippet

Shared lock-check and lock-release instructions for git-mutating workflows.
This is NOT a standalone command — it is referenced by other commands.

## Lock acquisition (run at the start of any leaf git-mutating command)

```powershell
$lock = ".git/.claude-git-lock"
if (Test-Path $lock) {
    $age = (Get-Date) - (Get-Item $lock).LastWriteTime
    if ($age.TotalMinutes -lt 10) {
        $content = Get-Content $lock -Raw
        Write-Host "LOCKED: $content"
    } else {
        Write-Host "STALE: $([int]$age.TotalMinutes) min old"
    }
} else {
    Write-Host "FREE"
}
```

**If the output is `LOCKED:`**, **stop the command** and tell the user:

> *Another Claude Code session is running a git command (started at <timestamp>). Wait for it to finish, or remove `.git/.claude-git-lock` manually if you know it's stale.*

Do not retry or continue to the next step.

**If the output is `STALE:`**, override the lock:

```powershell
Remove-Item ".git/.claude-git-lock"
"$([DateTime]::UtcNow.ToString('o')) <command-name>" | Set-Content ".git/.claude-git-lock"
```

**If the output is `FREE`**, acquire the lock:

```powershell
"$([DateTime]::UtcNow.ToString('o')) <command-name>" | Set-Content ".git/.claude-git-lock"
```

## Lock release (run on any command exit — success, abort, or error)

```powershell
Remove-Item ".git/.claude-git-lock" -ErrorAction SilentlyContinue
```
