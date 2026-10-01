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

slug=$(printf '%s' "$CUSTOMER" | iconv -f UTF-8 -t ASCII//TRANSLIT 2>/dev/null || printf '%s' "$CUSTOMER")
slug=$(printf '%s' "$slug" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g')
GROUP_NAME="${NB_ZABBIX_PROXY_GROUP:-proxy-${slug}}"

api(){
  local method="$1" params="$2" payload
  payload=$(jq -nc --arg method "$method" --argjson params "$params" '{jsonrpc:"2.0",method:$method,params:$params,id:1}')
  curl -fsS "$API_URL" -H 'Content-Type: application/json-rpc' -H "Authorization: Bearer $API_TOKEN" --data "$payload"
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
gid=$(result "$r" | jq -r '.[0].proxy_groupid // empty')
if [[ -z "$gid" ]]; then
  r=$(api proxygroup.create "$(jq -nc --arg n "$GROUP_NAME" '{name:$n,failover_delay:"1m",min_online:"1"}')")
  gid=$(result "$r" | jq -r '.proxy_groupids[0]')
fi

psk=$(cat "$PSK_FILE")
r=$(api proxy.get "$(jq -nc --arg n "$PROXY_NAME" '{output:["proxyid","name"],filter:{name:[$n]}}')")
pid=$(result "$r" | jq -r '.[0].proxyid // empty')

params=$(jq -nc --arg n "$PROXY_NAME" --arg gid "$gid" --arg a "$LAN_IP" --arg ident "$PSK_IDENTITY" --arg psk "$psk"   '{name:$n,proxy_groupid:$gid,local_address:$a,local_port:"10051",operating_mode:0,tls_accept:2,tls_psk_identity:$ident,tls_psk:$psk}')

if [[ -z "$pid" ]]; then
  r=$(api proxy.create "$params")
else
  params=$(jq -c --arg pid "$pid" '. + {proxyid:$pid}' <<<"$params")
  r=$(api proxy.update "$params")
fi
result "$r" >/dev/null

echo "Zabbix Proxy-Gruppe: $GROUP_NAME"
echo "Zabbix Proxy: $PROXY_NAME"
echo "Address for active agents: $LAN_IP:10051"
