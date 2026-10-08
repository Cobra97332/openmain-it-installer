#!/usr/bin/env bash
set -Eeuo pipefail

AUTO_ENV_FILE="${OPENMAIN_NETBIRD_PROM_ENV:-/etc/openmain-netbird-prometheus.env}"
if [[ -f "$AUTO_ENV_FILE" ]]; then
  # Root-only Datei mit den Einstellungen für die API-basierte Target-Synchronisation.
  set -a
  # shellcheck disable=SC1090
  source "$AUTO_ENV_FILE"
  set +a
fi

BASE_URL="${OPENMAIN_GITHUB_RAW:-https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/netbird-prometheus}"
INSTALL_DIR="${OPENMAIN_NETBIRD_PROM_DIR:-/opt/openmain-netbird-prometheus}"
NETBIRD_REF="${NETBIRD_OBSERVABILITY_REF:-24cb7b75c2e1cce4fbc2e2b1d00a4db60f8bd122}"
PROM_IMAGE="${PROMETHEUS_IMAGE:-prom/prometheus:v3.15.0}"
PROM_SERVICE="openmain-netbird-prometheus"
PROM_CONTAINER="${PROMETHEUS_CONTAINER:-openmain-netbird-prometheus}"
PROM_DS_UID="${PROMETHEUS_DATASOURCE_UID:-netbird-prometheus}"
CLUSTER_LABEL="${NETBIRD_CLUSTER:-openmain}"
ENVIRONMENT_LABEL="${NETBIRD_ENVIRONMENT:-prod}"
NETBIRD_HOST_LABEL="${NETBIRD_HOST_LABEL:-netbird-server}"
GRAFANA_CONTAINER="${GRAFANA_CONTAINER:-}"
NETBIRD_METRICS_TARGET="${NETBIRD_METRICS_TARGET:-}"
NETBIRD_RATE_INTERVAL="${NETBIRD_RATE_INTERVAL:-2m}"

NETBIRD_MANAGEMENT_URL="${NETBIRD_MANAGEMENT_URL:-https://netbird.openmain-it.de}"
NETBIRD_API_TOKEN="${NETBIRD_API_TOKEN:-}"
NETBIRD_AUTO_DISCOVERY="${NETBIRD_AUTO_DISCOVERY:-1}"
NETBIRD_AUTO_POLICY="${NETBIRD_AUTO_POLICY:-1}"
NETBIRD_METRICS_GROUP="${NETBIRD_METRICS_GROUP:-NetBird-Metrics}"
NETBIRD_MONITORING_GROUP="${NETBIRD_MONITORING_GROUP:-Monitoring}"
NETBIRD_METRICS_POLICY_NAME="${NETBIRD_METRICS_POLICY_NAME:-OpenMain Prometheus -> NetBird Client Metrics}"
NETBIRD_PROMETHEUS_PEER_IP="${NETBIRD_PROMETHEUS_PEER_IP:-}"
NETBIRD_CLIENT_METRICS_PORT="${NETBIRD_CLIENT_METRICS_PORT:-9191}"
NETBIRD_CUSTOMER_FALLBACK="${NETBIRD_CUSTOMER_FALLBACK:-intern}"
NETBIRD_CUSTOMER_IGNORE_GROUPS="${NETBIRD_CUSTOMER_IGNORE_GROUPS:-}"

die() { echo "[FEHLER] $*" >&2; exit 1; }
log() { echo "[+] $*"; }
warn() { echo "[!] $*" >&2; }

[[ $EUID -eq 0 ]] || die "Bitte als root ausführen."

for cmd in docker curl python3; do
  command -v "$cmd" >/dev/null 2>&1 || die "$cmd fehlt."
done
docker compose version >/dev/null 2>&1 || die "Docker Compose Plugin fehlt."

if [[ -z "$GRAFANA_CONTAINER" ]]; then
  mapfile -t candidates < <(
    docker ps --format '{{.Names}}|{{.Image}}' |
      awk 'BEGIN{IGNORECASE=1} /grafana/ {split($0,a,"|"); print a[1]}'
  )
  case "${#candidates[@]}" in
    0) die "Kein laufender Grafana-Container gefunden." ;;
    1) GRAFANA_CONTAINER="${candidates[0]}" ;;
    *)
      echo "Mehrere Grafana-Container gefunden:" >&2
      printf '  %s\n' "${candidates[@]}" >&2
      die "GRAFANA_CONTAINER=<Name> setzen und erneut ausführen."
      ;;
  esac
fi

docker inspect "$GRAFANA_CONTAINER" >/dev/null 2>&1 || die "Container $GRAFANA_CONTAINER nicht gefunden."

COMPOSE_SERVICE="$(docker inspect "$GRAFANA_CONTAINER" --format '{{index .Config.Labels "com.docker.compose.service"}}')"
COMPOSE_PROJECT="$(docker inspect "$GRAFANA_CONTAINER" --format '{{index .Config.Labels "com.docker.compose.project"}}')"
COMPOSE_WORKDIR="$(docker inspect "$GRAFANA_CONTAINER" --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}')"
COMPOSE_FILES_RAW="$(docker inspect "$GRAFANA_CONTAINER" --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}')"

[[ -n "$COMPOSE_SERVICE" && "$COMPOSE_SERVICE" != "<no value>" ]] || die "Grafana wurde nicht per Docker Compose erstellt."
[[ -n "$COMPOSE_WORKDIR" && "$COMPOSE_WORKDIR" != "<no value>" && -d "$COMPOSE_WORKDIR" ]] || die "Compose-Verzeichnis konnte nicht ermittelt werden."
[[ -n "$COMPOSE_FILES_RAW" && "$COMPOSE_FILES_RAW" != "<no value>" ]] || die "Compose-Dateien konnten nicht ermittelt werden."

if [[ -z "$NETBIRD_METRICS_TARGET" ]]; then
  if [[ -t 0 ]]; then
    read -r -p "NetBird Metrics Target (NetBird-IP:9090): " NETBIRD_METRICS_TARGET
  else
    die "NETBIRD_METRICS_TARGET fehlt, z. B. NETBIRD_METRICS_TARGET=100.80.10.20:9090"
  fi
fi

NETBIRD_METRICS_TARGET="${NETBIRD_METRICS_TARGET#http://}"
NETBIRD_METRICS_TARGET="${NETBIRD_METRICS_TARGET#https://}"
NETBIRD_METRICS_TARGET="${NETBIRD_METRICS_TARGET%%/*}"
[[ "$NETBIRD_METRICS_TARGET" == *:* ]] || NETBIRD_METRICS_TARGET="${NETBIRD_METRICS_TARGET}:9090"

case "$NETBIRD_AUTO_DISCOVERY" in
  1|true|TRUE|yes|YES) NETBIRD_AUTO_DISCOVERY=1 ;;
  0|false|FALSE|no|NO) NETBIRD_AUTO_DISCOVERY=0 ;;
  *) die "NETBIRD_AUTO_DISCOVERY muss 0/1 bzw. true/false sein." ;;
esac

case "$NETBIRD_AUTO_POLICY" in
  1|true|TRUE|yes|YES) NETBIRD_AUTO_POLICY=1 ;;
  0|false|FALSE|no|NO) NETBIRD_AUTO_POLICY=0 ;;
  *) die "NETBIRD_AUTO_POLICY muss 0/1 bzw. true/false sein." ;;
esac

if [[ "$NETBIRD_AUTO_DISCOVERY" == 1 && -z "$NETBIRD_API_TOKEN" ]]; then
  if [[ -t 0 ]]; then
    read -r -s -p "NetBird API Token für automatische Client-Erkennung (leer = deaktivieren): " NETBIRD_API_TOKEN
    echo
  fi
  if [[ -z "$NETBIRD_API_TOKEN" ]]; then
    warn "Kein NetBird API Token gesetzt. Automatische Client-Erkennung wird deaktiviert."
    NETBIRD_AUTO_DISCOVERY=0
  fi
fi

if [[ "$NETBIRD_AUTO_DISCOVERY" == 1 && -z "$NETBIRD_PROMETHEUS_PEER_IP" ]] && command -v netbird >/dev/null 2>&1; then
  NETBIRD_PROMETHEUS_PEER_IP="$(
    netbird status --ipv4 2>/dev/null |
      awk 'NF {sub(/\/.*/, "", $1); print $1; exit}' || true
  )"
fi

if [[ "$NETBIRD_AUTO_DISCOVERY" == 1 && -z "$NETBIRD_PROMETHEUS_PEER_IP" ]]; then
  warn "Prometheus-NetBird-IP konnte nicht automatisch erkannt werden."
  warn "Monitoring-Gruppe/Policy werden trotzdem verwaltet; den Prometheus-Peer ggf. einmalig der Gruppe '$NETBIRD_MONITORING_GROUP' zuordnen."
fi

HOST_BASE="$COMPOSE_WORKDIR/openmain-netbird-prometheus"
HOST_CONFIG="$HOST_BASE/prometheus.yml"
HOST_TARGETS="$HOST_BASE/targets"
HOST_DASHBOARDS="$HOST_BASE/dashboards"
HOST_DATASOURCE="$HOST_BASE/prometheus-datasource.yaml"
HOST_PROVIDER="$HOST_BASE/netbird-dashboards.yaml"
OVERRIDE_FILE="$COMPOSE_WORKDIR/compose.openmain-netbird-prometheus.yaml"
PROM_RELABEL_CAPTURE='$1'

install -d -m 0755 "$INSTALL_DIR" "$HOST_BASE" "$HOST_TARGETS" "$HOST_DASHBOARDS"

log "Installiere Target-Helper..."
curl -fsSL "$BASE_URL/add-client-target.py" -o "$INSTALL_DIR/add-client-target.py"
chmod 0755 "$INSTALL_DIR/add-client-target.py"

cat > /usr/local/sbin/openmain-netbird-add-client <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
"$INSTALL_DIR/add-client-target.py" --file "$HOST_TARGETS/netbird-clients.json" "\$@"

if [[ -f "$AUTO_ENV_FILE" && -x /usr/local/sbin/openmain-netbird-sync-targets ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$AUTO_ENV_FILE"
  set +a
  /usr/local/sbin/openmain-netbird-sync-targets
fi
EOF
chmod 0755 /usr/local/sbin/openmain-netbird-add-client

log "Installiere automatische NetBird-Target-Synchronisation..."
curl -fsSL "$BASE_URL/sync-client-targets.py" -o "$INSTALL_DIR/sync-client-targets.py"
chmod 0755 "$INSTALL_DIR/sync-client-targets.py"
ln -sfn "$INSTALL_DIR/sync-client-targets.py" /usr/local/sbin/openmain-netbird-sync-targets

curl -fsSL   "$BASE_URL/systemd/openmain-netbird-target-sync.service"   -o /etc/systemd/system/openmain-netbird-target-sync.service
curl -fsSL   "$BASE_URL/systemd/openmain-netbird-target-sync.timer"   -o /etc/systemd/system/openmain-netbird-target-sync.timer
chmod 0644   /etc/systemd/system/openmain-netbird-target-sync.service   /etc/systemd/system/openmain-netbird-target-sync.timer

log "Lade offizielle NetBird-Grafana-Dashboards (Ref: $NETBIRD_REF)..."
for dashboard in management signal relay client; do
  curl -fsSL \
    "https://raw.githubusercontent.com/netbirdio/netbird/$NETBIRD_REF/infrastructure_files/observability/grafana/dashboards/$dashboard.json" \
    -o "$HOST_DASHBOARDS/$dashboard.json"
done

log "Installiere Dashboard-Normalizer..."
curl -fsSL "$BASE_URL/normalize-dashboards.py" -o "$INSTALL_DIR/normalize-dashboards.py"
chmod 0755 "$INSTALL_DIR/normalize-dashboards.py"

log "Passe NetBird-Dashboards an Grafana 13 an (Rate-Intervall: $NETBIRD_RATE_INTERVAL)..."
python3 "$INSTALL_DIR/normalize-dashboards.py" \
  "$HOST_DASHBOARDS" \
  --rate-interval "$NETBIRD_RATE_INTERVAL"


cat > "$HOST_CONFIG" <<EOF
global:
  scrape_interval: 30s
  scrape_timeout: 10s
  evaluation_interval: 30s

scrape_configs:
  - job_name: netbird-server
    static_configs:
      - targets:
          - "$NETBIRD_METRICS_TARGET"
        labels:
          cluster: "$CLUSTER_LABEL"
          environment: "$ENVIRONMENT_LABEL"
          host: "$NETBIRD_HOST_LABEL"
    metric_relabel_configs:
      # The Combined NetBird server exports Management, Signal and Relay from
      # one target. Upstream dashboards were authored for separate services
      # and expect an application label on Management/Signal metrics.
      - source_labels: [__name__]
        regex: 'management_.*'
        target_label: application
        replacement: 'management'

      - source_labels: [__name__]
        regex: 'signal_.*'
        target_label: application
        replacement: 'signal'

      # Combined NetBird prefixes Signal application metrics with "signal_".
      # The upstream Signal dashboard expects standalone metric names.
      - source_labels: [__name__]
        regex: 'signal_(.*)'
        target_label: __name__
        replacement: "$PROM_RELABEL_CAPTURE"

  - job_name: netbird-client
    file_sd_configs:
      - files:
          - /etc/prometheus/targets/netbird-clients.json
        refresh_interval: 30s
EOF

if [[ ! -f "$HOST_TARGETS/netbird-clients.json" ]]; then
  printf '[]\n' > "$HOST_TARGETS/netbird-clients.json"
fi
python3 -m json.tool "$HOST_TARGETS/netbird-clients.json" >/dev/null

if [[ "$NETBIRD_AUTO_DISCOVERY" == 1 ]]; then
  {
    printf 'NETBIRD_MANAGEMENT_URL=%q\n' "$NETBIRD_MANAGEMENT_URL"
    printf 'NETBIRD_API_TOKEN=%q\n' "$NETBIRD_API_TOKEN"
    printf 'NETBIRD_METRICS_GROUP=%q\n' "$NETBIRD_METRICS_GROUP"
    printf 'NETBIRD_MONITORING_GROUP=%q\n' "$NETBIRD_MONITORING_GROUP"
    printf 'NETBIRD_METRICS_POLICY_NAME=%q\n' "$NETBIRD_METRICS_POLICY_NAME"
    printf 'NETBIRD_PROMETHEUS_PEER_IP=%q\n' "$NETBIRD_PROMETHEUS_PEER_IP"
    printf 'NETBIRD_CLIENT_METRICS_PORT=%q\n' "$NETBIRD_CLIENT_METRICS_PORT"
    printf 'NETBIRD_CLIENT_TARGET_FILE=%q\n' "$HOST_TARGETS/netbird-clients.json"
    printf 'NETBIRD_CUSTOMER_FALLBACK=%q\n' "$NETBIRD_CUSTOMER_FALLBACK"
    printf 'NETBIRD_CUSTOMER_IGNORE_GROUPS=%q\n' "$NETBIRD_CUSTOMER_IGNORE_GROUPS"
    printf 'NETBIRD_AUTO_POLICY=%q\n' "$NETBIRD_AUTO_POLICY"
  } > "$AUTO_ENV_FILE"
  chmod 0600 "$AUTO_ENV_FILE"

  log "Synchronisiere vorhandene NetBird-Metrics-Peers..."
  /usr/local/sbin/openmain-netbird-sync-targets

  systemctl daemon-reload
  systemctl enable --now openmain-netbird-target-sync.timer >/dev/null
else
  systemctl disable --now openmain-netbird-target-sync.timer >/dev/null 2>&1 || true
  systemctl daemon-reload
fi

cat > "$HOST_DATASOURCE" <<EOF
apiVersion: 1

datasources:
  - name: NetBird Prometheus
    uid: $PROM_DS_UID
    type: prometheus
    access: proxy
    url: http://$PROM_SERVICE:9090
    isDefault: false
    editable: false
    jsonData:
      httpMethod: POST
      timeInterval: 30s
EOF

cat > "$HOST_PROVIDER" <<'EOF'
apiVersion: 1

providers:
  - name: NetBird Prometheus
    orgId: 1
    folder: NetBird
    folderUid: netbird-prometheus
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    allowUiUpdates: false
    options:
      path: /var/lib/grafana/dashboards/netbird-prometheus
      foldersFromFilesStructure: false
EOF

cat > "$OVERRIDE_FILE" <<EOF
services:
  $PROM_SERVICE:
    image: $PROM_IMAGE
    container_name: $PROM_CONTAINER
    restart: unless-stopped
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.path=/prometheus
      - --storage.tsdb.retention.time=30d
      - --web.enable-lifecycle
    volumes:
      - "$HOST_CONFIG:/etc/prometheus/prometheus.yml:ro"
      - "$HOST_TARGETS:/etc/prometheus/targets:ro"
      - openmain_netbird_prometheus_data:/prometheus
    expose:
      - "9090"
    security_opt:
      - no-new-privileges:true

  $COMPOSE_SERVICE:
    volumes:
      - "$HOST_DATASOURCE:/etc/grafana/provisioning/datasources/openmain-netbird-prometheus.yaml:ro"
      - "$HOST_PROVIDER:/etc/grafana/provisioning/dashboards/netbird-prometheus.yaml:ro"
      - "$HOST_DASHBOARDS:/var/lib/grafana/dashboards/netbird-prometheus:ro"

volumes:
  openmain_netbird_prometheus_data:
EOF
chmod 0644 "$HOST_CONFIG" "$HOST_DATASOURCE" "$HOST_PROVIDER" "$OVERRIDE_FILE"
chmod 0755 "$HOST_TARGETS"
chmod 0644 "$HOST_TARGETS/netbird-clients.json"

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

log "Prüfe zusammengeführte Compose-Konfiguration..."
(
  cd "$COMPOSE_WORKDIR"
  "${compose_cmd[@]}" config >/dev/null
)

log "Starte Prometheus..."
(
  cd "$COMPOSE_WORKDIR"
  "${compose_cmd[@]}" pull "$PROM_SERVICE"
  "${compose_cmd[@]}" up -d "$PROM_SERVICE"
)

log "Prüfe Prometheus-Konfiguration..."
docker exec "$PROM_CONTAINER" promtool check config /etc/prometheus/prometheus.yml

log "Recreate nur des Grafana-Service für Provisioning-Mounts..."
(
  cd "$COMPOSE_WORKDIR"
  "${compose_cmd[@]}" up -d --no-deps "$COMPOSE_SERVICE"
)

sleep 3
[[ "$(docker inspect "$PROM_CONTAINER" --format '{{.State.Status}}' 2>/dev/null || true)" == "running" ]] || die "Prometheus läuft nicht."
[[ "$(docker inspect "$GRAFANA_CONTAINER" --format '{{.State.Status}}' 2>/dev/null || true)" == "running" ]] || die "Grafana läuft nicht."

log "Prüfe Prometheus API..."
docker exec "$PROM_CONTAINER" promtool query instant http://127.0.0.1:9090 'up' >/dev/null

log "Warte auf ersten erfolgreichen NetBird-Scrape..."
target_query=""
for attempt in {1..45}; do
  target_query="$(docker exec "$PROM_CONTAINER" \
    promtool query instant \
    http://127.0.0.1:9090 \
    'up{job="netbird-server"}' 2>&1 || true)"

  if grep -Eq '=>[[:space:]]+1([[:space:]]|@|$)' <<<"$target_query"; then
    log "NetBird-Server Target ist UP."
    break
  fi

  if (( attempt == 45 )); then
    echo >&2
    warn "NetBird-Server Target wurde innerhalb von 90 Sekunden nicht UP."
    [[ -n "$target_query" ]] && echo "$target_query" >&2
    warn "Letzte Prometheus-Logs:"
    docker logs --tail=100 "$PROM_CONTAINER" >&2 || true
    die "Prometheus kann $NETBIRD_METRICS_TARGET nicht erfolgreich scrapen."
  fi

  sleep 2
done

echo
echo "============================================================"
echo " OpenMain NetBird Prometheus"
echo "============================================================"
printf '%-24s %s\n' "Compose-Projekt:" "$COMPOSE_PROJECT"
printf '%-24s %s\n' "Grafana:" "$GRAFANA_CONTAINER"
printf '%-24s %s\n' "Prometheus:" "$PROM_CONTAINER"
printf '%-24s %s\n' "NetBird Server:" "$NETBIRD_METRICS_TARGET"
printf '%-24s %s\n' "Datasource UID:" "$PROM_DS_UID"
printf '%-24s %s\n' "Compose Override:" "$OVERRIDE_FILE"
printf '%-24s %s\n' "Prometheus Config:" "$HOST_CONFIG"
printf '%-24s %s\n' "Client Targets:" "$HOST_TARGETS/netbird-clients.json"
printf '%-24s %s\n' "Auto Discovery:" "$([[ "$NETBIRD_AUTO_DISCOVERY" == 1 ]] && echo aktiv || echo deaktiviert)"
if [[ "$NETBIRD_AUTO_DISCOVERY" == 1 ]]; then
  printf '%-24s %s\n' "Metrics Gruppe:" "$NETBIRD_METRICS_GROUP"
  printf '%-24s %s\n' "Monitoring Gruppe:" "$NETBIRD_MONITORING_GROUP"
  printf '%-24s %s\n' "Client Metrics Port:" "$NETBIRD_CLIENT_METRICS_PORT"
  printf '%-24s %s\n' "Prometheus Peer IP:" "${NETBIRD_PROMETHEUS_PEER_IP:-<nicht erkannt>}"
  printf '%-24s %s\n' "Sync Timer:" "openmain-netbird-target-sync.timer"
fi
echo
echo "Dashboards:"
printf '  %s\n' "Netbird / Management" "Netbird / Signal" "Netbird / Relay" "Netbird / Client"
echo
echo "NetBird-Server Target:"
printf '%s\n' "$target_query"
echo
if [[ "$NETBIRD_AUTO_DISCOVERY" == 1 ]]; then
  echo "Client-Rollout:"
  echo "  Peer in '$NETBIRD_METRICS_GROUP' + Metrics aktivieren -> Target wird automatisch übernommen."
  echo "  Manueller Helper bleibt als Fallback verfügbar:"
  echo "  openmain-netbird-add-client <NETBIRD-IP:9191> <HOSTNAME> [KUNDE]"
else
  echo "Client hinzufügen:"
  echo "  openmain-netbird-add-client <NETBIRD-IP:9191> <HOSTNAME> [KUNDE]"
fi
echo "============================================================"
