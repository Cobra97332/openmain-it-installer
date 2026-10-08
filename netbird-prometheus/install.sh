#!/usr/bin/env bash
set -Eeuo pipefail

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
      printf 'Mehrere Grafana-Container gefunden:\n' >&2
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

HOST_BASE="$COMPOSE_WORKDIR/openmain-netbird-prometheus"
HOST_CONFIG="$HOST_BASE/prometheus.yml"
HOST_TARGETS="$HOST_BASE/targets"
HOST_DASHBOARDS="$HOST_BASE/dashboards"
HOST_DATASOURCE="$HOST_BASE/prometheus-datasource.yaml"
HOST_PROVIDER="$HOST_BASE/netbird-dashboards.yaml"
OVERRIDE_FILE="$COMPOSE_WORKDIR/compose.openmain-netbird-prometheus.yaml"

install -d -m 0755 "$INSTALL_DIR" "$HOST_BASE" "$HOST_TARGETS" "$HOST_DASHBOARDS"

log "Lade offizielle NetBird-Grafana-Dashboards (Ref: $NETBIRD_REF)..."
for dashboard in management signal relay client; do
  curl -fsSL     "https://raw.githubusercontent.com/netbirdio/netbird/$NETBIRD_REF/infrastructure_files/observability/grafana/dashboards/$dashboard.json"     -o "$HOST_DASHBOARDS/$dashboard.json"
done

python3 - "$HOST_DASHBOARDS" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
expected = {"management.json", "signal.json", "relay.json", "client.json"}
found = {p.name for p in root.glob("*.json")}
if found != expected:
    raise SystemExit(f"Dashboard-Satz unvollständig: {sorted(found)}")

for path in root.glob("*.json"):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("title"):
        raise SystemExit(f"Dashboard ohne Titel: {path}")
    data["id"] = None
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY

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
      # Der NetBird Combined Server versieht Signal-App-Metriken mit "signal_".
      # Das offizielle Signal-Dashboard erwartet die Standalone-Namen.
      - source_labels: [__name__]
        regex: 'signal_(.*)'
        target_label: __name__
        replacement: '$1'

  - job_name: netbird-client
    file_sd_configs:
      - files:
          - /etc/prometheus/targets/netbird-clients.json
        refresh_interval: 30s
EOF

if [[ ! -f "$HOST_TARGETS/netbird-clients.json" ]]; then
  printf '[]\n' > "$HOST_TARGETS/netbird-clients.json"
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

cat > "$INSTALL_DIR/add-client-target.py" <<PY
#!/usr/bin/env python3
import json
import sys
from pathlib import Path

path = Path("$HOST_TARGETS/netbird-clients.json")
if len(sys.argv) < 3:
    raise SystemExit("Verwendung: add-client-target.py <NetBird-IP[:Port]> <Host> [Kunde]")

target = sys.argv[1]
if ":" not in target:
    target += ":9191"
host = sys.argv[2]
customer = sys.argv[3] if len(sys.argv) > 3 else ""

data = json.loads(path.read_text(encoding="utf-8"))
labels = {"host": host}
if customer:
    labels["customer"] = customer
entry = {"targets": [target], "labels": labels}

for idx, current in enumerate(data):
    if target in current.get("targets", []):
        data[idx] = entry
        break
else:
    data.append(entry)

data.sort(key=lambda x: (x.get("labels", {}).get("customer", ""), x.get("labels", {}).get("host", "")))
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
print(f"Target gespeichert: {target} -> {host}")
PY
chmod 0755 "$INSTALL_DIR/add-client-target.py"

cat > /usr/local/sbin/openmain-netbird-add-client <<EOF
#!/usr/bin/env bash
exec "$INSTALL_DIR/add-client-target.py" "\$@"
EOF
chmod 0755 /usr/local/sbin/openmain-netbird-add-client

echo
echo "============================================================"
echo " OpenMain NetBird Prometheus"
echo "============================================================"
printf '%-24s %s\n' "Grafana:" "$GRAFANA_CONTAINER"
printf '%-24s %s\n' "Prometheus:" "$PROM_CONTAINER"
printf '%-24s %s\n' "NetBird Server:" "$NETBIRD_METRICS_TARGET"
printf '%-24s %s\n' "Datasource UID:" "$PROM_DS_UID"
printf '%-24s %s\n' "Compose Override:" "$OVERRIDE_FILE"
printf '%-24s %s\n' "Prometheus Config:" "$HOST_CONFIG"
printf '%-24s %s\n' "Client Targets:" "$HOST_TARGETS/netbird-clients.json"
echo
echo "Dashboards:"
printf '  %s\n' "Netbird / Management" "Netbird / Signal" "Netbird / Relay" "Netbird / Client"
echo
echo "Prometheus-Targetstatus:"
docker exec "$PROM_CONTAINER" promtool query instant http://127.0.0.1:9090 'up{job="netbird-server"}' || true
echo
echo "Client hinzufügen:"
echo "  openmain-netbird-add-client <NETBIRD-IP:9191> <HOSTNAME> [KUNDE]"
echo "============================================================"
