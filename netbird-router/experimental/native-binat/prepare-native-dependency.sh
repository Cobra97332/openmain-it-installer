#!/usr/bin/env bash
# Build-only ABI correction for the exact public dependency; no router actions.
set -Eeuo pipefail
patch_file=${1:?dependency patch required}
go_binary=${GO_BINARY:-go}
[[ $("$go_binary" list -m -f '{{.Version}}' github.com/google/nftables) == v0.3.0 ]] || {
  echo 'Unexpected nftables dependency version; refusing build.' >&2
  exit 1
}
"$go_binary" mod download github.com/google/nftables
module_dir=$("$go_binary" list -m -f '{{.Dir}}' github.com/google/nftables)
[[ -d $module_dir && ! -e native-deps/nftables && ! -L native-deps/nftables && ! -L native-deps ]] || {
  echo 'Unknown dependency source/destination; refusing build.' >&2
  exit 1
}
# Exact upstream file, verified again before applying the narrowly scoped diff.
printf '%s  %s\n' 6eee76eff643f3d347fa10c50fd00f5a96a5af7d2a87af0adec1b39e01f7e159 \
  "$module_dir/expr/ct.go" | sha256sum --check --status
mkdir -p native-deps
cp -R -- "$module_dir" native-deps/nftables
chmod -R u+w -- native-deps/nftables
git -C native-deps/nftables apply --check "$patch_file"
git -C native-deps/nftables apply "$patch_file"
"$go_binary" mod edit -replace=github.com/google/nftables=./native-deps/nftables
