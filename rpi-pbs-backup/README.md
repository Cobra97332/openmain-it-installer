# OpenMain Universal Raspberry Pi → Proxmox Backup Server

Universelles Backup für Raspberry Pi 3/4/5 und Debian-/Raspberry-Pi-OS-Systeme auf ARMv7/ARM64.

Der Backup-Gateway wird **direkt auf einem Proxmox-VE-Host** installiert. Der Raspberry Pi überträgt seine Daten per `rsync`/SSH auf den PVE-Host; dort schreibt der offizielle `proxmox-backup-client` das Backup auf den bereits in PVE eingerichteten Proxmox Backup Server.

## Architektur

```text
Raspberry Pi (ARMv7/ARM64)
        │
        │ rsync + SSH
        ▼
Proxmox VE Host (x86-64)
        │
        │ proxmox-backup-client
        │ vorhandenes PVE-PBS-Storage
        ▼
Proxmox Backup Server
        └── host/<kunde>-<hostname>
```

## PVE-Gateway

Der Installer erkennt automatisch die in `/etc/pve/storage.cfg` eingetragenen PBS-Storages. Bei mehreren PBS-Storages wird eine Auswahl angezeigt.

Verwendet werden direkt die bestehende PVE-Konfiguration und die zugehörige Credential-Datei:

```text
/etc/pve/storage.cfg
/etc/pve/priv/storage/<STORAGE-ID>.pw
```

Das PBS-Passwort bzw. Token-Secret wird **nicht** in das Git-Repository und nicht zusätzlich nach `/etc/openmain` kopiert.

### Installation auf dem PVE-Host

```bash
curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/install-gateway-from-github.sh | bash
```

Bei mehreren PBS-Storages kann die Auswahl auch vorgegeben werden:

```bash
PVE_PBS_STORAGE="PBS_Terramaster" \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/install-gateway-from-github.sh)"
```

Neu konfigurieren:

```bash
curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/install-gateway-from-github.sh | bash -s -- --reconfigure
```

Die erzeugte Konfiguration liegt unter:

```text
/etc/openmain/rpi-pbs-gateway.conf
```

Standard-Staging:

```text
/var/lib/openmain-rpi-pbs/staging
```

Für mehrere oder große Raspberry Pis sollte `RPI_PBS_STAGING_BASE` auf ein ausreichend großes lokales Dateisystem gelegt werden.

## Funktionen

- direkte Installation auf Proxmox VE
- automatische Erkennung vorhandener PVE-PBS-Storages
- Wiederverwendung der vorhandenen PVE-PBS-Zugangsdaten
- automatische Backup-ID aus Kundenname + Hostname
- Standardpfade: `/etc`, `/root`, `/home`, `/opt`, `/usr/local`, `/var/www`, `/var/lib`, `/boot`, `/boot/firmware`
- System-Metadaten: OS, Pakete, Netzwerk, Mounts, systemd, Cron, nftables/iptables
- Docker-Erkennung für Named Volumes und Bind-Mounts
- Docker `overlay2`, Images und regenerierbare Cache-Daten werden nicht unnötig gesichert
- zweistufiges rsync: erster Lauf online, zweiter Delta-Lauf mit kurzer Quiesce-Phase
- optionale Quiesce-Dienste für InfluxDB, MariaDB/MySQL, PostgreSQL, Grafana und Mosquitto
- PBS-Archive: `system.pxar`, `docker.pxar`, `metadata.pxar`
- systemd-Service und systemd-Timer auf dem Raspberry Pi
- Restore-Helfer auf dem PVE-Gateway

## Raspberry Pi installieren

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

Empfohlen ist als `GATEWAY_HOST` die NetBird-IP des PVE-Nodes oder eine dedizierte Management-IP.

Public Key anzeigen:

```bash
cat /root/.ssh/openmain-rpi-pbs.pub
```

Auf dem PVE-Host eintragen:

```bash
nano /home/rpi-backup/.ssh/authorized_keys
```

Backup testen:

```bash
systemctl start rpi-pbs-backup.service
journalctl -u rpi-pbs-backup.service -n 200 --no-pager
```

## Dokumentation

- [HOWTO.md](HOWTO.md) – vollständige Installation und Konfiguration
- [RESTORE.md](RESTORE.md) – Wiederherstellung
- [SECURITY.md](SECURITY.md) – Sicherheitskonzept und Berechtigungen

## Retention

Prune-/Retention-Regeln werden zentral auf PBS konfiguriert. Beispiel:

- 7 tägliche
- 4 wöchentliche
- 12 monatliche

Zusätzlich regelmäßige PBS-Verify-Jobs konfigurieren.
