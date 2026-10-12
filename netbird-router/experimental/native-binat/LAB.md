# Required native NetBird acceptance lab (NOT yet executed)

Do not run this against CT 110/111, Bauer, existing profiles or existing services.
No production token, setup key, PSK, DNS name or management endpoint is required.
The reference scripts below are executable; the native-client section is a
future acceptance procedure, not a claim that the supplied seam is integrated.

## Already executable isolated reference tests

Prepare tests/Dockerfile.router using Debian mirrors. Then disconnect execution:

```sh
docker run --rm --network none --cap-add NET_ADMIN \
  --sysctl net.ipv4.ip_forward=1 -e OPENMAIN_ISOLATED_TEST=1 \
  -v "$PWD:/src:ro" openmain-router-test:debian13 \
  python3 tests/nft_native_snapshot_kernel.py

# Must still run; currently blocks release of the OLD implementation.
docker run --rm --network none --cap-add NET_ADMIN \
  --sysctl net.ipv4.ip_forward=1 -e OPENMAIN_ISOLATED_TEST=1 \
  -v "$PWD:/src:ro" openmain-router-test:debian13 \
  python3 tests/nft_kernel.py
```

These tests use synthetic ACL maps and actual kernel packets/conntrack. Do not
report them as a real NetBird policy lifecycle test. Both must eventually pass
against the integrated implementation; do not remove the older release gates.

## Native client build gate

Obtain exactly netbirdio/netbird v0.80.0/fca64287cf51a85552a41b022013abbfdb335452.
Verify ARCHITECTURE.md archive hash. From that source tree:

```sh
git apply --check /path/to/netbird-v0.80.0-policy-barrier.patch
git apply /path/to/netbird-v0.80.0-policy-barrier.patch
go test ./client/internal/acl ./client/firewall/nftables
```

CI also runs tests/check-native-binat-patch.sh: verified source/toolchain archives,
compiled native ACL code and four barrier contract cases in network-none Docker.
Use its pinned Go 1.26.7 toolchain. The patch adds only a publication seam. Stop
here until a compiled nft backend implements it and startup/reset/close paths
are connected. Applying this patch does not create a working BINAT client.

## Future real-controller lab — mandatory before installer integration

1. Prepare disposable lab-only controller, ordinary peer, primary and backup
   clients. Pin all images/binaries and use NetBird's version-matched official
   self-hosted deployment instructions. Set an outbound-deny isolated network;
   expose the controller only inside it. Use lab-local DNS and a lab CA with
   verified TLS; do not use production domains or disable TLS verification.
2. Generate identities/setup keys exclusively in that controller. Use clean
   profile directories and never mount host NetBird state. Capture no key or
   token contents. Archive hashes, versions and non-secret IDs only.
3. Native backend installs its persistent DENY boundary before enabling the
   overlay/router. Disable neither firewall nor independent policies. Do not
   start the current external ACL reconciler alongside the native publisher.
4. Use separate isolated customer LANs with the same real prefix, different
   virtual prefixes and primary/backup route metrics 100/200. Prove actual
   metric/path changes rather than manually choosing a packet interface.
5. Establish long-lived TCP and bidirectional UDP sessions. Confirm conntrack
   state and counters without dumping unrelated tables or secret profiles.
   Withdraw a source group through the synthetic controller. Define and record
   native publication ACK; send old/new tuples and replies immediately afterward.
   No post-withdrawal packet may pass the authorization hook. State delivery
   latency and already-in-flight packets separately.
6. Inject DROP during refresh, multiple independent overlapping source policies,
   port restrictions, echo ICMP and RELATED ICMP errors. Invalid/partial maps and
   interrupted publication must not restore an older ACCEPT snapshot.
7. Kill/restart the client during every update stage; delete/rebuild its ordinary
   native table via the native reset path. The persistent boundary remains closed
   until the latest complete map succeeds. Test rejected nft transactions and
   table-missing startup; abort acceptance on any unconfirmed closure.
8. Preserve a foreign firewall table and a foreign DROP policy and independent
   peer policies byte-for-byte; verify their packet effects still apply.
9. Repeat on disposable Debian-13 VM, fresh unprivileged Proxmox LXC with TUN,
   nftables/conntrack capabilities, and Raspberry Pi 3/4/5 ARM64. Exercise real
   reboot/failover. QEMU synthetic BIOS/UEFI fixtures are not netinstall acceptance.
10. Publish complete traces/counters and exact client/controller/installer SHAs.
    Obtain Codex security review of that same head. Only after all mandatory
    gates pass may a separate change propose installer integration.
