#!/usr/bin/env bash
set -Eeuo pipefail
OM_CUSTOMER='' OM_P1='' OM_P2='' OM_HOST=''
while (($#)); do
  (($# >= 2)) || { echo "Parameterwert fehlt: $1" >&2; exit 1; }
  case "$1" in
    --customer) OM_CUSTOMER="$2";;
    --proxy1) OM_P1="$2";;
    --proxy2) OM_P2="$2";;
    --hostname) OM_HOST="$2";;
    *) echo "Unbekannter Parameter: $1" >&2; exit 1;;
  esac
  shift 2
done
[[ "$EUID" -eq 0 ]] || { echo "Root erforderlich" >&2; exit 1; }
[[ -n "$OM_CUSTOMER" && -n "$OM_P1" ]] || { echo "--customer und --proxy1 erforderlich" >&2; exit 1; }
if ! command -v python3 >/dev/null 2>&1; then
  apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y python3 || exit 1
fi
if ! command -v zabbix_agent2 >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y zabbix-agent2 python3 || { echo "APT-Paket zabbix-agent2 nicht verfuegbar" >&2; exit 1; }
fi
set -- "$OM_CUSTOMER" "$OM_P1" "$OM_P2" "$OM_HOST"
# Weiter mit bestehender Konfigurationsroutine.
CUSTOMER="${1:-}"; PROXY1="${2:-}"; PROXY2="${3:-}"; HOST_OVERRIDE="${4:-}"
die(){ echo "FEHLER: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "Root erforderlich"
[[ "$CUSTOMER" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || die "Kunde: Buchstaben, Zahlen, . _ - (max. 64)"
[[ -n "$PROXY1" && "$PROXY1" != "$PROXY2" ]] || die "Proxy-Adresse fehlt oder doppelt"
command -v python3 >/dev/null && command -v zabbix_agent2 >/dev/null || die "Python3 und Zabbix Agent 2 erforderlich"
python3 - "$PROXY1" "$PROXY2" <<'PY'
import ipaddress,sys
nets=[ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')]
for addr in sys.argv[1:]:
    if addr:
        try: ip=ipaddress.IPv4Address(addr)
        except ValueError: raise SystemExit("Ungueltige IPv4: "+addr)
        if not any(ip in net for net in nets): raise SystemExit("Keine RFC1918 LAN-IPv4: "+addr)
PY
CFG=/etc/zabbix/zabbix_agent2.conf
[[ -f "$CFG" ]] || die "Agent-Konfiguration fehlt: $CFG"
ACTIVE="$PROXY1:10051"; [[ -z "$PROXY2" ]] || ACTIVE+=";$PROXY2:10051"
HOST="${HOST_OVERRIDE:-$(hostname -s)}"
[[ "$HOST" =~ ^[A-Za-z0-9._-]{1,128}$ ]] || die "Ungueltiger Zabbix-Hostname"
OS=Linux; [[ -f /etc/pve/.version ]] && OS=Proxmox
echo "Kunde: $CUSTOMER | Host: $HOST | Agent-Ziele: $ACTIVE"
read -r -p "Sind dies lokale Zabbix-Proxys (nicht der zentrale Server)? [ja/NEIN] " ANSWER
[[ "$ANSWER" == ja ]] || die "Abgebrochen"
BACKUP="$CFG.bak.$(date +%Y%m%d-%H%M%S)"
cp -a -- "$CFG" "$BACKUP"
TMP=$(mktemp /etc/zabbix/zabbix_agent2.conf.XXXXXX)
trap 'rm -f "$TMP"' EXIT
python3 - "$CFG" "$TMP" "$ACTIVE" "$HOST" "$CUSTOMER" "$OS" "$PROXY1" "$PROXY2" <<'PY'
import re,sys
source,dest,active,host,customer,os,proxy1,proxy2=sys.argv[1:]
with open(source,encoding="utf-8") as f: lines=f.readlines()
# Never silently override existing secure transport settings.
if any(re.match(r'^\s*(TLSConnect|TLSAccept|TLSPSKFile|TLSCertFile|TLSCAFile)\s*=',s) for s in lines):
    raise SystemExit("Bestehende TLS-Konfiguration: manuelle Pruefung notwendig")
values={"Server":",".join(["127.0.0.1",proxy1]+([proxy2] if proxy2 else [])),"ServerActive":active,"Hostname":host,
        "HostMetadata":f"OMLAN;Customer={customer};OS={os}","ListenPort":"10050"}
seen=set(); out=[]
for line in lines:
    m=re.match(r'^\s*([A-Za-z][A-Za-z0-9]*)\s*=',line)
    key=m.group(1) if m else None
    if key in values:
        if key not in seen: out.append(f"{key}={values[key]}\n"); seen.add(key)
    else: out.append(line)
for key,val in values.items():
    if key not in seen: out.append(f"{key}={val}\n")
with open(dest,"w",encoding="utf-8") as f: f.writelines(out)
PY
chmod --reference="$CFG" "$TMP"
chown --reference="$CFG" "$TMP"
zabbix_agent2 -T -c "$TMP" >/dev/null || die "Konfiguration ungueltig"
cp -a -- "$TMP" "$CFG"
if ! systemctl restart zabbix-agent2 || ! systemctl is-active --quiet zabbix-agent2; then
    cp -a -- "$BACKUP" "$CFG"
    systemctl restart zabbix-agent2 || true
    die "Agent-Neustart fehlgeschlagen; Backup wiederhergestellt: $BACKUP"
fi
echo "OK - Backup: $BACKUP"
grep -E '^(Server|ServerActive|Hostname|HostMetadata)=' "$CFG"
echo "Zabbix-Autoregistrierung muss serverseitig konfiguriert sein."
