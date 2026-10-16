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
    'userspace_tun_owner_death', 'stale_accept_restart_refused', 'primary_backup_failover_100_200', 'engine_restart_same_identity',
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


def wait_router(lab, router, predicate, error, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = observe(lab, router)
        if predicate(state):
            return state
        time.sleep(0.3)
    raise RuntimeError(error)


def observe(lab, router):
    return json.loads(lab.exec(router, 'python3', '/opt/lab/traffic.py', 'inspect'))


def wait_publication(lab, previous, timeout=90):
    deadline = time.monotonic() + timeout
    now = {}
    while time.monotonic() < deadline:
        now = {r: observe(lab, r) for r in previous}
        if all(now[r]['open'] and now[r]['serial'] > previous[r]['serial'] for r in previous):
            return now
        time.sleep(0.3)
    error = RuntimeError('lab_fresh_local_publication_missing')
    error.publication_failure = {r: {'open': now.get(r, {}).get('open') is True,
        'newer': now.get(r, {}).get('serial', -1) > previous[r]['serial'],
        'tun_exists': now.get(r, {}).get('tun_exists') is True} for r in previous}
    raise error


class Session:
    def __init__(self, lab, peer, destination, protocol, deadline=None, port=None, mode='session'):
        self.deadline = deadline
        try:
            cid = lab.compose('ps', '-q', peer, timeout=self.remaining(600)).stdout.strip()
        except subprocess.TimeoutExpired:
            raise RuntimeError('lab_packet_probe_timeout') from None
        # The lookup itself may have consumed the remaining budget. Do not
        # start docker exec after expiry, even if lookup returned normally.
        self.remaining(5)
        env = dict(os.environ)
        for k in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
            env.pop(k, None)
        self.proc = subprocess.Popen(['docker', '--host=unix:///var/run/docker.sock',
            'exec', '-i', cid, 'python3', '/opt/lab/traffic.py', mode, destination, protocol,
            *([str(port)] if port is not None else [])],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)

    def remaining(self, limit):
        if self.deadline is None:
            return limit
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError('lab_packet_probe_timeout')
        return min(limit, remaining)

    def exchange(self, request=None):
        import select
        timeout = self.remaining(5)
        deadline = time.monotonic() + timeout
        self.proc.stdin.write(json.dumps(request or {}) + '\n')
        self.proc.stdin.flush()
        response = b''
        while b'\n' not in response:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.proc.stdout], [], [], remaining)[0]:
                raise RuntimeError('lab_packet_probe_timeout')
            chunk = os.read(self.proc.stdout.fileno(), 4096)
            if not chunk or len(response) + len(chunk) > 4096:
                raise RuntimeError('lab_packet_probe_incomplete')
            response += chunk
        if time.monotonic() >= deadline:
            raise RuntimeError('lab_packet_probe_timeout')
        try:
            value = json.loads(response)
            if type(value.get('reply')) is not bool:
                raise ValueError
        except (ValueError, AttributeError):
            raise RuntimeError('lab_packet_probe_incomplete') from None
        return value['reply']

    def close(self):
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        self.proc.stdout.close()


def probe(lab, peer='peer', destination='198.18.10.100', allowed=True, protocols=('tcp', 'udp'), port=None):
    if peer not in ('peer', 'probe') or destination not in ('198.18.10.100', '198.18.20.100'):
        raise RuntimeError('lab_packet_target_refused')
    for proto in protocols:
        # Kernel publication is not a WireGuard/ICE transport-ready ACK.
        # Only positive setup/restoration may wait for transport. A single
        # unauthorized reply fails immediately; never retry until denial.
        deadline = time.monotonic() + (25 if allowed else 0)
        while True:
            if allowed and time.monotonic() >= deadline:
                raise RuntimeError('lab_authorized_' + proto + '_reply_missing')
            session = None
            try:
                session = Session(lab, peer, destination, proto, deadline=deadline if allowed else None, port=port)
                reply = session.exchange()
            except RuntimeError as error:
                if not allowed or str(error) != 'lab_packet_probe_timeout':
                    raise
                reply = False
            finally:
                if session is not None:
                    session.close()
            # No new positive attempt (including Docker lookup or select) may
            # exceed the per-protocol budget. Reaping a helper is separate,
            # bounded cleanup, never an extra packet attempt.
            remaining = deadline - time.monotonic() if allowed else 0
            if allowed and remaining <= 0:
                raise RuntimeError('lab_authorized_' + proto + '_reply_missing')
            if reply == allowed:
                break
            if not allowed:
                raise RuntimeError('lab_unauthorized_' + proto + '_reply_observed')
            time.sleep(min(0.3, remaining))


def establish_sessions(lab, sessions):
    # Each withdrawal needs live flows immediately beforehand. Never reuse a
    # TCP socket broken by an earlier successful denial as revocation evidence.
    for session in sessions:
        session.close()
    sessions.clear()
    for proto in ('tcp', 'udp'):
        session = Session(lab, 'peer', '198.18.10.100', proto)
        sessions.append(session)
        if not session.exchange():
            raise RuntimeError('lab_established_connection_missing')


def ping(lab, allowed):
    deadline = time.monotonic() + 25
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError('lab_icmp_permission_mismatch')
        reply = json.loads(lab.exec('peer', 'python3', '/opt/lab/traffic.py', 'ping', '198.18.10.100',
                                   timeout=remaining))['reply']
        if time.monotonic() >= deadline:
            raise RuntimeError('lab_icmp_permission_mismatch')
        if reply == allowed:
            return
        if not allowed or time.monotonic() >= deadline:
            raise RuntimeError('lab_icmp_permission_mismatch')
        time.sleep(min(0.3, max(0, deadline - time.monotonic())))


def extended_policy_cases(lab, result):
    # Each control call mutates ONE policy only. Affected routers acknowledge the
    # intervening map before the next write, so a prior queued map cannot stand
    # in for the next operation's publication barrier.
    # Unaffected peers need not receive a new Sync/publication for this edit.
    b_routers = ('primary-b', 'backup-b')
    previous = {r: observe(lab, r) for r in b_routers}
    lab.control('split_sources')
    wait_publication(lab, previous)
    # A-only and B-only clients; same real LAN, different virtual destinations.
    probe(lab)
    probe(lab, destination='198.18.20.100', allowed=False)
    probe(lab, peer='probe', allowed=False)
    probe(lab, peer='probe', destination='198.18.20.100')
    previous = {r: observe(lab, r) for r in b_routers}
    lab.control('restore_b')
    wait_publication(lab, previous)
    probe(lab, destination='198.18.20.100')
    probe(lab, peer='probe', destination='198.18.20.100', allowed=False)
    result['multiple_independent_acl_sources'] = 'pass'

    # Prove the alternate services and ICMP work before testing their denial.
    for proto, port in (('tcp', 8082), ('udp', 8083), ('tcp', 8081), ('udp', 8080)):
        probe(lab, protocols=(proto,), port=port)
    ping(lab, True)
    a_routers = ('primary-a', 'backup-a')
    for operation in ('extra_udp', 'extra_icmp'):
        previous = {r: observe(lab, r) for r in a_routers}
        lab.control(operation)
        wait_publication(lab, previous)
    previous = {r: observe(lab, r) for r in a_routers}
    lab.control('restrict_a')
    wait_publication(lab, previous)
    probe(lab)
    for proto, port in (('tcp', 8082), ('udp', 8083), ('tcp', 8081), ('udp', 8080)):
        probe(lab, protocols=(proto,), port=port, allowed=False)
    ping(lab, True)
    previous = {r: observe(lab, r) for r in a_routers}
    lab.control('remove_icmp')
    wait_publication(lab, previous)
    ping(lab, False)
    probe(lab)
    previous = {r: observe(lab, r) for r in a_routers}
    lab.control('restore_a')
    wait_publication(lab, previous)
    ping(lab, True)
    for proto, port in (('tcp', 8082), ('udp', 8083), ('tcp', 8081), ('udp', 8080)):
        probe(lab, protocols=(proto,), port=port)
    previous = {r: observe(lab, r) for r in a_routers}
    lab.control('remove_udp')
    wait_publication(lab, previous)
    result['icmp_port_protocol_restrictions'] = 'pass'


def related_error(lab, session, allowed):
    routers = ('primary-a', 'backup-a')
    before = sum(observe(lab, r)['related_packets'] for r in routers)
    lab.exec('lan-a', 'python3', '/opt/lab/related.py', 'emit')
    if session.exchange() != allowed:
        raise RuntimeError('lab_related_delivery_mismatch')
    after = sum(observe(lab, r)['related_packets'] for r in routers)
    if after <= before:
        raise RuntimeError('lab_related_classification_missing')


def prove_passive_sender(lab, barrier):
    emitted = json.loads(lab.exec('lan-a', 'python3', '/opt/lab/traffic.py', 'passive-proof'))
    if any(type(emitted.get(p)) is not int or emitted[p] <= barrier for p in ('tcp', 'udp')):
        error = RuntimeError('lab_passive_sender_unproven')
        error.sender_failure = {p: type(emitted.get(p)) is int and emitted[p] > barrier for p in ('tcp', 'udp')}
        raise error


def acceptance(lab):
    result = {name: 'blocked:mandatory_gate_not_implemented' for name in REQUIRED}
    result.update(concurrent_policy_reset='blocked:engine_reset_barrier_injector_missing',
                  unknown_stale_networkmap='blocked:management_map_injector_missing',
                  kernel_ack_failure='blocked:daemon_netlink_fault_injector_missing',
                  fresh_map_after_native_firewall_rebuild='blocked:live_engine_reset_fixture_missing')
    routers = ('primary-a', 'backup-a', 'primary-b', 'backup-b')
    sessions = []
    passive = []
    related = None
    try:
        initial = wait_publication(lab, {r: {'serial': -1} for r in routers})
        if not all(s['foreign_matches_initial'] for s in initial.values()):
            raise RuntimeError('lab_foreign_rules_changed')
        for dest in ('198.18.10.100', '198.18.20.100'):
            probe(lab, destination=dest)
            probe(lab, peer='probe', destination=dest, allowed=False)
        result.update(real_tcp_udp_binat='pass', overlapping_customer_lans='pass', unauthorized_new_tuples='pass')
        extended_policy_cases(lab, result)
        establish_sessions(lab, sessions)
        for proto in ('tcp', 'udp'):
            session = Session(lab, 'peer', '198.18.10.100', proto, mode='passive')
            passive.append(session)
            if not session.exchange():
                raise RuntimeError('lab_passive_baseline_missing')
        lab.exec('lan-a', 'python3', '/opt/lab/traffic.py', 'passive-pause')
        related = Session(lab, 'peer', '198.18.10.100', 'udp', mode='related')
        deadline = time.monotonic() + 15
        while True:
            try:
                lab.exec('lan-a', 'python3', '/opt/lab/related.py', 'ready')
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('lab_related_parent_missing') from None
                time.sleep(0.1)
        related_error(lab, related, True)
        initial = {r: observe(lab, r) for r in routers}
        # Mutation occurs AFTER our last observation; there is no polling ACL
        # copier or favorable writer timing in this test.
        lab.control('membership', allow=False)
        withdrawn = wait_publication(lab, initial)
        barrier = time.monotonic_ns()
        lab.exec('lan-a', 'python3', '/opt/lab/traffic.py', 'passive-resume')
        related_error(lab, related, False)
        result['related_icmp'] = 'pass'
        if any(s.exchange({'after_ns': barrier}) for s in passive):
            raise RuntimeError('lab_reply_only_withdrawal_failed')
        prove_passive_sender(lab, barrier)
        result['established_reply_only_tcp_udp'] = 'pass'
        if any(s.exchange() for s in sessions):
            raise RuntimeError('lab_existing_permission_survived_local_withdrawal')
        result['group_withdrawal_tcp_udp_existing_request'] = 'pass'
        probe(lab, allowed=False)
        result['group_withdrawal_tcp_udp_new'] = 'pass'
        result['policy_change_after_last_observation'] = 'pass'
        lab.control('membership', allow=True)
        restored = wait_publication(lab, withdrawn)
        probe(lab)
        establish_sessions(lab, sessions)
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
        establish_sessions(lab, sessions)
        result['controller_reconnect_same_identity'] = 'pass'
        restored = {r: observe(lab, r) for r in routers}
        lab.control('drop')
        wait_publication(lab, {r: restored[r] for r in ('primary-a', 'backup-a')})
        probe(lab, allowed=False)
        if any(s.exchange() for s in sessions):
            raise RuntimeError('lab_existing_permission_survived_drop')
        result['new_drop'] = 'pass'
        if all(observe(lab, r)['foreign_matches_initial'] for r in routers):
            result['foreign_rules_preserved'] = 'pass'
        else:
            raise RuntimeError('lab_foreign_rules_changed')
        # B is unaffected by the explicit A DROP. Verify actual HA path using
        # per-router NAT packet counters, not a manually chosen packet interface.
        before = observe(lab, 'primary-b')['dnat_packets']
        probe(lab, destination='198.18.20.100')
        if observe(lab, 'primary-b')['dnat_packets'] <= before:
            raise RuntimeError('lab_primary_not_selected')
        prior = observe(lab, 'primary-b')
        if not prior['tun_exists']:
            raise RuntimeError('lab_tun_survived_owner_death')
        lab.exec('primary-b', 'python3', '/opt/lab/traffic.py', 'kill')
        dead = wait_router(lab, 'primary-b', lambda s: not s['tun_exists'], 'lab_tun_survived_owner_death')
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
        probe(lab, peer='probe', destination='198.18.20.100', allowed=False)
        result['userspace_tun_owner_death'] = 'pass'
        # Actual same-identity CLI restart WITHOUT BINAT opt-in must refuse the
        # orphan before recreating wt0; never reuse a setup key or profile.
        lab.resume_router('primary-b', opt_in=False)
        refused = wait_router(lab, 'primary-b', lambda s: s['orphan_refusals'] > dead['orphan_refusals']
                              and not s['tun_exists'], 'lab_orphan_resume_not_refused')
        lab.exec('primary-b', 'python3', '/opt/lab/traffic.py', 'kill')
        result['no_optin_orphan_restart'] = 'pass'
        # A real stale untagged foreign ACCEPT must also prevent a configured
        # restart. This fixture alone is removed after the negative assertion.
        lab.exec('primary-b', 'python3', '/opt/lab/traffic.py', 'stale-seed')
        lab.resume_router('primary-b')
        wait_router(lab, 'primary-b', lambda s: s['stale_refusals'] > refused['stale_refusals']
                    and not s['tun_exists'] and not s['open'], 'lab_stale_resume_not_refused')
        lab.exec('primary-b', 'python3', '/opt/lab/traffic.py', 'kill')
        result['stale_accept_restart_refused'] = 'pass'
        lab.exec('primary-b', 'python3', '/opt/lab/traffic.py', 'stale-clear')
        lab.resume_router('primary-b')
        current = wait_router(lab, 'primary-b', lambda s: s['tun_exists'] and s['open']
                              and s['publication_count'] > prior['publication_count'],
                              'lab_restart_publication_missing', timeout=90)
        lab.control('same_identity')
        if not current['foreign_matches_initial']:
            raise RuntimeError('lab_foreign_rules_changed')
        before_return = current['dnat_packets']
        start_return = time.monotonic()
        while True:
            probe(lab, destination='198.18.20.100')
            if observe(lab, 'primary-b')['dnat_packets'] > before_return:
                break
            if time.monotonic() - start_return > 90:
                raise RuntimeError('lab_primary_return_missing')
            time.sleep(0.3)
        probe(lab, peer='probe', destination='198.18.20.100', allowed=False)
        result['primary_return_observed_after_seconds'] = round(time.monotonic() - start_return, 3)
        result['engine_restart_same_identity'] = 'pass'
        result['primary_backup_failover_100_200'] = 'pass'
        # The A DROP must still hold after independent B failover/restart.
        probe(lab, destination='198.18.10.100', allowed=False)
        if not all(observe(lab, r)['foreign_matches_initial'] for r in routers):
            raise RuntimeError('lab_foreign_rules_changed')
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as e:
        if hasattr(e, 'publication_failure'):
            result['publication_failure'] = e.publication_failure
        if hasattr(e, 'sender_failure'):
            result['sender_failure'] = e.sender_failure
        result['real_client_gate_failure'] = type(e).__name__
        if isinstance(e, RuntimeError) and str(e).startswith('lab_'):
            result['real_client_gate_failure'] = str(e)
    finally:
        for s in sessions + passive + ([related] if related is not None else []):
            s.close()
    return result
