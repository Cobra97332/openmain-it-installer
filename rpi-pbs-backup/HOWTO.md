# HOWTO – Raspberry Pi direkt über einen PVE-Host auf PBS sichern

## 1. Voraussetzungen

### Raspberry Pi

- Raspberry Pi OS oder Debian mit systemd
- ARMv7 oder ARM64
- root-Zugriff
- SSH-Verbindung zum PVE-Host
- `rsync` und OpenSSH-Client werden vom Installer installiert

### Proxmox VE

- x86-64 Proxmox VE
- der gewünschte Proxmox Backup Server ist bereits unter **Datacenter → Storage** als Typ `pbs` eingetragen
- das PBS-Storage funktioniert auf dem Node
- ausreichend lokaler Speicher für das Staging
- SSH vom Raspberry Pi zum PVE-Host bzw. dessen NetBird-/Management-IP

### PBS

Das Script kann die bereits in PVE hinterlegten PBS-Zugangsdaten verwenden. Für produktive Installationen ist ein eigener PBS-Benutzer/API-Token mit minimal benötigten Rechten dennoch die bevorzugte Variante.

Keine PBS-Tokens oder Secrets in GitHub ablegen.

## 2. Gateway direkt auf PVE installieren

Auf dem PVE-Host als `root`:

```bash
curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/install-gateway-from-github.sh | bash
```

Der Installer:

1. prüft, ob er auf Proxmox VE läuft,
2. installiert/prüft `proxmox-backup-client`, `rsync`, `openssh-server` und `sudo`,
3. liest alle `pbs:`-Storages aus `/etc/pve/storage.cfg`,
4. lässt bei mehreren Storages eines auswählen,
5. übernimmt Server, Datastore, Benutzer, Fingerprint und Namespace,
6. verwendet `/etc/pve/priv/storage/<STORAGE-ID>.pw` als vorhandene Credential-Datei,
7. erstellt den Benutzer `rpi-backup`,
8. richtet Staging und Restore-Verzeichnisse ein,
9. installiert `rpi-pbs-ingest` und `rpi-pbs-restore`,
10. testet den PBS-Zugriff.

Beispielauswahl ohne Rückfrage:

```bash
PVE_PBS_STORAGE="PBS_Terramaster" \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/install-gateway-from-github.sh)"
```

Neu konfigurieren:

```bash
curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/install-gateway-from-github.sh | bash -s -- --reconfigure
```

Konfiguration prüfen:

```bash
cat /etc/openmain/rpi-pbs-gateway.conf
```

Beispiel:

```bash
PVE_STORAGE_ID="PBS_Terramaster"
STAGING_BASE="/var/lib/openmain-rpi-pbs/staging"
PBS_REPOSITORY="root@pam@192.168.0.221:Backup"
PBS_PASSWORD_FILE="/etc/pve/priv/storage/PBS_Terramaster.pw"
PBS_FINGERPRINT="..."
PBS_NAMESPACE=""
PBS_KEYFILE=""
PBS_CHANGE_DETECTION="data"
RESTORE_BASE="/var/lib/openmain-rpi-pbs/restore"
```

### Staging auf anderes Dateisystem legen

Vor der Installation:

```bash
RPI_PBS_STAGING_BASE="/mnt/local-backup/rpi-staging" \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/install-gateway-from-github.sh)"
```

Das Staging enthält den jeweils aktuellen Datenstand der Raspberry Pis und bleibt für inkrementelle `rsync`-Läufe erhalten. Deshalb muss auf dem PVE-Host ausreichend Platz vorhanden sein.

## 3. Raspberry Pi installieren

```bash
curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/install-client-from-github.sh | bash
```

Konfiguration:

```bash
nano /etc/openmain/rpi-pbs-backup.conf
```

Minimal:

```bash
GATEWAY_HOST="100.64.0.10"
GATEWAY_PORT="22"
CUSTOMER="kunde1"
```

`GATEWAY_HOST` sollte bevorzugt die NetBird-IP oder eine dedizierte Management-IP des PVE-Nodes sein.

`BACKUP_ID=""` sollte normalerweise leer bleiben. Dann wird aus `CUSTOMER="kunde1"` und Hostname `router` automatisch `kunde1-router`.

## 4. SSH-Key auf dem PVE-Host freischalten

Auf dem Pi:

```bash
cat /root/.ssh/openmain-rpi-pbs.pub
```

Auf dem PVE-Host:

```bash
nano /home/rpi-backup/.ssh/authorized_keys
chown rpi-backup:rpi-backup /home/rpi-backup/.ssh/authorized_keys
chmod 600 /home/rpi-backup/.ssh/authorized_keys
```

Verbindung vom Pi testen:

```bash
ssh -i /root/.ssh/openmain-rpi-pbs rpi-backup@100.64.0.10 true
```

PVE-SSH sollte nur aus Management-Netzen bzw. über NetBird erreichbar sein. Kein unnötiges SSH-Inbound aus dem Internet freigeben.

## 5. Backup-Pfade

Standard:

```bash
BASE_PATHS=(/etc /root /home /opt /usr/local /var/www /var/lib)
```

Zusätzliche Pfade:

```bash
EXTRA_PATHS=(/srv /data)
```

Nicht vorhandene Pfade werden automatisch übersprungen.

### Docker

`/var/lib/docker` wird nicht vollständig kopiert. Persistente Named Volumes und Bind-Mounts werden per `docker inspect` erkannt und separat gesichert. Regenerierbare `overlay2`-Layer und Images werden dadurch nicht unnötig auf PBS gespeichert.

## 6. Docker und Datenbanken

Standardmäßig werden laufende Docker-Container für den zweiten Delta-Lauf kurz gestoppt:

```bash
QUIESCE_DOCKER="yes"
```

Host-Dienste aus dieser Liste werden ebenfalls kurz gestoppt, sofern sie aktiv sind:

```bash
QUIESCE_SERVICE_NAMES=(influxdb influxdb2 mariadb mysql postgresql grafana-server mosquitto)
```

Wenn ein System keinen Stopp verträgt:

```bash
QUIESCE_DOCKER="no"
QUIESCE_SERVICES="no"
```

Dann ist bei laufenden Datenbanken keine vollständige Applikationskonsistenz garantiert.

## 7. Ersten Test durchführen

Auf dem Raspberry Pi:

```bash
systemctl start rpi-pbs-backup.service
systemctl status rpi-pbs-backup.service --no-pager -l
journalctl -u rpi-pbs-backup.service -n 300 --no-pager
```

Lokaler Status:

```bash
cat /var/lib/openmain-rpi-backup/last-status
cat /var/lib/openmain-rpi-backup/last-success
```

`last-status` muss `0` enthalten.

Auf dem PVE-Host kann das Staging geprüft werden:

```bash
find /var/lib/openmain-rpi-pbs/staging -maxdepth 2 -type d -print
```

## 8. Timer

```bash
systemctl enable --now rpi-pbs-backup.timer
systemctl list-timers rpi-pbs-backup.timer
```

Standard: Samstag 03:00 Uhr plus 0–30 Minuten Zufallsversatz.

## 9. PBS-Retention

Retention zentral auf PBS konfigurieren, z. B.:

```text
keep-daily:   7
keep-weekly:  4
keep-monthly: 12
```

Zusätzlich regelmäßige Verify-Jobs konfigurieren.

## 10. Restore

Snapshots auf dem PVE-Gateway anzeigen:

```bash
rpi-pbs-restore list kunde1-router
```

Backup in ein separates Restore-Verzeichnis extrahieren:

```bash
rpi-pbs-restore extract kunde1-router 'host/kunde1-router/2026-10-06T01:15:00Z'
```

Weitere Hinweise: [RESTORE.md](RESTORE.md).
