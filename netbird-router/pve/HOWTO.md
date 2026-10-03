# HowTo: NetBird Kundenrouter als Proxmox-LXC

Dieses HowTo beschreibt die beiden aktuellen PVE-Installer aus diesem öffentlichen Repository:

- `install.sh` – NetBird-Router **mit Zabbix Proxy/Agent**
- `install-no-zabbix.sh` – NetBird-Router **ohne Zabbix**

Das Skript erstellt automatisch einen Debian-13-LXC, installiert und verbindet
NetBird, richtet IPv4-Forwarding und nftables-BINAT ein und registriert den
Container als Primary- oder Backup-Router in NetBird.

## 1. Voraussetzungen

Auf dem Proxmox-Host werden benötigt:

- Proxmox VE mit `pct`, `pveam`, `pvesm` und `pvesh`
- Root-Zugriff auf den PVE-Host
- Internetzugang zum Laden des Debian-13-LXC-Templates und der NetBird-Pakete
- aktive PVE-Bridge, standardmäßig `vmbr0`
- ein Storage mit Content-Typ `rootdir`
- ein Storage mit Content-Typ `vztmpl`
- `/dev/net/tun` auf dem PVE-Host
- NetBird Setup-Key
- NetBird API-Token
- Zabbix API-Token **nur bei `install.sh` mit Zabbix**; bei `install-no-zabbix.sh` nicht erforderlich
- NetBird Management URL: `https://netbird.openmain-it.de`

Das Skript erkennt automatisch, ob der PVE-Host `amd64` oder `arm64` verwendet
und lädt nur das passende Debian-13-Template.

## 2. Was wird automatisch angelegt?

Beispiel Kunde/Firma `Taxi Leykamm`:

- LXC-Container mit Debian 13
- NetBird-Peer, z. B. `nb-taxi-1`
- NetBird-Gruppe `Kunden`
- NetBird-Gruppe `Taxi Leykamm`
- der Peer wird beiden Gruppen zugeordnet
- NetBird-Network `Taxi Leykamm`
- Resource `BINAT-Taxi Leykamm`
- die Resource wird ebenfalls den Gruppen `Kunden` und `Taxi Leykamm` zugeordnet
- Primary-Router mit Metric 100 oder Backup-Router mit Metric 200
- `net.ipv4.ip_forward=1`
- nftables-BINAT-Regeln

Für ein Kunden-LAN wie:

```text
192.168.0.0/24
```

wird automatisch ein freies virtuelles `/24` aus dem Bereich `10.30.0.0/16`
vergeben, zum Beispiel:

```text
10.30.2.0/24 <-> 192.168.0.0/24
```

Für größere Netze von `/16` bis `/23` wird ein passender Bereich ab `10.40.0.0`
verwendet.

## 3. Installation direkt von GitHub (Copy & Paste)

Alle folgenden Befehle werden direkt auf dem Proxmox-VE-Host als `root` ausgeführt.

### Variante A: mit Zabbix

**Download-Links:**

- [install.sh direkt herunterladen](https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/netbird-router/pve/install.sh)
- [install.sh auf GitHub öffnen](https://github.com/Cobra97332/openmain-it-installer/blob/main/netbird-router/pve/install.sh)

#### Mit Zabbix herunterladen

Kompletten Block kopieren:

```bash
set -e
cd /root
command -v curl >/dev/null || { apt update && apt install -y curl; }
curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/netbird-router/pve/install.sh -o install.sh
chmod 700 install.sh
```

#### Mit Zabbix: Download + Primary direkt installieren

Nur `nb-kunde-1` und `Kunde GmbH` anpassen:

```bash
set -e
cd /root
command -v curl >/dev/null || { apt update && apt install -y curl; }
curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/netbird-router/pve/install.sh -o install.sh
chmod 700 install.sh
./install.sh --role primary --hostname nb-kunde-1 --customer "Kunde GmbH"
```

Bei dieser Variante werden abgefragt:

```text
NetBird Setup Key:
NetBird API Token:
Zabbix API Token:
```

---

### Variante B: ohne Zabbix

Diese Variante installiert nur den NetBird-Kundenrouter mit BINAT. Es werden **kein Zabbix Proxy und kein Zabbix Agent** installiert.

**Download-Links:**

- [install-no-zabbix.sh direkt herunterladen](https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/netbird-router/pve/install-no-zabbix.sh)
- [install-no-zabbix.sh auf GitHub öffnen](https://github.com/Cobra97332/openmain-it-installer/blob/main/netbird-router/pve/install-no-zabbix.sh)

#### Ohne Zabbix herunterladen

Kompletten Block kopieren:

```bash
set -e
cd /root
command -v curl >/dev/null || { apt update && apt install -y curl; }
curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/netbird-router/pve/install-no-zabbix.sh -o install-no-zabbix.sh
chmod 700 install-no-zabbix.sh
```

#### Ohne Zabbix: Download + Primary direkt installieren

Nur `nb-kunde-1` und `Kunde GmbH` anpassen:

```bash
set -e
cd /root
command -v curl >/dev/null || { apt update && apt install -y curl; }
curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/netbird-router/pve/install-no-zabbix.sh -o install-no-zabbix.sh
chmod 700 install-no-zabbix.sh
./install-no-zabbix.sh --role primary --hostname nb-kunde-1 --customer "Kunde GmbH"
```

Bei dieser Variante werden nur abgefragt:

```text
NetBird Setup Key:
NetBird API Token:
```

Ein Zabbix API-Token wird **nicht** benötigt.

---

### Welche Variante verwenden?

| Variante | NetBird | BINAT | Zabbix Proxy | Zabbix Agent | Zabbix API-Token |
|---|---:|---:|---:|---:|---:|
| `install.sh` | Ja | Ja | Ja | Ja | Ja |
| `install-no-zabbix.sh` | Ja | Ja | Nein | Nein | Nein |

Die Zugangsdaten werden interaktiv und verdeckt abgefragt. Dadurch müssen Setup-Key und API-Tokens nicht direkt in die Shell-History geschrieben werden.

Optional können die heruntergeladenen Skripte vor dem Start geprüft werden:

```bash
head -n 20 /root/install.sh
head -n 20 /root/install-no-zabbix.sh
```

## 4. Primary-Router installieren

Der Primary muss bei einem neuen Kunden immer zuerst eingerichtet werden.

Mit Zabbix:

```bash
./install.sh \
  --role primary \
  --hostname nb-taxi-1 \
  --customer "Taxi Leykamm"
```

Ohne Zabbix:

```bash
./install-no-zabbix.sh \
  --role primary \
  --hostname nb-taxi-1 \
  --customer "Taxi Leykamm"
```

Wenn keine CT-ID angegeben wird, wählt das Skript automatisch die nächste freie
Proxmox-CT-ID.

Während der Installation mit Zabbix werden NetBird Setup-Key, NetBird API-Token und Zabbix API-Token abgefragt. Bei der No-Zabbix-Variante werden nur NetBird Setup-Key und NetBird API-Token benötigt.

Die Eingabe wird nicht sichtbar angezeigt.

Der Primary erhält standardmäßig:

```text
Metric: 100
```

## 5. Backup-Router installieren

Der Backup-Router wird erst nach dem Primary eingerichtet.

Wichtig: `--customer` muss exakt denselben Wert wie beim Primary haben.

Mit Zabbix:

```bash
./install.sh \
  --role backup \
  --hostname nb-taxi-2 \
  --customer "Taxi Leykamm"
```

Ohne Zabbix:

```bash
./install-no-zabbix.sh \
  --role backup \
  --hostname nb-taxi-2 \
  --customer "Taxi Leykamm"
```

Der Backup übernimmt das bereits vorhandene NetBird-Network und dasselbe
virtuelle BINAT-Netz. Es wird kein zweites Mapping erzeugt.

Der Backup erhält standardmäßig:

```text
Metric: 200
```

Damit ergibt sich:

```text
Taxi Leykamm
|
+-- Primary  nb-taxi-1  Metric 100
|
+-- Backup   nb-taxi-2  Metric 200
|
+-- Resource BINAT-Taxi Leykamm
    10.30.x.0/24 -> 192.168.x.0/24
```

## 6. Primary und Backup auf zwei PVE-Nodes

Für echte Ausfallsicherheit sollten Primary und Backup möglichst nicht auf
demselben PVE-Host laufen.

Empfohlen:

```text
PVE1
+-- nb-taxi-1  Primary

PVE2
+-- nb-taxi-2  Backup
```

Beide Router müssen Zugang zum gleichen Kunden-LAN haben und denselben
`--customer`-Namen verwenden.

## 7. Feste CT-ID verwenden

Primary:

```bash
./install.sh \
  --role primary \
  --ctid 201 \
  --hostname nb-taxi-1 \
  --customer "Taxi Leykamm"
```

Backup:

```bash
./install.sh \
  --role backup \
  --ctid 202 \
  --hostname nb-taxi-2 \
  --customer "Taxi Leykamm"
```

Existiert die angegebene CT-ID bereits, bricht das Skript aus Sicherheitsgründen
ab und überschreibt keinen vorhandenen Container.

## 8. Netzwerk des LXC

Standardmäßig verwendet das Skript:

```text
Bridge: vmbr0
IP:     DHCP
```

Andere Bridge:

```bash
./install.sh \
  --role primary \
  --hostname nb-taxi-1 \
  --customer "Taxi Leykamm" \
  --bridge vmbr1
```

### Statische IP

Beispiel:

```bash
./install.sh \
  --role primary \
  --hostname nb-taxi-1 \
  --customer "Taxi Leykamm" \
  --ip 192.168.0.250/24 \
  --gateway 192.168.0.1
```

Das Skript erkennt anschließend das direkt verbundene LAN automatisch.

## 9. PVE-Storage

Das Skript erkennt normalerweise automatisch:

- einen aktiven Storage für LXC-RootFS (`rootdir`)
- einen aktiven Storage für LXC-Templates (`vztmpl`)

Prüfen:

```bash
pvesm status --content rootdir --enabled 1
pvesm status --content vztmpl --enabled 1
```

Falls gewünscht, kann der Storage explizit vorgegeben werden:

```bash
./install.sh \
  --role primary \
  --hostname nb-taxi-1 \
  --customer "Taxi Leykamm" \
  --storage ZFS \
  --template-storage local
```

## 10. Ressourcen des LXC anpassen

Standardwerte:

```text
CPU:       1 Core
RAM:       512 MB
Swap:      256 MB
Disk:      4 GB
Autostart: aktiviert
```

Beispiel mit mehr Ressourcen:

```bash
./install.sh \
  --role primary \
  --hostname nb-taxi-1 \
  --customer "Taxi Leykamm" \
  --cores 2 \
  --memory 1024 \
  --swap 512 \
  --disk 8
```

## 11. NetBird-Management-URL

Standardmäßig ist hinterlegt:

```text
https://netbird.openmain-it.de
```

Falls nötig kann eine andere URL angegeben werden:

```bash
--management-url https://netbird.example.de
```

## 12. Setup-Key und API-Token ohne interaktive Eingabe

Für automatisierte Installationen können die Werte über Umgebungsvariablen
gesetzt werden.

Mit Zabbix:

```bash
export NB_SETUP_KEY='DEIN_SETUP_KEY'
export NB_API_TOKEN='DEIN_API_TOKEN'
export NB_ZABBIX_API_TOKEN='DEIN_ZABBIX_API_TOKEN'

./install.sh \
  --role primary \
  --hostname nb-taxi-1 \
  --customer "Taxi Leykamm"
```

Ohne Zabbix:

```bash
export NB_SETUP_KEY='DEIN_SETUP_KEY'
export NB_API_TOKEN='DEIN_API_TOKEN'

./install-no-zabbix.sh \
  --role primary \
  --hostname nb-taxi-1 \
  --customer "Taxi Leykamm"
```

Nach Möglichkeit keine Tokens direkt als Kommandozeilenargument verwenden, weil
sie dadurch in der Shell-History sichtbar werden können.

## 13. NetBird-Gruppen

Das Skript verwendet automatisch zwei Gruppen:

```text
Kunden
<Firmenname>
```

Beispiel:

```text
Kunden
Taxi Leykamm
```

Peer und Resource werden beiden Gruppen zugeordnet.

Die frühere Option:

```text
--resource-group
```

ist nur noch aus Kompatibilitätsgründen vorhanden und wird ignoriert.

## 14. nftables/BINAT

Im Container wird die Tabelle `mein_binat` erzeugt.

Beispiel:

```nft
table ip mein_binat {
    chain prerouting {
        type nat hook prerouting priority dstnat; policy accept;
        iifname "wt0" dnat ip prefix to ip daddr map {
            10.30.2.0/24 : 192.168.0.0/24
        }
    }

    chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;
        oifname "eth0" ip saddr 100.64.0.0/10 counter masquerade
    }
}
```

Der tatsächliche NetBird-Interface-Name wird nach Möglichkeit automatisch aus
der lokalen NetBird-Daemon-API ermittelt; `wt0` ist der Fallback.

Die NetBird-Router-Option `masquerade` wird bewusst nicht für das Kunden-LAN
verwendet. Das Source-NAT übernimmt die lokale nftables-Regel.

## 15. Installation prüfen

Status des Containers:

```bash
pct status 201
```

NetBird-Status:

```bash
pct exec 201 -- netbird status
```

IPv4-Forwarding:

```bash
pct exec 201 -- cat /proc/sys/net/ipv4/ip_forward
```

Erwartet:

```text
1
```

nftables-Regeln:

```bash
pct exec 201 -- nft list table ip mein_binat
```

IP des Containers:

```bash
pct exec 201 -- ip -4 addr show eth0
```

Shell öffnen:

```bash
pct enter 201
```

## 16. Failover testen

Zuerst sicherstellen, dass beide Router in NetBird vorhanden sind:

```text
Primary  Metric 100
Backup   Metric 200
```

Von einem NetBird-Client eine Adresse im virtuellen Netz dauerhaft anpingen,
z. B.:

```bash
ping 10.30.2.10
```

Danach den Primary stoppen:

```bash
pct stop 201
```

NetBird sollte anschließend den Backup-Router verwenden. Bereits bestehende
TCP-Sitzungen können bei einem Routerwechsel neu aufgebaut werden müssen.

Primary wieder starten:

```bash
pct start 201
```

## 17. Wichtige Dateien im Container

```text
/etc/nftables.conf
/etc/nftables.d/netbird-kundenrouter.nft
/etc/sysctl.d/99-netbird-kundenrouter.conf
/etc/netbird-kundenrouter.conf
/var/lib/netbird-kundenrouter/state.json
```

Der Setup-Key und API-Token werden nicht dauerhaft in der Router-Konfigurationsdatei
gespeichert.

## 18. Häufige Fehler

### `Exec format error - Failed to exec /sbin/init`

Ursache: falsches LXC-Template für die CPU-Architektur.

Das aktuelle Skript erkennt automatisch:

```text
x86_64  -> amd64
aarch64 -> arm64
```

### `storage 'local-lvm' is not available`

Das aktuelle Skript sucht automatisch einen aktiven `rootdir`-Storage.

Prüfen:

```bash
pvesm status --content rootdir --enabled 1
```

### `/dev/net/tun ist im Container nicht sichtbar`

Auf dem PVE-Host prüfen:

```bash
ls -l /dev/net/tun
modprobe tun
```

Container-Konfiguration prüfen:

```bash
grep -E 'tun|10:200' /etc/pve/lxc/CTID.conf
```

Erwartet:

```text
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
```

### `permission denied` bei verschiedenen sysctl-Werten

Ein LXC darf viele globale Kernel-Parameter des PVE-Hosts nicht ändern.
Das Skript setzt deshalb gezielt nur:

```text
net.ipv4.ip_forward=1
```

### `/var/run/netbird.sock: no such file or directory`

Der NetBird-Daemon war noch nicht vollständig gestartet. Die aktuelle Version
wartet auf den Daemon/Socket, bevor `netbird up` ausgeführt wird.

### Backup findet kein bestehendes Mapping

Prüfen:

1. Primary wurde zuerst vollständig eingerichtet.
2. `--customer` ist bei Primary und Backup exakt identisch.
3. Network und Resource sind im NetBird-Dashboard vorhanden.

Beispiel: Primary mit `--customer "Taxi Leykamm"` und Backup ebenfalls exakt
mit `--customer "Taxi Leykamm"`.

## 19. Container entfernen

Achtung: Dadurch wird der LXC auf Proxmox gelöscht. NetBird-Objekte werden damit
nicht automatisch aus NetBird gelöscht.

```bash
pct stop 201
pct destroy 201 --purge
```

## 20. Empfohlenes Schema pro Kunde

```text
Firma: Taxi Leykamm

NetBird Gruppen:
- Kunden
- Taxi Leykamm

Network:
- Taxi Leykamm

Resource:
- BINAT-Taxi Leykamm

Primary:
- Hostname: nb-taxi-1
- Metric: 100

Backup:
- Hostname: nb-taxi-2
- Metric: 200
```

Für den nächsten Kunden wird einfach ein anderer Firmenname verwendet, z. B.:

```bash
./install.sh \
  --role primary \
  --hostname nb-muster-1 \
  --customer "Muster GmbH"
```

Das Skript erzeugt beziehungsweise verwendet dann automatisch die Gruppe
`Muster GmbH`, zusätzlich zur globalen Gruppe `Kunden`.
