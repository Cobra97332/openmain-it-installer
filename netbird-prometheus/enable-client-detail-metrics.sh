#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${OPENMAIN_GITHUB_RAW:-https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/netbird-prometheus}"
DETAIL_PORT="${NETBIRD_CLIENT_DETAIL_PORT:-9192}"
WAIT_SECONDS="${NETBIRD_CLIENT_DETAIL_WAIT_SECONDS:-60}"
UNIT_BASE="${NETBIRD_CLIENT_DETAIL_UNIT_BASE:-openmain-netbird-detail-proxy}"
EXPORTER="${NETBIRD_CLIENT_DETAIL_EXPORTER:-/usr/local/lib/openmain-netbird-status-exporter.py}"
EXPORTER_SERVICE="/etc/systemd/system/openmain-netbird-status-exporter.service"
SOCKET_UNIT="/etc/systemd/system/${UNIT_BASE}.socket"
PROXY_SERVICE_UNIT="/etc/systemd/system/${UNIT_BASE}.service"
LOOPBACK_IP="127.0.0.1"

die(){ echo "[FEHLER] $*" >&2; exit 1; }
log(){ echo "[+] $*"; }

[[ $EUID -eq 0 ]] || die "Bitte als root ausführen."

for cmd in netbird curl awk grep systemctl; do
  command -v "$cmd" >/dev/null 2>&1 || die "$cmd fehlt."
done

[[ "$DETAIL_PORT" =~ ^[0-9]+$ ]] || die "Ungültiger Detail-Port: $DETAIL_PORT"
(( DETAIL_PORT >= 1 && DETAIL_PORT <= 65535 )) || die "Ungültiger Detail-Port: $DETAIL_PORT"

if ! command -v python3 >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    log "Installiere Python 3..."
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3
  else
    die "python3 fehlt und kann nicht automatisch installiert werden."
  fi
fi

find_socket_proxyd(){
  local candidate
  for candidate in     /usr/lib/systemd/systemd-socket-proxyd     /lib/systemd/systemd-socket-proxyd     /usr/libexec/systemd/systemd-socket-proxyd; do
    [[ -x "$candidate" ]] && { echo "$candidate"; return 0; }
  done
  return 1
}

SOCKET_PROXYD="$(find_socket_proxyd || true)"
[[ -n "$SOCKET_PROXYD" ]] || die "systemd-socket-proxyd wurde nicht gefunden."

NETBIRD_IP="$(
  netbird status --ipv4 2>/dev/null |
    awk 'NF {sub(/\/.*/, "", $1); print $1; exit}' || true
)"
[[ -n "$NETBIRD_IP" ]] || die "Keine NetBird-IPv4 ermittelbar."

LOCAL_ENDPOINT="http://$LOOPBACK_IP:$DETAIL_PORT/metrics"
REMOTE_ENDPOINT="http://$NETBIRD_IP:$DETAIL_PORT/metrics"

check_endpoint(){
  local endpoint="$1" output
  output="$(curl -fsS --max-time 5 "$endpoint" 2>/dev/null || true)"
  grep -q '^openmain_netbird_status_exporter_up 1$' <<<"$output"
}

wait_endpoint(){
  local endpoint="$1" max_attempts attempt
  max_attempts=$(( WAIT_SECONDS / 2 ))
  (( max_attempts < 1 )) && max_attempts=1

  for ((attempt=1; attempt<=max_attempts; attempt++)); do
    check_endpoint "$endpoint" && return 0
    sleep 2
  done
  return 1
}

log "Installiere Status-Exporter..."
install -d -m 0755 "$(dirname "$EXPORTER")"
curl -fsSL "$BASE_URL/netbird-status-exporter.py" -o "$EXPORTER"
chmod 0755 "$EXPORTER"

cat > "$EXPORTER_SERVICE" <<EOF
[Unit]
Description=OpenMain detailed NetBird peer status exporter
Requires=netbird.service
After=netbird.service network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $EXPORTER --listen $LOOPBACK_IP --port $DETAIL_PORT
Restart=always
RestartSec=5
User=root
Group=root
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=strict

[Install]
WantedBy=multi-user.target
EOF

cat > "$SOCKET_UNIT" <<EOF
[Unit]
Description=OpenMain NetBird detailed status socket on $NETBIRD_IP:$DETAIL_PORT

[Socket]
ListenStream=$NETBIRD_IP:$DETAIL_PORT
FreeBind=yes
NoDelay=yes

[Install]
WantedBy=sockets.target
EOF

cat > "$PROXY_SERVICE_UNIT" <<EOF
[Unit]
Description=OpenMain NetBird detailed status proxy to localhost
Requires=${UNIT_BASE}.socket
After=network.target

[Service]
ExecStart=$SOCKET_PROXYD $LOOPBACK_IP:$DETAIL_PORT
NoNewPrivileges=yes
PrivateDevices=yes
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=strict
EOF

chmod 0644 "$EXPORTER_SERVICE" "$SOCKET_UNIT" "$PROXY_SERVICE_UNIT"
systemctl daemon-reload
systemctl enable --now openmain-netbird-status-exporter.service >/dev/null
systemctl enable --now "${UNIT_BASE}.socket" >/dev/null

log "Prüfe lokalen Detail-Endpunkt..."
wait_endpoint "$LOCAL_ENDPOINT" || {
  systemctl status openmain-netbird-status-exporter.service --no-pager >&2 || true
  journalctl -u openmain-netbird-status-exporter.service --since '-5 min' --no-pager >&2 || true
  die "Lokaler Detail-Endpunkt $LOCAL_ENDPOINT ist nicht erreichbar."
}

log "Prüfe NetBird-Detail-Endpunkt..."
wait_endpoint "$REMOTE_ENDPOINT" || {
  systemctl status "${UNIT_BASE}.socket" "${UNIT_BASE}.service" --no-pager >&2 || true
  die "NetBird-Detail-Endpunkt $REMOTE_ENDPOINT ist nicht erreichbar."
}

echo
echo "NetBird Relay-/Peer-Details aktiv:"
echo "  lokal:   $LOCAL_ENDPOINT"
echo "  NetBird: $REMOTE_ENDPOINT"
echo
echo "Prometheus Detail Target:"
echo "  $NETBIRD_IP:$DETAIL_PORT"
echo
echo "Sicherheit:"
echo "  - Status-Exporter lauscht nur auf Loopback."
echo "  - systemd-socket-proxyd veröffentlicht TCP/$DETAIL_PORT nur auf der NetBird-IP."
echo "  - Endpoint ohne Authentifizierung; NetBird-Policy nur vom Prometheus-Peer erlauben."
