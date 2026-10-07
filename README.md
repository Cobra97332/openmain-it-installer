# OpenMain-IT Installer

Öffentliche Installer ohne Login.

Das Hauptrepository bleibt privat.

## Raspberry Pi → Proxmox Backup Server

Universelles Backup für Raspberry Pi 3/4/5 sowie Debian/Raspberry Pi OS auf ARMv7/ARM64. Der Pi überträgt per rsync/SSH an ein x86-64-Gateway; dort schreibt der offizielle `proxmox-backup-client` auf PBS.

- [rpi-pbs-backup/README.md](rpi-pbs-backup/README.md)
- [rpi-pbs-backup/HOWTO.md](rpi-pbs-backup/HOWTO.md)
- [rpi-pbs-backup/RESTORE.md](rpi-pbs-backup/RESTORE.md)

Client installieren:

```bash
curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/install-client-from-github.sh | bash
```

Gateway installieren:

```bash
curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/install-gateway-from-github.sh | bash
```

## ISO-Build: bewährtes V8-Verfahren

Der ISO-Builder verwendet bewusst den bereits getesteten Debian-V8-Ablauf:

1. offizielles Debian-13-Netinst-ISO herunterladen
2. ISO per Loop-Mount einbinden
3. vollständigen ISO-Baum mit `rsync` kopieren
4. Preseed und OpenMain-IT First-Boot-Dateien ergänzen
5. BIOS- und UEFI/GRUB-Bootparameter patchen
6. vorhandene Debian-ISO-Bootstruktur mit `xorriso -as mkisofs` wieder aufbauen

Damit wird die Debian-Netinst nicht durch ein eigenes Live-System ersetzt.

### Build

```bash
apt update
apt install -y curl

curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/build-iso.sh -o build-iso.sh
chmod +x build-iso.sh
sudo ./build-iso.sh
```

Ergebnis:

```text
netbird-router-debian13-amd64.iso
netbird-router-debian13-amd64.iso.sha256
```

Die Buildmaschine benötigt keine Anmeldung bei GitHub und kein SCP.

## Zabbix aktualisieren

Die vollständige Anleitung für Zabbix Server, Proxy und Agent auf Debian 13:

- [docs/ZABBIX-UPGRADE.md](docs/ZABBIX-UPGRADE.md)

## PatchMon Proxmox Auto-Deployment

Öffentliche Dateien für die automatische PatchMon-Installation auf Proxmox-LXC und VMs:

- [patchmon-proxmox/README.md](patchmon-proxmox/README.md)

Unterstützt Linux-LXC, Linux-VMs, Windows-VMs und FreeBSD/OPNsense-VMs.


## Raspberry Pi NetBird Kundenrouter

- netbird-router/raspberry-pi/README.md
- netbird-router/raspberry-pi/HOWTO.md
- netbird-router/raspberry-pi/install-router.sh


## Grafana + Zabbix Dashboards

Automatisch provisionierte, dynamische Dashboards für OPNsense, Proxmox VE, Dell iDRAC, NAS, QNAP und Synology sowie zentrale Problem- und TV/NOC-Ansichten:

- [grafana-zabbix/README.md](grafana-zabbix/README.md)
- [grafana-zabbix/HOWTO.md](grafana-zabbix/HOWTO.md)

Dashboard-Provisioning installieren:

```bash
curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/grafana-zabbix/install.sh | bash
```

Hinweis: Vor dem Einsatz muss die UID der vorhandenen Grafana-Zabbix-Datasource geprüft werden. Standard ist `zabbix-main`; bei abweichender UID `GRAFANA_ZABBIX_UID` beim Installer setzen.
