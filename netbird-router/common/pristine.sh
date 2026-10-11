#!/usr/bin/env bash
set +xv
set -Eeuo pipefail
# Read-only bootstrap gate, also valid after an interrupted CT creation.
# Ordinary filter rules are retained; router identities and any NAT need review.
for path in /etc/netbird /var/lib/netbird /etc/zabbix /var/lib/zabbix \
  /var/lib/netbird-kundenrouter /etc/openmain-router \
  /etc/sysctl.d/99-netbird-kundenrouter.conf \
  /etc/nftables.d/netbird-kundenrouter.nft /etc/nftables.d/openmain-binat.nft; do
  [[ ! -e "$path" && ! -L "$path" ]] || exit 1
done
for cmd in netbird zabbix_proxy zabbix_agent2; do
  ! command -v "$cmd" >/dev/null || exit 1
done
unsafe_rules(){
  local result=0
  grep -Eq '(^|[^[:alnum:]_])(mein_binat|openmain_binat|netbird|masquerade|redirect)([^[:alnum:]_]|$)|type[[:space:]]+nat([^[:alnum:]_]|$)|(dnat|snat)[[:space:]]+(ip6?[[:space:]]+)?(prefix[[:space:]]+)?to([^[:alnum:]_]|$)' || result=$?
  # A grep/read error is ambiguous and must reject rather than admit.
  [[ $result == 1 ]] && return 1
  return 0
}
declare -A checked=()
check_file(){
  local file=$1 line include included_file
  [[ ! -L "$file" ]] || exit 1
  [[ -f "$file" ]] || return 0
  [[ -z "${checked[$file]:-}" ]] || return 0
  checked[$file]=1
  ! unsafe_rules < "$file" || exit 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ ^[[:space:]]*include[[:space:]] ]]; then
      [[ "$line" =~ ^[[:space:]]*include[[:space:]]+\"([^\"]+)\"[[:space:]]*\;?[[:space:]]*(#.*)?$ ]] || exit 1
      include=${BASH_REMATCH[1]}
      [[ "$include" == /* ]] || exit 1
      while IFS= read -r included_file; do check_file "$included_file"; done < <(compgen -G "$include" || true)
    fi
  done < "$file"
}
check_file /etc/nftables.conf
[[ ! -L /etc/nftables.d ]] || exit 1
shopt -s globstar nullglob dotglob
for file in /etc/nftables.d/**/*; do check_file "$file"; done
if command -v nft >/dev/null; then
  rules=$(nft -nn list ruleset 2>/dev/null) || exit 1
  ! unsafe_rules <<< "$rules" || exit 1
fi
