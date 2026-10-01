#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

CONFIG_FILE="${CONFIG_FILE:-/etc/patchmon-proxmox.env}"
STATE_DIR="${STATE_DIR:-/var/lib/patchmon-proxmox}"
LOG_FILE="${LOG_FILE:-/var/log/patchmon-proxmox.log}"

log() {
  printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG_FILE"
}

die() {
  log "FEHLER: $*"
  exit 1
}

[[ $EUID -eq 0 ]] || die "Als root auf dem Proxmox-Host ausführen."
[[ -r "$CONFIG_FILE" ]] || die "Konfiguration fehlt: $CONFIG_FILE"

# shellcheck disable=SC1090
source "$CONFIG_FILE"

: "${PATCHMON_URL:?PATCHMON_URL fehlt}"
: "${AUTO_ENROLLMENT_KEY:?AUTO_ENROLLMENT_KEY fehlt}"
: "${AUTO_ENROLLMENT_SECRET:?AUTO_ENROLLMENT_SECRET fehlt}"

PATCHMON_URL="${PATCHMON_URL%/}"
ENABLE_LXC="${ENABLE_LXC:-true}"
ENABLE_LINUX_VMS="${ENABLE_LINUX_VMS:-true}"
ENABLE_WINDOWS_VMS="${ENABLE_WINDOWS_VMS:-true}"
ENABLE_FREEBSD_VMS="${ENABLE_FREEBSD_VMS:-true}"
FORCE_INSTALL="${FORCE_INSTALL:-false}"
DRY_RUN="${DRY_RUN:-false}"

mkdir -p "$STATE_DIR"
touch "$LOG_FILE"
chmod 700 "$STATE_DIR"
chmod 600 "$LOG_FILE"

for cmd in curl jq pct qm pvesh; do
  command -v "$cmd" >/dev/null 2>&1 || die "Benötigter Befehl fehlt: $cmd"
done

NODE="$(hostname -s)"

api_reachable() {
  curl -fsS --connect-timeout 8 "$PATCHMON_URL/" >/dev/null 2>&1
}

guest_linux_has_agent_lxc() {
  pct exec "$1" -- test -f /etc/patchmon/config.yml >/dev/null 2>&1
}

guest_unix_has_agent_vm() {
  local out
  out="$(qm guest exec "$1" -- /bin/sh -c 'test -f /etc/patchmon/config.yml && echo PATCHMON_INSTALLED || true' 2>/dev/null || true)"
  grep -q 'PATCHMON_INSTALLED' <<<"$out"
}

guest_windows_has_agent_vm() {
  local out
  out="$(qm guest exec "$1" -- powershell.exe -NoProfile -NonInteractive -Command "if (Test-Path 'C:\\ProgramData\\PatchMon\\config.yml') { Write-Output PATCHMON_INSTALLED }" 2>/dev/null || true)"
  grep -q 'PATCHMON_INSTALLED' <<<"$out"
}

enroll_host() {
  local kind="$1" id="$2" name="$3" os="$4" state="$5"
  local machine_id="proxmox:${NODE}:${kind}:${id}"
  local payload tmp http

  if [[ -s "$state" ]]; then
    log "$kind $id ($name): vorhandene Enrollment-Credentials werden wiederverwendet"
    return 0
  fi

  payload="$(jq -n     --arg friendly "$name"     --arg machine "$machine_id"     --arg vmid "$id"     --arg node "$NODE"     --arg kind "$kind"     --arg os "$os"     '{friendly_name:$friendly,machine_id:$machine,metadata:{vmid:$vmid,proxmox_node:$node,guest_type:$kind,os_family:$os}}')"

  if [[ "$DRY_RUN" == "true" ]]; then
    log "$kind $id ($name): DRY-RUN – würde bei PatchMon registriert"
    return 10
  fi

  tmp="$(mktemp)"
  http="$(curl -sS --connect-timeout 10 --max-time 30     -o "$tmp" -w '%{http_code}'     -X POST "$PATCHMON_URL/api/v1/auto-enrollment/enroll"     -H "X-Auto-Enrollment-Key: $AUTO_ENROLLMENT_KEY"     -H "X-Auto-Enrollment-Secret: $AUTO_ENROLLMENT_SECRET"     -H 'Content-Type: application/json'     --data "$payload" || true)"

  if [[ "$http" != "201" ]]; then
    log "$kind $id ($name): Enrollment fehlgeschlagen (HTTP $http): $(tr '\n' ' ' < "$tmp")"
    rm -f "$tmp"
    return 1
  fi

  jq '{api_id:.host.api_id,api_key:.host.api_key}' "$tmp" > "$state"
  chmod 600 "$state"
  rm -f "$tmp"

  [[ -n "$(jq -r '.api_id // empty' "$state")" && -n "$(jq -r '.api_key // empty' "$state")" ]] || {
    rm -f "$state"
    log "$kind $id ($name): PatchMon-Antwort enthielt keine Host-Credentials"
    return 1
  }

  log "$kind $id ($name): in PatchMon registriert"
}

install_lxc() {
  local id="$1" name="$2" state="$STATE_DIR/lxc-$id.json"
  local api_id api_key cmd

  if guest_linux_has_agent_lxc "$id"; then
    rm -f "$state"
    log "LXC $id ($name): PatchMon bereits installiert"
    return
  fi

  enroll_host "lxc" "$id" "$name" "linux" "$state" || {
    [[ "$DRY_RUN" == "true" ]] && return
    return 1
  }
  [[ "$DRY_RUN" == "true" ]] && return

  api_id="$(jq -r '.api_id' "$state")"
  api_key="$(jq -r '.api_key' "$state")"

  cmd="set -e
if ! command -v curl >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y curl;
  elif command -v dnf >/dev/null 2>&1; then dnf install -y curl;
  elif command -v yum >/dev/null 2>&1; then yum install -y curl;
  elif command -v apk >/dev/null 2>&1; then apk add --no-cache curl;
  else echo 'curl fehlt und kein unterstützter Paketmanager gefunden' >&2; exit 20; fi
fi
curl -fsSL '$PATCHMON_URL/api/v1/hosts/install$([[ "$FORCE_INSTALL" == "true" ]] && printf '?force=true')' -H 'X-API-ID: $api_id' -H 'X-API-KEY: $api_key' | sh"

  if pct exec "$id" -- /bin/sh -c "$cmd" >>"$LOG_FILE" 2>&1; then
    rm -f "$state"
    log "LXC $id ($name): PatchMon erfolgreich installiert"
  else
    log "LXC $id ($name): Installation fehlgeschlagen – Credentials bleiben für Retry gespeichert"
    return 1
  fi
}

detect_vm_os() {
  local id="$1" ostype out
  ostype="$(qm config "$id" 2>/dev/null | awk -F': ' '/^ostype:/ {print $2; exit}')"

  case "$ostype" in
    win*) echo windows; return ;;
    l26)  echo linux; return ;;
  esac

  out="$(qm guest exec "$id" -- /bin/sh -c 'uname -s 2>/dev/null || true' 2>/dev/null || true)"
  if grep -qi 'FreeBSD' <<<"$out"; then
    echo freebsd
  elif grep -qi 'Linux' <<<"$out"; then
    echo linux
  else
    out="$(qm guest exec "$id" -- powershell.exe -NoProfile -NonInteractive -Command '$PSVersionTable.PSVersion.ToString()' 2>/dev/null || true)"
    grep -q 'exitcode' <<<"$out" && echo windows || echo unknown
  fi
}

install_unix_vm() {
  local id="$1" name="$2" os="$3" state="$STATE_DIR/vm-$id.json"
  local api_id api_key query cmd

  if guest_unix_has_agent_vm "$id"; then
    rm -f "$state"
    log "VM $id ($name/$os): PatchMon bereits installiert"
    return
  fi

  enroll_host "vm" "$id" "$name" "$os" "$state" || {
    [[ "$DRY_RUN" == "true" ]] && return
    return 1
  }
  [[ "$DRY_RUN" == "true" ]] && return

  api_id="$(jq -r '.api_id' "$state")"
  api_key="$(jq -r '.api_key' "$state")"

  if [[ "$os" == "freebsd" ]]; then
    query="?os=freebsd"
    cmd="set -e
command -v curl >/dev/null 2>&1 || pkg install -y curl
curl -fsSL '$PATCHMON_URL/api/v1/hosts/install$query' -H 'X-API-ID: $api_id' -H 'X-API-KEY: $api_key' | sh"
  else
    query=""
    [[ "$FORCE_INSTALL" == "true" ]] && query="?force=true"
    cmd="set -e
if ! command -v curl >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y curl;
  elif command -v dnf >/dev/null 2>&1; then dnf install -y curl;
  elif command -v yum >/dev/null 2>&1; then yum install -y curl;
  elif command -v apk >/dev/null 2>&1; then apk add --no-cache curl;
  else echo 'curl fehlt und kein unterstützter Paketmanager gefunden' >&2; exit 20; fi
fi
curl -fsSL '$PATCHMON_URL/api/v1/hosts/install$query' -H 'X-API-ID: $api_id' -H 'X-API-KEY: $api_key' | sh"
  fi

  if qm guest exec "$id" -- /bin/sh -c "$cmd" >>"$LOG_FILE" 2>&1; then
    rm -f "$state"
    log "VM $id ($name/$os): PatchMon erfolgreich installiert"
  else
    log "VM $id ($name/$os): Installation fehlgeschlagen – Credentials bleiben für Retry gespeichert"
    return 1
  fi
}

install_windows_vm() {
  local id="$1" name="$2" state="$STATE_DIR/vm-$id.json"
  local api_id api_key ps

  if guest_windows_has_agent_vm "$id"; then
    rm -f "$state"
    log "VM $id ($name/windows): PatchMon bereits installiert"
    return
  fi

  enroll_host "vm" "$id" "$name" "windows" "$state" || {
    [[ "$DRY_RUN" == "true" ]] && return
    return 1
  }
  [[ "$DRY_RUN" == "true" ]] && return

  api_id="$(jq -r '.api_id' "$state")"
  api_key="$(jq -r '.api_key' "$state")"

  ps="$ErrorActionPreference='Stop'; $h=@{'X-API-ID'='$api_id';'X-API-KEY'='$api_key'}; $f=Join-Path $env:TEMP 'patchmon-install.ps1'; Invoke-WebRequest -Uri '$PATCHMON_URL/api/v1/hosts/install?os=windows' -Headers $h -UseBasicParsing -OutFile $f; & $f"

  if qm guest exec "$id" -- powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$ps" >>"$LOG_FILE" 2>&1; then
    rm -f "$state"
    log "VM $id ($name/windows): PatchMon erfolgreich installiert"
  else
    log "VM $id ($name/windows): Installation fehlgeschlagen – Credentials bleiben für Retry gespeichert"
    return 1
  fi
}

process_lxc() {
  [[ "$ENABLE_LXC" == "true" ]] || return
  local id status name
  while read -r id status name _; do
    [[ "$id" == "VMID" || -z "$id" ]] && continue
    [[ "$status" == "running" ]] || { log "LXC $id ($name): gestoppt, übersprungen"; continue; }
    install_lxc "$id" "$name" || true
  done < <(pct list)
}

process_vms() {
  local id name status os
  while read -r id name status _; do
    [[ "$id" == "VMID" || -z "$id" ]] && continue
    [[ "$status" == "running" ]] || { log "VM $id ($name): gestoppt, übersprungen"; continue; }

    if ! qm agent "$id" ping >/dev/null 2>&1; then
      log "VM $id ($name): QEMU Guest Agent nicht erreichbar, übersprungen"
      continue
    fi

    os="$(detect_vm_os "$id")"
    case "$os" in
      linux)
        [[ "$ENABLE_LINUX_VMS" == "true" ]] && install_unix_vm "$id" "$name" linux || true
        ;;
      freebsd)
        [[ "$ENABLE_FREEBSD_VMS" == "true" ]] && install_unix_vm "$id" "$name" freebsd || true
        ;;
      windows)
        [[ "$ENABLE_WINDOWS_VMS" == "true" ]] && install_windows_vm "$id" "$name" || true
        ;;
      *)
        log "VM $id ($name): Gastbetriebssystem nicht erkannt, übersprungen"
        ;;
    esac
  done < <(qm list)
}

log "===== PatchMon Proxmox Auto-Deployment gestartet auf $NODE ====="

if ! api_reachable; then
  log "WARNUNG: $PATCHMON_URL ist vom PVE derzeit nicht erreichbar. Enrollment kann fehlschlagen."
fi

process_lxc
process_vms

log "===== PatchMon Proxmox Auto-Deployment beendet ====="
