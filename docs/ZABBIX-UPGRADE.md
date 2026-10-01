# Zabbix aktualisieren

Diese Anleitung beschreibt das Update von Zabbix auf Debian 13 für die OpenMain-IT Router und Zabbix-Systeme.

Die Befehle orientieren sich an der offiziellen Zabbix-Dokumentation.

## 1. Aktuelle Version prüfen

Auf dem System:

```bash
zabbix_proxy --version 2>/dev/null || true
zabbix_server --version 2>/dev/null || true
zabbix_agent2 --version 2>/dev/null || true
apt-cache policy zabbix-proxy-mysql zabbix-proxy-sqlite3 zabbix-server-mysql zabbix-agent2
```

Zusätzlich:

```bash
grep -E '^(Server|ServerActive|Hostname|TLSConnect|TLSPSKIdentity|TLSPSKFile|DBName)=' /etc/zabbix/zabbix_proxy.conf 2>/dev/null || true
```

## 2. Vor dem Update sichern

### Zabbix-Konfiguration

```bash
sudo mkdir -p /opt/zabbix-backup
sudo cp -a /etc/zabbix /opt/zabbix-backup/
```

Bei unserem Proxy insbesondere:

```bash
sudo cp -a /etc/zabbix/zabbix_proxy.conf /opt/zabbix-backup/ 2>/dev/null || true
sudo cp -a /etc/zabbix/zabbix_proxy.psk /opt/zabbix-backup/ 2>/dev/null || true
sudo chmod 700 /opt/zabbix-backup
sudo chmod 600 /opt/zabbix-backup/zabbix_proxy.psk 2>/dev/null || true
```

### Proxy-Datenbank

Zuerst prüfen:

```bash
sudo grep '^DBName=' /etc/zabbix/zabbix_proxy.conf
```

Wenn SQLite verwendet wird, den dort angegebenen `DBName` sichern. Beispiel:

```bash
sudo cp -a /var/lib/zabbix/zabbix_proxy.db /opt/zabbix-backup/ 2>/dev/null || true
```

Bei einer externen MySQL/MariaDB/PostgreSQL-Datenbank muss zusätzlich ein Datenbank-Backup erstellt werden.

## 3. Minor-Update innerhalb derselben Zabbix-Hauptversion

Beispiel: 8.0.x -> 8.0.y.

```bash
sudo apt update
sudo apt install --only-upgrade 'zabbix*'
```

Nur den Zabbix Proxy:

### SQLite

```bash
sudo systemctl stop zabbix-proxy
sudo apt update
sudo apt install --only-upgrade zabbix-proxy-sqlite3
sudo systemctl start zabbix-proxy
```

### MySQL/MariaDB

```bash
sudo systemctl stop zabbix-proxy
sudo apt update
sudo apt install --only-upgrade zabbix-proxy-mysql
sudo systemctl start zabbix-proxy
```

Zabbix empfiehlt, den Proxy während des Upgrades zu stoppen und danach wieder zu starten.

## 4. Zabbix Agent 2 aktualisieren

```bash
sudo systemctl stop zabbix-agent2
sudo apt update
sudo apt install --only-upgrade zabbix-agent2 'zabbix-agent2-plugin-*'
sudo systemctl start zabbix-agent2
```

Danach:

```bash
systemctl --no-pager --full status zabbix-agent2
zabbix_agent2 --version
```

## 5. Zabbix Server aktualisieren

Bei einem Server mit MySQL/MariaDB:

```bash
sudo systemctl stop zabbix-server
sudo apt update
sudo apt install --only-upgrade zabbix-server-mysql zabbix-frontend-php zabbix-agent
sudo systemctl start zabbix-server
```

Bei PostgreSQL:

```bash
sudo systemctl stop zabbix-server
sudo apt update
sudo apt install --only-upgrade zabbix-server-pgsql zabbix-frontend-php zabbix-agent
sudo systemctl start zabbix-server
```

Webserver je nach Installation anschließend neu starten:

```bash
sudo systemctl restart apache2
```

oder:

```bash
sudo systemctl restart nginx
```

## 6. Major-Upgrade, z. B. 7.4 -> 8.0

Ein Major-Upgrade ist **nicht** nur ein normales `apt upgrade`.

Vorher:

1. Zabbix-Versionssprung prüfen.
2. Offizielle Upgrade-Hinweise lesen.
3. Datenbank sichern.
4. Konfiguration sichern.
5. Zabbix-Repository auf die neue Hauptversion umstellen.

Für Debian 13 und Zabbix 8.0:

```bash
sudo rm -f /etc/apt/sources.list.d/zabbix.list

cd /tmp
wget https://repo.zabbix.com/zabbix/8.0/release/debian/pool/main/z/zabbix-release/zabbix-release_latest+debian13_all.deb
sudo dpkg -i zabbix-release_latest+debian13_all.deb

sudo apt update
```

Danach die passenden Zabbix-Pakete aktualisieren.

### Proxy

SQLite:

```bash
sudo systemctl stop zabbix-proxy
sudo apt install --only-upgrade zabbix-proxy-sqlite3
sudo systemctl start zabbix-proxy
```

MySQL/MariaDB:

```bash
sudo systemctl stop zabbix-proxy
sudo apt install --only-upgrade zabbix-proxy-mysql
sudo systemctl start zabbix-proxy
```

### Server

MySQL/MariaDB:

```bash
sudo systemctl stop zabbix-server
sudo apt install --only-upgrade zabbix-server-mysql zabbix-frontend-php
sudo systemctl start zabbix-server
```

PostgreSQL:

```bash
sudo systemctl stop zabbix-server
sudo apt install --only-upgrade zabbix-server-pgsql zabbix-frontend-php
sudo systemctl start zabbix-server
```

Bei einem Major-Upgrade kann die Datenbankmigration beim ersten Start längere Zeit dauern.

## 7. Nach dem Update prüfen

### Proxy

```bash
systemctl --no-pager --full status zabbix-proxy
zabbix_proxy --version
```

Logs:

```bash
sudo journalctl -u zabbix-proxy -n 100 --no-pager
```

Live:

```bash
sudo journalctl -u zabbix-proxy -f
```

### Server

```bash
systemctl --no-pager --full status zabbix-server
zabbix_server --version
sudo journalctl -u zabbix-server -n 100 --no-pager
```

### PSK prüfen

Bei unserem NetBird/Zabbix-Proxy:

```bash
sudo ls -l /etc/zabbix/zabbix_proxy.psk
sudo grep -E '^(TLSConnect|TLSPSKIdentity|TLSPSKFile)=' /etc/zabbix/zabbix_proxy.conf
```

Die PSK-Datei sollte nach dem Update weiterhin vorhanden sein.

## 8. Proxy-Verbindung zum Server prüfen

Von einem Proxy:

```bash
nc -vz 100.67.255.142 10051
```

Falls `nc` nicht installiert ist:

```bash
sudo apt install -y netcat-openbsd
nc -vz 100.67.255.142 10051
```

Danach im Zabbix-Frontend prüfen, ob der Proxy wieder Daten liefert.

## 9. Wichtig bei unserem OpenMain-IT Router

Beim Update **nicht** diese Dateien löschen:

```text
/etc/zabbix/zabbix_proxy.conf
/etc/zabbix/zabbix_proxy.psk
```

Insbesondere die PSK-Konfiguration muss erhalten bleiben:

```text
TLSConnect=psk
TLSPSKIdentity=...
TLSPSKFile=/etc/zabbix/zabbix_proxy.psk
```

Nach dem Paketupdate immer kontrollieren:

```bash
sudo grep -E '^(Hostname|Server|TLSConnect|TLSPSKIdentity|TLSPSKFile)=' /etc/zabbix/zabbix_proxy.conf
sudo test -s /etc/zabbix/zabbix_proxy.psk && echo "PSK vorhanden"
```

## 10. Empfohlene Reihenfolge bei mehreren Proxys

Wenn Server und mehrere Proxys aktualisiert werden:

```text
1. Datenbank-Backup
2. Zabbix Server aktualisieren
3. Zabbix Server starten und Logs prüfen
4. Proxy 1 aktualisieren
5. Proxy 1 prüfen
6. Proxy 2 aktualisieren
7. weitere Proxys nacheinander
8. Agenten aktualisieren
```

Während der Server kurz nicht verfügbar ist, können laufende Proxys Daten weiter sammeln. Zabbix empfiehlt, Proxys nach dem Server-Upgrade nacheinander zu aktualisieren.

## Offizielle Dokumentation

- https://www.zabbix.com/documentation/8.0/de/manual/installation/upgrade
- https://www.zabbix.com/documentation/8.0/de/manual/installation/upgrade/packages/debian_ubuntu

Stand dieser Anleitung: Oktober 2026.

Diese Anleitung bezieht sich auf Debian 13 und die offiziellen Zabbix-DEB-Pakete. Bei einem Wechsel auf eine andere Debian-Version oder Zabbix-Hauptversion müssen die offiziellen Upgrade-Hinweise erneut geprüft werden.
