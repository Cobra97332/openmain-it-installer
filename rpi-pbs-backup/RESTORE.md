# Restore – Raspberry Pi aus PBS wiederherstellen

Die Wiederherstellung erfolgt bewusst zweistufig. PBS wird zunächst auf dem x86-Gateway in ein separates Restore-Verzeichnis extrahiert. Erst danach werden ausgewählte Daten auf den Raspberry Pi übertragen.

Dadurch überschreibt ein falscher Restore-Befehl nicht direkt ein laufendes System.

## 1. Snapshots anzeigen

Auf dem Gateway:

```bash
rpi-pbs-restore list kunde1-router
```

Alternativ direkt:

```bash
source /etc/openmain/rpi-pbs-gateway.conf
export PBS_REPOSITORY PBS_PASSWORD_FILE
[ -n "$PBS_FINGERPRINT" ] && export PBS_FINGERPRINT
proxmox-backup-client snapshot list host/kunde1-router \
  --repository "$PBS_REPOSITORY" \
  --ns "$PBS_NAMESPACE"
```

## 2. Snapshot extrahieren

Beispiel:

```bash
rpi-pbs-restore extract \
  kunde1-router \
  'host/kunde1-router/2026-10-06T01:15:00Z'
```

Standardziel:

```text
/srv/rpi-pbs-restore/kunde1-router/
├── system/
├── docker/
└── metadata/
```

Eigenes Ziel:

```bash
rpi-pbs-restore extract \
  kunde1-router \
  'host/kunde1-router/2026-10-06T01:15:00Z' \
  /srv/restore-test/kunde1-router
```

## 3. Metadaten prüfen

Vor einem Restore:

```bash
cat /srv/rpi-pbs-restore/kunde1-router/metadata/os-release
cat /srv/rpi-pbs-restore/kunde1-router/metadata/uname.txt
cat /srv/rpi-pbs-restore/kunde1-router/metadata/lsblk.txt
cat /srv/rpi-pbs-restore/kunde1-router/metadata/docker/mounts.tsv
```

Damit lassen sich ursprüngliche OS-Version, Architektur, Partitionen und Docker-Mounts nachvollziehen.

## 4. Neuinstallation

Empfohlen:

1. Raspberry Pi OS/Debian neu installieren.
2. Hostname setzen.
3. Netzwerk/NetBird herstellen.
4. benötigte Pakete installieren.
5. Daten aus dem extrahierten Backup selektiv zurückspielen.

Paketliste:

```text
metadata/packages.tsv
```

## 5. Systemdaten zurückspielen

Beispiel für `/etc` auf einen Testpfad:

```bash
rsync -aHAXn --fake-super --numeric-ids /srv/rpi-pbs-restore/kunde1-router/system/etc/ root@NEUER-PI:/etc/
```

Das `-n` ist ein Dry Run. Erst nach Prüfung ohne `-n` ausführen. `--fake-super` ist hier erforderlich, weil das Gateway die ursprünglichen Eigentümer, Modi und privilegierten Metadaten im Backup als erweiterte Attribute gespeichert hat. `--numeric-ids` verhindert eine unerwünschte Namensauflösung von UID/GID.

Weitere typische Bereiche:

```text
system/root/
system/home/
system/opt/
system/usr/local/
system/var/www/
system/var/lib/
system/boot/
```

`/etc` und `/boot` sollten nicht blind zwischen unterschiedlichen Debian-/Raspberry-Pi-OS-Versionen überschrieben werden.

## 6. Docker wiederherstellen

Die ursprünglichen Mounts stehen in:

```text
metadata/docker/mounts.tsv
```

Die gesicherten Daten liegen unter:

```text
docker/mounts/<hash>/
```

Zuerst Docker und die Containerdefinitionen/Compose-Dateien wiederherstellen. Danach die persistenten Volumes bzw. Bind-Mounts an ihre ursprünglichen Pfade kopieren.

Vor dem Kopieren Container stoppen.

## 7. Datenbankdienste

Host-basierte Daten unter z. B. `/var/lib/influxdb`, `/var/lib/grafana`, `/var/lib/mysql` oder `/var/lib/postgresql` liegen im `system.pxar`, sofern sie nicht explizit ausgeschlossen wurden.

Für einen produktiven Restore sollten Dienstversion und Datenformat kompatibel sein. Bei größeren Versionssprüngen ist ein nativer Datenbank-Dump/Restore vorzuziehen.

## 8. Abschlussprüfung

Nach Restore mindestens prüfen:

```bash
systemctl --failed
ip addr
ip route
docker ps -a
journalctl -p err -b --no-pager
```

Anschließend einen neuen PBS-Backup-Lauf starten und den Restore dokumentieren.
