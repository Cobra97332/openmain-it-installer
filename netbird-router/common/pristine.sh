#!/usr/bin/env bash
set +xv
set -Eeuo pipefail
# Read-only bootstrap gate for a newly created Debian CT without Python.
# Any sign of a previous router/monitoring installation requires full preflight.
for path in /etc/netbird /var/lib/netbird /etc/zabbix /var/lib/zabbix \
  /var/lib/netbird-kundenrouter /etc/openmain-router \
  /etc/nftables.d /etc/sysctl.d/99-netbird-kundenrouter.conf; do
  [[ ! -e "$path" && ! -L "$path" ]] || exit 1
done
for cmd in netbird zabbix_proxy zabbix_agent2; do
  ! command -v "$cmd" >/dev/null || exit 1
done
if [[ -f /etc/nftables.conf ]]; then
  ! grep -Eq 'mein_binat|openmain_binat|include' /etc/nftables.conf || exit 1
fi
if command -v nft >/dev/null; then
  tables=$(nft list tables 2>/dev/null) || exit 1
  [[ -z "$tables" ]] || exit 1
fi
