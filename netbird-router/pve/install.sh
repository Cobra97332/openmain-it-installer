#!/usr/bin/env bash
set +xv
set -Eeuo pipefail
umask 077
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
EXISTING_CT=0
CTID='' CONFIG='' BRIDGE=vmbr0 STORAGE='' TEMPLATE_STORAGE=local
MEMORY=1024 CORES=2 DISK=8 IP_CONFIG=dhcp GATEWAY=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG=$2; shift 2;;
    --role) export NB_ROLE="$2"; shift 2;;
    --customer) export NB_CUSTOMER_NAME="$2"; shift 2;;
    --hostname) export NB_HOSTNAME="$2"; shift 2;;
    --management-url) export NB_MANAGEMENT_URL="$2"; shift 2;;
    --setup-key) export NB_SETUP_KEY="$2"; shift 2;;
    --api-token) export NB_API_TOKEN="$2"; shift 2;;
    --zabbix-api-url) export NB_ZABBIX_API_URL="$2"; shift 2;;
    --zabbix-server) export NB_ZABBIX_SERVER="$2"; shift 2;;
    --no-metrics) export NB_METRICS_ENABLED=0; shift;;
    --ctid) CTID=$2; shift 2;;
    --bridge) BRIDGE=$2; shift 2;;
    --storage) STORAGE=$2; shift 2;;
    --template-storage) TEMPLATE_STORAGE=$2; shift 2;;
    --memory) MEMORY=$2; shift 2;;
    --cores) CORES=$2; shift 2;;
    --disk) DISK=$2; shift 2;;
    --ip) IP_CONFIG=$2; shift 2;;
    --gateway) GATEWAY=$2; shift 2;;
    *) echo 'Unbekannte PVE-Option; Zugangsdaten ueber --config oder NB_ Variablen.' >&2; exit 1;;
  esac
done
if [[ $EUID -ne 0 ]] || ! command -v pct >/dev/null; then { echo 'Auf dem Proxmox-Host als root starten.' >&2; exit 1; }; fi
command -v python3 >/dev/null
install -d -m 0700 /var/lib/openmain-router-pve
exec 9>/var/lib/openmain-router-pve/install.lock
flock -n 9 || { echo 'PVE-Installer laeuft bereits.' >&2; exit 1; }
TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT
args=("$TMP/customer.json")
[[ -z "$CONFIG" ]] || args+=("$CONFIG")
python3 "$ROOT/netbird-router/common/prepare_config.py" "${args[@]}"
readarray -t identity < <(python3 - "$TMP/customer.json" <<'PY'
import hashlib,json,sys
c=json.load(open(sys.argv[1]));identity=hashlib.sha256((c['NB_CUSTOMER_NAME']+'\0'+c['NB_ROLE']).encode()).hexdigest()[:20]
print(identity);print(c['NB_ROLE']);print(c.get('NB_HOSTNAME') or 'router-'+identity)
PY
)
ID=${identity[0]}
HOSTNAME_CT=${identity[2]}
STATE="/var/lib/openmain-router-pve/$ID.ctid"
if [[ -s "$STATE" ]]; then
  read -r SAVED < "$STATE"
  [[ -z "$CTID" || "$CTID" == "$SAVED" ]] || { echo 'CTID weicht vom Wiederaufnahmestatus ab.' >&2; exit 1; }
  CTID=$SAVED
fi
[[ -n "$CTID" ]] || CTID=$(pvesh get /cluster/nextid)
[[ "$CTID" =~ ^[0-9]+$ && "$MEMORY" =~ ^[0-9]+$ && "$CORES" =~ ^[0-9]+$ && "$DISK" =~ ^[0-9]+$ ]] || exit 1
(( MEMORY >= 512 && CORES >= 1 && DISK >= 4 )) || { echo 'Mindestens 512 MB, 1 CPU und 4 GB erforderlich.' >&2; exit 1; }
ip link show "$BRIDGE" >/dev/null
if pct status "$CTID" >/dev/null 2>&1; then
  EXISTING_CT=1
  CT_CONFIG=$(pct config "$CTID")
  mapfile -t descriptions < <(sed -n 's/^description: //p' <<< "$CT_CONFIG")
  # PVE renders the stored trailing LF as %0A. Accept only that exact suffix,
  # never arbitrary URL decoding, whitespace trimming or substring matching.
  [[ ${#descriptions[@]} == 1 && ( "${descriptions[0]}" == "OpenMain managed $ID" || "${descriptions[0]}" == "OpenMain managed $ID%0A" ) ]] \
    || { echo 'Vorhandener CT gehoert nicht zu diesem Auftrag.' >&2; exit 1; }
  pct status "$CTID" | grep -q running || { echo 'Bestehender CT ist gestoppt; keine automatische Aktivierung vor Bestandspruefung.' >&2; exit 1; }
  readarray -t admission < <(python3 - "$TMP/customer.json" <<'PYCHECK'
import json,sys
c=json.load(open(sys.argv[1]))
for k,default in [('NB_CUSTOMER_NAME',''),('NB_ROLE',''),('NB_MANAGEMENT_URL','https://netbird.openmain-it.de'),('NB_ZABBIX_SERVER','100.107.91.6'),('NB_LAN_INTERFACE',''),('NB_ZABBIX_ENABLED','1')]:
    print(c.get(k,default))
PYCHECK
)
  # Source travels as code, not a guest file; no bundle/credential push precedes it.
  PROFILE_INFO=$(pct exec "$CTID" -- python3 -c "$(cat "$ROOT/netbird-router/common/preflight.py")" \
    --customer "${admission[0]}" --role "${admission[1]}" --management-url "${admission[2]}" \
    --zabbix-server "${admission[3]}" --lan-interface "${admission[4]}" --zabbix-enabled "${admission[5]}" --report-profile) \
    || { echo 'Gast-Bestandspruefung abgelehnt; keine Dateien/Pakete/Dienste geaendert.' >&2; exit 1; }
  ACTIVE_PROFILE=$(python3 -c 'import json,sys;print(json.load(sys.stdin)["path"])' <<<"$PROFILE_INFO")
  if [[ -z "$ACTIVE_PROFILE" ]]; then
    # Reuse the first protected stage's values (API tokens/customer/role).
    # Otherwise prepare_config prompts for every field a second time.
    python3 "$ROOT/netbird-router/common/prepare_config.py" "$TMP/customer.json" "$TMP/customer.json" --require-setup-key
  fi
else
  # Reuse the first protected stage's values (API tokens/customer/role).
    # Otherwise prepare_config prompts for every field a second time.
    python3 "$ROOT/netbird-router/common/prepare_config.py" "$TMP/customer.json" "$TMP/customer.json" --require-setup-key
  modprobe tun
  [[ -c /dev/net/tun ]]
  [[ -n "$STORAGE" ]] || STORAGE=$(pvesm status --content rootdir --enabled 1 | awk 'NR>1 && $3=="active"{print $1;exit}')
  [[ -n "$STORAGE" ]]
  pveam update >/dev/null
  TEMPLATE=$(pveam available --section system | awk '$2 ~ /^debian-13-standard_.*_amd64.tar./{print $2}' | sort -V | tail -n1)
  [[ -n "$TEMPLATE" ]]
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
  NET="name=eth0,bridge=$BRIDGE,type=veth,firewall=1,ip=$IP_CONFIG"
  [[ -z "$GATEWAY" ]] || NET+=",gw=$GATEWAY"
  # Write intent before creation; a retry validates the CT ownership marker.
  printf '%s\n' "$CTID" > "$STATE"
  # pct creates the rootfs before extracting as UID 100000. Its parent must
  # remain traversable for the unprivileged extractor; secrets stay under 077.
  (umask 022
   pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" --hostname "$HOSTNAME_CT" \
     --description "OpenMain managed $ID" --ostype debian --arch amd64 --unprivileged 1 \
     --memory "$MEMORY" --cores "$CORES" --rootfs "$STORAGE:$DISK" --net0 "$NET" --onboot 1 \
     --dev0 /dev/net/tun)
fi
if [[ "$EXISTING_CT" == 0 ]]; then
  pct status "$CTID" | grep -q running || pct start "$CTID"
fi
# A fresh Debian template may lack Python. The shell gate only admits an
# entirely pristine guest, before file transfer or DHCP service installation.
if [[ "$EXISTING_CT" == 0 ]]; then
  pct exec "$CTID" -- bash -c "$(cat "$ROOT/netbird-router/common/pristine.sh")" \
    || { echo 'Neuer Gast enthaelt Bestand; keine automatische Einrichtung.' >&2; exit 1; }
fi
# No credentials in pct arguments or shell snippets. Transfer protected file.
tar -C "$ROOT" -czf "$TMP/bundle.tar.gz" router-install.sh zabbix-proxy-install.sh zabbix-api-register.sh netbird-router netbird-prometheus
pct push "$CTID" "$TMP/bundle.tar.gz" /root/openmain-bundle.tar.gz --perms 600
pct push "$CTID" "$TMP/customer.json" /root/openmain-customer.json --perms 600
pct exec "$CTID" -- bash -c 'set +xv; set -eu; mkdir -p /opt/openmain-router; tar -xzf /root/openmain-bundle.tar.gz -C /opt/openmain-router; rm /root/openmain-bundle.tar.gz; bash /opt/openmain-router/netbird-router/pve/install-dhcp-recovery.sh'
# Recovery is installed before any package/API access. Wait for usable IPv4.
ready=0
for _ in {1..90}; do
  if pct exec "$CTID" -- bash -c 'ip -4 route show default | grep -q default'; then ready=1; break; fi
  pct exec "$CTID" -- bash /opt/openmain-router/netbird-router/common/dhcp-recovery.sh >/dev/null 2>&1 || true
  sleep 2
done
[[ "$ready" == 1 ]] || { echo "CT $CTID: Netzwerk ausstehend. Erneut mit gleicher Konfiguration starten." >&2; exit 1; }
if ! pct exec "$CTID" -- python3 --version >/dev/null 2>&1; then
  [[ "$EXISTING_CT" == 0 ]] || exit 1
  pct exec "$CTID" -- bash -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y python3'
fi
pct exec "$CTID" -- bash /opt/openmain-router/router-install.sh --config /root/openmain-customer.json
pct exec "$CTID" -- rm -f /root/openmain-customer.json
printf 'CT %s erfolgreich. Wiederaufnahme-ID: %s\n' "$CTID" "$ID"
