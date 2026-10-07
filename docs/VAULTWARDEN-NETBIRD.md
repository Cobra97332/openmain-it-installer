# Vaultwarden über NetBird – Betrieb und Fehlerbehebung

Stand: 2026-10-07

## Zielaufbau

Vaultwarden läuft ohne eigenes Docker-Netzwerk und verwendet vollständig den Netzwerk-Namespace eines NetBird-Sidecars:

```text
Vaultwarden
  |
  +-- network_mode: service:netbird-client
          |
          +-- NetBird Mesh
                 |
                 +-- vault.openmain-it.de
```

Vaultwarden selbst veröffentlicht keinen eigenen Host-Port für den privaten Zugriff.

## Relevante Compose-Konfiguration

Der Vaultwarden-Service besitzt bereits:

```yaml
restart: unless-stopped
network_mode: service:netbird-client
```

Der NetBird-Sidecar muss ebenfalls automatisch neu starten:

```yaml
netbird-client:
  container_name: netbird-client
  restart: unless-stopped
  cap_add:
    - NET_ADMIN
  environment:
    - NB_SETUP_KEY=<SETUP_KEY-ODER-BESTEHENDER-WERT>
    - NB_HOSTNAME=vaulwarden
    - NB_MANAGEMENT_URL=https://netbird.openmain-it.de
  volumes:
    - ./netbird:/var/lib/netbird
  image: netbirdio/netbird:latest
```

Wichtig: Der vorhandene NetBird-State liegt persistent unter:

```text
/opt/vaultwarden/netbird
```

Ein Setup-Key wird nur für ein neues Enrollment benötigt. Solange der persistente NetBird-State vorhanden und der Client registriert ist, kann der Container ohne erneutes Enrollment starten.

## Fehlerbild vom 2026-10-07

Von einem NetBird-Client war TCP/443 und TLS erreichbar:

```text
vault.openmain-it.de -> NetBird-IP
TLS-Verbindung erfolgreich
Zertifikat für vault.openmain-it.de wird ausgeliefert
```

Die Anwendung antwortete jedoch nicht korrekt.

Auf dem Docker-Host zeigte:

```bash
docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' | grep -Ei 'vault|warden'
```

dass der Vaultwarden-Container beendet war.

Nach dem Start:

```bash
docker start vaultwarden
```

meldete Vaultwarden:

```text
Rocket has launched from http://0.0.0.0:80
```

und war wieder erreichbar.

## Dauerhafte Korrektur

Ursache des instabilen Aufbaus war, dass nur Vaultwarden eine Restart-Policy hatte. Da Vaultwarden mit:

```yaml
network_mode: service:netbird-client
```

vollständig vom Netzwerk-Namespace des Sidecars abhängt, muss auch `netbird-client` automatisch neu starten.

Ergänzt wurde daher ausschließlich:

```yaml
restart: unless-stopped
```

im Service `netbird-client`.

Anschließend:

```bash
cd /opt/vaultwarden
docker compose up -d
```

Prüfung:

```bash
docker inspect netbird-client --format '{{.HostConfig.RestartPolicy.Name}}'
```

Erwartet:

```text
unless-stopped
```

## Diagnose

### Containerstatus

```bash
docker compose ps
docker ps -a --filter name=vaultwarden
docker ps -a --filter name=netbird-client
```

### Vaultwarden-Logs

```bash
docker logs vaultwarden --since 20m
```

### NetBird-Logs und Status

```bash
docker logs netbird-client --since 20m
docker exec netbird-client netbird status
```

### Externer Test über NetBird

```bash
curl -vk --max-time 10 https://vault.openmain-it.de/
```

Interpretation:

- TLS-Verbindung erfolgreich, aber keine HTTP-Antwort: Backend/Container prüfen.
- `502`/`504`: Reverse Proxy erreicht Vaultwarden nicht.
- Vaultwarden `Exited`: Containerlogs und Restart-Policy prüfen.
- NetBird nicht `Connected`: Sidecar und persistente NetBird-Daten prüfen.

## Vaultwarden config.json

Vaultwarden kann melden:

```text
Using saved config from data/config.json
The following environment variables are being overridden by the config.json file.
```

Dann existieren zwei Konfigurationsquellen:

```text
docker-compose.yml
data/config.json
```

`data/config.json` kann Environment-Werte überschreiben. Vor Änderungen immer sichern:

```bash
cd /opt/vaultwarden
cp -a data/config.json "data/config.json.backup-$(date +%Y%m%d-%H%M%S)"
```

Die Datei nicht ungeprüft löschen, insbesondere wenn SSO über Authentik aktiv ist.

## Sicherheit

Keine realen Werte für folgende Variablen in Git speichern:

- `NB_SETUP_KEY`
- `ADMIN_TOKEN`
- `SSO_CLIENT_SECRET`
- API-Tokens oder Passwörter

Geheimnisse gehören in eine nicht versionierte `.env` oder einen Secret-Store.

## Betriebsregel

Bei Diensten mit `network_mode: service:<sidecar>` müssen Anwendung und Netzwerk-Sidecar eine passende Restart-Policy besitzen. Ein funktionierender Anwendungscontainer ist sonst vom Lebenszyklus des Sidecars abhängig.
