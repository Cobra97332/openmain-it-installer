# Zabbix aktualisieren – Docker + PostgreSQL

Diese Anleitung ist für eine Zabbix-Installation mit **Docker Compose und PostgreSQL** gedacht.

Die offiziellen Zabbix-Container für PostgreSQL verwenden unter anderem:

- `zabbix/zabbix-server-pgsql`
- `zabbix/zabbix-web-nginx-pgsql`
- `postgres`

Zabbix beschreibt für Container-Upgrades ausdrücklich das Aktualisieren der Container-Images bzw. der Docker-Compose-Dateien. Vor einem Upgrade soll die Zabbix-Datenbank gesichert werden. Bei einem Major-Upgrade kann die Datenbankmigration längere Zeit dauern.

## 1. In das Zabbix-Docker-Verzeichnis wechseln

Beispiel:

```bash
cd /opt/zabbix-docker
```

Falls dein Compose-Verzeichnis anders heißt, entsprechend dorthin wechseln.

## 2. Aktuelle Container prüfen

```bash
docker compose ps
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
```

Versionen der laufenden Zabbix-Container:

```bash
docker inspect -f '{{.Name}} -> {{.Config.Image}}' $(docker ps -q --filter 'name=zabbix')
```

## 3. PostgreSQL-Backup erstellen

**Vor jedem Major-Upgrade zuerst die Datenbank sichern.**

PostgreSQL-Container ermitteln:

```bash
docker ps --format '{{.Names}}' | grep -E 'postgres|postgresql'
```

Angenommen der Container heißt `postgres-server`:

```bash
mkdir -p /opt/zabbix-backup
```

Datenbank sichern:

```bash
docker exec -t postgres-server pg_dump -U zabbix -d zabbix > /opt/zabbix-backup/zabbix-$(date +%F-%H%M).sql
```

Prüfen:

```ls -lh /opt/zabbix-backup/
```

Das Passwort wird bei einer normalen PostgreSQL-Containerinstallation über die Container-Umgebung bzw. das Compose-Setup verwaltet. Nicht in diese öffentliche Dokumentation eintragen.

## 4. Compose-Dateien und lokale Änderungen sichern

Vor einem `git pull`:

```bash
cp -a compose_pgsql.yaml /opt/zabbix-backup/ 2>/dev/null || true
cp -a .env /opt/zabbix-backup/ 2>/dev/null || true
```

Wenn das Zabbix-Docker-Repository per Git verwaltet wird:

```bash
git status
git diff
```

Lokale Änderungen müssen vor einem Wechsel des Branches bzw. einem Pull gesichert werden.

## 5. Minor-Update innerhalb derselben Hauptversion

Wenn beispielsweise innerhalb von Zabbix 8.0 auf die aktuelle 8.0-Minor-Version aktualisiert werden soll:

```bash
docker compose -f compose_pgsql.yaml pull
docker compose -f compose_pgsql.yaml up -d
```

Damit werden die neuen Zabbix-Images geladen und die Container mit den vorhandenen Volumes neu erstellt.

**PostgreSQL nicht löschen.**

Nicht ausführen:

```bash
docker compose down -v
```

wenn die Daten-Volumes erhalten bleiben sollen.

## 6. Major-Upgrade, z. B. 7.4 → 8.0

Bei einem Major-Upgrade zuerst die offizielle Upgrade-Dokumentation und die Upgrade Notes der Zielversion prüfen.

Wenn das Zabbix-Docker-Repository verwendet wird:

```bash
cd /opt/zabbix-docker

git status
git pull
git checkout 8.0
```

Danach PostgreSQL-Compose verwenden:

```bash
docker compose -f compose_pgsql.yaml pull
docker compose -f compose_pgsql.yaml up -d
```

Zabbix führt beim Start die erforderlichen Datenbankänderungen durch.

## 7. Wichtig: PostgreSQL während des Upgrades nicht löschen

Der PostgreSQL-Container und insbesondere dessen Daten-Volume müssen erhalten bleiben.

Prüfen:

```bash
docker volume ls
```

und:

```bash
docker inspect postgres-server --format '{{json .Mounts}}'
```

Den tatsächlichen PostgreSQL-Containernamen aus `docker compose ps` bzw. `docker ps` verwenden.

## 8. Logs während des Upgrades beobachten

Zabbix Server:

```bash
docker compose logs -f zabbix-server-pgsql
```

Falls der Compose-Service anders heißt:

```bash
docker compose ps
```

und den dort angezeigten Servicenamen verwenden.

PostgreSQL:

```bash
docker compose logs -f postgres-server
```

Weboberfläche:

```bash
docker compose logs -f zabbix-web-nginx-pgsql
```

## 9. Status nach dem Update

```bash
docker compose ps
```

Die Zabbix-Container sollten `Up` sein.

Falls ein Container `Exited` ist:

```bash
docker compose logs --tail=200 <service>
```

## 10. Zabbix-Version prüfen

Server:

```bash
docker exec <zabbix-server-container> zabbix_server --version
```

Alternativ:

```bash
docker images | grep zabbix
```

Auch im Zabbix-Webfrontend kann die installierte Version kontrolliert werden.

## 11. PostgreSQL prüfen

```bash
docker exec -it postgres-server psql -U zabbix -d zabbix -c 'SELECT version();'
```

Danach Zabbix Server erneut prüfen:

```bash
docker compose ps
docker compose logs --tail=100 zabbix-server-pgsql
```

## 12. Zabbix Proxy prüfen

Wenn die Proxys nach dem Server-Upgrade weiterarbeiten sollen:

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' | grep zabbix
```

Bei Docker-Proxys ebenfalls das Image auf die passende Zabbix-Hauptversion aktualisieren.

Zabbix unterstützt Proxys derselben Hauptversion wie der Server vollständig. Ältere unterstützte Proxy-Versionen haben Einschränkungen.

## 13. Bei Problemen

Zuerst:

```bash
docker compose ps
docker compose logs --tail=200 zabbix-server-pgsql
docker compose logs --tail=200 postgres-server
```

Datenbankverbindung prüfen:

```bash
docker exec -it postgres-server pg_isready -U zabbix -d zabbix
```

Volumes prüfen:

```bash
docker volume ls
```

**Nicht vorschnell Container oder Volumes löschen.**

## 14. Kurzversion für normales Update

Für ein normales Minor-Update innerhalb derselben Zabbix-Hauptversion:

```bash
cd /opt/zabbix-docker

docker compose -f compose_pgsql.yaml pull
docker compose -f compose_pgsql.yaml up -d

docker compose ps
docker compose logs --tail=100 zabbix-server-pgsql
```

Vor einem Major-Upgrade zusätzlich:

```bash
mkdir -p /opt/zabbix-backup
docker exec -t postgres-server pg_dump -U zabbix -d zabbix > /opt/zabbix-backup/zabbix-$(date +%F-%H%M).sql
```

Danach erst auf die neue Zabbix-Hauptversion wechseln.

## Offizielle Dokumentation

- https://www.zabbix.com/documentation/8.0/de/manual/installation/upgrade/containers
- https://www.zabbix.com/documentation/8.0/de/manual/installation/install/containers

Stand: Oktober 2026.


---

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
nc -vz 100.107.91.6 10051
```

Falls `nc` nicht installiert ist:

```bash
sudo apt install -y netcat-openbsd
nc -vz 100.107.91.6 10051
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
