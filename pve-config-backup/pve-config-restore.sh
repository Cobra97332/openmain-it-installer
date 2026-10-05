#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

CONFIG_FILE="${CONFIG_FILE:-/etc/pve-config-backup.conf}"
STORAGE_CFG="/etc/pve/storage.cfg"
RESTORE_BASE="${RESTORE_BASE:-/var/tmp/pve-config-restore}"
ARCHIVE_NAME="pve-config.pxar"

log()  { printf 'PVE-CONFIG-RESTORE: %s\n' "$*"; }
warn() { printf 'PVE-CONFIG-RESTORE WARNUNG: %s\n' "$*" >&2; }
die()  { printf 'PVE-CONFIG-RESTORE FEHLER: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Bitte als root ausführen."
command -v pveversion >/dev/null 2>&1 || die "Kein Proxmox VE erkannt."
command -v proxmox-backup-client >/dev/null 2>&1 || die "proxmox-backup-client fehlt."
command -v jq >/dev/null 2>&1 || die "jq fehlt."
[[ -r "$STORAGE_CFG" ]] || die "$STORAGE_CFG nicht lesbar."

[[ -r "$CONFIG_FILE" ]] && source "$CONFIG_FILE"

storage_value() {
  local sid="$1" key="$2"
  awk -v sid="$sid" -v key="$key" '
    /^[^[:space:]]/ { inblock=($1=="pbs:" && $2==sid); next }
    inblock && $1==key {
      $1=""; sub(/^[[:space:]]+/,""); print; exit
    }
  ' "$STORAGE_CFG"
}

choose_storage() {
  local input i
  local -a ids=()
  mapfile -t ids < <(awk '$1=="pbs:" {print $2}' "$STORAGE_CFG")
  (("${#ids[@]}" > 0)) || die "Kein PBS-Storage gefunden."

  if [[ -n "${PBS_STORAGE_ID:-}" ]]; then
    return
  fi

  if (("${#ids[@]}" == 1)); then
    PBS_STORAGE_ID="${ids[0]}"
    return
  fi

  echo "Verfügbare PBS-Storages:"
  for i in "${!ids[@]}"; do
    printf '  %d) %s\n' "$((i+1))" "${ids[$i]}"
  done

  read -r -p "PBS-Storage auswählen [1]: " input
  input="${input:-1}"
  [[ "$input" =~ ^[0-9]+$ ]] || die "Ungültige Auswahl."
  (( input >= 1 && input <= ${#ids[@]} )) || die "Ungültige Auswahl."
  PBS_STORAGE_ID="${ids[input-1]}"
}

setup_repository() {
  local server
  PBS_SERVER="${PBS_SERVER:-$(storage_value "$PBS_STORAGE_ID" server)}"
  PBS_DATASTORE="${PBS_DATASTORE:-$(storage_value "$PBS_STORAGE_ID" datastore)}"
  PBS_USER="${PBS_USER:-$(storage_value "$PBS_STORAGE_ID" username)}"
  PBS_FINGERPRINT="${PBS_FINGERPRINT:-$(storage_value "$PBS_STORAGE_ID" fingerprint)}"

  if [[ "${PBS_NAMESPACE-__AUTO__}" == "__AUTO__" ]]; then
    PBS_NAMESPACE="$(storage_value "$PBS_STORAGE_ID" namespace)"
    [[ -n "$PBS_NAMESPACE" ]] || PBS_NAMESPACE="$(storage_value "$PBS_STORAGE_ID" ns)"
  fi

  [[ -n "$PBS_SERVER" ]] || die "PBS-Server konnte nicht ermittelt werden."
  [[ -n "$PBS_DATASTORE" ]] || die "PBS-Datastore konnte nicht ermittelt werden."
  [[ -n "$PBS_USER" ]] || die "PBS-Benutzer konnte nicht ermittelt werden."

  server="$PBS_SERVER"
  [[ "$server" == *:* && "$server" != \[*\] ]] && server="[$server]"
  PBS_REPOSITORY="${PBS_REPOSITORY:-${PBS_USER}@${server}:${PBS_DATASTORE}}"

  [[ -n "$PBS_FINGERPRINT" ]] && export PBS_FINGERPRINT
  export PBS_REPOSITORY
}

pbs() {
  local -a cmd=(proxmox-backup-client "$@" --repository "$PBS_REPOSITORY")
  [[ -n "${PBS_NAMESPACE:-}" ]] && cmd+=(--ns "$PBS_NAMESPACE")
  "${cmd[@]}"
}

choose_group() {
  local json input line i=1
  local -a groups=()

  json="$(pbs snapshot list --output-format json)"
  mapfile -t groups < <(
    jq -r '
      map(select(."backup-type"=="host" and (."backup-id"|endswith("-config"))))
      | group_by(."backup-id")
      | map(max_by(."backup-time"))
      | sort_by(."backup-time") | reverse
      | .[]
      | [."backup-id", (."backup-time"|tostring)]
      | @tsv
    ' <<<"$json"
  )

  (("${#groups[@]}" > 0)) || die "Keine PVE-Konfigurationsbackups gefunden."

  echo
  echo "Verfügbare PVE-Konfigurationsbackups:"
  for line in "${groups[@]}"; do
    IFS=$'\t' read -r id epoch <<<"$line"
    printf '  %2d) %-36s %s\n' "$i" "$id" "$(date -d "@$epoch" '+%Y-%m-%d %H:%M:%S')"
    ((i++))
  done

  read -r -p "Backup auswählen [1]: " input
  input="${input:-1}"
  [[ "$input" =~ ^[0-9]+$ ]] || die "Ungültige Auswahl."
  (( input >= 1 && input <= ${#groups[@]} )) || die "Ungültige Auswahl."
  IFS=$'\t' read -r BACKUP_ID _ <<<"${groups[input-1]}"
}

choose_snapshot() {
  local json input line i=1
  local -a snaps=()

  json="$(pbs snapshot list "host/$BACKUP_ID" --output-format json)"
  mapfile -t snaps < <(
    jq -r '
      sort_by(."backup-time") | reverse
      | .[]
      | [."backup-time"|tostring, (.verification.state // "unbekannt")]
      | @tsv
    ' <<<"$json"
  )

  (("${#snaps[@]}" > 0)) || die "Keine Snapshots gefunden."

  echo
  echo "Snapshots:"
  for line in "${snaps[@]}"; do
    IFS=$'\t' read -r epoch verify <<<"$line"
    printf '  %2d) %s  Verify: %s\n' "$i" "$(date -d "@$epoch" '+%Y-%m-%d %H:%M:%S')" "$verify"
    ((i++))
  done

  read -r -p "Snapshot auswählen [1 = neuester]: " input
  input="${input:-1}"
  [[ "$input" =~ ^[0-9]+$ ]] || die "Ungültige Auswahl."
  (( input >= 1 && input <= ${#snaps[@]} )) || die "Ungültige Auswahl."

  IFS=$'\t' read -r epoch SNAPSHOT_VERIFY <<<"${snaps[input-1]}"
  SNAPSHOT="host/$BACKUP_ID/$(date -u -d "@$epoch" '+%Y-%m-%dT%H:%M:%SZ')"
}

restore_to_staging() {
  local stamp
  stamp="$(date '+%Y%m%d-%H%M%S')"
  RESTORE_DIR="$RESTORE_BASE/$BACKUP_ID-$stamp"
  mkdir -p "$RESTORE_DIR"

  log "Stelle $SNAPSHOT zunächst nur nach $RESTORE_DIR wieder her."
  local -a cmd=(proxmox-backup-client restore "$SNAPSHOT" "$ARCHIVE_NAME" "$RESTORE_DIR" --repository "$PBS_REPOSITORY")
  [[ -n "${PBS_NAMESPACE:-}" ]] && cmd+=(--ns "$PBS_NAMESPACE")
  [[ -n "${PBS_KEY_FILE:-}" ]] && cmd+=(--keyfile "$PBS_KEY_FILE")
  "${cmd[@]}"

  echo
  log "Staging-Restore erfolgreich."
  echo "  Snapshot: $SNAPSHOT"
  echo "  Verify:   $SNAPSHOT_VERIFY"
  echo "  Pfad:     $RESTORE_DIR"
  echo
  echo "Es wurde noch keine Datei des laufenden PVE überschrieben."
  echo "Prüfe den Inhalt und folge anschließend HOWTO.md für die gezielte Wiederherstellung."
}

choose_storage
setup_repository

echo
echo "PBS-Verbindung:"
echo "  Storage:    $PBS_STORAGE_ID"
echo "  Repository: $PBS_REPOSITORY"
echo "  Namespace:  ${PBS_NAMESPACE:-<root>}"

choose_group
choose_snapshot
restore_to_staging
