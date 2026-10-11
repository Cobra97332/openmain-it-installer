#!/usr/bin/env bash
set +xv
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
[[ $EUID -eq 0 ]] || { echo 'Als root ausfuehren.' >&2; exit 1; }
# Fail closed instead of installing dependencies before inspecting the host.
command -v python3 >/dev/null || { echo 'Python 3 fuer die unveraendernde Bestandspruefung erforderlich; keine Pakete installiert.' >&2; exit 1; }
exec bash "$ROOT/router-install.sh" "$@"
