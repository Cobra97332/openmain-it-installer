#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "Als root ausführen." >&2; exit 1; }

command -v pveversion >/dev/null 2>&1 || {
  echo "FEHLER: Dieses Gateway wird direkt auf einem Proxmox-VE-Host installiert." >&2
  exit 1
}

BASE_URL="https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/gateway"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

for f in install-gateway.sh rpi-pbs-ingest rpi-pbs-restore rpi-pbs-gateway.conf.example; do
  curl -fsSL "$BASE_URL/$f" -o "$TMP/$f"
done

chmod +x "$TMP/install-gateway.sh" "$TMP/rpi-pbs-ingest" "$TMP/rpi-pbs-restore"
"$TMP/install-gateway.sh" "$@"
