# Installation

Die Installation erfolgt aus dem öffentlichen Repository:

```bash
apt update
apt install -y git

cd /opt
git clone --depth 1 https://github.com/Cobra97332/openmain-it-installer.git
cd /opt/openmain-it-installer/pve-config-backup

bash install.sh
```

Der Installer fragt interaktiv nach Kunden-ID und PBS-Storage, prüft die Konfiguration, erstellt sofort ein Testbackup und aktiviert anschließend den stündlichen Timer.

## Update

```bash
cd /opt/openmain-it-installer
git pull --ff-only
cd pve-config-backup
bash install.sh
```

Falls `git pull` wegen lokaler Änderungen an `pve-config-backup/install.sh` abbricht:

```bash
cd /opt/openmain-it-installer
git restore pve-config-backup/install.sh
git pull --ff-only
cd pve-config-backup
bash install.sh
```

Die produktive Konfiguration `/etc/pve-config-backup.conf` wird dadurch nicht gelöscht oder überschrieben.


## Wiederherstellung

Nach der Installation steht der Restore-Assistent bereit:

```bash
/usr/local/sbin/pve-config-restore.sh
```

Der gewählte PBS-Snapshot wird zunächst nur nach `/var/tmp/pve-config-restore/` wiederhergestellt. Die laufende PVE-Konfiguration wird dabei nicht automatisch überschrieben. Die weiteren Restore-Schritte sind in `HOWTO.md` dokumentiert.
