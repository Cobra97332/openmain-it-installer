# PatchMon Proxmox Auto-Deployment

Öffentliche Dateien für die automatische PatchMon-Installation auf Proxmox-Gästen.

Unterstützt:
- Linux-LXC
- Linux-VMs
- Windows-VMs
- FreeBSD/OPNsense-VMs

Die VM-Ausführung verwendet den QEMU Guest Agent. Zugangsdaten werden ausschließlich lokal auf dem Proxmox-Host gespeichert und **nicht** in dieses öffentliche Repository geschrieben.

## Installation auf dem PVE

Repository laden:

```bash
git clone https://github.com/Cobra97332/openmain-it-installer.git
cd openmain-it-installer/patchmon-proxmox
bash install.bash
```

Während der Installation werden jetzt direkt abgefragt:

- PatchMon-URL
- Auto-Enrollment Token Key
- Auto-Enrollment Token Secret

Das Secret wird bei der Eingabe nicht angezeigt. Die Werte werden anschließend mit Modus `0600` in `/etc/patchmon-proxmox.env` gespeichert.

Wichtig: Die gewünschte Standardgruppe wird in PatchMon **am Auto-Enrollment-Token** festgelegt. Dadurch werden neue Hosts beim ersten Enrollment automatisch dieser Gruppe zugeordnet.

## Erst testen

```bash
DRY_RUN=true /usr/local/sbin/patchmon-proxmox-deploy
```

Log:

```bash
tail -f /var/log/patchmon-proxmox.log
```

## Automatik aktivieren

```bash
systemctl enable --now patchmon-proxmox-deploy.timer
systemctl list-timers | grep patchmon
```

## Voraussetzungen für VMs

Der QEMU Guest Agent muss in der VM installiert und in Proxmox aktiviert sein.

Test:

```bash
qm agent VMID ping
```

Für OPNsense das QEMU-Guest-Agent-Plugin installieren und in den VM-Optionen den Guest Agent aktivieren.

## Dateien

- `patchmon-proxmox-deploy.bash` – Deployment für LXC und VMs
- `install.bash` – lokale Installation auf dem PVE
- `patchmon-proxmox.env.example` – Konfigurationsvorlage ohne echte Zugangsdaten
- `systemd/patchmon-proxmox-deploy.service`
- `systemd/patchmon-proxmox-deploy.timer`

## Öffentliche Erreichbarkeit

PatchMon muss für dieses Deployment **öffentlich über HTTPS erreichbar** sein:

```text
https://patchmon.openmain-it.de
```

Erforderlich:

- öffentliches DNS
- TCP/443 erreichbar
- gültiges öffentliches TLS-Zertifikat
- Reverse Proxy mit funktionierendem WebSocket/WSS-Passthrough
- keine Abhängigkeit von NetBird oder internem Split-DNS

Bei `WS offline` in PatchMon zuerst den Reverse Proxy und WebSocket-Upgrade-Header prüfen.
