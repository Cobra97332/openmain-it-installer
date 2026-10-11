#!/usr/bin/env bash
set +xv
set -Eeuo pipefail
umask 077
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
CONFIG=''
PREPARED_CONFIG=/etc/openmain-router/pi.json
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG=$2; shift 2;;
    --customer) export NB_CUSTOMER_NAME="$2"; shift 2;;
    --role) export NB_ROLE="$2"; shift 2;;
    --hostname) export NB_HOSTNAME="$2"; shift 2;;
    --lan-interface) export NB_LAN_INTERFACE="$2"; shift 2;;
    --setup-key) export NB_SETUP_KEY="$2"; shift 2;;
    --api-token) export NB_API_TOKEN="$2"; shift 2;;
    --management-url) export NB_MANAGEMENT_URL="$2"; shift 2;;
    --no-metrics) export NB_METRICS_ENABLED=0; shift;;
    *) echo 'Aufruf: install-router.sh [--config /root/router.json]' >&2; exit 1;;
  esac
done
[[ $EUID -eq 0 ]] || exit 1
[[ ! -e /var/lib/openmain-pi-deployed ]] || { echo 'Bereits ausgerollt. Keine Aenderung; Updates separat ausfuehren.'; exit 0; }
[[ -n "$CONFIG" || ( ! -e "$PREPARED_CONFIG" && ! -L "$PREPARED_CONFIG" ) ]] || CONFIG="$PREPARED_CONFIG"
preflight_args=()
[[ -z "$CONFIG" ]] || preflight_args+=(--config "$CONFIG")
profile=$(python3 "$ROOT/netbird-router/common/preflight.py" "${preflight_args[@]}" --report-profile)
python3 "$ROOT/netbird-router/common/runtime.py" platform
if [[ ! -r /proc/device-tree/model ]] || ! grep -aq '^Raspberry Pi [345] Model ' /proc/device-tree/model; then { echo 'Raspberry Pi 3/4/5 erforderlich.' >&2; exit 1; }; fi
if ! command -v jq >/dev/null || ! command -v ping >/dev/null; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y jq iputils-ping
fi
install -d -m 0700 /etc/openmain-router
args=("$PREPARED_CONFIG")
[[ -z "$CONFIG" ]] || args+=("$CONFIG")
if [[ -z "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["path"])' <<< "$profile")" ]]; then
  args+=(--require-setup-key)
fi
python3 "$ROOT/netbird-router/common/prepare_config.py" "${args[@]}"
# Save office gateway MAC as well as prefix: equal RFC1918 networks occur often.
python3 "$ROOT/netbird-router/common/pi_firstboot.py" prepare
install -d -m 0755 /opt/openmain-router
if [[ "$ROOT" != /opt/openmain-router ]]; then
  python3 "$ROOT/netbird-router/common/runtime.py" bundle "$ROOT" /opt/openmain-router
fi
bash /opt/openmain-router/netbird-router/pve/install-dhcp-recovery.sh
cat > /etc/systemd/system/openmain-pi-firstboot.service <<'UNIT'
[Unit]
Description=Deploy customer router after leaving the preparation network
After=network-online.target
Wants=network-online.target
ConditionPathExists=!/var/lib/openmain-pi-deployed
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/openmain-router/netbird-router/common/pi_firstboot.py deploy
TimeoutStartSec=45min
UMask=0077
UNIT
cat > /etc/systemd/system/openmain-pi-firstboot.timer <<'UNIT'
[Unit]
Description=Retry deferred customer deployment
[Timer]
OnBootSec=30s
OnUnitInactiveSec=2min
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now openmain-pi-firstboot.timer
printf 'Vorbereitet: beim Kunden LAN anschliessen. Zugangsdaten werden nach Erfolg entfernt.\n'
