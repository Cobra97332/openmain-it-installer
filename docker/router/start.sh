#!/usr/bin/env bash
set +xv
set -Eeuo pipefail
umask 077
exec python3 /opt/openmain/router.py "${1:-gateway}"
