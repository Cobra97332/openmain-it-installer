# Small native BINAT candidate — LAB ONLY, RELEASE BLOCKED

Apply `netbird-v0.80.0-small-native.patch` to the pinned upstream NetBird v0.80.0
source described in ARCHITECTURE.md. Apply ONE patch: the earlier
`policy-barrier.patch` remains a research reference with its tests unchanged.
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
A scoped native POSTROUTING filter before SNAT delegates original and reply
traffic directly to the native routing ACL chain, then drops unmatched traffic.
The native ESTABLISHED shortcut excludes the configured virtual prefixes. Native
INPUT rules, foreign tables and unconfigured clients retain their usual behavior.

A small separate scope-bound readiness gate starts CLOSED, closes before native
changes and opens only after complete-map validation, native success and local
kernel readback. An in-memory epoch rejects an open racing with reset/closure.
An observed policy-only digest/serial refuses older or contradictory maps in the
same owner lifetime, including after rejected maps. There is no cached permission
or serial receipt to restore across restart: a fresh full authenticated map must
succeed. Readiness is a LOCAL result, never controller confirmation.

The owned gate layout must match its closed/open kernel reference exactly;
unknown layouts are refused unchanged. These bounded local nft invocations run
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
mode switch or migration is performed. The source paths are
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
