#!/usr/bin/env bash
set -Eeuo pipefail

ZABBIX_SERVER="${ZABBIX_SERVER:-100.107.91.6}"
ZABBIX_VERSION="${ZABBIX_VERSION:-7.4}"
CUSTOMER="${CUSTOMER:-}"
ROLE="${ROLE:-primary}"
HOSTNAME_LOCAL="${HOSTNAME_LOCAL:-$(hostname -s)}"

log(){ printf '\033[1;32m[+]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[FEHLER]\033[0m %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --customer) CUSTOMER="$2"; shift 2 ;;
    --role) ROLE="$2"; shift 2 ;;
    --server) ZABBIX_SERVER="$2"; shift 2 ;;
    --version) ZABBIX_VERSION="$2"; shift 2 ;;
    --hostname) HOSTNAME_LOCAL="$2"; shift 2 ;;
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

LAN_IF=$(ip -4 route show default | awk 'NR==1{print $5}')
LAN_IP=""
if [[ -n "$LAN_IF" ]]; then
  LAN_IP=$(ip -4 addr show dev "$LAN_IF" scope global | awk '/inet /{split($2,a,"/"); print a[1]; exit}')
fi
[[ -n "$LAN_IP" ]] || LAN_IP="NICHT_ERKANNT"

log "Installiere Zabbix Repository ..."
tmp=/tmp/zabbix-release.deb
curl -fsSL "https://repo.zabbix.com/zabbix/$ZABBIX_VERSION/release/debian/pool/main/z/zabbix-release/zabbix-release_latest_$ZABBIX_VERSION+debian13_all.deb" -o "$tmp"
dpkg -i "$tmp"
rm -f "$tmp"

apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y zabbix-proxy-sqlite3 zabbix-agent2 openssl

install -d -o zabbix -g zabbix -m 0750 /var/lib/zabbix
install -d -o root -g zabbix -m 0750 /etc/zabbix

if [[ ! -s "$PSK_FILE" ]]; then
  umask 077
  openssl rand -hex 32 > "$PSK_FILE"
fi
chown zabbix:zabbix "$PSK_FILE"
chmod 600 "$PSK_FILE"

cat > /etc/zabbix/zabbix_proxy.conf <<EOF
ProxyMode=0
Server=$ZABBIX_SERVER
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

cat > /etc/zabbix/zabbix_agent2.conf <<EOF
PidFile=/run/zabbix/zabbix_agent2.pid
LogFile=/var/log/zabbix/zabbix_agent2.log
LogFileSize=0
Server=127.0.0.1
ServerActive=127.0.0.1:10051
Hostname=$HOSTNAME_LOCAL
Include=/etc/zabbix/zabbix_agent2.d/*.conf
ControlSocket=/tmp/agent.sock
EOF

install -d -m 0755 /etc/zabbix/zabbix_agent2.d
cat > /etc/zabbix/zabbix_agent2.d/netbird-router.conf <<'EOF'
UserParameter=netbird.service,systemctl is-active --quiet netbird && echo 1 || echo 0
UserParameter=netbird.ipforward,cat /proc/sys/net/ipv4/ip_forward
UserParameter=netbird.nftables,nft list table ip mein_binat >/dev/null 2>&1 && echo 1 || echo 0
UserParameter=netbird.connected,netbird status >/dev/null 2>&1 && echo 1 || echo 0
EOF

API_HELPER="/usr/local/sbin/zabbix-api-register"
if [[ -x "$API_HELPER" ]]; then
  if [[ -n "${NB_ZABBIX_API_TOKEN:-}" ]]; then
    "$API_HELPER" "$CUSTOMER" "$ROLE" "$PROXY_NAME" "$LAN_IP" "$PSK_IDENTITY" "$PSK_FILE"
  else
    echo "[!] NB_ZABBIX_API_TOKEN fehlt - Proxy wird nicht automatisch per Zabbix API registriert." >&2
  fi
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
ZABBIX_PSK=$(cat "$PSK_FILE")
EOF
chmod 600 "$STATE_FILE"

cat > "$INFO_FILE" <<EOF
ZABBIX_INFO_PROXY=$PROXY_NAME
ZABBIX_INFO_SERVER=$ZABBIX_SERVER
ZABBIX_INFO_ACTIVE_ADDRESS=$LAN_IP
ZABBIX_INFO_ACTIVE_PORT=10051
ZABBIX_INFO_PSK_IDENTITY=$PSK_IDENTITY
EOF
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
echo "PSK anzeigen:"
echo "  sudo cat /etc/zabbix/zabbix_proxy.psk"
echo "============================================================"
echo
EOF
chmod 0755 /etc/profile.d/netbird-zabbix-info.sh

LOGIN_SOURCE_LINE='source /etc/profile.d/netbird-zabbix-info.sh'
for profile in /root/.profile /home/omadmin/.profile; do
  touch "$profile"
  grep -qxF "$LOGIN_SOURCE_LINE" "$profile" || echo "$LOGIN_SOURCE_LINE" >> "$profile"
done
chown root:root /root/.profile
chmod 0644 /root/.profile
if id omadmin >/dev/null 2>&1; then
  chown omadmin:omadmin /home/omadmin/.profile
  chmod 0644 /home/omadmin/.profile
fi

log "Zabbix Proxy mit PSK eingerichtet."
echo "Proxy-Name   : $PROXY_NAME"
echo "Zabbix       : $ZABBIX_SERVER:10051"
echo "Active Agents: $LAN_IP:10051"
echo "Verschluesselung: PSK"
echo "PSK Identity : $PSK_IDENTITY"
echo "PSK          : $(cat "$PSK_FILE")"
echo
echo "Im Zabbix-Frontend den Proxy als ACTIVE Proxy anlegen und unter Verschluesselung PSK aktivieren."
echo "Bei Proxy-Gruppen als Address for active agents eintragen: $LAN_IP Port 10051"
echo "Gespeicherte Zugangsdaten: $STATE_FILE"
