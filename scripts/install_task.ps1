param(
    [int]$Port = 8000,
    [switch]$AtStartup,
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonW = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $PythonW)) {
    $PythonW = "pythonw.exe"
}

$TaskName = "SmartHomeAVServer"
$CurrentUser = "$env:USERDOMAIN\$env:USERNAME"
$Action = New-ScheduledTaskAction -Execute $PythonW -Argument "-m app.server" -WorkingDirectory $ProjectRoot

if ($AtStartup) {
    $Trigger = New-ScheduledTaskTrigger -AtStartup
} else {
    $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
}

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

try {
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null
    Write-Host "Registered scheduled task '$TaskName' for $CurrentUser using $PythonW"
} catch {
    Write-Warning "Scheduled Task registration failed ($($_.Exception.Message)); falling back to user Startup shortcut."
    $StartupDir = [Environment]::GetFolderPath("Startup")
    $ShortcutPath = Join-Path $StartupDir "$TaskName.lnk"
    $Wsh = New-Object -ComObject WScript.Shell
    $Sc = $Wsh.CreateShortcut($ShortcutPath)
    $Sc.TargetPath = $PythonW
    $Sc.Arguments = "-m app.server"
    $Sc.WorkingDirectory = $ProjectRoot
    $Sc.WindowStyle = 7
    $Sc.Save()
    Write-Host "Created Startup shortcut at $ShortcutPath"
}

try {
    $existingRule = Get-NetFirewallRule -DisplayName "Smart Home AV Server" -ErrorAction SilentlyContinue
    if (-not $existingRule) {
        New-NetFirewallRule `
            -DisplayName "Smart Home AV Server" `
            -Direction Inbound `
            -Protocol TCP `
            -LocalPort $Port `
            -Profile Private,Public `
            -Action Allow | Out-Null
        Write-Host "Added Private & Public Windows Firewall rule for TCP port $Port."
    }
} catch {
    Write-Warning "Skipped Firewall rule (run PowerShell as Administrator if phones on Wi-Fi cannot connect)."
}

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Write-Host "Started '$TaskName' in the background."
}
