#!/usr/bin/env python3
"""Root-only control for disposable instrumented clients; no policy payloads."""
import json
from pathlib import Path
import socket
import sys

ROOT = Path('/run/netbird-binat-small-lab')
OPERATIONS = ('status', 'stale', 'invalid', 'unknown', 'incomplete', 'pause',
              'ack', 'readback', 'reset', 'release', 'unpause')


def command(operation):
    if operation not in OPERATIONS:
        raise RuntimeError('lab_injector_operation_refused')
    pid = int(Path('/run/lab-client.pid').read_text())
    if pid <= 1:
        raise RuntimeError('lab_injector_owner_invalid')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(10)
        s.connect(str(ROOT / ('inject-' + str(pid) + '.sock')))
        s.sendall(json.dumps({'operation': operation}).encode() + b'\n')
        data = b''
        while b'\n' not in data:
            chunk = s.recv(4096)
            if not chunk or len(data) + len(chunk) > 4096:
                raise RuntimeError('lab_injector_incomplete')
            data += chunk
    value = json.loads(data)
    if value.get('ok') is not True:
        raise RuntimeError('lab_injector_refused')
    # Fixed event markers, not arbitrary daemon data.
    value['ack_hit'] = (ROOT / 'hit-ack').is_file()
    value['readback_hit'] = (ROOT / 'hit-readback').is_file()
    return value


if __name__ == '__main__':
    try:
        print(json.dumps(command(sys.argv[1])))
    except (OSError, ValueError, RuntimeError, IndexError):
        print(json.dumps({'error_class': 'lab_injector_failed'}))
        sys.exit(1)
