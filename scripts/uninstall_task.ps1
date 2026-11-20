$ErrorActionPreference = "SilentlyContinue"
$TaskName = "SmartHomeAVServer"

Stop-ScheduledTask -TaskName $TaskName
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Remove-NetFirewallRule -DisplayName "Smart Home AV Server"

$StartupShortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "$TaskName.lnk"
if (Test-Path $StartupShortcut) {
    Remove-Item $StartupShortcut -Force
}

Write-Host "Removed scheduled task '$TaskName', startup shortcut, and firewall rule (if present)."
