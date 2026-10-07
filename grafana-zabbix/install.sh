#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${OPENMAIN_GITHUB_RAW:-https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/grafana-zabbix}"
INSTALL_DIR="${OPENMAIN_GRAFANA_ZABBIX_DIR:-/opt/openmain-grafana-zabbix}"
PROVISIONING_DIR="${GRAFANA_PROVISIONING_DIR:-/etc/grafana/provisioning}"
DASHBOARD_DIR="${GRAFANA_DASHBOARD_DIR:-/var/lib/grafana/dashboards/openmain}"
DATASOURCE_UID="${GRAFANA_ZABBIX_UID:-zabbix-main}"

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

install -d -m 0755 "$INSTALL_DIR"
install -d -m 0755 "$DASHBOARD_DIR"
install -d -m 0755 "$PROVISIONING_DIR/dashboards"

curl -fsSL "$BASE_URL/generate-dashboards.py" -o "$INSTALL_DIR/generate-dashboards.py"
curl -fsSL "$BASE_URL/sync-zabbix-groups.py" -o "$INSTALL_DIR/sync-zabbix-groups.py"
curl -fsSL "$BASE_URL/provisioning/dashboards/openmain.yaml" -o "$PROVISIONING_DIR/dashboards/openmain.yaml"

chmod 0755 "$INSTALL_DIR/generate-dashboards.py" "$INSTALL_DIR/sync-zabbix-groups.py"

python3 "$INSTALL_DIR/generate-dashboards.py" \
  --datasource-uid "$DATASOURCE_UID" \
  --output "$DASHBOARD_DIR"

if id grafana >/dev/null 2>&1; then
  chown -R grafana:grafana "$DASHBOARD_DIR"
fi

echo
echo "Grafana-Dashboards erzeugt:"
find "$DASHBOARD_DIR" -maxdepth 1 -type f -name '*.json' -printf '  %f\n' | sort
echo
echo "Datasource UID: $DATASOURCE_UID"
echo "Provider:       $PROVISIONING_DIR/dashboards/openmain.yaml"
echo "Dashboard-Pfad: $DASHBOARD_DIR"
echo
echo "Bei nativem Grafana jetzt: systemctl restart grafana-server"
echo "Bei Docker müssen Provisioning und Dashboard-Pfad in den Container gemountet sein."
