# HOWTO – PVE Config Backup

## Installation

Auf dem PVE:

```bash
apt update
apt install -y git

cd /opt
git clone --depth 1 https://github.com/Cobra97332/openmain-it-installer.git
cd /opt/openmain-it-installer/pve-config-backup

bash install.sh
```

Es werden keine Argumente benötigt.

Der Installer fragt interaktiv:

1. Kundenname/ID (leer = intern/kein Kunde)
2. verfügbaren PBS-Storage
3. Bestätigung der Auswahl

Beispiel:

```text
Kundenname/ID eingeben (leer = intern/kein Kunde): kunde-muster

Verfügbare PBS-Storages:
  1) PBS-Kunde
  2) PBS-Archiv

Nummer des PBS-Storage auswählen: 1

Ausgewählte Konfiguration:
  Kunde:       kunde-muster
  PBS-Storage: PBS-Kunde

Installation mit diesen Einstellungen starten? [J/n]:
```

Danach erledigt der Installer automatisch:

1. Backup-Skript, Service, Timer und Config installieren
2. Bash-Syntax prüfen
3. systemd-Units prüfen
4. PVE/PBS-Konfiguration prüfen
5. sofort ein echtes Backup auf den PBS starten
6. Backup-Service und Exitcode prüfen
7. Timer aktivieren
8. prüfen, ob der Timer aktiv ist

Wenn am Ende erscheint:

```text
Installation vollständig erfolgreich.
Sofort-Backup: OK
Backup-Service: OK
Timer: aktiviert und aktiv
```

ist keine weitere Einrichtung nötig.

## Kontrolle

```bash
/usr/local/sbin/pve-config-backup.sh --check
journalctl -u pve-config-backup.service -n 200 --no-pager
systemctl status pve-config-backup.timer --no-pager
```



## Stündliches Backup

Nach erfolgreicher Installation läuft das PVE-Konfigurationsbackup automatisch **einmal pro Stunde**.

Der installierte Timer verwendet:

```ini
OnCalendar=hourly
Persistent=true
RandomizedDelaySec=300
```

Damit wird stündlich ein Host-Konfigurationsbackup erstellt. Die zufällige Verzögerung von bis zu fünf Minuten verhindert, dass viele Kunden-PVE exakt gleichzeitig auf denselben PBS schreiben.

Timer prüfen:

```bash
systemctl status pve-config-backup.timer --no-pager
systemctl list-timers pve-config-backup.timer
```

Nach einer Änderung am Repository den Timer aktualisieren:

```bash
cd /opt/openmain-it-installer
git pull --ff-only
cd pve-config-backup
bash install.sh
```

Der Installer erstellt dabei sofort ein Backup und installiert anschließend den stündlichen Timer.

### Git-Pull meldet lokale Änderungen

Bei älteren Installationen wurde `install.sh` mit `chmod 700` verändert. Git kann diese Änderung am Dateimodus als lokale Änderung erkennen und deshalb ein Update abbrechen.

In diesem Fall:

```bash
cd /opt/openmain-it-installer
git restore pve-config-backup/install.sh
git pull --ff-only
cd pve-config-backup
bash install.sh
```

Dadurch wird nur die Repository-Datei `install.sh` auf den GitHub-Stand zurückgesetzt. Die produktive Konfiguration unter `/etc/pve-config-backup.conf` wird nicht verändert.

## Update

```bash
cd /opt/openmain-it-installer
git pull --ff-only
cd pve-config-backup
bash install.sh
```

Beim Update wird die vorhandene Konfiguration übernommen; Kunde und PBS-Storage können bei der interaktiven Abfrage neu gewählt werden.

## Wiederherstellung nach Neuinstallation des PVE-Servers

Diese Anleitung ist für den Fall gedacht, dass der PVE-Host komplett neu installiert wurde und die Host-Konfiguration aus dem PBS-Backup zurückgeholt werden soll.

### 1. PVE neu installieren

Proxmox VE möglichst in derselben bzw. einer kompatiblen Version installieren.

Zunächst nur das Management-Netzwerk so konfigurieren, dass der neue PVE den PBS erreichen kann.

Wichtig bei Remote-Servern: Die endgültige Netzwerkkonfiguration nicht blind übernehmen. Falsche Interface-Namen, Gateways oder zusätzliche Provider-IP-Einstellungen können den SSH-/Webzugriff sofort unterbrechen. Netzwerkänderungen möglichst über die Serverkonsole/IPMI durchführen.

### 2. PBS wieder als Storage einrichten

Der neue PVE benötigt zunächst wieder Zugriff auf den Proxmox Backup Server.

Das kann über die PVE-Weboberfläche erfolgen:

```text
Datacenter
-> Storage
-> Add
-> Proxmox Backup Server
```

Danach prüfen:

```bash
pvesm status
grep -A12 '^pbs:' /etc/pve/storage.cfg
```

Die Storage-ID merken.

### 3. PBS-Zugang für proxmox-backup-client verwenden

Die benötigten Werte aus `/etc/pve/storage.cfg` ablesen:

```bash
cat /etc/pve/storage.cfg
```

Beispiel:

```text
pbs: PBS-Backup
        datastore Backup
        server pbs.example.de
        username backup-user@pbs
        namespace kunde-muster
        fingerprint AA:BB:CC:...
```

Das von PVE gespeicherte Secret liegt normalerweise unter:

```text
/etc/pve/priv/storage/<STORAGE-ID>.pw
```

Beispiel:

```bash
export PBS_PASSWORD_FILE="/etc/pve/priv/storage/PBS-Backup.pw"
export PBS_FINGERPRINT="AA:BB:CC:..."
export PBS_REPOSITORY="backup-user@pbs@pbs.example.de:Backup"
export PBS_NAMESPACE="kunde-muster"
```

Bei Root-Namespace `PBS_NAMESPACE` leer lassen und bei den folgenden Befehlen `--ns` weglassen.

### 4. Vorhandene Host-Backups anzeigen

Mit Namespace:

```bash
proxmox-backup-client snapshot list \
  --repository "$PBS_REPOSITORY" \
  --ns "$PBS_NAMESPACE"
```

Ohne Namespace:

```bash
proxmox-backup-client snapshot list \
  --repository "$PBS_REPOSITORY"
```

Gesucht wird ein Backup in dieser Art:

```text
host/kunde-muster-pve01-config/2026-10-05T...
```

oder ohne Kunden-ID:

```text
host/pve01-config/2026-10-05T...
```

### 5. Backup zuerst in ein temporäres Verzeichnis zurückspielen

Das Backup niemals direkt über das laufende Dateisystem entpacken.

Restore-Verzeichnis anlegen:

```bash
mkdir -p /root/pve-config-restore
chmod 700 /root/pve-config-restore
```

Gewünschten Snapshot festlegen:

```bash
SNAPSHOT="host/kunde-muster-pve01-config/2026-10-05T00:00:00Z"
```

Mit Namespace:

```bash
proxmox-backup-client restore \
  "$SNAPSHOT" \
  pve-config.pxar \
  /root/pve-config-restore \
  --repository "$PBS_REPOSITORY" \
  --ns "$PBS_NAMESPACE"
```

Ohne Namespace:

```bash
proxmox-backup-client restore \
  "$SNAPSHOT" \
  pve-config.pxar \
  /root/pve-config-restore \
  --repository "$PBS_REPOSITORY"
```

Danach prüfen:

```bash
find /root/pve-config-restore -maxdepth 3 -type f | sort | less
```

### 6. Alte Netzwerkkonfiguration prüfen

Besonders wichtig bei zusätzlichen öffentlichen IP-Adressen:

```bash
cat /root/pve-config-restore/etc/network/interfaces
```

Falls vorhanden:

```bash
find /root/pve-config-restore/etc/network/interfaces.d \
  -maxdepth 1 -type f -print -exec cat {} \;
```

Gesicherten aktiven Zustand ansehen:

```bash
cat /root/pve-config-restore/system-info/network/ip-address-full.txt
cat /root/pve-config-restore/system-info/network/ip-route-all.txt
cat /root/pve-config-restore/system-info/network/ip6-route-all.txt
cat /root/pve-config-restore/system-info/network/ip-rule.txt
cat /root/pve-config-restore/system-info/network/ip6-rule.txt
cat /root/pve-config-restore/system-info/network/bridge-vlan.txt 2>/dev/null || true
```

Mit der neuen Installation vergleichen:

```bash
diff -u \
  /etc/network/interfaces \
  /root/pve-config-restore/etc/network/interfaces || true
```

### 7. Netzwerk gezielt wiederherstellen

Vorher aktuelle Neuinstallations-Konfiguration sichern:

```bash
cp -a /etc/network /root/network-before-restore
cp -a /etc/iproute2 /root/iproute2-before-restore 2>/dev/null || true
```

Wenn Interface-Namen, Gateway und Provider-Zuweisung passen:

```bash
cp -a /root/pve-config-restore/etc/network/. /etc/network/
```

Optional vorhandene Routing-Tabellen zurückspielen:

```bash
if [[ -d /root/pve-config-restore/etc/iproute2 ]]; then
  cp -a /root/pve-config-restore/etc/iproute2/. /etc/iproute2/
fi
```

Syntax prüfen:

```bash
ifquery --list
```

Wenn der Zugriff über eine lokale/Out-of-Band-Konsole gesichert ist:

```bash
ifreload -a
```

Danach kontrollieren:

```bash
ip -br addr
ip route show table all
ip -6 route show table all
ip rule
ip -6 rule
```

### 8. System- und eigene Skripte zurückspielen

Eigene Skripte:

```bash
cp -a /root/pve-config-restore/usr/local/sbin/. /usr/local/sbin/ 2>/dev/null || true
cp -a /root/pve-config-restore/usr/local/bin/. /usr/local/bin/ 2>/dev/null || true
```

Weitere Konfigurationen wie `/etc/fstab`, ZFS/LVM, sysctl, modprobe, SSH oder eigene systemd-Units immer zuerst vergleichen und nur gezielt übernehmen.

Beispiel:

```bash
diff -u /etc/fstab /root/pve-config-restore/etc/fstab || true
```

### 9. /etc/pve NICHT komplett blind überschreiben

`/etc/pve` wird von Proxmox über pmxcfs verwaltet.

Deshalb nicht:

```text
cp -a /root/pve-config-restore/etc/pve/. /etc/pve/
```

als pauschalen Restore durchführen.

Insbesondere Cluster-Zertifikate, Corosync-Daten und Inhalte unter `/etc/pve/priv/` dürfen nicht ungeprüft auf eine frische Installation kopiert werden.

Bei einem einzelnen Standalone-PVE können benötigte Konfigurationen gezielt verglichen bzw. übernommen werden, beispielsweise:

```text
storage.cfg
datacenter.cfg
firewall/
nodes/<alter-node>/qemu-server/
nodes/<alter-node>/lxc/
```

VM- und CT-Konfigurationen sollten nur passend zu den tatsächlich wiederhergestellten VM-/CT-Datenträgern übernommen werden.

### 10. VM- und CT-Backups aus PBS wiederherstellen

Nachdem Netzwerk und PBS-Zugriff funktionieren, die normalen VM-/CT-Backups über die PVE-Weboberfläche oder die Proxmox-Werkzeuge wiederherstellen.

Die Host-Konfigurationssicherung ersetzt kein VM-/CT-Backup.

### 11. Backup-Projekt auf dem neuen PVE wieder installieren

Nach erfolgreichem Restore:

```bash
apt update
apt install -y git

cd /opt
git clone --depth 1 https://github.com/Cobra97332/openmain-it-installer.git
cd /opt/openmain-it-installer/pve-config-backup

bash install.sh
```

Der Installer fragt wieder nach Kunden-ID und PBS-Storage und erstellt sofort ein neues Testbackup.

### 12. Abschlusskontrolle

Nach dem Wiederaufbau prüfen:

```bash
ip -br addr
ip route show table all
ip rule
pvesm status
qm list
pct list
systemctl status pve-config-backup.timer --no-pager
/usr/local/sbin/pve-config-backup.sh --check
```

Erst wenn Netzwerk, zusätzliche IP-Adressen, PBS, Storage sowie VM-/CT-Zugriffe korrekt funktionieren, ist der Restore abgeschlossen.

