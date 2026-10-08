#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${OPENMAIN_GITHUB_RAW:-https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/grafana-zabbix}"
INSTALL_DIR="${OPENMAIN_GRAFANA_ZABBIX_DIR:-/opt/openmain-grafana-zabbix}"
PROVISIONING_DIR="${GRAFANA_PROVISIONING_DIR:-/etc/grafana/provisioning}"
DASHBOARD_DIR="${GRAFANA_DASHBOARD_DIR:-/var/lib/grafana/dashboards/openmain}"
ENV_FILE="/etc/openmain-zabbix-metadata.env"
DATASOURCE_UID="${GRAFANA_ZABBIX_UID:-}"

if [[ $EUID -ne 0 ]]; then
  echo "Bitte als root ausführen." >&2
  exit 1
fi

for cmd in curl python3; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "$cmd fehlt." >&2
    exit 1
  }
done

detect_datasource_uid() {
  local db="${GRAFANA_DB_PATH:-/var/lib/grafana/grafana.db}"
  [[ -r "$db" ]] || return 1

  python3 - "$db" <<'PY'
import sqlite3
import sys

db = sys.argv[1]
try:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = list(con.execute(
        "SELECT uid FROM data_source "
        "WHERE type='alexanderzobnin-zabbix-datasource' "
        "ORDER BY is_default DESC, id ASC"
    ))
except Exception:
    raise SystemExit(1)

if not rows or not rows[0][0]:
    raise SystemExit(1)

print(rows[0][0])
PY
}

if [[ -z "$DATASOURCE_UID" ]]; then
  if detected_uid="$(detect_datasource_uid 2>/dev/null)"; then
    DATASOURCE_UID="$detected_uid"
    echo "[+] Vorhandene Grafana-Zabbix-Datasource erkannt: $DATASOURCE_UID"
  else
    DATASOURCE_UID="zabbix-main"
    echo "[!] Grafana-Zabbix-Datasource konnte nicht automatisch erkannt werden."
    echo "[!] Dashboard-UID wird vorläufig auf '$DATASOURCE_UID' gesetzt."
    echo "[!] Falls deine Datasource eine andere UID hat: GRAFANA_ZABBIX_UID=<UID> setzen und Installer erneut ausführen."
  fi
fi

install -d -m 0755 "$INSTALL_DIR"
install -d -m 0755 "$DASHBOARD_DIR"
install -d -m 0755 "$PROVISIONING_DIR/dashboards"

curl -fsSL "$BASE_URL/generate-dashboards.py" -o "$INSTALL_DIR/generate-dashboards.py"
curl -fsSL "$BASE_URL/sync-zabbix-groups.py" -o "$INSTALL_DIR/sync-zabbix-groups.py"\ncurl -fsSL "$BASE_URL/inspect-zabbix-host.py" -o "$INSTALL_DIR/inspect-zabbix-host.py"
curl -fsSL "$BASE_URL/provisioning/dashboards/openmain.yaml" -o "$PROVISIONING_DIR/dashboards/openmain.yaml"
curl -fsSL "$BASE_URL/openmain-zabbix-metadata.env.example" -o "$INSTALL_DIR/openmain-zabbix-metadata.env.example"

chmod 0755 "$INSTALL_DIR/generate-dashboards.py" "$INSTALL_DIR/sync-zabbix-groups.py" "$INSTALL_DIR/inspect-zabbix-host.py"

if [[ ! -e "$ENV_FILE" ]]; then
  install -m 0600 "$INSTALL_DIR/openmain-zabbix-metadata.env.example" "$ENV_FILE"
fi
chmod 0600 "$ENV_FILE"

# Migration: NetBird ist Teil der kritischen OpenMain-Plattformen.
if grep -q '^OPENMAIN_CRITICAL_PLATFORMS=' "$ENV_FILE"; then
  current_platforms="$(sed -n 's/^OPENMAIN_CRITICAL_PLATFORMS=//p' "$ENV_FILE" | head -1)"
  case ",$current_platforms," in
    *,netbird,*) ;;
    *) sed -i "s/^OPENMAIN_CRITICAL_PLATFORMS=.*/OPENMAIN_CRITICAL_PLATFORMS=$current_platforms,netbird/" "$ENV_FILE" ;;
  esac
else
  echo 'OPENMAIN_CRITICAL_PLATFORMS=opnsense,pve,idrac,nas,qnap,synology,netbird' >> "$ENV_FILE"
fi

python3 "$INSTALL_DIR/generate-dashboards.py" \
  --datasource-uid "$DATASOURCE_UID" \
  --output "$DASHBOARD_DIR"

if id grafana >/dev/null 2>&1; then
  chown -R grafana:grafana "$DASHBOARD_DIR"
fi

if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
  curl -fsSL "$BASE_URL/systemd/openmain-zabbix-groups.service" \
    -o /etc/systemd/system/openmain-zabbix-groups.service
  curl -fsSL "$BASE_URL/systemd/openmain-zabbix-groups.timer" \
    -o /etc/systemd/system/openmain-zabbix-groups.timer

  chmod 0644 \
    /etc/systemd/system/openmain-zabbix-groups.service \
    /etc/systemd/system/openmain-zabbix-groups.timer

  systemctl daemon-reload

  if [[ -s "$ENV_FILE" ]] && grep -Eq '^ZABBIX_ADMIN_API_TOKEN=.+$' "$ENV_FILE"; then
    chmod 0600 "$ENV_FILE"
    systemctl enable --now openmain-zabbix-groups.timer
    systemctl start openmain-zabbix-groups.service
    echo "[+] Zabbix-Metadaten-Sync aktiviert."
  else
    echo "[!] Zabbix-Metadaten-Sync installiert, aber noch nicht aktiviert."
    echo "[!] Vorlage: $INSTALL_DIR/openmain-zabbix-metadata.env.example"
    echo "[!] Ziel:    $ENV_FILE"
  fi
fi

if systemctl is-active --quiet grafana-server 2>/dev/null; then
  systemctl restart grafana-server
  echo "[+] Grafana neu gestartet."
fi

echo
echo "Grafana-Dashboards erzeugt:"
find "$DASHBOARD_DIR" -maxdepth 1 -type f -name '*.json' -printf '  %f\n' | sort

echo
echo "Datasource UID: $DATASOURCE_UID"
echo "Provider:       $PROVISIONING_DIR/dashboards/openmain.yaml"
echo "Dashboard-Pfad: $DASHBOARD_DIR"
echo "Sync-Umgebung:  $ENV_FILE"

echo
echo "Für Docker-Grafana müssen Provisioning- und Dashboard-Pfad persistent in den Container gemountet sein."
echo "Das Skript verändert keine vorhandene Docker-Compose-Datei automatisch."
