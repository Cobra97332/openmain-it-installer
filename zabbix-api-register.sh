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

api(){
  local method="$1" params="$2" payload
  payload=$(jq -nc --arg method "$method" --argjson params "$params" '{jsonrpc:"2.0",method:$method,params:$params,id:1}')
  # A server/proxy error can echo request secrets. Never relay raw diagnostics.
  curl -fsS --connect-timeout 8 --max-time 30 "$API_URL" \
    -H 'Content-Type: application/json-rpc' -H "Authorization: Bearer $API_TOKEN" --data "$payload" 2>/dev/null \
    || { echo "Zabbix-API-Transportfehler bei $method (Details unterdrueckt)." >&2; return 1; }
}

result(){
  local response="$1"
  if ! jq -e 'type=="object" and .jsonrpc=="2.0" and .id==1 and
      (has("error")|not) and has("result") and .result!=null' <<<"$response" >/dev/null 2>&1; then
    echo "Zabbix-API-Fehler oder ungueltige Antwort (Details unterdrueckt)." >&2
    return 1
  fi
  jq -c '.result' <<<"$response"
}

r=$(api proxy.get "$(jq -nc --arg n "$PROXY_NAME" '{output:["proxyid","name","operating_mode","local_address","local_port","proxy_groupid","tls_accept","tls_psk_identity"],filter:{name:[$n]}}')")
existing=$(result "$r")
jq -e --arg name "$PROXY_NAME" 'type=="array" and all(.[]; .name==$name and (.proxyid|type=="string" and length>0))' <<<"$existing" >/dev/null 2>&1 || { echo "Ungueltige Proxy-Liste." >&2; exit 1; }
proxy_count=$(jq -r 'length' <<<"$existing")
(( proxy_count <= 1 )) || { echo "Mehrere Zabbix-Proxys mit Namen $PROXY_NAME." >&2; exit 1; }
pid=$(jq -r '.[0].proxyid // empty' <<<"$existing")

if [[ -n "$pid" ]]; then
  [[ ! -e "$PSK_PENDING" ]] || { echo "Neuer lokaler PSK trifft auf bestehenden Proxy. TLS-Bestand manuell abgleichen; keine Aenderung." >&2; exit 1; }
  jq -e --arg ident "$PSK_IDENTITY" '
    .[0] | (.operating_mode|tostring)=="0" and .tls_psk_identity==$ident and
    ((.tls_accept|tonumber) as $tls | [2,3,6,7] | index($tls)!=null)
  ' <<<"$existing" >/dev/null 2>&1 || { echo "Bestehende Proxy-TLS-Identitaet, TLS-Modus oder Betriebsart weicht ab. Keine Aenderung." >&2; exit 1; }
fi

r=$(api proxygroup.get "$(jq -nc --arg n "$GROUP_NAME" '{output:["proxy_groupid","name"],filter:{name:[$n]}}')")
groups=$(result "$r")
jq -e --arg name "$GROUP_NAME" 'type=="array" and all(.[]; .name==$name and (.proxy_groupid|type=="string" and length>0))' <<<"$groups" >/dev/null 2>&1 || { echo "Ungueltige Proxy-Gruppenliste." >&2; exit 1; }
group_count=$(jq -r 'length' <<<"$groups")
(( group_count <= 1 )) || { echo "Doppelte Zabbix-Proxy-Gruppe: $GROUP_NAME" >&2; exit 1; }
gid=$(jq -r '.[0].proxy_groupid // empty' <<<"$groups")
if [[ -n "$pid" ]]; then
  old_gid=$(jq -r '.[0].proxy_groupid // "0"' <<<"$existing")
  [[ "$old_gid" == 0 || "$old_gid" == "$gid" ]] || { echo "Proxy gehoert bereits einer anderen Gruppe an. Keine Aenderung." >&2; exit 1; }
fi
if [[ -z "$gid" ]]; then
  r=$(api proxygroup.create "$(jq -nc --arg n "$GROUP_NAME" '{name:$n,failover_delay:"1m",min_online:"1"}')")
  gid=$(result "$r" | jq -r '.proxy_groupids[0] // empty')
fi
[[ -n "$gid" ]] || { echo "Zabbix Proxy-Gruppen-ID fehlt." >&2; exit 1; }

if [[ -z "$pid" ]]; then
  # A new proxy needs its PSK configuration; never overwrite a working PSK.
  [[ -s "$PSK_FILE" ]] || { echo "PSK-Datei fehlt oder leer: $PSK_FILE" >&2; exit 1; }
  [[ -n "$PSK_IDENTITY" ]] || { echo "PSK-Identitaet fehlt." >&2; exit 1; }
  psk=$(cat "$PSK_FILE" 2>/dev/null) || { echo "PSK nicht lesbar." >&2; exit 1; }
  [[ "$psk" =~ ^[[:xdigit:]]{32,512}$ && $(( ${#psk} % 2 )) == 0 ]] || { echo "Ungueltiger PSK." >&2; exit 1; }
  params=$(jq -nc --arg n "$PROXY_NAME" --arg gid "$gid" --arg a "$LAN_IP" \
    --arg ident "$PSK_IDENTITY" --arg psk "$psk" \
    '{name:$n,proxy_groupid:$gid,local_address:$a,local_port:"10051",operating_mode:0,tls_accept:2,tls_psk_identity:$ident,tls_psk:$psk}')
  r=$(api proxy.create "$params")
  created=$(result "$r")
  jq -e '.proxyids | type=="array" and length==1 and (.[0]|type=="string" and length>0)' <<<"$created" >/dev/null 2>&1 \
    || { echo "Proxy-Erstellung nicht bestaetigt; TLS-Zustand bleibt offen." >&2; exit 1; }
  rm -f -- "$PSK_PENDING"
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
  <<<"$existing" >/dev/null 2>&1 || { echo "Proxy-Gruppe, Modus oder LAN-Adresse nach API-Registrierung nicht bestaetigt: $PROXY_NAME" >&2; exit 1; }

echo "Zabbix Proxy-Gruppe: $GROUP_NAME"
echo "Zabbix Proxy: $PROXY_NAME"
echo "Address for active agents: $LAN_IP:10051"
