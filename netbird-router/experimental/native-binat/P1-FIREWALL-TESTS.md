# Native firewall P1 corrections — acceptance remains blocked

Starting head: 465c9560814fb63624f3084cfd597da36394362b.
Only the experimental small-native patch, lab and tests change. No production
installer, PR #33, customer identity, mapping, or metric is migrated.

## Native changes

* Suppress both firewalld TrustInterface and UntrustInterface while the BINAT
  owner is active. Ordinary unconfigured NetBird behavior is unchanged.
* Before newWgIface, with the readiness gate already CLOSED, inspect the current
  namespace's foreign nft rules for target IIFNAME/OIFNAME passthroughs. Refuse
  ACCEPT, jump/goto and unresolved interface-set/map paths. Untagged
  iptables-nft and tagged external NetBird rules are both covered. Interface
  prefix encodings are conservatively matched. No rule is deleted or adopted.
* Keep unrelated-interface rules and explicit administrator DROP rules intact.
  Inspection errors refuse startup. Only the default owned native IPv4 work
  table and verified private gate are exempt, since the Engine rebuilds them.
* Stored firewalld zone state is not certified by an nft snapshot. Therefore
  firewalld binaries or an active firewalld table explicitly refuse BINAT for
  now. No trust/untrust command runs. Legacy iptables filter tables and custom
  native table names are also refused rather than falsely claimed inspected.
  These are availability/support restrictions, not firewall disablement.

NetBird remains the sole ACL compiler/writer. Mapping and readiness contain no
peer grants. Existing native ORIGINAL source/destination/protocol/port checks for
request/reply, native DROP ordering and epoch/complete-map checks are retained.
Failed/stale/partial native updates cannot reopen the readiness gate. Metrics
100/200 and the same virtual-to-real translations are unchanged.

This does not defend against an independent privileged writer changing foreign
rules after preflight, persisting the TUN, or deleting native protections.
Concurrent policy updates remain owned by the native Engine/ACL lifecycle;
there is no external policy copier or periodic authorizer.

## Executed locally before commit

* Native BINAT core Go/race tests, including stale accept/prefix/set handling and
  a synthetic installed firewall-cmd which must never execute: PASS.
* Real isolated nft kernel refusal tests: INPUT/FORWARD, original and
  ESTABLISHED/RELATED reply accepts, tags, interface prefix and named set,
  firewalld table, unrelated-interface accept and administrator drop: PASS.
  Every inspection compares unchanged rules and a still-CLOSED ready chain.
* Existing real kernel TCP/UDP original/reply withdrawal, established/new flows,
  late DROP, restart closed, multiple sources and overlapping LANs: PASS.
  These use the compiled Go gate with synthetic native rules, not real clients.
* Patch applies cleanly to the pinned upstream archive. Full backend/Engine
  compilation locally is blocked by unavailable cached public Go dependencies;
  GitHub CI must compile both and build the actual client. No build success is
  inferred from core tests.
* Debian 13 complete offline/ShellCheck, no-jq and QEMU runs are recorded in the
  PR with final execution status. Never infer QEMU success from a driver test.

## Actual-client gates added, not predeclared passed

The existing controller/client lab now requires: verify actual wt0 disappearance
on SIGKILL; show excluded peers remain denied after automatic backup selection;
restart the same identity without opt-in and require orphan refusal; inject a
lab-owned stale ACCEPT and require configured restart refusal before TUN creation;
remove only that synthetic fixture; restart without a setup key; require a NEW
local publication event (not just a previous serial in an append-only log), same
peer IDs, automatic return to the primary and unchanged independent A DROP.
Foreign firewall references must still match. New and old mandatory gates remain
nonzero on missing proof. Their assertions are not replaced with mocks.

## Twelve previous missing cases

Four have new actual-client gate implementations: owner death, no-opt-in orphan
restart, same-identity Engine restart, and full primary/backup/primary path.
They count as proved only after actual-client CI executes them successfully.
The other eight remain blockers: RELATED-ICMP, concurrent policy/reset,
unknown/stale map injection, kernel-ACK failure in the actual daemon,
ICMP/port restrictions, independent ACL sources, fresh map after native firewall
rebuild, and established reply-only traffic. Existing synthetic/core coverage is
not relabeled as completed real-client evidence.

The six shipping legacy P1s and persistent-veth owner-death counterexample remain
unchanged and failing. The candidate refuses that persistent dataplane; real
nonpersistent TUN lifetime is a separate proof. Old-path failures are not waived
or used as evidence that these native corrections failed. No production or merge
approval follows, even if the two targeted native P1 paths pass review.

## Follow-up after first exact-head CI/review

The review of fe0a183 identified an inverted-interface bypass
(discussion_r4292357755). The refusal detector now rejects non-equality
interface comparisons conservatively, with Go and actual nft kernel cases for
`iifname != "lo" accept` and the ESTABLISHED/RELATED reply equivalent.
It distinguishes verdict lookups from pure data lookups, so the legitimate
IP-prefix DNAT map is not mistaken for an interface permission set. Both a positive real prefix-DNAT kernel case and negative
interface-set cases are required. No permission rules are generated by this scan.

The same CI built the actual patched CLI/Engine successfully but failed during
client configure; it did not prove any new actual-client packet cases. Additional
finite boolean preflight diagnostics identify refusals without publishing logs.

QEMU also exposed a missing test input introduced by the earlier contract
regression: its guest source tree omitted the two public patch digest files.
They are now copied as test fixtures (not installed into the runtime bundle).
The repeated actual Debian 13 QEMU VM passed all 252 regressions, native kernel
ABI readback and BOTH TUN SIGKILL/unconfirmed-closure fail-stop checks, then
failed the unchanged six shipping legacy P1 gates. This is not a VM-wide pass.

## Second actual-client CI and verdict-map review

CI for 5358aa508d7ee673183e2d78519d945b7fec4de4 built the actual CLI/Engine
and executed **29 passing cases, eight still blocked**, with no packet error.
This exercises all four new lifecycle/failover cases and stale-ACCEPT refusal.
It does not certify the eight missing daemon injection cases or solve legacy P1s.

The same-head review found discussion_r4292497403: a verdict map after register
reuse can grant without an inline ACCEPT. The final detector identifies verdict
lookups before examining direct interface constraints. It does not track/evaluate
an arbitrary foreign policy. Only direct exact, null-terminated equality is an
understood interface constraint for a potentially permitting rule; inverted,
aliased/indirect, prefix and interface-set variants are conservatively refused.
RETURN is also treated as potentially permitting because it can skip a later
DROP in a base chain. Pure DNAT data maps remain accepted. Actual kernel tests
now include the reviewed inverted-interface + IP verdict-map combination and
RETURN in addition to earlier cases; native Go/race and these tests pass locally.

Exact-final-head CI and security review must confirm this follow-up. Safe CI
summaries now include five fixed lifecycle case booleans, never raw API/log fields,
so the new actual proofs can be audited without downloading private artifacts.
