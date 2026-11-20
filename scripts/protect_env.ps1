param(
    [ValidateSet("hide", "unhide", "status")]
    [string]$Mode = "status"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectRoot ".env"

if (-not [System.IO.File]::Exists($EnvFile)) {
    Write-Host ".env file not found at $EnvFile"
    exit 1
}

$Py = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

if ($Mode -eq "hide") {
    & $Py -c "from pathlib import Path; from app.env_guard import protect_env; protect_env(Path(r'$EnvFile')); print('.env hidden and locked (+H +S +R, owner-only ACL).')"
} elseif ($Mode -eq "unhide") {
    & $Py -c "from pathlib import Path; from app.env_guard import unprotect_env; unprotect_env(Path(r'$EnvFile')); print('.env is now visible and writable.')"
} else {
    & $Py -c "from pathlib import Path; from app.env_guard import is_env_protected; print('Protected:', is_env_protected(Path(r'$EnvFile')))"
}
