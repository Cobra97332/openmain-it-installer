#!/usr/bin/env bash
set +xv
set -Eeuo pipefail
# Download ONE immutable public commit. No production service is contacted here.
# Local checkout users invoke their platform entrypoint directly.
[[ $EUID -eq 0 ]] || { echo 'Als root starten.' >&2; exit 1; }
PLATFORM="${NB_PLATFORM:-vm}"
case "$PLATFORM" in vm|pve|raspberry-pi) ;; *) echo 'NB_PLATFORM: vm, pve, raspberry-pi' >&2; exit 1;; esac
for tool in curl python3; do command -v "$tool" >/dev/null || { echo "Voraussetzung fehlt: $tool" >&2; exit 1; }; done
TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT
REF="${NB_INSTALLER_REF:-}"
if [[ -z "$REF" ]]; then
  curl -fsSL --max-time 30 https://api.github.com/repos/Cobra97332/openmain-it-installer/commits/main -o "$TMP/commit.json"
  REF=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha"])' "$TMP/commit.json")
fi
[[ "$REF" =~ ^[0-9a-f]{40}$ ]] || { echo 'NB_INSTALLER_REF muss ein vollstaendiger Commit-SHA sein.' >&2; exit 1; }
curl -fsSL --max-time 180 "https://api.github.com/repos/Cobra97332/openmain-it-installer/tarball/$REF" -o "$TMP/repo.tar.gz"
python3 - "$TMP/repo.tar.gz" "$TMP/source" <<'PY'
import sys,tarfile
with tarfile.open(sys.argv[1]) as archive:
    archive.extractall(sys.argv[2], filter='data')
PY
source_dirs=("$TMP/source/"*)
[[ ${#source_dirs[@]} == 1 ]]
ROOT=${source_dirs[0]}
entry=install.sh
[[ "$PLATFORM" != raspberry-pi ]] || entry=install-router.sh
bash "$ROOT/netbird-router/$PLATFORM/$entry" "$@"
