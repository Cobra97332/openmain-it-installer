#!/usr/bin/env bash
# Public, pinned source preparation; execution only in network-none Docker.
set -Eeuo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
lab=$(mktemp -d "${TMPDIR:-/tmp}/openmain-native-build.XXXXXXXX")
trap 'result=$?; chmod -R u+w -- "$lab"; rm -rf -- "$lab"; exit "$result"' EXIT
curl --fail --silent --show-error --location --retry 3 \
  https://codeload.github.com/netbirdio/netbird/tar.gz/refs/tags/v0.80.0 -o "$lab/netbird.tgz"
(
  cd "$lab"
  printf '%s  netbird.tgz\n' ab453b7d015fd657c912c40986868784f77c3460f296dc07bc2c7f07f4bce7b7 | sha256sum --check --status
)
curl --fail --silent --show-error --location --retry 3 \
  https://go.dev/dl/go1.26.7.linux-amd64.tar.gz -o "$lab/go.tgz"
(
  cd "$lab"
  printf '%s  go.tgz\n' ffb5f8de10c62550dfddab66b36b57030721e0a44a3218e9e1181d7b59f121ca | sha256sum --check --status
)
tar -xzf "$lab/netbird.tgz" -C "$lab"
tar -xzf "$lab/go.tgz" -C "$lab"
cd "$lab/netbird-0.80.0"
git apply --check "$repo/netbird-router/experimental/native-binat/netbird-v0.80.0-policy-barrier.patch"
git apply "$repo/netbird-router/experimental/native-binat/netbird-v0.80.0-policy-barrier.patch"
export GOPATH="$lab/modules" GOCACHE="$lab/cache" GOPROXY=https://proxy.golang.org
if ! "$lab/go/bin/go" test -c -o "$lab/acl.test" ./client/internal/acl >"$lab/compiler.log" 2>&1; then
  # Module proxy diagnostics can contain signed download URLs. Never emit them.
  if rg -q 'Forbidden|Get "https:' "$lab/compiler.log"; then
    echo 'Native patch compilation blocked by dependency download.' >&2
  else
    echo 'Native patch compilation failed; no native acceptance claimed.' >&2
  fi
  exit 1
fi
env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH \
  docker --host=unix:///var/run/docker.sock run --rm --network none \
  -v "$lab/acl.test:/lab/acl.test:ro" openmain-router-test:debian13 \
  /lab/acl.test -test.v -test.run '^TestBINATBarrier' -test.count=1
