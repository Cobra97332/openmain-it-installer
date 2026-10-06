#!/usr/bin/env bash
set -Eeuo pipefail

[[ $# -eq 0 ]] || {
  echo "Dieses Restore-Skript benötigt keine Argumente." >&2
  echo "Einfach ausführen mit: bash restore.sh" >&2
  exit 2
}

[[ $EUID -eq 0 ]] || {
  echo "Bitte als root ausführen." >&2
  exit 1
}

detect_mailcow_dirs() {
  local base
  for base in /opt /srv; do
    [[ -d "$base" ]] || continue
    find "$base" -maxdepth 3 -type f -name mailcow.conf -print 2>/dev/null
  done | while read -r conf; do
    dir="$(dirname "$conf")"
    if [[ -f "$dir/helper-scripts/backup_and_restore.sh" && -f "$dir/docker-compose.yml" ]]; then
      printf '%s\n' "$dir"
    fi
  done | sort -u
}

mapfile -t MAILCOW_DIRS < <(detect_mailcow_dirs)

if [[ "${#MAILCOW_DIRS[@]}" -eq 0 ]]; then
  read -r -p "Mailcow-Verzeichnis [/opt/mailcow-dockerized]: " MAILCOW_DIR
  MAILCOW_DIR="${MAILCOW_DIR:-/opt/mailcow-dockerized}"
else
  echo "Gefundene Mailcow-Installationen:"
  for i in "${!MAILCOW_DIRS[@]}"; do
    printf '  %d) %s\n' "$((i + 1))" "${MAILCOW_DIRS[$i]}"
  done

  while true; do
    read -r -p "Mailcow auswählen [1]: " CHOICE
    CHOICE="${CHOICE:-1}"
    if [[ "$CHOICE" =~ ^[0-9]+$ ]]        && (( CHOICE >= 1 && CHOICE <= ${#MAILCOW_DIRS[@]} )); then
      MAILCOW_DIR="${MAILCOW_DIRS[$((CHOICE - 1))]}"
      break
    fi
    echo "Ungültige Auswahl."
  done
fi

HELPER="$MAILCOW_DIR/helper-scripts/backup_and_restore.sh"
[[ -f "$HELPER" ]] || {
  echo "Mailcow Restore-Helper fehlt: $HELPER" >&2
  exit 1
}

read -r -p "Pfad mit mailcow-* Backup-Ordnern [/var/backups/mailcow/daily]: " BACKUP_LOCATION
BACKUP_LOCATION="${BACKUP_LOCATION:-/var/backups/mailcow/daily}"

[[ -d "$BACKUP_LOCATION" ]] || {
  echo "Backup-Pfad existiert nicht: $BACKUP_LOCATION" >&2
  exit 1
}

if ! find "$BACKUP_LOCATION" -mindepth 1 -maxdepth 1 -type d -name 'mailcow-*' -print -quit | grep -q .; then
  echo "Keine mailcow-* Backup-Ordner in $BACKUP_LOCATION gefunden." >&2
  exit 1
fi

CPU_COUNT="$(nproc 2>/dev/null || echo 1)"
if (( CPU_COUNT > 2 )); then
  DEFAULT_THREADS="$((CPU_COUNT - 2))"
else
  DEFAULT_THREADS=1
fi

read -r -p "Restore-Threads [$DEFAULT_THREADS]: " THREADS
THREADS="${THREADS:-$DEFAULT_THREADS}"

echo
echo "Mailcow:      $MAILCOW_DIR"
echo "Backup-Pfad: $BACKUP_LOCATION"
echo "Threads:      $THREADS"
echo
echo "ACHTUNG: Der offizielle Mailcow-Restore kann Dienste stoppen und Daten ersetzen."
read -r -p "Restore-Auswahl jetzt starten? [j/N]: " CONFIRM

case "${CONFIRM:-N}" in
  J|j|Y|y|JA|Ja|ja|YES|Yes|yes) ;;
  *)
    echo "Restore abgebrochen."
    exit 0
    ;;
esac

cd "$MAILCOW_DIR"
MAILCOW_BACKUP_LOCATION="$BACKUP_LOCATION" THREADS="$THREADS"   bash "$HELPER" restore
