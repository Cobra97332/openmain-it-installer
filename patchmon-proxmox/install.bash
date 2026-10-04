#!/usr/bin/env bash
set -Eeuo pipefail

RAW_BASE="https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/patchmon-proxmox"
CONFIG_FILE="/etc/patchmon-proxmox.env"
DEFAULT_PATCHMON_URL="https://patchmon.openmain-it.de"

[[ $EUID -eq 0 ]] || { echo "Bitte als root ausführen."; exit 1; }

for cmd in curl apt-get systemctl; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Benötigter Befehl fehlt: $cmd"; exit 1; }
done

if ! command -v jq >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y jq
fi

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

curl -fsSL "$RAW_BASE/patchmon-proxmox-deploy.bash"   -o /usr/local/sbin/patchmon-proxmox-deploy
chmod 0700 /usr/local/sbin/patchmon-proxmox-deploy

curl -fsSL "$RAW_BASE/systemd/patchmon-proxmox-deploy.service"   -o /etc/systemd/system/patchmon-proxmox-deploy.service
chmod 0644 /etc/systemd/system/patchmon-proxmox-deploy.service

curl -fsSL "$RAW_BASE/systemd/patchmon-proxmox-deploy.timer"   -o /etc/systemd/system/patchmon-proxmox-deploy.timer
chmod 0644 /etc/systemd/system/patchmon-proxmox-deploy.timer

umask 077
{
  echo "# Lokale Konfiguration für den PVE-Host"
  printf 'PATCHMON_URL=%q\n' "$PATCHMON_URL"
  printf 'AUTO_ENROLLMENT_KEY=%q\n' "$AUTO_ENROLLMENT_KEY"
  printf 'AUTO_ENROLLMENT_SECRET=%q\n' "$AUTO_ENROLLMENT_SECRET"
  echo "ENABLE_LXC=true"
  echo "ENABLE_LINUX_VMS=true"
  echo "ENABLE_WINDOWS_VMS=true"
  echo "ENABLE_FREEBSD_VMS=true"
  echo "DRY_RUN=false"
} > "$CONFIG_FILE"
chmod 0600 "$CONFIG_FILE"

systemctl daemon-reload
systemctl enable --now patchmon-proxmox-deploy.timer

echo
echo "Installation abgeschlossen."
echo "Konfiguration: $CONFIG_FILE"
echo "Timer: patchmon-proxmox-deploy.timer ist aktiviert und gestartet."
echo
echo "Testlauf:"
echo "  DRY_RUN=true /usr/local/sbin/patchmon-proxmox-deploy"
echo
echo "Echtlauf:"
echo "  /usr/local/sbin/patchmon-proxmox-deploy"
