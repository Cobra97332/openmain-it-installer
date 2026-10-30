#!/usr/bin/env bash
set -Eeuo pipefail
die(){ echo "FEHLER: $*" >&2; exit 1; }
CUSTOMER='' P1='' P2='' HOST='' REPLACE=0
while (($#)); do
 case "$1" in
 --customer) CUSTOMER="${2:?}"; shift 2;;
 --proxy1) P1="${2:?}"; shift 2;;
 --proxy2) P2="${2:?}"; shift 2;;
 --hostname) HOST="${2:?}"; shift 2;;
 --replace) REPLACE=1; shift;;
 *) die "Option unbekannt: $1";;
 esac
done
((EUID==0)) || die "Als root starten"
[[ "$CUSTOMER" =~ ^[a-zA-Z0-9._-]{1,64}$ && -n "$P1" ]] || die "Benutzung: --customer Bauer --proxy1 IP [--proxy2 IP] [--replace]"
[[ -z "$P2" || "$P1" != "$P2" ]] || die "Doppelte Proxy-Adresse"
. /etc/os-release
[[ "$ID" == debian || "$ID" == ubuntu || " ${ID_LIKE:-} " == *" debian "* ]] || die "Nur Debian/Ubuntu/Proxmox"
command -v python3 >/dev/null || die "python3 fehlt"
python3 - "$P1" "$P2" <<'PY' || die "Nur RFC1918-IPv4 erlaubt"
import sys,ipaddress
for s in sys.argv[1:]:
 if s and not ipaddress.IPv4Address(s).is_private: raise ValueError(s)
 if s and not (ipaddress.IPv4Address(s) in ipaddress.IPv4Network("10.0.0.0/8") or ipaddress.IPv4Address(s) in ipaddress.IPv4Network("172.16.0.0/12") or ipaddress.IPv4Address(s) in ipaddress.IPv4Network("192.168.0.0/16")): raise ValueError(s)
PY
HOST="${HOST:-$(hostname -s)}"
[[ "$HOST" =~ ^[a-zA-Z0-9._-]{1,128}$ ]] || die "Hostname ungueltig"
ACTIVE="$P1:10051"; [[ -z "$P2" ]] || ACTIVE+=";$P2:10051"
CFG=/etc/zabbix/zabbix_agent2.conf
[[ ! -e "$CFG" || "$REPLACE" == 1 ]] || die "Bestehende Konfiguration: nur mit --replace (nach Pruefung)"
for ip in "$P1" "$P2"; do
 [[ -n "$ip" ]] || continue
 if timeout 3 bash -c 'echo > /dev/tcp/$1/10051' _ "$ip" 2>/dev/null; then REACH=1; fi
done
[[ "${REACH:-0}" == 1 ]] || die "Keine Proxy-Verbindung auf TCP/10051"
if [[ ! -x /usr/sbin/zabbix_agent2 && ! -x /usr/bin/zabbix_agent2 ]]; then
 apt-get update
 DEBIAN_FRONTEND=noninteractive apt-get install -y zabbix-agent2 || die "Zabbix-Paket nicht verfuegbar: offizielles Zabbix-7.4-Repository einrichten"
fi
mkdir -p /etc/zabbix
if [[ -d /etc/zabbix/zabbix_agent2.d ]] && find /etc/zabbix/zabbix_agent2.d -name '*.conf' -type f | grep -q .; then
 die "Zusatzkonfiguration vorhanden: zuerst manuell pruefen"
fi
if [[ -f "$CFG" ]] && grep -Eiq '^[[:space:]]*(TLSConnect|TLSAccept|TLSPSKFile|TLSCertFile|Include)[[:space:]]*=' "$CFG"; then
 die "TLS- oder Include-Konfiguration vorhanden: keine automatische Ueberschreibung"
fi
BACKUP=''
if [[ -f "$CFG" ]]; then BACKUP="$CFG.bak.$(date +%Y%m%d%H%M%S)"; cp -a "$CFG" "$BACKUP"; fi
TMP=$(mktemp /etc/zabbix/agent2.XXXXXX)
trap 'rm -f "$TMP"' EXIT
OS=Linux; [[ ! -e /etc/pve/.version ]] || OS=Proxmox
cat > "$TMP" <<EOF
LogType=file
LogFile=/var/log/zabbix/zabbix_agent2.log
PidFile=/run/zabbix/zabbix_agent2.pid
Server=127.0.0.1,$P1${P2:+,$P2}
ListenPort=10050
ServerActive=$ACTIVE
Hostname=$HOST
HostMetadata=OMLAN;Customer=$CUSTOMER;OS=$OS
RefreshActiveChecks=120
Timeout=10
EOF
chmod 644 "$TMP"
zabbix_agent2 -T -c "$TMP" >/dev/null || die "Konfiguration ungueltig"
mv "$TMP" "$CFG"
if ! systemctl enable --now zabbix-agent2 || ! systemctl restart zabbix-agent2 || ! systemctl is-active --quiet zabbix-agent2; then
 [[ -z "$BACKUP" ]] || { cp -a "$BACKUP" "$CFG"; systemctl restart zabbix-agent2 || true; }
 die "Agent startet nicht"
fi
echo "OK: $HOST; $CUSTOMER; $ACTIVE"
echo "Serverseitige Zabbix-Autoregistrierungsaktion muss eingerichtet sein."
