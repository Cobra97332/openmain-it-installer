#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

CONFIG_FILE="${CONFIG_FILE:-/etc/mailcow-backup.conf}"
LOCK_FILE="${LOCK_FILE:-/run/lock/mailcow-backup.lock}"

log(){ printf 'MAILCOW-BACKUP: %s\n' "$*"; }
die(){ printf 'MAILCOW-BACKUP FEHLER: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Als root ausführen."
[[ -r "$CONFIG_FILE" ]] || die "Konfiguration fehlt: $CONFIG_FILE"
# shellcheck disable=SC1090
source "$CONFIG_FILE"

MAILCOW_DIR="${MAILCOW_DIR:-/opt/mailcow-dockerized}"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/mailcow}"
RETENTION_CRITICAL_DAYS="${RETENTION_CRITICAL_DAYS:-3}"
RETENTION_FULL_DAYS="${RETENTION_FULL_DAYS:-14}"
THREADS="${THREADS:-1}"
CUSTOMER_ID="${CUSTOMER_ID:-}"

HELPER="$MAILCOW_DIR/helper-scripts/backup_and_restore.sh"

check_number(){
  [[ "$1" =~ ^[0-9]+$ ]] || die "$2 muss eine Zahl sein."
}

preflight(){
  [[ -d "$MAILCOW_DIR" ]] || die "Mailcow-Verzeichnis nicht gefunden: $MAILCOW_DIR"
  [[ -f "$MAILCOW_DIR/mailcow.conf" ]] || die "mailcow.conf fehlt in $MAILCOW_DIR"
  [[ -f "$MAILCOW_DIR/docker-compose.yml" ]] || die "docker-compose.yml fehlt in $MAILCOW_DIR"
  [[ -x "$HELPER" || -f "$HELPER" ]] || die "Mailcow Backup/Restore Helper fehlt: $HELPER"
  command -v docker >/dev/null 2>&1 || die "docker fehlt."
  docker info >/dev/null 2>&1 || die "Docker ist nicht erreichbar."

  check_number "$RETENTION_CRITICAL_DAYS" RETENTION_CRITICAL_DAYS
  check_number "$RETENTION_FULL_DAYS" RETENTION_FULL_DAYS
  check_number "$THREADS" THREADS
  (( THREADS >= 1 )) || die "THREADS muss mindestens 1 sein."

  mkdir -p "$BACKUP_ROOT"
  chmod 700 "$BACKUP_ROOT"

  mkdir -p "$BACKUP_ROOT/hourly" "$BACKUP_ROOT/daily"
  chmod 755 "$BACKUP_ROOT/hourly" "$BACKUP_ROOT/daily"

  local real_mailcow real_backup
  real_mailcow="$(readlink -f "$MAILCOW_DIR")"
  real_backup="$(readlink -f "$BACKUP_ROOT")"

  if [[ "$real_backup" == "$real_mailcow"/* ]]; then
    die "BACKUP_ROOT darf nicht innerhalb des Mailcow-Verzeichnisses liegen."
  fi
}

latest_backup_dir(){
  local target="$1"
  find "$target" -mindepth 1 -maxdepth 1 -type d -name 'mailcow-*'     -printf '%T@ %p\n' 2>/dev/null     | sort -nr     | head -1     | cut -d' ' -f2-
}

write_metadata(){
  local dir="$1"
  {
    echo "created=$(date -Is)"
    echo "hostname=$(hostname -f 2>/dev/null || hostname)"
    echo "customer_id=${CUSTOMER_ID:-<unset>}"
    echo "mailcow_dir=$MAILCOW_DIR"
    echo "backup_root=$BACKUP_ROOT"
    echo "threads=$THREADS"
    echo "docker_version=$(docker --version 2>/dev/null || true)"
    if [[ -f "$MAILCOW_DIR/mailcow.conf" ]]; then
      grep -E '^(MAILCOW_HOSTNAME|COMPOSE_PROJECT_NAME|MAILDIR_SUB)=' "$MAILCOW_DIR/mailcow.conf" 2>/dev/null || true
    fi
  } > "$dir/openmain-backup-metadata.txt"
}

archive_install_config(){
  local dir="$1"

  tar     --acls     --xattrs     --numeric-owner     --exclude='./.git'     -C "$MAILCOW_DIR"     -czf "$dir/mailcow-install-config.tar.gz"     .

  tar -tzf "$dir/mailcow-install-config.tar.gz" >/dev/null
}

run_backup(){
  local profile="$1"
  local target retention
  shift

  case "$profile" in
    critical)
      target="$BACKUP_ROOT/hourly"
      retention="$RETENTION_CRITICAL_DAYS"
      ;;
    full)
      target="$BACKUP_ROOT/daily"
      retention="$RETENTION_FULL_DAYS"
      ;;
    *)
      die "Unbekanntes Profil: $profile"
      ;;
  esac

  log "Profil: $profile"
  log "Mailcow: $MAILCOW_DIR"
  log "Ziel: $target"
  log "Aufbewahrung: $retention Tage"
  log "Threads: $THREADS"

  cd "$MAILCOW_DIR"

  MAILCOW_BACKUP_LOCATION="$target"   THREADS="$THREADS"     "$HELPER" backup "$@" --delete-days "$retention"

  local latest
  latest="$(latest_backup_dir "$target")"
  [[ -n "$latest" && -d "$latest" ]] || die "Nach dem Backup wurde kein mailcow-* Verzeichnis gefunden."

  write_metadata "$latest"

  if [[ "$profile" == "full" ]]; then
    log "Archiviere zusätzlich die Mailcow-Installationskonfiguration."
    archive_install_config "$latest"
  fi

  log "Backup erfolgreich: $latest"
}

preflight

case "${1:-}" in
  --check|check)
    log "Preflight erfolgreich."
    log "Mailcow: $MAILCOW_DIR"
    log "Backup-Ziel: $BACKUP_ROOT"
    log "Freier Speicher:"
    df -h "$BACKUP_ROOT" | tail -n +1
    exit 0
    ;;
  critical)
    PROFILE="critical"
    COMPONENTS=(mysql crypt redis)
    ;;
  full)
    PROFILE="full"
    COMPONENTS=(all)
    ;;
  *)
    cat >&2 <<EOF
Verwendung:
  $0 --check
  $0 critical
  $0 full
EOF
    exit 2
    ;;
esac

install -d -m 755 "$(dirname "$LOCK_FILE")"
exec 9>"$LOCK_FILE"

if ! flock -n 9; then
  log "Ein anderer Mailcow-Backup-Lauf ist bereits aktiv. Dieser Lauf wird übersprungen."
  exit 0
fi

run_backup "$PROFILE" "${COMPONENTS[@]}"
