#!/usr/bin/env bash
# Public, pinned source preparation; execution only in network-none Docker.
set -Eeuo pipefail
variant=${1:-snapshot}
case "$variant" in
  snapshot) patch_name=policy-barrier; driver=lab; binary=native-publisher-lab; packet_test=nft_go_publisher_kernel.py; owner_gate=OPENMAIN_NATIVE_OWNER_DEATH_GATE ;;
  small) patch_name=small-native; driver=small-lab; binary=small-native-lab; packet_test=nft_small_native_kernel.py; owner_gate=OPENMAIN_SMALL_OWNER_DEATH_GATE ;;
  *) echo "Unknown native test variant" >&2; exit 2 ;;
esac
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
lab=$(mktemp -d "${TMPDIR:-/tmp}/openmain-native-build.XXXXXXXX")
cleanup() {
  local previous_status=$?
  chmod -R u+w -- "$lab"
  rm -rf -- "$lab"
  exit "$previous_status"
}
trap cleanup EXIT
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
git apply --check "$repo/netbird-router/experimental/native-binat/netbird-v0.80.0-${patch_name}.patch"
git apply "$repo/netbird-router/experimental/native-binat/netbird-v0.80.0-${patch_name}.patch"
export GOPATH="$lab/modules" GOCACHE="$lab/cache" GOPROXY=https://proxy.golang.org
compile() {
  local output=$1 package=$2
  if ! "$lab/go/bin/go" test -c -buildvcs=false -o "$lab/$output" "$package" >"$lab/compiler.log" 2>&1; then
    # Proxy diagnostics may contain signed URLs. Never emit raw responses.
    if rg -q 'Forbidden|Get "https:|proxyconnect' "$lab/compiler.log"; then
      echo "Native $package compilation blocked by dependency download." >&2
    else
      echo "Native $package compilation failed; no native acceptance claimed." >&2
      # Only source-positioned compiler diagnostics, with URLs removed.
      sed -n '/^[a-zA-Z_/].*\.go:[0-9][0-9]*:/p' "$lab/compiler.log" | sed 's@https://[^ ]*@<redacted-url>@g' >&2
    fi
    return 1
  fi
}
docker_local() {
  env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH \
    docker --host=unix:///var/run/docker.sock "$@"
}
compile core.test ./client/firewall/binat
docker_local run --rm --network none -v "$lab/core.test:/lab/core.test:ro" \
  openmain-router-test:debian13 /lab/core.test -test.v -test.count=1
"$lab/go/bin/go" build -buildvcs=false -o "$lab/$binary" "./client/firewall/binat/cmd/$driver"
if [[ $variant == small && -n ${OPENMAIN_NATIVE_DRIVER_EXPORT:-} ]]; then
  install -m 0755 "$lab/$binary" "$OPENMAIN_NATIVE_DRIVER_EXPORT"
fi
native_status=0
docker_local run --rm --network none --cap-add NET_ADMIN \
  --sysctl net.ipv4.ip_forward=1 -e OPENMAIN_ISOLATED_TEST=1 \
  -v "$repo:/src:ro" -v "$lab/$binary:/native/$binary:ro" \
  openmain-router-test:debian13 python3 "tests/$packet_test" || native_status=1
# A hard-killed native owner must not leave old permissions. Keep this additional
# P1 gate failing until the architecture can prove that invariant.
docker_local run --rm --network none --cap-add NET_ADMIN \
  --sysctl net.ipv4.ip_forward=1 -e OPENMAIN_ISOLATED_TEST=1 \
  -e "$owner_gate=1" \
  -v "$repo:/src:ro" -v "$lab/$binary:/native/$binary:ro" \
  openmain-router-test:debian13 python3 "tests/$packet_test" || native_status=1
GOARCH=arm64 CGO_ENABLED=0 compile core-arm64.test ./client/firewall/binat
docker_local run --rm --network none -v "$lab/core-arm64.test:/lab/core-arm64.test:ro" \
  openmain-router-test:debian13 qemu-aarch64 /lab/core-arm64.test -test.v -test.count=1
compile acl.test ./client/internal/acl
docker_local run --rm --network none -v "$lab/acl.test:/lab/acl.test:ro" \
  openmain-router-test:debian13 /lab/acl.test -test.v -test.run '^TestBINAT' -test.count=1
# Compile the actual Engine and native backend too; core-only compilation cannot
# validate the production lifecycle call sites. No Engine daemon is started.
compile engine.test ./client/internal
compile nftables.test ./client/firewall/nftables
if [[ $variant == small ]]; then
  compile sync-loss.test ./shared/management/client
  docker_local run --rm --network none -v "$lab/sync-loss.test:/lab/sync-loss.test:ro" \
    openmain-router-test:debian13 /lab/sync-loss.test -test.v -test.run '^TestBINATSync' -test.count=1
  if ! "$lab/go/bin/go" test -race -count=1 -run '^TestBINAT' \
    ./client/firewall/binat ./shared/management/client >"$lab/compiler.log" 2>&1; then
    echo "Native BINAT gate/stream-loss race tests failed; no acceptance claimed." >&2
    exit 1
  fi
  echo "PASS: native gate/stream-loss Go race tests"
  docker_local run --rm --network none -v "$lab/nftables.test:/lab/nftables.test:ro" \
    openmain-router-test:debian13 /lab/nftables.test -test.v -test.run '^TestBINATSmall' -test.count=1
fi

test "$native_status" -eq 0
