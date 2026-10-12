#!/usr/bin/env python3
"""TEST-ONLY experiment: bounded netlink snapshots and generation fencing.

Not bundled or used by the installer. Preserving identities is insufficient
for a safe live-policy implementation; nft_acl_research exposes the counterexample.

Preserve NetBird's opaque userdata and handle: its controller uses these to
retire native ACLs. Never put rule expressions or userdata into diagnostics.
"""
import socket
import struct
import time

MAX_DATAGRAM = 262144
MAX_MESSAGES = 4096
MAX_REPLY_BYTES = 8 * 1024 * 1024


class Rejected(RuntimeError):
    pass


def attribute(kind, data):
    length = 4 + len(data)
    if not 0 < kind <= 65535 or length > 65535:
        raise Rejected('Invalid netlink attribute')
    return struct.pack('=HH', length, kind) + data + b'\0' * (-length % 4)


def attributes(data):
    result, offset = {}, 0
    while offset < len(data):
        if len(data) - offset < 4:
            raise Rejected('Truncated netlink attribute')
        length, kind = struct.unpack_from('=HH', data, offset)
        kind &= 0x3fff
        if length < 4 or offset + length > len(data) or kind in result:
            raise Rejected('Invalid or duplicate netlink attribute')
        result[kind] = data[offset + 4:offset + length]
        offset += (length + 3) & ~3
    return result


def string(value):
    if not isinstance(value, str) or not value or '\0' in value or len(value) > 255:
        raise Rejected('Invalid netlink name')
    return value.encode('ascii') + b'\0'


class Netlink:
    def __init__(self):
        self.socket = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 12)
        self.socket.bind((0, 0))
        self.socket.settimeout(8)
        self.sequence = 1000

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.socket.close()

    def message(self, kind, flags, payload):
        self.sequence += 1
        return (struct.pack('=IHHII', 16 + len(payload), kind, flags,
                            self.sequence, 0) + payload, self.sequence)

    def receive(self, expected, dump=False):
        result, count, total = [], 0, 0
        deadline = time.monotonic() + 8
        while expected:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Rejected("Netlink deadline exceeded")
            self.socket.settimeout(remaining)
            data, unused, flags, sender = self.socket.recvmsg(MAX_DATAGRAM)
            if flags & socket.MSG_TRUNC or sender[0] != 0:
                raise Rejected('Truncated or non-kernel netlink response')
            total += len(data)
            if total > MAX_REPLY_BYTES:
                raise Rejected('Netlink response exceeds limit')
            offset = 0
            while offset < len(data):
                count += 1
                if count > MAX_MESSAGES or len(data) - offset < 16:
                    raise Rejected('Invalid or excessive netlink messages')
                size, kind, flags, sequence, unused = struct.unpack_from('=IHHII', data, offset)
                if size < 16 or offset + size > len(data):
                    raise Rejected('Truncated netlink message')
                body = data[offset + 16:offset + size]
                offset += (size + 3) & ~3
                if kind == 2:
                    if len(body) < 4:
                        raise Rejected('Invalid netlink acknowledgement')
                    error = struct.unpack_from('=i', body)[0]
                    # Batch-begin errors can arrive under another sequence.
                    # Never wait past or ignore a failed generation fence.
                    if error:
                        raise Rejected('Kernel rejected nftables transaction')
                if sequence not in expected:
                    continue
                if flags & 0x10:
                    raise Rejected('Interrupted netlink snapshot')
                if kind == 2:
                    if not dump:
                        expected.remove(sequence)
                elif kind == 3:
                    if len(body) >= 4 and struct.unpack_from('=i', body)[0]:
                        raise Rejected('Failed netlink dump')
                    expected.remove(sequence)
                else:
                    if len(body) < 4:
                        raise Rejected('Missing netfilter header')
                    result.append((kind, body))
                    if not flags & 2:
                        expected.remove(sequence)
        return result

    def request(self, kind, payload=b'', dump=False):
        data, sequence = self.message((10 << 8) | kind, 1 | (0x300 if dump else 0),
                                      b'\x02\0\0\0' + payload)
        self.socket.send(data)
        return self.receive({sequence}, dump)

    def generation(self):
        responses = self.request(16)
        if len(responses) != 1:
            raise Rejected('Invalid generation response')
        value = attributes(responses[0][1][4:]).get(1)
        if value is None or len(value) != 4:
            raise Rejected('Missing generation')
        return struct.unpack('!I', value)[0]

    def rules(self, table, chain):
        entries = self.request(7, attribute(1, string(table)) + attribute(2, string(chain)), True)
        result = {}
        for kind, body in entries:
            if kind != (10 << 8) | 6 or body[0] != socket.AF_INET:
                raise Rejected('Unexpected rule family or message')
            values = attributes(body[4:])
            if (values.get(1) != string(table) or values.get(2) != string(chain)
                    or len(values.get(3, b'')) != 8 or not values.get(4)):
                raise Rejected('Incomplete native rule')
            handle = struct.unpack('!Q', values[3])[0]
            if handle <= 0 or handle in result:
                raise Rejected('Duplicate native handle')
            result[handle] = dict(handle=handle, expressions=values[4], userdata=values.get(7, b''))
        return result

    def replace(self, table, chain, rules, generation):
        """Atomic compare-and-replace; never create a permission by fallback."""
        if type(generation) is not int or not 0 <= generation <= 0xffffffff or not rules:
            raise Rejected('Invalid replacement transaction')
        begin, unused = self.message(16, 1, b'\0\0\0\x0a' +
                                     attribute(1, struct.pack('!I', generation)))
        messages, expected, handles = [begin], set(), set()
        for rule in rules:
            handle = rule.get('handle')
            if (type(handle) is not int or not 0 < handle < 2**64 or handle in handles
                    or not isinstance(rule.get('expressions'), bytes) or not rule['expressions']
                    or not isinstance(rule.get('userdata'), bytes) or len(rule['userdata']) > 256):
                raise Rejected('Invalid replacement rule')
            handles.add(handle)
            payload = (attribute(1, string(table)) + attribute(2, string(chain)) +
                       attribute(3, struct.pack('!Q', handle)) +
                       attribute(4 | 0x8000, rule['expressions']) +
                       attribute(7, rule['userdata']))
            message, sequence = self.message((10 << 8) | 6, 1 | 4 | 0x100,
                                              b'\x02\0\0\0' + payload)
            messages.append(message)
            expected.add(sequence)
        end, unused = self.message(17, 1, b'\0\0\0\x0a')
        messages.append(end)
        packet = b''.join(messages)
        if len(packet) > MAX_DATAGRAM:
            raise Rejected('Replacement batch exceeds limit')
        self.socket.send(packet)
        self.receive(expected)
