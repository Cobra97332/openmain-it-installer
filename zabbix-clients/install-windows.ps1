# OpenMain IT - Zabbix Agent 2, Windows LAN only
[CmdletBinding()]
param(
 [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9._-]{1,64}$')][string]$Customer,
 [Parameter(Mandatory)][string]$Proxy1,
 [string]$Proxy2,
 [string]$Hostname=$env:COMPUTERNAME,
 [string]$MsiPath,
 [switch]$Replace
)
$ErrorActionPreference='Stop'
$principal=[Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'PowerShell als Administrator starten.' }
foreach($ip in @($Proxy1,$Proxy2)) {
 if (-not $ip) { continue }
 $parsed=$null
 if (-not [System.Net.IPAddress]::TryParse($ip,[ref]$parsed) -or $parsed.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) { throw "Ungueltige IPv4: $ip" }
 $b=$parsed.GetAddressBytes()
 if (-not (($b[0] -eq 10) -or ($b[0] -eq 172 -and $b[1] -ge 16 -and $b[1] -le 31) -or ($b[0] -eq 192 -and $b[1] -eq 168))) { throw "Keine LAN-Adresse: $ip" }
}
if ($Proxy2 -and $Proxy1 -eq $Proxy2) { throw 'Proxy-Adressen muessen verschieden sein.' }
if ($Hostname -notmatch '^[A-Za-z0-9._-]{1,128}$') { throw 'Hostname ungueltig.' }
$active="${Proxy1}:10051"
if ($Proxy2) { $active+=";${Proxy2}:10051" }
$reachable=$false
foreach($ip in @($Proxy1,$Proxy2)) {
 if (-not $ip) { continue }
 $client=New-Object System.Net.Sockets.TcpClient
 try {
  $async=$client.BeginConnect($ip,10051,$null,$null)
  if ($async.AsyncWaitHandle.WaitOne(3000)) { try { $client.EndConnect($async); $reachable=$true } catch {} }
  $async.AsyncWaitHandle.Close()
 } finally { $client.Close() }
}
if (-not $reachable) { throw 'Beide Proxys auf TCP/10051 nicht erreichbar.' }
$service=Get-Service -Name 'Zabbix Agent 2' -ErrorAction SilentlyContinue
if (-not $service) {
 if (-not $MsiPath -or -not (Test-Path -LiteralPath $MsiPath -PathType Leaf)) { throw 'Agent 2 nicht installiert. Bitte freigegebenes MSI mittels -MsiPath angeben.' }
 $msi=(Resolve-Path -LiteralPath $MsiPath).Path
 $sig=Get-AuthenticodeSignature -FilePath $msi
 if ($sig.Status -ne 'Valid') { throw 'MSI-Signatur ungueltig.' }
 $process=Start-Process msiexec.exe -ArgumentList @('/i',('"' + $msi + '"'),'/qn','/norestart') -Wait -PassThru
 if ($process.ExitCode -notin @(0,3010)) { throw "MSI Fehler: $($process.ExitCode)" }
}
$paths=@("$env:ProgramFiles\Zabbix Agent 2\zabbix_agent2.conf","${env:ProgramFiles(x86)}\Zabbix Agent 2\zabbix_agent2.conf")
$config=$paths | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $config) { throw 'Agent-Konfiguration nicht gefunden.' }
if (-not $Replace) { throw 'Bestehende Konfiguration: nach Pruefung -Replace angeben.' }
$old=Get-Content -LiteralPath $config -Raw
if ($old -match '(?mi)^\s*(Include|TLSConnect|TLSAccept|TLSPSKFile|TLSCertFile)\s*=') { throw 'TLS/Include gefunden, manuelle Pruefung erforderlich.' }
$backup="$config.bak.$(Get-Date -Format yyyyMMddHHmmss)"
Copy-Item -LiteralPath $config -Destination $backup
$dir=Split-Path $config -Parent
$lines=@('LogType=file',('LogFile='+ (Join-Path $dir 'zabbix_agent2.log')),'Server=',('ServerActive='+$active),('Hostname='+$Hostname),('HostMetadata=OMLAN;Customer='+$Customer+';OS=Windows'),'RefreshActiveChecks=120','Timeout=10')
$temp=Join-Path $dir 'zabbix_agent2.openmain.new'
[IO.File]::WriteAllLines($temp,[string[]]$lines,(New-Object System.Text.UTF8Encoding($false)))
try {
 Move-Item -LiteralPath $temp -Destination $config -Force
 Set-Service -Name 'Zabbix Agent 2' -StartupType Automatic
 Restart-Service -Name 'Zabbix Agent 2' -ErrorAction Stop
 if ((Get-Service 'Zabbix Agent 2').Status -ne 'Running') { throw 'Dienst nicht aktiv.' }
} catch {
 Copy-Item -LiteralPath $backup -Destination $config -Force
 try { Restart-Service 'Zabbix Agent 2' } catch {}
 throw
}
Write-Host "OK: $Hostname / $Customer / $active"
Write-Host 'Autoregistrierung muss serverseitig eingerichtet sein.'
