"""Mandatory actual Engine/map/netlink fault gates. Never a policy publisher."""
import json
import time

from packets import Session, observe, probe, wait_closed, wait_publication

A = ('primary-a', 'backup-a')


def inject(lab, operation):
    return {r: json.loads(lab.exec(r, 'python3', '/opt/lab/inject.py', operation)) for r in A}


def wait_inject(lab, predicate, timeout=40, after=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            states = inject(lab, 'status')
        except RuntimeError:
            # The real Engine restart closes/rebinds its test socket. A lost
            # observation is never a pass; require the new generation below.
            time.sleep(0.2)
            continue
        if all(predicate(s) and (after is None or s['rejected'] > after[r]['rejected'])
               for r, s in states.items()):
            return states
        time.sleep(0.2)
    raise RuntimeError('lab_injector_barrier_missing')


def baseline(lab):
    probe(lab)
    probe(lab, destination='198.18.20.100')
    sessions = [Session(lab, 'peer', '198.18.10.100', p) for p in ('tcp', 'udp')]
    try:
        if not all(s.exchange() for s in sessions):
            raise RuntimeError('lab_fault_baseline_missing')
    except BaseException:
        for s in sessions:
            s.close()
        raise
    return sessions, {r: observe(lab, r) for r in A}


def closed_packets(lab, sessions, before):
    wait_closed(lab, A)
    for s in sessions:
        if s.exchange():
            raise RuntimeError('lab_fault_existing_flow_allowed')
    probe(lab, allowed=False)
    # Other customer route/ACL and excluded identity must stay independent.
    probe(lab, destination='198.18.20.100')
    probe(lab, peer='probe', allowed=False)
    for r in A:
        now = observe(lab, r)
        if now['open'] or now['publication_count'] != before[r]['publication_count']:
            raise RuntimeError('lab_fault_unconfirmed_publication')
        if not now['tun_exists'] or not now['foreign_matches_initial']:
            raise RuntimeError('lab_fault_owner_or_foreign_changed')


def reset(lab):
    inject(lab, 'reset')
    wait_inject(lab, lambda s: s['reset_done'] and not s['reset_failed'])


def recover(lab, before):
    inject(lab, 'release')
    lab.control('restore_a')
    wait_publication(lab, before)
    probe(lab)
    lab.control('same_identity')


def acceptance(lab, result):
    # Every invalid-map vector hits the actual Engine.updateNetworkMap reached
    # by an authenticated real Sync. No test-supplied ACCEPTs or fake callback.
    for mode in ('stale', 'invalid', 'unknown', 'incomplete', 'ack', 'readback'):
        sessions, before = baseline(lab)
        try:
            prior = inject(lab, 'status')
            inject(lab, mode)
            lab.control('restrict_a')
            wait_inject(lab, lambda s: s['hold']
                        and (mode not in ('ack', 'readback') or s[mode + '_hit']), after=prior)
            closed_packets(lab, sessions, before)
            # Failed apply may leave backend caches dirty. Exercise the actual
            # native Close/Create/Init path, WITHOUT stopping the Engine/TUN.
            reset(lab)
            closed_packets(lab, sessions, before)
            recover(lab, before)
            result['native_fault_' + mode] = 'pass'
        finally:
            for s in sessions:
                s.close()
    result['unknown_stale_networkmap'] = 'pass'
    result['kernel_ack_failure'] = 'pass'

    sessions, before = baseline(lab)
    try:
        prior = inject(lab, 'status')
        inject(lab, 'pause')
        lab.control('restrict_a')
        wait_inject(lab, lambda s: s['paused'])
        # ACL apply/readback has completed, but publication has NOT. Reset
        # invalidates its epoch while Engine.syncMsgMux is still held.
        inject(lab, 'engine-reset')
        lab.control('restore_a')  # real concurrent controller update, queued
        closed_packets(lab, sessions, before)
        inject(lab, 'unpause')
        deadline = time.monotonic() + 60
        while True:
            try:
                now = inject(lab, 'status')
                if all(now[r]['generation'] > prior[r]['generation'] and now[r]['hold']
                       and now[r]['rejected'] > 0 for r in A):
                    break
            except RuntimeError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError('lab_real_engine_restart_missing')
            time.sleep(0.2)
        closed_packets(lab, sessions, before)
        lab.control('restrict_a')  # held complete Sync remains unauthorized
        recover(lab, before)
        result['concurrent_policy_reset'] = 'pass'
    finally:
        for s in sessions:
            s.close()

    sessions, before = baseline(lab)
    try:
        state = inject(lab, 'status')
        reset(lab)
        lab.control('restrict_a')
        deadline = time.monotonic() + 40
        while True:
            now = inject(lab, 'status')
            if all(now[r]['maps'] > state[r]['maps'] and now[r]['hold'] for r in A):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('lab_withheld_sync_missing')
            time.sleep(0.2)
        closed_packets(lab, sessions, before)
        recover(lab, before)
        result['fresh_map_after_native_firewall_rebuild'] = 'pass'
    finally:
        for s in sessions:
            s.close()
