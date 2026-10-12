"""Parser/fencing contracts for TEST-ONLY research; never an installer fallback."""
import socket
import struct
import unittest
from unittest.mock import Mock, patch

import nft_netlink_experiment as nl


def message(kind, payload, sequence=1002, flags=0):
    return struct.pack('=IHHII', 16 + len(payload), kind, flags, sequence, 0) + payload


class NetlinkResearchTests(unittest.TestCase):
    def client(self):
        raw = Mock()
        with patch.object(nl.socket, 'socket', return_value=raw):
            api = nl.Netlink()
        return api, raw

    def test_attribute_alignment_and_nested_kind(self):
        data = nl.attribute(1, b'ab') + nl.attribute(4 | 0x8000, b'123')
        self.assertEqual(nl.attributes(data), {1: b'ab', 4: b'123'})

    def test_truncated_duplicate_and_invalid_attributes_rejected(self):
        for data in (b'\0', b'\3\0\1\0', b'\xff\0\1\0',
                     nl.attribute(1, b'x') * 2):
            with self.subTest(data=data), self.assertRaises(nl.Rejected):
                nl.attributes(data)

    def test_names_are_data_not_null_injection(self):
        for name in ('', 'a\0b', 'a' * 256, None):
            with self.subTest(name=name), self.assertRaises(nl.Rejected):
                nl.string(name)

    def test_kernel_origin_and_complete_datagram_required(self):
        for flags, sender in ((socket.MSG_TRUNC, (0, 0)), (0, (42, 0))):
            api, raw = self.client()
            raw.recvmsg.return_value = (b'', [], flags, sender)
            with self.assertRaises(nl.Rejected):
                api.receive({1002})

    def test_interrupted_dump_is_not_partial_success(self):
        api, raw = self.client()
        raw.recvmsg.return_value = (message(3, b'', flags=0x10), [], 0, (0, 0))
        with self.assertRaises(nl.Rejected):
            api.receive({1002}, dump=True)

    def test_batch_begin_generation_rejection_not_ignored(self):
        api, raw = self.client()
        raw.recvmsg.return_value = (message(2, struct.pack('=i', -11), 1001), [], 0, (0, 0))
        with self.assertRaises(nl.Rejected):
            api.receive({1002})

    def test_replacement_preserves_handle_and_opaque_rule_id(self):
        api, raw = self.client()
        raw.recvmsg.return_value = (message(2, struct.pack('=i', 0)), [], 0, (0, 0))
        identity = b'10.40.1.0/24-1234567890abcdef'
        api.replace('fixture', 'routed', [dict(handle=15, expressions=b'abcd', userdata=identity)], 73)
        sent = raw.send.call_args.args[0]
        offset, packets = 0, []
        while offset < len(sent):
            size, kind, flags, seq, pid = struct.unpack_from('=IHHII', sent, offset)
            packets.append((kind, sent[offset + 16:offset + size]))
            offset += (size + 3) & ~3
        self.assertEqual([kind for kind, body in packets], [16, (10 << 8) | 6, 17])
        self.assertEqual(nl.attributes(packets[0][1][4:])[1], struct.pack('!I', 73))
        attrs = nl.attributes(packets[1][1][4:])
        self.assertEqual(attrs[3], struct.pack('!Q', 15))
        self.assertEqual(attrs[7], identity)

    def test_invalid_replacement_has_no_send(self):
        for rules in ([], [dict(handle=0, expressions=b'x', userdata=b'')],
                      [dict(handle=1, expressions=b'', userdata=b'')],
                      [dict(handle=1, expressions=b'x', userdata=b'x' * 257)]):
            api, raw = self.client()
            with self.subTest(rules=rules), self.assertRaises(nl.Rejected):
                api.replace('fixture', 'routed', rules, 1)
            raw.send.assert_not_called()

    def test_generation_must_be_complete_and_big_endian(self):
        api, raw = self.client()
        api.request = Mock(return_value=[(0, b'\x02\0\0\0' + nl.attribute(1, struct.pack('!I', 73)))])
        self.assertEqual(api.generation(), 73)
        api.request.return_value = [(0, b'\x02\0\0\0')]
        with self.assertRaises(nl.Rejected):
            api.generation()

    def test_context_closes_socket_even_after_failure(self):
        api, raw = self.client()
        with self.assertRaises(ValueError):
            with api:
                raise ValueError('fixture')
        raw.close.assert_called_once()

    def test_ack_cannot_be_truncated(self):
        api, raw = self.client()
        raw.recvmsg.return_value = (message(2, b'\0'), [], 0, (0, 0))
        with self.assertRaises(nl.Rejected):
            api.receive({1002})

    def test_deadline_prevents_repeated_slow_datagrams(self):
        api, raw = self.client()
        with patch.object(nl.time, 'monotonic', side_effect=[10, 19]):
            with self.assertRaises(nl.Rejected):
                api.receive({1002})
        raw.recvmsg.assert_not_called()


if __name__ == '__main__':
    unittest.main()
