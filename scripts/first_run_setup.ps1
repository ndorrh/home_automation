param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Test-VenvHealthy {
    $py = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) { return $false }
    $cfg = Join-Path $ProjectRoot ".venv\pyvenv.cfg"
    if (Test-Path $cfg) {
        $text = Get-Content $cfg -Raw
        if ($text -notmatch [regex]::Escape($ProjectRoot)) {
            return $false
        }
    }
    try {
        & $py -c "import fastapi, cryptography, mss, cv2, sounddevice" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

# 1. Ensure Python virtual environment works for this folder/PC
if (-not (Test-VenvHealthy)) {
    Write-Host "[Setup] Creating/updating self-contained Python .venv..."
    if (Test-Path (Join-Path $ProjectRoot ".venv")) {
        Remove-Item -Recurse -Force (Join-Path $ProjectRoot ".venv")
    }
    python -m venv .venv
    & (Join-Path $ProjectRoot ".venv\Scripts\python.exe") -m pip install --upgrade pip
    & (Join-Path $ProjectRoot ".venv\Scripts\pip.exe") install -r requirements.txt
}

# 2. Ensure self-contained bin\cloudflared.exe exists
$BinDir = Join-Path $ProjectRoot "bin"
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$LocalCf = Join-Path $BinDir "cloudflared.exe"
if (-not (Test-Path $LocalCf)) {
    $GlobalCf = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($GlobalCf -and (Test-Path $GlobalCf.Source)) {
        Copy-Item $GlobalCf.Source $LocalCf -Force
        Write-Host "[Setup] Copied cloudflared.exe into bin\"
    } else {
        Write-Host "[Setup] Downloading cloudflared-windows-amd64.exe into bin\..."
        $CfUrl = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
        Invoke-WebRequest -Uri $CfUrl -OutFile $LocalCf -UseBasicParsing
    }
}

# 3. Ensure Nodemailer dependencies exist
$MailerModules = Join-Path $ProjectRoot "mailer\node_modules\nodemailer"
if (-not (Test-Path $MailerModules)) {
    $Npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $Npm) {
        $Winget = Get-Command winget -ErrorAction SilentlyContinue
        if ($Winget) {
            Write-Host "[Setup] Installing Node.js LTS via winget..."
            winget install --id OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements
        }
    }
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        npm --prefix (Join-Path $ProjectRoot "mailer") install --omit=dev
    } else {
        Write-Warning "Node.js/npm not found; Gmail Nodemailer notifications disabled until Node.js is installed."
    }
}

# 4. Run Python bootstrap (.env, keys, directories, isolated cloudflared config)
& (Join-Path $ProjectRoot ".venv\Scripts\python.exe") -c "from app.bootstrap import run_bootstrap; run_bootstrap()"

# 5. Ensure Windows Firewall allows port 8000 on Wi-Fi & Hotspot (one-time UAC if missing)
$FwRule = Get-NetFirewallRule -DisplayName "Smart Home AV Server" -ErrorAction SilentlyContinue
if (-not $FwRule) {
    try {
        & (Join-Path $PSScriptRoot "fix_firewall.ps1") -Port $Port -Elevate
    } catch {
        Write-Warning "Firewall prompt cancelled or skipped; run Fix Firewall from the dashboard if phones cannot connect."
    }
}

# 6. Auto-register Windows Scheduled Task if missing or moved
$ExistingTask = Get-ScheduledTask -TaskName "SmartHomeAVServer" -ErrorAction SilentlyContinue
$NeedsTask = $true
if ($ExistingTask -and $ExistingTask.Actions[0].WorkingDirectory -eq $ProjectRoot) {
    $NeedsTask = $false
}
if ($NeedsTask) {
    & (Join-Path $PSScriptRoot "install_task.ps1") -Port $Port
}

# 7. Hide & lock .env by default
$EnvPath = Join-Path $ProjectRoot ".env"
if ([System.IO.File]::Exists($EnvPath)) {
    & (Join-Path $PSScriptRoot "protect_env.ps1") -Mode hide | Out-Null
}
