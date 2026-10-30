#!/usr/bin/env bash
# Never trace credentials, including when invoked with bash -x/-v.
set +xv
set -Eeuo pipefail

API_URL="${NB_ZABBIX_API_URL:-https://zabbix.openmain-it.de/api_jsonrpc.php}"
API_TOKEN="${NB_ZABBIX_API_TOKEN:-}"
CUSTOMER="${1:-}"
ROLE="${2:-}"
PROXY_NAME="${3:-}"
LAN_IP="${4:-}"
PSK_IDENTITY="${5:-}"
PSK_FILE="${6:-/etc/zabbix/zabbix_proxy.psk}"
PSK_PENDING="${NB_ZABBIX_PSK_PENDING_FILE:-/var/lib/netbird-kundenrouter/zabbix-psk.pending}"

[[ -n "$API_TOKEN" ]] || { echo "NB_ZABBIX_API_TOKEN fehlt." >&2; exit 1; }
[[ -n "$CUSTOMER" && -n "$ROLE" && -n "$PROXY_NAME" && -n "$LAN_IP" ]] || { echo "Parameter fehlen." >&2; exit 1; }

# Zabbix proxy group is named after the customer; it is NOT a NetBird peer group.
# NB_ZABBIX_PROXY_GROUP is reserved for the NetBird access group.
GROUP_NAME="${NB_ZABBIX_PROXYGROUP_NAME:-$CUSTOMER}"
[[ -n "$GROUP_NAME" ]] || { echo "Zabbix Proxy-Gruppenname fehlt." >&2; exit 1; }
[[ "$LAN_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Keine gueltige LAN-IPv4: $LAN_IP" >&2; exit 1; }

python3 -c 'import ipaddress,sys; ipaddress.IPv4Address(sys.argv[1])' "$LAN_IP" >/dev/null 2>&1 \
  || { echo 'Ungueltige LAN-Adresse.' >&2; exit 1; }

ACCESS_HELPER="${NB_BUNDLE_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}/netbird-router/common/zabbix_access.py"
[[ -f "$ACCESS_HELPER" ]] || ACCESS_HELPER=/usr/local/lib/openmain-router/zabbix_access.py
[[ -f "$ACCESS_HELPER" ]] || { echo 'Zabbix-Zugriffspruefer fehlt; vollstaendiges Installer-Bundle erforderlich.' >&2; exit 1; }
python3 "$ACCESS_HELPER" || { echo 'Keine Zabbix-Registrierung: NetBird-/HTTPS-Freigabe nicht bestaetigt. PSK und Bestand bleiben erhalten.' >&2; exit 1; }

REG_HELPER="${ACCESS_HELPER%/*}/zabbix_registration.py"
[[ -f "$REG_HELPER" ]] || { echo 'TLS-Registrierungspruefer fehlt; vollstaendiges Bundle erforderlich.' >&2; exit 1; }
export REG_FILE="${NB_ZABBIX_RECEIPT_FILE:-${PSK_PENDING%/*}/zabbix-registration.json}"
export REG_URL="$API_URL" REG_CUSTOMER="$CUSTOMER" REG_PROXY="$PROXY_NAME" REG_IDENTITY="$PSK_IDENTITY" REG_PSK_FILE="$PSK_FILE"
registration(){ python3 "$REG_HELPER" "$1"; }
registration check || exit 1
[[ ! -L "$PSK_PENDING" ]] || { echo 'Unsicherer Pending-Marker; keine Aenderung.' >&2; exit 1; }

api(){
  local method="$1" params="$2" payload
  if [[ "$API_URL" != http://* ]]; then
    python3 "$ACCESS_HELPER" || { echo 'Zabbix-Aenderung abgebrochen: NetBird-/HTTPS-Freigabe nicht bestaetigt; Bestand bleibt erhalten.' >&2; return 1; }
  fi
  payload=$(jq -nc --arg method "$method" --argjson params "$params" '{jsonrpc:"2.0",method:$method,params:$params,id:1}')
  if [[ "$API_URL" == http://* ]]; then
    # Exact endpoint admission, fresh status/route checks and device-bound
    # transport are shared with Agent/host registration. No HTTP proxy/DNS.
    python3 "$ACCESS_HELPER" --private-request <<<"$payload"
    return $?
  fi
  # A server/proxy error can echo request secrets. Never relay raw diagnostics.
  curl --disable --no-insecure --no-proxy-insecure --location --max-redirs 0 --proto '=https' --proto-redir '=https' -fsS --connect-timeout 8 --max-time 30 "$API_URL" \
    -H 'Content-Type: application/json-rpc' -H "Authorization: Bearer $API_TOKEN" --data "$payload" 2>/dev/null \
    || { echo "Zabbix-API-Transportfehler bei $method (Details unterdrueckt)." >&2; return 1; }
}

result(){
  registration result <<<"$1"
}

r=$(api proxy.get "$(jq -nc --arg n "$PROXY_NAME" '{output:["proxyid","name","operating_mode","local_address","local_port","proxy_groupid","tls_accept","description"],filter:{name:[$n]}}')")
existing=$(result "$r")
jq -e --arg name "$PROXY_NAME" 'type=="array" and all(.[]; .name==$name and (.proxyid|type=="string" and length>0))' <<<"$existing" >/dev/null 2>&1 || { echo "Ungueltige Proxy-Liste." >&2; exit 1; }
proxy_count=$(jq -r 'length' <<<"$existing")
(( proxy_count <= 1 )) || { echo "Mehrere Zabbix-Proxys mit Namen $PROXY_NAME." >&2; exit 1; }
pid=$(jq -r '.[0].proxyid // empty' <<<"$existing")

if [[ -n "$pid" ]]; then
  registration verify <<<"$(jq -c '.[0]' <<<"$existing")" || exit 1
fi

r=$(api proxygroup.get "$(jq -nc --arg n "$GROUP_NAME" '{output:["proxy_groupid","name","min_online","failover_delay"],filter:{name:[$n]}}')")
groups=$(result "$r")
jq -e --arg name "$GROUP_NAME" 'type=="array" and all(.[]; .name==$name and (.proxy_groupid|type=="string" and length>0))' <<<"$groups" >/dev/null 2>&1 || { echo "Ungueltige Proxy-Gruppenliste." >&2; exit 1; }
group_count=$(jq -r 'length' <<<"$groups")
(( group_count <= 1 )) || { echo "Doppelte Zabbix-Proxy-Gruppe: $GROUP_NAME" >&2; exit 1; }
gid=$(jq -r '.[0].proxy_groupid // empty' <<<"$groups")
if [[ -n "$pid" ]]; then
  old_gid=$(jq -r '.[0].proxy_groupid // "0"' <<<"$existing")
  [[ "$old_gid" == 0 || "$old_gid" == "$gid" ]] || { echo "Proxy gehoert bereits einer anderen Gruppe an. Keine Aenderung." >&2; exit 1; }
fi
if [[ -n "$gid" ]]; then
  jq -e '.[0] | (.min_online|tostring)=="1" and (.failover_delay|type=="string" and length>0)' <<<"$groups" >/dev/null 2>&1 \
    || { echo "Bestehende Proxy-Gruppe nicht fuer Einzelrouter-Failover konfiguriert. Keine Ueberschreibung." >&2; exit 1; }
fi
if [[ -z "$gid" ]]; then
  r=$(api proxygroup.create "$(jq -nc --arg n "$GROUP_NAME" '{name:$n,failover_delay:"1m",min_online:"1"}')")
  created_group=$(result "$r")
  jq -e '.proxy_groupids | type=="array" and length==1 and (.[0]|type=="string" and test("^[1-9][0-9]*$"))' <<<"$created_group" >/dev/null 2>&1 || { echo 'Proxy-Gruppenerstellung nicht bestaetigt.' >&2; exit 1; }
  gid=$(jq -r '.proxy_groupids[0]' <<<"$created_group")
fi
[[ -n "$gid" ]] || { echo "Zabbix Proxy-Gruppen-ID fehlt." >&2; exit 1; }

if [[ -z "$pid" ]]; then
  # A new proxy needs its PSK configuration; never overwrite a working PSK.
  [[ -s "$PSK_FILE" ]] || { echo "PSK-Datei fehlt oder leer: $PSK_FILE" >&2; exit 1; }
  [[ -n "$PSK_IDENTITY" ]] || { echo "PSK-Identitaet fehlt." >&2; exit 1; }
  psk=$(cat "$PSK_FILE" 2>/dev/null) || { echo "PSK nicht lesbar." >&2; exit 1; }
  [[ "$psk" =~ ^[[:xdigit:]]{32,512}$ && $(( ${#psk} % 2 )) == 0 ]] || { echo "Ungueltiger PSK." >&2; exit 1; }
  export REG_GROUP="$gid"
  marker=$(registration prepare <<<"$psk") || exit 1
  params=$(jq -nc --arg marker "$marker" --arg n "$PROXY_NAME" --arg gid "$gid" --arg a "$LAN_IP" \
    --arg ident "$PSK_IDENTITY" --arg psk "$psk" \
    '{name:$n,description:$marker,proxy_groupid:$gid,local_address:$a,local_port:"10051",operating_mode:0,tls_accept:2,tls_psk_identity:$ident,tls_psk:$psk}')
  r=$(api proxy.create "$params")
  created=$(result "$r")
  jq -e '.proxyids | type=="array" and length==1 and (.[0]|type=="string" and test("^[1-9][0-9]*$"))' <<<"$created" >/dev/null 2>&1 \
    || { echo "Proxy-Erstellung nicht bestaetigt; TLS-Zustand bleibt offen." >&2; exit 1; }
  pid=$(jq -r '.proxyids[0]' <<<"$created")
else
  # Update existing proxy group and active-agent LAN address only.
  # Keep operating mode, PSK, TLS settings and assigned hosts untouched.
  params=$(jq -nc --arg pid "$pid" --arg gid "$gid" --arg a "$LAN_IP" \
    '{proxyid:$pid,proxy_groupid:$gid,local_address:$a,local_port:"10051"}')
  r=$(api proxy.update "$params")
fi
result "$r" >/dev/null

# Read back and confirm that the API has stored the intended address.
r=$(api proxy.get "$(jq -nc --arg n "$PROXY_NAME" '{output:["proxyid","name","description","proxy_groupid","local_address","local_port","operating_mode","tls_accept"],filter:{name:[$n]}}')")
existing=$(result "$r")
jq -e --arg pid "$pid" --arg n "$PROXY_NAME" --arg gid "$gid" --arg address "$LAN_IP" \
  'length==1 and .[0].proxyid==$pid and .[0].name==$n and .[0].proxy_groupid==$gid and .[0].local_address==$address and .[0].local_port=="10051" and (.[0].operating_mode|tostring)=="0"' \
  <<<"$existing" >/dev/null 2>&1 || { echo "Proxy-Gruppe, Modus oder LAN-Adresse nach API-Registrierung nicht bestaetigt: $PROXY_NAME" >&2; exit 1; }

registration confirm <<<"$(jq -c '.[0]' <<<"$existing")" || exit 1
rm -f -- "$PSK_PENDING"

echo "Zabbix Proxy-Gruppe: $GROUP_NAME"
echo "Zabbix Proxy: $PROXY_NAME"
echo "Address for active agents: $LAN_IP:10051"
