#!/usr/bin/env bash
set -Eeuo pipefail

ROLE="primary"
CTID=""
HOSTNAME_CT=""
CUSTOMER=""
BRIDGE="vmbr0"
ROOTFS_STORAGE=""
TEMPLATE_STORAGE=""
MEMORY="512"
SWAP="256"
CORES="1"
DISK_GB="4"
IP_CONFIG="dhcp"
GATEWAY=""
MGMT_URL="https://netbird.openmain-it.de"
SETUP_KEY="${NB_SETUP_KEY:-}"
API_TOKEN="${NB_API_TOKEN:-}"
PRIMARY_METRIC="100"
BACKUP_METRIC="200"
COMMON_URL="${NETBIRD_ROUTER_COMMON_URL:-https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/router-install.sh}"
ZABBIX_URL="${NETBIRD_ROUTER_ZABBIX_URL:-https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/zabbix-proxy-install.sh}"
ZABBIX_API_HELPER_URL="${NETBIRD_ROUTER_ZABBIX_API_HELPER_URL:-https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/zabbix-api-register.sh}"
ZABBIX_SERVER="${NB_ZABBIX_SERVER:-100.67.255.142}"
ZABBIX_API_TOKEN="${NB_ZABBIX_API_TOKEN:-}"

log(){ printf '\033[1;32m[+]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[FEHLER]\033[0m %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --role) ROLE="$2"; shift 2;;
    --ctid) CTID="$2"; shift 2;;
    --hostname) HOSTNAME_CT="$2"; shift 2;;
    --customer) CUSTOMER="$2"; shift 2;;
    --bridge) BRIDGE="$2"; shift 2;;
    --storage) ROOTFS_STORAGE="$2"; shift 2;;
    --template-storage) TEMPLATE_STORAGE="$2"; shift 2;;
    --memory) MEMORY="$2"; shift 2;;
    --swap) SWAP="$2"; shift 2;;
    --cores) CORES="$2"; shift 2;;
    --disk) DISK_GB="$2"; shift 2;;
    --ip) IP_CONFIG="$2"; shift 2;;
    --gateway) GATEWAY="$2"; shift 2;;
    --management-url) MGMT_URL="$2"; shift 2;;
    --setup-key) SETUP_KEY="$2"; shift 2;;
    --api-token) API_TOKEN="$2"; shift 2;;
    --zabbix-server) ZABBIX_SERVER="$2"; shift 2;;
    *) die "Unbekannte Option: $1";;
  esac
done

[[ $EUID -eq 0 ]] || die "Als root auf dem PVE-Host ausführen."
command -v pct >/dev/null || die "pct fehlt."
[[ "$ROLE" == primary || "$ROLE" == backup ]] || die "Rolle muss primary oder backup sein."
[[ -n "$CTID" ]] || CTID=$(pvesh get /cluster/nextid)
[[ -n "$HOSTNAME_CT" ]] || read -r -p "Hostname: " HOSTNAME_CT
[[ -n "$CUSTOMER" ]] || read -r -p "Firmenname: " CUSTOMER
[[ -n "$SETUP_KEY" ]] || { read -r -s -p "NetBird Setup Key: " SETUP_KEY; echo; }
[[ -n "$API_TOKEN" ]] || { read -r -s -p "NetBird API Token: " API_TOKEN; echo; }
[[ -n "$ZABBIX_API_TOKEN" ]] || { read -r -s -p "Zabbix API Token: " ZABBIX_API_TOKEN; echo; }
pct status "$CTID" >/dev/null 2>&1 && die "CT $CTID existiert bereits."

case "$(uname -m)" in
  x86_64) ARCH="amd64";;
  aarch64|arm64) ARCH="arm64";;
  *) die "Architektur nicht unterstützt.";;
esac

modprobe tun || true
[[ -c /dev/net/tun ]] || die "/dev/net/tun fehlt."
ip link show "$BRIDGE" >/dev/null 2>&1 || die "Bridge $BRIDGE fehlt."

find_storage(){
  local content="$1" preferred="${2:-}"
  if [[ -n "$preferred" ]]; then
    pvesm status --content "$content" --enabled 1 --storage "$preferred" 2>/dev/null | awk 'NR>1 && $3=="active"{print $1;exit}'
  else
    pvesm status --content "$content" --enabled 1 2>/dev/null | awk 'NR>1 && $3=="active"{print $1;exit}'
  fi
}

[[ -n "$ROOTFS_STORAGE" ]] || ROOTFS_STORAGE=$(find_storage rootdir)
[[ -n "$TEMPLATE_STORAGE" ]] || TEMPLATE_STORAGE=$(find_storage vztmpl local)
[[ -n "$TEMPLATE_STORAGE" ]] || TEMPLATE_STORAGE=$(find_storage vztmpl)
[[ -n "$ROOTFS_STORAGE" && -n "$TEMPLATE_STORAGE" ]] || die "Kein geeigneter PVE-Storage gefunden."

pveam update >/dev/null
TEMPLATE=$(pveam available --section system | awk -v a="$ARCH" '$2 ~ /^debian-13-standard_/ && $2 ~ ("_" a "\\.tar\\.") {print $2}' | sort -V | tail -n1)
[[ -n "$TEMPLATE" ]] || die "Kein Debian-13-Template für $ARCH gefunden."
TEMPLATE_PATH="$TEMPLATE_STORAGE:vztmpl/$TEMPLATE"
pveam list "$TEMPLATE_STORAGE" | awk '{print $1}' | grep -Fxq "$TEMPLATE_PATH" || pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"

NET0="name=eth0,bridge=$BRIDGE,type=veth,firewall=0"
if [[ "$IP_CONFIG" == dhcp ]]; then
  NET0+=",ip=dhcp"
else
  NET0+=",ip=$IP_CONFIG"
  [[ -n "$GATEWAY" ]] && NET0+=",gw=$GATEWAY"
fi

log "Erstelle CT $CTID ($HOSTNAME_CT)"
pct create "$CTID" "$TEMPLATE_PATH"   --hostname "$HOSTNAME_CT" --arch "$ARCH" --unprivileged 0 --features nesting=1   --cores "$CORES" --memory "$MEMORY" --swap "$SWAP"   --rootfs "$ROOTFS_STORAGE:$DISK_GB" --net0 "$NET0" --onboot 1 --ostype debian

CONF="/etc/pve/lxc/$CTID.conf"
grep -qF 'lxc.cgroup2.devices.allow: c 10:200 rwm' "$CONF" || echo 'lxc.cgroup2.devices.allow: c 10:200 rwm' >> "$CONF"
grep -qF 'lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file' "$CONF" || echo 'lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file' >> "$CONF"

pct start "$CTID"
for _ in {1..30}; do pct exec "$CTID" -- true >/dev/null 2>&1 && break; sleep 1; done
pct exec "$CTID" -- test -c /dev/net/tun || die "TUN nicht im Container."

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "$COMMON_URL" -o "$TMP/router-install.sh" || die "router-install.sh konnte nicht von GitHub geladen werden."
curl -fsSL "$ZABBIX_URL" -o "$TMP/zabbix-proxy-install.sh" || die "zabbix-proxy-install.sh konnte nicht von GitHub geladen werden."
curl -fsSL "$ZABBIX_API_HELPER_URL" -o "$TMP/zabbix-api-register.sh" || die "zabbix-api-register.sh konnte nicht von GitHub geladen werden."
chmod 700 "$TMP/router-install.sh" "$TMP/zabbix-proxy-install.sh" "$TMP/zabbix-api-register.sh"
pct push "$CTID" "$TMP/router-install.sh" /root/router-install.sh -perms 700
pct push "$CTID" "$TMP/zabbix-proxy-install.sh" /usr/local/sbin/zabbix-proxy-install -perms 755
pct push "$CTID" "$TMP/zabbix-api-register.sh" /usr/local/sbin/zabbix-api-register -perms 755

cat > "$TMP/secrets" <<EOF
NB_SETUP_KEY=$(printf '%q' "$SETUP_KEY")
NB_API_TOKEN=$(printf '%q' "$API_TOKEN")
NB_MANAGEMENT_URL=$(printf '%q' "$MGMT_URL")
NB_ROLE=$(printf '%q' "$ROLE")
NB_PRIMARY_METRIC=$(printf '%q' "$PRIMARY_METRIC")
NB_BACKUP_METRIC=$(printf '%q' "$BACKUP_METRIC")
NB_ZABBIX_SERVER=$(printf '%q' "$ZABBIX_SERVER")
NB_ZABBIX_API_TOKEN=$(printf '%q' "$ZABBIX_API_TOKEN")
EOF
chmod 600 "$TMP/secrets"
pct push "$CTID" "$TMP/secrets" /root/.netbird-router-secrets -perms 600

QC=$(printf '%q' "$CUSTOMER")
QH=$(printf '%q' "$HOSTNAME_CT")
pct exec "$CTID" -- bash -lc "set -a; source /root/.netbird-router-secrets; set +a; /root/router-install.sh --role \"\$NB_ROLE\" --management-url \"\$NB_MANAGEMENT_URL\" --zabbix-server \"\$NB_ZABBIX_SERVER\" --customer $QC --hostname $QH; rc=\$?; rm -f /root/.netbird-router-secrets; exit \$rc"

log "Fertig. Prüfen mit:"
echo "pct exec $CTID -- netbird status"
echo "pct exec $CTID -- nft list table ip mein_binat"
echo "pct exec $CTID -- cat /proc/sys/net/ipv4/ip_forward"
echo "pct exec $CTID -- systemctl status zabbix-proxy --no-pager"
echo "pct exec $CTID -- systemctl status zabbix-agent2 --no-pager"
