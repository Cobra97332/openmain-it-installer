# OpenMain Universal Raspberry Pi → Proxmox Backup Server

Universelles Backup für Raspberry Pi 3/4/5 sowie Debian-/Raspberry-Pi-OS-Systeme auf ARMv7/ARM64.

**Empfohlener Aufbau:** Das Gateway läuft in einem eigenen, unprivilegierten Debian-13-LXC auf Proxmox VE. Damit bleibt der PVE-Host frei von zusätzlicher Backup-Logik und der SSH-Zugriff der Raspberry Pis endet im isolierten Gateway-CT.

## Architektur

```text
Raspberry Pi (ARMv7/ARM64)
        │
        │ rsync + SSH
        ▼
Debian 13 LXC: rpi-pbs-gateway
        │
        │ proxmox-backup-client
        ▼
Proxmox Backup Server
        └── host/<kunde>-<hostname>
```

Der PVE-Installer erstellt den CT automatisch, installiert den offiziellen Proxmox Backup Client aus dem Client-only-Repository und übernimmt die bereits auf PVE hinterlegte PBS-Storage-Konfiguration als Startkonfiguration. Proxmox dokumentiert das Client-only-Repository für Debian 13/Trixie offiziell.

## Standard-CT

```text
Hostname:      rpi-pbs-gateway
CTID:          automatisch nächste freie ID
CPU:           1 Core
RAM:           1024 MB
Swap:          512 MB
Root-Disk:     8 GB
Staging-Disk:  64 GB, separat, backup=0
OS:            Debian 13
Container:     unprivilegiert
Autostart:     ja
```

Das Staging-Volume ist absichtlich von normalen PVE-Backups ausgeschlossen, weil es nur den aktuellen Zwischenstand der Raspberry-Pi-Daten enthält. Die eigentlichen Sicherungen liegen auf PBS.

## Gateway-CT erstellen

Auf dem PVE-Host als `root`:

```bash
curl -fsSL \
https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/install-gateway-from-github.sh \
| bash
```

Der Installer erkennt automatisch:

- geeignete PVE-Storages für LXC
- Template-Storage
- Linux-Bridge
- vorhandene PBS-Storages
- nächste freie CTID
- aktuelles Debian-13-LXC-Template

Bei mehreren Storages/Bridges erscheint ein Auswahlmenü.

### Werte vorgeben

```bash
CTID=120 \
CT_HOSTNAME="rpi-pbs-gateway" \
CT_STORAGE="local-lvm" \
CT_DATA_STORAGE="local-lvm" \
CT_DATA_SIZE=100 \
CT_BRIDGE="vmbr0" \
PVE_PBS_STORAGE="PBS_Terramaster" \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/install-gateway-from-github.sh)"
```

Statische IP optional:

```bash
CT_IP_CIDR="192.168.178.30/24" \
CT_GATEWAY="192.168.178.1" \
CT_DNS_SERVER="192.168.178.1" \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/install-gateway-from-github.sh)"
```

Ohne `CT_IP_CIDR` wird DHCP verwendet.

## Raspberry Pi installieren

```bash
curl -fsSL \
https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/install-client-from-github.sh \
| bash
```

Danach:

```bash
nano /etc/openmain/rpi-pbs-backup.conf
```

Mindestens:

```bash
GATEWAY_HOST="IP-DES-GATEWAY-CT"
CUSTOMER="kunde"
```

Public Key anzeigen:

```bash
cat /root/.ssh/openmain-rpi-pbs.pub
```

Auf PVE in den Gateway-CT eintragen:

```bash
pct exec <CTID> -- nano /home/rpi-backup/.ssh/authorized_keys
```

## Funktionen

- automatische Backup-ID aus Kundenname + Hostname
- Sicherung von `/etc`, `/root`, `/home`, `/opt`, `/usr/local`, `/var/www`, `/var/lib`, `/boot`, `/boot/firmware`
- System-Metadaten: OS, Pakete, Netzwerk, Mounts, systemd, Cron, nftables/iptables
- Docker Named Volumes und Bind-Mounts automatisch erkennen
- Docker `overlay2`/Images nicht unnötig sichern
- zweistufiges rsync mit kurzer Quiesce-Phase
- InfluxDB, MariaDB/MySQL, PostgreSQL, Grafana und Mosquitto berücksichtigen
- PBS-Archive `system.pxar`, `docker.pxar`, `metadata.pxar`
- Restore-Helfer im Gateway-CT

## Direkte Installation auf dem PVE-Host

Die ältere direkte PVE-Variante bleibt als Fallback verfügbar, wird aber nicht mehr empfohlen:

```bash
curl -fsSL \
https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/install-gateway-direct-pve-from-github.sh \
| bash
```

## Dokumentation

- [HOWTO.md](HOWTO.md)
- [RESTORE.md](RESTORE.md)
- [SECURITY.md](SECURITY.md)
