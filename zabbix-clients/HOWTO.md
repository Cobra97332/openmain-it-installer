# HOWTO – Zabbix Agent 2 auf Clients im Kunden-LAN

Stand: 30.10.2026 · GitHub-Branch: `feature/zabbix-lan-client-install`

## Zweck und Geltungsbereich

Die Skripte richten **Clients** (Debian/Ubuntu, Proxmox-Hosts, Windows) für vorhandene lokale Zabbix-Proxys ein. Sie installieren **keinen** Proxy, verändern weder die NetBird-Router noch die zentrale Zabbix-Instanz und erstellen keine Proxy-Gruppen.

Die Proxys werden im Projekt bereits über `zabbix-proxy-install.sh` und `zabbix-api-register.sh` bereitgestellt. Der Proxy-zu-Server-Verkehr ist dort mit PSK konfiguriert; **Client-zu-Proxy** verwendet in diesem HOWTO bewusst unverschlüsselten Transport **nur im vertrauenswürdigen, abgeschotteten LAN**.

Die Zabbix-Hostnamen müssen **global eindeutig** sein. Mehrere Kunden können jeweils einen Rechner namens `mailcow` betreiben; daher beispielsweise `luxon-mailcow` verwenden. Der Betriebssystem-Hostname wird dadurch nicht verändert.

## Voraussetzungen

- Die übergebenen Proxy-IPs sind **tatsächlich die LAN-Adressen** von Mitgliedern derselben Zabbix-Proxy-Gruppe. Keine Adressen raten; die Zabbix-Server-IP ist nicht automatisch eine Proxy-IP.
- Client kann Proxy TCP **10051** erreichen (Active Checks).
- Proxy kann Client TCP **10050** erreichen (Passive Checks); Host-Firewall nur für Proxy-IP(s) öffnen. Die Skripte erstellen keine Firewall-Ausnahmen.
- Die Proxy-Gruppe existiert zentral bereits. Beide Proxy-IP-Adressen sind in Zabbix als „Address for active agents“ bei den jeweiligen Proxys hinterlegt.
- Bei zwei Mitgliedern verwendet `ServerActive` ein **Semikolon** als Proxy-Gruppentrenner; `Server` verwendet **Kommas** als passive Allowed-Source-Liste.
- Autoregistrierung ist **serverseitig separat einzurichten**. Die Skripte setzen nur `HostMetadata`. Eine vorhandene Host-/Template-/Proxy-Gruppen-Zuordnung wird nicht durch die Installation bewiesen.
- Skript vor Ausführung prüfen. Im produktiven Rollout möglichst einen freigegebenen Commit statt eines veränderlichen Branches referenzieren.

## Linux / Proxmox – bestehende Installation (empfohlen bei Debian 13 mit vorhandenen Agent-Plugins)

Als root, bei **einem** Proxy:

```bash
curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/feature/zabbix-lan-client-install/zabbix-clients/fix-zabbix-agent.sh -o /tmp/fix-zabbix.sh && bash /tmp/fix-zabbix.sh Luxon 192.168.40.152 '' luxon-mailcow
```

**ACHTUNG:** `192.168.40.152` ist nur der im bisherigen Test benutzte Wert und könnte die **zentrale Server-IP** sein! Durch die **echte lokale Proxy-IP** ersetzen und vor der Bestätigung kontrollieren.

Mit **zwei** lokalen Proxy-Adressen:

```bash
bash /tmp/fix-zabbix.sh Luxon 192.168.40.10 192.168.40.11 luxon-mailcow
```

`192.168.40.10` und `192.168.40.11` sind **Beispiele**, keine ermittelten Standortadressen. Der vierte Parameter ist der gewünschte **Zabbix-Hostname**. Das Reparaturskript:
- sichert die vollständige Agent-Hauptkonfiguration als `.bak.<Zeitstempel>`;
- behält bisherige `Include`-Zeilen und Plugin-Dateien bei;
- ändert `Server`, `ServerActive`, `ListenPort`, `Hostname`, `HostMetadata`;
- prüft mit `zabbix_agent2 -T`, startet den Dienst neu und rollt bei Dienststartfehler zurück;
- verweigert bewusst bestehende TLS-Konfigurationen.

Es **installiert kein Paket**. Vorhandener `zabbix-agent2` ist Voraussetzung.

## Linux / Proxmox – Neuinstallation

Das separate Skript `install-linux.sh` versucht `zabbix-agent2` über konfigurierte APT-Quellen zu installieren. Bei bestehenden Plugin-Includes ist es noch restriktiv und kann abbrechen – dann das obige Reparaturskript verwenden. Bei einer Neuinstallation müssen die Paketquellen einen passenden Agent 2 anbieten.

```bash
curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/feature/zabbix-lan-client-install/zabbix-clients/install-linux.sh -o /tmp/install-zabbix.sh && bash /tmp/install-zabbix.sh --customer Luxon --proxy1 192.168.40.10 --proxy2 192.168.40.11 --hostname luxon-server01
```

Für vorhandene Konfigurationen verlangt `install-linux.sh` `--replace`, verweigert aber vorhandene Includes/TLS-Konfigurationen weiterhin: **nicht durch manuelles Löschen der Plugin-Dateien umgehen**.

## Windows – vorhandener Agent 2

PowerShell als Administrator:

```powershell
$u='https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/feature/zabbix-lan-client-install/zabbix-clients/install-windows.ps1'; $p="$env:TEMP\install-zabbix.ps1"; Invoke-WebRequest -Uri $u -OutFile $p; & $p -Customer Luxon -Proxy1 192.168.40.10 -Proxy2 192.168.40.11 -Hostname luxon-pc01 -Replace
```

Das Windows-Skript prüft vorhandene Konfigurationen restriktiv. Bei vorhandenen `Include`- oder TLS-Anweisungen stoppt es. Bei Neuinstallation muss ein **vorher bereitgestelltes, signiertes Agent-2-MSI** mit `-MsiPath 'C:\Install\zabbix_agent2.msi'` übergeben werden. Der Download des MSI ist **noch nicht automatisiert**. Das Skript öffnet keine Windows-Firewall-Regel; für passive Checks muss TCP 10050 gezielt von den Proxy-IPs zulässig sein.

## Zielkonfiguration (Beispiel: zwei Proxys)

```ini
Server=127.0.0.1,192.168.40.10,192.168.40.11
ListenPort=10050
ServerActive=192.168.40.10:10051;192.168.40.11:10051
Hostname=luxon-mailcow
HostMetadata=OMLAN;Customer=Luxon;OS=Linux
```

`Server` gestattet passive Abfragen nur von den definierten Quell-IPs; die Host-Firewall bleibt eine zusätzliche notwendige Zugriffskontrolle. `ServerActive` ist die Zielgruppe für aktive Checks. **Nur** zusammengehörige Proxys derselben Gruppe zusammen angeben.

## Funktionstest auf Linux

```bash
zabbix_agent2 -T -c /etc/zabbix/zabbix_agent2.conf
systemctl is-active zabbix-agent2
grep -E '^(Server|ServerActive|ListenPort|Hostname|HostMetadata)=' /etc/zabbix/zabbix_agent2.conf
ss -lntp | grep ':10050'
journalctl -u zabbix-agent2 --since '10 minutes ago' --no-pager | tail -80
```

Verbindung **vom Proxy zum Client** auf TCP 10050 zusätzlich prüfen. Ein erfolgreicher Test vom Client zu TCP 10051 belegt **nicht** den passiven Rückweg.

### Docker-Monitoring auf Linux

```bash
id zabbix
runuser -u zabbix -- zabbix_agent2 -c /etc/zabbix/zabbix_agent2.conf -t docker.info
```

Wenn der Docker-Socket für den Agent nicht lesbar ist, ist die Docker-Gruppe **eine mögliche, aber hochprivilegierte** Lösung: Gruppenmitgliedschaft gewährt praktisch root-ähnliche Rechte auf dem Docker-Host. Für produktive Mailserver nur nach Sicherheitsabwägung oder mit eingeschränkter Alternative:

```bash
usermod -aG docker zabbix
systemctl restart zabbix-agent2
```

Das Ergebnis `docker.info [s|{...}]` bestätigt die lokale Abfrage. `KernelMemory=false` und `KernelMemoryTCP=false` in Docker-Informationen sind keine Beweise für einen Agentfehler.

## Autoregistrierung und Templates

Die Parameter `HostMetadata=OMLAN;Customer=Luxon;OS=Linux` dienen der Erkennung. Auf dem **Zabbix-Server** müssen Autoregistrierungsaktionen bzw. eine geprüfte API-Registrierung vorhanden sein, die:
1. nur erwartete Metadaten akzeptiert;
2. den Host der richtigen Kunden-Hostgruppe und **bestehenden Proxy-Gruppe** zuweist;
3. das passende Linux-/Windows-Active-Template zuweist und für passive Items einen erreichbaren Agent-Interface-Endpunkt (Client-IP:10050) hat;
4. einen **global eindeutigen** Hostnamen sicherstellt.

**Sicherheitsgrenze:** Metadata ist clientseitig beeinflussbar und kein belastbarer Kundenauthentisierungsnachweis. Für eine Mandantenumgebung zusätzlich Proxy-/Netzwerkzuordnung verifizieren; keine kundenübergreifenden automatischen Berechtigungen allein anhand von Metadata.

Zabbix UI: `Data collection → Hosts`; `Monitoring → Latest data`. Active Checks können `Available` sein, während passive Checks `Not available` melden. Dann `Server=`, Proxy-Quell-IP, Proxy-Zuordnung, Firewall, Agent-Port und Template-Item-Typ prüfen.

## Fehlerbehebung

- `Bestehende Konfiguration: nur mit --replace`: beim Neuinstaller vorhandenes Setup erkannt. Bei Plugin-Includes das Reparaturskript verwenden.
- `Zusatzkonfiguration vorhanden`: der Neuinstaller blockiert bewusst Includes; nichts löschen.
- `Permission denied` beim manuellen Test: immer `-c /etc/zabbix/zabbix_agent2.conf` mit absolutem Pfad übergeben; auch Datei-/Verzeichnisrechte prüfen.
- `Connection reset by peer` für passive Checks: Quell-IP in `Server=`, Firewall sowie TCP 10050 prüfen.
- Falscher Hostname: beim Reparaturskript ist es der **vierte Positionsparameter** (der dritte muss bei nur einem Proxy als `''` mitgegeben werden).
- Agent läuft, Host nicht in Zabbix: Autoregistrierungsaktion/Proxy-Gruppe/Metadaten und Zabbix-Server-/Proxy-Logs prüfen.

## Status / Limitierungen

- GitHub-Branch ist **Teststand**, nicht `main`.
- Eine Hostinstallation auf `luxon-mailcow` und `docker.info` wurden manuell erfolgreich geprüft; **nicht** alle aktuellen Varianten von Windows/Proxmox/Neuinstallation.
- Installation und Konfiguration allein erstellen noch keine serverseitige Autoregistrierungsaktion.
- Keine automatischen Firewall-Regeln, keine automatische Windows-MSI-Bereitstellung.
- Wenn nur ein Proxy verwendet wird, entfällt der zweite Proxy-Parameter bzw. wird beim Reparaturskript als `''` übergeben.
