#!/usr/bin/env bash
set -Eeuo pipefail

NETBIRD_CONTAINER="${NETBIRD_CONTAINER:-netbird-server}"
METRICS_PORT="${NETBIRD_METRICS_PORT:-9090}"
NETBIRD_INTERFACE="${NETBIRD_INTERFACE:-wt0}"
BIND_IP="${NETBIRD_METRICS_BIND_IP:-}"

die() { echo "[FEHLER] $*" >&2; exit 1; }
log() { echo "[+] $*"; }
warn() { echo "[!] $*" >&2; }

trap 'rc=$?; echo "[FEHLER] Unerwarteter Abbruch in Zeile $LINENO (Exit $rc)." >&2; exit $rc' ERR

[[ $EUID -eq 0 ]] || die "Bitte als root ausführen."

for cmd in docker ip curl awk cut head; do
  command -v "$cmd" >/dev/null 2>&1 || die "$cmd fehlt."
done

docker compose version >/dev/null 2>&1 || die "Docker Compose Plugin fehlt."

log "Prüfe NetBird-Container..."
docker inspect "$NETBIRD_CONTAINER" >/dev/null 2>&1 || die "Container $NETBIRD_CONTAINER nicht gefunden."

COMPOSE_SERVICE="$(docker inspect "$NETBIRD_CONTAINER" --format '{{index .Config.Labels "com.docker.compose.service"}}')"
COMPOSE_WORKDIR="$(docker inspect "$NETBIRD_CONTAINER" --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}')"
COMPOSE_FILES_RAW="$(docker inspect "$NETBIRD_CONTAINER" --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}')"

[[ -n "$COMPOSE_SERVICE" && "$COMPOSE_SERVICE" != "<no value>" ]] || die "Compose-Service nicht ermittelbar."
[[ -n "$COMPOSE_WORKDIR" && "$COMPOSE_WORKDIR" != "<no value>" && -d "$COMPOSE_WORKDIR" ]] || die "Compose-Verzeichnis nicht ermittelbar."
[[ -n "$COMPOSE_FILES_RAW" && "$COMPOSE_FILES_RAW" != "<no value>" ]] || die "Compose-Dateien nicht ermittelbar."

log "Compose-Service: $COMPOSE_SERVICE"
log "Compose-Verzeichnis: $COMPOSE_WORKDIR"

detect_bind_ip() {
  local ip_addr=""

  if ip link show "$NETBIRD_INTERFACE" >/dev/null 2>&1; then
    ip_addr="$(
      ip -4 -o addr show dev "$NETBIRD_INTERFACE" 2>/dev/null |
        awk '{print $4}' |
        cut -d/ -f1 |
        head -1 || true
    )"
    if [[ -n "$ip_addr" ]]; then
      echo "$ip_addr"
      return 0
    fi
  fi

  local iface
  while IFS= read -r iface; do
    [[ -n "$iface" ]] || continue
    ip_addr="$(
      ip -4 -o addr show dev "$iface" 2>/dev/null |
        awk '{print $4}' |
        cut -d/ -f1 |
        head -1 || true
    )"
    if [[ -n "$ip_addr" ]]; then
      NETBIRD_INTERFACE="$iface"
      echo "$ip_addr"
      return 0
    fi
  done < <(
    ip -o link show |
      awk -F': ' '{print $2}' |
      sed 's/@.*//' |
      grep -E '^(wt[0-9]+|netbird[0-9]*|nb[0-9]*)$' || true
  )

  return 1
}

if [[ -z "$BIND_IP" ]]; then
  log "Ermittle NetBird-IP auf Interface $NETBIRD_INTERFACE ..."
  BIND_IP="$(detect_bind_ip || true)"
fi

if [[ -z "$BIND_IP" ]]; then
  echo >&2
  warn "Keine NetBird-IPv4 automatisch gefunden."
  warn "Vorhandene IPv4-Adressen:"
  ip -4 -o addr show | sed 's/^/    /' >&2 || true
  echo >&2
  die "Bitte NETBIRD_METRICS_BIND_IP explizit setzen, z. B. NETBIRD_METRICS_BIND_IP=100.x.x.x"
fi

if ! ip -4 -o addr show | awk '{print $4}' | cut -d/ -f1 | grep -Fxq "$BIND_IP"; then
  die "Die angegebene Bind-IP $BIND_IP ist auf diesem Host nicht vorhanden."
fi

log "Metrics werden nur an $BIND_IP:$METRICS_PORT gebunden."

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

status="$(docker inspect "$NETBIRD_CONTAINER" --format '{{.State.Status}}' 2>/dev/null || true)"
[[ "$status" == "running" ]] || die "NetBird-Server läuft nach Recreate nicht. Status: $status"

log "Prüfe Docker-Portbindung..."
docker port "$NETBIRD_CONTAINER" "$METRICS_PORT/tcp" 2>/dev/null || true

log "Prüfe Metrics-Endpunkt..."
if ! metrics_sample="$(curl -fsS --max-time 5 "http://$BIND_IP:$METRICS_PORT/metrics" | head -n 5)"; then
  echo >&2
  warn "Metrics-Endpunkt ist noch nicht erreichbar."
  warn "Container-Logs:"
  docker logs --tail=80 "$NETBIRD_CONTAINER" >&2 || true
  die "Abruf von http://$BIND_IP:$METRICS_PORT/metrics fehlgeschlagen."
fi

printf '%s\n' "$metrics_sample"

echo
echo "NetBird Metrics erreichbar unter:"
echo "  http://$BIND_IP:$METRICS_PORT/metrics"
echo
echo "Override:"
echo "  $OVERRIDE_FILE"
echo
echo "Port $METRICS_PORT nicht über Traefik oder die öffentliche Firewall freigeben."
