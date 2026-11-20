$ErrorActionPreference = "SilentlyContinue"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogFile = Join-Path $ProjectRoot "logs\server.log"

Write-Host "=== Smart Home AV Server Status ===" -ForegroundColor Cyan

$task = Get-ScheduledTask -TaskName "SmartHomeAVServer"
if ($task) {
    Write-Host "Scheduled Task : $($task.TaskName) ($($task.State))"
} else {
    Write-Host "Scheduled Task : Not registered" -ForegroundColor Yellow
}

$pyProcs = Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -in @("python.exe", "pythonw.exe")) -and ($_.CommandLine -match "app\.server")
}
$cfProcs = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "cloudflared.exe" -and $_.CommandLine -match "cloudflared_quick\.yml"
}

Write-Host "Server Process : $(if ($pyProcs) { 'RUNNING (PID ' + ($pyProcs.ProcessId -join ', ') + ')' } else { 'STOPPED' })"
Write-Host "Cloudflare Tun : $(if ($cfProcs) { 'RUNNING (PID ' + ($cfProcs.ProcessId -join ', ') + ')' } else { 'STOPPED' })"

try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8000/" -UseBasicParsing -TimeoutSec 2
    Write-Host "Local HTTP     : REACHABLE (HTTP $($resp.StatusCode)) -> http://localhost:8000" -ForegroundColor Green
} catch {
    Write-Host "Local HTTP     : NOT REACHABLE" -ForegroundColor Red
}

if (Test-Path $LogFile) {
    Write-Host "`n--- Latest Tunnel / Access URLs from logs\server.log ---" -ForegroundColor Cyan
    Get-Content $LogFile | Select-String -Pattern "Local Wi-Fi dashboard URL|Cloudflare tunnel ready|Sent access URLs" | Select-Object -Last 6
    Write-Host "`n--- Last 10 Log Lines ---" -ForegroundColor Cyan
    Get-Content $LogFile -Tail 10
} else {
    Write-Host "Log file not found yet at $LogFile"
}
