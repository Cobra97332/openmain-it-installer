#!/usr/bin/env bash
set -Eeuo pipefail

CUSTOMER_ID_ARG=""
PBS_STORAGE_ID_ARG=""

usage() {
  cat <<'EOF'
Usage: install.sh [--customer-id NAME] [--storage-id STORAGE]

Beispiele:
  ./install.sh
  ./install.sh --customer-id kunde-muster
  ./install.sh --customer-id kunde-muster --storage-id PBS-Kunde
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --customer-id)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      CUSTOMER_ID_ARG="$2"
      shift 2
      ;;
    --storage-id)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      PBS_STORAGE_ID_ARG="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

[[ $EUID -eq 0 ]] || {
  echo "Bitte als root ausführen." >&2
  exit 1
}

command -v pveversion >/dev/null 2>&1 || {
  echo "Kein Proxmox-VE-System erkannt." >&2
  exit 1
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

install -o root -g root -m 700   "$SCRIPT_DIR/pve-config-backup.sh"   /usr/local/sbin/pve-config-backup.sh

install -o root -g root -m 644   "$SCRIPT_DIR/pve-config-backup.service"   /etc/systemd/system/pve-config-backup.service

install -o root -g root -m 644   "$SCRIPT_DIR/pve-config-backup.timer"   /etc/systemd/system/pve-config-backup.timer

if [[ ! -e /etc/pve-config-backup.conf ]]; then
  install -o root -g root -m 600     "$SCRIPT_DIR/pve-config-backup.conf.example"     /etc/pve-config-backup.conf
  echo "Konfiguration angelegt: /etc/pve-config-backup.conf"
else
  echo "Vorhandene /etc/pve-config-backup.conf bleibt erhalten."
fi

append_setting() {
  local key="$1" value="$2"
  [[ -n "$value" ]] || return 0

  if grep -qE "^[[:space:]]*${key}=" /etc/pve-config-backup.conf; then
    sed -i -E "s|^[[:space:]]*${key}=.*|${key}=\"${value//|/\\|}\"|"       /etc/pve-config-backup.conf
  else
    printf '\n%s="%s"\n' "$key" "$value" >> /etc/pve-config-backup.conf
  fi
}

append_setting CUSTOMER_ID "$CUSTOMER_ID_ARG"
append_setting PBS_STORAGE_ID "$PBS_STORAGE_ID_ARG"
chmod 600 /etc/pve-config-backup.conf

systemctl daemon-reload

echo
echo "Prüfe PVE/PBS-Konfiguration..."
if /usr/local/sbin/pve-config-backup.sh --check; then
  systemctl enable --now pve-config-backup.timer
  echo
  echo "Installation abgeschlossen."
  echo "Timer aktiviert."
  echo "Testbackup: systemctl start pve-config-backup.service"
  echo "Log: journalctl -u pve-config-backup.service -n 200 --no-pager"
else
  systemctl disable --now pve-config-backup.timer >/dev/null 2>&1 || true
  echo >&2
  echo "Installation durchgeführt, Timer wegen Preflight-Fehler deaktiviert." >&2
  echo "Konfiguration: /etc/pve-config-backup.conf" >&2
  exit 1
fi
