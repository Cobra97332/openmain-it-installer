#!/usr/bin/env python3
"""Minimal, read-only Zabbix Agent 2 passive item query for router readiness."""
import ipaddress
import socket
import struct
import sys


def receive_exact(connection, count):
    result = bytearray()
    while len(result) < count:
        part = connection.recv(count - len(result))
        if not part:
            raise OSError("truncated Zabbix response")
        result.extend(part)
    return bytes(result)


def query(address, key):
    ipaddress.IPv4Address(address)
    if key not in ("openmain.router.role", "openmain.router.ready"):
        raise ValueError("unsupported item")
    request = key.encode("ascii")
    packet = b"ZBXD\x01" + struct.pack("<II", len(request), 0) + request
    with socket.create_connection((address, 10050), timeout=5) as connection:
        connection.settimeout(5)
        connection.sendall(packet)
        header = receive_exact(connection, 13)
        if header[:5] != b"ZBXD\x01":
            raise ValueError("invalid Zabbix response")
        length, reserved = struct.unpack("<II", header[5:])
        if reserved or not 0 < length < 256:
            raise ValueError("unexpected Zabbix response length")
        answer = receive_exact(connection, length).decode("utf-8", "strict").strip()
        if answer not in (("primary", "backup") if key.endswith("role") else ("0", "1")):
            raise ValueError("unexpected Zabbix item value")
        return answer


if __name__ == "__main__":
    try:
        if len(sys.argv) != 3:
            raise ValueError("usage: openmain-zabbix-query IPv4 item")
        print(query(sys.argv[1], sys.argv[2]))
    except (OSError, ValueError, UnicodeError) as exc:
        print(f"Zabbix-Abfrage fehlgeschlagen: {exc}", file=sys.stderr)
        sys.exit(1)
