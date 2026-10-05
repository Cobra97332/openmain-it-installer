#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

CONFIG_FILE="${CONFIG_FILE:-/etc/pve-config-backup.conf}"
[[ -r "$CONFIG_FILE" ]] && source "$CONFIG_FILE"

PBS_STORAGE_ID="${PBS_STORAGE_ID:-}"
CUSTOMER_ID="${CUSTOMER_ID:-}"
PBS_NAMESPACE="${PBS_NAMESPACE-__AUTO__}"
BACKUP_ID="${BACKUP_ID:-}"
STAGE_BASE="${STAGE_BASE:-/var/lib/pve-config-backup}"
STAGE=""
KEEP_LOCAL_STAGE="${KEEP_LOCAL_STAGE:-0}"
INCLUDE_ROOT_SSH="${INCLUDE_ROOT_SSH:-0}"
LOCK_FILE="${LOCK_FILE:-/run/lock/pve-config-backup.lock}"

log(){ printf 'PVE-CONFIG-BACKUP: %s\n' "$*"; }
die(){ printf 'PVE-CONFIG-BACKUP FEHLER: %s\n' "$*" >&2; exit 1; }

cleanup(){
  local rc=$?
  if [[ "$KEEP_LOCAL_STAGE" != "1" && -n "${STAGE:-}" && -d "$STAGE" ]]; then
    rm -rf -- "$STAGE" || true
  fi
  exit "$rc"
}
trap cleanup EXIT

[[ $EUID -eq 0 ]] || die "Als root ausführen."
command -v pveversion >/dev/null 2>&1 || die "Kein Proxmox VE erkannt."
command -v proxmox-backup-client >/dev/null 2>&1 || die "proxmox-backup-client fehlt."
[[ -r /etc/pve/storage.cfg ]] || die "/etc/pve/storage.cfg nicht lesbar."

list_pbs(){
  awk '
    function flush() {
      if (type == "pbs" && id != "" && disabled != "1") print id
    }
    /^[^[:space:]]/ {
      flush()
      type=""; id=""; disabled="0"
      if ($1=="pbs:") { type="pbs"; id=$2 }
      next
    }
    type=="pbs" && $1=="disable" { disabled=$2 }
    END { flush() }
  ' /etc/pve/storage.cfg
}

storage_value(){
  local sid="$1" key="$2"
  awk -v sid="$sid" -v key="$key" '
    /^[^[:space:]]/ { inblock=($1=="pbs:" && $2==sid); next }
    inblock && $1==key {
      $1=""
      sub(/^[[:space:]]+/,"")
      print
      exit
    }
  ' /etc/pve/storage.cfg
}

if [[ -z "$PBS_STORAGE_ID" ]]; then
  mapfile -t PBS_IDS < <(list_pbs)
  case "${#PBS_IDS[@]}" in
    0)
      die "Kein aktiver PBS-Storage gefunden."
      ;;
    1)
      PBS_STORAGE_ID="${PBS_IDS[0]}"
      ;;
    *)
      printf 'Gefundene PBS-Storages:\n' >&2
      printf '  %s\n' "${PBS_IDS[@]}" >&2
      die "Mehrere PBS-Storages vorhanden. PBS_STORAGE_ID in $CONFIG_FILE setzen."
      ;;
  esac
fi

PBS_SERVER="${PBS_SERVER:-$(storage_value "$PBS_STORAGE_ID" server)}"
PBS_DATASTORE="${PBS_DATASTORE:-$(storage_value "$PBS_STORAGE_ID" datastore)}"
PBS_USER="${PBS_USER:-$(storage_value "$PBS_STORAGE_ID" username)}"
PBS_FINGERPRINT="${PBS_FINGERPRINT:-$(storage_value "$PBS_STORAGE_ID" fingerprint)}"

if [[ "$PBS_NAMESPACE" == "__AUTO__" ]]; then
  PBS_NAMESPACE="$(storage_value "$PBS_STORAGE_ID" namespace)"
  [[ -n "$PBS_NAMESPACE" ]] || PBS_NAMESPACE="$(storage_value "$PBS_STORAGE_ID" ns)"
fi

[[ -n "$PBS_SERVER" ]] || die "PBS-Server konnte nicht ermittelt werden."
[[ -n "$PBS_DATASTORE" ]] || die "PBS-Datastore konnte nicht ermittelt werden."
[[ -n "$PBS_USER" ]] || die "PBS-Benutzer konnte nicht ermittelt werden."

repo_server="$PBS_SERVER"
if [[ "$repo_server" == *:* && "$repo_server" != \[*\] ]]; then
  repo_server="[$repo_server]"
fi

PBS_REPOSITORY="${PBS_REPOSITORY:-${PBS_USER}@${repo_server}:${PBS_DATASTORE}}"

export PBS_PASSWORD_FILE="${PBS_PASSWORD_FILE:-/etc/pve/priv/storage/${PBS_STORAGE_ID}.pw}"
[[ -r "$PBS_PASSWORD_FILE" ]] || die "PBS-Secret nicht lesbar: $PBS_PASSWORD_FILE"

if [[ -n "$PBS_FINGERPRINT" ]]; then
  export PBS_FINGERPRINT
fi

if [[ -n "${PBS_KEY_FILE:-}" ]]; then
  [[ -r "$PBS_KEY_FILE" ]] || die "PBS_KEY_FILE nicht lesbar: $PBS_KEY_FILE"
fi

slug(){
  printf '%s' "$1" |
    tr '[:upper:]' '[:lower:]' |
    sed -E 's/[^a-z0-9._-]+/-/g;s/^-+//;s/-+$//'
}

HOST_ID="$(slug "$(hostname -s)")"
[[ -n "$HOST_ID" ]] || HOST_ID="pve"

if [[ -z "$BACKUP_ID" ]]; then
  if [[ -n "$CUSTOMER_ID" ]]; then
    CUSTOMER_SLUG="$(slug "$CUSTOMER_ID")"
    [[ -n "$CUSTOMER_SLUG" ]] || die "CUSTOMER_ID ist ungültig."
    BACKUP_ID="${CUSTOMER_SLUG}-${HOST_ID}-config"
  else
    BACKUP_ID="${HOST_ID}-config"
  fi
fi

if [[ "${1:-}" == "--check" ]]; then
  log "Host: $(hostname -f 2>/dev/null || hostname)"
  log "PBS Storage: $PBS_STORAGE_ID"
  log "PBS Repository: $PBS_REPOSITORY"
  log "Namespace: ${PBS_NAMESPACE:-<root>}"
  log "Backup-ID: $BACKUP_ID"
  exit 0
fi

if [[ -n "${1:-}" ]]; then
  die "Unbekannter Parameter: $1"
fi

# Nur echte Backup-Läufe benötigen den Lock. Ein --check darf parallel laufen.
install -d -m 755 "$(dirname "$LOCK_FILE")"
exec 9>"$LOCK_FILE"
flock -n 9 || die "Ein anderer Backup-Lauf ist bereits aktiv."

# Altes festes Staging-Verzeichnis aus Versionen vor 2.x erst entfernen,
# nachdem der Lock exklusiv gehalten wird.
install -d -m 700 "$STAGE_BASE"
rm -rf -- "$STAGE_BASE/stage" 2>/dev/null || true

# Jeder Lauf erhält ein eigenes Staging-Verzeichnis. Dadurch kann kein
# fehlgeschlagener Check das Staging eines laufenden Backups löschen.
STAGE="$(mktemp -d "$STAGE_BASE/stage.XXXXXX")"
chmod 700 "$STAGE"

install -d -m 700   "$STAGE/system-info/network"   "$STAGE/system-info/storage"   "$STAGE/system-info/proxmox"

copy_path(){
  local src="$1"
  [[ -e "$src" || -L "$src" ]] || return 0
  cp -a --parents "$src" "$STAGE/"
}

for p in   /etc/pve   /etc/vzdump.conf   /etc/corosync   /etc/ceph   /etc/network   /etc/iproute2   /etc/hosts   /etc/hostname   /etc/resolv.conf   /etc/systemd/network   /etc/nftables.conf   /etc/nftables   /etc/iptables   /etc/sysctl.conf   /etc/sysctl.d   /etc/udev/rules.d   /etc/fstab   /etc/crypttab   /etc/zfs   /etc/lvm   /etc/default   /etc/modprobe.d   /etc/modules   /etc/modules-load.d   /etc/kernel   /etc/apt   /etc/ssh   /etc/systemd/system   /etc/cron.d   /usr/local/sbin   /usr/local/bin
do
  copy_path "$p"
done

if [[ "$INCLUDE_ROOT_SSH" == "1" ]]; then
  copy_path /root/.ssh
fi

{
  echo "created=$(date -Is)"
  echo "hostname=$(hostname -f 2>/dev/null || hostname)"
  echo "pbs_storage_id=$PBS_STORAGE_ID"
  echo "pbs_server=$PBS_SERVER"
  echo "pbs_datastore=$PBS_DATASTORE"
  echo "pbs_namespace=${PBS_NAMESPACE:-<root>}"
  echo "backup_id=$BACKUP_ID"
  echo "customer_id=${CUSTOMER_ID:-<unset>}"
} > "$STAGE/system-info/backup-metadata.txt"

ip -br addr > "$STAGE/system-info/network/ip-address-brief.txt" 2>&1 || true
ip addr show > "$STAGE/system-info/network/ip-address-full.txt" 2>&1 || true
ip route show table all > "$STAGE/system-info/network/ip-route-all.txt" 2>&1 || true
ip -6 route show table all > "$STAGE/system-info/network/ip6-route-all.txt" 2>&1 || true
ip rule show > "$STAGE/system-info/network/ip-rule.txt" 2>&1 || true
ip -6 rule show > "$STAGE/system-info/network/ip6-rule.txt" 2>&1 || true

if command -v bridge >/dev/null 2>&1; then
  bridge link show > "$STAGE/system-info/network/bridge-link.txt" 2>&1 || true
  bridge vlan show > "$STAGE/system-info/network/bridge-vlan.txt" 2>&1 || true
fi

if command -v nft >/dev/null 2>&1; then
  nft list ruleset > "$STAGE/system-info/network/nft-ruleset.txt" 2>&1 || true
fi

pveversion -v > "$STAGE/system-info/proxmox/pveversion.txt" 2>&1 || true
pvesm status > "$STAGE/system-info/storage/pvesm-status.txt" 2>&1 || true
qm list > "$STAGE/system-info/proxmox/qm-list.txt" 2>&1 || true
pct list > "$STAGE/system-info/proxmox/pct-list.txt" 2>&1 || true
lsblk -f > "$STAGE/system-info/storage/lsblk.txt" 2>&1 || true

if command -v zpool >/dev/null 2>&1; then
  zpool status > "$STAGE/system-info/storage/zpool-status.txt" 2>&1 || true
fi

if command -v zfs >/dev/null 2>&1; then
  zfs list > "$STAGE/system-info/storage/zfs-list.txt" 2>&1 || true
fi

ARGS=(
  backup
  "pve-config.pxar:$STAGE"
  --backup-type host
  --backup-id "$BACKUP_ID"
  --repository "$PBS_REPOSITORY"
)

[[ -n "$PBS_NAMESPACE" ]] && ARGS+=(--ns "$PBS_NAMESPACE")
[[ -n "${PBS_KEY_FILE:-}" ]] && ARGS+=(--keyfile "$PBS_KEY_FILE")

log "Sichere $BACKUP_ID auf PBS."
proxmox-backup-client "${ARGS[@]}"
log "Backup erfolgreich."
