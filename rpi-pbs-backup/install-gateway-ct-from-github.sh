#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "Als root auf dem PVE-Host ausführen." >&2; exit 1; }
command -v pveversion >/dev/null 2>&1 || { echo "FEHLER: Kein Proxmox VE erkannt." >&2; exit 1; }
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/pve/deploy-gateway-ct.sh -o "$TMP"
chmod 700 "$TMP"
"$TMP" "$@"
