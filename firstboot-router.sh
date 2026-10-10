#!/usr/bin/env bash
set +xv
set -Eeuo pipefail
DONE=/var/lib/openmain-router-firstboot.done
[[ -e "$DONE" ]] && exit 0
ROOT=/opt/openmain-router
[[ -f "$ROOT/router-install.sh" ]] || { echo 'Installer-Bundle fehlt.' >&2; exit 1; }
install -d -m 0700 /etc/openmain-router
CONFIG=/etc/openmain-router/firstboot.json
# Keep root-only input on failures; retries reconcile the same customer objects.
if [[ ! -f "$CONFIG" ]]; then
  python3 "$ROOT/netbird-router/common/prepare_config.py" "$CONFIG"
fi
bash "$ROOT/router-install.sh" --config "$CONFIG"
install -d -m 0700 /var/lib
(umask 077; touch "$DONE")
rm -f "$CONFIG"
echo 'Kundenrouter erfolgreich eingerichtet; Pruefbericht siehe Installer-Ausgabe.'
