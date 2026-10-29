#!/usr/bin/env bash
# Never trace credentials, including when invoked with bash -x/-v.
set +xv
set -Eeuo pipefail

ZABBIX_SERVER="${ZABBIX_SERVER:-100.107.91.6}"
ZABBIX_VERSION="${ZABBIX_VERSION:-7.4}"
CUSTOMER="${CUSTOMER:-}"
ROLE="${ROLE:-primary}"
HOSTNAME_LOCAL="${HOSTNAME_LOCAL:-$(hostname -s)}"
ZABBIX_AGENT_GROUP_IPS="${NB_ZABBIX_AGENT_GROUP_IPS:-}"

log(){ printf '\033[1;32m[+]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[FEHLER]\033[0m %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --customer) CUSTOMER="$2"; shift 2 ;;
    --role) ROLE="$2"; shift 2 ;;
    --server) ZABBIX_SERVER="$2"; shift 2 ;;
    --version) ZABBIX_VERSION="$2"; shift 2 ;;
    --hostname) HOSTNAME_LOCAL="$2"; shift 2 ;;
    --agent-group-ips) ZABBIX_AGENT_GROUP_IPS="$2"; shift 2 ;;
    *) die "Unbekannte Option: $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "Bitte als root ausführen."

[[ "$ROLE" == primary || "$ROLE" == backup ]] || die "--role muss primary oder backup sein."
[[ -n "$CUSTOMER" ]] || read -r -p "Firmenname/Kunde: " CUSTOMER

slug=$(printf '%s' "$CUSTOMER" | iconv -f UTF-8 -t ASCII//TRANSLIT 2>/dev/null || printf '%s' "$CUSTOMER")
slug=$(printf '%s' "$slug" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g')
[[ -n "$slug" ]] || slug="kunde"

PROXY_NAME="${NB_ZABBIX_PROXY_NAME:-proxy-${slug}-${ROLE}}"
PSK_IDENTITY="PSK-${PROXY_NAME}"
PSK_FILE="/etc/zabbix/zabbix_proxy.psk"
STATE_DIR="/var/lib/netbird-kundenrouter"
STATE_FILE="$STATE_DIR/zabbix-proxy.env"
INFO_FILE="/etc/netbird-zabbix-info.conf"
PROXY_CONF="/etc/zabbix/zabbix_proxy.conf"
AGENT_CONF="/etc/zabbix/zabbix_agent2.conf"
AGENT_ITEMS="/etc/zabbix/zabbix_agent2.d/netbird-router.conf"
PSK_PENDING="$STATE_DIR/zabbix-psk.pending"
PROXY_EXISTED=0
AGENT_EXISTED=0
ITEMS_EXISTED=0
[[ ! -e "$AGENT_CONF" && ! -L "$AGENT_CONF" ]] || AGENT_EXISTED=1
[[ ! -e "$AGENT_ITEMS" && ! -L "$AGENT_ITEMS" ]] || ITEMS_EXISTED=1

# Read configuration as data (including Include files), never source it as shell.
# Refuse ambiguous/incomplete TLS state before installing or replacing anything.
if [[ -e "$PROXY_CONF" || -L "$PROXY_CONF" ]]; then
  PROXY_EXISTED=1
  details=$(python3 - "$PROXY_CONF" 2>/dev/null <<'PYCONFIG'
import glob
import pathlib
import sys

keys = ("Hostname", "Server", "TLSConnect", "TLSPSKIdentity", "TLSPSKFile", "DBName")
values = {}
seen = set()

def read_config(filename):
    path = pathlib.Path(filename).resolve(strict=True)
    if path in seen:
        raise ValueError("Repeated include")
    seen.add(path)
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key == "Include":
            if not pathlib.Path(value).is_absolute():
                raise ValueError("Ambiguous relative include")
            for included in sorted(glob.glob(value)):
                read_config(included)
        elif key in keys:
            if key in values or not value:
                raise ValueError("Ambiguous configuration")
            values[key] = value

read_config(sys.argv[1])
for key in keys:
    print(values[key])
PYCONFIG
  ) || die "Vorhandene Proxy-/TLS-Konfiguration unvollstaendig oder mehrdeutig. Keine Aenderung."
  mapfile -t config_values <<< "$details"
  [[ "${config_values[0]}" == "$PROXY_NAME" && "${config_values[1]}" == "$ZABBIX_SERVER" ]] \
    || die "Vorhandener Proxy-Name oder Server weicht ab. Keine automatische Ueberschreibung."
  [[ "${config_values[2]}" == psk && "${config_values[4]}" == /* ]] \
    || die "Vorhandene TLS-Konfiguration nicht eindeutig PSK-basiert. Keine Aenderung."
  PSK_IDENTITY="${config_values[3]}"
  PSK_FILE="${config_values[4]}"
  [[ -s "$PSK_FILE" ]] || die "Vorhandene TLS-Konfiguration ohne lesbaren PSK. Kein Ersatzschluessel erzeugt."
elif [[ -e "$PSK_FILE" || -L "$PSK_FILE" || -e "$STATE_FILE" ]]; then
  die "Vorhandener PSK/Zustand ohne Proxy-Konfiguration. TLS-Identitaet zuerst wiederherstellen."
fi

if [[ -e "$PSK_FILE" ]]; then
  existing_psk=$(cat "$PSK_FILE" 2>/dev/null) || die "Vorhandener PSK nicht lesbar."
  [[ "$existing_psk" =~ ^[[:xdigit:]]{32,512}$ && $(( ${#existing_psk} % 2 )) == 0 ]] \
    || die "Vorhandener PSK ungueltig. Kein Ersatzschluessel erzeugt."
  unset existing_psk
fi


# Prefer the same LAN interface that the router's BINAT configuration uses.
# Fallback only for standalone Zabbix helper installations.
LAN_IF="${NB_LAN_INTERFACE:-$(ip -4 route show default | awk 'NR==1{print $5}')}"
[[ -n "$LAN_IF" ]] || die "Kein Kunden-LAN-Interface erkannt."
LAN_IP=$(ip -4 -o addr show dev "$LAN_IF" scope global | awk 'NR==1{split($4,a,"/");print a[1]}')
[[ "$LAN_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Keine gueltige LAN-IPv4 auf $LAN_IF erkannt. Zabbix-Registrierung abgebrochen."
log "Zabbix-Adresse fuer aktive Agents: $LAN_IF / $LAN_IP:10051"

# Optional customer proxy group: the installer must receive all peer LAN IPs.
# Do not guess sibling IPs or use a different customer network.
AGENT_SERVER="127.0.0.1"
AGENT_ACTIVE="127.0.0.1:10051"
if [[ -n "$ZABBIX_AGENT_GROUP_IPS" ]]; then
  IFS=',' read -r -a GROUP_ADDRS <<< "$ZABBIX_AGENT_GROUP_IPS"
  (( ${#GROUP_ADDRS[@]} >= 2 )) || die "Proxy-Gruppe benoetigt mindestens zwei LAN-Adressen."
  seen_local=0
  declare -A addr_seen=()
  AGENT_ACTIVE=""
  for addr in "${GROUP_ADDRS[@]}"; do
    python3 -c 'import ipaddress,sys; ipaddress.IPv4Address(sys.argv[1])' "$addr" >/dev/null 2>&1 \
      || die "Ungueltige Zabbix-Gruppen-LAN-IP: $addr"
    [[ -z "${addr_seen[$addr]:-}" ]] || die "Doppelte Proxy-Gruppen-LAN-IP: $addr"
    addr_seen[$addr]=1
    [[ "$addr" == "$LAN_IP" ]] && seen_local=1
    AGENT_SERVER+=",$addr"
    if [[ -z "$AGENT_ACTIVE" ]]; then
      AGENT_ACTIVE="$addr:10051"
    else
      AGENT_ACTIVE+=";$addr:10051"
    fi
  done
  (( seen_local == 1 )) || die "Eigene LAN-IP $LAN_IP fehlt in --agent-group-ips."
  log "Zabbix Agent-Proxy-Gruppe: $AGENT_ACTIVE"
fi

# SQLite online backup before package upgrades; never remove/reinitialize a DB.
if [[ "$PROXY_EXISTED" == 1 ]]; then
  install -d -m 0700 "$STATE_DIR"
  cp -p "$PROXY_CONF" "$STATE_DIR/zabbix_proxy.conf.previous"
  python3 - "${config_values[5]}" "$STATE_DIR/zabbix-proxy.sqlite3.previous" 2>/dev/null <<'PYBACKUP' || die "SQLite-Sicherung fehlgeschlagen; Paketinstallation abgebrochen."
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from urllib.parse import quote
source, target = map(Path, sys.argv[1:])
if not source.is_absolute():
    raise ValueError('Absolute SQLite path required')
if source.exists():
    fd, temporary = tempfile.mkstemp(dir=target.parent)
    os.close(fd)
    try:
        with sqlite3.connect('file:' + quote(str(source)) + '?mode=ro', uri=True, timeout=10) as original:
            with sqlite3.connect(temporary) as backup:
                original.backup(backup)
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
PYBACKUP
fi

log "Installiere Zabbix Repository ..."
tmp=$(mktemp)
curl -fsSL "https://repo.zabbix.com/zabbix/$ZABBIX_VERSION/release/debian/pool/main/z/zabbix-release/zabbix-release_latest_$ZABBIX_VERSION+debian13_all.deb" -o "$tmp"
dpkg -i "$tmp"
rm -f "$tmp"

apt-get update -y
# Debian maintainer scripts must not start/restart daemons before registration.
# Preserve existing policy-rc.d; accept only a static unconditional denial.
DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l python3 - <<'PYPACKAGES' || die "Paketinstallation/Dienstblockade fehlgeschlagen; keine Registrierung bestaetigt."
import os
from pathlib import Path
import stat
import subprocess
policy = Path('/usr/sbin/policy-rc.d')
managed = b'#!/bin/sh\n# OpenMain temporary package service guard v1\nexit 101\n'
owned = None
try:
    parent = policy.parent.stat()
    if policy.parent.is_symlink() or parent.st_uid != os.geteuid() or parent.st_mode & 0o022:
        raise ValueError()
    if policy.exists() or policy.is_symlink():
        fd = os.open(policy, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            body = stream.read()
        if info.st_uid != os.geteuid() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o022 or not info.st_mode & stat.S_IXUSR or not body.splitlines() or body.splitlines()[0] not in (b'#!/bin/sh', b'#!/bin/bash', b'#!/usr/bin/sh', b'#!/usr/bin/bash'):
            raise ValueError()
        if body == managed:
            owned = info.st_ino
        elif [line.strip() for line in body.decode().splitlines() if line.strip() and not line.lstrip().startswith('#')] not in (['exit 101'], ['exit 101;']):
            raise ValueError()
    else:
        fd = os.open(policy, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o755)
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o755)
            stream.write(managed)
            stream.flush()
            os.fsync(stream.fileno())
            owned = os.fstat(stream.fileno()).st_ino
    subprocess.run(['apt-get', '-o', 'Dpkg::Options::=--force-confold', 'install', '-y',
                    'zabbix-proxy-sqlite3', 'zabbix-agent2', 'openssl', 'python3', 'jq'], check=True)
except Exception:
    raise SystemExit('Paketinstallation gestoppt; vorhandene Dienstpolicy nicht ersetzt. Details unterdrueckt.') from None
finally:
    if owned is not None:
        fd = os.open(policy, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            unchanged = info.st_ino == owned and stream.read() == managed
        if unchanged:
            policy.unlink()
PYPACKAGES

install -d -o zabbix -g zabbix -m 0750 /var/lib/zabbix
install -d -o root -g zabbix -m 0750 /etc/zabbix

if [[ "$PROXY_EXISTED" == 0 ]]; then
  install -d -m 0700 "$STATE_DIR"
  # Keep the marker across failed/ambiguous registration attempts. Never silently
  # attach a newly generated local key to an existing server-side proxy.
  (umask 077; : > "$PSK_PENDING")
  (umask 077; set -o noclobber; openssl rand -hex 32 > "$PSK_FILE")
  chown zabbix:zabbix "$PSK_FILE"
  chmod 600 "$PSK_FILE"
fi

if [[ "$PROXY_EXISTED" == 0 ]]; then
cat > "$PROXY_CONF" <<EOF
ProxyMode=0
Server=$ZABBIX_SERVER
StatsAllowedIP=127.0.0.1,::1,$ZABBIX_SERVER
Hostname=$PROXY_NAME
DBName=/var/lib/zabbix/zabbix_proxy.db
ProxyConfigFrequency=60
DataSenderFrequency=5
LogFile=/var/log/zabbix/zabbix_proxy.log
LogFileSize=0
PidFile=/run/zabbix/zabbix_proxy.pid
SocketDir=/run/zabbix
TLSConnect=psk
TLSPSKIdentity=$PSK_IDENTITY
TLSPSKFile=$PSK_FILE
EOF
chmod 0644 "$PROXY_CONF"
fi

if [[ "$AGENT_EXISTED" == 0 ]]; then
cat > "$AGENT_CONF" <<EOF
# OpenMain managed agent configuration v1
PidFile=/run/zabbix/zabbix_agent2.pid
LogFile=/var/log/zabbix/zabbix_agent2.log
LogFileSize=0
Server=$AGENT_SERVER
ServerActive=$AGENT_ACTIVE
Hostname=${NB_ZABBIX_AGENT_HOST:-$HOSTNAME_LOCAL}
TLSConnect=psk
TLSAccept=psk
TLSPSKIdentity=$PSK_IDENTITY
TLSPSKFile=$PSK_FILE
Include=/etc/zabbix/zabbix_agent2.d/*.conf
ControlSocket=/tmp/agent.sock
EOF
chmod 0644 "$AGENT_CONF"
fi

install -d -m 0755 /etc/zabbix/zabbix_agent2.d
if [[ "$ITEMS_EXISTED" == 0 ]]; then
cat > "$AGENT_ITEMS" <<'EOF'
UserParameter=netbird.service,systemctl is-active --quiet netbird && echo 1 || echo 0
UserParameter=netbird.ipforward,cat /proc/sys/net/ipv4/ip_forward
UserParameter=netbird.nftables,systemctl is-active --quiet openmain-binat && echo 1 || echo 0
UserParameter=openmain.agent.active,echo 1
UserParameter=netbird.connected,netbird status >/dev/null 2>&1 && echo 1 || echo 0
EOF
chmod 0644 "$AGENT_ITEMS"
fi

# Validators may print configuration values on errors; emit only a safe summary.
zabbix_proxy -T -c "$PROXY_CONF" >/dev/null 2>&1 || die "Proxy-Konfiguration ungueltig; kein Dienstneustart."
zabbix_agent2 -T -c "$AGENT_CONF" >/dev/null 2>&1 || die "Agent-Konfiguration ungueltig; kein Dienstneustart."

API_HELPER="${NB_BUNDLE_DIR:+$NB_BUNDLE_DIR/zabbix-api-register.sh}"
API_HELPER="${API_HELPER:-/usr/local/sbin/zabbix-api-register}"
ZABBIX_API_REGISTRATION_STATUS="pending"
if [[ -f "$API_HELPER" && -n "${NB_ZABBIX_API_TOKEN:-}" ]]; then
  if NB_ZABBIX_PSK_PENDING_FILE="$PSK_PENDING" bash "$API_HELPER" "$CUSTOMER" "$ROLE" "$PROXY_NAME" "$LAN_IP" "$PSK_IDENTITY" "$PSK_FILE"; then
    ZABBIX_API_REGISTRATION_STATUS="registered"
  else
    die "Zabbix-API-Registrierung nicht bestaetigt. TLS-Bestand pruefen; kein Dienstneustart."
  fi
else
  echo "[WARNUNG] Kein Zabbix-API-Token oder Registrierungshelfer vorhanden. Registrierung ausstehend." >&2
fi

if [[ "${NB_ZABBIX_REGISTER_HOST:-0}" == 1 ]]; then
  [[ "$ZABBIX_API_REGISTRATION_STATUS" == registered ]] || die "Vollstaendige API-Registrierung erforderlich."
  LAN_NETWORK=$(ip -4 -o addr show dev "$LAN_IF" scope global | awk 'NR==1{print $4}')
  LAN_NETWORK=$(python3 -c 'import ipaddress,sys; print(ipaddress.ip_interface(sys.argv[1]).network)' "$LAN_NETWORK")
  export SYNC_CUSTOMER="$CUSTOMER" SYNC_ROLE="$ROLE" SYNC_HOST="${NB_ZABBIX_AGENT_HOST:-$HOSTNAME_LOCAL}" SYNC_PROXY="$PROXY_NAME"
  export SYNC_IDENTITY="$PSK_IDENTITY" SYNC_PSK_FILE="$PSK_FILE" SYNC_INTERFACE="$LAN_IF" SYNC_NETWORK="$LAN_NETWORK" SYNC_AGENT_CONFIG="$AGENT_CONF"
  python3 "$NB_BUNDLE_DIR/netbird-router/common/zabbix_sync.py" install || die "Zabbix-Abgleich abgebrochen; Fehlerklasse siehe oben. Kein Dienststart."
  install -d -m 0755 /usr/local/lib/openmain-router
  install -m 0644 "$NB_BUNDLE_DIR/netbird-router/common/"{runtime,preflight,zabbix_access,zabbix_registration,zabbix_sync}.py /usr/local/lib/openmain-router/
  cat > /etc/systemd/system/openmain-zabbix-sync.service <<'UNIT'
[Unit]
Description=Update customer proxy LAN addresses and agent failover members
After=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/lib/openmain-router/zabbix_sync.py
UMask=0077
UNIT
  cat > /etc/systemd/system/openmain-zabbix-sync.timer <<'UNIT'
[Unit]
Description=Refresh Zabbix customer proxy group membership
[Timer]
OnBootSec=2min
OnUnitInactiveSec=2min
[Install]
WantedBy=timers.target
UNIT
  systemctl daemon-reload
  systemctl enable --now openmain-zabbix-sync.timer
fi

[[ "$ZABBIX_API_REGISTRATION_STATUS" == registered ]] || die "API-Registrierung ausstehend; kein Zabbix-Dienststart/-neustart. PSK und Wiederaufnahmezustand bleiben erhalten."
systemctl enable --now zabbix-proxy zabbix-agent2
if [[ "$PROXY_EXISTED" == 0 || "$AGENT_EXISTED" == 0 ]]; then
  systemctl restart zabbix-proxy zabbix-agent2
fi

install -d -m 0700 "$STATE_DIR"
umask 077
cat > "$STATE_FILE" <<EOF
ZABBIX_SERVER=$ZABBIX_SERVER
ZABBIX_PROXY_NAME=$PROXY_NAME
ZABBIX_ACTIVE_AGENT_ADDRESS=$LAN_IP
ZABBIX_ACTIVE_AGENT_PORT=10051
ZABBIX_PSK_IDENTITY=$PSK_IDENTITY
ZABBIX_API_REGISTRATION_STATUS=$ZABBIX_API_REGISTRATION_STATUS
EOF
chmod 600 "$STATE_FILE"

{
  printf 'ZABBIX_INFO_PROXY=%q\n' "$PROXY_NAME"
  printf 'ZABBIX_INFO_SERVER=%q\n' "$ZABBIX_SERVER"
  printf 'ZABBIX_INFO_ACTIVE_ADDRESS=%q\n' "$LAN_IP"
  printf 'ZABBIX_INFO_ACTIVE_PORT=10051\n'
  printf 'ZABBIX_INFO_PSK_IDENTITY=%q\n' "$PSK_IDENTITY"
} > "$INFO_FILE"
chmod 0644 "$INFO_FILE"

cat > /etc/profile.d/netbird-zabbix-info.sh <<'EOF'
#!/usr/bin/env bash
[[ $- == *i* ]] || return 0
INFO_FILE="/etc/netbird-zabbix-info.conf"
[[ -r "$INFO_FILE" ]] || return 0
. "$INFO_FILE"

echo
echo "============================================================"
echo " NetBird / Zabbix Router"
echo "============================================================"
printf '%-18s %s\n' "Proxy:" "$ZABBIX_INFO_PROXY"
printf '%-18s %s\n' "Zabbix-Server:" "$ZABBIX_INFO_SERVER:10051"
printf '%-18s %s\n' "Active Agents:" "$ZABBIX_INFO_ACTIVE_ADDRESS:$ZABBIX_INFO_ACTIVE_PORT"
printf '%-18s %s\n' "PSK Identity:" "$ZABBIX_INFO_PSK_IDENTITY"
echo
echo "PSK wird nicht in Terminal oder Logs ausgegeben."
echo "============================================================"
echo
EOF
chmod 0755 /etc/profile.d/netbird-zabbix-info.sh

LOGIN_SOURCE_LINE='source /etc/profile.d/netbird-zabbix-info.sh'
for profile in /root/.profile /home/omadmin/.profile; do
  [[ -d "$(dirname "$profile")" ]] || continue
  touch "$profile"
  grep -qxF "$LOGIN_SOURCE_LINE" "$profile" || echo "$LOGIN_SOURCE_LINE" >> "$profile"
done
chown root:root /root/.profile
chmod 0644 /root/.profile
if id omadmin >/dev/null 2>&1; then
  chown omadmin:omadmin /home/omadmin/.profile
  chmod 0644 /home/omadmin/.profile
fi

log "Zabbix Proxy mit PSK eingerichtet. API-Registrierung: $ZABBIX_API_REGISTRATION_STATUS"
echo "Proxy-Name   : $PROXY_NAME"
echo "Zabbix       : $ZABBIX_SERVER:10051"
echo "Active Agents: $LAN_IP:10051"
echo "Verschluesselung: PSK"
echo "PSK Identity : $PSK_IDENTITY"
echo "PSK          : geschuetzt in der lokalen PSK-Datei (wird nicht ausgegeben)"
echo
