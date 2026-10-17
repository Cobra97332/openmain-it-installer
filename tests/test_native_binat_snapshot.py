"""Reference contract mocks, not native NetBird integration tests."""
import importlib.util
from pathlib import Path
import threading
import unittest

path = Path(__file__).resolve().parents[1] / 'netbird-router/experimental/native-binat/snapshot.py'
spec = importlib.util.spec_from_file_location('snapshot', path)
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)
SCOPE = dict(virtual='10.80.1.0/24', real='192.168.80.0/24', overlay='nb0', lan='lan0')
RULE = dict(source='100.64.0.10/32', destination='10.80.1.0/24', protocol='all', ports=[], action='accept')


class SnapshotTests(unittest.TestCase):
    def test_deny_precedence_and_original_protocol_ports(self):
        deny = dict(RULE, protocol='tcp', ports=[[443,443]], action='drop')
        result = snapshot.compile_rules([RULE,deny], [SCOPE])
        self.assertLess(result.index('drop'),result.index('accept'))
        self.assertIn('ct original proto-dst 443-443',result)
        self.assertNotIn('established accept',result)
        self.assertNotIn('mark',result)

    def test_complete_input_validation_closes_existing_grant(self):
        scripts=[];owner=snapshot.Publisher([SCOPE],scripts.append)
        owner.publish(1,[RULE])
        for n,bad in enumerate([{},dict(RULE,action='unknown'),dict(RULE,ports=[[True,443]]),dict(RULE,protocol='icmp',ports=[[1,2]]),dict(RULE,source='0.0.0.0/0')],2):
            with self.assertRaises(snapshot.Rejected):owner.publish(n,[bad])
            self.assertEqual(scripts[-1],f'flush chain ip {snapshot.TABLE} current\n')

    def test_stale_call_and_native_failure_remain_closed(self):
        scripts=[];owner=snapshot.Publisher([SCOPE],scripts.append)
        owner.publish(2,[RULE])
        with self.assertRaises(snapshot.Rejected):owner.publish(1,[RULE])
        def failed():raise RuntimeError('synthetic backend failure')
        with self.assertRaises(RuntimeError):owner.publish(3,[RULE],failed)
        self.assertEqual(scripts[-1],f'flush chain ip {snapshot.TABLE} current\n')
        with self.assertRaises(snapshot.Rejected):owner.publish(3,[RULE])
        owner.publish(4,[RULE])

    def test_concurrent_requests_serialize_close_before_native_work(self):
        scripts=[];entered=threading.Event();release=threading.Event()
        owner=snapshot.Publisher([SCOPE],scripts.append)
        def apply():
            self.assertEqual(scripts[-1],f'flush chain ip {snapshot.TABLE} current\n')
            entered.set();self.assertTrue(release.wait(3))
        t=threading.Thread(target=lambda:owner.publish(1,[RULE],apply))
        t.start();self.assertTrue(entered.wait(3))
        other=threading.Thread(target=lambda:owner.publish(2,[]))
        other.start();release.set();t.join(3);other.join(3)
        self.assertFalse(t.is_alive());self.assertFalse(other.is_alive())
        self.assertEqual(scripts[-1],f'flush chain ip {snapshot.TABLE} current\n')

    def test_ambiguous_virtual_scopes_rejected_real_overlap_supported(self):
        with self.assertRaises(snapshot.Rejected):snapshot.Publisher([SCOPE,SCOPE])
        snapshot.Publisher([SCOPE,dict(SCOPE,virtual='10.81.1.0/24',overlay='backup0')])

    def test_commit_with_lost_acknowledgment_recloses(self):
        state={'open':False}
        def uncertain(script):
            state['open']='add rule' in script
            if state['open']:raise TimeoutError('synthetic lost ACK after commit')
        owner=snapshot.Publisher([SCOPE],uncertain)
        with self.assertRaisesRegex(snapshot.Rejected,'closure confirmed'):owner.publish(1,[RULE])
        self.assertFalse(state['open'])

    def test_failed_reclosure_is_explicitly_unconfirmed(self):
        state={'open':False,'calls':0}
        def uncertain(script):
            state['calls']+=1
            if state['calls']==2:
                state['open']=True
                raise TimeoutError('synthetic ACK loss')
            if state['calls']==3:raise OSError('synthetic close rejection')
        owner=snapshot.Publisher([SCOPE],uncertain)
        with self.assertRaisesRegex(snapshot.ClosureUnconfirmed,'closure unconfirmed'):owner.publish(1,[RULE])
        self.assertTrue(state['open'])  # Never falsely assert failure means denial.

    def test_unconfirmed_kernel_close_aborts_before_native_processing(self):
        def reject(script):raise OSError('synthetic kernel rejection')
        owner=snapshot.Publisher([SCOPE],reject)
        calls=[]
        with self.assertRaises(OSError):owner.publish(1,[RULE],lambda:calls.append(True))
        self.assertEqual(calls,[])
        self.assertEqual(owner.ticket,-1)
        # This proves failure reporting, NOT withdrawal of an existing grant.

    def test_no_installer_bundle_integration(self):
        bundle=(path.parents[2]/'bundle.files').read_text()
        self.assertNotIn('experimental',bundle)
