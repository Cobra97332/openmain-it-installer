#!/usr/bin/env bash
set -Eeuo pipefail

# Apply explicit customer LAN proxy-group endpoints to an existing Zabbix Agent 2.
# Usage: openmain-zabbix-agent-group.sh IP_PRIMARY IP_BACKUP [IP_THIRD ...]
# Only Server and ServerActive are changed; all other parameters are preserved.

[[ "$EUID" -eq 0 ]] || { echo "Bitte als root ausfuehren." >&2; exit 1; }
(( $# >= 2 )) || { echo "Mindestens zwei LAN-IPs angeben." >&2; exit 1; }
for cmd in python3 ip awk zabbix_agent2 systemctl mktemp; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Befehl fehlt: $cmd" >&2; exit 1; }
done

CONFIG="/etc/zabbix/zabbix_agent2.conf"
[[ -f "$CONFIG" ]] || { echo "Agent2-Datei fehlt: $CONFIG" >&2; exit 1; }

LAN_IF=$(ip -4 route show default | awk 'NR==1{print $5}')
[[ -n "$LAN_IF" ]] || { echo "Kein LAN-Interface per Default-Route gefunden." >&2; exit 1; }
LAN_IP=$(ip -4 -o addr show dev "$LAN_IF" scope global | awk 'NR==1{split($4,a,"/");print a[1]}')
[[ -n "$LAN_IP" ]] || { echo "Keine LAN-IP auf $LAN_IF." >&2; exit 1; }

declare -A ADDRS=()
SERVER="127.0.0.1"
ACTIVE=""
LOCAL_FOUND=0
for addr in "$@"; do
  python3 -c 'import ipaddress,sys; ipaddress.IPv4Address(sys.argv[1])' "$addr" >/dev/null 2>&1 || { echo "Ungueltige IPv4: $addr" >&2; exit 1; }
  [[ -z "${ADDRS[$addr]:-}" ]] || { echo "Doppelte IPv4: $addr" >&2; exit 1; }
  ADDRS[$addr]=1
  [[ "$addr" == "$LAN_IP" ]] && LOCAL_FOUND=1
  SERVER+=",$addr"
  [[ -z "$ACTIVE" ]] && ACTIVE="$addr:10051" || ACTIVE+=";$addr:10051"
done
(( LOCAL_FOUND == 1 )) || { echo "Eigene LAN-IP $LAN_IP fehlt in der Proxy-Liste. Keine Aenderung." >&2; exit 1; }

TMP=$(mktemp "${CONFIG}.XXXXXX")
trap 'rm -f "$TMP"' EXIT
awk -v svr="$SERVER" -v active="$ACTIVE" '
  BEGIN{done_s=0;done_a=0}
  /^[[:space:]]*Server[[:space:]]*=/ { if(!done_s){print "Server=" svr; done_s=1}; next }
  /^[[:space:]]*ServerActive[[:space:]]*=/ { if(!done_a){print "ServerActive=" active; done_a=1}; next }
  {print}
  END{if(!done_s)print "Server=" svr; if(!done_a)print "ServerActive=" active}
' "$CONFIG" > "$TMP"
chown --reference="$CONFIG" "$TMP"
chmod --reference="$CONFIG" "$TMP"
zabbix_agent2 -T -c "$TMP" >/dev/null || { echo "Neue Zabbix-Agent-Konfiguration ungueltig. Keine Aenderung." >&2; exit 1; }
BACKUP="${CONFIG}.bak-$(date +%Y%m%d-%H%M%S)"
cp -p "$CONFIG" "$BACKUP"
mv -f "$TMP" "$CONFIG"
if ! systemctl restart zabbix-agent2 || ! systemctl is-active --quiet zabbix-agent2; then
  cp -p "$BACKUP" "$CONFIG"
  systemctl restart zabbix-agent2 || true
  echo "Agent2-Start fehlgeschlagen; Konfiguration aus Backup wiederhergestellt." >&2
  exit 1
fi
echo "Agent2 Proxy-Gruppe eingerichtet; Backup: $BACKUP"
grep -E '^(Server|ServerActive|Hostname)=' "$CONFIG"
