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

Der Installer:
  1. installiert Skript, Service und Timer
  2. prüft Bash- und systemd-Konfiguration
  3. prüft PVE/PBS per --check
  4. startet SOFORT ein echtes Backup
  5. prüft den Backup-Service
  6. aktiviert erst danach den täglichen Timer
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

fail_install() {
  echo >&2
  echo "FEHLER: Installation/Test nicht erfolgreich." >&2
  systemctl disable --now pve-config-backup.timer >/dev/null 2>&1 || true
  echo "Timer wurde deaktiviert." >&2
  echo "Log:" >&2
  journalctl -u pve-config-backup.service -n 100 --no-pager >&2 2>/dev/null || true
  exit 1
}

echo "==> Installiere PVE Config Backup"

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

echo
echo "==> 1/5 Bash-Syntax prüfen"
bash -n /usr/local/sbin/pve-config-backup.sh || fail_install
bash -n "$0" || fail_install
echo "OK"

echo
echo "==> 2/5 systemd-Units prüfen"
systemctl daemon-reload || fail_install

if command -v systemd-analyze >/dev/null 2>&1; then
  systemd-analyze verify     /etc/systemd/system/pve-config-backup.service     /etc/systemd/system/pve-config-backup.timer     >/dev/null 2>&1 || fail_install
fi
echo "OK"

echo
echo "==> 3/5 PVE/PBS-Konfiguration prüfen"
/usr/local/sbin/pve-config-backup.sh --check || fail_install
echo "OK"

echo
echo "==> 4/5 SOFORT echtes Konfigurationsbackup auf PBS starten"
systemctl reset-failed pve-config-backup.service >/dev/null 2>&1 || true
if ! systemctl start pve-config-backup.service; then
  fail_install
fi

RESULT="$(systemctl show pve-config-backup.service -p Result --value 2>/dev/null || true)"
STATUS="$(systemctl show pve-config-backup.service -p ExecMainStatus --value 2>/dev/null || true)"

if [[ "$RESULT" != "success" || "$STATUS" != "0" ]]; then
  echo "Service Result=$RESULT ExecMainStatus=$STATUS" >&2
  fail_install
fi

echo "Backup erfolgreich."
journalctl -u pve-config-backup.service -n 30 --no-pager || true

echo
echo "==> 5/5 täglichen Timer aktivieren und prüfen"
systemctl enable --now pve-config-backup.timer || fail_install
systemctl is-enabled --quiet pve-config-backup.timer || fail_install
systemctl is-active --quiet pve-config-backup.timer || fail_install

echo
echo "============================================================"
echo "Installation vollständig erfolgreich."
echo "Sofort-Backup: OK"
echo "Backup-Service: OK"
echo "Timer: aktiviert und aktiv"
echo "============================================================"
echo
systemctl list-timers pve-config-backup.timer --no-pager || true
echo
echo "Konfiguration: /etc/pve-config-backup.conf"
echo "Manueller Check: /usr/local/sbin/pve-config-backup.sh --check"
echo "Backup-Log: journalctl -u pve-config-backup.service -n 200 --no-pager"
