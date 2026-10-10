#!/usr/bin/env bash
set +xv
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
[[ $EUID -eq 0 ]] || { echo 'Als root ausfuehren.' >&2; exit 1; }
# Debian's minimal VM images may not include Python or jq yet.
# shellcheck disable=SC1091
. /etc/os-release
[[ "$ID" == debian && "$VERSION_ID" == 13 ]] || { echo 'Debian 13 erforderlich.' >&2; exit 1; }
if ! command -v python3 >/dev/null || ! command -v jq >/dev/null; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3 jq iproute2 curl ca-certificates
fi
exec bash "$ROOT/router-install.sh" "$@"
