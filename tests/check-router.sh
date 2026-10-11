#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname -- "${BASH_SOURCE[0]}")/.."
files=(router-install.sh zabbix-proxy-install.sh zabbix-api-register.sh firstboot-router.sh build-iso.sh
       netbird-router/install.sh netbird-router/{pve,vm,raspberry-pi}/*.sh netbird-router/common/*.sh
       netbird-prometheus/enable-client{,-detail}-metrics.sh grafana-zabbix/install{,-docker}.sh)
for file in "${files[@]}"; do bash -n "$file"; done
shellcheck "${files[@]}"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
