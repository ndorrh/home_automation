param(
    [ValidateSet("status", "start", "stop")]
    [string]$Action = "status",
    [string]$FallbackSsid = "SmartHomeAV-AP",
    [string]$FallbackPass = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $ProjectRoot "logs\hotspot_ap.pid"

[Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime] | Out-Null
[Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,ContentType=WindowsRuntime] | Out-Null

function Get-TetheringManager {
    $profile = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
    if (-not $profile) {
        $profiles = [Windows.Networking.Connectivity.NetworkInformation]::GetConnectionProfiles()
        $profile = $profiles | Select-Object -First 1
    }
    if (-not $profile) { return $null }
    try {
        return [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($profile)
    } catch {
        return $null
    }
}

function Get-HotspotIp {
    $addr = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { ($_.IPAddress -eq "192.168.137.1" -or $_.InterfaceAlias -like "*Local Area Connection*") -and $_.IPAddress -notlike "169.254.*" } |
        Select-Object -First 1
    if ($addr) { return $addr.IPAddress }
    return $null
}

function Test-SoftApPidRunning {
    if (-not (Test-Path $PidFile)) { return $false }
    $raw = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
    if (-not $raw) { return $false }
    $proc = Get-Process -Id ([int]$raw) -ErrorAction SilentlyContinue
    return ($null -ne $proc)
}

if ($Action -eq "status") {
    $mgr = Get-TetheringManager
    $ssid = $FallbackSsid
    $active = $false
    $mode = "none"
    if ($mgr) {
        $cfg = $mgr.GetCurrentAccessPointConfiguration()
        if ($cfg.Ssid) { $ssid = $cfg.Ssid }
        if ($mgr.TetheringOperationalState -eq "On") {
            $active = $true
            $mode = "windows_mobile_hotspot"
        }
    }
    if (-not $active -and (Test-SoftApPidRunning)) {
        $active = $true
        $mode = "wifi_direct_ap"
    }
    @{
        active = $active
        mode = $mode
        ssid = $ssid
        hotspot_ip = Get-HotspotIp
    } | ConvertTo-Json -Compress
    exit 0
}

if ($Action -eq "stop") {
    $mgr = Get-TetheringManager
    if ($mgr -and $mgr.TetheringOperationalState -ne "Off") {
        $op = $mgr.StopTetheringAsync()
        for ($i = 0; $i -lt 20 -and $mgr.TetheringOperationalState -ne "Off"; $i++) {
            Start-Sleep -Milliseconds 250
        }
    }
    if (Test-Path $PidFile) {
        $pidVal = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
        if ($pidVal) { Stop-Process -Id ([int]$pidVal) -Force -ErrorAction SilentlyContinue }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
    @{ active = $false; mode = "stopped" } | ConvertTo-Json -Compress
    exit 0
}

# Action -eq "start"
$mgr = Get-TetheringManager
if ($mgr) {
    if ($mgr.TetheringOperationalState -ne "On") {
        $null = $mgr.StartTetheringAsync()
        for ($i = 0; $i -lt 24 -and $mgr.TetheringOperationalState -ne "On"; $i++) {
            Start-Sleep -Milliseconds 300
        }
    }
    if ($mgr.TetheringOperationalState -eq "On") {
        $cfg = $mgr.GetCurrentAccessPointConfiguration()
        $ip = Get-HotspotIp
        @{
            active = $true
            mode = "windows_mobile_hotspot"
            ssid = $cfg.Ssid
            hotspot_ip = $(if ($ip) { $ip } else { "192.168.137.1" })
        } | ConvertTo-Json -Compress
        exit 0
    }
}

# Fallback when completely disconnected from any upstream network: Wi-Fi Direct Legacy SoftAP
if (-not (Test-SoftApPidRunning)) {
    $passToUse = if ($FallbackPass.Length -ge 8) { $FallbackPass } else { "SmartHomeAV123" }
    $helperCode = @"
[Windows.Devices.WiFiDirect.WiFiDirectAdvertisementPublisher,Windows.Devices.WiFiDirect,ContentType=WindowsRuntime] | Out-Null
[Windows.Security.Credentials.PasswordCredential,Windows.Security.Credentials,ContentType=WindowsRuntime] | Out-Null
`$pub = New-Object Windows.Devices.WiFiDirect.WiFiDirectAdvertisementPublisher
`$pub.Advertisement.IsAutonomousGroupOwnerEnabled = `$true
`$pub.Advertisement.LegacySettings.IsEnabled = `$true
`$pub.Advertisement.LegacySettings.Ssid = '$FallbackSsid'
`$cred = New-Object Windows.Security.Credentials.PasswordCredential
`$cred.Password = '$passToUse'
`$pub.Advertisement.LegacySettings.Passphrase = `$cred
`$pub.Start()
while (`$true) { Start-Sleep -Seconds 5 }
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($helperCode))
    $p = Start-Process powershell -ArgumentList "-NoProfile -WindowStyle Hidden -EncodedCommand $encoded" -PassThru
    Set-Content -Path $PidFile -Value $p.Id -Encoding ascii
    Start-Sleep -Seconds 2
}

$ip = Get-HotspotIp
@{
    active = (Test-SoftApPidRunning)
    mode = "wifi_direct_ap"
    ssid = $FallbackSsid
    hotspot_ip = $(if ($ip) { $ip } else { "192.168.137.1" })
} | ConvertTo-Json -Compress
