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

chmod 700 install.sh
./install.sh
```

Für einen Kunden:

```bash
./install.sh --customer-id kunde-muster
```

## Sicherheit

Das Backup enthält sensible PVE-Konfigurationen und kann Secrets aus `/etc/pve/priv/` enthalten. Keine Zugangsdaten oder Verschlüsselungsschlüssel im öffentlichen GitHub-Repository ablegen.
