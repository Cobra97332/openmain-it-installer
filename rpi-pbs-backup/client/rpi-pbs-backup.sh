#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

CONFIG="${RPI_PBS_CONFIG:-/etc/openmain/rpi-pbs-backup.conf}"
STATE_DIR="/var/lib/openmain-rpi-backup"
LOCK_FILE="/run/lock/openmain-rpi-pbs-backup.lock"
LOG_TAG="openmain-rpi-pbs"

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; logger -t "$LOG_TAG" -- "$*" 2>/dev/null || true; }
die() { log "FEHLER: $*"; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "Benötigtes Programm fehlt: $1"; }
sanitize() { tr '[:upper:]' '[:lower:]' <<<"$1" | sed -E 's/[^a-z0-9._-]+/-/g; s/^-+//; s/-+$//' | cut -c1-120; }

[[ $EUID -eq 0 ]] || die "Als root ausführen."
[[ -r "$CONFIG" ]] || die "Konfiguration fehlt: $CONFIG"
# shellcheck disable=SC1090
source "$CONFIG"

: "${GATEWAY_HOST:?GATEWAY_HOST fehlt}"
: "${GATEWAY_USER:=rpi-backup}"
: "${GATEWAY_PORT:=22}"
: "${GATEWAY_BASE:=/srv/rpi-pbs-staging}"
: "${SSH_KEY:=/root/.ssh/openmain-rpi-pbs}"
: "${CUSTOMER:=}"
: "${BACKUP_ID:=}"
: "${QUIESCE_DOCKER:=yes}"
: "${QUIESCE_SERVICES:=yes}"

need ssh
need rsync
need flock
need awk
need sed
need sha256sum

mkdir -p "$STATE_DIR" "$(dirname "$LOCK_FILE")"
exec 9>"$LOCK_FILE"
flock -n 9 || die "Es läuft bereits ein Backup."

HOST_RAW="$(hostname -s 2>/dev/null || hostname)"
HOST_ID="$(sanitize "$HOST_RAW")"
[[ -n "$HOST_ID" ]] || HOST_ID="rpi"
if [[ -n "$BACKUP_ID" ]]; then
  ID="$(sanitize "$BACKUP_ID")"
elif [[ -n "$CUSTOMER" ]]; then
  ID="$(sanitize "${CUSTOMER}-${HOST_ID}")"
else
  ID="$HOST_ID"
fi
[[ "$ID" =~ ^[a-z0-9][a-z0-9._-]*$ ]] || die "Ungültige Backup-ID: $ID"

SSH=(ssh -p "$GATEWAY_PORT" -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new "${GATEWAY_USER}@${GATEWAY_HOST}")
RSYNC_SSH="ssh -p ${GATEWAY_PORT} -i ${SSH_KEY} -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"
REMOTE_ROOT="${GATEWAY_BASE%/}/${ID}"
META="$STATE_DIR/metadata"
rm -rf "$META"
mkdir -p "$META/docker"

cleanup_items=()
RUNNING_CONTAINERS=()
STOPPED_SERVICES=()
cleanup() {
  local rc=$?
  set +e
  if ((${#RUNNING_CONTAINERS[@]})); then
    docker start "${RUNNING_CONTAINERS[@]}" >/dev/null 2>&1 || true
  fi
  if ((${#STOPPED_SERVICES[@]})); then
    for ((i=${#STOPPED_SERVICES[@]}-1; i>=0; i--)); do
      systemctl start "${STOPPED_SERVICES[$i]}" >/dev/null 2>&1 || true
    done
  fi
  if (( rc != 0 )); then
    printf '%s\n' "$rc" > "$STATE_DIR/last-status"
  fi
}
trap cleanup EXIT INT TERM

collect_metadata() {
  log "Sammle System-Metadaten"
  date --iso-8601=seconds > "$META/backup-time.txt"
  hostnamectl > "$META/hostnamectl.txt" 2>&1 || hostname > "$META/hostname.txt"
  uname -a > "$META/uname.txt"
  cat /etc/os-release > "$META/os-release" 2>/dev/null || true
  dpkg-query -W -f='${binary:Package}\t${Version}\n' > "$META/packages.tsv" 2>/dev/null || true
  systemctl list-unit-files --state=enabled > "$META/systemd-enabled.txt" 2>/dev/null || true
  systemctl list-units --type=service --state=running > "$META/systemd-running.txt" 2>/dev/null || true
  ip -details addr > "$META/ip-address.txt" 2>/dev/null || true
  ip route show table all > "$META/ip-route.txt" 2>/dev/null || true
  ip -6 route show table all > "$META/ip6-route.txt" 2>/dev/null || true
  lsblk -f > "$META/lsblk.txt" 2>/dev/null || true
  blkid > "$META/blkid.txt" 2>/dev/null || true
  findmnt -R / > "$META/findmnt.txt" 2>/dev/null || true
  cp -a /etc/fstab "$META/fstab" 2>/dev/null || true
  nft list ruleset > "$META/nftables.conf" 2>/dev/null || true
  iptables-save > "$META/iptables.rules" 2>/dev/null || true
  crontab -l > "$META/root-crontab.txt" 2>/dev/null || true
  if command -v docker >/dev/null 2>&1; then
    docker version > "$META/docker/version.txt" 2>&1 || true
    docker ps -a --no-trunc > "$META/docker/containers.txt" 2>&1 || true
    docker images --digests --no-trunc > "$META/docker/images.txt" 2>&1 || true
    docker volume ls > "$META/docker/volumes.txt" 2>&1 || true
    for cid in $(docker ps -aq 2>/dev/null); do
      docker inspect "$cid" > "$META/docker/inspect-${cid}.json" 2>/dev/null || true
    done
  fi
}

remote_prepare() {
  "${SSH[@]}" "mkdir -p '$REMOTE_ROOT/system' '$REMOTE_ROOT/docker/mounts' '$REMOTE_ROOT/metadata' && chmod 700 '$REMOTE_ROOT'"
}

rsync_dir() {
  local src="$1" dst="$2"
  [[ -e "$src" ]] || return 0
  "${SSH[@]}" "mkdir -p '$dst'"
  if [[ -d "$src" ]]; then
    rsync -aHAXx --numeric-ids --delete --delete-excluded -M--fake-super \
      -e "$RSYNC_SSH" "$src"/ "${GATEWAY_USER}@${GATEWAY_HOST}:$dst/"
  else
    # Bind mounts can also be single files. Store them as payload under the hash directory.
    rsync -aHAX --numeric-ids -M--fake-super \
      -e "$RSYNC_SSH" "$src" "${GATEWAY_USER}@${GATEWAY_HOST}:$dst/payload"
  fi
}

sync_system() {
  local -a paths=()
  local p opt
  if declare -p BASE_PATHS >/dev/null 2>&1; then paths+=("${BASE_PATHS[@]}"); fi
  [[ -d /boot ]] && paths+=(/boot)
  [[ -d /boot/firmware ]] && paths+=(/boot/firmware)
  if declare -p EXTRA_PATHS >/dev/null 2>&1; then paths+=("${EXTRA_PATHS[@]}"); fi

  for p in "${paths[@]}"; do
    [[ -e "$p" ]] || continue
    local dst="$REMOTE_ROOT/system$p"
    "${SSH[@]}" "mkdir -p '$dst'"
    local -a args=(-aHAXx --numeric-ids --delete --delete-excluded -M--fake-super -e "$RSYNC_SSH")
    if declare -p RSYNC_EXCLUDES >/dev/null 2>&1; then
      for opt in "${RSYNC_EXCLUDES[@]}"; do args+=(--exclude "$opt"); done
    fi
    if [[ "$p" == "/var/lib" ]] && declare -p VAR_LIB_EXCLUDES >/dev/null 2>&1; then
      for opt in "${VAR_LIB_EXCLUDES[@]}"; do args+=(--exclude "$opt"); done
    fi
    log "Synchronisiere $p"
    rsync "${args[@]}" "$p"/ "${GATEWAY_USER}@${GATEWAY_HOST}:$dst/"
  done
}

docker_mount_inventory() {
  local out="$META/docker/mounts.tsv"
  : > "$out"
  command -v docker >/dev/null 2>&1 || return 0
  local cid name
  while read -r cid; do
    [[ -n "$cid" ]] || continue
    name="$(docker inspect -f '{{.Name}}' "$cid" 2>/dev/null | sed 's#^/##')"
    docker inspect -f '{{range .Mounts}}{{printf "%s\t%s\t%s\n" .Type .Source .Destination}}{{end}}' "$cid" 2>/dev/null | \
      awk -v c="$cid" -v n="$name" 'BEGIN{OFS="\t"} NF>=3 {print c,n,$1,$2,$3}' >> "$out"
  done < <(docker ps -aq 2>/dev/null)
}

skip_docker_source() {
  local src="$1" p
  [[ "$src" == "/var/run/docker.sock" ]] && return 0
  if declare -p DOCKER_SKIP_PREFIXES >/dev/null 2>&1; then
    for p in "${DOCKER_SKIP_PREFIXES[@]}"; do
      [[ "$src" == "$p" || "$src" == "$p/"* ]] && return 0
    done
  fi
  return 1
}

sync_docker_mounts() {
  local inv="$META/docker/mounts.tsv"
  [[ -s "$inv" ]] || return 0
  local src hash dst
  while IFS=$'\t' read -r _cid _name _type src _target; do
    [[ -n "$src" && -e "$src" ]] || continue
    skip_docker_source "$src" && continue
    hash="$(printf '%s' "$src" | sha256sum | awk '{print substr($1,1,20)}')"
    dst="$REMOTE_ROOT/docker/mounts/$hash"
    log "Docker persistent: $src -> $hash"
    rsync_dir "$src" "$dst"
  done < <(sort -u -t$'\t' -k4,4 "$inv")
}

stop_writers() {
  if [[ "$QUIESCE_SERVICES" == "yes" ]] && command -v systemctl >/dev/null 2>&1 && declare -p QUIESCE_SERVICE_NAMES >/dev/null 2>&1; then
    local svc
    for svc in "${QUIESCE_SERVICE_NAMES[@]}"; do
      if systemctl is-active --quiet "$svc" 2>/dev/null; then
        log "Stoppe Dienst kurz für konsistentes Backup: $svc"
        systemctl stop "$svc"
        STOPPED_SERVICES+=("$svc")
      fi
    done
  fi

  if [[ "$QUIESCE_DOCKER" == "yes" ]] && command -v docker >/dev/null 2>&1; then
    mapfile -t RUNNING_CONTAINERS < <(docker ps -q 2>/dev/null)
    if ((${#RUNNING_CONTAINERS[@]})); then
      log "Stoppe ${#RUNNING_CONTAINERS[@]} laufende Docker-Container für zweiten Delta-Lauf"
      docker stop -t 60 "${RUNNING_CONTAINERS[@]}" >/dev/null
    fi
  fi
}

start_writers() {
  if ((${#RUNNING_CONTAINERS[@]})); then
    log "Starte Docker-Container wieder"
    docker start "${RUNNING_CONTAINERS[@]}" >/dev/null
    RUNNING_CONTAINERS=()
  fi
  if ((${#STOPPED_SERVICES[@]})); then
    log "Starte angehaltene Dienste wieder"
    for ((i=${#STOPPED_SERVICES[@]}-1; i>=0; i--)); do
      systemctl start "${STOPPED_SERVICES[$i]}"
    done
    STOPPED_SERVICES=()
  fi
}

sync_metadata() {
  rsync -aHAX --delete -M--fake-super -e "$RSYNC_SSH" "$META"/ \
    "${GATEWAY_USER}@${GATEWAY_HOST}:$REMOTE_ROOT/metadata/"
}

trigger_pbs() {
  log "Starte PBS-Ingest auf dem x86-Gateway"
  "${SSH[@]}" "sudo -n /usr/local/sbin/rpi-pbs-ingest '$ID'"
}

main() {
  [[ -r "$SSH_KEY" ]] || die "SSH-Key fehlt: $SSH_KEY"
  log "Backup startet: Host=$HOST_RAW ID=$ID Gateway=$GATEWAY_HOST"
  "${SSH[@]}" true || die "SSH-Verbindung zum Gateway fehlgeschlagen"
  remote_prepare
  collect_metadata
  docker_mount_inventory

  # First pass while applications are running.
  sync_system
  sync_docker_mounts

  # Brief second pass while known writers are stopped. This keeps downtime short.
  stop_writers
  sync_system
  sync_docker_mounts
  sync_metadata
  start_writers

  trigger_pbs
  date +%s > "$STATE_DIR/last-success"
  printf '0\n' > "$STATE_DIR/last-status"
  log "Backup erfolgreich abgeschlossen"
}

main "$@"
