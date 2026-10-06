#!/usr/bin/env bash
set -Eeuo pipefail

[[ $EUID -eq 0 ]] || { echo "Als root auf dem PVE-Host ausführen." >&2; exit 1; }
command -v pveversion >/dev/null 2>&1 || { echo "FEHLER: Kein Proxmox VE erkannt." >&2; exit 1; }
command -v pct >/dev/null 2>&1 || { echo "FEHLER: pct fehlt." >&2; exit 1; }
[[ -r /etc/pve/storage.cfg ]] || { echo "FEHLER: /etc/pve/storage.cfg fehlt." >&2; exit 1; }

CTID="${CTID:-$(pvesh get /cluster/nextid)}"
CT_HOSTNAME="${CT_HOSTNAME:-rpi-pbs-gateway}"
CT_CORES="${CT_CORES:-1}"
CT_MEMORY="${CT_MEMORY:-1024}"
CT_SWAP="${CT_SWAP:-512}"
CT_ROOTFS_SIZE="${CT_ROOTFS_SIZE:-8}"
CT_DATA_SIZE="${CT_DATA_SIZE:-64}"
CT_IP_CIDR="${CT_IP_CIDR:-dhcp}"
CT_GATEWAY="${CT_GATEWAY:-}"
CT_DNS_SERVER="${CT_DNS_SERVER:-}"

choose_from_array() {
  local title="$1" default_idx="$2"
  shift 2
  local -a values=("$@")
  ((${#values[@]} > 0)) || return 1
  if ((${#values[@]} == 1)); then
    printf '%s\n' "${values[0]}"
    return 0
  fi
  echo "$title" >&2
  local i
  for i in "${!values[@]}"; do
    printf '  %d) %s\n' "$((i+1))" "${values[$i]}" >&2
  done
  local choice="$default_idx"
  if [[ -r /dev/tty ]]; then
    read -r -p "Auswahl [$default_idx]: " choice </dev/tty || true
    choice="${choice:-$default_idx}"
  fi
  [[ "$choice" =~ ^[0-9]+$ ]] || { echo "Ungültige Auswahl." >&2; exit 1; }
  (( choice >= 1 && choice <= ${#values[@]} )) || { echo "Ungültige Auswahl." >&2; exit 1; }
  printf '%s\n' "${values[$((choice-1))]}"
}

mapfile -t ROOT_STORAGES < <(pvesm status --content rootdir 2>/dev/null | awk 'NR>1 && $3=="active" {print $1}')
((${#ROOT_STORAGES[@]} > 0)) || { echo "FEHLER: Kein aktiver PVE-Storage mit rootdir-Inhalt gefunden." >&2; exit 1; }
CT_STORAGE="${CT_STORAGE:-$(choose_from_array 'Verfügbare CT-Storages:' 1 "${ROOT_STORAGES[@]}")}"
CT_DATA_STORAGE="${CT_DATA_STORAGE:-$CT_STORAGE}"

mapfile -t TEMPLATE_STORAGES < <(pvesm status --content vztmpl 2>/dev/null | awk 'NR>1 && $3=="active" {print $1}')
((${#TEMPLATE_STORAGES[@]} > 0)) || { echo "FEHLER: Kein aktiver Template-Storage (vztmpl) gefunden." >&2; exit 1; }
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-$(choose_from_array 'Verfügbare Template-Storages:' 1 "${TEMPLATE_STORAGES[@]}")}"

mapfile -t BRIDGES < <(awk '/^auto vmbr[0-9]+/ {print $2}' /etc/network/interfaces 2>/dev/null | sort -u)
if ((${#BRIDGES[@]} == 0)); then BRIDGES=(vmbr0); fi
CT_BRIDGE="${CT_BRIDGE:-$(choose_from_array 'Verfügbare Bridges:' 1 "${BRIDGES[@]}")}"

mapfile -t PBS_STORAGES < <(awk '$1=="pbs:" {print $2}' /etc/pve/storage.cfg)
((${#PBS_STORAGES[@]} > 0)) || { echo "FEHLER: Kein PBS-Storage in PVE konfiguriert." >&2; exit 1; }
PVE_PBS_STORAGE="${PVE_PBS_STORAGE:-$(choose_from_array 'Verfügbare PBS-Storages:' 1 "${PBS_STORAGES[@]}")}"

storage_prop() {
  local id="$1" key="$2"
  awk -v id="$id" -v key="$key" '
    $1=="pbs:" {in_block=($2==id); next}
    in_block && $0 ~ /^[^[:space:]]/ {exit}
    in_block && $1==key {$1=""; sub(/^[[:space:]]+/, ""); print; exit}
  ' /etc/pve/storage.cfg
}

PBS_SERVER="$(storage_prop "$PVE_PBS_STORAGE" server)"
PBS_DATASTORE="$(storage_prop "$PVE_PBS_STORAGE" datastore)"
PBS_USERNAME="$(storage_prop "$PVE_PBS_STORAGE" username)"
PBS_FINGERPRINT="$(storage_prop "$PVE_PBS_STORAGE" fingerprint)"
PBS_NAMESPACE="$(storage_prop "$PVE_PBS_STORAGE" namespace)"
PBS_PORT="$(storage_prop "$PVE_PBS_STORAGE" port)"
PBS_PW_FILE="/etc/pve/priv/storage/${PVE_PBS_STORAGE}.pw"

[[ -n "$PBS_SERVER" && -n "$PBS_DATASTORE" && -n "$PBS_USERNAME" ]] || {
  echo "FEHLER: PBS-Storage '$PVE_PBS_STORAGE' unvollständig." >&2; exit 1;
}
[[ -r "$PBS_PW_FILE" ]] || { echo "FEHLER: PBS Credential-Datei fehlt: $PBS_PW_FILE" >&2; exit 1; }

pveam update >/dev/null

HOST_ARCH="$(dpkg --print-architecture 2>/dev/null || true)"
case "$HOST_ARCH" in
  amd64) TEMPLATE_ARCH="amd64" ;;
  arm64) TEMPLATE_ARCH="arm64" ;;
  *)
    echo "FEHLER: Nicht unterstützte PVE-Architektur: ${HOST_ARCH:-unbekannt}" >&2
    exit 1
    ;;
esac

echo "==> PVE-Architektur: $HOST_ARCH"
TEMPLATE="$(pveam available --section system | awk -v arch="$TEMPLATE_ARCH" '$2 ~ ("^debian-13-standard_.*_" arch "\\.tar\\.zst$") {print $2}' | sort -V | tail -1)"
[[ -n "$TEMPLATE" ]] || {
  echo "FEHLER: Kein Debian-13-LXC-Template für Architektur $TEMPLATE_ARCH gefunden." >&2
  exit 1
}
echo "==> Verwende Template: $TEMPLATE"
if ! pveam list "$TEMPLATE_STORAGE" | awk 'NR>1 {print $1}' | grep -q "/$TEMPLATE$"; then
  echo "==> Lade Template: $TEMPLATE"
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
fi
TEMPLATE_VOL="${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}"

if pct status "$CTID" >/dev/null 2>&1; then
  echo "FEHLER: CTID $CTID existiert bereits." >&2
  exit 1
fi

NET0="name=eth0,bridge=${CT_BRIDGE},ip=${CT_IP_CIDR},type=veth"
if [[ "$CT_IP_CIDR" != "dhcp" && -n "$CT_GATEWAY" ]]; then
  NET0+=",gw=${CT_GATEWAY}"
fi

CREATE_ARGS=(
  "$CTID" "$TEMPLATE_VOL"
  --hostname "$CT_HOSTNAME"
  --cores "$CT_CORES"
  --memory "$CT_MEMORY"
  --swap "$CT_SWAP"
  --rootfs "${CT_STORAGE}:${CT_ROOTFS_SIZE}"
  --net0 "$NET0"
  --unprivileged 1
  --onboot 1
  --start 0
)
[[ -n "$CT_DNS_SERVER" ]] && CREATE_ARGS+=(--nameserver "$CT_DNS_SERVER")

echo "==> Erstelle unprivilegierten Debian-13-CT $CTID ($CT_HOSTNAME)"
pct create "${CREATE_ARGS[@]}"

echo "==> Lege separates Staging-Volume mit ${CT_DATA_SIZE}G an"
pct set "$CTID" --mp0 "${CT_DATA_STORAGE}:${CT_DATA_SIZE},mp=/var/lib/openmain-rpi-pbs,backup=0"

pct start "$CTID"
for _ in {1..60}; do
  if pct exec "$CTID" -- true >/dev/null 2>&1; then break; fi
  sleep 1
done
pct exec "$CTID" -- true >/dev/null 2>&1 || { echo "FEHLER: CT startet nicht sauber." >&2; exit 1; }

for _ in {1..60}; do
  if pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1; then break; fi
  sleep 2
done

pct exec "$CTID" -- bash -lc 'apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates'

echo "==> Installiere Gateway-Software im CT"
pct exec "$CTID" -- bash -lc 'curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/gateway/install-gateway-debian.sh -o /root/install-gateway-debian.sh && chmod 700 /root/install-gateway-debian.sh && /root/install-gateway-debian.sh'

REPO_HOST="$PBS_SERVER"
if [[ "$REPO_HOST" == *:* && "$REPO_HOST" != \[*\] ]]; then REPO_HOST="[$REPO_HOST]"; fi
if [[ -n "$PBS_PORT" && "$PBS_PORT" != "8007" ]]; then REPO_HOST="${REPO_HOST}:${PBS_PORT}"; fi
PBS_REPOSITORY="${PBS_USERNAME}@${REPO_HOST}:${PBS_DATASTORE}"

TMP_CONF="$(mktemp)"
trap 'rm -f "$TMP_CONF"' EXIT
cat >"$TMP_CONF" <<EOFCONF
# OpenMain Raspberry Pi -> PBS Gateway CT
PVE_STORAGE_ID="$PVE_PBS_STORAGE"
STAGING_BASE="/var/lib/openmain-rpi-pbs/staging"
PBS_REPOSITORY="$PBS_REPOSITORY"
PBS_PASSWORD_FILE="/etc/openmain/pbs-secret"
PBS_FINGERPRINT="$PBS_FINGERPRINT"
PBS_NAMESPACE="$PBS_NAMESPACE"
PBS_KEYFILE=""
PBS_CHANGE_DETECTION="data"
RESTORE_BASE="/var/lib/openmain-rpi-pbs/restore"
EOFCONF

pct push "$CTID" "$TMP_CONF" /etc/openmain/rpi-pbs-gateway.conf
pct push "$CTID" "$PBS_PW_FILE" /etc/openmain/pbs-secret

pct exec "$CTID" -- chown root:root /etc/openmain/rpi-pbs-gateway.conf /etc/openmain/pbs-secret
pct exec "$CTID" -- chmod 0600 /etc/openmain/rpi-pbs-gateway.conf /etc/openmain/pbs-secret
pct exec "$CTID" -- install -d -o rpi-backup -g rpi-backup -m 0700 /var/lib/openmain-rpi-pbs/staging
pct exec "$CTID" -- install -d -o root -g root -m 0700 /var/lib/openmain-rpi-pbs/restore

set +e
pct exec "$CTID" -- bash -lc 'source /etc/openmain/rpi-pbs-gateway.conf; export PBS_REPOSITORY PBS_PASSWORD_FILE; [[ -n "$PBS_FINGERPRINT" ]] && export PBS_FINGERPRINT; proxmox-backup-client status --repository "$PBS_REPOSITORY"'
PBS_RC=$?
set -e

CT_IP="$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}')"

echo
echo "Gateway-CT erstellt."
echo "CTID:        $CTID"
echo "Hostname:    $CT_HOSTNAME"
echo "IP:          ${CT_IP:-unbekannt}"
echo "PVE Storage: $PVE_PBS_STORAGE"
echo "PBS Repo:    $PBS_REPOSITORY"
echo "Staging:     ${CT_DATA_SIZE}G auf $CT_DATA_STORAGE (backup=0)"
if (( PBS_RC == 0 )); then
  echo "PBS-Test:     OK"
else
  echo "PBS-Test:     FEHLER - Zugang/Berechtigungen prüfen"
fi

echo
echo "Nächster Schritt auf dem Raspberry Pi:"
echo "  GATEWAY_HOST=\"${CT_IP:-<CT-IP>}\""
echo
echo "Public Key des Pi danach in diesem CT eintragen:"
echo "  pct exec $CTID -- nano /home/rpi-backup/.ssh/authorized_keys"
echo
echo "Hinweis: Der PVE-PBS-Credential wurde für den einfachen Start in den CT kopiert."
echo "Für maximale Rechte-Trennung später einen dedizierten PBS-API-Token für RPi-Backups verwenden."
