#!/usr/bin/env bash
set -Eeuo pipefail

[[ $# -eq 0 ]] || {
  echo "Dieser Installer benötigt keine Argumente." >&2
  echo "Einfach ausführen mit: bash install.sh" >&2
  exit 2
}

[[ $EUID -eq 0 ]] || {
  echo "Bitte als root ausführen." >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || {
  echo "Docker wurde nicht gefunden." >&2
  exit 1
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="/etc/mailcow-backup.conf"

fail_install() {
  echo >&2
  echo "FEHLER: Mailcow-Backup-Installation/Test fehlgeschlagen." >&2
  systemctl disable --now mailcow-backup-hourly.timer >/dev/null 2>&1 || true
  systemctl disable --now mailcow-backup-daily.timer >/dev/null 2>&1 || true
  echo "Timer wurden deaktiviert." >&2
  echo "Letzte Logs:" >&2
  journalctl -u mailcow-backup-hourly.service -u mailcow-backup-daily.service -n 120 --no-pager >&2 2>/dev/null || true
  exit 1
}

set_config_value() {
  local key="$1"
  local value="$2"

  sed -i -E "/^[[:space:]]*${key}=/d" "$CONFIG_FILE"
  printf '%s=%q\n' "$key" "$value" >> "$CONFIG_FILE"
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

OLD_MAILCOW_DIR=""
OLD_BACKUP_ROOT=""
OLD_CUSTOMER_ID=""
OLD_RETENTION_CRITICAL_DAYS=""
OLD_RETENTION_FULL_DAYS=""
OLD_THREADS=""

if [[ -r "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
  OLD_MAILCOW_DIR="${MAILCOW_DIR:-}"
  OLD_BACKUP_ROOT="${BACKUP_ROOT:-}"
  OLD_CUSTOMER_ID="${CUSTOMER_ID:-}"
  OLD_RETENTION_CRITICAL_DAYS="${RETENTION_CRITICAL_DAYS:-}"
  OLD_RETENTION_FULL_DAYS="${RETENTION_FULL_DAYS:-}"
  OLD_THREADS="${THREADS:-}"
fi

echo "============================================================"
echo " Mailcow Backup - Installation"
echo "============================================================"
echo

read -r -p "Kundenname/ID [${OLD_CUSTOMER_ID:-leer = intern}]: " CUSTOMER_ID
CUSTOMER_ID="${CUSTOMER_ID:-$OLD_CUSTOMER_ID}"

mapfile -t MAILCOW_DIRS < <(detect_mailcow_dirs)

if [[ "${#MAILCOW_DIRS[@]}" -gt 0 ]]; then
  echo
  echo "Gefundene Mailcow-Installationen:"
  for i in "${!MAILCOW_DIRS[@]}"; do
    printf '  %d) %s\n' "$((i + 1))" "${MAILCOW_DIRS[$i]}"
  done

  DEFAULT_CHOICE=""
  if [[ -n "$OLD_MAILCOW_DIR" ]]; then
    for i in "${!MAILCOW_DIRS[@]}"; do
      if [[ "${MAILCOW_DIRS[$i]}" == "$OLD_MAILCOW_DIR" ]]; then
        DEFAULT_CHOICE="$((i + 1))"
        break
      fi
    done
  fi
  [[ -n "$DEFAULT_CHOICE" ]] || DEFAULT_CHOICE=1

  while true; do
    read -r -p "Mailcow auswählen [$DEFAULT_CHOICE]: " MAILCOW_CHOICE
    MAILCOW_CHOICE="${MAILCOW_CHOICE:-$DEFAULT_CHOICE}"

    if [[ "$MAILCOW_CHOICE" =~ ^[0-9]+$ ]]        && (( MAILCOW_CHOICE >= 1 && MAILCOW_CHOICE <= ${#MAILCOW_DIRS[@]} )); then
      MAILCOW_DIR="${MAILCOW_DIRS[$((MAILCOW_CHOICE - 1))]}"
      break
    fi

    echo "Ungültige Auswahl."
  done
else
  read -r -p "Mailcow-Verzeichnis [${OLD_MAILCOW_DIR:-/opt/mailcow-dockerized}]: " MAILCOW_DIR
  MAILCOW_DIR="${MAILCOW_DIR:-${OLD_MAILCOW_DIR:-/opt/mailcow-dockerized}}"
fi

[[ -f "$MAILCOW_DIR/mailcow.conf" ]] || {
  echo "mailcow.conf fehlt in $MAILCOW_DIR" >&2
  exit 1
}
[[ -f "$MAILCOW_DIR/helper-scripts/backup_and_restore.sh" ]] || {
  echo "backup_and_restore.sh fehlt in $MAILCOW_DIR/helper-scripts" >&2
  exit 1
}

echo
read -r -p "Backup-Ziel [${OLD_BACKUP_ROOT:-/var/backups/mailcow}]: " BACKUP_ROOT
BACKUP_ROOT="${BACKUP_ROOT:-${OLD_BACKUP_ROOT:-/var/backups/mailcow}}"

[[ "$BACKUP_ROOT" = /* ]] || {
  echo "Das Backup-Ziel muss ein absoluter Pfad sein." >&2
  exit 1
}

mkdir -p "$BACKUP_ROOT"

MOUNT_TARGET="$(findmnt -T "$BACKUP_ROOT" -n -o TARGET 2>/dev/null || true)"
MOUNT_SOURCE="$(findmnt -T "$BACKUP_ROOT" -n -o SOURCE 2>/dev/null || true)"
MOUNT_FSTYPE="$(findmnt -T "$BACKUP_ROOT" -n -o FSTYPE 2>/dev/null || true)"

echo
echo "Backup-Ziel liegt auf:"
echo "  Quelle:     ${MOUNT_SOURCE:-unbekannt}"
echo "  Mountpoint: ${MOUNT_TARGET:-unbekannt}"
echo "  Dateisystem:${MOUNT_FSTYPE:+ $MOUNT_FSTYPE}"

if [[ "$MOUNT_TARGET" == "/" ]]; then
  echo
  echo "WARNUNG: Das Backup-Ziel liegt auf dem lokalen Root-Dateisystem."
  echo "Für Disaster-Recovery ist ein externes/gemountetes Backup-Ziel sinnvoller."
  read -r -p "Trotzdem verwenden? [j/N]: " LOCAL_OK
  case "${LOCAL_OK:-N}" in
    J|j|Y|y|JA|Ja|ja|YES|Yes|yes) ;;
    *)
      echo "Installation abgebrochen. Bitte externes Backup-Ziel mounten und erneut starten."
      exit 0
      ;;
  esac
fi

read -r -p "Aufbewahrung stündliche Backups in Tagen [${OLD_RETENTION_CRITICAL_DAYS:-3}]: " RETENTION_CRITICAL_DAYS
RETENTION_CRITICAL_DAYS="${RETENTION_CRITICAL_DAYS:-${OLD_RETENTION_CRITICAL_DAYS:-3}}"

read -r -p "Aufbewahrung tägliche Vollbackups in Tagen [${OLD_RETENTION_FULL_DAYS:-14}]: " RETENTION_FULL_DAYS
RETENTION_FULL_DAYS="${RETENTION_FULL_DAYS:-${OLD_RETENTION_FULL_DAYS:-14}}"

CPU_COUNT="$(nproc 2>/dev/null || echo 1)"
if (( CPU_COUNT > 2 )); then
  AUTO_THREADS="$((CPU_COUNT - 2))"
else
  AUTO_THREADS=1
fi
[[ -n "$OLD_THREADS" ]] && AUTO_THREADS="$OLD_THREADS"

read -r -p "Backup-Threads [$AUTO_THREADS]: " THREADS
THREADS="${THREADS:-$AUTO_THREADS}"

for n in "$RETENTION_CRITICAL_DAYS" "$RETENTION_FULL_DAYS" "$THREADS"; do
  [[ "$n" =~ ^[0-9]+$ ]] || {
    echo "Aufbewahrung und Threads müssen Zahlen sein." >&2
    exit 1
  }
done

(( THREADS >= 1 )) || {
  echo "THREADS muss mindestens 1 sein." >&2
  exit 1
}

echo
echo "Ausgewählte Konfiguration:"
echo "  Kunde:                    ${CUSTOMER_ID:-intern/kein Kunde}"
echo "  Mailcow:                  $MAILCOW_DIR"
echo "  Backup-Ziel:              $BACKUP_ROOT"
echo "  Stündlich:                mysql + crypt + redis"
echo "  Aufbewahrung stündlich:   $RETENTION_CRITICAL_DAYS Tage"
echo "  Täglich:                  vollständiges Backup (all)"
echo "  Aufbewahrung täglich:     $RETENTION_FULL_DAYS Tage"
echo "  Threads:                  $THREADS"
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

install -o root -g root -m 700   "$SCRIPT_DIR/mailcow-backup.sh"   /usr/local/sbin/mailcow-backup.sh

for unit in   mailcow-backup-hourly.service   mailcow-backup-hourly.timer   mailcow-backup-daily.service   mailcow-backup-daily.timer
do
  install -o root -g root -m 644     "$SCRIPT_DIR/$unit"     "/etc/systemd/system/$unit"
done

if [[ ! -e "$CONFIG_FILE" ]]; then
  install -o root -g root -m 600     "$SCRIPT_DIR/mailcow-backup.conf.example"     "$CONFIG_FILE"
else
  echo "Vorhandene Konfiguration wird beibehalten und aktualisiert."
fi

set_config_value CUSTOMER_ID "$CUSTOMER_ID"
set_config_value MAILCOW_DIR "$MAILCOW_DIR"
set_config_value BACKUP_ROOT "$BACKUP_ROOT"
set_config_value RETENTION_CRITICAL_DAYS "$RETENTION_CRITICAL_DAYS"
set_config_value RETENTION_FULL_DAYS "$RETENTION_FULL_DAYS"
set_config_value THREADS "$THREADS"
chmod 600 "$CONFIG_FILE"

echo
echo "==> 1/4 Syntax und systemd prüfen"
bash -n /usr/local/sbin/mailcow-backup.sh || fail_install
bash -n "$0" || fail_install
systemctl daemon-reload || fail_install

if command -v systemd-analyze >/dev/null 2>&1; then
  systemd-analyze verify     /etc/systemd/system/mailcow-backup-hourly.service     /etc/systemd/system/mailcow-backup-hourly.timer     /etc/systemd/system/mailcow-backup-daily.service     /etc/systemd/system/mailcow-backup-daily.timer     >/dev/null 2>&1 || fail_install
fi
echo "OK"

echo
echo "==> 2/4 Mailcow-Backup-Preflight"
/usr/local/sbin/mailcow-backup.sh --check || fail_install
echo "OK"

echo
echo "==> 3/4 SOFORT vollständiges Erstbackup starten"
systemctl reset-failed mailcow-backup-daily.service >/dev/null 2>&1 || true
if ! systemctl start mailcow-backup-daily.service; then
  fail_install
fi

RESULT="$(systemctl show mailcow-backup-daily.service -p Result --value 2>/dev/null || true)"
STATUS="$(systemctl show mailcow-backup-daily.service -p ExecMainStatus --value 2>/dev/null || true)"

if [[ "$RESULT" != "success" || "$STATUS" != "0" ]]; then
  echo "Service Result=$RESULT ExecMainStatus=$STATUS" >&2
  fail_install
fi

echo "Vollbackup erfolgreich."
journalctl -u mailcow-backup-daily.service -n 40 --no-pager || true

echo
echo "==> 4/4 stündlichen und täglichen Timer aktivieren"
systemctl enable --now mailcow-backup-hourly.timer || fail_install
systemctl enable --now mailcow-backup-daily.timer || fail_install
systemctl is-enabled --quiet mailcow-backup-hourly.timer || fail_install
systemctl is-enabled --quiet mailcow-backup-daily.timer || fail_install
systemctl is-active --quiet mailcow-backup-hourly.timer || fail_install
systemctl is-active --quiet mailcow-backup-daily.timer || fail_install

echo
echo "============================================================"
echo "Mailcow Backup vollständig eingerichtet."
echo "Kunde: ${CUSTOMER_ID:-intern/kein Kunde}"
echo "Sofort-Vollbackup: OK"
echo "Stündliches Critical-Backup: aktiv"
echo "Tägliches Vollbackup: aktiv"
echo "============================================================"
echo
systemctl list-timers 'mailcow-backup-*' --no-pager || true
echo
echo "Konfiguration: $CONFIG_FILE"
echo "Backup-Log:"
echo "  journalctl -u mailcow-backup-hourly.service -n 200 --no-pager"
echo "  journalctl -u mailcow-backup-daily.service -n 200 --no-pager"
