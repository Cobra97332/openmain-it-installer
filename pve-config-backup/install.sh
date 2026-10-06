#!/usr/bin/env bash
set -Eeuo pipefail

SSH_PUBLIC_KEY="${OPENMAIN_SSH_PUBLIC_KEY:-ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJc8VZvZ7o/8emKoGC7UXPiOMP8PSxch6P2rUGNio8Vi Stefan}"

ensure_openmain_root_ssh() {
  if ! command -v sshd >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-server
  fi

  install -d -m 0700 /root/.ssh
  touch /root/.ssh/authorized_keys
  grep -qxF "$SSH_PUBLIC_KEY" /root/.ssh/authorized_keys 2>/dev/null || printf '%s\n' "$SSH_PUBLIC_KEY" >> /root/.ssh/authorized_keys
  chown root:root /root/.ssh/authorized_keys
  chmod 0600 /root/.ssh/authorized_keys

  install -d -m 0755 /etc/ssh/sshd_config.d
  cat > /etc/ssh/sshd_config.d/00-openmain-root-key.conf <<'EOF'
PermitRootLogin prohibit-password
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF

  sshd -t
  systemctl enable --now ssh >/dev/null 2>&1 || systemctl enable --now sshd >/dev/null 2>&1 || true
  systemctl restart ssh >/dev/null 2>&1 || systemctl restart sshd >/dev/null 2>&1 || true
}


[[ $# -eq 0 ]] || {
  echo "Dieser Installer benötigt keine Argumente." >&2
  echo "Einfach ausführen mit: ./install.sh" >&2
  exit 2
}

[[ $EUID -eq 0 ]] || {
  echo "Bitte als root ausführen." >&2
  exit 1
}

ensure_openmain_root_ssh

command -v pveversion >/dev/null 2>&1 || {
  echo "Kein Proxmox-VE-System erkannt." >&2
  exit 1
}

[[ -r /etc/pve/storage.cfg ]] || {
  echo "/etc/pve/storage.cfg ist nicht lesbar." >&2
  exit 1
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

fail_install() {
  echo >&2
  echo "FEHLER: Installation/Test nicht erfolgreich." >&2
  systemctl disable --now pve-config-backup.timer >/dev/null 2>&1 || true
  echo "Timer wurde deaktiviert." >&2
  echo "Letztes Backup-Log:" >&2
  journalctl -u pve-config-backup.service -n 100 --no-pager >&2 2>/dev/null || true
  exit 1
}

list_active_pbs_storages() {
  awk '
    function flush() {
      if (type == "pbs" && id != "" && disabled != "1") print id
    }
    /^[^[:space:]]/ {
      flush()
      type=""; id=""; disabled="0"
      if ($1 == "pbs:") {
        type="pbs"
        id=$2
      }
      next
    }
    type == "pbs" && $1 == "disable" { disabled=$2 }
    END { flush() }
  ' /etc/pve/storage.cfg
}


storage_value() {
  local sid="$1"
  local key="$2"

  awk -v sid="$sid" -v key="$key" '
    /^[^[:space:]]/ {
      inblock=($1=="pbs:" && $2==sid)
      next
    }
    inblock && $1==key {
      $1=""
      sub(/^[[:space:]]+/,"")
      print
      exit
    }
  ' /etc/pve/storage.cfg
}

set_config_value() {
  local key="$1"
  local value="$2"

  sed -i -E "/^[[:space:]]*${key}=/d" /etc/pve-config-backup.conf
  printf '%s=%q\n' "$key" "$value" >> /etc/pve-config-backup.conf
}

echo "============================================================"
echo " PVE Config Backup - Installation"
echo "============================================================"
echo

read -r -p "Kundenname/ID eingeben (leer = intern/kein Kunde): " CUSTOMER_ID

mapfile -t PBS_STORAGES < <(list_active_pbs_storages)

if [[ "${#PBS_STORAGES[@]}" -eq 0 ]]; then
  echo "FEHLER: Kein aktiver PBS-Storage in /etc/pve/storage.cfg gefunden." >&2
  echo "Bitte zuerst den Proxmox Backup Server als Storage in PVE einrichten." >&2
  exit 1
fi

echo
echo "Verfügbare PBS-Storages:"
for i in "${!PBS_STORAGES[@]}"; do
  printf '  %d) %s\n' "$((i + 1))" "${PBS_STORAGES[$i]}"
done

while true; do
  if [[ "${#PBS_STORAGES[@]}" -eq 1 ]]; then
    read -r -p "PBS-Storage auswählen [1]: " PBS_CHOICE
    PBS_CHOICE="${PBS_CHOICE:-1}"
  else
    read -r -p "Nummer des PBS-Storage auswählen: " PBS_CHOICE
  fi

  if [[ "$PBS_CHOICE" =~ ^[0-9]+$ ]]      && (( PBS_CHOICE >= 1 && PBS_CHOICE <= ${#PBS_STORAGES[@]} )); then
    PBS_STORAGE_ID="${PBS_STORAGES[$((PBS_CHOICE - 1))]}"
    break
  fi

  echo "Ungültige Auswahl."
done

echo
echo "Ausgewählte Konfiguration:"
echo "  Kunde:       ${CUSTOMER_ID:-intern/kein Kunde}"
echo "  PBS-Storage: $PBS_STORAGE_ID"
echo

read -r -p "Installation mit diesen Einstellungen starten? [J/n]: " CONFIRM
case "${CONFIRM:-J}" in
  J|j|Y|y|JA|Ja|ja|YES|Yes|yes) ;;
  *)
    echo "Installation abgebrochen."
    exit 0
    ;;
esac

echo
echo "==> Installiere Dateien"

install -o root -g root -m 700   "$SCRIPT_DIR/pve-config-backup.sh"   /usr/local/sbin/pve-config-backup.sh
install -o root -g root -m 700   "$SCRIPT_DIR/pve-config-restore.sh"  /usr/local/sbin/pve-config-restore.sh

install -o root -g root -m 644   "$SCRIPT_DIR/pve-config-backup.service"   /etc/systemd/system/pve-config-backup.service

install -o root -g root -m 644   "$SCRIPT_DIR/pve-config-backup.timer"   /etc/systemd/system/pve-config-backup.timer

if [[ ! -e /etc/pve-config-backup.conf ]]; then
  install -o root -g root -m 600     "$SCRIPT_DIR/pve-config-backup.conf.example"     /etc/pve-config-backup.conf
  echo "Konfiguration angelegt: /etc/pve-config-backup.conf"
else
  echo "Vorhandene Konfiguration wird beibehalten und Auswahl aktualisiert."
fi

set_config_value CUSTOMER_ID "$CUSTOMER_ID"
set_config_value PBS_STORAGE_ID "$PBS_STORAGE_ID"
chmod 600 /etc/pve-config-backup.conf

echo
echo "==> 1/5 Bash-Syntax prüfen"
bash -n /usr/local/sbin/pve-config-backup.sh || fail_install
bash -n /usr/local/sbin/pve-config-restore.sh || fail_install
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
  BACKUP_LOG="$(journalctl -u pve-config-backup.service -n 80 --no-pager 2>/dev/null || true)"

  if grep -qi "namespace not found" <<<"$BACKUP_LOG"; then
    PBS_NAMESPACE="$(storage_value "$PBS_STORAGE_ID" namespace)"
    [[ -n "$PBS_NAMESPACE" ]] || PBS_NAMESPACE="$(storage_value "$PBS_STORAGE_ID" ns)"

    if [[ -n "$PBS_NAMESPACE" ]]; then
      echo
      echo "PBS-Namespace '$PBS_NAMESPACE' ist am PVE-Storage eingetragen,"
      echo "existiert aber auf dem PBS-Datastore noch nicht."
      echo

      read -r -p "Namespace '$PBS_NAMESPACE' jetzt auf dem PBS anlegen und Backup erneut testen? [J/n]: " CREATE_NS

      case "${CREATE_NS:-J}" in
        J|j|Y|y|JA|Ja|ja|YES|Yes|yes)
          PBS_SERVER="$(storage_value "$PBS_STORAGE_ID" server)"
          PBS_DATASTORE="$(storage_value "$PBS_STORAGE_ID" datastore)"
          PBS_USER="$(storage_value "$PBS_STORAGE_ID" username)"
          PBS_FINGERPRINT="$(storage_value "$PBS_STORAGE_ID" fingerprint)"

          repo_server="$PBS_SERVER"
          if [[ "$repo_server" == *:* && "$repo_server" != \[*\] ]]; then
            repo_server="[$repo_server]"
          fi

          PBS_REPOSITORY="${PBS_USER}@${repo_server}:${PBS_DATASTORE}"
          export PBS_PASSWORD_FILE="/etc/pve/priv/storage/${PBS_STORAGE_ID}.pw"
          [[ -n "$PBS_FINGERPRINT" ]] && export PBS_FINGERPRINT

          echo "Lege Namespace '$PBS_NAMESPACE' an..."

          if ! proxmox-backup-client namespace create "$PBS_NAMESPACE"             --repository "$PBS_REPOSITORY"; then
            echo "Namespace konnte mit dem konfigurierten PBS-Benutzer nicht angelegt werden." >&2
            echo "Bitte Namespace auf dem PBS manuell anlegen oder die PBS-Berechtigungen prüfen." >&2
            fail_install
          fi

          echo "Namespace angelegt. Starte Backup erneut..."
          systemctl reset-failed pve-config-backup.service >/dev/null 2>&1 || true

          if ! systemctl start pve-config-backup.service; then
            fail_install
          fi
          ;;
        *)
          echo "Namespace wurde nicht angelegt." >&2
          echo "Bitte auf dem PBS im Datastore '$PBS_DATASTORE' den Namespace '$PBS_NAMESPACE' anlegen" >&2
          echo "oder den Namespace aus dem PVE-Storage entfernen, wenn der Root-Namespace verwendet werden soll." >&2
          fail_install
          ;;
      esac
    else
      fail_install
    fi
  else
    fail_install
  fi
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
echo "==> 5/5 stündlichen Timer aktivieren und prüfen"
systemctl enable --now pve-config-backup.timer || fail_install
systemctl is-enabled --quiet pve-config-backup.timer || fail_install
systemctl is-active --quiet pve-config-backup.timer || fail_install

echo
echo "============================================================"
echo "Installation vollständig erfolgreich."
echo "Kunde: ${CUSTOMER_ID:-intern/kein Kunde}"
echo "PBS-Storage: $PBS_STORAGE_ID"
echo "Sofort-Backup: OK"
echo "Backup-Service: OK"
echo "Timer: aktiviert und aktiv"
echo "============================================================"
echo
systemctl list-timers pve-config-backup.timer --no-pager || true
echo
echo "Konfiguration: /etc/pve-config-backup.conf"
echo "Manueller Check: /usr/local/sbin/pve-config-backup.sh --check"
echo "Restore: /usr/local/sbin/pve-config-restore.sh"
echo "Backup-Log: journalctl -u pve-config-backup.service -n 200 --no-pager"
