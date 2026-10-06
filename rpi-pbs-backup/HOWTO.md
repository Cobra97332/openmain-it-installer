# HOWTO – Raspberry Pi über eigenen Gateway-CT auf PBS sichern

## 1. Zielaufbau

```text
Raspberry Pi
    │
    │ SSH / rsync
    ▼
rpi-pbs-gateway (Debian 13 LXC)
    │
    │ proxmox-backup-client
    ▼
PBS
```

Der Gateway-CT ist unprivilegiert. Die Raspberry Pis benötigen keinen PBS-Client und keine PBS-Zugangsdaten.

## 2. Voraussetzungen auf PVE

- Proxmox VE
- mindestens ein aktiver Storage mit `rootdir`
- ein Storage mit `vztmpl`
- funktionierende Bridge, z. B. `vmbr0`
- PBS bereits unter **Datacenter → Storage** eingebunden
- ausreichend Speicher für das Staging

Empfehlung für das Staging: mindestens so groß wie die Summe der relevanten Nutzdaten aller angebundenen Pis plus Reserve.

## 3. Gateway-CT automatisch erstellen

Auf dem PVE-Host:

```bash
curl -fsSL \
https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/install-gateway-from-github.sh \
| bash
```

Standardwerte:

```text
1 vCPU
1024 MB RAM
512 MB Swap
8 GB Root-Disk
64 GB separate Staging-Disk
DHCP
unprivilegierter LXC
Autostart aktiviert
```

Der Installer lädt automatisch das aktuelle Debian-13-Standard-LXC-Template, erstellt den CT und installiert darin den offiziellen `proxmox-backup-client` aus dem Proxmox Backup Client-only-Repository.

## 4. Eigene Werte verwenden

Beispiel mit 100-GB-Staging:

```bash
CT_DATA_SIZE=100 \
PVE_PBS_STORAGE="PBS_Terramaster" \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/install-gateway-from-github.sh)"
```

Beispiel mit fester IP:

```bash
CT_IP_CIDR="192.168.178.30/24" \
CT_GATEWAY="192.168.178.1" \
CT_DNS_SERVER="192.168.178.1" \
PVE_PBS_STORAGE="PBS_Terramaster" \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/install-gateway-from-github.sh)"
```

Alle relevanten Variablen:

```text
CTID
CT_HOSTNAME
CT_CORES
CT_MEMORY
CT_SWAP
CT_ROOTFS_SIZE
CT_DATA_SIZE
CT_STORAGE
CT_DATA_STORAGE
TEMPLATE_STORAGE
CT_BRIDGE
CT_IP_CIDR
CT_GATEWAY
CT_DNS_SERVER
PVE_PBS_STORAGE
```

## 5. Was der PVE-Installer automatisch übernimmt

Aus `/etc/pve/storage.cfg` werden übernommen:

- PBS-Server
- Datastore
- Benutzer/Auth-ID
- Fingerprint
- Namespace
- optionaler Port

Für den ersten Start wird die vorhandene PVE-Credential-Datei aus

```text
/etc/pve/priv/storage/<PBS-STORAGE-ID>.pw
```

als `/etc/openmain/pbs-secret` in den Gateway-CT kopiert.

Für maximale Rechte-Trennung sollte später ein eigener PBS-API-Token nur für die Raspberry-Pi-Backups eingerichtet und in `/etc/openmain/rpi-pbs-gateway.conf` hinterlegt werden.

## 6. Gateway prüfen

Vom PVE-Host:

```bash
pct list
pct exec <CTID> -- systemctl status ssh --no-pager
pct exec <CTID> -- cat /etc/openmain/rpi-pbs-gateway.conf
```

PBS-Test im CT:

```bash
pct exec <CTID> -- bash -lc '
source /etc/openmain/rpi-pbs-gateway.conf
export PBS_REPOSITORY PBS_PASSWORD_FILE
[ -n "$PBS_FINGERPRINT" ] && export PBS_FINGERPRINT
proxmox-backup-client status --repository "$PBS_REPOSITORY"
'
```

## 7. Raspberry Pi installieren

```bash
curl -fsSL \
https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/install-client-from-github.sh \
| bash
```

Konfiguration:

```bash
nano /etc/openmain/rpi-pbs-backup.conf
```

Beispiel:

```bash
GATEWAY_HOST="192.168.178.30"
GATEWAY_PORT="22"
CUSTOMER="kunde1"
BACKUP_ID=""
```

Bei `BACKUP_ID=""` wird aus Kunde + Hostname automatisch z. B. `kunde1-router`.

## 8. SSH-Key freischalten

Auf dem Pi:

```bash
cat /root/.ssh/openmain-rpi-pbs.pub
```

Auf dem PVE-Host:

```bash
pct exec <CTID> -- nano /home/rpi-backup/.ssh/authorized_keys
pct exec <CTID> -- chown rpi-backup:rpi-backup /home/rpi-backup/.ssh/authorized_keys
pct exec <CTID> -- chmod 600 /home/rpi-backup/.ssh/authorized_keys
```

Hinweis: Der Installer hinterlegt zusätzlich den OpenMain-Admin-Key für Root unter `/root/.ssh/authorized_keys`. Root-SSH ist nur per Public Key erlaubt.

Vom Pi testen:

```bash
ssh -i /root/.ssh/openmain-rpi-pbs rpi-backup@<GATEWAY-IP> true
```

## 9. Ersten Backup-Lauf testen

Auf dem Pi:

```bash
systemctl start rpi-pbs-backup.service
systemctl status rpi-pbs-backup.service --no-pager -l
journalctl -u rpi-pbs-backup.service -n 300 --no-pager
```

Auf dem Gateway-CT:

```bash
find /var/lib/openmain-rpi-pbs/staging -maxdepth 2 -type d -print
```

## 10. Timer

```bash
systemctl enable --now rpi-pbs-backup.timer
systemctl list-timers rpi-pbs-backup.timer
```

Standard: Samstag 03:00 Uhr plus bis zu 30 Minuten Zufallsversatz.

## 11. Restore

Im Gateway-CT:

```bash
rpi-pbs-restore list kunde1-router
```

Snapshot extrahieren:

```bash
rpi-pbs-restore extract \
  kunde1-router \
  'host/kunde1-router/2026-10-06T01:15:00Z'
```

Siehe auch [RESTORE.md](RESTORE.md).
