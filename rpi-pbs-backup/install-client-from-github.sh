#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "Als root ausführen." >&2; exit 1; }
BASE_URL="https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/client"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
for f in install-rpi-client.sh rpi-pbs-backup.sh rpi-pbs-backup.conf.example rpi-pbs-backup.service rpi-pbs-backup.timer; do
  curl -fsSL "$BASE_URL/$f" -o "$TMP/$f"
done
chmod +x "$TMP/install-rpi-client.sh" "$TMP/rpi-pbs-backup.sh"
"$TMP/install-rpi-client.sh"
