# Historical research — NOT the selected architecture

Preserved for review and reproducibility. Statements about mandatory platforms,
publishers and unexecuted tests below describe earlier revisions. They are not
current delivery instructions. See ARCHITECTURE.md and INTERNAL-TEST-SCOPE.md.

## Archived ARCHITECTURE.md

# Experimental native BINAT policy publication — BLOCKED

Current internal AMD64 CI scope and historical-versus-current test obligations:
[INTERNAL-TEST-SCOPE.md](INTERNAL-TEST-SCOPE.md). The historical broad lab
requirements below are not a claim of current platform support or approval.

Base: OpenMain f0d8738b982bdd68090ffd3c1f1cd821b03f982c (PR #33).
This directory is excluded from the installer bundle. Neither installer behavior
nor the unsafe existing helper is changed. **Do not deploy or merge PR #33.**

## Current simplification candidate

See [SMALL-NATIVE.md](SMALL-NATIVE.md). `netbird-v0.80.0-small-native.patch`
delegates authorization directly to native route ACLs, removes the duplicate
snapshot/receipt machinery and requires existing userspace WireGuard on a
nonpersistent TUN. It is a separate mutually exclusive lab patch, not an installer
change. The complete-map publisher below is retained as research evidence; its
security gates remain mandatory and unchanged. Release remains blocked.

## Source review and exact dependencies

Reviewed NetBird v0.80.0, commit fca64287cf51a85552a41b022013abbfdb335452.
Archive SHA256: ab453b7d015fd657c912c40986868784f77c3460f296dc07bc2c7f07f4bce7b7.
GitHub's latest published release remained v0.80.0 during this investigation.
Also reviewed newer upstream main 67c2041fb5534c19d7b2320617cfc3aaab8e7d3d,
2026-10-09, archive SHA256:
f7632d4c48eeb9986af428d50ad55193adf44faed7b426334ab2e01ecc3569c2.
It is an unreleased source revision, not a newer released client.

Sources (relative to the pinned NetBird tree):
- client/internal/acl/manager.go: ApplyFiltering holds a mutex; hash skipping
  omits processing identical maps. applyRouteACLs adds rules before deleting
  obsolete rules; errors preserve some old tracking entries. Final Flush is
  NOT an atomic full-map publication: AddFilterRule/DeleteFilterRule flush too.
- client/firewall/nftables/filter_linux.go: destination/source matches use
  payload addresses; transport matches read current headers. DROP inserts,
  ACCEPT appends. Raw userdata is native RuleID; external replacement breaks
  ownership unless identity/handle and native tracking are preserved.
- client/firewall/nftables/manager_linux.go: createWorkTableFamily deletes the
  entire work table; insertReturnTrafficRule permits ESTABLISHED/RELATED.
- client/firewall/nftables/chains_linux.go: DNAT precedes forward filtering;
  external-chain monitor modifies INPUT/FORWARD filter hooks only. A separate
  POSTROUTING filter boundary is not one of those external accept targets.
- shared/management/types/policyrule.go: ALL/TCP/UDP/ICMP and DROP/port ranges.

Newer main retains the same reviewed ApplyFiltering and route-match lifecycle.
The filter diff removes hasDNATRule; manager removes loopback EBPF NOTRACK
machinery. Neither change introduces original-tuple route authorization or
synchronous complete-map BINAT revocation. No upstream security fix is assumed.

## Architecture alternatives

| Approach | Realizability | Security and maintenance |
|---|---|---|
| Native single-owner complete-map publication with original tuples | Extend the native ACL manager and nft backend | Preferred. No copied live ACL handles, no timer/event authorization. Close BINAT before incremental native work; commit a complete original-tuple snapshot only after success. Needs full lifecycle wiring. |
| Native atomic generation replacing all route rules and NAT in one batch | Refactor every per-rule Flush and native set/refcount/handle bookkeeping | Viable upstream design; avoids intermediate denial but substantially larger backend change. Must gate existing/reply flows and retain a persistent boundary through resets; simply replacing destination matches is insufficient. |
| External monitor/events/retries or cached packet proofs | Existing helper can be modified | Rejected as security boundary. Independent writers retain race windows, new DROP may miss after DNAT, stored proofs outlive authorization. |

Select the first approach for the experiment: conservative denial during updates
is preferable to accidental access. A second native batch implementation may
later reduce the availability interruption without changing authorization rules.

## Implemented native experiment

The patch now contains `client/firewall/binat`, a Linux single-owner nft publisher
inside the pinned NetBird process, and actual Engine/ACL/nft backend call sites.
It is opt-in only with `OPENMAIN_ISOLATED_TEST=1` and
`NB_EXPERIMENTAL_BINAT_CONFIG=/run/netbird-binat-lab/config.json`. Configuration,
owner lock and adoption receipt are root-owned, protected and symlink-refusing.
The preceding owned v3 guard layout is recognized by independently compiled
kernel fingerprints AND matching protected scope/guard/current receipts. Its
current grants are emptied AND its guard replaced in the same first atomic
live nft transaction. Both kernel readbacks are verified before a receipt/ACK.
The kernel regression keeps NOTRACK routing active across upgrade and records
that first live mutation through a transparent real-nft command-boundary spy.
Atomic retry is bounded and only permits the same proven owned legacy state;
a tuple-only fallback closure is never called.
Unknown layouts are refused unchanged even if a receipt matches them. This is
an isolated experimental-table upgrade, not a customer/profile migration.
Stock NetBird without the lab variable has no publisher or destination rewrite.
Releasing the active owner atomically clears its scope registry and local ACK;
a previous released owner cannot erase a subsequent owner's configuration.
This patch remains excluded from OpenMain's installer bundle.

`acl.BINATRules` consumes the full authoritative NetworkMap before native work.
It preserves every source, ACCEPT/DROP, ALL/TCP/UDP/ICMP and port/range selector.
Before semantic validation, the publisher binds the received serial and a
policy-only deterministic protobuf digest. A rejected partial/unsupported newer
map therefore cannot be replaced by an older or different same-serial policy;
that observation is retained in the protected adoption receipt.
Explicit empty policies deny; partial, contradictory-serial, dynamic/domain,
custom protocol and service-forwarding maps refuse publication. Virtual IPv4
prefixes are disjoint; different virtual prefixes may map to the same real LAN.
No real/virtual prefix overlap, arbitrary external table adoption or flowtable
admission is allowed. Real packet DNAT/SNAT rules in tests remain synthetic;
this patch implements authorization, not a new production translation installer.

The Engine closes BINAT before management envelope/config/NetworkMap processing,
peer address, DNS, route and ACL changes, and management-stream termination.
Start bootstraps a persistent closed boundary before interface creation and
requires the native nft backend, active firewall and matching overlay interface.
Stop, native table rebuild, rollback and firewall Close synchronously close it.
A configured but absent owner, or unconfirmed closure, refuses native teardown
and rollback mutations instead of removing the still-protective native rules.
A failed native operation, absent native chain/tracked rule, stale map, context
cancellation or publication/readback failure cannot produce a successful ACK.
The ACL manager disables hash shortcuts for managed publication and exposes
native apply errors. Native scoped route destination loads become original
conntrack-destination loads, preserving native RuleIDs, userdata and tracking.
No other policy verdict, foreign table or native rule handle is replaced.

The publisher owns `ip netbird_binat_experiment_v3`. Its immutable POSTROUTING
filter hook precedes SNAT and independently rejects unauthorized original AND
reply tuples after earlier native ESTABLISHED/RELATED shortcuts. The current
chain contains original source/destination/protocol/destination-port matches;
DROP always precedes overlapping ACCEPT. RELATED ICMP uses its parent's original
tuple, rather than a blanket RELATED allowance or blanket ICMP grant. Invalid
and untracked scoped traffic drops. A separate reserved-virtual-header denial
also covers untracked packets without an original tuple; the kernel test adds a
real route/neighbor and NOTRACK rule to prove this is a guard drop, not no-route. Empty current chains fall through to scoped
DROP. No packet marks, asynchronous authorization or conntrack deletion is used.

One mutex closes before native mutation, prepares a complete candidate and
atomically replaces the live policy only after native success. Candidate and
live fingerprints come from separate actual kernel reads, not nft's transaction
input representation. Fingerprints include selectors, verdicts, order and owner
comments, exclude handles/counter values. Protected receipts retain serial floor,
policy/guard digests and epoch through owner reload. Close invalidates pending
work and prior ACK. Indeterminate publication immediately attempts confirmed
re-closure; failed closure is reported UNCONFIRMED, never called fail-closed.
`nft` is a bounded local transaction codec, not an independent ACL reconciler.

A local ACK contains NetworkMap serial, local epoch, canonical policy digest and
read-back kernel digest, **ControllerConfirmed=false**. NetBird v0.80.0 Sync has
no client kernel-publication acknowledgement protocol. Controller API success
and local delivery latency are not controller confirmation of revocation.
Packets already beyond the authorization hook may finish. No packet-drainage
or zero controller-to-client latency guarantee is claimed.

## Remaining release blockers / safety limits

1. **P1: hard owner death.** SIGKILL can leave a committed authorization chain
   and kernel WireGuard device alive. Graceful Stop/reset/reload hooks cannot
   execute then. A new mandatory real-kernel owner-loss gate demonstrates this
   and fails; it is not an expected failure or waiver. No deployment is allowed.
   A viable next architecture is a dedicated disposable network namespace whose
   entire forwarding path and WireGuard device are owned by the native service,
   with guaranteed kernel teardown on loss of every task/namespace reference.
   That requires proving child-process/cgroup kill and reference lifetimes,
   routes and host exposure. A watchdog/timer alone is not an equivalent proof.
2. **P1: shipped helper remains unsafe.** All six inherited P1 gates stay
   unchanged and failing. The experimental patch is not installed by OpenMain;
   passing native publication tests does not resolve the old runtime's P1s.
3. **Unverified real lifecycle.** Source wiring and Go/core packet tests do not
   prove complete real Engine/controller ordering, live NetworkMap-envelope
   processing, reset/reconnect and actual primary/backup metric-100/200 failover.
   No isolated real controller/client deployment has been run in this revision.
4. **Exclusive privileged ownership assumption.** Another privileged writer
   deleting the guard, adding flowtables after admission or bypassing the kernel
   hook is outside the current proof. Readback cannot make two independent
   writers race-free. Missing native tables are checked before commit; the
   separate guard survives owned native reset. External destruction after the
   check remains a production blocker until exclusive lifetime is enforced.
5. **Limited feature scope.** IPv6 BINAT, dynamic DNS/resource destinations,
   forwarded services and DNAT port remapping remain unsupported. Unsupported
   maps fail closed instead of publishing a permissive subset. Conservative
   closure on config-only messages can interrupt availability until a complete
   map arrives. Reordered same-serial protobuf rule lists conservatively refuse
   publication. Arbitrary routing/iface changes require a fresh protected scope.
   Durable generation replay protection assumes successful protected receipt
   persistence; storage errors are not certified recovery/owner-lifetime proof.
6. Physical Raspberry Pi, real Proxmox VM/unprivileged LXC, full NetBird daemon,
   official Debian netinstall and real reboot/failover remain uncertified.
   ARM64 Go/Python emulation and synthetic BIOS/UEFI are separate evidence.

Do not integrate this experiment into installer/runtime.py, apply-binat.sh,
binat_acl.py or bundle.files. PR #33 and production integration remain blocked.


## Archived SMALL-NATIVE.md

# Small native BINAT candidate — LAB ONLY, RELEASE BLOCKED

Current internal AMD64 CI scope and historical-versus-current test obligations:
[INTERNAL-TEST-SCOPE.md](INTERNAL-TEST-SCOPE.md). The historical broad lab
requirements below are not a claim of current platform support or approval.

Apply `netbird-v0.80.0-small-native.patch` to the pinned upstream NetBird v0.80.0
source described in ARCHITECTURE.md. Apply ONE patch: the earlier
`policy-barrier.patch` remains a research reference with its tests unchanged.
The small candidate additionally needs the separately recorded
`google-nftables-v0.3.0-ct-direction.patch`. The build-only
`prepare-native-dependency.sh` verifies the exact upstream version/file hash,
copies the public module to the disposable source tree and applies the UAPI fix.
Dockerfile.client and check-native-binat-patch.sh perform this automatically.
Native and dependency patch digests are both bound into the client build marker;
a stale client built from only one matching patch is refused.

Neither patch is included in the OpenMain installer bundle. No existing router,
NetBird identity, BINAT mapping, PSK or configuration is migrated.

## Authorization and lifecycle

NetBird's existing ACL manager remains the ONLY policy publisher. Its native
RuleIDs, source groups/sets, ACCEPT/DROP order, protocols and port selectors are
retained. No second complete-map rule compiler, durable serial receipt, copied
ACL handles, controller ACK protocol, polling process or new service is added.

While opted in, native IPv4 route-rule address/protocol/port loads use conntrack
ORIGINAL tuples. This applies to all IPv4 route destinations so a real-LAN rule
cannot accidentally authorize a different virtual customer destination after
DNAT. Port remapping/forwarding services are refused, not silently interpreted.
The pinned controller's exact empty `PortInfo` message means no port
restriction and is accepted, matching native `convertPortInfo`. A complete map may also retain an empty source list after membership removal:
the native manager skips that rule and removes its previous grant. Validation
accepts this zero-member state while still checking all destination/action/port
semantics; it never interprets missing sources as a wildcard. Deletion failure
keeps the gate closed. Explicit zero
ports, invalid ranges and unknown fields remain rejected; no wildcard is inferred
from an invalid explicit selector. A scoped native POSTROUTING filter before SNAT delegates original and reply
traffic directly to the native routing ACL chain, then drops unmatched traffic.
The native ESTABLISHED shortcut excludes the configured virtual prefixes. In opted-in mode NetBird's automatic external-chain passthrough writer is
suppressed for external nftables chains AND the iptables-backed filter table,
including monitor reconciliation and shutdown cleanup. Existing external INPUT/FORWARD
rules remain authoritative constraints too; administrators must explicitly
permit intended traffic where those rules would otherwise drop it. Native
INPUT rules, foreign tables and unconfigured clients retain their usual behavior.

A small separate scope-bound readiness gate starts CLOSED, closes before native
changes and opens only after complete-map validation, native success and local
kernel readback. An in-memory epoch rejects an open racing with reset/closure.
An observed policy-only digest/serial refuses older or contradictory maps in the
same owner lifetime, including after rejected maps. There is no cached permission
or serial receipt to restore across restart: a fresh full authenticated map must
succeed. Readiness is a LOCAL result, never controller confirmation.

Management `Sync()` retries transport failures internally; waiting for its outer
return is insufficient. The current Engine therefore registers a synchronous
stream-disconnection listener on the existing management client. A failed Sync
stream closes and confirms the local gate, advancing its epoch, BEFORE retry.
The callback runs outside the transport state mutex and never takes the Engine
sync mutex. Reconnect does not reopen it: only a new full validated NetworkMap
and native kernel publication do. Shutdown unregisters this Engine's listener.
Ordinary clients without BINAT retain their behavior; Job-stream failures are
not used as policy events. No additional process, polling or ACL copy is added.
This source-level transport correction still needs the mandatory actual-client
controller-outage/reconnection packet proof before release.

The owned gate layout must match its closed/open kernel reference exactly;
unknown layouts are refused unchanged. Closed/open layout references are compiled once and cached for this owner
lifetime; every check still reads the live kernel. Random no-hook candidate
names avoid a startup collision after owner death. A killed startup can leave
an inert candidate table for later manual lab cleanup, never an active grant.
These bounded local nft invocations run
only inside NetBird lifecycle callbacks. They are not ACL synchronization timers.

## Small, existing dataplane instead of another lifetime supervisor

Kernel WireGuard devices can outlive NetBird. A new actual-packet counterexample
proves a NEW connection can pass after the policy-owner process dies. Merely
accepting delayed established-flow revocation does not fix that failure.

The candidate therefore requires NetBird's EXISTING userspace WireGuard mode:
`NB_WG_KERNEL_DISABLED=true`, a fresh overlay interface and a verified
nonpersistent TUN. Pre-existing overlays, kernel WireGuard, non-TUN devices,
persistent TUNs, netstack/non-native firewall backends are refused. TUN type and
persistence are queried through NetBird's existing rtnetlink library, in the
current network namespace; inherited sysfs mounts are not used as evidence. No automatic
mode switch or migration is performed. Even WITHOUT the opt-in variable, the
patched Linux Engine queries current-namespace nft tables/chains before creating
any tunnel. An orphaned small/old experimental gate or native BINAT boundary
refuses startup, including with firewall disabled or another backend. Inspection
failure refuses startup; nothing is deleted. An incomplete opt-out cannot reuse
an open orphan. Non-Linux stock startup without opt-in stays unchanged.
Do not replace this binary with an unpatched stock client while experimental
kernel state remains: that client has no orphan check. Verified manual cleanup
is required for rollback/opt-out; it is never an automatic customer migration. The source paths are
`client/iface/iface_new_linux.go`, `device/kernel_module_linux.go` and
`device/device_usp_unix.go`; the pinned wireguard-go CreateTUN opens the descriptor.

A hard process death closes the transport/TUN descriptors; the kernel removes
the nonpersistent TUN. If an owned gate closure cannot be confirmed after that
TUN has been verified, NetBird exits immediately instead of continuing with an
unknown grant. Its existing service may restart it, but startup remains closed
until a fresh complete policy applies. This can interrupt availability, including
on transient nft read failures. No extra supervisor or automatic firewall repair
is introduced. Unknown owned layouts require investigation; foreign rules are
never erased to recover availability.

Assumptions: no independent privileged writer duplicates/persists the TUN,
replaces the guard/native rules, installs flowtable bypasses or impersonates its
interface. This is not protection against a malicious local root process.

## Guarantees, accepted risks and required proof

New accesses must be evaluated against the locally delivered native policy;
unsupported/incomplete maps must not reopen the gate. The implementation also
tries to deny established original/reply traffic on policy removal, stronger
than the agreed operating model. Packet drainage and zero controller delivery
latency are NOT guaranteed. Brief existing-flow continuation is accepted; an
unbounded NEW-flow grant after owner death is not. Userspace-WireGuard throughput,
real daemon ordering/reconnect and real primary/backup failover need acceptance.

Debian 13 AMD64/ARM64 are the target. Proxmox VM/LXC and Pi must provide TUN and
native nftables. Raspberry Pi hardware performance, real Proxmox and actual
isolated NetBird clients/controller have NOT been certified by source/fixture
checks. Existing kernel-WireGuard customers remain unchanged and unsupported by
this experiment; adopting this constraint needs separate operational approval.

## Tests — retain failures honestly

`bash tests/check-native-binat-patch.sh small` builds this separate patch, runs
native Go tests, real kernel gate/UDP/TCP/RELATED-ICMP tests, ARM64 QEMU-user,
and compiles the actual ACL manager, Engine and nft backend. The old variant and
all SIX legacy P1 counterexamples still run. The small gate-only veth-owner-death
counterexample also still runs and fails: that fixture retains a dataplane which
the real candidate explicitly refuses. It is NOT silently removed/waived or
reported as passed. It demonstrates why the TUN constraint is necessary.

The platform CI additionally executes `nft_small_tun_lifetime.py` twice in fresh
QEMU network namespaces: SIGKILL and unconfirmed-closure fail-stop. Those tests
use actual TUN, UDP socket, nftables, conntrack and captured LAN packets. The
transport is synthetic plaintext, NOT real WireGuard/NetBird enrollment. They
must prove an authorized flow, closed-gate denial, descriptor/interface loss and
denial of a NEW tuple after owner exit; unknown owned layouts/foreign rules stay
unchanged. BIOS/UEFI results remain synthetic remaster boot probes, not official
Debian Installer acceptance.

No security assertion was weakened. Some legacy assertions require immediate
existing-flow revocation beyond the accepted model, but remain unchanged because
the shipped helper still has independent NEW-flow P1s. Overall CI cannot be called
successful while those gates fail. Do not integrate or merge this experiment
until actual native clients/controller, lifecycle and platform acceptance pass
and an exact-head security review confirms the boundaries.

## Kernel readback ABI correction (mandatory, no fallback)

Linux nftables encodes `NFTA_CT_DIRECTION` as NLA_U8. google/nftables v0.3.0
incorrectly decodes it as uint32, making native original-tuple readback fail with
`attribute 3 is not a uint32; length: 1` AFTER successful kernel installation.
This was reproduced with the exact dependency and a real isolated kernel.
The separate dependency patch encodes/decodes the correct byte, rejects unknown
directions/malformed encodings and preserves the public Go field type.
Original AND reply address/port rules now roundtrip with unchanged UserData and
nonzero handles. Kernel confirmation is retained; no parse-error waiver exists.

Actual AMD64 kernel roundtrip and Go/race ABI tests pass locally. An attempted
ARM64 kernel roundtrip under QEMU-user FAILED: it does not support
NETLINK_NETFILTER sockets. ARM64 ABI byte tests run under QEMU-user; they are
NOT ARM kernel/client proof. Full ARM64 VM/hardware kernel acceptance remains
required and production release remains BLOCKED.

The real-client lab captures a protected canonical foreign-table reference
BEFORE starting the Engine. It checks that reference after first publication
and later changes, not merely against a post-start snapshot. Symlinks, writable
or missing references are refused; no automatic repair/removal is performed.


## Archived LAB.md

# Required native NetBird acceptance lab (NOT yet executed)

Current internal AMD64 CI scope and historical-versus-current test obligations:
[INTERNAL-TEST-SCOPE.md](INTERNAL-TEST-SCOPE.md). The historical broad lab
requirements below are not a claim of current platform support or approval.

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

## Executable Docker controller/client laboratory

See `docker-lab/RUNBOOK.txt` and `docker-lab/lab.py`. The version-pinned real
combined controller, verified lab TLS, initial owner/PAT, synthetic one-off keys
and actual DROP/port API contract have now run successfully in isolated Docker.
The preferred small-patch REAL client Dockerfile and two overlapping LANs with
two primary/backup pairs are provided. No callbacks or small-lab driver substitute
for that client. Full client/Engine packet acceptance is **not yet demonstrated**:
the local Go build is blocked by dependency-download policy, and required
adversarial fault injectors remain incomplete. The full command fails for a
missing real image or an incomplete gate; controller-only success never grants
release. New CI stages run both paths while keeping every earlier P1 gate.
