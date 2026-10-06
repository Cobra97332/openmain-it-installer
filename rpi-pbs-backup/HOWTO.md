# HOWTO – Raspberry Pi auf Proxmox Backup Server sichern

## 1. Voraussetzungen

### Raspberry Pi

- Raspberry Pi OS oder Debian mit systemd
- ARMv7 oder ARM64
- root-Zugriff
- SSH-Verbindung zum Backup-Gateway
- `rsync` und OpenSSH-Client werden vom Installer installiert

### Backup-Gateway

- x86-64 Debian oder Proxmox VE
- offizieller `proxmox-backup-client`
- Netzwerkzugriff auf PBS TCP/8007
- Netzwerkzugriff vom Raspberry Pi auf SSH TCP/22 oder einen abweichenden SSH-Port

### PBS

Empfohlen ist ein eigener API-Token nur für Raspberry-Pi-Backups. Rechte nur auf den benötigten Datastore/Namespace vergeben.

Beispielstruktur:

```text
Datastore: Backup
Namespace: rpi

host/kunde1-router
host/kunde1-iobroker
host/intern-monitoring
```

Keine PBS-Tokens oder Secrets in GitHub ablegen.

## 2. Gateway installieren

Einfachinstallation aus dem öffentlichen Repository:

```bash
curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/install-gateway-from-github.sh | bash
```

Konfiguration öffnen:

```bash
nano /etc/openmain/rpi-pbs-gateway.conf
```

Beispiel:

```bash
STAGING_BASE="/srv/rpi-pbs-staging"
PBS_REPOSITORY="backup@pbs!rpi@pbs.example.invalid:Backup"
PBS_PASSWORD_FILE="/etc/openmain/pbs-token.secret"
PBS_FINGERPRINT=""
PBS_NAMESPACE="rpi"
PBS_KEYFILE=""
PBS_CHANGE_DETECTION="data"
```

Token-Secret anlegen:

```bash
install -m 0600 /dev/null /etc/openmain/pbs-token.secret
nano /etc/openmain/pbs-token.secret
```

Dateirechte prüfen:

```bash
stat -c '%a %U:%G %n' /etc/openmain/pbs-token.secret
```

Erwartet:

```text
600 root:root /etc/openmain/pbs-token.secret
```

PBS-Verbindung testen:

```bash
source /etc/openmain/rpi-pbs-gateway.conf
export PBS_REPOSITORY PBS_PASSWORD_FILE
[ -n "$PBS_FINGERPRINT" ] && export PBS_FINGERPRINT
proxmox-backup-client status --repository "$PBS_REPOSITORY"
```

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

`BACKUP_ID=""` sollte normalerweise leer bleiben. Dann wird automatisch z. B. aus `CUSTOMER="kunde1"` und Hostname `router` die PBS-ID `kunde1-router`.

## 4. SSH-Key am Gateway freischalten

Auf dem Pi:

```bash
cat /root/.ssh/openmain-rpi-pbs.pub
```

Auf dem Gateway:

```bash
nano /home/rpi-backup/.ssh/authorized_keys
chown rpi-backup:rpi-backup /home/rpi-backup/.ssh/authorized_keys
chmod 600 /home/rpi-backup/.ssh/authorized_keys
```

Verbindung vom Pi testen:

```bash
ssh -i /root/.ssh/openmain-rpi-pbs rpi-backup@100.64.0.10 true
```

Für produktive Umgebungen sollte das Gateway nur über internes Management-Netz oder NetBird erreichbar sein.

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

### `/var/lib/docker`

Das komplette Docker-Datenverzeichnis wird bewusst nicht über den normalen Systemlauf gesichert. Persistente Named Volumes und Bind-Mounts werden per `docker inspect` erkannt und separat gesichert.

Regenerierbare Container-Layer unter `overlay2` müssen dadurch nicht auf PBS gespeichert werden.

## 6. Docker und Datenbanken

Standardmäßig werden alle aktuell laufenden Docker-Container beim zweiten Delta-Lauf kurz gestoppt:

```bash
QUIESCE_DOCKER="yes"
```

Zusätzlich werden laufende Host-Dienste aus dieser Liste kurz gestoppt:

```bash
QUIESCE_SERVICE_NAMES=(influxdb influxdb2 mariadb mysql postgresql grafana-server mosquitto)
```

Weitere Dienste können ergänzt werden.

Wenn ein bestimmtes System keinen Stopp verträgt:

```bash
QUIESCE_DOCKER="no"
QUIESCE_SERVICES="no"
```

Dann ist bei Datenbanken jedoch keine applikationskonsistente Sicherung garantiert.

## 7. Ersten Test durchführen

```bash
systemctl start rpi-pbs-backup.service
```

Status:

```bash
systemctl status rpi-pbs-backup.service --no-pager -l
```

Log:

```bash
journalctl -u rpi-pbs-backup.service -n 300 --no-pager
```

Lokaler Erfolgsstatus:

```bash
cat /var/lib/openmain-rpi-backup/last-status
cat /var/lib/openmain-rpi-backup/last-success
```

`last-status` muss `0` enthalten.

## 8. Timer

```bash
systemctl enable --now rpi-pbs-backup.timer
systemctl list-timers rpi-pbs-backup.timer
```

Standard:

```text
Samstag 03:00 Uhr
+ 0–30 Minuten RandomizedDelaySec
```

Zeitplan ändern:

```bash
systemctl edit rpi-pbs-backup.timer
```

Beispiel täglich 02:30 Uhr:

```ini
[Timer]
OnCalendar=
OnCalendar=*-*-* 02:30:00
RandomizedDelaySec=900
```

Danach:

```bash
systemctl daemon-reload
systemctl restart rpi-pbs-backup.timer
```

## 9. PBS-Retention

Retention zentral über PBS konfigurieren. Beispiel:

```text
keep-daily:   7
keep-weekly:  4
keep-monthly: 12
```

Zusätzlich regelmäßige PBS-Verify-Jobs konfigurieren.

## 10. Kontrolle auf dem Gateway

Staging:

```bash
find /srv/rpi-pbs-staging -maxdepth 2 -type d -print
```

Der aktuelle Staging-Stand bleibt erhalten, damit spätere rsync-Läufe nur Änderungen übertragen müssen.
