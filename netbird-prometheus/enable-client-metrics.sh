#!/usr/bin/env bash
set -Eeuo pipefail

METRICS_PORT="${NETBIRD_CLIENT_METRICS_PORT:-9191}"
BIND_IP="${NETBIRD_CLIENT_METRICS_BIND_IP:-}"
ALLOW_NON_NETBIRD_BIND="${NETBIRD_ALLOW_NON_NETBIRD_BIND:-NO}"
WAIT_SECONDS="${NETBIRD_CLIENT_METRICS_WAIT_SECONDS:-60}"

die() { echo "[FEHLER] $*" >&2; exit 1; }
log() { echo "[+] $*"; }
warn() { echo "[!] $*" >&2; }

trap 'rc=$?; echo "[FEHLER] Unerwarteter Abbruch in Zeile $LINENO (Exit $rc)." >&2; exit $rc' ERR

[[ $EUID -eq 0 ]] || die "Bitte als root ausführen."

for cmd in netbird curl ip awk grep hostname; do
  command -v "$cmd" >/dev/null 2>&1 || die "$cmd fehlt."
done

[[ "$METRICS_PORT" =~ ^[0-9]+$ ]] || die "Ungültiger Port: $METRICS_PORT"
(( METRICS_PORT >= 1 && METRICS_PORT <= 65535 )) || die "Ungültiger Port: $METRICS_PORT"

NETBIRD_IP="$(
  netbird status --ipv4 2>/dev/null |
    awk 'NF {sub(/\/.*/, "", $1); print $1; exit}' || true
)"

[[ -n "$NETBIRD_IP" ]] || die "Keine NetBird-IPv4 über 'netbird status --ipv4' ermittelbar."

if [[ -z "$BIND_IP" ]]; then
  BIND_IP="$NETBIRD_IP"
fi

if [[ "$BIND_IP" != "$NETBIRD_IP" && "$ALLOW_NON_NETBIRD_BIND" != "YES" ]]; then
  die "Bind-IP $BIND_IP ist nicht die NetBird-IP $NETBIRD_IP. Für eine abweichende Bind-IP explizit NETBIRD_ALLOW_NON_NETBIRD_BIND=YES setzen."
fi

if ! ip -4 -o addr show | awk '{split($4,a,"/"); print a[1]}' | grep -Fxq "$BIND_IP"; then
  die "Bind-IP $BIND_IP ist aktuell nicht auf diesem Host vorhanden."
fi

ENDPOINT="http://$BIND_IP:$METRICS_PORT/metrics"

check_metrics() {
  local output=""
  output="$(curl -fsS --max-time 5 "$ENDPOINT" 2>/dev/null || true)"
  [[ -n "$output" ]] || return 1
  grep -q '^netbird_' <<<"$output"
}

if check_metrics; then
  log "NetBird Client Metrics sind bereits aktiv."
  echo
  echo "Endpoint:"
  echo "  $ENDPOINT"
  echo
  echo "Prometheus Target:"
  echo "  $BIND_IP:$METRICS_PORT"
  exit 0
fi

warn "Der Metrics-Endpunkt ist noch nicht aktiv."
warn "NetBird ignoriert 'netbird up' Konfigurationsflags, solange der Client bereits verbunden ist."
warn "Für die Aktivierung ist deshalb ein kurzer 'netbird down' / 'netbird up' Zyklus nötig."

SSH_SERVER_IP=""
if [[ -n "${SSH_CONNECTION:-}" ]]; then
  SSH_SERVER_IP="$(awk '{print $3}' <<<"$SSH_CONNECTION")"
fi

apply_metrics() {
  local max_attempts attempt

  log "Trenne NetBird kurz..."
  netbird down

  sleep 2

  log "Aktiviere Client Metrics auf $BIND_IP:$METRICS_PORT ..."
  netbird up     --enable-local-metrics     --local-metrics-address "$BIND_IP:$METRICS_PORT"

  max_attempts=$(( WAIT_SECONDS / 2 ))
  (( max_attempts < 1 )) && max_attempts=1

  for (( attempt=1; attempt<=max_attempts; attempt++ )); do
    if check_metrics; then
      log "Metrics-Endpunkt ist erreichbar."
      return 0
    fi
    sleep 2
  done

  return 1
}

if [[ "$SSH_SERVER_IP" == "$BIND_IP" ]]; then
  command -v systemd-run >/dev/null 2>&1     || die "Aktuelle SSH-Verbindung läuft über NetBird ($BIND_IP), aber systemd-run fehlt. Bitte über lokale/PVE-Konsole ausführen."

  UNIT="openmain-netbird-client-metrics-$METRICS_PORT"
  HELPER="/run/$UNIT.sh"

  cat > "$HELPER" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
sleep 2
netbird down
sleep 2
netbird up --enable-local-metrics --local-metrics-address "$BIND_IP:$METRICS_PORT"

for attempt in \$(seq 1 $(( WAIT_SECONDS / 2 ))); do
  output="\$(curl -fsS --max-time 5 "http://$BIND_IP:$METRICS_PORT/metrics" 2>/dev/null || true)"
  if grep -q '^netbird_' <<<"\$output"; then
    echo "SUCCESS: NetBird Client Metrics aktiv auf $BIND_IP:$METRICS_PORT"
    exit 0
  fi
  sleep 2
done

echo "FAILED: Metrics-Endpunkt nicht erreichbar" >&2
exit 1
EOF
  chmod 0700 "$HELPER"

  warn "Die aktuelle SSH-Sitzung läuft über NetBird."
  warn "Die Aktivierung wird deshalb als systemd-Job gestartet, damit sie den kurzen NetBird-Abbruch überlebt."
  warn "Die SSH-Verbindung kann dabei kurz getrennt werden."

  systemd-run     --unit="$UNIT"     --collect     --property=Type=oneshot     "$HELPER"

  echo
  echo "Job gestartet:"
  echo "  systemctl status $UNIT"
  echo
  echo "Nach Wiederverbindung erneut ausführen; das Skript prüft dann idempotent den Endpoint."
  exit 0
fi

if ! apply_metrics; then
  echo >&2
  warn "Metrics-Endpunkt wurde innerhalb von $WAIT_SECONDS Sekunden nicht erreichbar."
  warn "NetBird Status:"
  netbird status --detail >&2 || true
  warn "NetBird Service-Logs:"
  journalctl -u netbird --since '-5 min' --no-pager | tail -100 >&2 || true
  die "Aktivierung fehlgeschlagen."
fi

echo
echo "NetBird Client Metrics aktiv:"
echo "  $ENDPOINT"
echo
echo "Prometheus Target:"
echo "  $BIND_IP:$METRICS_PORT"
echo
echo "Hostname:"
echo "  $(hostname -s)"
echo
echo "Sicherheit:"
echo "  Der Endpoint hat keine Authentifizierung."
echo "  Zugriff nur per NetBird-Policy vom Prometheus-Host auf TCP/$METRICS_PORT erlauben."
