#!/usr/bin/env bash
set -Eeuo pipefail
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
install -d -m 0755 /usr/local/sbin
install -m 0700 "$BASE_DIR/patchmon-proxmox-deploy.bash" /usr/local/sbin/patchmon-proxmox-deploy
if [[ ! -f /etc/patchmon-proxmox.env ]]; then
  install -m 0600 "$BASE_DIR/patchmon-proxmox.env.example" /etc/patchmon-proxmox.env
fi
install -m 0644 "$BASE_DIR/systemd/patchmon-proxmox-deploy.service" /etc/systemd/system/patchmon-proxmox-deploy.service
install -m 0644 "$BASE_DIR/systemd/patchmon-proxmox-deploy.timer" /etc/systemd/system/patchmon-proxmox-deploy.timer
systemctl daemon-reload
systemctl enable patchmon-proxmox-deploy.timer
echo "Installiert. Jetzt /etc/patchmon-proxmox.env bearbeiten."
