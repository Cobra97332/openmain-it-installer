"""Real delayed process pipes, NOT NetBird packet/authorization evidence."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import time
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'netbird-router/experimental/native-binat/docker-lab/packets.py'
spec = importlib.util.spec_from_file_location('realtime_packets', SOURCE)
packets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packets)


class ProbeRealtimeTests(unittest.TestCase):
    def session(self, body, budget):
        session = packets.Session.__new__(packets.Session)
        session.deadline = time.monotonic() + budget
        session.proc = subprocess.Popen([sys.executable, '-u', '-c',
            'import sys,time\nsys.stdin.readline()\n' + body], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.addCleanup(session.close)
        return session

    def test_delayed_positive_reply_within_budget(self):
        session = self.session('time.sleep(0.15)\nprint(\'{"reply":true}\',flush=True)', 2)
        start = time.monotonic()
        self.assertTrue(session.exchange())
        self.assertGreaterEqual(time.monotonic() - start, 0.1)

    def test_silent_process_timeout_is_not_a_denial(self):
        session = self.session('time.sleep(1.5)\nprint(\'{"reply":false}\',flush=True)', 0.2)
        start = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, '^lab_packet_probe_timeout$'):
            session.exchange()
        self.assertLess(time.monotonic() - start, 1.2)

    def test_fragmented_reply_cannot_block_readline_past_deadline(self):
        session = self.session('sys.stdout.write(\'{"reply":\');sys.stdout.flush()\n'
                               'time.sleep(1.5)\nprint(\'false}\',flush=True)', 0.2)
        start = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, '^lab_packet_probe_timeout$'):
            session.exchange()
        self.assertLess(time.monotonic() - start, 1.2)

    def test_nonboolean_reply_and_eof_are_not_authorization_evidence(self):
        for body in ('print(\'{"reply":0}\',flush=True)', 'pass'):
            with self.subTest(body=body):
                session = self.session(body, 2)
                with self.assertRaisesRegex(RuntimeError, '^lab_packet_probe_incomplete$'):
                    session.exchange()
