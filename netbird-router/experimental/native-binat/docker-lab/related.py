"""Synthetic ICMP errors through the ACTUAL lab routers, not synthetic ACLs."""
import json
import os
from pathlib import Path
import socket
import struct
import sys
import time

RECORD = Path('/run/lab-related-parent.json')
PAYLOAD = b'openmain-related-parent'


def checksum(data):
    if len(data) % 2:
        data += b'\0'
    value = sum(struct.unpack('!%dH' % (len(data) // 2), data))
    while value >> 16:
        value = (value & 65535) + (value >> 16)
    return (~value) & 65535


def record():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(('192.168.88.100', 8086))
        while True:
            payload, peer = receiver.recvfrom(2048)
            if payload != PAYLOAD or peer[0] not in ('192.168.88.1', '192.168.88.3'):
                continue
            temporary = RECORD.with_suffix('.new')
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump({'source': peer[0], 'port': peer[1]}, stream)
            os.replace(temporary, RECORD)


def emit():
    # Only this disposable LAN's synthetic parent, never arbitrary quoted tuples.
    fd = os.open(RECORD, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        parent = json.load(stream)
    source, port = parent['source'], parent['port']
    if source not in ('192.168.88.1', '192.168.88.3') or type(port) is not int or not 1024 <= port <= 65535:
        raise RuntimeError('lab_related_parent_invalid')
    udp = struct.pack('!HHHH', port, 8086, 8 + len(PAYLOAD), 0)
    ip = struct.pack('!BBHHHBBH4s4s', 0x45, 0, 28 + len(PAYLOAD), 0, 0, 64, 17, 0,
                     socket.inet_aton(source), socket.inet_aton('192.168.88.100'))
    ip = ip[:10] + struct.pack('!H', checksum(ip)) + ip[12:]
    error = struct.pack('!BBHI', 3, 3, 0, 0) + ip + udp
    error = error[:2] + struct.pack('!H', checksum(error)) + error[4:]
    with socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP) as sender:
        sender.bind(('192.168.88.100', 0))
        sender.sendto(error, (source, 0))


def matches(packet, host, port):
    if len(packet) < 56 or packet[0] >> 4 != 4 or packet[9] != 1:
        return False
    offset = (packet[0] & 15) * 4
    if packet[offset:offset+2] != b'\x03\x03':
        return False
    quote = packet[offset+8:]
    if offset < 20 or len(quote) < 20 or quote[0] >> 4 != 4:
        return False
    size = (quote[0] & 15) * 4
    return (size >= 20 and len(quote) >= size + 8 and quote[9] == 17
            and quote[16:20] == socket.inet_aton(host)
            and struct.unpack('!HH', quote[size:size+4]) == (port, 8086))


def client(host):
    if host != '198.18.10.100':
        raise RuntimeError('lab_packet_target_refused')
    with socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP) as capture, \
         socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as parent:
        parent.connect((host, 8086))
        parent.send(PAYLOAD)  # ONLY request, before withdrawal
        port = parent.getsockname()[1]
        for line in sys.stdin:
            json.loads(line)
            reply = False
            deadline = time.monotonic() + 0.8
            while time.monotonic() < deadline:
                capture.settimeout(max(0.001, deadline - time.monotonic()))
                try:
                    packet = capture.recv(4096)
                    reply = reply or matches(packet, host, port)
                except socket.timeout:
                    break
            print(json.dumps({'reply': reply}), flush=True)


if __name__ == '__main__':
    if os.environ.get('OPENMAIN_ISOLATED_TEST') != '1':
        raise SystemExit('isolated lab required')
    if sys.argv[1] == 'emit':
        emit()
    elif sys.argv[1] == 'ready':
        assert RECORD.is_file(), 'lab_related_parent_missing'
    else:
        raise SystemExit('lab operation refused')
