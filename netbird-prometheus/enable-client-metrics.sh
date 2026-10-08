#!/usr/bin/env bash
set -Eeuo pipefail

METRICS_PORT="${NETBIRD_CLIENT_METRICS_PORT:-9191}"
WAIT_SECONDS="${NETBIRD_CLIENT_METRICS_WAIT_SECONDS:-60}"
UNIT_BASE="${NETBIRD_CLIENT_METRICS_UNIT_BASE:-openmain-netbird-metrics-proxy}"

LOOPBACK_IP="127.0.0.1"
LOOPBACK_ENDPOINT="http://${LOOPBACK_IP}:${METRICS_PORT}/metrics"
SOCKET_UNIT="/etc/systemd/system/${UNIT_BASE}.socket"
SERVICE_UNIT="/etc/systemd/system/${UNIT_BASE}.service"

die() { echo "[FEHLER] $*" >&2; exit 1; }
log() { echo "[+] $*"; }
warn() { echo "[!] $*" >&2; }

trap 'rc=$?; echo "[FEHLER] Unerwarteter Abbruch in Zeile $LINENO (Exit $rc)." >&2; exit $rc' ERR

[[ $EUID -eq 0 ]] || die "Bitte als root ausführen."

for cmd in netbird curl ip awk grep hostname systemctl; do
  command -v "$cmd" >/dev/null 2>&1 || die "$cmd fehlt."
done

[[ "$METRICS_PORT" =~ ^[0-9]+$ ]] || die "Ungültiger Port: $METRICS_PORT"
(( METRICS_PORT >= 1 && METRICS_PORT <= 65535 )) || die "Ungültiger Port: $METRICS_PORT"

find_socket_proxyd() {
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
[[ -n "$NETBIRD_IP" ]] || die "Keine NetBird-IPv4 über 'netbird status --ipv4' ermittelbar."

REMOTE_ENDPOINT="http://${NETBIRD_IP}:${METRICS_PORT}/metrics"

check_endpoint() {
  local endpoint="$1"
  local output=""
  output="$(curl -fsS --max-time 5 "$endpoint" 2>/dev/null || true)"
  [[ -n "$output" ]] || return 1
  grep -q '^netbird_' <<<"$output"
}

write_proxy_units() {
  cat > "$SOCKET_UNIT" <<EOF
[Unit]
Description=OpenMain NetBird client metrics socket on $NETBIRD_IP:$METRICS_PORT

[Socket]
ListenStream=$NETBIRD_IP:$METRICS_PORT
FreeBind=yes
NoDelay=yes

[Install]
WantedBy=sockets.target
EOF

  cat > "$SERVICE_UNIT" <<EOF
[Unit]
Description=OpenMain NetBird client metrics proxy to localhost
Requires=${UNIT_BASE}.socket
After=network.target

[Service]
ExecStart=$SOCKET_PROXYD $LOOPBACK_IP:$METRICS_PORT
NoNewPrivileges=yes
PrivateDevices=yes
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=strict
EOF

  chmod 0644 "$SOCKET_UNIT" "$SERVICE_UNIT"
  systemctl daemon-reload
}

start_proxy() {
  systemctl enable --now "${UNIT_BASE}.socket" >/dev/null
}

wait_for_endpoint() {
  local endpoint="$1"
  local max_attempts attempt

  max_attempts=$(( WAIT_SECONDS / 2 ))
  (( max_attempts < 1 )) && max_attempts=1

  for (( attempt=1; attempt<=max_attempts; attempt++ )); do
    if check_endpoint "$endpoint"; then
      return 0
    fi
    sleep 2
  done
  return 1
}

apply_local_metrics() {
  log "Trenne NetBird kurz, damit die geänderte Client-Konfiguration übernommen wird..."
  netbird down
  sleep 2

  log "Aktiviere NetBird Client Metrics ausschließlich auf $LOOPBACK_IP:$METRICS_PORT ..."
  netbird up     --enable-local-metrics     --local-metrics-address "$LOOPBACK_IP:$METRICS_PORT"

  log "Warte auf lokalen Metrics-Endpunkt..."
  wait_for_endpoint "$LOOPBACK_ENDPOINT"
}

finish_and_verify() {
  log "Starte NetBird-only Socket-Proxy auf $NETBIRD_IP:$METRICS_PORT ..."
  start_proxy

  log "Prüfe lokalen Metrics-Endpunkt..."
  check_endpoint "$LOOPBACK_ENDPOINT"     || die "Lokaler Metrics-Endpunkt $LOOPBACK_ENDPOINT liefert keine NetBird-Metriken."

  log "Prüfe NetBird-Endpunkt..."
  wait_for_endpoint "$REMOTE_ENDPOINT"     || {
      systemctl status "${UNIT_BASE}.socket" "${UNIT_BASE}.service" --no-pager >&2 || true
      die "NetBird-Endpunkt $REMOTE_ENDPOINT ist nicht erreichbar."
    }

  echo
  echo "NetBird Client Metrics aktiv:"
  echo "  lokal:    $LOOPBACK_ENDPOINT"
  echo "  NetBird:  $REMOTE_ENDPOINT"
  echo
  echo "Prometheus Target:"
  echo "  $NETBIRD_IP:$METRICS_PORT"
  echo
  echo "Hostname:"
  echo "  $(hostname -s)"
  echo
  echo "Sicherheit:"
  echo "  - NetBird selbst lauscht nur auf Loopback."
  echo "  - systemd-socket-proxyd veröffentlicht TCP/$METRICS_PORT nur auf der NetBird-IP."
  echo "  - Der Endpoint hat keine Authentifizierung."
  echo "  - Zugriff zusätzlich per NetBird-Policy nur vom Prometheus-Peer erlauben."
}

log "NetBird IPv4: $NETBIRD_IP"
log "Schreibe Socket-Proxy Units..."
write_proxy_units

if check_endpoint "$LOOPBACK_ENDPOINT"; then
  log "Lokale NetBird Client Metrics sind bereits aktiv."
  finish_and_verify
  exit 0
fi

warn "Lokale Client Metrics sind noch nicht aktiv."
warn "NetBird v0.80.0 beendet 'netbird up' bei einer bestehenden Verbindung mit 'Already connected'."
warn "Deshalb ist einmalig ein kurzer 'netbird down' / 'netbird up' Zyklus erforderlich."

SSH_SERVER_IP=""
if [[ -n "${SSH_CONNECTION:-}" ]]; then
  SSH_SERVER_IP="$(awk '{print $3}' <<<"$SSH_CONNECTION")"
fi

if [[ "$SSH_SERVER_IP" == "$NETBIRD_IP" ]]; then
  command -v systemd-run >/dev/null 2>&1     || die "Aktuelle SSH-Verbindung läuft über NetBird, aber systemd-run fehlt. Bitte über PVE/LAN-Konsole ausführen."

  JOB_UNIT="openmain-enable-netbird-client-metrics-$METRICS_PORT"
  HELPER="/run/$JOB_UNIT.sh"

  cat > "$HELPER" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail

netbird down
sleep 2
netbird up --enable-local-metrics --local-metrics-address "$LOOPBACK_IP:$METRICS_PORT"

max_attempts=$(( WAIT_SECONDS / 2 ))
(( max_attempts < 1 )) && max_attempts=1

for (( attempt=1; attempt<=max_attempts; attempt++ )); do
  output="\$(curl -fsS --max-time 5 "$LOOPBACK_ENDPOINT" 2>/dev/null || true)"
  if grep -q '^netbird_' <<<"\$output"; then
    systemctl enable --now "${UNIT_BASE}.socket"
    echo "SUCCESS: NetBird Client Metrics aktiv."
    exit 0
  fi
  sleep 2
done

echo "FAILED: Lokaler Metrics-Endpunkt nicht erreichbar." >&2
journalctl -u netbird --since '-5 min' --no-pager | tail -100 >&2 || true
exit 1
EOF

  chmod 0700 "$HELPER"

  warn "Die aktuelle SSH-Sitzung läuft über NetBird."
  warn "Die Sitzung kann beim Umschalten kurz getrennt werden."
  warn "Der Vorgang wird deshalb unabhängig als systemd-Job weitergeführt."

  systemd-run     --unit="$JOB_UNIT"     --collect     --property=Type=oneshot     "$HELPER"

  echo
  echo "Job gestartet:"
  echo "  systemctl status $JOB_UNIT"
  echo
  echo "Nach Wiederverbindung dasselbe Skript erneut ausführen."
  exit 0
fi

if ! apply_local_metrics; then
  echo >&2
  warn "Lokaler Metrics-Endpunkt wurde innerhalb von $WAIT_SECONDS Sekunden nicht erreichbar."
  warn "NetBird Status:"
  netbird status --detail >&2 || true
  warn "NetBird Service-Logs:"
  journalctl -u netbird --since '-5 min' --no-pager | tail -100 >&2 || true
  die "Aktivierung fehlgeschlagen."
fi

finish_and_verify
