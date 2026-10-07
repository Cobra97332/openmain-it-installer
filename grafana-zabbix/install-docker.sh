#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${OPENMAIN_GITHUB_RAW:-https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/grafana-zabbix}"
INSTALL_DIR="${OPENMAIN_GRAFANA_ZABBIX_DIR:-/opt/openmain-grafana-zabbix}"
ENV_FILE="/etc/openmain-zabbix-metadata.env"
GRAFANA_CONTAINER="${GRAFANA_CONTAINER:-}"
DATASOURCE_UID="${GRAFANA_ZABBIX_UID:-}"

die() { echo "[FEHLER] $*" >&2; exit 1; }
log() { echo "[+] $*"; }

[[ $EUID -eq 0 ]] || die "Bitte als root ausführen."

for cmd in docker python3 curl; do
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

[[ -n "$COMPOSE_SERVICE" && "$COMPOSE_SERVICE" != "<no value>" ]] || die "Grafana-Container wurde nicht durch Docker Compose erstellt."
[[ -n "$COMPOSE_WORKDIR" && "$COMPOSE_WORKDIR" != "<no value>" ]] || die "Compose-Working-Directory konnte nicht ermittelt werden."
[[ -d "$COMPOSE_WORKDIR" ]] || die "Compose-Verzeichnis existiert nicht: $COMPOSE_WORKDIR"
[[ -n "$COMPOSE_FILES_RAW" && "$COMPOSE_FILES_RAW" != "<no value>" ]] || die "Compose-Konfigurationsdateien konnten nicht ermittelt werden."

log "Grafana-Container: $GRAFANA_CONTAINER"
log "Compose-Projekt: $COMPOSE_PROJECT"
log "Compose-Service: $COMPOSE_SERVICE"
log "Compose-Verzeichnis: $COMPOSE_WORKDIR"

detect_datasource_uid() {
  local tmp dbcopy
  tmp="$(mktemp -d)"
  dbcopy="$tmp/grafana.db"
  if ! docker cp "$GRAFANA_CONTAINER:/var/lib/grafana/grafana.db" "$dbcopy" >/dev/null 2>&1; then
    rm -rf "$tmp"
    return 1
  fi
  python3 - "$dbcopy" <<'PY'
import sqlite3
import sys
db = sys.argv[1]
try:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = list(con.execute("SELECT uid FROM data_source WHERE type='alexanderzobnin-zabbix-datasource' ORDER BY is_default DESC, id ASC"))
except Exception:
    raise SystemExit(1)
if not rows or not rows[0][0]:
    raise SystemExit(1)
print(rows[0][0])
PY
  rc=$?
  rm -rf "$tmp"
  return $rc
}

if [[ -z "$DATASOURCE_UID" ]]; then
  if detected_uid="$(detect_datasource_uid 2>/dev/null)"; then
    DATASOURCE_UID="$detected_uid"
    log "Zabbix-Datasource-UID erkannt: $DATASOURCE_UID"
  else
    DATASOURCE_UID="zabbix-main"
    echo "[!] Datasource-UID konnte nicht automatisch aus grafana.db gelesen werden." >&2
    echo "[!] Verwende vorläufig: $DATASOURCE_UID" >&2
    echo "[!] Bei abweichender UID: GRAFANA_ZABBIX_UID=<UID> setzen." >&2
  fi
fi

HOST_BASE="$COMPOSE_WORKDIR/openmain-grafana-zabbix"
HOST_DASHBOARDS="$HOST_BASE/dashboards"
HOST_PROVIDER="$HOST_BASE/openmain.yaml"
OVERRIDE_FILE="$COMPOSE_WORKDIR/compose.openmain-zabbix.yaml"

install -d -m 0755 "$INSTALL_DIR" "$HOST_DASHBOARDS" "$HOST_BASE"

curl -fsSL "$BASE_URL/generate-dashboards.py" -o "$INSTALL_DIR/generate-dashboards.py"
curl -fsSL "$BASE_URL/sync-zabbix-groups.py" -o "$INSTALL_DIR/sync-zabbix-groups.py"
curl -fsSL "$BASE_URL/provisioning/dashboards/openmain.yaml" -o "$HOST_PROVIDER"
curl -fsSL "$BASE_URL/openmain-zabbix-metadata.env.example" -o "$INSTALL_DIR/openmain-zabbix-metadata.env.example"
curl -fsSL "$BASE_URL/systemd/openmain-zabbix-groups.service" -o /etc/systemd/system/openmain-zabbix-groups.service
curl -fsSL "$BASE_URL/systemd/openmain-zabbix-groups.timer" -o /etc/systemd/system/openmain-zabbix-groups.timer

chmod 0755 "$INSTALL_DIR/generate-dashboards.py" "$INSTALL_DIR/sync-zabbix-groups.py"
chmod 0644 "$HOST_PROVIDER" /etc/systemd/system/openmain-zabbix-groups.service /etc/systemd/system/openmain-zabbix-groups.timer

if [[ ! -e "$ENV_FILE" ]]; then
  install -m 0600 "$INSTALL_DIR/openmain-zabbix-metadata.env.example" "$ENV_FILE"
else
  chmod 0600 "$ENV_FILE"
fi

python3 "$INSTALL_DIR/generate-dashboards.py" --datasource-uid "$DATASOURCE_UID" --output "$HOST_DASHBOARDS"

cat > "$OVERRIDE_FILE" <<EOF
services:
  $COMPOSE_SERVICE:
    volumes:
      - "$HOST_PROVIDER:/etc/grafana/provisioning/dashboards/openmain.yaml:ro"
      - "$HOST_DASHBOARDS:/var/lib/grafana/dashboards/openmain:ro"
EOF
chmod 0644 "$OVERRIDE_FILE"

IFS=',' read -r -a compose_files <<< "$COMPOSE_FILES_RAW"
compose_cmd=(docker compose)
for file in "${compose_files[@]}"; do
  file="${file#"${file%%[![:space:]]*}"}"
  file="${file%"${file##*[![:space:]]}"}"
  [[ -n "$file" ]] || continue
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

log "Recreate nur des Grafana-Service..."
(
  cd "$COMPOSE_WORKDIR"
  "${compose_cmd[@]}" up -d --no-deps "$COMPOSE_SERVICE"
)

systemctl daemon-reload
if grep -Eq '^ZABBIX_ADMIN_API_TOKEN=.+$' "$ENV_FILE"; then
  systemctl enable --now openmain-zabbix-groups.timer
  systemctl start openmain-zabbix-groups.service
  log "Zabbix-Metadaten-Sync aktiviert."
else
  echo "[!] Zabbix-Sync installiert, aber noch nicht aktiviert." >&2
  echo "[!] Token eintragen: $ENV_FILE" >&2
fi

sleep 2
status="$(docker inspect "$GRAFANA_CONTAINER" --format '{{.State.Status}}' 2>/dev/null || true)"
[[ "$status" == "running" ]] || die "Grafana läuft nach dem Recreate nicht. Status: $status"

mounts="$(docker inspect "$GRAFANA_CONTAINER" --format '{{range .Mounts}}{{println .Source " -> " .Destination}}{{end}}')"
grep -Fq '/etc/grafana/provisioning/dashboards/openmain.yaml' <<<"$mounts" || die "Provisioning-Mount fehlt."
grep -Fq '/var/lib/grafana/dashboards/openmain' <<<"$mounts" || die "Dashboard-Mount fehlt."

echo
echo "============================================================"
echo " OpenMain Grafana/Zabbix Docker-Provisioning"
echo "============================================================"
printf '%-22s %s\n' "Container:" "$GRAFANA_CONTAINER"
printf '%-22s %s\n' "Compose-Service:" "$COMPOSE_SERVICE"
printf '%-22s %s\n' "Datasource UID:" "$DATASOURCE_UID"
printf '%-22s %s\n' "Override:" "$OVERRIDE_FILE"
printf '%-22s %s\n' "Dashboards:" "$HOST_DASHBOARDS"
printf '%-22s %s\n' "Provider:" "$HOST_PROVIDER"
printf '%-22s %s\n' "Sync-Konfiguration:" "$ENV_FILE"
echo
echo "Erzeugte Dashboards:"
find "$HOST_DASHBOARDS" -maxdepth 1 -type f -name '*.json' -printf '  %f\n' | sort
echo "============================================================"
