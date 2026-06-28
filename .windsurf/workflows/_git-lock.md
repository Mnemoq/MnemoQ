# Git Session Lock Snippet

Shared lock-check and lock-release instructions for git-mutating workflows.
This is NOT a standalone workflow — it is referenced by other workflows.

## Lock acquisition (run at the start of any leaf git-mutating workflow)

```powershell
$lock = ".git/.windsurf-git-lock"
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

**If the output is `LOCKED:`**, **stop the workflow** and tell the user:

> *Another Windsurf session is running a git workflow (started at <timestamp>). Wait for it to finish, or remove `.git/.windsurf-git-lock` manually if you know it's stale.*

Do not retry or continue to the next step.

**If the output is `STALE:`**, override the lock:

```powershell
Remove-Item ".git/.windsurf-git-lock"
"$([DateTime]::UtcNow.ToString('o')) <workflow-name>" | Set-Content ".git/.windsurf-git-lock"
```

**If the output is `FREE`**, acquire the lock:

```powershell
"$([DateTime]::UtcNow.ToString('o')) <workflow-name>" | Set-Content ".git/.windsurf-git-lock"
```

## Lock release (run on any workflow exit — success, abort, or error)

```powershell
Remove-Item ".git/.windsurf-git-lock" -ErrorAction SilentlyContinue
```
