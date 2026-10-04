#!/usr/bin/env bash
set -Eeuo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="/etc/patchmon-proxmox.env"
DEFAULT_PATCHMON_URL="https://patchmon.openmain-it.de"

[[ $EUID -eq 0 ]] || { echo "Bitte als root ausführen."; exit 1; }

echo "PatchMon Proxmox Installation"
echo
read -r -p "PatchMon URL [${DEFAULT_PATCHMON_URL}]: " PATCHMON_URL
PATCHMON_URL="${PATCHMON_URL:-$DEFAULT_PATCHMON_URL}"

while :; do
  read -r -p "Auto-Enrollment Token Key: " AUTO_ENROLLMENT_KEY
  [[ -n "$AUTO_ENROLLMENT_KEY" ]] && break
  echo "Der Token Key darf nicht leer sein."
done

while :; do
  read -r -s -p "Auto-Enrollment Token Secret: " AUTO_ENROLLMENT_SECRET
  echo
  [[ -n "$AUTO_ENROLLMENT_SECRET" ]] && break
  echo "Das Token Secret darf nicht leer sein."
done

echo
echo "Hinweis: Die in PatchMon am Auto-Enrollment-Token konfigurierte Standardgruppe"
echo "wird automatisch für neu aufgenommene Hosts verwendet."
echo

install -d -m 0755 /usr/local/sbin
install -m 0700 "$BASE_DIR/patchmon-proxmox-deploy.bash" /usr/local/sbin/patchmon-proxmox-deploy
install -m 0644 "$BASE_DIR/systemd/patchmon-proxmox-deploy.service" /etc/systemd/system/patchmon-proxmox-deploy.service
install -m 0644 "$BASE_DIR/systemd/patchmon-proxmox-deploy.timer" /etc/systemd/system/patchmon-proxmox-deploy.timer

umask 077
cat > "$CONFIG_FILE" <<EOF
# Lokale Konfiguration für den PVE-Host
PATCHMON_URL="$PATCHMON_URL"
AUTO_ENROLLMENT_KEY="$AUTO_ENROLLMENT_KEY"
AUTO_ENROLLMENT_SECRET="$AUTO_ENROLLMENT_SECRET"
ENABLE_LXC=true
ENABLE_LINUX_VMS=true
ENABLE_WINDOWS_VMS=true
ENABLE_FREEBSD_VMS=true
DRY_RUN=false
EOF
chmod 0600 "$CONFIG_FILE"

systemctl daemon-reload
systemctl enable --now patchmon-proxmox-deploy.timer

echo
echo "Installation abgeschlossen."
echo "Konfiguration: $CONFIG_FILE"
echo "Testlauf:"
echo "  DRY_RUN=true /usr/local/sbin/patchmon-proxmox-deploy"
echo
echo "Echtlauf:"
echo "  /usr/local/sbin/patchmon-proxmox-deploy"
