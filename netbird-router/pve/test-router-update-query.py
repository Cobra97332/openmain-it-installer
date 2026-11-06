#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

path = Path(__file__).with_name("router-update-zabbix-query.py")
spec = importlib.util.spec_from_file_location("router_query", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Socket:
    def __init__(self, answer):
        payload = answer.encode()
        self.response = b"ZBXD\x01" + struct.pack("<II", len(payload), 0) + payload
        self.request = b""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def settimeout(self, _):
        pass

    def sendall(self, data):
        self.request = data

    def recv(self, count):
        part, self.response = self.response[:count], self.response[count:]
        return part


class QueryTests(unittest.TestCase):
    def test_role_and_readiness(self):
        for key, answer in (("openmain.router.role", "primary"), ("openmain.router.ready", "1")):
            sock = Socket(answer)
            with patch.object(module.socket, "create_connection", return_value=sock):
                self.assertEqual(module.query("192.0.2.2", key), answer)
            self.assertEqual(sock.request[:5], b"ZBXD\x01")
            self.assertEqual(sock.request[13:], key.encode())

    def test_unexpected_response_fails_closed(self):
        with patch.object(module.socket, "create_connection", return_value=Socket("ZBX_NOTSUPPORTED")):
            with self.assertRaises(ValueError):
                module.query("192.0.2.2", "openmain.router.ready")


if __name__ == "__main__":
    unittest.main()
