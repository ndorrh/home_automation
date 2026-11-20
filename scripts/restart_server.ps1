param(
    [int]$DelaySeconds = 2
)

$ErrorActionPreference = "SilentlyContinue"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Start-Sleep -Seconds $DelaySeconds

Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -eq "cloudflared.exe" -and $_.CommandLine -match "cloudflared_quick") -or
    ($_.Name -in @("python.exe", "pythonw.exe") -and $_.CommandLine -match "app\.server")
} | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force
}

Start-Sleep -Milliseconds 800
$PythonW = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $PythonW)) { $PythonW = "pythonw.exe" }
Start-Process -FilePath $PythonW -ArgumentList "-m", "app.server" -WorkingDirectory $ProjectRoot -WindowStyle Hidden
