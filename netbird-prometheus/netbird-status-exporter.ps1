#requires -Version 5.1

[CmdletBinding()]
param(
    [string]$ListenAddress = "127.0.0.1",
    [ValidateRange(1, 65535)][int]$Port = 9192,
    [string]$NetBirdExe = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-NetBirdExecutable {
    if ($NetBirdExe) { return $NetBirdExe }

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

function Escape-PrometheusLabel {
    param([AllowNull()][object]$Value)
    if ($null -eq $Value) { return "" }

    $result = [string]$Value
    $result = $result.Replace("\", "\\")
    $result = $result.Replace([Environment]::NewLine, "\n")
    $result = $result.Replace('"', '\"')
    return $result
}

function Get-HandshakeTimestamp {
    param([AllowNull()][object]$Value)

    if ($null -eq $Value) { return 0 }
    $text = [string]$Value
    if (-not $text -or $text.StartsWith("0001-01-01")) { return 0 }

    try {
        return [DateTimeOffset]::Parse($text).ToUnixTimeMilliseconds() / 1000.0
    }
    catch {
        return 0
    }
}

function Get-NetBirdStatusMetrics {
    param([Parameter(Mandatory = $true)][string]$Executable)

    $jsonText = (& $Executable status --json 2>$null | Out-String)
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($jsonText)) {
        throw "'netbird status --json' ist fehlgeschlagen."
    }

    $status = $jsonText | ConvertFrom-Json
    $lines = New-Object System.Collections.Generic.List[string]

    $lines.Add("# HELP openmain_netbird_status_exporter_up Whether the detailed NetBird status exporter could query the local daemon.")
    $lines.Add("# TYPE openmain_netbird_status_exporter_up gauge")
    $lines.Add("openmain_netbird_status_exporter_up 1")
    $lines.Add("# HELP openmain_netbird_peer_connection_info Connected NetBird peer relation with connection type and relay address.")
    $lines.Add("# TYPE openmain_netbird_peer_connection_info gauge")
    $lines.Add("# HELP openmain_netbird_peer_transfer_received_bytes WireGuard bytes received from a connected peer.")
    $lines.Add("# TYPE openmain_netbird_peer_transfer_received_bytes gauge")
    $lines.Add("# HELP openmain_netbird_peer_transfer_sent_bytes WireGuard bytes sent to a connected peer.")
    $lines.Add("# TYPE openmain_netbird_peer_transfer_sent_bytes gauge")
    $lines.Add("# HELP openmain_netbird_peer_last_handshake_timestamp_seconds Unix timestamp of the last WireGuard handshake.")
    $lines.Add("# TYPE openmain_netbird_peer_last_handshake_timestamp_seconds gauge")

    $connectedCount = 0
    $relayCount = 0
    $details = @()
    if ($status.peers -and $status.peers.details) { $details = @($status.peers.details) }

    foreach ($peer in $details) {
        if (([string]$peer.status).ToLowerInvariant() -ne "connected") { continue }

        $connectionType = ([string]$peer.connectionType).Trim().ToLowerInvariant()
        if ($connectionType -eq "relayed") {
            $connectionType = "relay"
        }
        elseif ($connectionType -eq "p2p") {
            $connectionType = "p2p"
        }
        elseif (-not $connectionType) {
            $connectionType = "unknown"
        }

        $connectedCount++
        if ($connectionType -eq "relay") { $relayCount++ }

        $peerName = [string]$peer.fqdn
        if (-not $peerName) { $peerName = [string]$peer.netbirdIp }
        if (-not $peerName) { $peerName = "unknown" }

        $labelText = @(
            'peer="' + (Escape-PrometheusLabel $peerName) + '"'
            'peer_ip="' + (Escape-PrometheusLabel $peer.netbirdIp) + '"'
            'connection_type="' + (Escape-PrometheusLabel $connectionType) + '"'
            'relay_address="' + (Escape-PrometheusLabel $peer.relayAddress) + '"'
        ) -join ","

        $received = 0
        $sent = 0
        if ($null -ne $peer.transferReceived) { $received = [Int64]$peer.transferReceived }
        if ($null -ne $peer.transferSent) { $sent = [Int64]$peer.transferSent }
        $handshake = Get-HandshakeTimestamp $peer.lastWireguardHandshake

        $lines.Add("openmain_netbird_peer_connection_info{$labelText} 1")
        $lines.Add("openmain_netbird_peer_transfer_received_bytes{$labelText} $received")
        $lines.Add("openmain_netbird_peer_transfer_sent_bytes{$labelText} $sent")
        $lines.Add("openmain_netbird_peer_last_handshake_timestamp_seconds{$labelText} $handshake")
    }

    $lines.Add("# HELP openmain_netbird_connected_peer_relations Number of connected peer relations visible from this client.")
    $lines.Add("# TYPE openmain_netbird_connected_peer_relations gauge")
    $lines.Add("openmain_netbird_connected_peer_relations $connectedCount")
    $lines.Add("# HELP openmain_netbird_relay_peer_relations Number of relayed peer relations visible from this client.")
    $lines.Add("# TYPE openmain_netbird_relay_peer_relations gauge")
    $lines.Add("openmain_netbird_relay_peer_relations $relayCount")

    return ($lines -join [Environment]::NewLine) + [Environment]::NewLine
}

function Send-HttpResponse {
    param(
        [Parameter(Mandatory = $true)]$Stream,
        [Parameter(Mandatory = $true)][int]$StatusCode,
        [Parameter(Mandatory = $true)][string]$Reason,
        [Parameter(Mandatory = $true)][string]$Body
    )

    $bodyBytes = [Text.Encoding]::UTF8.GetBytes($Body)
    $crlf = [string][char]13 + [string][char]10
    $header = "HTTP/1.1 $StatusCode $Reason" + $crlf +
              "Content-Type: text/plain; version=0.0.4; charset=utf-8" + $crlf +
              "Content-Length: $($bodyBytes.Length)" + $crlf +
              "Connection: close" + $crlf + $crlf
    $headerBytes = [Text.Encoding]::ASCII.GetBytes($header)

    $Stream.Write($headerBytes, 0, $headerBytes.Length)
    $Stream.Write($bodyBytes, 0, $bodyBytes.Length)
    $Stream.Flush()
}

$resolvedNetBirdExe = Get-NetBirdExecutable
$ipAddress = [Net.IPAddress]::Parse($ListenAddress)
$listener = [Net.Sockets.TcpListener]::new($ipAddress, $Port)
$listener.Start()

try {
    while ($true) {
        $client = $listener.AcceptTcpClient()
        try {
            $client.ReceiveTimeout = 10000
            $client.SendTimeout = 10000
            $stream = $client.GetStream()
            $reader = New-Object IO.StreamReader($stream, [Text.Encoding]::ASCII, $false, 1024, $true)

            $requestLine = $reader.ReadLine()
            while ($true) {
                $line = $reader.ReadLine()
                if ($null -eq $line -or $line -eq "") { break }
            }

            if ($requestLine -notmatch '^GET\s+/metrics/?(?:\s|\?)') {
                Send-HttpResponse -Stream $stream -StatusCode 404 -Reason "Not Found" -Body ("not found" + [Environment]::NewLine)
                continue
            }

            try {
                $body = Get-NetBirdStatusMetrics -Executable $resolvedNetBirdExe
                Send-HttpResponse -Stream $stream -StatusCode 200 -Reason "OK" -Body $body
            }
            catch {
                $message = ([string]$_.Exception.Message).Replace([Environment]::NewLine, " ")
                $body = "# HELP openmain_netbird_status_exporter_up Whether the detailed NetBird status exporter could query the local daemon." + [Environment]::NewLine +
                        "# TYPE openmain_netbird_status_exporter_up gauge" + [Environment]::NewLine +
                        "openmain_netbird_status_exporter_up 0" + [Environment]::NewLine +
                        "# ERROR $message" + [Environment]::NewLine
                Send-HttpResponse -Stream $stream -StatusCode 500 -Reason "Internal Server Error" -Body $body
            }
        }
        finally {
            $client.Close()
        }
    }
}
finally {
    $listener.Stop()
}
