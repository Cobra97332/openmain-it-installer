#!/usr/bin/env bash
set -Eeuo pipefail

NETBIRD_INTERFACE="${NETBIRD_INTERFACE:-wt0}"
METRICS_PORT="${NETBIRD_CLIENT_METRICS_PORT:-9191}"
BIND_IP="${NETBIRD_CLIENT_METRICS_BIND_IP:-}"

die() { echo "[FEHLER] $*" >&2; exit 1; }
log() { echo "[+] $*"; }

[[ $EUID -eq 0 ]] || die "Bitte als root ausführen."
command -v netbird >/dev/null 2>&1 || die "netbird CLI fehlt."
command -v curl >/dev/null 2>&1 || die "curl fehlt."

if [[ -z "$BIND_IP" ]]; then
  BIND_IP="$(ip -4 -o addr show dev "$NETBIRD_INTERFACE" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)"
fi
[[ -n "$BIND_IP" ]] || die "Keine IPv4 auf $NETBIRD_INTERFACE gefunden. NETBIRD_CLIENT_METRICS_BIND_IP explizit setzen."

log "Aktiviere lokale NetBird-Metriken auf $BIND_IP:$METRICS_PORT ..."
netbird up   --enable-local-metrics   --local-metrics-address "$BIND_IP:$METRICS_PORT"

sleep 2
metrics_output="$(curl -fsS --max-time 5 "http://$BIND_IP:$METRICS_PORT/metrics")" \
  || die "Metrics-Endpunkt ist nicht erreichbar."

grep -q '^netbird_' <<<"$metrics_output" \
  || die "Metrics-Endpunkt liefert keine NetBird-Metriken."

echo
echo "Client Metrics aktiv:"
echo "  http://$BIND_IP:$METRICS_PORT/metrics"
echo
echo "Der Endpoint ist absichtlich nur an die NetBird-IP gebunden."
echo "Den Zugriff zusätzlich per NetBird-Policy auf den Prometheus-Host beschränken."
