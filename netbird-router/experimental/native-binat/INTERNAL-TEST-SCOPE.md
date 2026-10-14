# Internal AMD64 test scope — no production approval

This is the current CI scope for PR #34's **small-native** candidate. It
supersedes earlier requirements to build both mutually exclusive publishers,
ARM64, or a synthetic netinstall ISO on every change. Historical research and
its tests remain available. No runtime security code, customer configuration,
installer bundle or legacy P1 assertion is removed by this simplification.

## Supported candidate

Debian 13 AMD64, initially in isolated Docker/QEMU. Proxmox AMD64 VM deployment
still needs a local platform acceptance test. Unprivileged LXC needs its own
TUN/capability test. **ARM64/Raspberry Pi is not a supported delivery target for
this experimental candidate.** Existing Raspberry Pi installer code is retained;
this is not a withdrawal or migration of customer installations.

A small private network and lack of public ingress do not remove the need for
NetBird policy enforcement and customer separation.

## Required evidence and CI reductions

| Requirement | Mandatory execution |
|---|---|
| Syntax / regression / protected state | One full Bash/ShellCheck/Python run in Debian 13; separate jq-absent bootstrap |
| BINAT / policy / customer separation | Actual v0.80.0 Engine and controller, overlapping LANs, allowed and excluded peers, TCP/UDP, native DROP, membership withdrawal and restoration |
| Unknown state / fail-closed | Native Go/race validation, failed readback/publication tests, controller disconnect/reconnect, real nftables packets |
| Kernel ABI / process lifetime | AMD64 kernel conntrack readback plus Debian 13 QEMU nonpersistent-TUN death and closure-failure tests |
| Remaining shipping legacy risks | Unchanged six P1 kernel counterexamples, in Docker and QEMU; failures still fail CI |

Removed duplicate host unit execution: the same suite runs once under the actual
supported Debian release. The cheap offline suite remains whole to preserve
installer compatibility rather than maintain a fragile list of unit tests.
Controller TLS/bootstrap/API validation already occurs within the full real
client run; it is no longer run in a second, controller-only deployment.
QEMU consumes the exact public probes built by the native test job rather than
fetching/building NetBird again. No identity or credential artifact is shared.
The QEMU security job runs even when the preceding regression job fails.

Pull requests trigger one router workflow rather than both push and PR runs.
Direct pushes to main/beta still trigger validation; this change does not push to
or modify either branch. Superseded runs are cancelled. Unrelated monitoring
workflows are unchanged.

The unselected full-snapshot publisher and Python snapshot kernel experiment
are no longer required CI jobs. Neither is installed or used by the selected
client. Their source, regressions and known security findings remain intact;
they must not be deployed based on this candidate's results. Manual research:
`bash tests/check-native-binat-patch.sh snapshot` and `tests/nft_native_snapshot_kernel.py`.

ARM64 build/emulation and synthetic ISO BIOS/UEFI boots are outside this support
scope. The scripts remain available manually. `tests/qemu_platforms.py --vm-only`
still runs every VM security gate, the whole offline suite and firstboot service
lifecycle; it does not use the reduced `--unit-only` debug mode. It does not claim
real NetBird clients inside QEMU or actual Proxmox hardware certification.

## Twelve outstanding additional cases

Separate end-to-end cases are not intrinsically necessary. However equivalent
coverage must be identified before a missing assertion is removed. The current
real-client acceptance list remains unchanged: unimplemented cases still block,
not skip, its exit status. Current evidence and gaps:

| Case | Existing evidence / remaining gap |
|---|---|
| userspace_tun_owner_death | Actual QEMU process-owned TUN teardown; real-client kill observed, a standalone real-client gate still missing |
| primary_backup_failover_100_200 | Backup path observed after primary kill; return to primary and measured full scenario missing |
| engine_restart_same_identity | Controller reconnect preserves identity; actual Engine restart not equivalent |
| related_icmp | Synthetic native kernel coverage; real Engine/controller packet evidence missing |
| concurrent_policy_reset | Go epoch/race and kernel scenarios; actual Engine reset concurrency missing |
| unknown_stale_networkmap | Native map validation; actual controller injection missing |
| kernel_ack_failure | Native injected failure tests and real readback; actual daemon fault injection missing |
| no_optin_orphan_restart | Actual QEMU orphan refusal; actual CLI restart case missing |
| icmp_port_protocol_restrictions | Native selector/kernel tests; actual client mixed-protocol/port case missing |
| multiple_independent_acl_sources | Native unit/kernel rules; actual multiple-controller-source case missing |
| fresh_map_after_native_firewall_rebuild | Native lifecycle validation; actual daemon rebuild case missing |
| established_reply_only_tcp_udp | Kernel reply tests; real-client unsolicited reply-only case missing |

Some of these can later be consolidated into the existing packet runs. This PR
revision does not silently replace missing daemon proofs with mock success.
Immediate termination of every established connection is a stronger guarantee
than the accepted internal model. That concession does **not** justify any stale
permission for new connections or cross-customer access. Existing strict tests
are retained until their narrower replacement proves those properties.

## Relevant failures and risks (baseline a947a85)

* The external helper still ships through the installer. Its six legacy P1
  failures include **new-flow** stale grants / native DROP bypass, not just
  accepted established-flow delay. They remain relevant and blocking. The native
  experiment is excluded from the installer bundle and does not repair it.
* The synthetic persistent-veth owner-death test is outside the candidate's
  required nonpersistent-TUN mode. It nevertheless still fails visibly; no
  expected-failure waiver is added. QEMU TUN teardown is the relevant positive
  proof, not a claim that the standalone gate is safe on arbitrary interfaces.
* Current review also reports P1 firewalld trust and stale NetBird-owned foreign
  ACCEPT rules on opt-in: discussion_r4261005972 and discussion_r4261005987 in
  PR #34. They are not fixed by reducing platform coverage. Foreign rules must
  not be bypassed, even in a private deployment. P2 discussion_r4261005992
  concerns the positive-probe retry deadline. These remain open in this revision.
* NetBird controller API success is not a kernel acknowledgement. Full local
  publication is checked separately; no distributed instantaneous revocation
  or protection against an independent privileged firewall writer is claimed.

Baseline exact-head CI executed 248 offline regressions, seven no-jq tests and
23 real-client laboratory cases successfully; twelve missing cases blocked the
lab. Docker/QEMU security jobs failed. These are historical executed results,
not claims about this new commit. New run links/results are recorded in the PR.

**Production approval remains blocked.** Fewer duplicated tests do not resolve
policy or customer-isolation vulnerabilities. No production migration, stock
client rollback over old guards, or installer integration is authorized here.
