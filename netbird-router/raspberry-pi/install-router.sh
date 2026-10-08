#!/usr/bin/env bash
set -Eeuo pipefail

VERSION="3.0"
COMMON_ROUTER_URL="https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/router-install.sh"
ZABBIX_PROXY_URL="https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/zabbix-proxy-install.sh"
ZABBIX_API_URL_FILE="https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/zabbix-api-register.sh"

MGMT_URL="${NB_MANAGEMENT_URL:-https://netbird.openmain-it.de}"
CUSTOMER="${NB_CUSTOMER_NAME:-}"
ROLE="${NB_ROLE:-primary}"
SETUP_KEY="${NB_SETUP_KEY:-}"
API_TOKEN="${NB_API_TOKEN:-}"
ZABBIX_SERVER="${NB_ZABBIX_SERVER:-100.107.91.6}"
ZABBIX_API_TOKEN="${NB_ZABBIX_API_TOKEN:-}"
ZABBIX_API_ENDPOINT="${NB_ZABBIX_API_URL:-https://zabbix.openmain-it.de/api_jsonrpc.php}"
LAN_IF="${NB_LAN_INTERFACE:-}"
HOSTNAME_LOCAL="${NB_HOSTNAME:-$(hostname -s)}"
ZABBIX_ENABLED="${NB_ZABBIX_ENABLED:-1}"
METRICS_ENABLED="${NB_METRICS_ENABLED:-1}"
METRICS_PORT="${NB_METRICS_PORT:-9191}"
METRICS_GROUP="${NB_METRICS_GROUP:-NetBird-Metrics}"
MONITORING_GROUP="${NB_MONITORING_GROUP:-Monitoring}"
PROMETHEUS_NETBIRD_IP="${NB_PROMETHEUS_NETBIRD_IP:-100.107.91.6}"
SSH_PUBLIC_KEY="${NB_SSH_PUBLIC_KEY:-ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJc8VZvZ7o/8emKoGC7UXPiOMP8PSxch6P2rUGNio8Vi Stefan}"

BASE_DIR="/etc/openmain-netbird-router"
WORK_DIR="/usr/local/lib/openmain-netbird-router"
STATE_DIR="/var/lib/netbird-kundenrouter"
ENV_FILE="${BASE_DIR}/customer.env"
OFFICE_FILE="${BASE_DIR}/office-network.env"
DEPLOY_SCRIPT="/usr/local/sbin/openmain-netbird-customer-deploy"
SERVICE_FILE="/etc/systemd/system/openmain-netbird-customer-deploy.service"

log(){ printf '\033[1;32m[+]\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die(){ printf '\033[1;31m[FEHLER]\033[0m %s\n' "$*" >&2; exit 1; }

usage(){
cat <<'EOF'
Openmain-it Raspberry Pi Kundenrouter - Vorbereitung im Buero

Der Pi wird vorbereitet, verbindet sich aber NICHT mit NetBird.
Nach dem Aufstellen beim Kunden erkennt der systemd-Dienst das neue LAN
und fuehrt erst dort die NetBird-/BINAT-Konfiguration aus.

Optionen:
  --customer "Firma"
  --role primary|backup
  --hostname NAME
  --setup-key KEY
  --api-token TOKEN
  --management-url URL
  --zabbix-api-token TOKEN
  --zabbix-api-url URL
  --zabbix-server IP
  --lan-interface IFACE
  --metrics-port PORT
  --prometheus-netbird-ip IP
  --metrics-group NAME
  --monitoring-group NAME
  --no-metrics
  --no-zabbix

Oeffentliches Repository:
  sudo ./install-router.sh --customer "Firma"
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --customer) CUSTOMER="$2"; shift 2;;
    --role) ROLE="$2"; shift 2;;
    --hostname) HOSTNAME_LOCAL="$2"; shift 2;;
    --setup-key) SETUP_KEY="$2"; shift 2;;
    --api-token) API_TOKEN="$2"; shift 2;;
    --management-url) MGMT_URL="$2"; shift 2;;
    --zabbix-api-token) ZABBIX_API_TOKEN="$2"; shift 2;;
    --zabbix-api-url) ZABBIX_API_ENDPOINT="$2"; shift 2;;
    --zabbix-server) ZABBIX_SERVER="$2"; shift 2;;
    --lan-interface) LAN_IF="$2"; shift 2;;
    --metrics-port) METRICS_PORT="$2"; shift 2;;
    --prometheus-netbird-ip) PROMETHEUS_NETBIRD_IP="$2"; shift 2;;
    --metrics-group) METRICS_GROUP="$2"; shift 2;;
    --monitoring-group) MONITORING_GROUP="$2"; shift 2;;
    --no-metrics) METRICS_ENABLED=0; shift;;
    --no-zabbix) ZABBIX_ENABLED=0; shift;;
    -h|--help) usage; exit 0;;
    *) die "Unbekannte Option: $1";;
  esac
done

[[ $EUID -eq 0 ]] || die "Bitte als root ausfuehren."
[[ "$ROLE" == "primary" || "$ROLE" == "backup" ]] || die "--role muss primary oder backup sein."
[[ "$METRICS_ENABLED" == 0 || "$METRICS_ENABLED" == 1 ]] || die "NB_METRICS_ENABLED muss 0 oder 1 sein."
[[ "$METRICS_PORT" =~ ^[0-9]+$ ]] || die "Ungueltiger Metrics-Port: $METRICS_PORT"
(( METRICS_PORT >= 1 && METRICS_PORT <= 65535 )) || die "Ungueltiger Metrics-Port: $METRICS_PORT"

MODEL="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
[[ "$MODEL" == *"Raspberry Pi"* ]] || die "Kein Raspberry Pi erkannt: $MODEL"
ARCH="$(uname -m)"
[[ "$ARCH" == "aarch64" ]] || die "64-bit Raspberry Pi OS / Debian arm64 erforderlich. Erkannt: $ARCH"

[[ -n "$CUSTOMER" ]] || read -r -p "Firmenname/Kunde: " CUSTOMER
[[ -n "$SETUP_KEY" ]] || { read -r -s -p "NetBird Setup Key: " SETUP_KEY; echo; }
[[ -n "$API_TOKEN" ]] || { read -r -s -p "NetBird API Token: " API_TOKEN; echo; }
[[ -n "$CUSTOMER" && -n "$SETUP_KEY" && -n "$API_TOKEN" ]] || die "Pflichtangabe fehlt."

detect_network(){
  local iface="${1:-}"
  [[ -n "$iface" ]] || iface="$(ip -4 route show default | awk 'NR==1{print $5}')"
  [[ -n "$iface" ]] || return 1

  local lan gw mac
  lan="$(ip -4 route show dev "$iface" proto kernel scope link | awk '$1 ~ /^[0-9]+\./ && $1 ~ /\// {print $1; exit}')"
  gw="$(ip -4 route show default dev "$iface" | awk 'NR==1{for(i=1;i<=NF;i++) if($i=="via"){print $(i+1); exit}}')"
  [[ -n "$lan" && -n "$gw" ]] || return 1

  ping -c1 -W1 "$gw" >/dev/null 2>&1 || true
  mac="$(ip neigh show "$gw" dev "$iface" 2>/dev/null | awk 'NR==1{for(i=1;i<=NF;i++) if($i=="lladdr"){print $(i+1); exit}}')"

  printf '%s|%s|%s|%s\n' "$iface" "$lan" "$gw" "$mac"
}

fetch_file(){
  local url="$1" out="$2"
  curl -fsSL "$url" -o "$out"
}

write_var(){
  local key="$1" value="$2" file="$3"
  printf '%s=%q\n' "$key" "$value" >> "$file"
}

log "Installiere Pakete fuer die Vorbereitung ..."
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  openssh-server sudo ca-certificates curl jq nftables python3 iproute2 iputils-ping gnupg

if ! command -v netbird >/dev/null 2>&1; then
  install -d -m 0755 /usr/share/keyrings
  curl -fsSL https://pkgs.netbird.io/debian/public.key | gpg --dearmor --yes -o /usr/share/keyrings/netbird-archive-keyring.gpg
  printf '%s\n' 'deb [signed-by=/usr/share/keyrings/netbird-archive-keyring.gpg] https://pkgs.netbird.io/debian stable main' > /etc/apt/sources.list.d/netbird.list
  apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y netbird
fi

netbird down >/dev/null 2>&1 || true
systemctl disable --now netbird >/dev/null 2>&1 || true

log "Richte SSH fuer omadmin und root ein ..."
if ! id omadmin >/dev/null 2>&1; then
  useradd -m -s /bin/bash -G sudo omadmin
else
  usermod -aG sudo omadmin
fi
passwd -l omadmin >/dev/null 2>&1 || true

install -d -m 0700 -o omadmin -g omadmin /home/omadmin/.ssh
printf '%s\n' "$SSH_PUBLIC_KEY" > /home/omadmin/.ssh/authorized_keys
chown omadmin:omadmin /home/omadmin/.ssh/authorized_keys
chmod 0600 /home/omadmin/.ssh/authorized_keys

install -d -m 0700 /root/.ssh
printf '%s\n' "$SSH_PUBLIC_KEY" > /root/.ssh/authorized_keys
chmod 0600 /root/.ssh/authorized_keys

install -d -m 0755 /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/00-openmain-router.conf <<'EOF'
PermitRootLogin prohibit-password
PubkeyAuthentication yes
PasswordAuthentication no
EOF
sshd -t
systemctl enable --now ssh
systemctl restart ssh

if [[ "$(hostname -s)" != "$HOSTNAME_LOCAL" ]]; then
  hostnamectl set-hostname "$HOSTNAME_LOCAL"
fi

install -d -m 0700 "$BASE_DIR"
install -d -m 0755 "$WORK_DIR"
install -d -m 0700 "$STATE_DIR"

log "Speichere zentrale Installationsskripte lokal ..."
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
if [[ -f "$SCRIPT_DIR/../common/router-install.sh" ]]; then
  cp "$SCRIPT_DIR/../common/router-install.sh" "$WORK_DIR/router-install.sh"
  cp "$SCRIPT_DIR/../common/zabbix-proxy-install.sh" "$WORK_DIR/zabbix-proxy-install.sh"
  [[ -f "$SCRIPT_DIR/../common/zabbix-api-register.sh" ]] && cp "$SCRIPT_DIR/../common/zabbix-api-register.sh" "$WORK_DIR/zabbix-api-register.sh"
else
  fetch_file "$COMMON_ROUTER_URL" "$WORK_DIR/router-install.sh"
  fetch_file "$ZABBIX_PROXY_URL" "$WORK_DIR/zabbix-proxy-install.sh"
  fetch_file "$ZABBIX_API_URL_FILE" "$WORK_DIR/zabbix-api-register.sh"
fi
chmod 0755 "$WORK_DIR/"*.sh
install -m 0755 "$WORK_DIR/zabbix-proxy-install.sh" /usr/local/sbin/zabbix-proxy-install
[[ -f "$WORK_DIR/zabbix-api-register.sh" ]] && install -m 0755 "$WORK_DIR/zabbix-api-register.sh" /usr/local/sbin/zabbix-api-register

office="$(detect_network "$LAN_IF")" || die "Bueronetz konnte nicht erkannt werden. LAN/DHCP/Default-Route pruefen."
IFS='|' read -r OFFICE_IF OFFICE_LAN OFFICE_GW OFFICE_GW_MAC <<< "$office"

: > "$OFFICE_FILE"
write_var OFFICE_IF "$OFFICE_IF" "$OFFICE_FILE"
write_var OFFICE_LAN "$OFFICE_LAN" "$OFFICE_FILE"
write_var OFFICE_GW "$OFFICE_GW" "$OFFICE_FILE"
write_var OFFICE_GW_MAC "$OFFICE_GW_MAC" "$OFFICE_FILE"
chmod 0600 "$OFFICE_FILE"

: > "$ENV_FILE"
write_var NB_MANAGEMENT_URL "$MGMT_URL" "$ENV_FILE"
write_var NB_CUSTOMER_NAME "$CUSTOMER" "$ENV_FILE"
write_var NB_ROLE "$ROLE" "$ENV_FILE"
write_var NB_SETUP_KEY "$SETUP_KEY" "$ENV_FILE"
write_var NB_API_TOKEN "$API_TOKEN" "$ENV_FILE"
write_var NB_ZABBIX_SERVER "$ZABBIX_SERVER" "$ENV_FILE"
write_var NB_ZABBIX_API_URL "$ZABBIX_API_ENDPOINT" "$ENV_FILE"
write_var NB_ZABBIX_API_TOKEN "$ZABBIX_API_TOKEN" "$ENV_FILE"
write_var NB_ZABBIX_ENABLED "$ZABBIX_ENABLED" "$ENV_FILE"
write_var NB_METRICS_ENABLED "$METRICS_ENABLED" "$ENV_FILE"
write_var NB_METRICS_PORT "$METRICS_PORT" "$ENV_FILE"
write_var NB_METRICS_GROUP "$METRICS_GROUP" "$ENV_FILE"
write_var NB_MONITORING_GROUP "$MONITORING_GROUP" "$ENV_FILE"
write_var NB_PROMETHEUS_NETBIRD_IP "$PROMETHEUS_NETBIRD_IP" "$ENV_FILE"
write_var NB_HOSTNAME "$HOSTNAME_LOCAL" "$ENV_FILE"
chmod 0600 "$ENV_FILE"

cat > "$DEPLOY_SCRIPT" <<'DEPLOY'
#!/usr/bin/env bash
set -Eeuo pipefail

BASE_DIR="/etc/openmain-netbird-router"
ENV_FILE="$BASE_DIR/customer.env"
OFFICE_FILE="$BASE_DIR/office-network.env"
MARKER="/var/lib/netbird-kundenrouter/customer-deployed"
INSTALLER="/usr/local/lib/openmain-netbird-router/router-install.sh"
FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

log(){ logger -t openmain-netbird-deploy -- "$*"; printf '[deploy] %s\n' "$*"; }

[[ -f "$MARKER" ]] && exit 0
[[ -r "$ENV_FILE" && -r "$OFFICE_FILE" && -x "$INSTALLER" ]] || { log "Vorbereitung unvollstaendig."; exit 1; }

set -a
. "$ENV_FILE"
. "$OFFICE_FILE"
set +a

detect_network(){
  local iface lan gw mac
  iface="$(ip -4 route show default | awk 'NR==1{print $5}')"
  [[ -n "$iface" ]] || return 1
  lan="$(ip -4 route show dev "$iface" proto kernel scope link | awk '$1 ~ /^[0-9]+\./ && $1 ~ /\// {print $1; exit}')"
  gw="$(ip -4 route show default dev "$iface" | awk 'NR==1{for(i=1;i<=NF;i++) if($i=="via"){print $(i+1); exit}}')"
  [[ -n "$lan" && -n "$gw" ]] || return 1
  ping -c1 -W1 "$gw" >/dev/null 2>&1 || true
  mac="$(ip neigh show "$gw" dev "$iface" 2>/dev/null | awk 'NR==1{for(i=1;i<=NF;i++) if($i=="lladdr"){print $(i+1); exit}}')"
  printf '%s|%s|%s|%s\n' "$iface" "$lan" "$gw" "$mac"
}

is_office_network(){
  local iface="$1" lan="$2" gw="$3" mac="$4"
  [[ "$lan" != "$OFFICE_LAN" ]] && return 1
  [[ "$gw" != "$OFFICE_GW" ]] && return 1
  if [[ -n "$OFFICE_GW_MAC" && -n "$mac" && "$mac" != "$OFFICE_GW_MAC" ]]; then
    return 1
  fi
  return 0
}

while (( FORCE == 0 )); do
  current="$(detect_network || true)"
  if [[ -z "$current" ]]; then
    log "Warte auf LAN, DHCP und Default-Route ..."
    sleep 15
    continue
  fi

  IFS='|' read -r LAN_IF LAN_NET LAN_GW LAN_GW_MAC <<< "$current"

  if is_office_network "$LAN_IF" "$LAN_NET" "$LAN_GW" "$LAN_GW_MAC"; then
    log "Bueronetz erkannt ($LAN_NET, Gateway $LAN_GW). Noch nicht aktivieren."
    sleep 30
    continue
  fi

  if ! curl -sS --connect-timeout 5 --max-time 10 -o /dev/null "${NB_MANAGEMENT_URL%/}"; then
    log "Neues LAN erkannt, aber NetBird-Management noch nicht erreichbar."
    sleep 20
    continue
  fi

  log "Kundennetz erkannt: $LAN_IF / $LAN_NET / Gateway $LAN_GW"
  break
done

if (( FORCE == 1 )); then
  current="$(detect_network)" || { log "Kein nutzbares LAN gefunden."; exit 1; }
  IFS='|' read -r LAN_IF LAN_NET LAN_GW LAN_GW_MAC <<< "$current"
  log "Erzwungene Aktivierung auf $LAN_IF / $LAN_NET"
fi

export NB_LAN_INTERFACE="$LAN_IF"

args=(
  --customer "$NB_CUSTOMER_NAME"
  --role "$NB_ROLE"
  --hostname "$NB_HOSTNAME"
  --management-url "$NB_MANAGEMENT_URL"
  --setup-key "$NB_SETUP_KEY"
  --api-token "$NB_API_TOKEN"
  --zabbix-server "$NB_ZABBIX_SERVER"
  --lan-interface "$NB_LAN_INTERFACE"
)
[[ "${NB_ZABBIX_ENABLED:-1}" == "1" ]] || args+=(--no-zabbix)
[[ "${NB_METRICS_ENABLED:-1}" == "1" ]] || args+=(--no-metrics)

"$INSTALLER" "${args[@]}"

install -d -m 0700 "$(dirname "$MARKER")"
printf 'customer=%s\nlan=%s\ninterface=%s\ndeployed_at=%s\n' \
  "$NB_CUSTOMER_NAME" "$LAN_NET" "$LAN_IF" "$(date -Is)" > "$MARKER"
chmod 0600 "$MARKER"

if command -v shred >/dev/null 2>&1; then
  shred -u "$ENV_FILE" || rm -f "$ENV_FILE"
else
  rm -f "$ENV_FILE"
fi

systemctl disable openmain-netbird-customer-deploy.service >/dev/null 2>&1 || true
log "Kundenrouter erfolgreich aktiviert."
DEPLOY
chmod 0755 "$DEPLOY_SCRIPT"

cat > "$SERVICE_FILE" <<'EOF'
[Unit]
Description=Openmain NetBird Kundenrouter Auto-Deployment
Wants=network-online.target
After=network-online.target
ConditionPathExists=!/var/lib/netbird-kundenrouter/customer-deployed

[Service]
Type=simple
ExecStart=/usr/local/sbin/openmain-netbird-customer-deploy
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable openmain-netbird-customer-deploy.service >/dev/null

log "Vorbereitung abgeschlossen. NetBird bleibt im Buero deaktiviert."
echo
echo "Kunde       : $CUSTOMER"
echo "Rolle       : $ROLE"
echo "Hostname    : $HOSTNAME_LOCAL"
echo "Bueronetz   : $OFFICE_LAN"
echo "Gateway     : $OFFICE_GW"
echo "Gateway-MAC : ${OFFICE_GW_MAC:-nicht ermittelt}"
echo "Metrics     : $([[ "$METRICS_ENABLED" == 1 ]] && echo "aktiv / TCP $METRICS_PORT" || echo "deaktiviert")"
echo
echo "Naechster Schritt:"
echo "  1. Pi sauber herunterfahren: shutdown -h now"
echo "  2. Beim Kunden per LAN anschliessen und einschalten."
echo "  3. Der Pi erkennt das neue Netz und aktiviert NetBird/BINAT automatisch."
echo
echo "Status spaeter:"
echo "  systemctl status openmain-netbird-customer-deploy --no-pager"
echo "  journalctl -u openmain-netbird-customer-deploy -f"
echo
echo "Manuell erzwingen:"
echo "  sudo /usr/local/sbin/openmain-netbird-customer-deploy --force"
