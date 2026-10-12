# Experimental native BINAT policy publication — BLOCKED

Base: OpenMain f0d8738b982bdd68090ffd3c1f1cd821b03f982c (PR #33).
This directory is excluded from the installer bundle. Neither installer behavior
nor the unsafe existing helper is changed. **Do not deploy or merge PR #33.**

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

## Implemented reference, not a native adapter

snapshot.py implements the packet-policy compiler and single-owner publication
contract. Complete immutable local snapshots contain scoped IPv4 prefixes,
source ranges, protocol, original destination-port ranges and actions. DROP
precedes all overlapping ACCEPTs. Multiple live ACLs are retained, not mistaken
for separate generations. Virtual scopes must be disjoint; separate virtual
prefixes may map to the same real LAN. Primary/backup use the same contract;
routing metrics do not grant permission.

The persistent table nb_binat_snapshot_lab has a POSTROUTING filter hook before
SNAT. Both directions use ct original saddr/daddr/protocol/port. Thus an earlier
native ESTABLISHED ACCEPT cannot authorize a packet rejected here. No marks,
header rewrites, broad conntrack deletes or native/foreign rule edits are used.
The snapshot chain falls through to scoped DROP when empty. It cannot be deleted
while referenced; replacement is a single nft batch. This namespace-local lab
table is never adopted, removed or installed by the production installer.

Publication holds one owner mutex, empties the authorization chain BEFORE native
processing, validates the entire map, and publishes only after native success.
Local increasing tickets reject delayed attempts to restore older snapshots.
A management-server ACK is NOT implemented. The confirmation boundary in tests
is successful synchronous nft publication; packets already beyond that hook may
finish. Physical packet drainage or upstream controller delivery latency is not
claimed. Malformed snapshots/native failures leave the successfully closed path
empty. If closing itself fails, denial cannot be certified: fail and stop; do not
claim that kernel failure magically revoked the previously installed rules.

The pinned native patch adds an opt-in synchronous lifecycle seam to
DefaultManager.ApplyFiltering. It closes before hash shortcuts/native rule work,
commits only after successful native operations and closes on failures. The
normal constructor remains unchanged. **It supplies NO native publisher,
configuration switch, engine wiring or restart integration.** It is an upstream
API experiment, not a working patched NetBird client. Patch application is
checked separately; no compiled/native-daemon acceptance is claimed.

## Remaining release blockers / safety limits

1. P1: Existing shipped helper still races and permits established traffic.
   Existing blocking tests are unchanged and must remain mandatory.
2. P1: No native publisher connects authoritative NetworkMap processing to this
   reference. Synthetic map callbacks do not prove actual controller/client
   policy ordering, acknowledgements or dynamic resource/DNS set updates.
3. P1: Every native init/reset/close/error path must close the persistent boundary
   BEFORE overlay forwarding becomes possible. NetBird currently resets outside
   ApplyFiltering. The experimental seam alone cannot prove that guarantee.
4. Guard-table destruction by another privileged firewall owner is outside this
   reference. Required production design must define exclusive ownership and
   synchronous recovery/start ordering. Asynchronous repair is insufficient.
5. RELATED traffic is conservatively denied for BINAT; related ICMP errors need
   parent-tuple policy semantics and kernel tests. Echo ICMP is tested. IPv6,
   DNAT port changes, dynamic destinations and service resources are rejected/
   unsupported; do not silently expand them into permissive selectors.
6. Kernel packet tests are actual UDP/TCP/ICMP and conntrack tests, but native
   policy callbacks, router path selection, controller and client are synthetic.
   Separate backup path is not a real metric-100/200 failover/reboot test.
7. Native Go patch build, actual unprivileged Proxmox LXC, physical Pi and
   isolated real NetBird controller/client acceptance remain required.

Do not integrate this experiment into installer/runtime.py, apply-binat.sh,
binat_acl.py or bundle.files. Green reference tests do not clear PR #33's P1s.
