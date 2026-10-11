#!/usr/bin/env bash
set +xv
set -Eeuo pipefail
umask 077
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
[[ $EUID -eq 0 ]] || { echo 'Als root ausfuehren.' >&2; exit 1; }
# Fail closed instead of installing dependencies before inspecting the host.
command -v python3 >/dev/null || { echo 'Python 3 fuer die unveraendernde Bestandspruefung erforderlich; keine Pakete installiert.' >&2; exit 1; }
# Preserve existing direct CLI usage. Default/JSON usage persists admission input
# before the core, so an interrupted VM installation does not prompt again.
if [[ $# -gt 0 && "$1" != --config ]]; then
  exec bash "$ROOT/router-install.sh" "$@"
fi
CONFIG=/etc/openmain-router/vm.json
INPUT=''
if [[ "${1:-}" == --config ]]; then
  [[ $# -ge 2 ]] || { echo 'Konfigurationspfad fehlt.' >&2; exit 1; }
  CONFIG=$2; INPUT=$CONFIG; shift 2
elif [[ -e "$CONFIG" || -L "$CONFIG" ]]; then
  INPUT=$CONFIG
fi
preflight_args=(--report-profile)
[[ -z "$INPUT" ]] || preflight_args+=(--config "$INPUT")
profile=$(python3 "$ROOT/netbird-router/common/preflight.py" "${preflight_args[@]}")
args=("$CONFIG")
[[ -z "$INPUT" ]] || args+=("$INPUT")
if [[ -z "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["path"])' <<< "$profile")" ]]; then
  args+=(--require-setup-key)
fi
python3 "$ROOT/netbird-router/common/prepare_config.py" "${args[@]}"
exec bash "$ROOT/router-install.sh" --config "$CONFIG" "$@"
