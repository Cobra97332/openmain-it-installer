# OpenMain Universal Raspberry Pi → Proxmox Backup Server

Universelles Backup für Raspberry Pi 3/4/5 und andere Debian-/Raspberry-Pi-OS-Systeme auf ARMv7/ARM64.

Da der offizielle `proxmox-backup-client` auf einem x86-64-Gateway läuft, benötigt der Raspberry Pi selbst keinen inoffiziellen ARM-Build.

## Architektur

```text
Raspberry Pi (ARMv7/ARM64)
        │
        │ rsync + SSH
        ▼
x86-64 Debian/PVE Backup-Gateway
        │
        │ proxmox-backup-client
        ▼
Proxmox Backup Server
        └── host/<kunde>-<hostname>
```

## Funktionen

- automatische Backup-ID aus Kundenname + Hostname
- Standardpfade: `/etc`, `/root`, `/home`, `/opt`, `/usr/local`, `/var/www`, `/var/lib`, `/boot`, `/boot/firmware`
- System-Metadaten: OS, Pakete, Netzwerk, Mounts, systemd, Cron, nftables/iptables
- Docker-Erkennung für Named Volumes und Bind-Mounts
- Docker `overlay2`, Images und regenerierbare Cache-Daten werden nicht unnötig gesichert
- zweistufiges rsync: erster Lauf online, zweiter Delta-Lauf mit kurzer Quiesce-Phase
- optionale Quiesce-Dienste für InfluxDB, MariaDB/MySQL, PostgreSQL, Grafana und Mosquitto
- PBS-Archive: `system.pxar`, `docker.pxar`, `metadata.pxar`
- PBS-Zugangsdaten liegen ausschließlich auf dem x86-Gateway
- systemd-Service und systemd-Timer
- Restore-Helfer auf dem Gateway

## Schnellinstallation

### 1. Gateway installieren

Auf einem x86-64 Debian/PVE-System mit installiertem `proxmox-backup-client`:

```bash
curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/install-gateway-from-github.sh | bash
```

Danach:

```bash
nano /etc/openmain/rpi-pbs-gateway.conf
```

### 2. Raspberry Pi installieren

```bash
curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/install-client-from-github.sh | bash
```

Danach:

```bash
nano /etc/openmain/rpi-pbs-backup.conf
```

Mindestens setzen:

```bash
GATEWAY_HOST="100.x.x.x"
CUSTOMER="kunde"
```

Public Key anzeigen:

```bash
cat /root/.ssh/openmain-rpi-pbs.pub
```

Diesen Key auf dem Gateway in `/home/rpi-backup/.ssh/authorized_keys` eintragen.

Test:

```bash
systemctl start rpi-pbs-backup.service
journalctl -u rpi-pbs-backup.service -n 200 --no-pager
```

Timer aktivieren:

```bash
systemctl enable --now rpi-pbs-backup.timer
systemctl list-timers rpi-pbs-backup.timer
```

Standard: Samstag 03:00 Uhr plus maximal 30 Minuten Zufallsversatz.

## Dokumentation

- [HOWTO.md](HOWTO.md) – vollständige Installation und Konfiguration
- [RESTORE.md](RESTORE.md) – Wiederherstellung
- [SECURITY.md](SECURITY.md) – Sicherheitskonzept und Berechtigungen

## Verzeichnisstruktur

```text
rpi-pbs-backup/
├── README.md
├── HOWTO.md
├── RESTORE.md
├── SECURITY.md
├── install-client-from-github.sh
├── install-gateway-from-github.sh
├── client/
│   ├── install-rpi-client.sh
│   ├── rpi-pbs-backup.sh
│   ├── rpi-pbs-backup.conf.example
│   ├── rpi-pbs-backup.service
│   └── rpi-pbs-backup.timer
└── gateway/
    ├── install-gateway.sh
    ├── rpi-pbs-ingest
    ├── rpi-pbs-restore
    └── rpi-pbs-gateway.conf.example
```

## Retention

Prune-/Retention-Regeln werden zentral auf PBS konfiguriert, nicht auf jedem Raspberry Pi. Beispiel:

- 7 tägliche
- 4 wöchentliche
- 12 monatliche

## Hinweis

Das Backup ersetzt kein getestetes Restore-Verfahren. Nach Installation sollte mindestens ein Test-Restore in ein separates Verzeichnis durchgeführt werden.
