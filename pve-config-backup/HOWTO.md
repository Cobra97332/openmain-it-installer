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

Es werden keine Argumente benötigt.

Der Installer fragt interaktiv:

1. Kundenname/ID (leer = intern/kein Kunde)
2. verfügbaren PBS-Storage
3. Bestätigung der Auswahl

Beispiel:

```text
Kundenname/ID eingeben (leer = intern/kein Kunde): kunde-muster

Verfügbare PBS-Storages:
  1) PBS-Kunde
  2) PBS-Archiv

Nummer des PBS-Storage auswählen: 1

Ausgewählte Konfiguration:
  Kunde:       kunde-muster
  PBS-Storage: PBS-Kunde

Installation mit diesen Einstellungen starten? [J/n]:
```

Danach erledigt der Installer automatisch:

1. Backup-Skript, Service, Timer und Config installieren
2. Bash-Syntax prüfen
3. systemd-Units prüfen
4. PVE/PBS-Konfiguration prüfen
5. sofort ein echtes Backup auf den PBS starten
6. Backup-Service und Exitcode prüfen
7. Timer aktivieren
8. prüfen, ob der Timer aktiv ist

Wenn am Ende erscheint:

```text
Installation vollständig erfolgreich.
Sofort-Backup: OK
Backup-Service: OK
Timer: aktiviert und aktiv
```

ist keine weitere Einrichtung nötig.

## Kontrolle

```bash
/usr/local/sbin/pve-config-backup.sh --check
journalctl -u pve-config-backup.service -n 200 --no-pager
systemctl status pve-config-backup.timer --no-pager
```

## Update

```bash
cd /opt/openmain-it-installer
git pull --ff-only
cd pve-config-backup
./install.sh
```

Beim Update wird die vorhandene Konfiguration übernommen; Kunde und PBS-Storage können bei der interaktiven Abfrage neu gewählt werden.
