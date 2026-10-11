#!/usr/bin/env bash
set +xv
set -Eeuo pipefail
DONE=/var/lib/openmain-router-firstboot.done
[[ -e "$DONE" ]] && exit 0
ROOT=/opt/openmain-router
[[ -f "$ROOT/router-install.sh" ]] || { echo 'Installer-Bundle fehlt.' >&2; exit 1; }
CONFIG=/etc/openmain-router/firstboot.json
preflight_args=()
[[ ! -e "$CONFIG" && ! -L "$CONFIG" ]] || preflight_args+=(--config "$CONFIG")
profile=$(python3 "$ROOT/netbird-router/common/preflight.py" "${preflight_args[@]}" --report-profile)
install -d -m 0700 /etc/openmain-router
# Keep root-only input on failures; retries reconcile the same customer objects.
config_args=("$CONFIG")
[[ ! -f "$CONFIG" ]] || config_args+=("$CONFIG")
if [[ -z "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["path"])' <<< "$profile")" ]]; then
  config_args+=(--require-setup-key)
fi
python3 "$ROOT/netbird-router/common/prepare_config.py" "${config_args[@]}"
bash "$ROOT/router-install.sh" --config "$CONFIG"
install -d -m 0700 /var/lib
(umask 077; touch "$DONE")
rm -f "$CONFIG"
echo 'Kundenrouter erfolgreich eingerichtet; Pruefbericht siehe Installer-Ausgabe.'
