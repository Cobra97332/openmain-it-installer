#!/usr/bin/env bash
set -Eeuo pipefail
DONE=/var/lib/openmain-router-firstboot.done
[[ -e "$DONE" ]] && exit 0
PUBLIC_BASE="https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main"

echo "=== OpenMain-IT NetBird Kundenrouter ==="
read -r -p "Firmenname/Kunde: " CUSTOMER
while [[ -z "$CUSTOMER" ]]; do read -r -p "Firmenname/Kunde: " CUSTOMER; done
echo "1) Primary"; echo "2) Backup"
read -r -p "Auswahl [1/2]: " R
case "$R" in 1) ROLE=primary;;2) ROLE=backup;;*) exit 1;;esac
read -r -p "Hostname [$(hostname -s)]: " HOSTNAME_LOCAL
HOSTNAME_LOCAL="${HOSTNAME_LOCAL:-$(hostname -s)}"
hostnamectl set-hostname "$HOSTNAME_LOCAL"
read -r -s -p "NetBird Setup Key: " NB_SETUP_KEY; echo
read -r -s -p "NetBird API Token: " NB_API_TOKEN; echo
read -r -s -p "Zabbix API Token (optional): " NB_ZABBIX_API_TOKEN; echo

apt-get update
apt-get install -y curl ca-certificates
for f in router-install.sh zabbix-proxy-install.sh zabbix-api-register.sh; do
  curl -fsSL "$PUBLIC_BASE/$f" -o "/tmp/$f"
  chmod 0755 "/tmp/$f"
done
install -m 0755 /tmp/zabbix-proxy-install.sh /usr/local/sbin/zabbix-proxy-install
install -m 0755 /tmp/zabbix-api-register.sh /usr/local/sbin/zabbix-api-register

export NB_MANAGEMENT_URL="https://netbird.openmain-it.de"
export NB_ZABBIX_SERVER="100.107.91.6"
export NB_ZABBIX_API_URL="https://zabbix.openmain-it.de/api_jsonrpc.php"
export NB_ZABBIX_API_TOKEN
/tmp/router-install.sh --customer "$CUSTOMER" --role "$ROLE" --hostname "$HOSTNAME_LOCAL" --management-url "$NB_MANAGEMENT_URL" --setup-key "$NB_SETUP_KEY" --api-token "$NB_API_TOKEN" --zabbix-server "$NB_ZABBIX_SERVER"

touch "$DONE"
chmod 600 "$DONE"
echo "Router-Einrichtung abgeschlossen."
echo "IPv4 Forwarding: $(cat /proc/sys/net/ipv4/ip_forward)"
