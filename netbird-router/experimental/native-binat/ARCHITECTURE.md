# Experimental native BINAT policy publication — BLOCKED

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
