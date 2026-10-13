#!/usr/bin/env python3
"""Actual sockets and current-namespace kernel observations; no ACL fixtures."""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
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
        'binat_native_error': ('BINAT native',),
        'binat_gate_error': ('BINAT guard', 'BINAT reference', 'BINAT unknown', 'BINAT closure', 'BINAT unconfirmed'),
        'binat_closure_error': ('BINAT reset closure unconfirmed', 'BINAT stream-end closure unconfirmed'),
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
            **{k: any(n in text for n in v) for k, v in needles.items()}}


def inspect():
    text = Path('/run/lab-client.log').read_text() if Path('/run/lab-client.log').exists() else ''
    serials = re.findall(r'BINAT native kernel policy applied locally: serial=(\d+) controller_ack=false', text)
    gate = nft('ip netbird_binat_native_gate_v1')
    ready = [] if gate is None else [x['rule'] for x in gate['nftables']
            if 'rule' in x and x['rule'].get('chain') == 'ready']
    foreign = nft('inet lab_foreign')
    nat = nft('ip lab_translation')
    # Only generic proof data. Never emit the client log or profile content.
    return {'serial': int(serials[-1]) if serials else -1,
        'open': len(ready) == 1 and ready[0].get('comment') == 'native-policy-ready',
        # sysfs can refer to the outer namespace; use actual rtnetlink instead.
        'tun_exists': subprocess.run(['ip', 'link', 'show', 'dev', 'wt0'],
                                    capture_output=True, timeout=10).returncode == 0,
        'foreign_digest': hashlib.sha256(json.dumps(canonical(foreign), sort_keys=True).encode()).hexdigest(),
        'dnat_packets': sum(expr['counter']['packets'] for obj in (nat or {}).get('nftables', [])
                            for expr in obj.get('rule', {}).get('expr', []) if 'counter' in expr
                            and obj.get('rule', {}).get('chain') == 'dn')}


def socket_session(host, protocol):
    port = 8080 if protocol == 'tcp' else 8081
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM if protocol == 'tcp' else socket.SOCK_DGRAM) as sock:
        sock.settimeout(0.6)
        connected = False
        for line in sys.stdin:
            req = json.loads(line)
            reply = False
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
    if sys.argv[1] == 'diagnostics':
        print(json.dumps(client_diagnostics()))
    elif sys.argv[1] == 'inspect':
        print(json.dumps(inspect()))
    elif sys.argv[1] == 'session':
        # Only the two exact synthetic BINAT destinations, never arbitrary URLs.
        if sys.argv[2] not in ('198.18.10.100', '198.18.20.100') or sys.argv[3] not in ('tcp', 'udp'):
            raise ValueError('lab_target_refused')
        socket_session(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == 'kill':
        os.kill(int(Path('/run/lab-client.pid').read_text()), signal.SIGKILL)
        print('{}')
    else:
        raise ValueError('lab_operation_refused')


if __name__ == '__main__':
    main()
