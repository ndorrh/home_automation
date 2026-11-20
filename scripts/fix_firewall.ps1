param(
    [int]$Port = 8000,
    [switch]$Elevate
)

$ErrorActionPreference = "Stop"
$RuleName = "Smart Home AV Server"

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

$existing = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
if ($existing) {
    if (Test-IsAdmin) {
        Set-NetFirewallRule -DisplayName $RuleName -Enabled True -Profile Private,Public -Action Allow | Out-Null
    }
    Write-Host "Firewall rule '$RuleName' is active for TCP port $Port."
    exit 0
}

if (-not (Test-IsAdmin)) {
    if ($Elevate) {
        $argList = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Port $Port"
        Start-Process powershell -ArgumentList $argList -Verb RunAs -Wait
        $after = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
        if ($after) {
            Write-Host "Firewall rule '$RuleName' added successfully."
            exit 0
        }
    }
    Write-Warning "Administrator elevation is needed to add Firewall rule for TCP port $Port."
    exit 2
}

New-NetFirewallRule `
    -DisplayName $RuleName `
    -Direction Inbound `
    -Protocol TCP `
    -LocalPort $Port `
    -Profile Private,Public `
    -Action Allow | Out-Null

Write-Host "Added Windows Firewall rule '$RuleName' (TCP port $Port, Private & Public)."
