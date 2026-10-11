#!/usr/bin/env bash
# Never trace credentials, including when invoked with bash -x/-v.
set +xv
set -Eeuo pipefail
umask 077
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
RUNTIME="$SCRIPT_DIR/netbird-router/common/runtime.py"
PROFILE_EXISTED=0
NETBIRD_PROFILE=""
NETBIRD_INTERFACE=""
[[ -f "$RUNTIME" ]] || { echo "Bitte das vollstaendige Installer-Bundle verwenden." >&2; exit 1; }
if [[ "${1:-}" == --config ]]; then
  config_file=$(mktemp)
  trap 'rm -f -- "$config_file"' EXIT
  python3 "$RUNTIME" config "$2" > "$config_file" || exit 1
  while IFS= read -r -d '' setting; do export "${setting?}"; done < "$config_file"
  rm -f -- "$config_file"
  trap - EXIT
  shift 2
fi

MGMT_URL="${NB_MANAGEMENT_URL:-https://netbird.openmain-it.de}"
API_TOKEN="${NB_API_TOKEN:-}"
SETUP_KEY="${NB_SETUP_KEY:-}"
CUSTOMER="${NB_CUSTOMER_NAME:-}"
HOSTNAME_OVERRIDE="${NB_HOSTNAME:-}"
ROLE="${NB_ROLE:-}"
PRIMARY_METRIC="${NB_PRIMARY_METRIC:-100}"
BACKUP_METRIC="${NB_BACKUP_METRIC:-200}"
ZABBIX_ENABLED="${NB_ZABBIX_ENABLED:-1}"
ZABBIX_SERVER="${NB_ZABBIX_SERVER:-100.107.91.6}"
ZABBIX_PROXY_GROUP="${NB_ZABBIX_PROXY_GROUP:-Zabbix-Kunden-Proxies}"
ZABBIX_SERVER_GROUP="${NB_ZABBIX_SERVER_GROUP:-Zabbix-Server}"
ZABBIX_POLICY_NAME="${NB_ZABBIX_POLICY_NAME:-Zabbix Proxy to Server}"
METRICS_ENABLED="${NB_METRICS_ENABLED:-0}"
METRICS_PORT="${NB_METRICS_PORT:-9191}"
METRICS_DETAIL_PORT="${NB_METRICS_DETAIL_PORT:-9192}"
METRICS_GROUP="${NB_METRICS_GROUP:-NetBird-Metrics}"
MONITORING_GROUP="${NB_MONITORING_GROUP:-Monitoring}"
PROMETHEUS_NETBIRD_IP="${NB_PROMETHEUS_NETBIRD_IP:-100.107.91.6}"
METRICS_POLICY_NAME="${NB_METRICS_POLICY_NAME:-OpenMain Prometheus -> NetBird Client Metrics}"
LAN_IF_OVERRIDE="${NB_LAN_INTERFACE:-}"
SSH_PUBLIC_KEY="${NB_SSH_PUBLIC_KEY:-}"
GLOBAL_GROUP="Kunden"
CUSTOMER_MARKER_PREFIX="${NB_CUSTOMER_MARKER_PREFIX:-Kunde:}"
STATE_FILE="/var/lib/netbird-kundenrouter/state.json"
NFT_FILE="/etc/nftables.d/openmain-binat.nft"

log(){ printf '\033[1;32m[+]\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die(){ printf '\033[1;31m[FEHLER]\033[0m %s\n' "$*" >&2; exit 1; }

usage(){
cat <<'EOF'
NetBird Kundenrouter
  --config /root/router.json (als erste Option; JSON, root:root 0600)
  --role primary|backup
  --customer "Firma"
  --hostname NAME
  --management-url URL
  --setup-key KEY
  --api-token TOKEN
  --zabbix-server HOST
  --zabbix-proxy-group NAME
  --zabbix-server-group NAME
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
    --zabbix-proxy-group) ZABBIX_PROXY_GROUP="$2"; shift 2;;
    --zabbix-server-group) ZABBIX_SERVER_GROUP="$2"; shift 2;;
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
[[ -n "$ROLE" ]] || read -r -p "Rolle (primary/backup): " ROLE
[[ "$ROLE" == primary || "$ROLE" == backup ]] || die "--role muss primary oder backup sein."
[[ "$METRICS_ENABLED" == 0 || "$METRICS_ENABLED" == 1 ]] || die "NB_METRICS_ENABLED muss 0 oder 1 sein."
[[ "$METRICS_PORT" =~ ^[0-9]+$ ]] || die "Ungültiger Metrics-Port: $METRICS_PORT"
(( METRICS_PORT >= 1 && METRICS_PORT <= 65535 )) || die "Ungültiger Metrics-Port: $METRICS_PORT"
[[ "$METRICS_DETAIL_PORT" =~ ^[0-9]+$ ]] || die "Ungültiger Metrics-Detail-Port: $METRICS_DETAIL_PORT"
(( METRICS_DETAIL_PORT >= 1 && METRICS_DETAIL_PORT <= 65535 )) || die "Ungültiger Metrics-Detail-Port: $METRICS_DETAIL_PORT"
[[ "$METRICS_DETAIL_PORT" != "$METRICS_PORT" ]] || die "Metrics-Port und Metrics-Detail-Port müssen verschieden sein."
[[ -n "$CUSTOMER" ]] || read -r -p "Firmenname: " CUSTOMER
[[ -n "$API_TOKEN" ]] || { read -r -s -p "NetBird API Token: " API_TOKEN; echo; }
[[ -n "$CUSTOMER" && -n "$API_TOKEN" ]] || die "Pflichtangabe fehlt."

[[ "$CUSTOMER" != *$'\n'* && "$CUSTOMER" != *$'\r'* ]] || die "Ungueltiger Kundenname."
[[ "$MGMT_URL" == https://* ]] || die "Management-URL muss HTTPS verwenden."
[[ "$PRIMARY_METRIC" == 100 && "$BACKUP_METRIC" == 200 ]] || die "Routermetriken muessen 100/200 sein."
python3 "$RUNTIME" platform || die "Plattform nicht unterstuetzt."
if [[ "$ZABBIX_ENABLED" == 1 && -z "${NB_ZABBIX_API_TOKEN:-}" ]]; then
  read -r -s -p "Zabbix API Token: " NB_ZABBIX_API_TOKEN; echo
  export NB_ZABBIX_API_TOKEN
fi
[[ "$ZABBIX_ENABLED" == 0 || -n "${NB_ZABBIX_API_TOKEN:-}" ]] || die "Zabbix API Token erforderlich."
MGMT_URL="${MGMT_URL%/}"
API_ROOT="$MGMT_URL/api"
HOSTNAME_LOCAL="${HOSTNAME_OVERRIDE:-$(hostname -s)}"
ROUTER_METRIC="$PRIMARY_METRIC"
[[ "$ROLE" == backup ]] && ROUTER_METRIC="$BACKUP_METRIC"

api(){
  local method="$1" path="$2" data="${3:-}" tmp code
  tmp=$(mktemp)
  local args=(-sS --connect-timeout 10 --max-time 45 -X "$method" "$API_ROOT$path" -H "Authorization: Token $API_TOKEN" -H 'Accept: application/json')
  [[ -n "$data" ]] && args+=(-H 'Content-Type: application/json' --data-raw "$data")
  code=$(curl "${args[@]}" -o "$tmp" -w '%{http_code}' 2>/dev/null) || { rm -f "$tmp"; die "API-Aufruf fehlgeschlagen: $method $path"; }
  if [[ ! "$code" =~ ^2 ]]; then
    rm -f "$tmp"
    die "API $method $path -> HTTP $code"
  fi
  cat "$tmp"
  rm -f "$tmp"
}

install_packages(){
  # Repair only verified public NetBird files BEFORE APT's first sandboxed read.
  python3 "$SCRIPT_DIR/netbird-router/common/apt_repository.py" repair \
    || die "NetBird-Paketquelle nicht sicher reparierbar; kein APT-Update."
  apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl gnupg jq nftables python3 iproute2 openssh-server sudo
  if ! command -v netbird >/dev/null 2>&1; then
    python3 "$SCRIPT_DIR/netbird-router/common/apt_repository.py" install \
      || die "NetBird-Paketquelle nicht verifiziert; kein APT-Update."
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y netbird
  fi
}

setup_access(){
  # Access on existing VMs belongs to the administrator. Add only an explicitly
  # supplied public key; never change passwords, accounts or sshd policy here.
  [[ -n "$SSH_PUBLIC_KEY" ]] || return 0
  [[ "$SSH_PUBLIC_KEY" == ssh-* && "$SSH_PUBLIC_KEY" != *$'\n'* ]] || die "Ungueltiger SSH-Public-Key."
  install -d -m 0700 /root/.ssh
  touch /root/.ssh/authorized_keys
  grep -qxF "$SSH_PUBLIC_KEY" /root/.ssh/authorized_keys || printf '%s\n' "$SSH_PUBLIC_KEY" >> /root/.ssh/authorized_keys
  chmod 0600 /root/.ssh/authorized_keys
}

detect_lan(){
  local detected
  detected=$(python3 "$RUNTIME" lan "$LAN_IF_OVERRIDE") || die "LAN nicht eindeutig; NB_LAN_INTERFACE setzen."
  local values
  values=$(python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["interface"]); print(d["network"]); print(d["address"])' <<<"$detected")
  local fields
  mapfile -t fields <<< "$values"
  LAN_IF=${fields[0]}
  LAN_NET=${fields[1]}
  LAN_IP=${fields[2]}
  if [[ -s "$STATE_FILE" ]]; then
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(not(d["customer"]==sys.argv[2] and d["lan"]==sys.argv[3]))' "$STATE_FILE" "$CUSTOMER" "$LAN_NET" >/dev/null 2>&1 \
      || die "Kunde/LAN weicht vom gespeicherten Bestand ab; Migration separat durchfuehren."
  fi
  log "LAN: $LAN_IF / $LAN_NET / $LAN_IP"
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

load_netbird_profile(){
  local info
  info=$(python3 "$SCRIPT_DIR/netbird-router/common/preflight.py" --profile-info \
    --management-url "$MGMT_URL" --expected-profile "$NETBIRD_PROFILE") \
    || die "Aktives NetBird-Profil nicht eindeutig/verbunden. Kein Reset."
  NETBIRD_PROFILE=$(python3 -c 'import json,sys;print(json.load(sys.stdin)["path"])' <<<"$info")
  NETBIRD_INTERFACE=$(python3 -c 'import json,sys;print(json.load(sys.stdin)["interface"])' <<<"$info")
}

connect_netbird(){
  local up_args
  if [[ "${PROFILE_EXISTED:-0}" == 1 ]]; then
    # Ownership, endpoint and firewall were already admitted before packages.
    # Never enable/start/reconnect an existing peer here.
    load_netbird_profile
    return 0
  fi
  systemctl enable --now netbird >/dev/null 2>&1 || die "NetBird-Dienst nicht gestartet."
  wait_netbird
  [[ -n "$SETUP_KEY" ]] || die "Setup-Key fuer neue Registrierung erforderlich."
  up_args=(up --setup-key "$SETUP_KEY" --management-url "$MGMT_URL")
  [[ -z "${HOSTNAME_OVERRIDE:-}" ]] || up_args+=(--hostname "$HOSTNAME_OVERRIDE")
  if [[ "$METRICS_ENABLED" == 1 ]]; then
    up_args+=(--enable-local-metrics --local-metrics-address "127.0.0.1:$METRICS_PORT")
  fi
  netbird "${up_args[@]}" >/dev/null 2>&1 || die "NetBird-Anmeldung fehlgeschlagen (Details unterdrueckt)."
  load_netbird_profile
}

find_peer(){
  local peers netbird_ip matches count tries
  netbird_ip=$(netbird status --ipv4 | awk 'NF {sub(/\/.*/, "", $1); print $1; exit}')
  [[ "$netbird_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Aktuelle NetBird-IPv4 konnte nicht bestimmt werden."

  # Alte Peers mit gleichem Hostnamen duerfen nie ausgewaehlt werden.
  for ((tries=1; tries<=30; tries++)); do
    peers=$(api GET "/peers")
    matches=$(jq -c --arg ip "$netbird_ip" '[(if type=="array" then . else [] end)[] | select(.ip==$ip)]' <<<"$peers")
    count=$(jq -r 'length' <<<"$matches")
    if (( count == 1 )); then
      PEER_ID=$(jq -r '.[0].id // empty' <<<"$matches")
      [[ -n "$PEER_ID" ]] || die "Peer mit $netbird_ip hat keine gueltige ID."
      PEER_IP="$netbird_ip"
      log "Aktueller Peer: $PEER_ID / $PEER_IP / Hostname $HOSTNAME_LOCAL"
      return 0
    fi
    if (( count > 1 )); then
      die "Mehrere Peers mit NetBird-IP $netbird_ip gefunden. Gruppenzuordnung abgebrochen."
    fi
    sleep 2
  done
  die "Neuer Peer $netbird_ip nicht per NetBird-API sichtbar. Kein Fallback auf Hostname."
}

valid_group(){
  # Null is an empty collection only with its explicit, numeric zero count.
  jq -sce --arg gid "$1" '
    def valid_id: type=="string" and length>0 and (test("[^A-Za-z0-9_-]")|not);
    def collection($field; $count):
      has($field) and has($count) and
      (.[$count] | type=="number" and .>=0 and floor==.) and
      (if .[$field]==null then .[$count]==0
       elif (.[$field]|type)=="array" then (.[$field]|length)==.[$count]
       else false end);
    (if length==1 then .[0] else null end) |
    select(type=="object" and (.id|valid_id) and .id==$gid and
      (.name|type=="string" and length>0) and
      collection("peers"; "peers_count") and collection("resources"; "resources_count")) |
    select(all((.peers // [])[]; (if type=="object" then .id else . end)|valid_id) and
      all((.resources // [])[]; type=="object" and (.id|valid_id) and (.type|valid_id))) |
    {id, name, peers: [(.peers // [])[] | if type=="object" then .id else . end],
     resources: [(.resources // [])[] | {id,type}]} |
    select((.peers|length)==(.peers|unique|length) and
      (.resources|length)==([.resources[].id]|unique|length)) |
    .peers |= sort | .resources |= sort_by(.id,.type)
  ' 2>/dev/null
}

ensure_group(){
  local name="$1" groups gid payload resp
  groups=$(api GET "/groups")
  jq -es --arg n "$name" '
    def valid_id: type=="string" and length>0 and (test("[^A-Za-z0-9_-]")|not);
    (if length==1 then .[0] else null end) |
    type=="array" and all(.[]; type=="object" and (.id|valid_id) and
      (.name|type=="string" and length>0)) and
    (length==([.[].id]|unique|length)) and ([.[] | select(.name==$n)] | length <= 1)
  ' <<<"$groups" >/dev/null 2>&1 \
    || die "Gruppenliste ungueltig oder Gruppenname nicht eindeutig. Keine Aenderung."
  gid=$(jq -r --arg n "$name" '.[] | select(.name==$n) | .id' <<<"$groups")
  if [[ -z "$gid" ]]; then
    payload=$(jq -nc --arg n "$name" '{name:$n,peers:[]}')
    resp=$(api POST "/groups" "$payload")
    gid=$(jq -ers --arg n "$name" '(if length==1 then .[0] else null end) |
      select(type=="object" and .name==$n) | .id |
      select(type=="string" and length>0 and (test("[^A-Za-z0-9_-]")|not))' <<<"$resp") \
      || die "Antwort auf Gruppenerstellung ungueltig."
  fi
  printf '%s' "$gid"
}

ensure_peer_in_group(){
  local gid="$1" g payload current
  [[ "$gid" =~ ^[A-Za-z0-9_-]+$ && "$PEER_ID" =~ ^[A-Za-z0-9_-]+$ ]] \
    || die "Gruppen-/Peer-ID ungueltig. Keine Aenderung."
  g=$(api GET "/groups/$gid")
  g=$(valid_group "$gid" <<<"$g") || die "Gruppenbestand unvollstaendig. Keine Aenderung."
  if jq -e --arg p "$PEER_ID" '.peers | index($p) != null' <<<"$g" >/dev/null; then
    return 0
  fi
  payload=$(jq -c --arg p "$PEER_ID" '{name, peers:(.peers+[$p]|sort), resources}' <<<"$g")

  # Do not overwrite a group that changed since the initial inventory.
  current=$(api GET "/groups/$gid")
  current=$(valid_group "$gid" <<<"$current") || die "Gruppenbestand vor Update unvollstaendig. Keine Aenderung."
  [[ "$current" == "$g" ]] || die "Gruppenbestand zwischenzeitlich geaendert. Keine Aenderung."
  api PUT "/groups/$gid" "$payload" >/dev/null
  current=$(api GET "/groups/$gid")
  current=$(valid_group "$gid" <<<"$current") || die "Gruppenbestand nach Update unvollstaendig."
  jq -e --argjson expected "$payload" '
    . as $actual | .name==$expected.name and
    all($expected.peers[]; . as $id | $actual.peers | index($id)!=null) and
    all($expected.resources[]; . as $resource | $actual.resources | index($resource)!=null)
  ' <<<"$current" >/dev/null || die "Gruppenzuordnung nicht vollstaendig erhalten/verifiziert."
}

setup_groups(){
  local marker_group

  marker_group="${CUSTOMER_MARKER_PREFIX}${CUSTOMER}"

  GLOBAL_GROUP_ID=$(ensure_group "$GLOBAL_GROUP")
  COMPANY_GROUP_ID=$(ensure_group "$CUSTOMER")
  CUSTOMER_MARKER_GROUP_ID=$(ensure_group "$marker_group")

  ensure_peer_in_group "$GLOBAL_GROUP_ID"
  ensure_peer_in_group "$COMPANY_GROUP_ID"
  ensure_peer_in_group "$CUSTOMER_MARKER_GROUP_ID"

  # Nur die bestehenden Zugriffsgruppen an Netzwerk-Ressourcen hängen.
  # Die Kunde:<Name>-Gruppe dient ausschließlich als eindeutiges Metadaten-
  # Merkmal für Monitoring/Inventar und erhält keine zusätzlichen Rechte.
  log "Gruppen: $GLOBAL_GROUP + $CUSTOMER + $marker_group (Monitoring-Marker)"
}

setup_zabbix_access(){
  [[ "$ZABBIX_ENABLED" == 1 ]] || return 0

  local peers matches count server_id saved_peer_id
  [[ "$ZABBIX_SERVER" =~ ^100\.[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || die "Zabbix-Server muss eine NetBird-IPv4 sein: $ZABBIX_SERVER"

  ZABBIX_PROXY_GROUP_ID=$(ensure_group "$ZABBIX_PROXY_GROUP")
  ensure_peer_in_group "$ZABBIX_PROXY_GROUP_ID"
  ZABBIX_SERVER_GROUP_ID=$(ensure_group "$ZABBIX_SERVER_GROUP")

  # Identify destination using its current overlay IP, never hostname.
  peers=$(api GET "/peers")
  matches=$(jq -c --arg ip "$ZABBIX_SERVER" \
    '[.[] | select(.ip==$ip)]' <<<"$peers")
  count=$(jq -r 'length' <<<"$matches")
  [[ "$count" == 1 ]] \
    || die "Zabbix-Server-Peer $ZABBIX_SERVER nicht eindeutig gefunden (Anzahl: $count)."
  server_id=$(jq -r '.[0].id // empty' <<<"$matches")
  [[ -n "$server_id" && "$server_id" != "$PEER_ID" ]] \
    || die "Zabbix-Ziel-Peer ungueltig."

  METRICS_POLICY_NAME="$ZABBIX_POLICY_NAME" MONITORING_GROUP_ID="$ZABBIX_PROXY_GROUP_ID" \
    METRICS_GROUP_ID="$ZABBIX_SERVER_GROUP_ID" METRICS_PORT=10051 METRICS_DETAIL_PORT=10051 ensure_metrics_policy

  saved_peer_id="$PEER_ID"
  PEER_ID="$server_id"
  ensure_peer_in_group "$ZABBIX_SERVER_GROUP_ID"
  PEER_ID="$saved_peer_id"

  NB_MANAGEMENT_URL="$MGMT_URL" NB_API_TOKEN="$API_TOKEN" NB_ZABBIX_SERVER="$ZABBIX_SERVER" \
    NB_ZABBIX_PROXY_GROUP="$ZABBIX_PROXY_GROUP" NB_ZABBIX_POLICY_NAME="$ZABBIX_POLICY_NAME" \
    python3 "$SCRIPT_DIR/netbird-router/common/zabbix_access.py" \
    || die "Zabbix-Zugriff nicht freigegeben: Gruppenmitgliedschaft, TCP/10051 und HTTPS/443 pruefen. Bestand bleibt erhalten; Auftrag erneut starten."
  log "Zabbix-Zugriff bestaetigt: Mitgliedschaft, Server TCP/10051 und tatsaechlicher HTTPS-API-Zugriffsweg"
  log "Gruppen: Router=$ZABBIX_PROXY_GROUP, Server=$ZABBIX_SERVER_GROUP"
}

ensure_metrics_policy(){
  local policies policy_id payload

  policies=$(api GET "/policies")
  jq -e --arg n "$METRICS_POLICY_NAME" 'type=="array" and ([.[] | select(.name==$n)] | length<=1)' <<<"$policies" >/dev/null 2>&1 \
    || die "Policy-Liste ungueltig oder Policy-Name nicht eindeutig. Keine Aenderung."
  policy_id=$(jq -r --arg n "$METRICS_POLICY_NAME" '.[] | select(.name==$n) | .id // empty' <<<"$policies")

  if jq -e --arg n "$METRICS_POLICY_NAME" 'any(.[]; .name==$n)' <<<"$policies" >/dev/null 2>&1; then
    # Verify required access only; retain extra rules, disabled rules and posture checks.
    [[ -n "$policy_id" ]] || die "Bestehende Metrics-Policy ohne ID. Keine Aenderung."
    jq -e --arg n "$METRICS_POLICY_NAME" --arg src "$MONITORING_GROUP_ID" \
      --arg dst "$METRICS_GROUP_ID" --arg p "$METRICS_PORT" --arg d "$METRICS_DETAIL_PORT" '
      .[] | select(.name==$n) | . as $policy |
      .enabled==true and all([$p,$d][]; . as $port |
        any($policy.rules[]?; .enabled==true and .action=="accept" and .protocol=="tcp" and
          any(.sources[]?; (if type=="object" then .id else . end)==$src) and
          any(.destinations[]?; (if type=="object" then .id else . end)==$dst) and
          any(.ports[]?; tostring==$port)))
    ' <<<"$policies" >/dev/null 2>&1 || die "Bestehende Metrics-Policy passt nicht. Keine automatische Ueberschreibung."
    log "Bestehende Metrics-Policy beibehalten: $METRICS_POLICY_NAME"
    return 0
  fi

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

  api POST "/policies" "$payload" >/dev/null
  log "Metrics-Policy erstellt: $METRICS_POLICY_NAME"
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
  # Enrollment keys are shared credentials; do not alter their auto_groups.

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
  install -m 0755 "$SCRIPT_DIR/netbird-prometheus/enable-client-metrics.sh" "$helper"
  chmod 0755 "$helper"

  NB_BUNDLE_DIR="$SCRIPT_DIR" NETBIRD_CLIENT_SKIP_RECONNECT=1 NETBIRD_CLIENT_METRICS_PORT="$METRICS_PORT" \
  NETBIRD_CLIENT_DETAIL_PORT="$METRICS_DETAIL_PORT" \
  "$helper"

  netbird_ip=$(netbird status --ipv4 2>/dev/null | awk 'NF {sub(/\/.*/, "", $1); print $1; exit}')
  [[ -n "$netbird_ip" ]] || die "NetBird-IP nach Metrics-Aktivierung nicht ermittelbar."
  METRICS_TARGET="$netbird_ip:$METRICS_PORT"

  log "NetBird Client Metrics aktiv: $METRICS_TARGET"
}

validate_monitoring_assignment(){
  local peers marker peer_groups missing=()

  marker="${CUSTOMER_MARKER_PREFIX}${CUSTOMER}"
  peers=$(api GET "/peers")

  peer_groups=$(jq -r --arg p "$PEER_ID" '
    (if type=="array" then . else [] end)[]
    | select(.id==$p)
    | (.groups // [])[]
    | if type=="object" then .name else empty end
  ' <<<"$peers")

  grep -Fxq "$GLOBAL_GROUP" <<<"$peer_groups" || missing+=("$GLOBAL_GROUP")
  grep -Fxq "$CUSTOMER" <<<"$peer_groups" || missing+=("$CUSTOMER")
  grep -Fxq "$marker" <<<"$peer_groups" || missing+=("$marker")

  if [[ "$METRICS_ENABLED" == 1 ]]; then
    grep -Fxq "$METRICS_GROUP" <<<"$peer_groups" || missing+=("$METRICS_GROUP")
  fi

  if (( ${#missing[@]} > 0 )); then
    die "Monitoring-/Kundenzuordnung unvollständig. Fehlende Gruppen: ${missing[*]}"
  fi

  log "Zuordnung validiert: Kunde=$CUSTOMER / Marker=$marker / Metrics=$([[ "$METRICS_ENABLED" == 1 ]] && echo "$METRICS_GROUP" || echo deaktiviert)"
}

allocate_mapping(){
  local result
  result=$(NB_MANAGEMENT_URL="$MGMT_URL" NB_API_TOKEN="$API_TOKEN" \
    python3 "$SCRIPT_DIR/netbird-router/common/network.py" "$CUSTOMER" "$ROLE" "$LAN_NET" "$PEER_ID" "$COMPANY_GROUP_ID") \
    || die "NetBird-Netzwerkabgleich fehlgeschlagen; vorhandene Zuordnungen bleiben erhalten."
  VIRTUAL_NET=$(jq -r .virtual <<<"$result")
  NETWORK_ID=$(jq -r .network <<<"$result")
  RESOURCE_ID=$(jq -r .resource <<<"$result")
  ROUTER_ID=$(jq -r .router <<<"$result")
}

ensure_network(){
  [[ -n "$NETWORK_ID" && -n "$RESOURCE_ID" && -n "$ROUTER_ID" ]] || die "Netzwerkabgleich unvollstaendig."
}

write_nft(){
  local candidate transaction nbif
  load_netbird_profile
  nbif="$NETBIRD_INTERFACE"
  install -d -m 0755 /etc/nftables.d
  candidate=$(mktemp /etc/nftables.d/.openmain.XXXXXX)
  transaction=$(mktemp)
  python3 "$RUNTIME" nft "$LAN_IF" "$nbif" "$VIRTUAL_NET" "$LAN_NET" > "$candidate" || die "BINAT-Konfiguration ungueltig."
  # Atomic transaction restricted to our table. Never reload /etc/nftables.conf.
  if nft list table ip openmain_binat >/dev/null 2>&1; then
    printf 'delete table ip openmain_binat\n' > "$transaction"
  fi
  cat "$candidate" >> "$transaction"
  if ! nft -c -f "$transaction" >/dev/null 2>&1 || ! nft -f "$transaction" >/dev/null 2>&1; then
    rm -f "$candidate" "$transaction"
    die "BINAT-Pruefung/Aktivierung fehlgeschlagen; keine fremden Firewallregeln geaendert."
  fi
  [[ ! -f "$NFT_FILE" ]] || cp -p "$NFT_FILE" "$NFT_FILE.previous"
  mv "$candidate" "$NFT_FILE"
  rm -f "$transaction"
  install -m 0755 "$SCRIPT_DIR/netbird-router/common/apply-binat.sh" /usr/local/sbin/openmain-apply-binat
  cat > /etc/systemd/system/openmain-binat.service <<'UNIT'
[Unit]
Description=OpenMain customer BINAT (own table only)
After=network-online.target netbird.service nftables.service
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/openmain-apply-binat
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
UNIT
  # NetBird evaluates the *translated* destination in FORWARD. Reconcile
  # its policy-generated rule to the conntrack original destination while
  # retaining the dynamic NetBird source set. Never add a parallel allow.
  install -d -m 0755 /usr/local/lib/openmain-router
  install -m 0644 "$SCRIPT_DIR/netbird-router/common/binat_acl.py" /usr/local/lib/openmain-router/binat_acl.py
  local acl_temp
  acl_temp=$(mktemp /etc/nftables.d/.openmain-acl.XXXXXX)
  python3 - "$acl_temp" "$nbif" "$LAN_IF" "$VIRTUAL_NET" "$LAN_NET" <<'PYACL'
import json,sys
with open(sys.argv[1], 'w') as stream:
    json.dump(dict(zip(('overlay','lan','virtual','real'),sys.argv[2:])),stream)
    stream.write(chr(10))
PYACL
  chmod 0600 "$acl_temp"
  mv -f -- "$acl_temp" /etc/nftables.d/openmain-binat-acl.json
  cat > /etc/systemd/system/openmain-binat-acl-sync.service <<'UNIT'
[Unit]
Description=Reconcile NetBird route ACL with managed BINAT (fail closed)
After=netbird.service openmain-binat.service
Requires=netbird.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/lib/openmain-router/binat_acl.py
UMask=0077
UNIT
  cat > /etc/systemd/system/openmain-binat-acl-sync.timer <<'UNIT'
[Unit]
Description=Reconcile NetBird route ACL after policy refresh
[Timer]
OnBootSec=20s
OnUnitInactiveSec=30s
Unit=openmain-binat-acl-sync.service
[Install]
WantedBy=timers.target
UNIT
  systemctl daemon-reload
  systemctl enable --now openmain-binat.service >/dev/null
  systemctl enable --now openmain-binat-acl-sync.timer >/dev/null
  local ok=0
  for _ in {1..12}; do
    if systemctl start openmain-binat-acl-sync.service >/dev/null 2>&1; then
      ok=1
      break
    fi
    sleep 5
  done
  [[ "$ok" == 1 ]] || die "NetBird-Routing-Freigabe fuer BINAT fehlt oder ist inkompatibel; kein ACL-Bypass. Bestand bleibt erhalten."
}

setup_zabbix(){
  [[ "$ZABBIX_ENABLED" == 1 ]] || { log "Zabbix Proxy deaktiviert."; return 0; }

  local helper=""
  for candidate in "$SCRIPT_DIR/zabbix-proxy-install.sh" /usr/local/sbin/zabbix-proxy-install /root/zabbix-proxy-install.sh /usr/local/sbin/zabbix-proxy-install.sh; do
    if [[ -f "$candidate" ]]; then
      helper="$candidate"
      break
    fi
  done

  [[ -n "$helper" ]] || die "Zabbix-Helferskript fehlt. Erwartet: /usr/local/sbin/zabbix-proxy-install"

  if [[ ! -e "${STATE_FILE%/*}/zabbix-intent.json" ]]; then
    jq -nc --arg customer "$CUSTOMER" --arg proxy "proxy-${COMPANY_GROUP_ID}-${ROLE}" \
      --arg host "router-${COMPANY_GROUP_ID}-${ROLE}" '{customer:$customer,proxy:$proxy,host:$host}' > "${STATE_FILE%/*}/zabbix-intent.tmp"
    mv "${STATE_FILE%/*}/zabbix-intent.tmp" "${STATE_FILE%/*}/zabbix-intent.json"
  fi
  NB_MANAGEMENT_URL="$MGMT_URL" NB_API_TOKEN="$API_TOKEN" NB_ZABBIX_SERVER="$ZABBIX_SERVER" NB_ZABBIX_PROXY_GROUP="$ZABBIX_PROXY_GROUP" NB_ZABBIX_POLICY_NAME="$ZABBIX_POLICY_NAME" \
  NB_LAN_INTERFACE="$LAN_IF" NB_BUNDLE_DIR="$SCRIPT_DIR" NB_ZABBIX_REGISTER_HOST=1 NB_ZABBIX_PROXY_NAME="proxy-${COMPANY_GROUP_ID}-${ROLE}" NB_ZABBIX_AGENT_HOST="router-${COMPANY_GROUP_ID}-${ROLE}" bash "$helper" \
    --customer "$CUSTOMER" --role "$ROLE" \
    --server "$ZABBIX_SERVER" --hostname "$HOSTNAME_LOCAL"
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
    }' > "$STATE_FILE.tmp"
  mv "$STATE_FILE.tmp" "$STATE_FILE"
  chmod 600 "$STATE_FILE"
}

checkpoint(){
  python3 "$RUNTIME" checkpoint -- "${STATE_FILE%/*}/progress.json" "$1" "$CUSTOMER" "$ROLE"
}

installation_exit(){
  local status=$1
  # Capture failures including explicit die/exit, without a recursive trap.
  trap - EXIT ERR
  if (( status != 0 )); then
    checkpoint failed || warn "Fehlerstatus konnte nicht gespeichert werden."
    warn "Installation unterbrochen. Mit denselben Angaben erneut starten."
  fi
  exit "$status"
}

preflight(){
  local info
  info=$(python3 "$SCRIPT_DIR/netbird-router/common/preflight.py" \
    --customer "$CUSTOMER" --role "$ROLE" --management-url "$MGMT_URL" \
    --zabbix-server "$ZABBIX_SERVER" --zabbix-enabled "$ZABBIX_ENABLED" \
    --lan-interface "$LAN_IF_OVERRIDE" --report-profile) || die "Bestandspruefung abgelehnt; keine Aenderungen."
  NETBIRD_PROFILE=$(python3 -c 'import json,sys;print(json.load(sys.stdin)["path"])' <<<"$info")
  NETBIRD_INTERFACE=$(python3 -c 'import json,sys;print(json.load(sys.stdin)["interface"])' <<<"$info")
  PROFILE_EXISTED=0
  [[ -z "$NETBIRD_PROFILE" ]] || PROFILE_EXISTED=1
  detect_lan
}

main(){
  # No state directory, lock, package, service, API write or connection before this.
  preflight
  install -d -m 0700 "${STATE_FILE%/*}"
  exec 9>"${STATE_FILE%/*}/install.lock"
  flock -n 9 || die "Ein Installer laeuft bereits."
  preflight
  [[ "$PROFILE_EXISTED" == 1 || -n "$SETUP_KEY" ]] || { read -r -s -p "NetBird Setup Key: " SETUP_KEY; echo; }
  trap 'installation_exit "$?"' EXIT
  checkpoint preflight
  install_packages
  setup_access
  checkpoint netbird
  connect_netbird
  find_peer
  setup_groups
  setup_zabbix_access
  setup_metrics
  validate_monitoring_assignment
  checkpoint network
  allocate_mapping
  ensure_network
  # Persist the strictly validated NetBird allocation before making NAT
  # changes: ACL reconciliation may fail while tables/files already exist.
  # This guarded ownership state makes retries read-only verifiable.
  save_state
  write_nft
  enable_forwarding
  checkpoint zabbix
  setup_zabbix
  NB_LAN_INTERFACE="$LAN_IF" bash "$SCRIPT_DIR/netbird-router/pve/install-dhcp-recovery.sh"
  systemctl is-active --quiet netbird || die "Abschlusspruefung: NetBird nicht aktiv."
  nft list table ip openmain_binat >/dev/null 2>&1 || die "Abschlusspruefung: BINAT fehlt."
  if [[ "$ZABBIX_ENABLED" == 1 ]]; then
    systemctl is-active --quiet zabbix-proxy zabbix-agent2 || die "Abschlusspruefung: Zabbix-Dienst nicht aktiv."
  fi
  checkpoint complete
  log "Pruefbericht: Plattform kompatibel; LAN $LAN_IF/$LAN_IP; NetBird verbunden; Gruppen/Policies verifiziert."
  log "BINAT $VIRTUAL_NET -> $LAN_NET; Rolle $ROLE / Metrik $ROUTER_METRIC; Forwarding aktiv."
  log "Zabbix=$ZABBIX_ENABLED: Konfiguration/API/Dienste geprueft. Status: ${STATE_FILE%/*}/progress.json"
  warn "Remote-Messwerte, realer Routerausfall und Hardware-Boot sind separat zu pruefen."
}

main "$@"
