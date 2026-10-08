#requires -Version 5.1
#requires -RunAsAdministrator

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$PrometheusIp,

    [ValidateRange(1, 65535)]
    [int]$DetailPort = 9192
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "[+] $Message"
}

function Test-IPv4Address {
    param([Parameter(Mandatory = $true)][string]$Address)
    $parsed = $null
    if (-not [System.Net.IPAddress]::TryParse($Address, [ref]$parsed)) { return $false }
    return $parsed.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork
}

function Get-NetBirdExe {
    $cmd = Get-Command netbird.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($candidate in @(
        "$env:ProgramFiles\Netbird\netbird.exe",
        "$env:ProgramFiles\NetBird\netbird.exe"
    )) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    throw "netbird.exe wurde nicht gefunden."
}

function Get-NetBirdIPv4 {
    param([Parameter(Mandatory = $true)][string]$NetBirdExe)
    $raw = & $NetBirdExe status --ipv4 2>$null
    if ($LASTEXITCODE -ne 0) { throw "'netbird status --ipv4' ist fehlgeschlagen." }
    foreach ($line in @($raw)) {
        $candidate = ([string]$line).Trim()
        if ($candidate -match "/") { $candidate = $candidate.Split("/")[0] }
        if ($candidate -match "(\d{1,3}(?:\.\d{1,3}){3})") { $candidate = $Matches[1] }
        if ($candidate -and (Test-IPv4Address -Address $candidate)) { return $candidate }
    }
    throw "Keine NetBird-IPv4 konnte ermittelt werden."
}

function Get-MetricsText {
    param([Parameter(Mandatory = $true)][string]$Uri)
    try {
        $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        return [string]$response.Content
    } catch {
        return $null
    }
}

function Test-DetailMetrics {
    param([Parameter(Mandatory = $true)][string]$Uri)
    $content = Get-MetricsText -Uri $Uri
    if ([string]::IsNullOrWhiteSpace($content)) { return $false }
    return $content -match "(?m)^openmain_netbird_status_exporter_up 1$"
}

if (-not (Test-IPv4Address -Address $PrometheusIp)) {
    throw "PrometheusIp '$PrometheusIp' ist keine gültige IPv4-Adresse."
}

$netBirdExe = Get-NetBirdExe
$netBirdIp = Get-NetBirdIPv4 -NetBirdExe $netBirdExe
$baseUrl = "https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/netbird-prometheus"
$exporterDir = Join-Path $env:ProgramData "OpenMain\NetBird"
$exporterPath = Join-Path $exporterDir "netbird-status-exporter.ps1"
$taskName = "OpenMain NetBird Status Exporter"
$localUri = "http://127.0.0.1:$DetailPort/metrics"
$remoteUri = "http://$netBirdIp`:$DetailPort/metrics"

New-Item -ItemType Directory -Force -Path $exporterDir | Out-Null
Write-Step "Lade detaillierten NetBird Status Exporter..."
Invoke-WebRequest -Uri "$baseUrl/netbird-status-exporter.ps1" -OutFile $exporterPath -UseBasicParsing

$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

$taskArgs = '-NoProfile -ExecutionPolicy Bypass -File "' + $exporterPath + '" -ListenAddress 127.0.0.1 -Port ' + $DetailPort + ' -NetBirdExe "' + $netBirdExe + '"'
$action = New-ScheduledTaskAction -Execute "PowerShell.exe" -Argument $taskArgs
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
Start-ScheduledTask -TaskName $taskName

Write-Step "Warte auf lokalen Detail-Endpunkt..."
$deadline = (Get-Date).AddSeconds(30)
do {
    if (Test-DetailMetrics -Uri $localUri) { break }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)
if (-not (Test-DetailMetrics -Uri $localUri)) {
    throw "Detaillierter NetBird Status Exporter ist auf $localUri nicht erreichbar."
}

$ipHelper = Get-Service -Name iphlpsvc -ErrorAction SilentlyContinue
if (-not $ipHelper) { throw "Windows Dienst 'IP Helper' (iphlpsvc) wurde nicht gefunden." }
if ($ipHelper.StartType -eq "Disabled") { Set-Service -Name iphlpsvc -StartupType Automatic }
if ($ipHelper.Status -ne "Running") { Start-Service -Name iphlpsvc }

Write-Step "Konfiguriere Windows Portproxy $netBirdIp`:$DetailPort -> 127.0.0.1:$DetailPort ..."
& netsh interface portproxy delete v4tov4 listenaddress=$netBirdIp listenport=$DetailPort protocol=tcp 2>$null | Out-Null
& netsh interface portproxy add v4tov4 listenaddress=$netBirdIp listenport=$DetailPort connectaddress=127.0.0.1 connectport=$DetailPort protocol=tcp | Out-Null
if ($LASTEXITCODE -ne 0) { throw "netsh portproxy konnte für TCP/$DetailPort nicht eingerichtet werden." }

$ruleName = "OpenMain NetBird Metrics TCP $DetailPort"
Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
$firewallParams = @{
    DisplayName = $ruleName
    Description = "OpenMain Prometheus access to NetBird Relay and peer detail metrics"
    Direction = "Inbound"
    Action = "Allow"
    Protocol = "TCP"
    LocalAddress = $netBirdIp
    LocalPort = $DetailPort
    RemoteAddress = $PrometheusIp
    Profile = "Any"
    EdgeTraversalPolicy = "Block"
    Enabled = "True"
}
New-NetFirewallRule @firewallParams | Out-Null

Write-Step "Prüfe Detail-Portproxy..."
$tcpOk = Test-NetConnection -ComputerName $netBirdIp -Port $DetailPort -InformationLevel Quiet -WarningAction SilentlyContinue
if (-not $tcpOk) { throw "Detail-Portproxy $netBirdIp`:$DetailPort ist lokal nicht erreichbar." }

Write-Host ""
Write-Host "NetBird Relay-/Peer-Details aktiv:"
Write-Host ("{0,-22} {1}" -f "NetBird-IP:", $netBirdIp)
Write-Host ("{0,-22} {1}" -f "Lokal:", $localUri)
Write-Host ("{0,-22} {1}" -f "Detail Target:", "$netBirdIp`:$DetailPort")
Write-Host ("{0,-22} {1}" -f "Erlaubte Quelle:", $PrometheusIp)
Write-Host ""
Write-Host "Sicherheit:"
Write-Host "  - Status-Exporter lauscht nur auf 127.0.0.1:$DetailPort."
Write-Host "  - Windows Portproxy veröffentlicht nur die NetBird-IP."
Write-Host "  - Windows Firewall erlaubt nur $PrometheusIp als Quelle."
Write-Host "  - NetBird Policy muss TCP/$DetailPort ebenfalls erlauben."
