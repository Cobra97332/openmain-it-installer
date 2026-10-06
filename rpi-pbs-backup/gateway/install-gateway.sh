#!/usr/bin/env bash
set -Eeuo pipefail

[[ $EUID -eq 0 ]] || { echo "Als root ausführen." >&2; exit 1; }

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="/etc/openmain/rpi-pbs-gateway.conf"
RECONFIGURE=0

for arg in "$@"; do
  case "$arg" in
    --reconfigure) RECONFIGURE=1 ;;
    -h|--help)
      cat <<'HELP'
OpenMain Raspberry Pi -> PBS Gateway Installer für Proxmox VE

Aufruf:
  install-gateway.sh
  install-gateway.sh --reconfigure

Umgebungsvariablen:
  PVE_PBS_STORAGE       gewünschte PVE-PBS-Storage-ID
  RPI_PBS_STAGING_BASE  Staging-Pfad
  RPI_PBS_RESTORE_BASE  Restore-Pfad
HELP
      exit 0
      ;;
    *) echo "Unbekannter Parameter: $arg" >&2; exit 2 ;;
  esac
done

command -v pveversion >/dev/null 2>&1 || {
  echo "FEHLER: Dieses Installationsscript ist für einen Proxmox-VE-Host vorgesehen." >&2
  exit 1
}
[[ -r /etc/pve/storage.cfg ]] || {
  echo "FEHLER: /etc/pve/storage.cfg nicht lesbar." >&2
  exit 1
}

echo "==> Proxmox VE erkannt: $(pveversion | head -1)"
echo "==> Node: $(hostname -s)"

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  proxmox-backup-client rsync openssh-server sudo

SSH_PUBLIC_KEY="${OPENMAIN_SSH_PUBLIC_KEY:-ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJc8VZvZ7o/8emKoGC7UXPiOMP8PSxch6P2rUGNio8Vi Stefan}"
install -d -m 0700 /root/.ssh
touch /root/.ssh/authorized_keys
grep -qxF "$SSH_PUBLIC_KEY" /root/.ssh/authorized_keys 2>/dev/null || printf '%s\n' "$SSH_PUBLIC_KEY" >> /root/.ssh/authorized_keys
chown root:root /root/.ssh/authorized_keys
chmod 0600 /root/.ssh/authorized_keys
install -d -m 0755 /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/99-openmain-root-key.conf <<'EOF'
PermitRootLogin prohibit-password
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
sshd -t
systemctl enable --now ssh >/dev/null 2>&1 || systemctl enable --now sshd >/dev/null 2>&1 || true
systemctl restart ssh >/dev/null 2>&1 || systemctl restart sshd >/dev/null 2>&1 || true

command -v proxmox-backup-client >/dev/null 2>&1 || {
  echo "FEHLER: proxmox-backup-client konnte nicht installiert werden." >&2
  exit 1
}

install -d -m 0700 /etc/openmain
install -m 0755 "$BASE/rpi-pbs-ingest" /usr/local/sbin/rpi-pbs-ingest
install -m 0755 "$BASE/rpi-pbs-restore" /usr/local/sbin/rpi-pbs-restore

mapfile -t PBS_STORAGES < <(
  awk '$1 == "pbs:" { print $2 }' /etc/pve/storage.cfg
)

((${#PBS_STORAGES[@]} > 0)) || {
  echo "FEHLER: In /etc/pve/storage.cfg ist kein PBS-Storage konfiguriert." >&2
  echo "Zuerst PBS unter Datacenter -> Storage -> Add -> Proxmox Backup Server eintragen." >&2
  exit 1
}

storage_prop() {
  local id="$1" key="$2"
  awk -v id="$id" -v key="$key" '
    $1 == "pbs:" {
      in_block = ($2 == id)
      next
    }
    in_block && $0 ~ /^[^[:space:]]/ { exit }
    in_block && $1 == key {
      $1=""
      sub(/^[[:space:]]+/, "")
      print
      exit
    }
  ' /etc/pve/storage.cfg
}

choose_storage() {
  local requested="${PVE_PBS_STORAGE:-}"
  if [[ -n "$requested" ]]; then
    local s
    for s in "${PBS_STORAGES[@]}"; do
      [[ "$s" == "$requested" ]] && { printf '%s\n' "$s"; return 0; }
    done
    echo "FEHLER: PVE_PBS_STORAGE '$requested' ist kein konfiguriertes PBS-Storage." >&2
    exit 1
  fi

  if ((${#PBS_STORAGES[@]} == 1)); then
    printf '%s\n' "${PBS_STORAGES[0]}"
    return 0
  fi

  echo "Verfügbare PBS-Storages:" >&2
  local i
  for i in "${!PBS_STORAGES[@]}"; do
    printf '  %d) %s\n' "$((i+1))" "${PBS_STORAGES[$i]}" >&2
  done

  local choice="1"
  if [[ -r /dev/tty ]]; then
    read -r -p "PBS-Storage auswählen [1]: " choice </dev/tty || true
    choice="${choice:-1}"
  else
    echo "Kein TTY vorhanden; verwende erstes PBS-Storage." >&2
  fi

  [[ "$choice" =~ ^[0-9]+$ ]] || { echo "Ungültige Auswahl." >&2; exit 1; }
  (( choice >= 1 && choice <= ${#PBS_STORAGES[@]} )) || { echo "Ungültige Auswahl." >&2; exit 1; }
  printf '%s\n' "${PBS_STORAGES[$((choice-1))]}"
}

OLD_PVE_STORAGE=""
if [[ -r "$CONFIG" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG" || true
  OLD_PVE_STORAGE="${PVE_STORAGE_ID:-}"
fi

if [[ -n "$OLD_PVE_STORAGE" && $RECONFIGURE -eq 0 ]]; then
  PBS_STORAGE="$OLD_PVE_STORAGE"
  echo "==> Vorhandene Gateway-Konfiguration wird beibehalten: $PBS_STORAGE"
else
  PBS_STORAGE="$(choose_storage)"
fi

SERVER="$(storage_prop "$PBS_STORAGE" server)"
DATASTORE="$(storage_prop "$PBS_STORAGE" datastore)"
USERNAME="$(storage_prop "$PBS_STORAGE" username)"
FINGERPRINT="$(storage_prop "$PBS_STORAGE" fingerprint)"
NAMESPACE="$(storage_prop "$PBS_STORAGE" namespace)"
PORT="$(storage_prop "$PBS_STORAGE" port)"

[[ -n "$SERVER" ]] || { echo "FEHLER: server fehlt bei Storage $PBS_STORAGE." >&2; exit 1; }
[[ -n "$DATASTORE" ]] || { echo "FEHLER: datastore fehlt bei Storage $PBS_STORAGE." >&2; exit 1; }
[[ -n "$USERNAME" ]] || { echo "FEHLER: username fehlt bei Storage $PBS_STORAGE." >&2; exit 1; }

PW_FILE="/etc/pve/priv/storage/${PBS_STORAGE}.pw"
[[ -r "$PW_FILE" ]] || {
  echo "FEHLER: PVE-Credential-Datei fehlt: $PW_FILE" >&2
  echo "Das PBS-Storage muss auf diesem PVE-Node funktionsfähig eingerichtet sein." >&2
  exit 1
}

REPO_HOST="$SERVER"
if [[ "$REPO_HOST" == *:* && "$REPO_HOST" != \[*\] ]]; then
  REPO_HOST="[$REPO_HOST]"
fi
if [[ -n "$PORT" && "$PORT" != "8007" ]]; then
  REPO_HOST="${REPO_HOST}:${PORT}"
fi
PBS_REPOSITORY="${USERNAME}@${REPO_HOST}:${DATASTORE}"

DEFAULT_STAGING="${RPI_PBS_STAGING_BASE:-/var/lib/openmain-rpi-pbs/staging}"
DEFAULT_RESTORE="${RPI_PBS_RESTORE_BASE:-/var/lib/openmain-rpi-pbs/restore}"

if [[ -r "$CONFIG" ]]; then
  # Bestehende lokale Pfade und optionale Verschlüsselung beim Update beibehalten.
  # shellcheck disable=SC1090
  source "$CONFIG" || true
fi

STAGING_BASE="${RPI_PBS_STAGING_BASE:-${STAGING_BASE:-$DEFAULT_STAGING}}"
RESTORE_BASE="${RPI_PBS_RESTORE_BASE:-${RESTORE_BASE:-$DEFAULT_RESTORE}}"
PBS_KEYFILE="${PBS_KEYFILE:-}"
PBS_CHANGE_DETECTION="${PBS_CHANGE_DETECTION:-data}"

[[ "$STAGING_BASE" == /* ]] || { echo "FEHLER: STAGING_BASE muss absolut sein." >&2; exit 1; }
[[ "$RESTORE_BASE" == /* ]] || { echo "FEHLER: RESTORE_BASE muss absolut sein." >&2; exit 1; }

cat >"$CONFIG" <<EOFCONF
# OpenMain Raspberry Pi -> PBS Gateway auf Proxmox VE
# Automatisch aus PVE-Storage '$PBS_STORAGE' erzeugt.
# chmod 0600

PVE_STORAGE_ID="$PBS_STORAGE"
STAGING_BASE="$STAGING_BASE"
PBS_REPOSITORY="$PBS_REPOSITORY"
PBS_PASSWORD_FILE="$PW_FILE"
PBS_FINGERPRINT="$FINGERPRINT"
PBS_NAMESPACE="$NAMESPACE"

# Optional: clientseitige PBS-Verschlüsselung.
PBS_KEYFILE="$PBS_KEYFILE"

# legacy, data oder metadata
PBS_CHANGE_DETECTION="$PBS_CHANGE_DETECTION"

RESTORE_BASE="$RESTORE_BASE"
EOFCONF
chmod 0600 "$CONFIG"

if ! id rpi-backup >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash rpi-backup
fi
passwd -l rpi-backup >/dev/null 2>&1 || true

install -d -o rpi-backup -g rpi-backup -m 0700 /home/rpi-backup/.ssh
touch /home/rpi-backup/.ssh/authorized_keys
chown rpi-backup:rpi-backup /home/rpi-backup/.ssh/authorized_keys
chmod 0600 /home/rpi-backup/.ssh/authorized_keys

install -d -o rpi-backup -g rpi-backup -m 0700 "$STAGING_BASE"
install -d -o root -g root -m 0700 "$RESTORE_BASE"

cat >/etc/sudoers.d/openmain-rpi-pbs <<'SUDO'
rpi-backup ALL=(root) NOPASSWD: /usr/local/sbin/rpi-pbs-ingest
SUDO
chmod 0440 /etc/sudoers.d/openmain-rpi-pbs
visudo -cf /etc/sudoers.d/openmain-rpi-pbs >/dev/null

systemctl enable --now ssh >/dev/null 2>&1 || systemctl enable --now sshd >/dev/null 2>&1 || true

echo
echo "==> PBS-Konfiguration"
echo "PVE Storage:   $PBS_STORAGE"
echo "Repository:    $PBS_REPOSITORY"
echo "Namespace:     ${NAMESPACE:-<root>}"
echo "Staging:       $STAGING_BASE"
echo "Credential:    $PW_FILE"
echo
df -h "$STAGING_BASE" | tail -1 || true

echo
echo "==> Teste PBS-Zugriff mit der vorhandenen PVE-Storage-Konfiguration"
set +e
(
  export PBS_REPOSITORY PBS_PASSWORD_FILE="$PW_FILE"
  [[ -n "$FINGERPRINT" ]] && export PBS_FINGERPRINT="$FINGERPRINT"
  proxmox-backup-client status --repository "$PBS_REPOSITORY"
)
PBS_TEST_RC=$?
set -e

if (( PBS_TEST_RC != 0 )); then
  echo "WARNUNG: PBS-Verbindungstest fehlgeschlagen. PVE-Storage und Berechtigungen prüfen." >&2
else
  echo "PBS-Verbindung erfolgreich."
fi

cat <<EOFMSG

Gateway auf PVE installiert.

Nächste Schritte:
1. Auf dem Raspberry Pi den Client installieren.
2. Public Key des Pi nach:
   /home/rpi-backup/.ssh/authorized_keys
   eintragen.
3. Auf dem Pi GATEWAY_HOST auf die PVE//NetBird-IP dieses Nodes setzen.
4. Erstes Backup manuell testen.

Konfiguration:
  $CONFIG

Neu konfigurieren:
  $0 --reconfigure

Wichtig:
  Das Staging bleibt lokal auf dem PVE-Host erhalten.
  Bei mehreren Pis ausreichend freien Platz für die aktuellen Datenstände einplanen.
EOFMSG
