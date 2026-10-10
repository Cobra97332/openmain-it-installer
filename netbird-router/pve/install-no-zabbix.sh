#!/usr/bin/env bash
set +xv
set -Eeuo pipefail
export NB_ZABBIX_ENABLED=0
exec bash "$(dirname -- "${BASH_SOURCE[0]}")/install.sh" "$@"
