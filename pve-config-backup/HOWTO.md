# HOWTO – PVE Config Backup

## Installation

Auf dem PVE:

```bash
apt update
apt install -y git

cd /opt
git clone --depth 1 https://github.com/Cobra97332/openmain-it-installer.git
cd /opt/openmain-it-installer/pve-config-backup

chmod 700 install.sh
./install.sh
```

Für einen Kunden:

```bash
./install.sh --customer-id kunde-muster
```

Bei mehreren PBS-Storages:

```bash
./install.sh --customer-id kunde-muster --storage-id PBS-Backup
```

## Was der Installer automatisch macht

Nach dem Start werden automatisch ausgeführt:

1. Installation von Backup-Skript, Service, Timer und Config
2. Bash-Syntaxprüfung
3. Prüfung der systemd-Units
4. Prüfung der PVE-/PBS-Konfiguration
5. sofortiges echtes Host-Konfigurationsbackup auf den PBS
6. Prüfung des Service-Ergebnisses und Exitcodes
7. Aktivierung des täglichen Timers
8. Prüfung, ob der Timer aktiv und enabled ist

Wenn einer dieser Schritte fehlschlägt, beendet sich der Installer mit Fehler und deaktiviert den Timer.

Wenn am Ende erscheint:

```text
Installation vollständig erfolgreich.
Sofort-Backup: OK
Backup-Service: OK
Timer: aktiviert und aktiv
```

ist keine weitere Einrichtung nötig.

## Kontrolle

Backup-Log:

```bash
journalctl -u pve-config-backup.service -n 200 --no-pager
```

Timer:

```bash
systemctl status pve-config-backup.timer --no-pager
systemctl list-timers pve-config-backup.timer
```

PVE/PBS-Erkennung:

```bash
/usr/local/sbin/pve-config-backup.sh --check
```

## Update

```bash
cd /opt/openmain-it-installer
git pull --ff-only

cd pve-config-backup
./install.sh
```

Beim Update wird eine vorhandene `/etc/pve-config-backup.conf` nicht überschrieben. Der Installer führt anschließend erneut ein sofortiges Testbackup aus.
