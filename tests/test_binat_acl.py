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


def native_rule(source='@nb-7d0b7d19', extra=None, handle=51):
    expr = [
        {'match': {'op': '==', 'left': {'payload': {'protocol': 'ip', 'field': 'saddr'}}, 'right': source}},
        {'match': {'op': '==', 'left': {'payload': {'protocol': 'ip', 'field': 'daddr'}},
                   'right': {'prefix': {'addr': '10.30.2.0', 'len': 24}}}},
        {'counter': {'packets': 0, 'bytes': 0}},
        {'accept': None}]
    if extra is not None:
        expr.insert(2, extra)
    return dict(family='ip', table='netbird', chain='netbird-rt-fwd', handle=handle, expr=expr)


def managed_rule(source='@nb-7d0b7d19', handle=51):
    rule = native_rule(source=source, handle=handle)
    rule['comment'] = acl.MARKER
    rule['expr'] = [
        {'match': {'op': '==', 'left': {'meta': {'key': 'iifname'}}, 'right': 'wt0'}},
        {'match': {'op': '==', 'left': {'meta': {'key': 'oifname'}}, 'right': 'eth0'}},
        {'match': {'op': '==', 'left': {'payload': {'protocol': 'ip', 'field': 'saddr'}}, 'right': source}},
        {'match': {'op': 'in', 'left': {'ct': {'key': 'status'}}, 'right': 'dnat'}},
        {'match': {'op': '==', 'left': {'ct': {'dir': 'original', 'key': 'daddr'}},
                   'right': {'prefix': {'addr': '10.30.2.0', 'len': 24}}}},
        {'match': {'op': '==', 'left': {'payload': {'protocol': 'ip', 'field': 'daddr'}},
                   'right': {'prefix': {'addr': '192.168.178.0', 'len': 24}}}},
        {'counter': {'packets': 0, 'bytes': 0}}, {'accept': None}]
    return rule


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

    def test_two_native_backup_rules_are_preserved_atomically(self):
        # CT 111 has a customer source set and a separate primary router IP.
        rules = output(native_rule(handle=38),
                       native_rule('100.107.35.72', handle=39))
        command = acl.transaction(rules, CFG)
        lines = command.strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn('handle 38', lines[0])
        self.assertIn('ip saddr @nb-7d0b7d19', lines[0])
        self.assertIn('handle 39', lines[1])
        self.assertIn('ip saddr 100.107.35.72', lines[1])
        for line in lines:
            self.assertIn('ct status dnat', line)
            self.assertIn('ct original daddr & 255.255.255.0 == 10.30.2.0', line)
            self.assertIn('ip daddr 192.168.178.0/24', line)
            self.assertNotIn('add rule', line)

    def test_duplicate_or_unsafe_sources_are_rejected(self):
        invalid = (
            output(native_rule(), native_rule(handle=52)),
            output(native_rule(), native_rule('100.107.35.72', handle=51)),
            output(native_rule('100.107.35.72')),
            output(native_rule(), native_rule('192.168.178.1', handle=52)),
            output(native_rule(), native_rule('0.0.0.0/0', handle=52)),
            output(native_rule(), native_rule('100.107.35.72;flush ruleset', handle=52)),
        )
        for rules in invalid:
            with self.subTest(rules=rules), self.assertRaises(acl.Refused):
                acl.transaction(rules, CFG)

    def test_two_managed_backup_rules_are_clean_noop(self):
        native = native_rule()
        native['comment'] = acl.MARKER
        native['expr'] = [
            {'match': {'op': '==', 'left': {'meta': {'key': 'iifname'}}, 'right': 'wt0'}},
            {'match': {'op': '==', 'left': {'meta': {'key': 'oifname'}}, 'right': 'eth0'}},
            {'match': {'op': '==', 'left': {'payload': {'protocol': 'ip', 'field': 'saddr'}}, 'right': '@nb-7d0b7d19'}},
            {'match': {'op': 'in', 'left': {'ct': {'key': 'status'}}, 'right': 'dnat'}},
            {'match': {'op': '==', 'left': {'ct': {'dir': 'original', 'key': 'daddr'}},
                       'right': {'prefix': {'addr': '10.30.2.0', 'len': 24}}}},
            {'match': {'op': '==', 'left': {'payload': {'protocol': 'ip', 'field': 'daddr'}},
                       'right': {'prefix': {'addr': '192.168.178.0', 'len': 24}}}},
            {'counter': {'packets': 0, 'bytes': 0}}, {'accept': None}]
        peer = {**native, 'handle': 39, 'expr': list(native['expr'])}
        peer['expr'][2] = {'match': {'op': '==',
            'left': {'payload': {'protocol': 'ip', 'field': 'saddr'}},
            'right': '100.107.35.72'}}
        self.assertEqual('', acl.transaction(output(native, peer), CFG))
        revoked = acl.transaction(output(native, native_rule('100.107.35.72', handle=39)), CFG)
        self.assertEqual(revoked.strip(), 'delete rule ip netbird netbird-rt-fwd handle 51')
        with self.assertRaises(acl.Refused):
            acl.transaction(output(native_rule('100.107.35.72', handle=39)), CFG)

    def test_mixed_policy_refresh_revokes_stale_managed_acl(self):
        old = managed_rule(source='@nb-7d0b7d19', handle=51)
        new = native_rule(source='@nb-ffeeddcc', handle=52)
        command = acl.transaction(output(old, new), CFG)
        self.assertEqual(command.count('delete rule '), 1)
        self.assertEqual(command.count('replace rule '), 1)
        self.assertIn('delete rule ip netbird netbird-rt-fwd handle 51', command)
        self.assertIn('replace rule ip netbird netbird-rt-fwd handle 52', command)
        self.assertNotIn('ip saddr @nb-7d0b7d19', command)
        self.assertIn('ip saddr @nb-ffeeddcc', command)

    def test_mixed_refresh_without_customer_group_only_revokes_stale_acl(self):
        old = managed_rule(handle=51)
        native_peer = native_rule('100.107.35.72', handle=52)
        command = acl.transaction(output(old, native_peer), CFG)
        self.assertEqual(command.strip(), 'delete rule ip netbird netbird-rt-fwd handle 51')
        with self.assertRaises(acl.Refused):
            acl.transaction(output(native_peer), CFG)

    def test_mixed_refresh_with_conflicting_rule_stays_unmodified(self):
        old=managed_rule(handle=51)
        bad=native_rule(source='0.0.0.0/0',handle=52)
        with self.assertRaises(acl.Refused):
            acl.transaction(output(old,bad),CFG)

    def test_other_customer_destination_is_not_used(self):
        rule = native_rule()
        rule['expr'][1]['match']['right']['prefix']['addr'] = '10.30.1.0'
        with self.assertRaises(acl.Refused):
            acl.transaction(output(rule), CFG)

    def test_managed_rule_noop_without_creating_second_rule(self):
        rule = native_rule()
        rule['comment'] = acl.MARKER
        rule['expr'] = [
            {'match': {'op': '==', 'left': {'meta': {'key': 'iifname'}}, 'right': 'wt0'}},
            {'match': {'op': '==', 'left': {'meta': {'key': 'oifname'}}, 'right': 'eth0'}},
            {'match': {'op': '==', 'left': {'payload': {'protocol': 'ip', 'field': 'saddr'}}, 'right': '@nb-7d0b7d19'}},
            {'match': {'op': 'in', 'left': {'ct': {'key': 'status'}}, 'right': 'dnat'}},
            {'match': {'op': '==', 'left': {'ct': {'key': 'daddr', 'dir': 'original'}},
                       'right': {'prefix': {'addr': '10.30.2.0', 'len': 24}}}},
            {'match': {'op': '==', 'left': {'payload': {'protocol': 'ip', 'field': 'daddr'}},
                       'right': {'prefix': {'addr': '192.168.178.0', 'len': 24}}}},
            {'counter': {'packets': 0, 'bytes': 0}},
            {'accept': None}]
        self.assertEqual('', acl.transaction(output(rule), CFG))
        mixed = acl.transaction(output(rule, native_rule(source='@nb-ffeeddcc', handle=52)), CFG)
        self.assertIn('delete rule ip netbird netbird-rt-fwd handle 51', mixed)
        self.assertIn('replace rule ip netbird netbird-rt-fwd handle 52', mixed)


if __name__ == '__main__':
    unittest.main()
