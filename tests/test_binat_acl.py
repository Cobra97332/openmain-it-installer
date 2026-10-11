#!/usr/bin/env python3
"""Fail-closed policy compatibility tests: only rewrite a real NetBird native ACL."""
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'netbird-router/common/binat_acl.py'
spec = importlib.util.spec_from_file_location('binat_acl', MODULE)
acl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acl)

CFG = dict(overlay='wt0', lan='eth0', virtual='10.30.2.0/24', real='192.168.178.0/24')


def native_rule(source='@nb-7d0b7d19', extra=None):
    expr = [
        {'match': {'op': '==', 'left': {'payload': {'protocol': 'ip', 'field': 'saddr'}}, 'right': source}},
        {'match': {'op': '==', 'left': {'payload': {'protocol': 'ip', 'field': 'daddr'}},
                   'right': {'prefix': {'addr': '10.30.2.0', 'len': 24}}}},
        {'counter': {'packets': 0, 'bytes': 0}},
        {'accept': None}]
    if extra is not None:
        expr.insert(2, extra)
    return dict(family='ip', table='netbird', chain='netbird-rt-fwd', handle=51, expr=expr)


def output(*rules):
    return {'nftables': [{'metainfo': {'version': '1.0'}}, *({'rule': r} for r in rules)]}


class NetBirdBINATPolicyTests(unittest.TestCase):
    def test_authorized_native_rule_is_replaced_not_bypassed(self):
        command = acl.transaction(output(native_rule()), CFG)
        for value in ('replace rule ip netbird netbird-rt-fwd handle 51',
                      'ip saddr @nb-7d0b7d19', 'ct original daddr & 255.255.255.0 == 10.30.2.0',
                      'ip daddr 192.168.178.0/24', 'ct status dnat',
                      'iifname "wt0" oifname "eth0"'):
            self.assertIn(value, command)
        self.assertNotIn('add rule', command)

    def test_policy_missing_no_permit(self):
        with self.assertRaises(acl.Refused):
            acl.transaction(output(), CFG)

    def test_unknown_rule_layout_or_source_cannot_be_rewritten(self):
        invalid = (
            native_rule('@not-a-netbird-set'),
            native_rule(extra={'match': {'op': '==', 'left': {'payload': {
                'protocol': 'ip', 'field': 'protocol'}}, 'right': 6}}),
            dict(native_rule(), handle='51'))
        for rule in invalid:
            with self.subTest(rule=rule), self.assertRaises(acl.Refused):
                acl.transaction(output(rule), CFG)

    def test_policy_duplicates_are_rejected(self):
        with self.assertRaises(acl.Refused):
            acl.transaction(output(native_rule(), native_rule('@nb-ffeeddcc')), CFG)

    def test_other_customer_destination_is_not_used(self):
        rule = native_rule()
        rule['expr'][1]['match']['right']['prefix']['addr'] = '10.30.1.0'
        with self.assertRaises(acl.Refused):
            acl.transaction(output(rule), CFG)

    def test_managed_rule_noop_without_creating_second_rule(self):
        rule = native_rule()
        rule['comment'] = acl.MARKER
        rule['expr'] = [
            {'comment': acl.MARKER},
            {'match': {'op': '==', 'left': {'binop': {'op': '&',
                       'left': {'ct': {'key': 'daddr', 'dir': 'original'}},
                       'right': '255.255.255.0'}}, 'right': '10.30.2.0'}},
            {'match': {'op': '==', 'left': {'payload': {'protocol': 'ip', 'field': 'daddr'}},
                       'right': {'prefix': {'addr': '192.168.178.0', 'len': 24}}}},
            {'meta': {'key': 'iifname', 'name': 'wt0'}},
            {'meta': {'key': 'oifname', 'name': 'eth0'}}]
        self.assertEqual('', acl.transaction(output(rule), CFG))
        with self.assertRaises(acl.Refused):
            acl.transaction(output(rule, native_rule()), CFG)


if __name__ == '__main__':
    unittest.main()
