#!/usr/bin/env bash
set -Eeuo pipefail

# Run inside a Debian 13 Proxmox LXC. Installs a non-disruptive DHCP retry timer.
[[ "$EUID" -eq 0 ]] || { echo "Root-Rechte erforderlich." >&2; exit 1; }
command -v systemctl >/dev/null 2>&1 || { echo "systemd fehlt." >&2; exit 1; }
command -v ip >/dev/null 2>&1 || { echo "iproute2 fehlt." >&2; exit 1; }
command -v flock >/dev/null 2>&1 || { echo "flock fehlt." >&2; exit 1; }

# Only the LXC eth0 configuration with DHCP is supported.
if ! grep -Eq '^[[:space:]]*iface[[:space:]]+eth0[[:space:]]+inet[[:space:]]+dhcp([[:space:]]|$)' /etc/network/interfaces; then
  echo "eth0 ist nicht als DHCP-Schnittstelle konfiguriert; kein DHCP-Retry installiert." >&2
  exit 1
fi

if ! command -v dhclient >/dev/null 2>&1; then
  apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y isc-dhcp-client
fi

install -d -m 0755 /usr/local/sbin /etc/systemd/system

cat > /usr/local/sbin/openmain-dhcp-recovery <<'SCRIPT'
#!/usr/bin/env bash
set -Eeuo pipefail

IFACE="${1:-eth0}"
[[ "$IFACE" == eth0 ]] || exit 1
[[ -d "/sys/class/net/$IFACE" ]] || exit 0

# Do nothing when a global IPv4 address is already assigned.
has_ipv4() {
  ip -4 -o addr show dev "$IFACE" scope global | grep -q ' inet '
}

# Disable recovery automatically if eth0 is switched to a static address.
if ! grep -Eq '^[[:space:]]*iface[[:space:]]+eth0[[:space:]]+inet[[:space:]]+dhcp([[:space:]]|$)' /etc/network/interfaces; then
  exit 0
fi
has_ipv4 && exit 0

exec 9>/run/openmain-dhcp-recovery.lock
flock -n 9 || exit 0
has_ipv4 && exit 0

PIDFILE="/run/dhclient.${IFACE}.pid"
LEASEFILE="/var/lib/dhcp/dhclient.${IFACE}.leases"

# Never race against a still-running dhclient started by ifupdown2.
if [[ -s "$PIDFILE" ]]; then
  read -r CLIENT_PID < "$PIDFILE" || true
  if [[ "${CLIENT_PID:-}" =~ ^[0-9]+$ ]] && \
     [[ -r "/proc/$CLIENT_PID/comm" ]] && \
     grep -qx 'dhclient' "/proc/$CLIENT_PID/comm"; then
    exit 0
  fi
fi

logger -t openmain-dhcp-recovery "eth0 without IPv4: retrying DHCP"
if timeout --signal=TERM --kill-after=5s 65s \
  dhclient -4 -1 -v -pf "$PIDFILE" -lf "$LEASEFILE" "$IFACE"; then
  if has_ipv4; then
    logger -t openmain-dhcp-recovery "DHCP recovered IPv4 on eth0"
  else
    logger -t openmain-dhcp-recovery "dhclient returned without an IPv4; retry scheduled"
  fi
else
  RC=$?
  logger -t openmain-dhcp-recovery "DHCP retry failed (exit $RC); retry scheduled"
fi
# A successful service result keeps the periodic timer enabled for the next try.
exit 0
SCRIPT
chmod 0755 /usr/local/sbin/openmain-dhcp-recovery

cat > /etc/systemd/system/openmain-dhcp-recovery.service <<'UNIT'
[Unit]
Description=OpenMain NetBird customer router DHCP recovery
After=networking.service
ConditionPathExists=/sys/class/net/eth0

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/openmain-dhcp-recovery eth0
TimeoutStartSec=85s
UNIT

cat > /etc/systemd/system/openmain-dhcp-recovery.timer <<'TIMER'
[Unit]
Description=Check NetBird customer router DHCP after boot and periodically

[Timer]
OnBootSec=90s
OnUnitInactiveSec=120s
AccuracySec=10s
Unit=openmain-dhcp-recovery.service

[Install]
WantedBy=timers.target
TIMER

systemctl daemon-reload
systemctl enable --now openmain-dhcp-recovery.timer
systemctl is-enabled --quiet openmain-dhcp-recovery.timer
echo "DHCP-Recovery aktiviert (erste Pruefung 90s nach Boot, danach 120s nach letzter Pruefung)."
