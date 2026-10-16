#!/usr/bin/env python3
"""Actual sockets and current-namespace kernel observations; no ACL fixtures."""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import socket
import subprocess
import sys


def nft(table):
    r = subprocess.run(['nft', '-j', 'list', 'table', *table.split()], capture_output=True, text=True, timeout=10)
    if r.returncode:
        return None
    return json.loads(r.stdout)


def canonical(value):
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items() if k not in ('handle', 'packets', 'bytes')}
    if isinstance(value, list):
        return [canonical(v) for v in value]
    return value


def client_diagnostics():
    # Read only this disposable client's private log; export fixed flags, never
    # lines/paths/profile values or arbitrary upstream error strings.
    path = Path('/run/lab-client.log')
    text = path.read_text(errors='replace')[-1048576:] if path.exists() else ''
    needles = {
        'argument_error': ('unknown flag:', 'unknown shorthand flag:'),
        'tls_error': ('certificate signed by unknown authority', 'certificate is not valid'),
        'enrollment_error': ('setup key is invalid', 'invalid setup key'),
        'permission_error': ('permission denied', 'operation not permitted'),
        'binat_start_error': ('BINAT requires', 'BINAT overlay', 'BINAT existing guard', 'BINAT orphan'),
        'binat_map_error': ('BINAT stale/incomplete', 'BINAT complete', 'BINAT ambiguous'),
        'binat_unconfirmed_error': ('BINAT native gate closure unconfirmed',),
        'binat_route_update_error': ('BINAT route update failed', 'BINAT forwarding update failed'),
        'binat_address_update_error': ('BINAT local-address update failed', 'BINAT DNS update failed'),
        'binat_map_type_error': ('BINAT dynamic/custom route unsupported',
                               'BINAT forwarding services unsupported', 'BINAT unsupported route protocol',
                               'BINAT unsupported route action'),
        'binat_map_selector_error': ('BINAT invalid route destination', 'BINAT invalid port',
                                   'BINAT incomplete port selector', 'BINAT unknown port selector', 'BINAT ports on unsupported protocol',
                                   'BINAT invalid source', 'BINAT unknown route policy fields'),
        'binat_kernel_operation_error': ('BINAT gate kernel operation failed', 'BINAT opening failed'),
        'sync_handler_error': ('failed handling an update message received from Management Service:',),
        'binat_native_apply_error': ('BINAT native ACL apply failed',),
        'binat_native_chain_error': ('BINAT native chain readback failed', 'BINAT native chain missing',
                                     'BINAT native hook changed', 'BINAT native postrouting boundary missing'),
        'binat_native_handle_error': ('BINAT native handle missing', 'BINAT native ACL chain missing',
                                      'BINAT native tracked ACL absent', 'BINAT native ACL readback failed'),
        'native_peer_apply_error': ('apply peer ACLs:',),
        'native_route_apply_error': ('apply route ACLs:',),
        'native_flush_error': ('failed to flush firewall rules:',),
        'kernel_invalid_argument': ('invalid argument', 'Invalid argument'),
        'kernel_unsupported_operation': ('operation not supported', 'Operation not supported'),
        'binat_native_error': ('BINAT native ACL apply failed', 'BINAT native chain',
                               'BINAT native hook changed', 'BINAT native postrouting boundary missing',
                               'BINAT native handle missing', 'BINAT native tracked ACL absent',
                               'BINAT native ACL readback failed', 'BINAT native ACL chain missing'),
        'stale_passthrough_refused': ('BINAT stale foreign interface passthrough',),
        'firewalld_state_refused': ('BINAT firewalld state unsupported', 'BINAT foreign firewalld state refused'),
        'legacy_filter_refused': ('BINAT legacy iptables filter state unsupported',),
        'native_table_ownership_refused': ('BINAT requires default native table ownership',),
        'binat_gate_error': ('BINAT guard', 'BINAT reference', 'BINAT unknown', 'BINAT closure', 'BINAT unconfirmed'),
        'config_only_closure_seen': ('BINAT config-only sync: guard remains closed',),
        'binat_closure_error': ('BINAT reset closure unconfirmed', 'BINAT stream-end closure unconfirmed', 'BINAT stream-loss closure unconfirmed'),
    }
    alive = False
    if Path('/run/lab-client.pid').exists():
        try:
            os.kill(int(Path('/run/lab-client.pid').read_text()), 0)
            alive = True
        except (ValueError, OSError):
            pass
    state = inspect()
    return {'process_exists': alive, 'local_publication_seen': state['serial'] >= 0,
            'gate_open': state['open'], 'tun_exists': state['tun_exists'],
            'gate_table_present': state.get('gate_table_present', False),
            'ready_rule_present': state.get('ready_rule_present', False),
            'scoped_dnat_seen': state.get('dnat_packets', 0) > 0,
            'scoped_readiness_counter_seen': state.get('ready_packets', 0) > 0,
            **{k: any(n in text for n in v) for k, v in needles.items()}}


FOREIGN_REFERENCE = Path('/run/lab-foreign-reference')


def foreign_digest(rules):
    return hashlib.sha256(json.dumps(canonical(rules), sort_keys=True).encode()).hexdigest()


def foreign_state():
    external, filter_table = nft('inet lab_foreign'), nft('ip filter')
    if external is None or filter_table is None:
        return None
    return {'external': external, 'filter': filter_table}


def record_foreign_reference():
    rules = foreign_state()
    if rules is None:
        raise RuntimeError('lab_foreign_reference_missing')
    fd = os.open(FOREIGN_REFERENCE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(foreign_digest(rules))


def foreign_matches_reference(rules):
    if rules is None:
        return False
    try:
        fd = os.open(FOREIGN_REFERENCE, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'r') as f:
            info = os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
                return False
            value = f.read(65)
        return bool(re.fullmatch('[a-f0-9]{64}', value)) and value == foreign_digest(rules)
    except (OSError, ValueError):
        return False


def inspect():
    text = Path('/run/lab-client.log').read_text() if Path('/run/lab-client.log').exists() else ''
    serials = re.findall(r'BINAT native kernel policy applied locally: serial=(\d+) controller_ack=false', text)
    gate = nft('ip netbird_binat_native_gate_v1')
    ready = [] if gate is None else [x['rule'] for x in gate['nftables']
            if 'rule' in x and x['rule'].get('chain') == 'ready']
    foreign = foreign_state()
    nat = nft('ip lab_translation')
    # Only generic proof data. Never emit the client log or profile content.
    return {'serial': int(serials[-1]) if serials else -1,
        'publication_count': len(serials),
        'stale_refusals': text.count('BINAT stale foreign interface passthrough'),
        'orphan_refusals': text.count('BINAT orphaned boundary'),
        'ready_packets': sum(e['counter']['packets'] for r in ready for e in r.get('expr', []) if 'counter' in e),
        'gate_table_present': gate is not None, 'ready_rule_present': bool(ready),
        'open': len(ready) == 1 and ready[0].get('comment') == 'native-policy-ready',
        # sysfs can refer to the outer namespace; use actual rtnetlink instead.
        'tun_exists': subprocess.run(['ip', 'link', 'show', 'dev', 'wt0'],
                                    capture_output=True, timeout=10).returncode == 0,
        'foreign_digest': foreign_digest(foreign),
        'foreign_matches_initial': foreign_matches_reference(foreign),
        'dnat_packets': sum(expr['counter']['packets'] for obj in (nat or {}).get('nftables', [])
                            for expr in obj.get('rule', {}).get('expr', []) if 'counter' in expr
                            and obj.get('rule', {}).get('chain') == 'dn'),
        'related_packets': sum(expr['counter']['packets'] for obj in (nat or {}).get('nftables', [])
                            for expr in obj.get('rule', {}).get('expr', []) if 'counter' in expr
                            and obj.get('rule', {}).get('chain') == 'related_observe')}


def socket_session(host, protocol, port=None, passive=False):
    port = port or ((8084 if protocol == 'tcp' else 8085) if passive else (8080 if protocol == 'tcp' else 8081))
    if port not in (8080, 8081, 8082, 8083, 8084, 8085):
        raise ValueError('lab_target_refused')
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM if protocol == 'tcp' else socket.SOCK_DGRAM) as sock:
        sock.settimeout(0.6)
        connected = False
        buffered = b''
        if passive:
            sock.connect((host, port))
            sock.sendall(b'openmain-passive-start')  # only client write
            connected = True
        for line in sys.stdin:
            req = json.loads(line)
            reply = False
            if passive:
                # Docker clients/server/runner share the kernel monotonic clock
                # (no time namespaces). Exclude frames sent BEFORE the caller's
                # confirmed-publication barrier, without a grace-period waiver.
                # Collection sends NO request; TCP ACKs are kernel-generated.
                import time
                until = time.monotonic() + 0.8
                while time.monotonic() < until:
                    sock.settimeout(max(0.001, until - time.monotonic()))
                    try:
                        data = sock.recv(4096)
                        if not data:
                            break
                        buffered += data
                        while b'\n' in buffered:
                            line, buffered = buffered.split(b'\n', 1)
                            if not line.startswith(b'openmain-passive:'):
                                raise ValueError('lab_passive_frame_invalid')
                            sent = int(line.split(b':', 1)[1])
                            reply = reply or sent >= req.get('after_ns', 0)
                    except (OSError, TimeoutError):
                        break
                print(json.dumps({'reply': reply, 'connected': connected}), flush=True)
                continue
            try:
                if not connected:
                    sock.connect((host, port))
                    connected = True
                payload = os.urandom(24)
                sock.sendall(payload)
                reply = sock.recv(1024) == payload
            except (OSError, TimeoutError):
                pass
            print(json.dumps({'reply': reply, 'connected': connected}), flush=True)


def main():
    if sys.argv[1] == 'related':
        from related import client
        client(sys.argv[2])
    elif sys.argv[1] == 'foreign-reference':
        record_foreign_reference()
        print('{}')
    elif sys.argv[1] == 'diagnostics':
        print(json.dumps(client_diagnostics()))
    elif sys.argv[1] == 'inspect':
        print(json.dumps(inspect()))
    elif sys.argv[1] in ('session', 'passive'):
        # Only the two exact synthetic BINAT destinations, never arbitrary URLs.
        if sys.argv[2] not in ('198.18.10.100', '198.18.20.100') or sys.argv[3] not in ('tcp', 'udp'):
            raise ValueError('lab_target_refused')
        socket_session(sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else None,
                       passive=sys.argv[1] == 'passive')
    elif sys.argv[1] == 'ping':
        if sys.argv[2] not in ('198.18.10.100', '198.18.20.100'):
            raise ValueError('lab_target_refused')
        code = subprocess.run(['ping', '-n', '-c', '1', '-W', '1', sys.argv[2]],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3).returncode
        if code not in (0, 1):
            raise RuntimeError('lab_icmp_probe_failed')
        print(json.dumps({'reply': code == 0}))
    elif sys.argv[1] in ('stale-seed', 'stale-clear'):
        if os.environ.get('OPENMAIN_ISOLATED_TEST') != '1':
            raise ValueError('lab_operation_refused')
        script = ('create table inet lab_stale_resume\n'
                  'add chain inet lab_stale_resume forward { type filter hook forward priority 0; policy accept; }\n'
                  'add rule inet lab_stale_resume forward iifname "wt0" counter accept\n')
        if sys.argv[1] == 'stale-clear':
            script = 'delete table inet lab_stale_resume\n'  # only this synthetic test table
        subprocess.run(['nft', '-f', '-'], input=script, text=True, check=True, capture_output=True)
        print('{}')
    elif sys.argv[1] == 'kill':
        try:
            os.kill(int(Path('/run/lab-client.pid').read_text()), signal.SIGKILL)
        except ProcessLookupError:
            pass  # Our failed test client may already have exited.
        print('{}')
    else:
        raise ValueError('lab_operation_refused')


if __name__ == '__main__':
    main()
