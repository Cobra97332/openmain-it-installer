#!/usr/bin/env bash
set -Eeuo pipefail

VERSION="3.0"
MGMT_URL="${NB_MANAGEMENT_URL:-https://netbird.openmain-it.de}"
API_TOKEN="${NB_API_TOKEN:-}"
SETUP_KEY="${NB_SETUP_KEY:-}"
CT_PASSWORD="${NB_CT_PASSWORD:-}"
ADMIN_USER="${NB_ADMIN_USER:-omadmin}"
CUSTOMER="${NB_CUSTOMER_NAME:-}"
HOSTNAME_OVERRIDE="${NB_HOSTNAME:-}"
ROLE="${NB_ROLE:-primary}"
PRIMARY_METRIC="${NB_PRIMARY_METRIC:-100}"
BACKUP_METRIC="${NB_BACKUP_METRIC:-200}"
ZABBIX_ENABLED="${NB_ZABBIX_ENABLED:-1}"
ZABBIX_SERVER="${NB_ZABBIX_SERVER:-100.107.91.6}"
METRICS_ENABLED="${NB_METRICS_ENABLED:-1}"
METRICS_PORT="${NB_METRICS_PORT:-9191}"
METRICS_DETAIL_PORT="${NB_METRICS_DETAIL_PORT:-9192}"
METRICS_GROUP="${NB_METRICS_GROUP:-NetBird-Metrics}"
MONITORING_GROUP="${NB_MONITORING_GROUP:-Monitoring}"
PROMETHEUS_NETBIRD_IP="${NB_PROMETHEUS_NETBIRD_IP:-100.107.91.6}"
METRICS_POLICY_NAME="${NB_METRICS_POLICY_NAME:-OpenMain Prometheus -> NetBird Client Metrics}"
METRICS_HELPER_URL="${NB_METRICS_HELPER_URL:-https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/netbird-prometheus/enable-client-metrics.sh}"
LAN_IF_OVERRIDE="${NB_LAN_INTERFACE:-}"
SSH_PUBLIC_KEY="${NB_SSH_PUBLIC_KEY:-ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJc8VZvZ7o/8emKoGC7UXPiOMP8PSxch6P2rUGNio8Vi Stefan}"
GLOBAL_GROUP="Kunden"
CUSTOMER_LABEL_PREFIX="${NB_CUSTOMER_LABEL_PREFIX:-Kunde:}"
JSON_SOCKET="/var/run/netbird-http.sock"
STATE_FILE="/var/lib/netbird-kundenrouter/state.json"
NFT_FILE="/etc/nftables.d/netbird-kundenrouter.nft"

log(){ printf '\033[1;32m[+]\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die(){ printf '\033[1;31m[FEHLER]\033[0m %s\n' "$*" >&2; exit 1; }

usage(){
cat <<'EOF'
NetBird Kundenrouter
  --role primary|backup
  --customer "Firma"
  --hostname NAME
  --management-url URL
  --setup-key KEY
  --api-token TOKEN
  --zabbix-server HOST
  --lan-interface IFACE
  --metrics-port PORT
  --metrics-detail-port PORT
  --prometheus-netbird-ip IP
  --metrics-group NAME
  --monitoring-group NAME
  --no-metrics
  --no-zabbix
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --role) ROLE="$2"; shift 2;;
    --customer) CUSTOMER="$2"; shift 2;;
    --hostname) HOSTNAME_OVERRIDE="$2"; shift 2;;
    --management-url) MGMT_URL="$2"; shift 2;;
    --setup-key) SETUP_KEY="$2"; shift 2;;
    --api-token) API_TOKEN="$2"; shift 2;;
    --zabbix-server) ZABBIX_SERVER="$2"; shift 2;;
    --lan-interface) LAN_IF_OVERRIDE="$2"; shift 2;;
    --metrics-port) METRICS_PORT="$2"; shift 2;;
    --metrics-detail-port) METRICS_DETAIL_PORT="$2"; shift 2;;
    --prometheus-netbird-ip) PROMETHEUS_NETBIRD_IP="$2"; shift 2;;
    --metrics-group) METRICS_GROUP="$2"; shift 2;;
    --monitoring-group) MONITORING_GROUP="$2"; shift 2;;
    --no-metrics) METRICS_ENABLED=0; shift;;
    --no-zabbix) ZABBIX_ENABLED=0; shift;;
    -h|--help) usage; exit 0;;
    *) die "Unbekannte Option: $1";;
  esac
done

[[ $EUID -eq 0 ]] || die "Bitte als root ausführen."
[[ "$ROLE" == primary || "$ROLE" == backup ]] || die "--role muss primary oder backup sein."
[[ "$METRICS_ENABLED" == 0 || "$METRICS_ENABLED" == 1 ]] || die "NB_METRICS_ENABLED muss 0 oder 1 sein."
[[ "$METRICS_PORT" =~ ^[0-9]+$ ]] || die "Ungültiger Metrics-Port: $METRICS_PORT"
(( METRICS_PORT >= 1 && METRICS_PORT <= 65535 )) || die "Ungültiger Metrics-Port: $METRICS_PORT"
[[ "$METRICS_DETAIL_PORT" =~ ^[0-9]+$ ]] || die "Ungültiger Metrics-Detail-Port: $METRICS_DETAIL_PORT"
(( METRICS_DETAIL_PORT >= 1 && METRICS_DETAIL_PORT <= 65535 )) || die "Ungültiger Metrics-Detail-Port: $METRICS_DETAIL_PORT"
[[ "$METRICS_DETAIL_PORT" != "$METRICS_PORT" ]] || die "Metrics-Port und Metrics-Detail-Port müssen verschieden sein."
[[ -n "$CUSTOMER" ]] || read -r -p "Firmenname: " CUSTOMER
[[ -n "$SETUP_KEY" ]] || { read -r -s -p "NetBird Setup Key: " SETUP_KEY; echo; }
[[ -n "$API_TOKEN" ]] || { read -r -s -p "NetBird API Token: " API_TOKEN; echo; }
[[ -n "$CUSTOMER" && -n "$SETUP_KEY" && -n "$API_TOKEN" ]] || die "Pflichtangabe fehlt."

MGMT_URL="${MGMT_URL%/}"
API_ROOT="$MGMT_URL/api"
HOSTNAME_LOCAL="${HOSTNAME_OVERRIDE:-$(hostname -s)}"
ROUTER_METRIC="$PRIMARY_METRIC"
[[ "$ROLE" == backup ]] && ROUTER_METRIC="$BACKUP_METRIC"

api(){
  local method="$1" path="$2" data="${3:-}" tmp code
  tmp=$(mktemp)
  local args=(-sS -X "$method" "$API_ROOT$path" -H "Authorization: Token $API_TOKEN" -H 'Accept: application/json')
  [[ -n "$data" ]] && args+=(-H 'Content-Type: application/json' --data-raw "$data")
  code=$(curl "${args[@]}" -o "$tmp" -w '%{http_code}') || { cat "$tmp" >&2; rm -f "$tmp"; die "API-Aufruf fehlgeschlagen: $method $path"; }
  if [[ ! "$code" =~ ^2 ]]; then
    cat "$tmp" >&2
    rm -f "$tmp"
    die "API $method $path -> HTTP $code"
  fi
  cat "$tmp"
  rm -f "$tmp"
}

install_packages(){
  apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl gnupg jq nftables python3 iproute2 openssh-server sudo
  if ! command -v netbird >/dev/null 2>&1; then
    install -d -m 0755 /usr/share/keyrings
    curl -fsSL https://pkgs.netbird.io/debian/public.key | gpg --dearmor --yes -o /usr/share/keyrings/netbird-archive-keyring.gpg
    printf '%s\n' 'deb [signed-by=/usr/share/keyrings/netbird-archive-keyring.gpg] https://pkgs.netbird.io/debian stable main' > /etc/apt/sources.list.d/netbird.list
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y netbird
  fi
}

setup_access(){
  if ! id "$ADMIN_USER" >/dev/null 2>&1; then
    useradd -m -s /bin/bash "$ADMIN_USER"
  fi
  usermod -aG sudo "$ADMIN_USER"

  if [[ -n "$CT_PASSWORD" ]]; then
    printf '%s:%s\n' "$ADMIN_USER" "$CT_PASSWORD" | chpasswd
  fi

  install -d -m 0700 /root/.ssh
  touch /root/.ssh/authorized_keys
  grep -qxF "$SSH_PUBLIC_KEY" /root/.ssh/authorized_keys 2>/dev/null || printf '%s\n' "$SSH_PUBLIC_KEY" >> /root/.ssh/authorized_keys
  chown root:root /root/.ssh/authorized_keys
  chmod 0600 /root/.ssh/authorized_keys

  install -d -m 0700 -o "$ADMIN_USER" -g "$ADMIN_USER" "/home/$ADMIN_USER/.ssh"
  touch "/home/$ADMIN_USER/.ssh/authorized_keys"
  grep -qxF "$SSH_PUBLIC_KEY" "/home/$ADMIN_USER/.ssh/authorized_keys" 2>/dev/null || printf '%s\n' "$SSH_PUBLIC_KEY" >> "/home/$ADMIN_USER/.ssh/authorized_keys"
  chown "$ADMIN_USER:$ADMIN_USER" "/home/$ADMIN_USER/.ssh/authorized_keys"
  chmod 0600 "/home/$ADMIN_USER/.ssh/authorized_keys"

  install -d -m 0755 /etc/ssh/sshd_config.d
  local password_auth="no"
  [[ -n "$CT_PASSWORD" ]] && password_auth="yes"
  cat > /etc/ssh/sshd_config.d/00-openmain-access.conf <<EOF
PermitRootLogin prohibit-password
PubkeyAuthentication yes
PasswordAuthentication $password_auth
EOF
  systemctl enable --now ssh >/dev/null 2>&1 || systemctl enable --now sshd >/dev/null 2>&1 || true
  systemctl restart ssh >/dev/null 2>&1 || systemctl restart sshd >/dev/null 2>&1 || true
  log "SSH-Zugang: root per Key, $ADMIN_USER per Key/Passwort."
}

detect_lan(){
  LAN_IF="$LAN_IF_OVERRIDE"
  if [[ -z "$LAN_IF" ]]; then
    LAN_IF=$(ip -4 route show default | awk 'NR==1{print $5}')
  fi
  [[ -n "$LAN_IF" ]] || die "Kein LAN-Interface gefunden."
  ip link show dev "$LAN_IF" >/dev/null 2>&1 || die "LAN-Interface '$LAN_IF' existiert nicht."

  LAN_NET=$(ip -4 route show dev "$LAN_IF" proto kernel scope link | awk '$1 ~ /^[0-9]+\./ && $1 ~ /\// {print $1; exit}')
  [[ -n "$LAN_NET" ]] || die "LAN-Netz auf '$LAN_IF' konnte nicht erkannt werden."

  PREFIX="${LAN_NET#*/}"
  (( PREFIX >= 16 && PREFIX <= 24 )) || die "Nur /16 bis /24 unterstützt: $LAN_NET"
  log "LAN: $LAN_IF / $LAN_NET"
}

enable_forwarding(){
  printf 'net.ipv4.ip_forward=1\n' > /etc/sysctl.d/99-netbird-kundenrouter.conf
  sysctl -w net.ipv4.ip_forward=1 >/dev/null
  [[ "$(cat /proc/sys/net/ipv4/ip_forward)" == 1 ]] || die "IPv4 forwarding konnte nicht aktiviert werden."
}

wait_netbird(){
  for _ in {1..60}; do
    systemctl is-active --quiet netbird 2>/dev/null && [[ -S /var/run/netbird.sock ]] && return 0
    sleep 1
  done
  die "NetBird-Daemon nicht bereit."
}

connect_netbird(){
  local up_args
  systemctl enable --now netbird >/dev/null 2>&1 || true
  wait_netbird
  netbird service reconfigure --enable-json-socket >/dev/null 2>&1 || true
  systemctl restart netbird
  wait_netbird

  # Bei einem Retry können bereits aktive Verbindungen sonst neue "netbird up"
  # Flags ignorieren. Ein kontrolliertes down/up macht die Konfiguration
  # deterministisch und aktiviert Metrics bereits beim ersten Enrollment.
  netbird down >/dev/null 2>&1 || true

  up_args=(
    up
    --setup-key "$SETUP_KEY"
    --management-url "$MGMT_URL"
    --disable-firewall
  )
  if [[ "$METRICS_ENABLED" == 1 ]]; then
    up_args+=(
      --enable-local-metrics
      --local-metrics-address "127.0.0.1:$METRICS_PORT"
    )
  fi

  netbird "${up_args[@]}"
  netbird service reconfigure --enable-json-socket >/dev/null 2>&1 || true
  systemctl restart netbird
  wait_netbird
}

find_peer(){
  local peers
  peers=$(api GET "/peers")
  PEER_ID=$(jq -r --arg n "$HOSTNAME_LOCAL" '(if type=="array" then . else [] end)[] | select(.name==$n or .hostname==$n or .dns_label==$n) | .id' <<<"$peers" | head -n1)
  [[ -n "$PEER_ID" ]] || die "Peer '$HOSTNAME_LOCAL' nicht in NetBird gefunden."
  log "Peer: $PEER_ID"
}

ensure_group(){
  local name="$1" groups gid payload resp
  groups=$(api GET "/groups")
  gid=$(jq -r --arg n "$name" '(if type=="array" then . else [] end)[] | select(.name==$n) | .id' <<<"$groups" | head -n1)
  if [[ -z "$gid" ]]; then
    payload=$(jq -nc --arg n "$name" '{name:$n,peers:[]}')
    resp=$(api POST "/groups" "$payload")
    gid=$(jq -r '.id' <<<"$resp")
  fi
  [[ -n "$gid" && "$gid" != null ]] || die "Gruppe '$name' konnte nicht bestimmt werden."
  printf '%s' "$gid"
}

ensure_peer_in_group(){
  local gid="$1" g payload
  g=$(api GET "/groups/$gid")

  # GET /groups/{id} liefert peers als Objekte. PUT /groups/{id}
  # erwartet dagegen eine Liste von Peer-IDs (Strings).
  if jq -e --arg p "$PEER_ID" '[(.peers // [])[]? | if type=="object" then .id else . end] | index($p) != null' <<<"$g" >/dev/null; then
    return 0
  fi

  payload=$(jq -c --arg p "$PEER_ID" '{
    name: .name,
    peers: ([(.peers // [])[]? | if type=="object" then .id else . end] + [$p] | map(select(. != null and . != "")) | unique)
  }' <<<"$g")

  api PUT "/groups/$gid" "$payload" >/dev/null
}

setup_groups(){
  local customer_label_group customer_label_group_id

  GLOBAL_GROUP_ID=$(ensure_group "$GLOBAL_GROUP")
  COMPANY_GROUP_ID=$(ensure_group "$CUSTOMER")
  customer_label_group="${CUSTOMER_LABEL_PREFIX}${CUSTOMER}"
  customer_label_group_id=$(ensure_group "$customer_label_group")

  ensure_peer_in_group "$GLOBAL_GROUP_ID"
  ensure_peer_in_group "$COMPANY_GROUP_ID"
  ensure_peer_in_group "$customer_label_group_id"

  # Nur die bestehenden Funktionsgruppen werden für Netzwerkressourcen
  # verwendet. "Kunde:<Name>" dient ausschließlich als eindeutige Metadaten-
  # Kennzeichnung für Monitoring/Prometheus und erweitert keine Zugriffsrechte.
  GROUP_IDS=$(jq -nc --arg a "$GLOBAL_GROUP_ID" --arg b "$COMPANY_GROUP_ID" '[$a,$b]')
  log "Gruppen: $GLOBAL_GROUP + $CUSTOMER + $customer_label_group"
}

ensure_metrics_policy(){
  local policies policy_id payload

  policies=$(api GET "/policies")
  policy_id=$(jq -r --arg n "$METRICS_POLICY_NAME"     '(if type=="array" then . else [] end)[] | select(.name==$n) | .id // empty'     <<<"$policies" | head -n1)

  payload=$(jq -nc     --arg name "$METRICS_POLICY_NAME"     --arg src "$MONITORING_GROUP_ID"     --arg dst "$METRICS_GROUP_ID"     --arg port "$METRICS_PORT"     --arg detail_port "$METRICS_DETAIL_PORT"     '{
      name:$name,
      description:"Automatisch verwaltete OpenMain-Policy für NetBird Client Metrics und Relay/Peer-Details",
      enabled:true,
      source_posture_checks:[],
      rules:[{
        name:("Prometheus TCP " + $port + "," + $detail_port),
        description:"Prometheus Zugriff auf NetBird Client Metrics und Relay/Peer-Details",
        enabled:true,
        action:"accept",
        bidirectional:false,
        protocol:"tcp",
        ports:[$port,$detail_port],
        sources:[$src],
        destinations:[$dst]
      }]
    }')

  if [[ -n "$policy_id" ]]; then
    api PUT "/policies/$policy_id" "$payload" >/dev/null
    log "Metrics-Policy aktualisiert: $METRICS_POLICY_NAME"
  else
    api POST "/policies" "$payload" >/dev/null
    log "Metrics-Policy erstellt: $METRICS_POLICY_NAME"
  fi
}

ensure_setup_key_metrics_group(){
  local keys key_id current revoked payload

  keys=$(api GET "/setup-keys")
  key_id=$(jq -r --arg key "$SETUP_KEY"     '(if type=="array" then . else [] end)[] | select(.key==$key) | .id // empty'     <<<"$keys" | head -n1)

  if [[ -z "$key_id" ]]; then
    warn "Verwendeter Setup-Key konnte per API nicht eindeutig gefunden werden."
    warn "Neue Peers mit diesem Key ggf. manuell/über die NetBird-UI der Gruppe '$METRICS_GROUP' zuordnen."
    return 0
  fi

  current=$(jq -c --arg id "$key_id"     '(if type=="array" then . else [] end)[] | select(.id==$id)'     <<<"$keys" | head -n1)
  revoked=$(jq -r '.revoked // false' <<<"$current")

  if jq -e --arg gid "$METRICS_GROUP_ID"       '(.auto_groups // []) | index($gid) != null' <<<"$current" >/dev/null; then
    log "Setup-Key weist neue Peers bereits automatisch '$METRICS_GROUP' zu."
    return 0
  fi

  payload=$(jq -nc     --argjson groups "$(jq -c --arg gid "$METRICS_GROUP_ID"       '((.auto_groups // []) + [$gid] | unique)' <<<"$current")"     --argjson revoked "$revoked"     '{auto_groups:$groups,revoked:$revoked}')

  api PUT "/setup-keys/$key_id" "$payload" >/dev/null
  log "Setup-Key erweitert: neue Peers erhalten automatisch Gruppe '$METRICS_GROUP'."
}

setup_metrics(){
  [[ "$METRICS_ENABLED" == 1 ]] || {
    log "NetBird Client Metrics deaktiviert."
    return 0
  }

  local peers prometheus_peer_id helper netbird_ip

  METRICS_GROUP_ID=$(ensure_group "$METRICS_GROUP")
  MONITORING_GROUP_ID=$(ensure_group "$MONITORING_GROUP")

  ensure_peer_in_group "$METRICS_GROUP_ID"
  log "Metrics-Gruppe: $METRICS_GROUP"

  # Der gerade verwendete Setup-Key wird für zukünftige Rollouts vorbereitet.
  # Dadurch landen weitere Windows-/Linux-/Router-Peers, die denselben Key
  # verwenden, automatisch in der Metrics-Gruppe.
  ensure_setup_key_metrics_group

  peers=$(api GET "/peers")
  prometheus_peer_id=$(jq -r --arg ip "$PROMETHEUS_NETBIRD_IP"     '(if type=="array" then . else [] end)[] | select(.ip==$ip) | .id // empty'     <<<"$peers" | head -n1)

  if [[ -n "$prometheus_peer_id" ]]; then
    local saved_peer_id="$PEER_ID"
    PEER_ID="$prometheus_peer_id"
    ensure_peer_in_group "$MONITORING_GROUP_ID"
    PEER_ID="$saved_peer_id"
    log "Prometheus-Peer $PROMETHEUS_NETBIRD_IP in Gruppe $MONITORING_GROUP."
  else
    warn "Prometheus-Peer mit NetBird-IP $PROMETHEUS_NETBIRD_IP nicht gefunden."
    warn "Gruppe/Policy werden trotzdem angelegt; Prometheus-Peer ggf. einmalig der Gruppe '$MONITORING_GROUP' zuordnen."
  fi

  ensure_metrics_policy

  helper="/usr/local/sbin/openmain-netbird-client-metrics"
  curl -fsSL "$METRICS_HELPER_URL" -o "$helper"     || die "Metrics-Helper konnte nicht geladen werden: $METRICS_HELPER_URL"
  chmod 0755 "$helper"

  NETBIRD_CLIENT_METRICS_PORT="$METRICS_PORT" \
  NETBIRD_CLIENT_DETAIL_PORT="$METRICS_DETAIL_PORT" \
  "$helper"

  netbird_ip=$(netbird status --ipv4 2>/dev/null | awk 'NF {sub(/\/.*/, "", $1); print $1; exit}')
  [[ -n "$netbird_ip" ]] || die "NetBird-IP nach Metrics-Aktivierung nicht ermittelbar."
  METRICS_TARGET="$netbird_ip:$METRICS_PORT"

  log "NetBird Client Metrics aktiv: $METRICS_TARGET"
}

allocate_mapping(){
  local resources used networks nid rs addr
  networks=$(api GET "/networks")

  # Vorhandenes Mapping immer wiederverwenden. Das macht den Installer
  # bei First-Boot-Retries idempotent und verhindert Mapping-Wechsel.
  nid=$(jq -r --arg n "$CUSTOMER" '(if type=="array" then . else [] end)[] | select(.name==$n) | .id' <<<"$networks" | head -n1)
  if [[ -n "$nid" ]]; then
    rs=$(api GET "/networks/$nid/resources")
    addr=$(jq -r --arg n "BINAT-$CUSTOMER" '(if type=="array" then . else [] end)[] | select(.name==$n) | .address' <<<"$rs" | head -n1)
    if [[ -n "$addr" ]]; then
      VIRTUAL_NET="$addr"
      log "Vorhandenes Mapping wird wiederverwendet: $VIRTUAL_NET"
      return 0
    fi
  fi

  [[ "$ROLE" != backup ]] || die "Backup: bestehendes Mapping fuer '$CUSTOMER' nicht gefunden. Primary zuerst installieren."

  resources="$networks"
  used=$(mktemp)
  : > "$used"
  while read -r nid; do
    [[ -n "$nid" ]] || continue
    api GET "/networks/$nid/resources" | jq -r '(if type=="array" then . else [] end)[]?.address // empty' >> "$used"
  done < <(jq -r '(if type=="array" then . else [] end)[]?.id // empty' <<<"$resources")

  VIRTUAL_NET=$(python3 - "$LAN_NET" "$used" <<'PY'
import ipaddress,sys
lan=ipaddress.ip_network(sys.argv[1],strict=False)
used=[]
for l in open(sys.argv[2],encoding='utf-8'):
    try: used.append(ipaddress.ip_network(l.strip(),strict=False))
    except: pass
def free(n): return not any(n.overlaps(u) for u in used)
if lan.prefixlen==24:
    for n in ipaddress.ip_network('10.30.0.0/16').subnets(new_prefix=24):
        if free(n): print(n); break
else:
    start=int(ipaddress.ip_address('10.40.0.0'))
    end=int(ipaddress.ip_address('10.49.255.255'))
    size=1 << (32-lan.prefixlen)
    cur=((start+size-1)//size)*size
    while cur+size-1<=end:
        n=ipaddress.ip_network((cur,lan.prefixlen))
        if free(n): print(n); break
        cur+=size
PY
)
  rm -f "$used"
  [[ -n "$VIRTUAL_NET" ]] || die "Kein freies Mapping-Netz gefunden."
}

ensure_network(){
  local networks payload resp resources rid routers router_id
  networks=$(api GET "/networks")
  NETWORK_ID=$(jq -r --arg n "$CUSTOMER" '(if type=="array" then . else [] end)[] | select(.name==$n) | .id' <<<"$networks" | head -n1)
  if [[ -z "$NETWORK_ID" ]]; then
    payload=$(jq -nc --arg n "$CUSTOMER" '{name:$n,description:"NetBird Kundenrouter"}')
    resp=$(api POST "/networks" "$payload")
    NETWORK_ID=$(jq -r '.id' <<<"$resp")
  fi

  resources=$(api GET "/networks/$NETWORK_ID/resources")
  rid=$(jq -r --arg n "BINAT-$CUSTOMER" '(if type=="array" then . else [] end)[] | select(.name==$n) | .id' <<<"$resources" | head -n1)
  payload=$(jq -nc --arg n "BINAT-$CUSTOMER" --arg a "$VIRTUAL_NET" --argjson g "$GROUP_IDS" '{name:$n,address:$a,enabled:true,groups:$g}')
  if [[ -z "$rid" ]]; then
    resp=$(api POST "/networks/$NETWORK_ID/resources" "$payload")
    RESOURCE_ID=$(jq -r '.id' <<<"$resp")
  else
    RESOURCE_ID="$rid"
    api PUT "/networks/$NETWORK_ID/resources/$RESOURCE_ID" "$payload" >/dev/null
  fi

  routers=$(api GET "/networks/$NETWORK_ID/routers")
  router_id=$(jq -r --arg p "$PEER_ID" '(if type=="array" then . else [] end)[] | select(.peer==$p) | .id' <<<"$routers" | head -n1)
  if [[ -z "$router_id" ]]; then
    payload=$(jq -nc --arg p "$PEER_ID" --argjson m "$ROUTER_METRIC" '{peer:$p,metric:$m,masquerade:false,enabled:true}')
    resp=$(api POST "/networks/$NETWORK_ID/routers" "$payload")
    ROUTER_ID=$(jq -r '.id' <<<"$resp")
  else
    ROUTER_ID="$router_id"
  fi
}

write_nft(){
  local nbif="wt0" cfg
  if [[ -S "$JSON_SOCKET" ]]; then
    cfg=$(curl -s --unix-socket "$JSON_SOCKET" -X POST -H 'Content-Type: application/json' -d '{}' http://localhost/daemon.DaemonService/GetConfig || true)
    nbif=$(jq -r '.interfaceName // .interface_name // "wt0"' <<<"$cfg")
  fi
  install -d -m 0755 /etc/nftables.d
  cat > "$NFT_FILE" <<EOF
table ip mein_binat {
  chain prerouting {
    type nat hook prerouting priority dstnat; policy accept;
    iifname "$nbif" dnat ip prefix to ip daddr map { $VIRTUAL_NET : $LAN_NET }
  }
  chain postrouting {
    type nat hook postrouting priority srcnat; policy accept;
    oifname "$LAN_IF" ip saddr 100.64.0.0/10 counter masquerade
  }
}
EOF
  if [[ ! -f /etc/nftables.conf ]]; then
    printf '#!/usr/sbin/nft -f\ninclude "/etc/nftables.d/*.nft"\n' > /etc/nftables.conf
  elif ! grep -qF 'include "/etc/nftables.d/*.nft"' /etc/nftables.conf; then
    printf '\ninclude "/etc/nftables.d/*.nft"\n' >> /etc/nftables.conf
  fi
  nft -c -f /etc/nftables.conf
  systemctl enable --now nftables
  systemctl restart nftables
}


setup_zabbix(){
  [[ "$ZABBIX_ENABLED" == 1 ]] || { log "Zabbix Proxy deaktiviert."; return 0; }

  local helper=""
  for candidate in /usr/local/sbin/zabbix-proxy-install /root/zabbix-proxy-install.sh /usr/local/sbin/zabbix-proxy-install.sh; do
    if [[ -x "$candidate" ]]; then
      helper="$candidate"
      break
    fi
  done

  [[ -n "$helper" ]] || die "Zabbix-Helferskript fehlt. Erwartet: /usr/local/sbin/zabbix-proxy-install"

  "$helper"     --customer "$CUSTOMER"     --role "$ROLE"     --server "$ZABBIX_SERVER"     --hostname "$HOSTNAME_LOCAL"
}

save_state(){
  install -d -m 0700 "$(dirname "$STATE_FILE")"
  jq -nc     --arg customer "$CUSTOMER"     --arg role "$ROLE"     --arg lan "$LAN_NET"     --arg virtual "$VIRTUAL_NET"     --arg peer "$PEER_ID"     --arg network "$NETWORK_ID"     --arg resource "$RESOURCE_ID"     --arg router "$ROUTER_ID"     --arg metrics_target "${METRICS_TARGET:-}"     --arg metrics_group "$METRICS_GROUP"     --arg metrics_detail_port "$METRICS_DETAIL_PORT"     --argjson metric "$ROUTER_METRIC"     --argjson metrics_enabled "$METRICS_ENABLED"     '{
      customer:$customer,
      role:$role,
      lan:$lan,
      virtual:$virtual,
      peer_id:$peer,
      network_id:$network,
      resource_id:$resource,
      router_id:$router,
      metric:$metric,
      metrics_enabled:($metrics_enabled == 1),
      metrics_target:$metrics_target,
      metrics_group:$metrics_group,
      metrics_detail_port:$metrics_detail_port
    }' > "$STATE_FILE"
  chmod 600 "$STATE_FILE"
}

main(){
  install_packages
  setup_access
  detect_lan
  enable_forwarding
  connect_netbird
  find_peer
  setup_groups
  setup_metrics
  allocate_mapping
  ensure_network
  write_nft
  setup_zabbix
  save_state
  log "Fertig: $CUSTOMER / $ROLE / Metric $ROUTER_METRIC / $VIRTUAL_NET -> $LAN_NET"
  if [[ "$ZABBIX_ENABLED" == 1 ]]; then
    log "Zabbix Proxy aktiv -> $ZABBIX_SERVER"
  fi
  if [[ "$METRICS_ENABLED" == 1 ]]; then
    log "NetBird Metrics: ${METRICS_TARGET:-unbekannt} / Details: TCP/$METRICS_DETAIL_PORT / Gruppe: $METRICS_GROUP"
  fi
  return 0
}

main "$@"
