# Required native NetBird acceptance lab (NOT yet executed)

Do not run this against CT 110/111, Bauer, existing profiles or existing services.
No production token, setup key, PSK, DNS name or management endpoint is required.
The core publisher scripts are executable. The real-controller section remains
a required acceptance procedure, not a claim of completed daemon acceptance.

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

CI runs `tests/check-native-binat-patch.sh`: verified source/toolchain archives,
compiled native publisher/core contracts, actual Go-publisher kernel packets,
a mandatory hard-owner-death kernel gate, ARM64 Go contracts under QEMU-user,
native authoritative-map/barrier contracts and actual Engine/nft compilation.
Use pinned Go 1.26.7. All native code is contained in the patch, not the installer.

The normal packet suite accepts `/native/native-publisher-lab`, built from the
patched `./client/firewall/binat/cmd/lab`. It receives synthetic maps over stdin,
not real NetworkMap/controller updates. The driver creates only protected
synthetic lab scopes and refuses a namespace with any NIC besides loopback.
CI repeats it with `OPENMAIN_NATIVE_OWNER_DEATH_GATE=1`; that gate must also
pass before release. It currently demonstrates a surviving grant after SIGKILL.
No successful release is claimed from the other passing scenarios.

Actual client laboratory opt-in (only disposable isolated network namespaces):
- Root-owned `/run/netbird-binat-lab` mode 0700; `config.json` regular 0600,
  JSON array of `Virtual`, `Real`, `Overlay`, `LAN` strings. The overlay name
  must match that client's real WireGuard interface. Use only synthetic nets.
- Enable both lab environment variables described in ARCHITECTURE.md. Do not
  enable an external OpenMain ACL helper concurrently. Keep native firewall on.
- No production configuration/identity directory mounts, public endpoints,
  tokens or broad forwarding exposure. No flowtable/offload objects.
- Add translation only inside the fresh isolated namespace; confirm native
  original-destination ACLs and persistent guard before starting traffic.
- Capture only generic local ACK serial/epoch/digests; never credentials.

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

## Small native candidate

Follow SMALL-NATIVE.md and apply only the small patch to the pinned clean source.
Use its protected `/run/netbird-binat-small-lab/config.json`, with both lab env
variables and explicit `NB_WG_KERNEL_DISABLED=true`. Keep native nftables enabled;
no existing overlay interface or persistent TUN may be adopted. Do not enable the
external helper or the old full-snapshot patch in this client/namespace.

Run `bash tests/check-native-binat-patch.sh small`. The QEMU fixture additionally
requires `tests/qemu_platforms.py --native-small /path/to/small-native-lab`; both
TUN lifetime scenarios run before the unchanged legacy release gates. Do not use
`--unit-only` to claim acceptance. The veth-death counterexample remains failing
and is explicitly distinguished from verified process-owned TUN behavior.

Before integration, use actual isolated clients/controller as described above:
verify full-map delivery, normal policy refresh, original/reply TCP/UDP/ICMP,
management disconnect, an invalid map, native update/readback failures, SIGKILL,
systemd restart with the same identity, and real 100/200 failover. Verify every
restart gets a fresh authoritative map before allowing a new flow. Verify no
configuration-only message leaves an unexplained permanent outage. Measure Pi
throughput/CPU and document the service-exit availability tradeoff.
