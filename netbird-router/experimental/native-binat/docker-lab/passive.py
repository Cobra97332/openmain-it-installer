"""Read-only LAN packet proof: application sendall alone is not wire evidence."""
import json
import os
from pathlib import Path
import re
import socket
import struct

PROOF = Path('/run/lab-passive-proof.json')


def timestamp(frame):
    if len(frame) < 42 or frame[12:14] != b'\x08\x00':
        return None
    ip = frame[14:]
    size = (ip[0] & 15) * 4
    if size < 20 or len(ip) < size + 8 or ip[12:16] != socket.inet_aton('192.168.88.100'):
        return None
    data = ip[size:]
    port = struct.unpack('!H', data[:2])[0]
    if ip[9] == 6 and port == 8084 and len(data) >= 20:
        protocol, offset = 'tcp', (data[12] >> 4) * 4
        if offset < 20:
            return None
    elif ip[9] == 17 and port == 8085:
        protocol, offset = 'udp', 8
    else:
        return None
    times = re.findall(rb'openmain-passive:(\d+)\n', data[offset:])
    return (protocol, max(map(int, times))) if times else None


def capture(dev):
    observed = {'tcp': 0, 'udp': 0}
    with socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3)) as stream:
        stream.bind((dev, 0))
        while True:
            frame, address = stream.recvfrom(65535)
            if address[2] != socket.PACKET_OUTGOING:
                continue
            proof = timestamp(frame)
            if proof is None:
                continue
            protocol, sent = proof
            observed[protocol] = max(observed[protocol], sent)
            temporary = PROOF.with_suffix('.new')
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as output:
                json.dump(observed, output)
            os.replace(temporary, PROOF)


def read():
    fd = os.open(PROOF, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        value = json.load(stream)
    if set(value) != {'tcp', 'udp'} or any(type(v) is not int or v < 0 for v in value.values()):
        raise RuntimeError('lab_passive_sender_unproven')
    return value
