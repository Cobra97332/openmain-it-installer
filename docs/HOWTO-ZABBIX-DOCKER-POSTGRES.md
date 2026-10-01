# HOWTO – Zabbix Docker + PostgreSQL

Diese Anleitung beschreibt Pflege, Backup und Updates der Zabbix-Installation mit Docker Compose und PostgreSQL.

## 1. Docker-Verzeichnis

Beispiel:

~~~
cd /opt/zabbix-docker
~~~

Services anzeigen:

~~~
docker compose config --services
docker compose ps
~~~

Container anzeigen:

~~~
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
~~~

## 2. Vor einem Update: PostgreSQL sichern

Backup-Verzeichnis:

~~~
sudo mkdir -p /opt/zabbix-backup
~~~

PostgreSQL-Container ermitteln:

~~~
docker ps --format '{{.Names}}' | grep -Ei 'postgres|postgresql'
~~~

Beispiel mit Containername postgres-server:

~~~
docker exec -t postgres-server pg_dump -U zabbix -d zabbix \
  > /opt/zabbix-backup/zabbix-$(date +%F-%H%M).sql
~~~

Backup prüfen:

~~~
ls -lh /opt/zabbix-backup/
~~~

Benutzername, Datenbankname und Containername müssen zu deiner Compose-Konfiguration passen.

## 3. Compose-Konfiguration sichern

~~~
cp -a compose_pgsql.yaml /opt/zabbix-backup/ 2>/dev/null || true
cp -a .env /opt/zabbix-backup/ 2>/dev/null || true
~~~

Bei einer Git-Installation:

~~~
git status
git diff
~~~

## 4. Normales Zabbix-Update

Für ein Minor-Update innerhalb derselben Hauptversion:

~~~
docker compose -f compose_pgsql.yaml pull
docker compose -f compose_pgsql.yaml up -d
~~~

Danach:

~~~
docker compose ps
~~~

Logs:

~~~
docker compose logs --tail=100 zabbix-server-pgsql
docker compose logs --tail=100 postgres-server
~~~

Falls die Servicenamen abweichen:

~~~
docker compose config --services
~~~

verwenden.

## 5. PostgreSQL prüfen

~~~
docker exec -it postgres-server pg_isready -U zabbix -d zabbix
~~~

PostgreSQL-Version:

~~~
docker exec -it postgres-server \
  psql -U zabbix -d zabbix -c 'SELECT version();'
~~~

## 6. Major-Upgrade

Beispiel:

~~~
Zabbix 7.4 -> Zabbix 8.0
~~~

Vorher:

1. PostgreSQL-Backup erstellen.
2. Compose-Dateien sichern.
3. Aktuelle Zabbix-Version prüfen.
4. Zielversion und offizielle Upgrade-Hinweise prüfen.
5. Erst danach die Image-/Compose-Version ändern.

Danach:

~~~
docker compose -f compose_pgsql.yaml pull
docker compose -f compose_pgsql.yaml up -d
~~~

Während des ersten Starts kann Zabbix die Datenbank migrieren.

Logs beobachten:

~~~
docker compose logs -f zabbix-server-pgsql
~~~

PostgreSQL:

~~~
docker compose logs -f postgres-server
~~~

## 7. PostgreSQL-Volume niemals versehentlich löschen

Für ein normales Update nicht verwenden:

~~~
docker compose down -v
~~~

Das kann die zugehörigen Docker-Volumes entfernen.

Volumes prüfen:

~~~
docker volume ls
~~~

Mounts prüfen:

~~~
docker inspect postgres-server --format '{{json .Mounts}}'
~~~

## 8. Zabbix-Version prüfen

Image:

~~~
docker images | grep -i zabbix
~~~

Server:

~~~
docker exec <zabbix-server-container> zabbix_server --version
~~~

Web-Image:

~~~
docker inspect <zabbix-web-container> --format '{{.Config.Image}}'
~~~

## 9. Zabbix Proxy prüfen

Bei einem Docker-Proxy:

~~~
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' | grep -i zabbix
~~~

Bei unserem Debian-NetBird/Zabbix-Proxy:

~~~
systemctl status zabbix-proxy
zabbix_proxy --version
journalctl -u zabbix-proxy -n 100 --no-pager
~~~

NetBird-Verbindung zum Zabbix Server:

~~~
nc -vz 100.67.255.142 10051
~~~

Falls nc fehlt:

~~~
apt update
apt install -y netcat-openbsd
~~~

## 10. PSK prüfen

Auf dem OpenMain-IT Proxy:

~~~
sudo ls -l /etc/zabbix/zabbix_proxy.psk
sudo grep -E '^(Hostname|Server|TLSConnect|TLSPSKIdentity|TLSPSKFile)=' /etc/zabbix/zabbix_proxy.conf
sudo test -s /etc/zabbix/zabbix_proxy.psk && echo "PSK vorhanden"
~~~

Die PSK-Datei niemals in GitHub speichern.

## 11. Fehlerdiagnose

Zuerst:

~~~
docker compose ps
docker compose logs --tail=200 zabbix-server-pgsql
docker compose logs --tail=200 postgres-server
~~~

Datenbank:

~~~
docker exec -it postgres-server pg_isready -U zabbix -d zabbix
~~~

Services:

~~~
docker compose config --services
~~~

Nicht vorschnell Container oder Volumes löschen.

## 12. Standardablauf

Für ein normales Update:

~~~
cd /opt/zabbix-docker

docker compose ps

mkdir -p /opt/zabbix-backup

docker exec -t postgres-server pg_dump -U zabbix -d zabbix \
  > /opt/zabbix-backup/zabbix-$(date +%F-%H%M).sql

docker compose -f compose_pgsql.yaml pull
docker compose -f compose_pgsql.yaml up -d

docker compose ps

docker compose logs --tail=100 zabbix-server-pgsql
docker compose logs --tail=100 postgres-server
~~~

Danach im Zabbix-Frontend prüfen:

- Zabbix Server verfügbar
- Daten kommen von den Proxys
- Proxys sind online
- keine Datenbankfehler
- keine TLS/PSK-Fehler
- aktuelle Zabbix-Version

## Wichtig

Immer:

~~~
Backup -> Update -> Logs -> Datenbank prüfen -> Proxy prüfen
~~~

Nicht:

- PostgreSQL-Volume löschen
- Datenbankcontainer ohne Backup entfernen
- PSK-Dateien überschreiben
- Secrets in GitHub speichern
- bei einem Major-Upgrade einfach nur Container neu starten, ohne die Zabbix-Upgrade-Hinweise zu prüfen

## Offizielle Dokumentation

Zabbix Container Upgrade:

https://www.zabbix.com/documentation/8.0/de/manual/installation/upgrade/containers

Zabbix Container Installation:

https://www.zabbix.com/documentation/8.0/de/manual/installation/install/containers

Stand: Oktober 2026.
