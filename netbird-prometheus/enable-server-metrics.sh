#!/usr/bin/env bash
set -Eeuo pipefail

NETBIRD_CONTAINER="${NETBIRD_CONTAINER:-netbird-server}"
METRICS_PORT="${NETBIRD_METRICS_PORT:-9090}"
NETBIRD_INTERFACE="${NETBIRD_INTERFACE:-wt0}"
BIND_IP="${NETBIRD_METRICS_BIND_IP:-}"

die() { echo "[FEHLER] $*" >&2; exit 1; }
log() { echo "[+] $*"; }

[[ $EUID -eq 0 ]] || die "Bitte als root ausführen."
command -v docker >/dev/null 2>&1 || die "docker fehlt."
docker compose version >/dev/null 2>&1 || die "Docker Compose Plugin fehlt."

docker inspect "$NETBIRD_CONTAINER" >/dev/null 2>&1 || die "Container $NETBIRD_CONTAINER nicht gefunden."

COMPOSE_SERVICE="$(docker inspect "$NETBIRD_CONTAINER" --format '{{index .Config.Labels "com.docker.compose.service"}}')"
COMPOSE_WORKDIR="$(docker inspect "$NETBIRD_CONTAINER" --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}')"
COMPOSE_FILES_RAW="$(docker inspect "$NETBIRD_CONTAINER" --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}')"

[[ -n "$COMPOSE_SERVICE" && "$COMPOSE_SERVICE" != "<no value>" ]] || die "Compose-Service nicht ermittelbar."
[[ -n "$COMPOSE_WORKDIR" && "$COMPOSE_WORKDIR" != "<no value>" && -d "$COMPOSE_WORKDIR" ]] || die "Compose-Verzeichnis nicht ermittelbar."
[[ -n "$COMPOSE_FILES_RAW" && "$COMPOSE_FILES_RAW" != "<no value>" ]] || die "Compose-Dateien nicht ermittelbar."

if [[ -z "$BIND_IP" ]]; then
  BIND_IP="$(ip -4 -o addr show dev "$NETBIRD_INTERFACE" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)"
fi
[[ -n "$BIND_IP" ]] || die "Keine IPv4 auf $NETBIRD_INTERFACE gefunden. NETBIRD_METRICS_BIND_IP explizit setzen."

OVERRIDE_FILE="$COMPOSE_WORKDIR/compose.openmain-netbird-metrics.yaml"

cat > "$OVERRIDE_FILE" <<EOF
services:
  $COMPOSE_SERVICE:
    ports:
      - "$BIND_IP:$METRICS_PORT:$METRICS_PORT/tcp"
EOF
chmod 0644 "$OVERRIDE_FILE"

IFS=',' read -r -a compose_files <<< "$COMPOSE_FILES_RAW"
compose_cmd=(docker compose)
for file in "${compose_files[@]}"; do
  file="${file#"${file%%[![:space:]]*}"}"
  file="${file%"${file##*[![:space:]]}"}"
  [[ -n "$file" ]] || continue
  [[ "$file" == "$OVERRIDE_FILE" ]] && continue
  if [[ "$file" != /* ]]; then
    file="$COMPOSE_WORKDIR/$file"
  fi
  [[ -f "$file" ]] || die "Compose-Datei nicht gefunden: $file"
  compose_cmd+=(-f "$file")
done
compose_cmd+=(-f "$OVERRIDE_FILE")

log "Prüfe Compose-Konfiguration..."
(
  cd "$COMPOSE_WORKDIR"
  "${compose_cmd[@]}" config >/dev/null
)

log "Recreate des NetBird-Server-Service..."
(
  cd "$COMPOSE_WORKDIR"
  "${compose_cmd[@]}" up -d --no-deps "$COMPOSE_SERVICE"
)

sleep 3
[[ "$(docker inspect "$NETBIRD_CONTAINER" --format '{{.State.Status}}' 2>/dev/null || true)" == "running" ]] || die "NetBird-Server läuft nach Recreate nicht."

log "Prüfe Metrics-Endpunkt..."
curl -fsS --max-time 5 "http://$BIND_IP:$METRICS_PORT/metrics" | head -n 5

echo
echo "NetBird Metrics erreichbar unter:"
echo "  http://$BIND_IP:$METRICS_PORT/metrics"
echo
echo "Port $METRICS_PORT nicht über Traefik oder die öffentliche Firewall freigeben."
