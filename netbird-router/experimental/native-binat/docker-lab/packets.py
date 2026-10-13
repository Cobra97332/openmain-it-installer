"""Real-client gates. Not executed by --controller-only. No mock policy writer."""
import json
import os
import subprocess
import time

# These are release blockers, not skipped/expected-failing tests. A full run
# returns nonzero until ALL gates have an actual supported fault-injection test.
REQUIRED = ('real_tcp_udp_binat', 'overlapping_customer_lans', 'unauthorized_new_tuples',
    'group_withdrawal_tcp_udp_existing_request', 'group_withdrawal_tcp_udp_new', 'restoration',
    'policy_change_after_last_observation', 'new_drop', 'foreign_rules_preserved',
    'userspace_tun_owner_death', 'primary_backup_failover_100_200', 'engine_restart_same_identity',
    'related_icmp', 'concurrent_policy_reset', 'unknown_stale_networkmap',
    'kernel_ack_failure', 'no_optin_orphan_restart', 'icmp_port_protocol_restrictions',
    'multiple_independent_acl_sources', 'fresh_map_after_native_firewall_rebuild',
    'established_reply_only_tcp_udp', 'controller_outage_fail_closed', 'controller_reconnect_same_identity')


def wait_closed(lab, routers, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        states = {r: observe(lab, r) for r in routers}
        if all(not value['open'] for value in states.values()):
            return states
        time.sleep(0.3)
    raise RuntimeError('lab_controller_loss_gate_remained_open')


def observe(lab, router):
    return json.loads(lab.exec(router, 'python3', '/opt/lab/traffic.py', 'inspect'))


def wait_publication(lab, previous, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        now = {r: observe(lab, r) for r in previous}
        if all(now[r]['open'] and now[r]['serial'] > previous[r]['serial'] for r in previous):
            return now
        time.sleep(0.3)
    raise RuntimeError('lab_fresh_local_publication_missing')


class Session:
    def __init__(self, lab, peer, destination, protocol):
        cid = lab.compose('ps', '-q', peer).stdout.strip()
        env = dict(os.environ)
        for k in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
            env.pop(k, None)
        self.proc = subprocess.Popen(['docker', '--host=unix:///var/run/docker.sock',
            'exec', '-i', cid, 'python3', '/opt/lab/traffic.py', 'session', destination, protocol],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)

    def exchange(self):
        import select
        self.proc.stdin.write('{}\n')
        self.proc.stdin.flush()
        if not select.select([self.proc.stdout], [], [], 5)[0]:
            raise RuntimeError('lab_packet_probe_timeout')
        return json.loads(self.proc.stdout.readline())['reply']

    def close(self):
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        self.proc.stdout.close()


def probe(lab, peer='peer', destination='198.18.10.100', allowed=True):
    for proto in ('tcp', 'udp'):
        session = Session(lab, peer, destination, proto)
        try:
            if session.exchange() != allowed:
                raise RuntimeError('lab_packet_permission_mismatch')
        finally:
            session.close()


def acceptance(lab):
    result = {name: 'blocked:mandatory_gate_not_implemented' for name in REQUIRED}
    routers = ('primary-a', 'backup-a', 'primary-b', 'backup-b')
    sessions = []
    try:
        initial = wait_publication(lab, {r: {'serial': -1} for r in routers})
        for dest in ('198.18.10.100', '198.18.20.100'):
            probe(lab, destination=dest)
            probe(lab, peer='probe', destination=dest, allowed=False)
        result.update(real_tcp_udp_binat='pass', overlapping_customer_lans='pass', unauthorized_new_tuples='pass')
        for proto in ('tcp', 'udp'):
            session = Session(lab, 'peer', '198.18.10.100', proto)
            sessions.append(session)
            if not session.exchange():
                raise RuntimeError('lab_established_connection_missing')
        # Mutation occurs AFTER our last observation; there is no polling ACL
        # copier or favorable writer timing in this test.
        lab.control('membership', allow=False)
        withdrawn = wait_publication(lab, initial)
        if any(s.exchange() for s in sessions):
            raise RuntimeError('lab_existing_permission_survived_local_withdrawal')
        result['group_withdrawal_tcp_udp_existing_request'] = 'pass'
        probe(lab, allowed=False)
        result['group_withdrawal_tcp_udp_new'] = 'pass'
        result['policy_change_after_last_observation'] = 'pass'
        lab.control('membership', allow=True)
        restored = wait_publication(lab, withdrawn)
        probe(lab)
        result['restoration'] = 'pass'
        # Keep the lab-only controller database across stop/start. Losing tmpfs
        # identities would test re-enrollment, not reconnect of the same peers.
        lab.compose('stop', '-t', '2', 'controller')
        wait_closed(lab, routers)
        probe(lab, allowed=False)
        if any(s.exchange() for s in sessions):
            raise RuntimeError('lab_controller_loss_existing_flow_allowed')
        result['controller_outage_fail_closed'] = 'pass'
        lab.compose('start', 'controller')
        deadline = time.monotonic() + 90
        while True:
            try:
                lab.control('same_identity')
                if all(observe(lab, r)['open'] for r in routers):
                    break
            except RuntimeError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError('lab_controller_reconnect_missing')
            time.sleep(0.3)
        probe(lab)
        result['controller_reconnect_same_identity'] = 'pass'
        restored = {r: observe(lab, r) for r in routers}
        lab.control('drop')
        wait_publication(lab, {r: restored[r] for r in ('primary-a', 'backup-a')})
        probe(lab, allowed=False)
        if any(s.exchange() for s in sessions):
            raise RuntimeError('lab_existing_permission_survived_drop')
        result['new_drop'] = 'pass'
        if all(observe(lab, r)['foreign_digest'] == initial[r]['foreign_digest'] for r in routers):
            result['foreign_rules_preserved'] = 'pass'
        else:
            raise RuntimeError('lab_foreign_rules_changed')
        # B is unaffected by the explicit A DROP. Verify actual HA path using
        # per-router NAT packet counters, not a manually chosen packet interface.
        before = observe(lab, 'primary-b')['dnat_packets']
        probe(lab, destination='198.18.20.100')
        if observe(lab, 'primary-b')['dnat_packets'] <= before:
            raise RuntimeError('lab_primary_not_selected')
        lab.exec('primary-b', 'python3', '/opt/lab/traffic.py', 'kill')
        start = time.monotonic()
        backup_before = observe(lab, 'backup-b')['dnat_packets']
        while True:
            try:
                probe(lab, destination='198.18.20.100')
                if observe(lab, 'backup-b')['dnat_packets'] > backup_before:
                    break
            except RuntimeError:
                pass
            if time.monotonic() - start > 90:
                raise RuntimeError('lab_automatic_backup_failover_missing')
            time.sleep(0.3)
        # No precise zero loss claim: wall time includes both TCP/UDP probes.
        result['backup_observed_after_seconds'] = round(time.monotonic() - start, 3)
        # Return/re-enrollment/orphan and malformed-map injection are still
        # mandatory blockers; failover alone is not declared a full PASS.
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as e:
        result['real_client_gate_failure'] = type(e).__name__
        if isinstance(e, RuntimeError) and str(e).startswith('lab_'):
            result['real_client_gate_failure'] = str(e)
    finally:
        for s in sessions:
            s.close()
    return result
