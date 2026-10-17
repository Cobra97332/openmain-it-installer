# Selected small native patch — AMD64, LAB ONLY

[ARCHITECTURE.md](ARCHITECTURE.md) defines the single-authority design.
Only NetBird decides permissions. BINAT configuration contains virtual/real
prefixes and interfaces, never users, groups or grants.

## Build

Apply `netbird-v0.80.0-small-native.patch` to clean NetBird v0.80.0, upstream
`fca64287cf51a85552a41b022013abbfdb335452`.
Archive SHA256: `ab453b7d015fd657c912c40986868784f77c3460f296dc07bc2c7f07f4bce7b7`.
The build helper additionally applies
`google-nftables-v0.3.0-ct-direction.patch` to the exact verified dependency.
Linux encodes NFTA_CT_DIRECTION as NLA_U8; the dependency incorrectly used
uint32. The fix preserves kernel readback instead of ignoring its error.

`Dockerfile.client` builds the actual CLI/Engine, not a policy callback fixture.
Native and dependency patch digests jointly identify the build. There is no
additional runtime library/service beyond the native process and nftables.
Neither patch is installed by the existing OpenMain installer.

## Runtime boundaries

* NetBird's existing route ACL manager owns the rules and their removal.
* Native route loads use ORIGINAL tuples; a scoped check calls the same native
  chain for original/reply traffic. No rules are copied into a parallel ACL.
* CLOSED/READY tracks native publication health, not who is allowed access.
  Complete validated maps and confirmed native kernel publication reopen it;
  failed updates, unknown selectors, reset and management disconnect close it.
* No cached grants are restored across restart. Local serial/epoch/digest checks
  prevent obsolete completions reopening a closed scope.
* Existing userspace WireGuard, fresh verified nonpersistent TUN and native
  nftables are required. Persistent interfaces and alternate backends are
  refused. Unconfirmed closure fail-stops the process; TUN lifetime is tested.
* Orphaned experimental state is refused without destructive cleanup. Do not
  roll back to an unpatched client over remaining experimental rules.

No external ACL polling, second publisher, durable policy receipt, user store or
new recovery daemon is needed. The earlier complete-map publisher is archived
research, not an alternative suggested for internal operation.

## Current limits

Mode-specific owner-death analysis, all six legacy counterexamples and the
eight concrete native acceptance cases are in OPERATING-MODE-ACCEPTANCE.txt.
Added executable cases count as proven only after actual-client execution;
remaining Engine fault/reset prerequisites stay blocking, not skipped.

New connections must obey locally applied NetBird policy. Brief continuation of
existing connections after withdrawal is accepted; the current implementation
has stronger original/reply checks, which are preserved. No distributed zero
latency or controller-to-kernel acknowledgement is claimed.

Native firewalld trust/untrust is suppressed. Startup refuses stale foreign
interface permissions before creating the overlay. Firewalld hosts and legacy
iptables filter state remain unsupported and fail closed. These corrections
require exact-head review and real-client acceptance; see P1-FIREWALL-TESTS.md.
The selected supported target is Debian 13 AMD64. Proxmox VM/LXC still require
local TUN/capability acceptance. ARM64/Pi is not an experimental delivery target.
Existing customers, kernel-WireGuard installations, identities and mappings are
unchanged. No automatic migration or installer integration is included.

See [INTERNAL-TEST-SCOPE.md](INTERNAL-TEST-SCOPE.md) for actual evidence and
unresolved cases. Passing synthetic nftables tests alone is not actual Engine
acceptance. Production approval remains blocked.
