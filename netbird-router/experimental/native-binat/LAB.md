# Isolated native BINAT lab — no production approval

Use the selected small patch described in ARCHITECTURE.md. The existing
controller manages all synthetic identities, groups and policies. No additional
OpenMain authorizer is run. Never use CT 110/111, customer profiles or tokens.

## Current commands

Build the disposable Debian test images before disconnecting test networks.

```sh
bash tests/check-router.sh
OPENMAIN_NATIVE_TEST_ARCHES=amd64 bash tests/check-native-binat-patch.sh small

OPENMAIN_ISOLATED_TEST=1 python3 \
  netbird-router/experimental/native-binat/docker-lab/lab.py \
  --report /path/to/new-protected-report.json
```

The full Docker run uses the actual patched v0.80.0 Engine and pinned controller,
internal Docker networks, synthetic certificates and generated disposable keys.
It includes bootstrap and the native DROP/port API contract before enrollment.
Contract verification never creates setup keys; the enrollment step owns their
single creation. Contract failure aborts before client startup. Only sanitized
fixed diagnostic classes/results may be published.

`--controller-only` remains a diagnostic for actual TLS/bootstrap/API/key
creation, not a successful client or packet acceptance test.

For QEMU use a new work directory and independently prepared Debian 13 root:

```sh
python3 tests/qemu_platforms.py --vm-only \
  --native-small /path/to/verified/small-native-lab \
  --native-readback /path/to/verified/ct-readback
```

Run only inside the script's explicitly isolated network-none Docker context.
Do not use `--unit-only` as security evidence. The VM executes ABI readback,
nonpersistent TUN SIGKILL/closure-failure packet tests and unchanged legacy P1
counterexamples. Compiled-driver tests are synthetic transport/policy fixtures;
they are not a second real-controller/Engine integration proof.

## Interpretation

Mandatory properties: syntax, translation, native policy enforcement, customer
separation and fail-closed. The CI is AMD64-only. Twelve missing real-client
cases may be consolidated when equivalent proofs exist, but are not presently
marked successful or waived. The legacy new-flow bypasses remain relevant
because the installer still ships that path. Its gates intentionally fail.

At starting head e5c002f577d4cd094c30f63fa84027e74366d0b7 both router CI jobs
failed; monitoring validation passed. Earlier actual-client runs passed 23
cases but left twelve blocked. Treat those as historical results, not proof
for a future commit. This revision also restores the API contract accidentally
omitted from the previous full-client orchestration.

Local Proxmox VM/LXC capability tests, actual Engine reset/restart, complete
failover/recovery and unresolved native firewall P1s remain acceptance work.
No production approval follows from a smaller CI. Historical procedures and
unselected snapshot research are retained in RESEARCH-HISTORY.md.
