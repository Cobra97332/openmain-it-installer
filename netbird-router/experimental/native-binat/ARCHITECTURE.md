# Native BINAT: NetBird owns authorization — RELEASE BLOCKED

Selected client: `netbird-v0.80.0-small-native.patch`, AMD64 / Debian 13.
NetBird v0.80.0 upstream commit:
`fca64287cf51a85552a41b022013abbfdb335452`.
This is an isolated experiment, excluded from the installer bundle.

## One authority, two responsibilities

| Component | Responsibility |
|---|---|
| Existing NetBird controller and ACL manager | Identities, groups, policies, ACCEPT/DROP, protocols and ports |
| BINAT mapping | Transparent, port-preserving IPv4 1:1 prefix translation; virtual prefixes must be disjoint, real LANs may overlap |
| Small native compatibility changes | Make existing NetBird ACLs see the pre-translation destination; keep translated forwarding closed when native publication is unknown or fails |

There is no BINAT user database, group-to-address permission table, copied ACL
set, second policy compiler, external reconciliation process or periodic timer.
Primary/backup metrics 100/200 select routes; they grant no permission.

## Why stock NetBird plus translation is insufficient

For example, virtual A `198.18.10.100` and B `198.18.20.100` both translate to
`192.168.88.100` on their isolated LANs. PREROUTING DNAT runs before FORWARD.
Stock `buildRouteFilterExprs`/`prefixMatchExprs` read the **current IP header**:
a native A-only virtual destination rule no longer matches either packet.
Replacing that destination with the real LAN would lose the A/B distinction.
A broad real-LAN allowance must not become permission for both virtual networks.
Routing alone is not an authorization check.

Source: [filter_linux.go, route matches](https://github.com/netbirdio/netbird/blob/fca64287cf51a85552a41b022013abbfdb335452/client/firewall/nftables/filter_linux.go#L156)
and [prefix loads](https://github.com/netbirdio/netbird/blob/fca64287cf51a85552a41b022013abbfdb335452/client/firewall/nftables/filter_linux.go#L444).

Native route updates add new rules and subsequently remove obsolete ones.
Apply/Flush failures are logged and leave retained rules for retry. They do not
close BINAT automatically. A failed removal can therefore retain a permission
for **new** tuples. This is distinct from the accepted short continuation of
already established connections.
Source: [ACL lifecycle](https://github.com/netbirdio/netbird/blob/fca64287cf51a85552a41b022013abbfdb335452/client/internal/acl/manager.go#L99).

Stock ESTABLISHED/RELATED acceptance also bypasses reevaluation of existing
flows. That limit is documented; immediate distributed revocation is not a
requirement of this internal model. It is not a reason to copy native ACLs.

## Smallest necessary correction

1. Change native routed IPv4 selectors to conntrack ORIGINAL tuples, preserving
   NetBird's RuleIDs, source sets, order, actions and protocol/port semantics.
   Apply this consistently to route ACLs so a real-LAN rule cannot accidentally
   authorize an independently addressed virtual destination after DNAT.
2. Keep the native ACL manager as the sole rule writer. The scoped native
   forwarding check delegates to **that same chain**, not a duplicate rule set.
3. Close the translated scope before native changes; reopen only after a valid
   complete map, successful native apply and confirmed local kernel publication.
   A reset/error/disconnect must not reopen an older authorization state.
4. Preserve administrator firewall constraints. Do not insert broad external
   ACCEPTs, trust the interface in firewalld, or adopt stale passthrough rules.

The existing readiness gate is a lifecycle interlock. Its rules contain the
mapping scopes and CLOSED/READY state, **no peer permissions**; READY does not
accept a packet in place of the native ACL. Removing it while native updates
remain incremental would remove the error barrier, not simplify authorization.
Epoch/serial checks prevent an older completion reopening after closure. Kernel
readback is a local confirmation, never an acknowledgement from the controller.

The candidate also evaluates original/reply flows through native ACLs and
requires a fresh process-owned nonpersistent TUN. These existing protections
remain; no weaker replacement has been proved. Userspace WireGuard is used to
avoid a live forwarding device surviving policy-owner death. No extra supervisor
or recovery service is introduced.

## Removed from the selected design

The earlier full-snapshot publisher, authorization receipts and reference Python
compiler are **research only**, not a supported variant or build default.
Their files/tests remain as historical evidence; never combine their patch with
the selected small patch. See [RESEARCH-HISTORY.md](RESEARCH-HISTORY.md).

## Evidence and boundaries

Native compatibility tests and real controller/client packet cases exist, but
this is **not a completed security approval**. The shipping external helper
remains unsafe and is not replaced by this experiment. Two open candidate P1s
concern firewalld trust and stale foreign ACCEPTs after opt-in:
[firewalld](https://github.com/Cobra97332/openmain-it-installer/pull/34#discussion_r4261005972),
[stale rules](https://github.com/Cobra97332/openmain-it-installer/pull/34#discussion_r4261005987).
The smallest remediation is to suppress the trust call and refuse unverified
stale interface passthrough state before tunnel creation; never erase foreign
rules or introduce a second authorizer. This revision documents that requirement
and does not claim those P1s fixed.

Unsupported: ARM64 delivery, IPv6 BINAT, dynamic destinations, port remapping,
netstack/kernel-WireGuard adoption and automatic customer migration. Independent
privileged destruction of firewall/TUN state is outside the proof. Brief
established-flow continuation and controller delivery latency are accepted;
new-flow policy bypass and cross-customer access are not.

See [SMALL-NATIVE.md](SMALL-NATIVE.md), [LAB.md](LAB.md) and
[INTERNAL-TEST-SCOPE.md](INTERNAL-TEST-SCOPE.md). No production changes or merge.
