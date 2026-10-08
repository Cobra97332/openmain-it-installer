#requires -Version 5.1
#requires -RunAsAdministrator

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$PrometheusIp,

    [ValidateRange(1, 65535)]
    [int]$MetricsPort = 9191,

    [ValidateRange(1, 65535)]
    [int]$DetailPort = 9192,

    [ValidateRange(5, 180)]
    [int]$WaitSeconds = 60
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "[+] $Message"
}

function Write-WarnLine {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Warning $Message
}

function Test-IPv4Address {
    param([Parameter(Mandatory = $true)][string]$Address)

    $parsed = $null
    if (-not [System.Net.IPAddress]::TryParse($Address, [ref]$parsed)) {
        return $false
    }

    return $parsed.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork
}

function Get-NetBirdExe {
    $cmd = Get-Command netbird.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    $candidates = @(
        "$env:ProgramFiles\Netbird\netbird.exe",
        "$env:ProgramFiles\NetBird\netbird.exe"
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }

    throw "netbird.exe wurde nicht gefunden."
}

function Get-NetBirdIPv4 {
    param([Parameter(Mandatory = $true)][string]$NetBirdExe)

    $raw = & $NetBirdExe status --ipv4 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "'netbird status --ipv4' ist fehlgeschlagen."
    }

    foreach ($line in @($raw)) {
        $candidate = ($line -as [string]).Trim()
        if ($candidate -match '/') {
            $candidate = $candidate.Split('/')[0]
        }

        if ($candidate -match '(\d{1,3}(?:\.\d{1,3}){3})') {
            $candidate = $Matches[1]
        }

        if ($candidate -and (Test-IPv4Address -Address $candidate)) {
            return $candidate
        }
    }

    throw "Keine NetBird-IPv4 konnte ermittelt werden."
}

function Get-MetricsText {
    param([Parameter(Mandatory = $true)][string]$Uri)

    try {
        $response = Invoke-WebRequest `
            -Uri $Uri `
            -UseBasicParsing `
            -TimeoutSec 5 `
            -ErrorAction Stop

        return [string]$response.Content
    }
    catch {
        return $null
    }
}

function Test-NetBirdMetrics {
    param([Parameter(Mandatory = $true)][string]$Uri)

    $content = Get-MetricsText -Uri $Uri
    if ([string]::IsNullOrWhiteSpace($content)) {
        return $false
    }

    return $content -match '(?m)^netbird_'
}

function Wait-NetBirdMetrics {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    do {
        if (Test-NetBirdMetrics -Uri $Uri) {
            return $true
        }

        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)

    return $false
}

function Set-PortProxy {
    param(
        [Parameter(Mandatory = $true)][string]$ListenAddress,
        [Parameter(Mandatory = $true)][int]$Port
    )

    Write-Step "Konfiguriere Windows Portproxy $ListenAddress`:$Port -> 127.0.0.1:$Port ..."

    $ipHelper = Get-Service -Name iphlpsvc -ErrorAction SilentlyContinue
    if (-not $ipHelper) {
        throw "Windows Dienst 'IP Helper' (iphlpsvc) wurde nicht gefunden."
    }

    if ($ipHelper.StartType -eq 'Disabled') {
        Set-Service -Name iphlpsvc -StartupType Automatic
    }

    if ($ipHelper.Status -ne 'Running') {
        Start-Service -Name iphlpsvc
    }

    & netsh interface portproxy delete v4tov4 `
        listenaddress=$ListenAddress `
        listenport=$Port `
        protocol=tcp 2>$null | Out-Null

    & netsh interface portproxy add v4tov4 `
        listenaddress=$ListenAddress `
        listenport=$Port `
        connectaddress=127.0.0.1 `
        connectport=$Port `
        protocol=tcp | Out-Null

    if ($LASTEXITCODE -ne 0) {
        throw "netsh portproxy konnte nicht eingerichtet werden."
    }
}

function Set-MetricsFirewallRule {
    param(
        [Parameter(Mandatory = $true)][string]$ListenAddress,
        [Parameter(Mandatory = $true)][string]$RemoteAddress,
        [Parameter(Mandatory = $true)][int]$Port
    )

    $ruleName = "OpenMain NetBird Metrics TCP $Port"

    Write-Step "Konfiguriere Windows Firewall: nur $RemoteAddress -> $ListenAddress`:$Port/TCP ..."

    Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue

    New-NetFirewallRule `
        -DisplayName $ruleName `
        -Description "OpenMain Prometheus access to NetBird client metrics" `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalAddress $ListenAddress `
        -LocalPort $Port `
        -RemoteAddress $RemoteAddress `
        -Profile Any `
        -EdgeTraversalPolicy Block `
        -Enabled True | Out-Null
}

if (-not (Test-IPv4Address -Address $PrometheusIp)) {
    throw "PrometheusIp '$PrometheusIp' ist keine gültige IPv4-Adresse."
}

if ($DetailPort -eq $MetricsPort) {
    throw "MetricsPort und DetailPort müssen verschieden sein."
}

$netBirdExe = Get-NetBirdExe
$netBirdIp = Get-NetBirdIPv4 -NetBirdExe $netBirdExe
$localUri = "http://127.0.0.1:$MetricsPort/metrics"

Write-Step "NetBird CLI: $netBirdExe"
Write-Step "NetBird IPv4: $netBirdIp"
Write-Step "Prometheus IPv4: $PrometheusIp"

if (-not (Test-NetBirdMetrics -Uri $localUri)) {
    Write-WarnLine "Lokale NetBird Client Metrics sind noch nicht aktiv."
    Write-WarnLine "Die bestehende NetBird-Verbindung wird einmal kurz getrennt und danach wieder aufgebaut."
    Write-WarnLine "Bei RDP/SSH über NetBird kann die Sitzung dabei kurz unterbrochen werden."

    Write-Step "NetBird wird kurz getrennt..."
    & $netBirdExe down
    if ($LASTEXITCODE -ne 0) {
        throw "'netbird down' ist fehlgeschlagen."
    }

    Start-Sleep -Seconds 2

    Write-Step "Aktiviere NetBird Client Metrics auf 127.0.0.1:$MetricsPort ..."
    & $netBirdExe up `
        --enable-local-metrics `
        --local-metrics-address "127.0.0.1:$MetricsPort"

    if ($LASTEXITCODE -ne 0) {
        throw "'netbird up' ist fehlgeschlagen."
    }

    Write-Step "Warte auf lokalen Metrics-Endpunkt..."
    if (-not (Wait-NetBirdMetrics -Uri $localUri -TimeoutSeconds $WaitSeconds)) {
        throw "Lokaler Metrics-Endpunkt $localUri wurde nicht erreichbar."
    }
}
else {
    Write-Step "Lokale NetBird Client Metrics sind bereits aktiv."
}

Set-PortProxy -ListenAddress $netBirdIp -Port $MetricsPort
Set-MetricsFirewallRule `
    -ListenAddress $netBirdIp `
    -RemoteAddress $PrometheusIp `
    -Port $MetricsPort

Write-Step "Prüfe lokale NetBird-Metriken..."
if (-not (Test-NetBirdMetrics -Uri $localUri)) {
    throw "Lokaler Metrics-Endpunkt $localUri liefert keine NetBird-Metriken."
}

Write-Step "Prüfe lokalen Portproxy..."
$tcpTest = Test-NetConnection `
    -ComputerName $netBirdIp `
    -Port $MetricsPort `
    -InformationLevel Quiet `
    -WarningAction SilentlyContinue

if (-not $tcpTest) {
    Write-WarnLine "Der Portproxy ist lokal noch nicht erreichbar. Prüfe 'netsh interface portproxy show v4tov4' und den Dienst iphlpsvc."
}
else {
    Write-Step "Portproxy lauscht auf $netBirdIp`:$MetricsPort."
}

Write-Step "Installiere Relay-/Peer-Detail-Exporter..."
$detailHelper = Join-Path $env:TEMP "enable-netbird-client-detail-metrics.ps1"
Invoke-WebRequest `
    -Uri "https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/netbird-prometheus/enable-client-detail-metrics.ps1" `
    -OutFile $detailHelper `
    -UseBasicParsing

& PowerShell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File $detailHelper `
    -PrometheusIp $PrometheusIp `
    -DetailPort $DetailPort

if ($LASTEXITCODE -ne 0) {
    throw "Relay-/Peer-Detail-Exporter konnte nicht eingerichtet werden."
}
Write-Host ""
Write-Host "============================================================"
Write-Host " OpenMain NetBird Client Metrics - Windows"
Write-Host "============================================================"
Write-Host ("{0,-22} {1}" -f "Hostname:", $env:COMPUTERNAME)
Write-Host ("{0,-22} {1}" -f "NetBird-IP:", $netBirdIp)
Write-Host ("{0,-22} {1}" -f "Lokal:", $localUri)
Write-Host ("{0,-22} {1}" -f "Basis Target:", "$netBirdIp`:$MetricsPort")
Write-Host ("{0,-22} {1}" -f "Detail Target:", "$netBirdIp`:$DetailPort")
Write-Host ("{0,-22} {1}" -f "Erlaubte Quelle:", $PrometheusIp)
Write-Host ""
Write-Host "Prometheus-Server:"
Write-Host "  openmain-netbird-add-client $netBirdIp $($env:COMPUTERNAME.ToLower()) <KUNDE>"
Write-Host ""
Write-Host "Sicherheit:"
Write-Host "  - NetBird selbst lauscht nur auf 127.0.0.1:$MetricsPort."
Write-Host "  - Windows Portproxy veröffentlicht nur $netBirdIp`:$MetricsPort und $netBirdIp`:$DetailPort."
Write-Host "  - Windows Firewall erlaubt nur $PrometheusIp als Quelle."
Write-Host "  - Zusätzlich NetBird Policy Monitoring -> NetBird-Metrics TCP/$MetricsPort,$DetailPort verwenden."
Write-Host "============================================================"
