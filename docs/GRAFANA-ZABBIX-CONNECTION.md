# Grafana ↔ Zabbix – Verbindung und Fehlerbehebung

Stand: 2026-10-07

## Getesteter Stand

```text
Grafana: 13.2.1
Grafana-Zabbix Plugin: 6.8.0
Zabbix: 7.4.15
```

Grafana und Zabbix sind über das gemeinsame Docker-Netz `zabbix-docker_frontend` verbunden.

## Interne API-Verbindung

Der Zabbix-Webcontainer besitzt im Frontend-Netz den Service-Alias:

```text
zabbix-web-nginx-pgsql
```

und lauscht intern auf:

```text
8080/tcp
```

Ein erfolgreicher API-Test:

```bash
docker run --rm \
  --network zabbix-docker_frontend \
  curlimages/curl:latest \
  -sS \
  -H 'Content-Type: application/json-rpc' \
  --data '{"jsonrpc":"2.0","method":"apiinfo.version","params":{},"id":1}' \
  http://zabbix-docker-zabbix-web-nginx-pgsql-1:8080/api_jsonrpc.php
```

Ergebnis:

```json
{"jsonrpc":"2.0","result":"7.4.15","id":1}
```

Damit sind Docker-Netzwerk, Port und Zabbix-API grundsätzlich funktionsfähig.

## Grafana Datasource

Interne URL:

```text
http://zabbix-docker-zabbix-web-nginx-pgsql-1:8080/api_jsonrpc.php
```

HTTP Authentication:

```text
No Authentication
```

Die HTTP-Authentifizierung ist von der Zabbix-API-Authentifizierung zu unterscheiden.

Für die Zabbix-Datasource ist zusätzlich ein Zabbix-API-Token oder eine unterstützte Zabbix-Anmeldung erforderlich. Für produktiven Betrieb sollte ein eigener Service-Benutzer mit minimal notwendigen Leserechten verwendet werden.

## Fehler: origin not allowed

Wenn Grafana hinter einem Reverse Proxy betrieben wird, muss die externe URL konsistent konfiguriert sein.

Beispiel:

```yaml
environment:
  GF_SERVER_DOMAIN: grafana.openmain-it.de
  GF_SERVER_ROOT_URL: https://grafana.openmain-it.de
  GF_SECURITY_CSRF_TRUSTED_ORIGINS: https://grafana.openmain-it.de
  GF_SECURITY_CSRF_ADDITIONAL_HEADERS: X-Forwarded-Host
```

Danach:

```bash
cd /opt/grafana
docker compose config
docker compose up -d --force-recreate
```

Prüfung:

```bash
docker exec grafana env | grep -E 'GF_SERVER|GF_SECURITY_CSRF'
```

## Fehler: Could not connect to given url

Zuerst den API-Endpunkt unabhängig von Grafana testen. Wenn der JSON-RPC-Test erfolgreich ist, liegt kein grundlegendes Docker-Netzwerkproblem vor.

## Fehler: Invalid params. Not authorized.

Dieser Fehler bestätigt, dass Grafana den Zabbix-Server erreicht, die Zabbix-API den Request aber wegen fehlender oder ungültiger Authentifizierung ablehnt.

Typischer Logeintrag:

```text
Error connecting Zabbix server
err="Invalid params. Not authorized."
```

Lösung:

1. Zabbix-API-Token für einen dedizierten Grafana-Benutzer erstellen.
2. Token in der Zabbix-Datasource konfigurieren.
3. Benutzerrechte auf die benötigten Hostgruppen prüfen.
4. Keine Tokens im Git-Repository speichern.

## Fehler: trying to update old version of datasource

Wenn Grafana mit HTTP 409 meldet:

```text
trying to update old version of datasource
```

die Datasource-Seite neu laden. Bei inkonsistentem Zustand die Datasource sauber neu anlegen und die aktuelle Konfiguration erneut speichern.

## Diagnosebefehle

```bash
docker logs grafana --since 5m 2>&1 | \
grep -Ei 'zabbix|datasource|plugin|health|connect|error|failed|origin|csrf'
```

Grafana-Netzwerke:

```bash
docker inspect grafana \
  --format '{{range $name,$conf := .NetworkSettings.Networks}}{{println $name}}{{end}}'
```

Zabbix-Web-Netzwerke und Aliase:

```bash
docker inspect zabbix-docker-zabbix-web-nginx-pgsql-1 \
  --format '{{range $name,$net := .NetworkSettings.Networks}}NETZ={{$name}} IP={{$net.IPAddress}} ALIASES={{json $net.Aliases}}{{println}}{{end}}'
```

## Sicherheit

- Grafana und Zabbix intern über Docker/NetBird verbinden, nicht unnötig über den öffentlichen Reverse Proxy.
- Dedizierten Zabbix-Service-Benutzer für Grafana verwenden.
- Nur benötigte Leserechte vergeben.
- API-Tokens niemals in Git committen.
