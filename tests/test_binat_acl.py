#!/usr/bin/env python3
"""Fail-closed policy compatibility tests: only rewrite a real NetBird native ACL."""
import importlib.util
import json
import os
from unittest.mock import patch
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


class ReadinessProbeTests(unittest.TestCase):
    def probe(self, rules, set_available=True):
        with patch.object(acl, 'configuration', return_value=CFG), \
             patch.object(acl, 'read_chain', return_value=rules), \
             patch.object(acl, 'nft', side_effect=(lambda *args: b'{}' if set_available else
                                                   (_ for _ in ()).throw(acl.Refused('set missing')))), \
             patch.object(acl, 'apply') as apply:
            try:
                return acl.probe()
            finally:
                apply.assert_not_called()

    def test_established_only_is_not_a_routing_grant(self):
        established = dict(family='ip', table='netbird', chain='netbird-rt-fwd',
                           handle=1, expr=[{'match': {'op': 'in',
                           'left': {'ct': {'key': 'state'}},
                           'right': ['established', 'related']}}, {'accept': None}])
        with self.assertRaises(acl.MissingPolicy):
            self.probe(output(established))

    def test_correct_native_route_and_source_set_are_ready_without_mutation(self):
        self.assertIsNone(self.probe(output(native_rule())))

    def test_route_without_source_set_is_still_pending(self):
        with self.assertRaises(acl.MissingPolicy):
            self.probe(output(native_rule()), set_available=False)

    def test_incompatible_route_format_is_not_treated_as_pending(self):
        bad = native_rule(extra={'match': {'op': '==', 'left': {
            'payload': {'protocol': 'ip', 'field': 'protocol'}}, 'right': 6}})
        with self.assertRaises(acl.Refused) as error:
            self.probe(output(bad))
        self.assertNotIsInstance(error.exception, acl.MissingPolicy)

    def test_other_customer_rule_does_not_count(self):
        wrong = native_rule()
        wrong['expr'][1]['match']['right']['prefix']['addr'] = '10.30.8.0'
        with self.assertRaises(acl.MissingPolicy):
            self.probe(output(wrong))

    def test_refresh_needs_only_new_source_set(self):
        old = managed_rule(source='@nb-7d0b7d19', handle=51)
        new = native_rule(source='@nb-ffeeddcc', handle=52)
        with patch.object(acl, 'configuration', return_value=CFG), \
             patch.object(acl, 'read_chain', return_value=output(old,new)), \
             patch.object(acl, 'nft', return_value=b'{}') as nft:
            acl.probe()
        nft.assert_called_once_with('-j','list','set','ip','netbird','nb-ffeeddcc')



class ReconciliationTests(unittest.TestCase):
    def invoke(self, observations, writes, failure=None):
        def write(command):
            writes.append(command)
            if failure and len(writes) == 1:
                raise failure
        with patch.object(acl, 'configuration', return_value=CFG), \
             patch.object(acl, 'read_chain', side_effect=observations), \
             patch.object(acl, 'apply', side_effect=write):
            acl.main()

    def test_post_apply_refresh_is_reconciled_in_same_invocation(self):
        mixed=output(managed_rule(handle=51), native_rule('@nb-ffeeddcc',handle=52))
        writes=[]
        self.invoke([output(native_rule()), mixed, mixed,
                     output(managed_rule('@nb-ffeeddcc',handle=52))], writes)
        self.assertEqual(len(writes),2)
        self.assertIn('delete rule ip netbird netbird-rt-fwd handle 51',writes[1])
        self.assertIn('replace rule ip netbird netbird-rt-fwd handle 52',writes[1])
        self.assertNotIn('ip saddr @nb-7d0b7d19',writes[1])

    def test_exhausted_generations_revoke_only_current_verified_grants(self):
        sources=['@nb-11223344','@nb-22334455','@nb-33445566','@nb-44556677']
        observations=[]
        for index in range(acl.MAX_RECONCILE_ATTEMPTS):
            observations.append(output(native_rule(sources[index],handle=60+index)))
            observations.append(output(managed_rule(sources[index],handle=60+index),
                                       native_rule(sources[index+1],handle=61+index)))
        # Fresh cleanup snapshot, not a handle saved from any previous apply.
        observations.extend([output(managed_rule(sources[2],handle=99),native_rule(sources[3],handle=100)),
                             output(native_rule(sources[3],handle=100))])
        writes=[]
        with self.assertRaises(acl.Refused):self.invoke(observations,writes)
        self.assertEqual(len(writes),acl.MAX_RECONCILE_ATTEMPTS+1)
        self.assertEqual(writes[-1],'delete rule ip netbird netbird-rt-fwd handle 99\n')
        self.assertNotIn('replace',writes[-1])

    def test_disappeared_handle_never_replays_stale_batch(self):
        writes=[]
        self.invoke([output(native_rule(handle=51)),
                     output(native_rule('@nb-ffeeddcc',handle=80)),
                     output(managed_rule('@nb-ffeeddcc',handle=80))],writes,
                    acl.Refused('synthetic handle vanished'))
        self.assertEqual(len(writes),2)
        self.assertIn('handle 80',writes[1]);self.assertNotIn('handle 51',writes[1])

    def test_unknown_native_layout_retires_known_managed_only(self):
        unknown=native_rule(extra={'drop':None},handle=80)
        foreign=managed_rule(handle=90);foreign['expr'][2]['match']=None
        mixed=output(managed_rule(handle=51),unknown,foreign)
        writes=[]
        with self.assertRaises(acl.Refused):
            self.invoke([mixed]*acl.MAX_RECONCILE_ATTEMPTS+[mixed,output(unknown,foreign)],writes)
        self.assertEqual(writes,['delete rule ip netbird netbird-rt-fwd handle 51\n'])
        self.assertFalse(acl.managed(foreign,CFG))

    def test_customer_group_loss_revokes_and_never_rewrites_peer_only(self):
        mixed=output(managed_rule(),native_rule('100.64.0.11',handle=52))
        peer=output(native_rule('100.64.0.11',handle=52))
        writes=[]
        with self.assertRaises(acl.Refused):
            self.invoke([mixed,peer,peer,peer,peer],writes)
        self.assertEqual(writes,['delete rule ip netbird netbird-rt-fwd handle 51\n'])

    def test_set_turnover_failure_still_deletes_known_permits(self):
        mixed=output(managed_rule(),native_rule('@nb-ffeeddcc',handle=52))
        batches=[]
        def nft(*args):
            if args[:3] == ('-j','list','set'):
                raise acl.Refused('synthetic set unavailable')
            if args[:1] == ('-f',):
                batches.append(Path(args[-1]).read_text())
            return b''
        with patch.object(acl,'configuration',return_value=CFG), \
             patch.object(acl,'read_chain',side_effect=[mixed]*4+[output(native_rule('@nb-ffeeddcc',handle=52))]), \
             patch.object(acl,'nft',side_effect=nft):
            with self.assertRaises(acl.Refused):acl.main()
        self.assertEqual(batches,['delete rule ip netbird netbird-rt-fwd handle 51\n'])

    def test_unavailable_kernel_is_bounded_and_revocation_not_confirmed(self):
        with patch.object(acl,'configuration',return_value=CFG), \
             patch.object(acl,'read_chain',side_effect=acl.Refused('synthetic read failure')) as read:
            with self.assertRaises(acl.RevocationFailed):acl.main()
        self.assertEqual(read.call_count,2*acl.MAX_RECONCILE_ATTEMPTS)

    def test_noop_is_verified_and_creates_no_batch(self):
        writes=[];self.invoke([output(managed_rule())]*2,writes)
        self.assertEqual(writes,[])

    def test_cleanup_retries_vanished_handles_and_rejects_ambiguity(self):
        for rules in (output(managed_rule(handle='51')),
                      output(managed_rule(handle=51),native_rule(handle=51))):
            with self.assertRaises(acl.Refused):acl.revocation_transaction(rules,CFG)
        batches=[]
        def write(command):
            batches.append(command)
            if len(batches)==1:raise acl.Refused('synthetic vanished handle')
        with patch.object(acl,'read_chain',side_effect=[output(managed_rule(handle=51)),
                        output(managed_rule(handle=99)),output()]), \
             patch.object(acl,'apply',side_effect=write):acl.revoke(CFG)
        self.assertEqual(batches,['delete rule ip netbird netbird-rt-fwd handle 51\n',
                                 'delete rule ip netbird netbird-rt-fwd handle 99\n'])

    def test_apply_validates_sets_private_batch_and_cleans_on_check_failure(self):
        paths=[];calls=[]
        def nft(*args):
            calls.append(args)
            if '-f' in args:
                path=Path(args[-1]);paths.append(path)
                self.assertEqual(path.stat().st_mode & 0o777,0o600)
                self.assertEqual(path.stat().st_uid,os.geteuid())
                raise acl.Refused('synthetic validation failure')
            return b'{}'
        with patch.object(acl,'nft',side_effect=nft):
            with self.assertRaises(acl.Refused):acl.apply(acl.transaction(output(native_rule()),CFG))
        self.assertEqual(calls[0],('-j','list','set','ip','netbird','nb-7d0b7d19'))
        self.assertEqual(len(paths),1);self.assertFalse(paths[0].exists())
        self.assertFalse(any(args[0]=='-f' for args in calls))


if __name__ == '__main__':
    unittest.main()
