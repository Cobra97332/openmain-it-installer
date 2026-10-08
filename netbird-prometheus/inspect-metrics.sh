#!/usr/bin/env bash
set -Eeuo pipefail

PROM_CONTAINER="${PROMETHEUS_CONTAINER:-openmain-netbird-prometheus}"
PROM_URL="${PROMETHEUS_URL:-http://127.0.0.1:9090}"
OUT="${1:-/tmp/netbird-prometheus-inventory.txt}"

die() { echo "[FEHLER] $*" >&2; exit 1; }
section() { printf '\n===== %s =====\n' "$1"; }

command -v docker >/dev/null 2>&1 || die "docker fehlt."
docker inspect "$PROM_CONTAINER" >/dev/null 2>&1 || die "Container $PROM_CONTAINER nicht gefunden."
[[ "$(docker inspect "$PROM_CONTAINER" --format '{{.State.Running}}')" == "true" ]] || die "Container $PROM_CONTAINER läuft nicht."

query() {
  local expr="$1"
  docker exec "$PROM_CONTAINER"     promtool query instant "$PROM_URL" "$expr" 2>&1 || true
}

{
  echo "OpenMain NetBird Prometheus Inventory"
  echo "Generated: $(date -Is)"
  echo "Container: $PROM_CONTAINER"
  echo "Prometheus: $PROM_URL"

  section "TARGET"
  query 'up{job="netbird-server"}'

  section "ALL MANAGEMENT METRIC NAMES"
  query 'sort(count by (__name__) ({__name__=~"management_.*"}))'

  section "UPDATECHANNEL METRICS"
  query 'sort(count by (__name__) ({__name__=~"management_updatechannel_.*|management_grpc_updatechannel_.*"}))'

  section "NETWORK MAP METRICS"
  query 'sort(count by (__name__) ({__name__=~"management_account_.*network.*|management_account_update_account_peers_.*"}))'

  section "STORE METRICS"
  query 'sort(count by (__name__) ({__name__=~"management_store_.*"}))'

  section "IDP METRICS"
  query 'sort(count by (__name__) ({__name__=~"management_idp_.*"}))'

  section "HTTP METRICS"
  query 'sort(count by (__name__) ({__name__=~"management_http_.*"}))'

  section "HTTP ACTIVITY LAST HOUR"
  query 'sum by (method, type, exported_endpoint) (increase(management_http_request_counter_total[1h]))'

  section "GRPC METRICS"
  query 'sort(count by (__name__) ({__name__=~"management_grpc_.*"}))'

  section "SIGNAL METRICS"
  query 'sort(count by (__name__) ({__name__=~"(active_peers|registrations_total|registration_failures_total|messages_forwarded_total|message_forward_failures_total|peer_connection_duration.*|message_forward_latency.*|message_size.*)"}))'

  section "RELAY METRICS"
  query 'sort(count by (__name__) ({__name__=~"relay_.*"}))'

  section "LABEL CHECK"
  query 'count by (application, cluster, environment, job, host) (management_grpc_connected_streams_ratio)'

} | tee "$OUT"

echo
echo "Inventory gespeichert: $OUT"
echo "Diese Datei vollständig an ChatGPT senden."
