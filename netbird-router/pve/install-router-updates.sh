#!/usr/bin/env bash
set -Eeuo pipefail

ROLE=""
GROUP_IPS=""
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
while [[ $# -gt 0 ]]; do
  case "$1" in
    --role) ROLE="$2"; shift 2 ;;
    --agent-group-ips) GROUP_IPS="$2"; shift 2 ;;
    *) echo "Unbekannte Option: $1" >&2; exit 1 ;;
  esac
done
[[ $EUID -eq 0 ]] || { echo 'Root erforderlich' >&2; exit 1; }
[[ "$ROLE" == primary || "$ROLE" == backup ]] || { echo 'Rolle primary|backup erforderlich' >&2; exit 1; }
[[ -s /var/lib/netbird-kundenrouter/state.json ]] || { echo 'Routerzustand fehlt' >&2; exit 1; }
[[ $(jq -r '.role' /var/lib/netbird-kundenrouter/state.json) == "$ROLE" ]] || { echo 'Rolle passt nicht zum Routerzustand' >&2; exit 1; }

topology=single
partner_ip=""
if [[ -z "$GROUP_IPS" ]]; then
  [[ "$ROLE" != backup ]] || { echo 'Backup benötigt die beiden bekannten Router-LAN-IPs' >&2; exit 1; }
  if [[ -f /etc/zabbix/zabbix_agent2.conf ]] && grep -Eq '^ServerActive=[^#]*;' /etc/zabbix/zabbix_agent2.conf; then
    echo 'Zabbix-Agent-Konfiguration zeigt ein Routerpaar; beide LAN-IPs sind erforderlich' >&2
    exit 1
  fi
fi
if [[ -n "$GROUP_IPS" ]]; then
  IFS=, read -r -a addresses <<<"$GROUP_IPS"
  [[ ${#addresses[@]} -eq 2 ]] || { echo 'Genau zwei Router-LAN-IPs erforderlich' >&2; exit 1; }
  local_ip=$(ip -4 route get "${addresses[0]}" | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -n1)
  [[ -n "$local_ip" ]] || { echo 'Eigene LAN-IP nicht bestimmbar' >&2; exit 1; }
  for addr in "${addresses[@]}"; do
    python3 -c 'import ipaddress,sys; ipaddress.IPv4Address(sys.argv[1])' "$addr" || exit 1
  done
  [[ "${addresses[0]}" != "${addresses[1]}" ]] || { echo 'Doppelte Router-IP' >&2; exit 1; }
  if [[ "${addresses[0]}" == "$local_ip" ]]; then partner_ip=${addresses[1]};
  elif [[ "${addresses[1]}" == "$local_ip" ]]; then partner_ip=${addresses[0]};
  else echo 'Eigene LAN-IP fehlt in Routerpaar' >&2; exit 1; fi
  topology=pair
  command -v zabbix_get >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y zabbix-get
fi

timezone=$(timedatectl show -p Timezone --value 2>/dev/null || true)
[[ "$timezone" == Europe/Berlin ]] || echo "[WARNUNG] Systemzeitzone ist ${timezone:-unbekannt}; Timer verwendet ausdrücklich Europe/Berlin." >&2

install -m 0755 "$SCRIPT_DIR/router-update.sh" /usr/local/sbin/openmain-router-update
install -m 0755 "$SCRIPT_DIR/router-update-health.sh" /usr/local/sbin/openmain-router-update-health
cat >/etc/sudoers.d/openmain-router-update-health <<'EOF'
zabbix ALL=(root) NOPASSWD: /usr/local/sbin/openmain-router-update-health role, /usr/local/sbin/openmain-router-update-health ready
EOF
chmod 0440 /etc/sudoers.d/openmain-router-update-health
visudo -cf /etc/sudoers.d/openmain-router-update-health
install -d -m 0755 /etc/zabbix/zabbix_agent2.d
cat >/etc/zabbix/zabbix_agent2.d/openmain-router-update.conf <<'EOF'
UserParameter=openmain.router.role,sudo -n /usr/local/sbin/openmain-router-update-health role
UserParameter=openmain.router.ready,sudo -n /usr/local/sbin/openmain-router-update-health ready
EOF
chmod 0644 /etc/zabbix/zabbix_agent2.d/openmain-router-update.conf
tmp=$(mktemp)
printf 'ROLE=%q\nTOPOLOGY=%q\nPARTNER_LAN_IP=%q\n' "$ROLE" "$topology" "$partner_ip" >"$tmp"
install -m 0644 "$tmp" /etc/openmain-router-update.conf
rm -f "$tmp"

hour=04
[[ "$ROLE" == backup ]] && hour=05
cat >/etc/systemd/system/openmain-router-update.service <<'EOF'
[Unit]
Description=OpenMain router weekly package update
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/openmain-router-update
StandardOutput=journal
StandardError=journal
EOF
cat >/etc/systemd/system/openmain-router-update.timer <<EOF
[Unit]
Description=OpenMain router Sunday update ($ROLE)

[Timer]
OnCalendar=Sun *-*-* $hour:00:00 Europe/Berlin
Persistent=true
RandomizedDelaySec=0
Unit=openmain-router-update.service

[Install]
WantedBy=timers.target
EOF
systemd-analyze verify /etc/systemd/system/openmain-router-update.service /etc/systemd/system/openmain-router-update.timer
systemctl daemon-reload
systemctl enable --now openmain-router-update.timer
if systemctl is-active --quiet zabbix-agent2.service; then
  systemctl restart zabbix-agent2.service
elif [[ "$topology" == pair ]]; then
  echo '[WARNUNG] Zabbix Agent 2 ist nicht aktiv; Partnerpruefung wird fehlschlagen und Neustarts aussetzen.' >&2
fi
echo "Update-Timer installiert: Sonntag $hour:00 Europe/Berlin ($topology)."
