# HOWTO – Mailcow Backup und Restore

## 1. Zweck

Dieses Projekt ergänzt VM-/Server-Backups um ein anwendungsbezogenes Mailcow-Backup.

Es verwendet den von Mailcow mitgelieferten Helper:

```text
helper-scripts/backup_and_restore.sh
```

Der Mailcow-Helper bleibt an seinem Originalort innerhalb der Mailcow-Installation.

## 2. Installation

Auf dem Mailcow-Server:

```bash
apt update
apt install -y git

cd /opt
git clone --depth 1 https://github.com/Cobra97332/openmain-it-installer.git
cd /opt/openmain-it-installer/mailcow-backup

bash install.sh
```

Der Installer benötigt keine Argumente.

Er fragt nach:

1. Kundenname/ID
2. erkannter Mailcow-Installation
3. Backup-Ziel
4. Aufbewahrung für stündliche Backups
5. Aufbewahrung für tägliche Vollbackups
6. Anzahl der Backup-Threads
7. Bestätigung

## 3. Backup-Ziel

Empfohlen ist ein externes bzw. separat gemountetes Ziel, zum Beispiel:

```text
/mnt/backup/mailcow
/mnt/nas/mailcow
/backup/mailcow
```

Wenn das Ziel auf dem Root-Dateisystem des Mailcow-Servers liegt, zeigt der Installer eine Warnung.

Ein lokales Backup auf demselben Server ist kein vollständiger Schutz gegen den Ausfall des Servers.

## 4. Backup-Strategie

### Stündlich

Der stündliche Job sichert:

```text
mysql
crypt
redis
```

Service:

```text
mailcow-backup-hourly.service
```

Timer:

```text
mailcow-backup-hourly.timer
```

Standard-Aufbewahrung:

```text
3 Tage
```

### Täglich

Der tägliche Job verwendet:

```text
backup all
```

und erstellt damit ein vollständiges Backup aller vom Mailcow-Helper unterstützten Komponenten.

Service:

```text
mailcow-backup-daily.service
```

Timer:

```text
mailcow-backup-daily.timer
```

Standardzeit:

```text
03:30 Uhr + bis zu 30 Minuten zufällige Verzögerung
```

Standard-Aufbewahrung:

```text
14 Tage
```

Zusätzlich wird die Mailcow-Installationskonfiguration in das tägliche Backup aufgenommen:

```text
mailcow-install-config.tar.gz
```

## 5. Sofortiger Test bei der Installation

Der Installer führt automatisch aus:

1. Bash-Syntaxprüfung
2. systemd-Prüfung
3. Mailcow-/Docker-Preflight
4. sofortiges vollständiges Erstbackup
5. Prüfung des Exitcodes
6. Aktivierung beider Timer
7. Prüfung beider Timer

Wenn am Ende steht:

```text
Mailcow Backup vollständig eingerichtet.
Sofort-Vollbackup: OK
Stündliches Critical-Backup: aktiv
Tägliches Vollbackup: aktiv
```

ist die Einrichtung abgeschlossen.

## 6. Kontrolle

Preflight:

```bash
/usr/local/sbin/mailcow-backup.sh --check
```

Timer:

```bash
systemctl list-timers 'mailcow-backup-*'
```

Stündliches Log:

```bash
journalctl -u mailcow-backup-hourly.service -n 200 --no-pager
```

Tägliches Log:

```bash
journalctl -u mailcow-backup-daily.service -n 200 --no-pager
```

## 7. Manuelles Backup

Critical:

```bash
/usr/local/sbin/mailcow-backup.sh critical
```

Vollständig:

```bash
/usr/local/sbin/mailcow-backup.sh full
```

Wenn bereits ein anderer Mailcow-Backup-Lauf aktiv ist, wird ein paralleler Lauf übersprungen.

## 8. Backup-Struktur

Beispiel:

```text
/backup/mailcow/
├── hourly/
│   ├── mailcow-2026-10-06-08-02-00/
│   ├── mailcow-2026-10-06-09-01-00/
│   └── ...
└── daily/
    ├── mailcow-2026-10-05-03-42-00/
    └── mailcow-2026-10-06-03-37-00/
```

Die vom offiziellen Mailcow-Helper erzeugten `mailcow-DATUM` Verzeichnisse nicht umbenennen.

## 9. Update

```bash
cd /opt/openmain-it-installer
git pull --ff-only

cd mailcow-backup
bash install.sh
```

Die vorhandene `/etc/mailcow-backup.conf` wird als Ausgangspunkt für die interaktive Konfiguration verwendet.

## 10. Restore auf dem vorhandenen Mailcow-Server

In das Projekt wechseln:

```bash
cd /opt/openmain-it-installer/mailcow-backup
bash restore.sh
```

Das Restore-Skript fragt nach:

1. Mailcow-Installation
2. Pfad mit den `mailcow-*` Backup-Ordnern
3. Thread-Anzahl
4. Bestätigung

Danach startet es den offiziellen Mailcow-Restore. Dort wird der gewünschte Restore-Punkt und anschließend die Komponente bzw. `all` ausgewählt.

Für tägliche Vollbackups ist der typische Backup-Pfad:

```text
/DEIN/BACKUP-ZIEL/daily
```

Für die stündlichen Critical-Backups:

```text
/DEIN/BACKUP-ZIEL/hourly
```

## 11. Restore nach kompletter Neuinstallation

Bei einem komplett neuen Mailcow-Server:

1. Betriebssystem und Docker vorbereiten.
2. Mailcow neu installieren.
3. Mailcow initialisieren und starten.
4. Prüfen, dass die leere Mailcow grundsätzlich läuft.
5. Backup-Speicher einbinden.
6. Dieses GitHub-Projekt klonen.
7. `restore.sh` ausführen.
8. Gewünschten Restore-Punkt auswählen.
9. `all` auswählen, wenn das komplette Mailcow-Backup wiederhergestellt werden soll.
10. Nach dem Restore alle Container, Mailfluss, Weboberfläche und Postfächer prüfen.

Wichtig: Mailcow muss auf dem neuen System initialisiert und gestartet sein, bevor der offizielle Restore ausgeführt wird.

Installation des Restore-Projekts:

```bash
apt update
apt install -y git

cd /opt
git clone --depth 1 https://github.com/Cobra97332/openmain-it-installer.git
cd /opt/openmain-it-installer/mailcow-backup

bash restore.sh
```

## 12. MAILDIR_SUB bei älteren Installationen

Bei einem Restore auf einen neuen Server muss der Wert `MAILDIR_SUB` aus der ursprünglichen `mailcow.conf` beachtet werden.

War `MAILDIR_SUB` auf dem alten Server nicht gesetzt, sollte dieser Wert auf dem neuen System vor dem Restore nicht abweichend gesetzt werden.

Die tägliche Sicherung enthält sowohl die vom Mailcow-Helper gesicherte `mailcow.conf` als auch das zusätzliche Archiv:

```text
mailcow-install-config.tar.gz
```

Damit kann die ursprüngliche Konfiguration vor dem Restore verglichen werden.

## 13. Zusätzliche Installationskonfiguration wiederherstellen

Das tägliche Backup enthält:

```text
mailcow-install-config.tar.gz
```

Dieses Archiv enthält den Mailcow-Installationsordner ohne das Git-Verzeichnis.

Nicht blind über eine neue Mailcow-Installation entpacken.

Zuerst in ein temporäres Verzeichnis:

```bash
mkdir -p /root/mailcow-config-restore
tar -xzf /PFAD/ZUM/BACKUP/mailcow-install-config.tar.gz \
  -C /root/mailcow-config-restore
```

Danach gezielt vergleichen, zum Beispiel:

```bash
diff -u \
  /opt/mailcow-dockerized/mailcow.conf \
  /root/mailcow-config-restore/mailcow.conf || true
```

Eigene Anpassungen unter `data/conf/`, Zertifikate und weitere lokale Änderungen nur nach Prüfung übernehmen.

## 14. Abschlusskontrolle nach Restore

```bash
cd /opt/mailcow-dockerized
docker compose ps
```

Zusätzlich prüfen:

- Mailcow-Weboberfläche
- Anmeldung
- vorhandene Domains und Postfächer
- eingehende E-Mail
- ausgehende E-Mail
- IMAP
- SMTP
- SOGo
- DKIM/SPF/DMARC-Konfiguration
- Zertifikate
- Queue und Container-Logs

## 15. Sicherheit

Mailcow-Backups enthalten produktive E-Mails, Datenbanken und Zugangsdaten.

Das Backup-Ziel muss deshalb entsprechend geschützt werden.

Empfehlungen:

- externer Speicher
- eingeschränkte Zugriffsrechte
- Snapshots bzw. Versionierung auf dem Ziel
- zusätzliche Offsite-Kopie
- regelmäßiger Restore-Test
