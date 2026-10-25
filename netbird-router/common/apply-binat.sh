#!/usr/bin/env bash
set -Eeuo pipefail
transaction=$(mktemp)
trap 'rm -f "$transaction"' EXIT
if nft list table ip openmain_binat >/dev/null 2>&1; then
  printf 'delete table ip openmain_binat\n' > "$transaction"
fi
cat /etc/nftables.d/openmain-binat.nft >> "$transaction"
nft -c -f "$transaction"
nft -f "$transaction"
