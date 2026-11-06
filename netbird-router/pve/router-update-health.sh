#!/usr/bin/env bash
set -Eeuo pipefail

source /etc/openmain-router-update.conf
case "${1:-}" in
  role) printf '%s\n' "$ROLE"; exit 0 ;;
  ready) ;;
  *) exit 1 ;;
esac

fail(){ echo 0; exit 0; }
[[ -s /var/lib/netbird-kundenrouter/state.json ]] || fail
[[ $(cat /proc/sys/net/ipv4/ip_forward) == 1 ]] || fail
systemctl is-active --quiet netbird || fail
status=$(netbird status 2>/dev/null) || fail
grep -Eq '^Management:[[:space:]]+Connected' <<<"$status" || fail
grep -Eq '^Signal:[[:space:]]+Connected' <<<"$status" || fail
virtual=$(jq -er '.virtual' /var/lib/netbird-kundenrouter/state.json 2>/dev/null) || fail
lan=$(jq -er '.lan' /var/lib/netbird-kundenrouter/state.json 2>/dev/null) || fail
rules=$(nft list table ip openmain_binat 2>/dev/null) || rules=$(nft list table ip mein_binat 2>/dev/null) || fail
grep -Fq "$virtual" <<<"$rules" || fail
grep -Fq "$lan" <<<"$rules" || fail
grep -Fq 'dnat ip prefix' <<<"$rules" || fail
gateway=$(ip -4 route show default | awk 'NR==1 {print $3}')
[[ -n "$gateway" ]] || fail
ping -n -c 1 -W 2 "$gateway" >/dev/null 2>&1 || fail
echo 1
