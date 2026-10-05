# PVE Config Backup to PBS

Universelles Host-Konfigurationsbackup für Proxmox VE auf einen Proxmox Backup Server.

Das Projekt ergänzt normale VM-/LXC-Backups um die PVE-Hostkonfiguration. Schwerpunkt ist die Wiederherstellbarkeit der Netzwerkumgebung inklusive zusätzlicher IPv4-/IPv6-Adressen, Bridges, VLANs und Routing-Regeln.

## Universeller Einsatz

Es sind keine festen Werte für PBS-Storage, Server, Datastore oder Namespace im Skript hinterlegt.

- genau ein aktiver PBS-Storage: automatische Erkennung
- mehrere PBS-Storages: `PBS_STORAGE_ID` setzen
- Server, Datastore, Benutzer und Fingerprint: aus `/etc/pve/storage.cfg`
- Namespace: automatisch aus dem PBS-Storage
- PBS-Secret: vorhandene PVE-Secret-Datei
- Backup-ID: standardmäßig `<hostname>-config`
- optional `CUSTOMER_ID` für eindeutige Kunden-Zuordnung

## Netzwerkdaten

Gesichert werden unter anderem:

- `/etc/network/`
- `/etc/iproute2/`
- `/etc/sysctl.conf` und `/etc/sysctl.d/`
- nftables/iptables-Konfigurationen
- aktive IPv4-/IPv6-Adressen
- alle Routing-Tabellen
- Policy-Routing-Regeln
- Bridges und VLANs

## Weitere Daten

- `/etc/pve/`
- `/etc/vzdump.conf`
- Corosync/Ceph-Konfiguration, falls vorhanden
- ZFS/LVM
- Boot-/Kernel-Konfiguration
- SSH-Serverkonfiguration
- systemd-Units und cron
- APT-Konfiguration
- eigene Skripte unter `/usr/local/sbin` und `/usr/local/bin`

## Installation

Siehe [HOWTO.md](HOWTO.md).

Kurzfassung:

```bash
apt update
apt install -y git

cd /opt
git clone --depth 1 https://github.com/Cobra97332/openmain-it-installer.git
cd /opt/openmain-it-installer/pve-config-backup

bash install.sh

# Der Installer führt danach automatisch aus:
# - Syntaxprüfung
# - systemd-Prüfung
# - PVE/PBS-Preflight
# - SOFORT ein echtes Backup auf den PBS
# - Prüfung des Backup-Service
# - Aktivierung und Prüfung des Timers
```

Der Installer fragt interaktiv nach:

- Kundenname/ID (optional)
- gewünschtem PBS-Storage

Es sind keine Installationsargumente mehr notwendig.

```bash
bash install.sh
```

## Sicherheit

Das Backup enthält sensible PVE-Konfigurationen und kann Secrets aus `/etc/pve/priv/` enthalten. Keine Zugangsdaten oder Verschlüsselungsschlüssel im öffentlichen GitHub-Repository ablegen.


## Verhalten nach der Installation

Wenn `./install.sh` ohne Fehler endet, ist die Einrichtung vollständig abgeschlossen.

Der Installer führt automatisch folgende Schritte durch:

1. Bash-Syntaxprüfung
2. systemd-Unit-Prüfung
3. PVE/PBS-Konfigurationscheck
4. sofortiges echtes Konfigurationsbackup auf den PBS
5. Prüfung von Service-Result und Exitcode
6. Aktivierung und Prüfung des stündlichen Timers

Schlägt einer dieser Schritte fehl, wird der Timer deaktiviert und die Installation mit Fehler beendet.

Zur Kontrolle kann jederzeit ausgeführt werden:

```bash
journalctl -u pve-config-backup.service -n 200 --no-pager
systemctl status pve-config-backup.timer --no-pager
```


## Backup-Intervall

Das PVE-Konfigurationsbackup läuft automatisch **stündlich**.

Der systemd-Timer verwendet:

```ini
OnCalendar=hourly
Persistent=true
RandomizedDelaySec=300
```

Dadurch läuft pro Stunde ein Backup. Die zufällige Verzögerung von bis zu fünf Minuten verteilt die Last, wenn viele Kunden-PVE denselben PBS verwenden.

Beim Installer wird zusätzlich sofort ein erstes Backup erstellt.


## Update-Hinweis

Den Installer künftig mit

```bash
bash install.sh
```

starten. Dadurch wird das Git-Dateirecht von `install.sh` nicht lokal geändert.

Falls ein älterer Stand beim `git pull` meldet, dass lokale Änderungen an `pve-config-backup/install.sh` überschrieben würden:

```bash
cd /opt/openmain-it-installer
git restore pve-config-backup/install.sh
git pull --ff-only
cd pve-config-backup
bash install.sh
```

Die lokale Datei `/etc/pve-config-backup.conf` liegt außerhalb des Git-Repositories und bleibt dabei erhalten.
