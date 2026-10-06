# Mailcow Backup

Automatisiertes Applikationsbackup für **mailcow: dockerized**.

Das Projekt verwendet absichtlich das offizielle Mailcow-Skript
`helper-scripts/backup_and_restore.sh` und kopiert dieses nicht an einen anderen Ort.

## Backup-Strategie

Es werden zwei Ebenen eingerichtet:

### Stündlich

```text
mysql
crypt
redis
```

Standard-Aufbewahrung: **3 Tage**.

### Täglich

```text
all
```

Damit werden alle vom offiziellen Mailcow-Helper unterstützten Komponenten gesichert.

Standard-Aufbewahrung: **14 Tage**.

Beim täglichen Vollbackup wird zusätzlich die Mailcow-Installationskonfiguration als

```text
mailcow-install-config.tar.gz
```

in den erzeugten `mailcow-*` Backup-Ordner geschrieben.

## Zielstruktur

Beispiel:

```text
/var/backups/mailcow/
├── hourly/
│   ├── mailcow-2026-10-06-09-00-00/
│   └── ...
└── daily/
    ├── mailcow-2026-10-06-03-30-00/
    │   ├── mailcow.conf
    │   ├── backup_*.tar.zst
    │   ├── mailcow-install-config.tar.gz
    │   └── openmain-backup-metadata.txt
    └── ...
```

Das Backup-Ziel sollte möglichst auf einem externen oder separat gemounteten Speicher liegen.

## Installation

```bash
apt update
apt install -y git

cd /opt
git clone --depth 1 https://github.com/Cobra97332/openmain-it-installer.git
cd /opt/openmain-it-installer/mailcow-backup

bash install.sh
```

Der Installer benötigt keine Argumente und fragt interaktiv nach:

- Kundenname/ID
- Mailcow-Installation
- Backup-Ziel
- Aufbewahrungszeiten
- Thread-Anzahl

Danach werden Syntax und Mailcow geprüft, sofort ein vollständiges Erstbackup erstellt und anschließend beide Timer aktiviert.

## Timer

Stündlich:

```text
mailcow-backup-hourly.timer
```

Täglich:

```text
mailcow-backup-daily.timer
```

Prüfen:

```bash
systemctl list-timers 'mailcow-backup-*'
```

## Manuelle Tests

Preflight:

```bash
/usr/local/sbin/mailcow-backup.sh --check
```

Critical-Backup:

```bash
/usr/local/sbin/mailcow-backup.sh critical
```

Vollbackup:

```bash
/usr/local/sbin/mailcow-backup.sh full
```

## Restore

Siehe [HOWTO.md](HOWTO.md).

Es ist zusätzlich ein interaktiver Wrapper vorhanden:

```bash
bash restore.sh
```

Der eigentliche Restore wird weiterhin vom offiziellen Mailcow Backup/Restore-Helper ausgeführt.

## Sicherheit

Mailcow-Backups enthalten vertrauliche Daten, unter anderem E-Mails und Konfigurationen mit Zugangsdaten. Das Backup-Ziel muss entsprechend geschützt werden.

Keine Passwörter, Tokens oder produktiven Konfigurationen in dieses öffentliche Repository eintragen.
