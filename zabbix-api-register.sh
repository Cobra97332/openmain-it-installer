#!/usr/bin/env bash
set -Eeuo pipefail

API_URL="${NB_ZABBIX_API_URL:-https://zabbix.openmain-it.de/api_jsonrpc.php}"
API_TOKEN="${NB_ZABBIX_API_TOKEN:-}"
CUSTOMER="${1:-}"
ROLE="${2:-}"
PROXY_NAME="${3:-}"
LAN_IP="${4:-}"
PSK_IDENTITY="${5:-}"
PSK_FILE="${6:-/etc/zabbix/zabbix_proxy.psk}"

[[ -n "$API_TOKEN" ]] || { echo "NB_ZABBIX_API_TOKEN fehlt." >&2; exit 1; }
[[ -n "$CUSTOMER" && -n "$ROLE" && -n "$PROXY_NAME" && -n "$LAN_IP" ]] || { echo "Parameter fehlen." >&2; exit 1; }

# Zabbix proxy group is named after the customer; it is NOT a NetBird peer group.
# NB_ZABBIX_PROXY_GROUP is reserved for the NetBird access group.
GROUP_NAME="${NB_ZABBIX_PROXYGROUP_NAME:-$CUSTOMER}"
[[ -n "$GROUP_NAME" ]] || { echo "Zabbix Proxy-Gruppenname fehlt." >&2; exit 1; }
[[ "$LAN_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Keine gueltige LAN-IPv4: $LAN_IP" >&2; exit 1; }

api(){
  local method="$1" params="$2" payload
  payload=$(jq -nc --arg method "$method" --argjson params "$params" '{jsonrpc:"2.0",method:$method,params:$params,id:1}')
  curl -fsS --connect-timeout 8 --max-time 30 "$API_URL" \
    -H 'Content-Type: application/json-rpc' -H "Authorization: Bearer $API_TOKEN" --data "$payload"
}

result(){
  local response="$1"
  if jq -e '.error' <<<"$response" >/dev/null; then
    jq -r '.error | "\(.message): \(.data // "")"' <<<"$response" >&2
    exit 1
  fi
  jq -c '.result' <<<"$response"
}

r=$(api proxygroup.get "$(jq -nc --arg n "$GROUP_NAME" '{output:["proxy_groupid","name"],filter:{name:[$n]}}')")
groups=$(result "$r")
group_count=$(jq -r 'length' <<<"$groups")
(( group_count <= 1 )) || { echo "Doppelte Zabbix-Proxy-Gruppe: $GROUP_NAME" >&2; exit 1; }
gid=$(jq -r '.[0].proxy_groupid // empty' <<<"$groups")
if [[ -z "$gid" ]]; then
  r=$(api proxygroup.create "$(jq -nc --arg n "$GROUP_NAME" '{name:$n,failover_delay:"1m",min_online:"1"}')")
  gid=$(result "$r" | jq -r '.proxy_groupids[0] // empty')
fi
[[ -n "$gid" ]] || { echo "Zabbix Proxy-Gruppen-ID fehlt." >&2; exit 1; }

r=$(api proxy.get "$(jq -nc --arg n "$PROXY_NAME" '{output:["proxyid","name","operating_mode","local_address","local_port","proxy_groupid"],filter:{name:[$n]}}')")
existing=$(result "$r")
proxy_count=$(jq -r 'length' <<<"$existing")
(( proxy_count <= 1 )) || { echo "Mehrere Zabbix-Proxys mit Namen $PROXY_NAME." >&2; exit 1; }
pid=$(jq -r '.[0].proxyid // empty' <<<"$existing")

if [[ -z "$pid" ]]; then
  # A new proxy needs its PSK configuration; never overwrite a working PSK.
  [[ -s "$PSK_FILE" ]] || { echo "PSK-Datei fehlt oder leer: $PSK_FILE" >&2; exit 1; }
  psk=$(cat "$PSK_FILE")
  params=$(jq -nc --arg n "$PROXY_NAME" --arg gid "$gid" --arg a "$LAN_IP" \
    --arg ident "$PSK_IDENTITY" --arg psk "$psk" \
    '{name:$n,proxy_groupid:$gid,local_address:$a,local_port:"10051",operating_mode:0,tls_accept:2,tls_psk_identity:$ident,tls_psk:$psk}')
  r=$(api proxy.create "$params")
else
  # Update existing proxy group and active-agent LAN address only.
  # Keep operating mode, PSK, TLS settings and assigned hosts untouched.
  params=$(jq -nc --arg pid "$pid" --arg gid "$gid" --arg a "$LAN_IP" \
    '{proxyid:$pid,proxy_groupid:$gid,local_address:$a,local_port:"10051"}')
  r=$(api proxy.update "$params")
fi
result "$r" >/dev/null

# Read back and confirm that the API has stored the intended address.
r=$(api proxy.get "$(jq -nc --arg n "$PROXY_NAME" '{output:["proxyid","proxy_groupid","local_address","local_port","operating_mode"],filter:{name:[$n]}}')")
existing=$(result "$r")
jq -e --arg n "$PROXY_NAME" --arg gid "$gid" --arg address "$LAN_IP" \
  'length==1 and .[0].proxy_groupid==$gid and .[0].local_address==$address and .[0].local_port=="10051" and (.[0].operating_mode|tostring)=="0"' \
  <<<"$existing" >/dev/null || { echo "Proxy-Gruppe, Modus oder LAN-Adresse nach API-Registrierung nicht bestaetigt: $PROXY_NAME" >&2; exit 1; }

echo "Zabbix Proxy-Gruppe: $GROUP_NAME"
echo "Zabbix Proxy: $PROXY_NAME"
echo "Address for active agents: $LAN_IP:10051"
