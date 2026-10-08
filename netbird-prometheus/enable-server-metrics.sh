#!/usr/bin/env bash
set -Eeuo pipefail

NETBIRD_CONTAINER="${NETBIRD_CONTAINER:-netbird-server}"
METRICS_PORT="${NETBIRD_METRICS_PORT:-9090}"
PREFERRED_INTERFACE="${NETBIRD_INTERFACE:-}"
BIND_IP="${NETBIRD_METRICS_BIND_IP:-}"

die() { echo "[FEHLER] $*" >&2; exit 1; }
log() { echo "[+] $*"; }
warn() { echo "[!] $*" >&2; }

trap 'rc=$?; echo "[FEHLER] Unerwarteter Abbruch in Zeile $LINENO (Exit $rc)." >&2; exit $rc' ERR

[[ $EUID -eq 0 ]] || die "Bitte als root ausführen."

for cmd in docker ip curl awk cut head grep; do
  command -v "$cmd" >/dev/null 2>&1 || die "$cmd fehlt."
done
docker compose version >/dev/null 2>&1 || die "Docker Compose Plugin fehlt."

is_private_ipv4() {
  local ip_addr="$1"
  [[ "$ip_addr" =~ ^10\. ]] && return 0
  [[ "$ip_addr" =~ ^192\.168\. ]] && return 0
  if [[ "$ip_addr" =~ ^172\.([0-9]+)\. ]]; then
    local second="${BASH_REMATCH[1]}"
    (( second >= 16 && second <= 31 )) && return 0
  fi
  return 1
}

ipv4_on_interface() {
  local iface="$1"
  ip -4 -o addr show dev "$iface" scope global 2>/dev/null |
    awk '{print $4}' |
    cut -d/ -f1 |
    head -1 || true
}

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

if [[ -z "$BIND_IP" && -n "$PREFERRED_INTERFACE" ]]; then
  log "Prüfe gewünschtes Interface $PREFERRED_INTERFACE ..."
  if ip link show "$PREFERRED_INTERFACE" >/dev/null 2>&1; then
    candidate="$(ipv4_on_interface "$PREFERRED_INTERFACE")"
    if [[ -n "$candidate" ]]; then
      BIND_IP="$candidate"
      log "IPv4 auf $PREFERRED_INTERFACE gefunden: $BIND_IP"
    fi
  fi
fi

# Falls auf dem NetBird-Server zusätzlich ein NetBird-Client läuft, dessen Adresse bevorzugen.
if [[ -z "$BIND_IP" ]]; then
  for iface in wt0 netbird0; do
    if ip link show "$iface" >/dev/null 2>&1; then
      candidate="$(ipv4_on_interface "$iface")"
      if [[ -n "$candidate" ]]; then
        BIND_IP="$candidate"
        log "NetBird-Client-Interface $iface erkannt: $BIND_IP"
        break
      fi
    fi
  done
fi

# Ein NetBird-Server benötigt selbst keinen NetBird-Client. Dann die private
# IPv4 des Default-Route-Interfaces verwenden, aber niemals automatisch eine
# öffentliche Adresse oder Docker-Bridge veröffentlichen.
if [[ -z "$BIND_IP" ]]; then
  default_iface="$(ip -4 route show default 2>/dev/null | awk '{print $5; exit}' || true)"
  if [[ -n "$default_iface" ]]; then
    candidate="$(ipv4_on_interface "$default_iface")"
    if [[ -n "$candidate" ]] && is_private_ipv4 "$candidate"; then
      BIND_IP="$candidate"
      log "Kein NetBird-Client auf dem Server. Private Management-IP erkannt: $BIND_IP ($default_iface)"
    fi
  fi
fi

if [[ -z "$BIND_IP" ]]; then
  while read -r iface candidate; do
    [[ -n "$iface" && -n "$candidate" ]] || continue
    case "$iface" in
      lo|docker*|br-*|veth*) continue ;;
    esac
    if is_private_ipv4 "$candidate"; then
      BIND_IP="$candidate"
      log "Private IPv4 erkannt: $BIND_IP ($iface)"
      break
    fi
  done < <(
    ip -4 -o addr show scope global |
      awk '{iface=$2; sub(/@.*/, "", iface); split($4,a,"/"); print iface, a[1]}'
  )
fi

if [[ -z "$BIND_IP" ]]; then
  echo >&2
  warn "Keine sichere private IPv4 automatisch gefunden."
  warn "Vorhandene IPv4-Adressen:"
  ip -4 -o addr show | sed 's/^/    /' >&2 || true
  echo >&2
  die "NETBIRD_METRICS_BIND_IP explizit setzen. Keine öffentliche IP verwenden."
fi

if ! ip -4 -o addr show | awk '{print $4}' | cut -d/ -f1 | grep -Fxq "$BIND_IP"; then
  die "Die angegebene Bind-IP $BIND_IP ist auf diesem Host nicht vorhanden."
fi

if ! is_private_ipv4 "$BIND_IP"; then
  warn "Die Bind-IP $BIND_IP ist keine RFC1918-Adresse."
  die "Aus Sicherheitsgründen wird der Metrics-Port nicht automatisch an eine öffentliche IPv4 gebunden."
fi

log "Metrics werden ausschließlich an $BIND_IP:$METRICS_PORT gebunden."

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

log "Warte auf Metrics-Endpunkt..."
metrics_sample=""
for attempt in $(seq 1 30); do
  if metrics_sample="$(curl -fsS --max-time 3 "http://$BIND_IP:$METRICS_PORT/metrics" 2>/dev/null | head -n 5)"; then
    [[ -n "$metrics_sample" ]] && break
  fi

  if (( attempt == 30 )); then
    echo >&2
    warn "Metrics-Endpunkt ist nach 60 Sekunden noch nicht erreichbar."
    warn "Container-Logs:"
    docker logs --tail=80 "$NETBIRD_CONTAINER" >&2 || true
    die "Abruf von http://$BIND_IP:$METRICS_PORT/metrics fehlgeschlagen."
  fi

  sleep 2
done

log "Metrics-Endpunkt ist erreichbar."
printf '%s\n' "$metrics_sample"

echo
echo "NetBird Metrics erreichbar unter:"
echo "  http://$BIND_IP:$METRICS_PORT/metrics"
echo
echo "Override:"
echo "  $OVERRIDE_FILE"
echo
echo "Hinweis:"
echo "  Dieser NetBird-Server benötigt keinen lokalen NetBird-Client."
echo "  Prometheus greift auf die private Management-IP zu."
echo "  Port $METRICS_PORT nicht über Traefik oder die öffentliche Firewall freigeben."
