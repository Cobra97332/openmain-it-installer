#!/usr/bin/env bash
set -Eeuo pipefail
RUNTIME="$(dirname -- "${BASH_SOURCE[0]}")/runtime.py"
[[ -f "$RUNTIME" ]] || RUNTIME=/usr/local/lib/openmain-router/runtime.py
# Ask the active network manager; never run a competing DHCP client.
dhcp_manager(){
  local iface="$1" connection details file
  if command -v nmcli >/dev/null && systemctl is-active --quiet NetworkManager; then
    connection=$(nmcli -g GENERAL.CON-UUID device show "$iface" 2>/dev/null || true)
    [[ -n "$connection" && "$connection" != -- ]] || return 1
    [[ "$(nmcli -g ipv4.method connection show "$connection")" == auto ]] || return 1
    echo NetworkManager
  elif command -v networkctl >/dev/null && systemctl is-active --quiet systemd-networkd; then
    details=$(networkctl --json=short status "$iface" 2>/dev/null || true)
    file=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("NetworkFile", ""))' <<<"$details" 2>/dev/null || true)
    [[ -f "$file" ]] || return 1
    grep -Eq '^[[:space:]]*DHCP[[:space:]]*=[[:space:]]*(yes|ipv4|true)[[:space:]]*$' "$file" || return 1
    echo networkd
  elif command -v ifquery >/dev/null && ifquery --no-mappings --no-scripts "$iface" >/dev/null 2>&1; then
    # Classic ifquery prints only options, often nothing for DHCP. Query success
    # establishes ownership; inspect the method without scripts or DHCP probes.
    python3 "$RUNTIME" dhcp-ifupdown "$iface" >/dev/null 2>&1 || return 1
    echo ifupdown
  else
    return 1
  fi
}

if [[ "${1:-}" != --detect ]]; then
  exec 9>/run/openmain-dhcp-recovery.lock
  flock -n 9 || exit 0
fi
found=0
for device in /sys/class/net/*; do
  iface=${device##*/}
  [[ "$iface" != lo ]] || continue
  manager=$(dhcp_manager "$iface") || continue
  found=1
  [[ "${1:-}" != --detect ]] || continue
  ip -4 -o addr show dev "$iface" scope global | grep -q ' inet ' && continue
  case "$manager" in
    NetworkManager) timeout 25 nmcli device reapply "$iface" >/dev/null 2>&1 || true;;
    networkd) timeout 25 networkctl renew "$iface" >/dev/null 2>&1 || true;;
    ifupdown) timeout 25 ifup --force "$iface" >/dev/null 2>&1 || true;;
  esac
done
[[ "${1:-}" != --detect || "$found" == 1 ]]
