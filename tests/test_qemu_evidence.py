"""A failed guest must report fixed proof flags without exposing its console."""
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('qemu_platforms', Path(__file__).with_name('qemu_platforms.py'))
qemu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qemu)


class QemuEvidenceTests(unittest.TestCase):
    def test_failed_guest_preserves_known_proofs_without_raw_console(self):
        raw = ('synthetic-private-material\n'
               'PASS: actual kernel original/reply address/port readback, stable UserData and handles\n'
               'SIGKILL removes interface and blocks NEW tuple; foreign rules preserved\n'
               'P1_AFTER_FINAL_READ_STALE_GRANT:45707\n'
               'OPENMAIN_QA_FAIL_LINE_19\n')
        proof = qemu.guest_evidence(raw)
        self.assertTrue(proof['ct_readback_pass'])
        self.assertTrue(proof['tun_sigkill_pass'])
        self.assertTrue(proof['qa_failure'])
        self.assertFalse(proof['qa_pass'])
        self.assertTrue(proof['legacy_p1_markers']['P1_AFTER_FINAL_READ_STALE_GRANT:45707'])
        self.assertFalse(proof['tun_failstop_pass'])
        self.assertNotIn('synthetic-private-material', json.dumps(proof))
        self.assertTrue(all(type(v) is bool for k, v in proof.items() if k != 'legacy_p1_markers'))
        self.assertTrue(qemu.guest_evidence('FAILED (errors=1)')['unit_failure'])
        self.assertFalse(any(qemu.guest_evidence('')['legacy_p1_markers'].values()))
