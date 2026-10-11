#!/usr/bin/env bash
set -Eeuo pipefail
# Detection/recovery stays with the owning network manager; never release a lease.
[[ $EUID -eq 0 ]] || { echo 'Root-Rechte erforderlich.' >&2; exit 1; }
SOURCE="$(dirname -- "${BASH_SOURCE[0]}")/../common/dhcp-recovery.sh"
if ! bash "$SOURCE" --detect; then
  # A static or unknown configuration is not an installation error.
  if systemctl is-enabled --quiet openmain-dhcp-recovery.timer 2>/dev/null; then
    systemctl disable --now openmain-dhcp-recovery.timer >/dev/null
  fi
  echo 'Kein verwaltetes DHCP-Interface: Recovery nicht aktiviert.'
  exit 0
fi
install -d -m 0755 /usr/local/lib/openmain-router
install -m 0644 "$(dirname -- "${BASH_SOURCE[0]}")/../common/runtime.py" /usr/local/lib/openmain-router/runtime.py
install -m 0755 "$(dirname -- "${BASH_SOURCE[0]}")/../common/dhcp-recovery.sh" /usr/local/sbin/openmain-dhcp-recovery
cat > /etc/systemd/system/openmain-dhcp-recovery.service <<'UNIT'
[Unit]
Description=Retry DHCP only for configured DHCP interfaces without IPv4
After=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/openmain-dhcp-recovery
TimeoutStartSec=90
UNIT
cat > /etc/systemd/system/openmain-dhcp-recovery.timer <<'UNIT'
[Unit]
Description=OpenMain DHCP recovery
[Timer]
OnBootSec=30s
OnUnitInactiveSec=120s
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now openmain-dhcp-recovery.timer >/dev/null
