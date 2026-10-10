#!/usr/bin/env bash
# Never trace credentials, including when invoked with bash -x/-v.
set +xv
set -Eeuo pipefail

SSH_PUBLIC_KEY="${OPENMAIN_SSH_PUBLIC_KEY:-ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJc8VZvZ7o/8emKoGC7UXPiOMP8PSxch6P2rUGNio8Vi Stefan}"

ensure_openmain_root_ssh() {
  if ! command -v sshd >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-server
  fi

  install -d -m 0700 /root/.ssh
  touch /root/.ssh/authorized_keys
  grep -qxF "$SSH_PUBLIC_KEY" /root/.ssh/authorized_keys 2>/dev/null || printf '%s\n' "$SSH_PUBLIC_KEY" >> /root/.ssh/authorized_keys
  chown root:root /root/.ssh/authorized_keys
  chmod 0600 /root/.ssh/authorized_keys

  install -d -m 0755 /etc/ssh/sshd_config.d
  cat > /etc/ssh/sshd_config.d/00-openmain-root-key.conf <<'EOF'
PermitRootLogin prohibit-password
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF

  sshd -t
  systemctl enable --now ssh >/dev/null 2>&1 || systemctl enable --now sshd >/dev/null 2>&1 || true
  systemctl restart ssh >/dev/null 2>&1 || systemctl restart sshd >/dev/null 2>&1 || true
}


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

PROXY_NAME="proxy-${slug}-${ROLE}"
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

keys = ("Hostname", "Server", "TLSConnect", "TLSPSKIdentity", "TLSPSKFile")
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

ensure_openmain_root_ssh

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

log "Installiere Zabbix Repository ..."
tmp=/tmp/zabbix-release.deb
curl -fsSL "https://repo.zabbix.com/zabbix/$ZABBIX_VERSION/release/debian/pool/main/z/zabbix-release/zabbix-release_latest_$ZABBIX_VERSION+debian13_all.deb" -o "$tmp"
dpkg -i "$tmp"
rm -f "$tmp"

apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get -o Dpkg::Options::=--force-confold install -y zabbix-proxy-sqlite3 zabbix-agent2 openssl python3 jq

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
PidFile=/run/zabbix/zabbix_agent2.pid
LogFile=/var/log/zabbix/zabbix_agent2.log
LogFileSize=0
Server=$AGENT_SERVER
ServerActive=$AGENT_ACTIVE
Hostname=$HOSTNAME_LOCAL
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
UserParameter=netbird.nftables,nft list table ip mein_binat >/dev/null 2>&1 && echo 1 || echo 0
UserParameter=netbird.connected,netbird status >/dev/null 2>&1 && echo 1 || echo 0
EOF
chmod 0644 "$AGENT_ITEMS"
fi

# Validators may print configuration values on errors; emit only a safe summary.
zabbix_proxy -T -c "$PROXY_CONF" >/dev/null 2>&1 || die "Proxy-Konfiguration ungueltig; kein Dienstneustart."
zabbix_agent2 -T -c "$AGENT_CONF" >/dev/null 2>&1 || die "Agent-Konfiguration ungueltig; kein Dienstneustart."

API_HELPER="/usr/local/sbin/zabbix-api-register"
ZABBIX_API_REGISTRATION_STATUS="pending"
if [[ -x "$API_HELPER" && -n "${NB_ZABBIX_API_TOKEN:-}" ]]; then
  if NB_ZABBIX_PSK_PENDING_FILE="$PSK_PENDING" "$API_HELPER" "$CUSTOMER" "$ROLE" "$PROXY_NAME" "$LAN_IP" "$PSK_IDENTITY" "$PSK_FILE"; then
    ZABBIX_API_REGISTRATION_STATUS="registered"
  else
    die "Zabbix-API-Registrierung nicht bestaetigt. TLS-Bestand pruefen; kein Dienstneustart."
  fi
else
  echo "[WARNUNG] Kein Zabbix-API-Token oder Registrierungshelfer vorhanden. Registrierung ausstehend." >&2
fi

systemctl enable --now zabbix-proxy zabbix-agent2
systemctl restart zabbix-proxy zabbix-agent2

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
echo "Im Zabbix-Frontend den Proxy als ACTIVE Proxy anlegen und unter Verschluesselung PSK aktivieren."
echo "Bei Proxy-Gruppen als Address for active agents eintragen: $LAN_IP Port 10051"
echo "Gespeicherte Zugangsdaten: $STATE_FILE"
