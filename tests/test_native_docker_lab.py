"""Lab isolation/report contracts. These mocks are NOT real NetBird tests."""
import importlib.util
import json
from pathlib import Path
import tempfile
import shutil
import subprocess
import unittest
from unittest.mock import Mock, patch

DIR = Path(__file__).resolve().parents[1] / 'netbird-router/experimental/native-binat/docker-lab'


def load(name):
    spec = importlib.util.spec_from_file_location('native_lab_' + name, DIR / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lab = load('lab')
packets = load('packets')
container = load('container')
traffic = load('traffic')
related = load('related')
passive = load('passive')


class NativeDockerLabTests(unittest.TestCase):
    def test_crossed_protocol_denials_are_required_before_marking_case_passed(self):
        for protocols, port in ((('tcp',), 8081), (('udp',), 8080)):
            def deny_failure(*args, **kwargs):
                if kwargs.get('protocols') == protocols and kwargs.get('port') == port and kwargs.get('allowed') is False:
                    raise RuntimeError('lab_unauthorized_crossed_pair')
            result = {}
            with patch.object(packets, 'observe', return_value={}), patch.object(packets, 'wait_publication'), \
                 patch.object(packets, 'ping'), patch.object(packets, 'probe', side_effect=deny_failure):
                with self.assertRaisesRegex(RuntimeError, '^lab_unauthorized_crossed_pair$'):
                    packets.extended_policy_cases(Mock(), result)
            self.assertNotIn('icmp_port_protocol_restrictions', result)

    def test_passive_silence_cannot_pass_without_post_barrier_wire_emission(self):
        instance = Mock()
        for proof in ({'tcp': 101, 'udp': 99}, {'tcp': 101}, {'tcp': True, 'udp': 101}):
            instance.exec.return_value = json.dumps(proof)
            with self.assertRaisesRegex(RuntimeError, '^lab_passive_sender_unproven$'):
                packets.prove_passive_sender(instance, 100)
        instance.exec.return_value = '{"tcp":101,"udp":102}'
        packets.prove_passive_sender(instance, 100)

    def test_wire_proof_requires_the_sender_protocol_and_framed_timestamp(self):
        import socket
        import struct
        ip = bytearray(20); ip[0] = 0x45; ip[9] = 17
        ip[12:16] = socket.inet_aton('192.168.88.100')
        ethernet = b'\0' * 12 + b'\x08\x00'
        udp = struct.pack('!HHHH', 8085, 50000, 40, 0)
        payload = b'openmain-passive:12345\n'
        self.assertEqual(passive.timestamp(ethernet + ip + udp + payload), ('udp', 12345))
        self.assertIsNone(passive.timestamp(ethernet + ip + udp + payload[:-1]))
        wrong_port = struct.pack('!HHHH', 8081, 50000, 40, 0)
        self.assertIsNone(passive.timestamp(ethernet + ip + wrong_port + payload))

    def test_exec_lookup_and_command_share_a_remaining_budget(self):
        instance = lab.Lab.__new__(lab.Lab)
        instance.compose = Mock(return_value=Mock(stdout='synthetic-container'))
        with patch.object(lab.time, 'monotonic', side_effect=[0, 0, 24.9]), \
             patch.object(lab, 'docker', return_value=Mock(returncode=0, stdout='{}')) as command:
            instance.exec('peer', 'synthetic-command', timeout=25)
        self.assertEqual(instance.compose.call_args.kwargs['timeout'], 25)
        self.assertAlmostEqual(command.call_args.kwargs['timeout'], 0.1)
        with patch.object(lab.time, 'monotonic', side_effect=[0, 0, 25]), patch.object(lab, 'docker') as command:
            with self.assertRaises(subprocess.TimeoutExpired):
                instance.exec('peer', 'synthetic-command', timeout=25)
        command.assert_not_called()

    def test_policy_sources_omitted_by_controller_formatter_remain_a_failure(self):
        rule = {'sources': ['source'], 'destinations': ['target']}
        with patch.object(container, 'api', return_value={'enabled': True, 'rules': [dict(rule, sources=None)]}):
            with self.assertRaisesRegex(RuntimeError, '^lab_policy_readback_mismatch$'):
                container.verify_lab_policy('synthetic', [rule])
    def test_policy_readback_normalizes_group_objects_and_refuses_mismatch(self):
        state = {'policies': {'a': 'lab-policy'}}
        rule = {'action': 'accept', 'protocol': 'tcp', 'bidirectional': False,
                'sources': ['source'], 'destinations': ['target'], 'ports': ['8080']}
        observed = dict(rule, sources=[{'id': 'source', 'name': 'synthetic'}],
                        destinations=[{'id': 'target'}])
        with patch.object(container, 'api', side_effect=[{}, {'enabled': True, 'rules': [observed]}]) as api:
            container.replace_lab_policy(state, 'a', [rule])
        self.assertEqual(api.call_args_list[0].args[:2], ('/api/policies/lab-policy', 'PUT'))
        with patch.object(container, 'api', side_effect=[{}, {'enabled': True, 'rules': [dict(observed, ports=['8082'])]}]):
            with self.assertRaisesRegex(RuntimeError, '^lab_policy_readback_mismatch$'):
                container.replace_lab_policy(state, 'a', [rule])

    def test_extra_icmp_policy_has_one_mutation_and_strict_enabled_readback(self):
        state = {'allowed': 'source', 'dest': {'a': 'target'}}
        actual = {'enabled': True, 'rules': [{'enabled': True, 'action': 'accept', 'protocol': 'icmp',
            'bidirectional': True, 'sources': [{'id': 'source'}], 'destinations': [{'id': 'target'}]}]}
        with patch.object(container.Path, 'read_text', return_value=json.dumps(state)), \
             patch.object(container, 'policy', return_value='new-lab-id') as create, \
             patch.object(container, 'api', return_value=actual) as api, patch.object(container, 'save_objects') as save:
            container.mutate({'operation': 'extra_icmp'})
        self.assertEqual(create.call_args.kwargs['protocol'], 'icmp')
        api.assert_called_once_with('/api/policies/new-lab-id')  # GET, no second PUT
        self.assertEqual(save.call_args.args[0]['protocol_policies'], {'icmp': 'new-lab-id'})

    def test_related_denial_requires_kernel_related_classification(self):
        session = Mock()
        session.exchange.return_value = False
        with patch.object(packets, 'observe', return_value={'related_packets': 0}):
            with self.assertRaisesRegex(RuntimeError, '^lab_related_classification_missing$'):
                packets.related_error(Mock(), session, False)
        session.exchange.return_value = True
        with patch.object(packets, 'observe', return_value={'related_packets': 0}):
            with self.assertRaisesRegex(RuntimeError, '^lab_related_delivery_mismatch$'):
                packets.related_error(Mock(), session, False)

    def test_related_capture_checks_parent_original_tuple(self):
        import socket
        import struct
        outer = bytearray(20); outer[0] = 0x45; outer[9] = 1
        quote = bytearray(20); quote[0] = 0x45; quote[9] = 17
        quote[16:20] = socket.inet_aton('198.18.10.100')
        packet = bytes(outer) + b'\x03\x03' + b'\0' * 6 + bytes(quote) + struct.pack('!HHHH', 49152, 8086, 32, 0)
        self.assertTrue(related.matches(packet, '198.18.10.100', 49152))
        self.assertFalse(related.matches(packet, '198.18.20.100', 49152))
        self.assertFalse(related.matches(packet, '198.18.10.100', 49153))
        self.assertFalse(related.matches(packet[:55], '198.18.10.100', 49152))

    def test_same_identity_resume_never_adds_a_setup_key(self):
        def read_public(path):
            if str(path) == '/opt/native/patch.sha256':
                return 'test-patch'
            if str(path) == '/opt/native/upstream.sha':
                return 'fca64287cf51a85552a41b022013abbfdb335452'
            return ''
        for opt_in in (True, False):
            with tempfile.TemporaryDirectory() as d:
                log = Path(d) / 'private.log'
                real_open = container.os.open
                def open_log(path, flags, mode=0o777):
                    return real_open(log, flags, mode)
                with patch.object(container.Path, 'read_text', read_public), \
                     patch.object(container.Path, 'exists', return_value=True), \
                     patch.object(container.Path, 'write_text'), patch.object(container, 'lan_dev', return_value='eth1'), \
                     patch.object(container.os, 'open', side_effect=open_log), patch.object(container.subprocess, 'Popen') as launch, \
                     patch.object(container, 'cmd') as command:
                    container.start('primary-b', 'test-patch', resume=True, opt_in=opt_in)
                args = launch.call_args
                self.assertNotIn('--setup-key-file', args.args[0])
                env = args.kwargs['env']
                self.assertEqual('NB_EXPERIMENTAL_BINAT_CONFIG' in env, opt_in)
                command.assert_not_called()  # no address or firewall reinitialization

    def test_failed_restart_cannot_be_reported_as_kernel_publication(self):
        stale = {'tun_exists': False, 'open': False, 'publication_count': 3}
        with patch.object(packets, 'observe', return_value=stale), \
             patch.object(packets.time, 'monotonic', side_effect=[0, 0, 31]), patch.object(packets.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, '^lab_restart_publication_missing$'):
                packets.wait_router('lab', 'primary-b', lambda s: s['tun_exists'] and s['open']
                                    and s['publication_count'] > 3, 'lab_restart_publication_missing')

    def test_full_client_contract_failure_stops_before_enrollment(self):
        from contextlib import redirect_stdout
        import io
        for controller_only in (False, True):
            instance = Mock()
            instance.cleanup.return_value = True
            def control(operation):
                if operation == 'api_contract':
                    raise RuntimeError('lab_controller_policy_contract')
                return {'bootstrap': 'pass'}
            instance.control.side_effect = control
            image = Mock(returncode=0, stdout='synthetic-image')
            with tempfile.TemporaryDirectory() as d:
                report = Path(d) / 'report.json'
                argv = ['lab.py', '--report', str(report)]
                if controller_only:
                    argv.append('--controller-only')
                work = Path(d) / 'work'
                work.mkdir()
                with patch.object(lab.sys, 'argv', argv), patch.dict(lab.os.environ, {'OPENMAIN_ISOLATED_TEST': '1'}), \
                     patch.object(lab.tempfile, 'mkdtemp', return_value=str(work)), \
                     patch.object(lab, 'Lab', return_value=instance), patch.object(lab, 'docker', return_value=image), \
                     redirect_stdout(io.StringIO()):
                    self.assertEqual(lab.main(), 1)
                self.assertEqual([c.args[0] for c in instance.control.call_args_list], ['bootstrap', 'api_contract'])
                instance.exec.assert_not_called()
                data = json.loads(report.read_text())
                self.assertFalse(data['real_clients_executed'])
                self.assertEqual(data['release'], 'blocked')

    def test_controller_policy_contract_does_not_duplicate_setup_keys(self):
        response = {'rules': [{'action': 'drop', 'ports': ['8443']}]}
        with patch.object(container, 'group', side_effect=['source', 'destination']), \
             patch.object(container, 'policy', return_value='policy'), \
             patch.object(container, 'api', return_value=response), patch.object(container, 'keys') as keys:
            self.assertEqual(container.api_contract(), {'real_controller_accepts_drop_ports': 'pass'})
            keys.assert_not_called()

    def test_positive_timeout_is_bounded_but_negative_timeout_is_not_accepted(self):
        slow, tcp, udp = Mock(), Mock(), Mock()
        slow.exchange.side_effect = RuntimeError('lab_packet_probe_timeout')
        tcp.exchange.return_value = udp.exchange.return_value = True
        with patch.object(packets, 'Session', side_effect=[slow, tcp, udp]), patch.object(packets.time, 'sleep'):
            packets.probe('isolated-lab')
        with patch.object(packets, 'Session', return_value=slow):
            with self.assertRaisesRegex(RuntimeError, '^lab_packet_probe_timeout$'):
                packets.probe('isolated-lab', allowed=False)

    def test_foreign_fixture_has_real_hooks_and_filter_table(self):
        text = container.translation_script('198.18.10.0/24', 'eth1')
        self.assertIn('chain marker { type filter hook forward', text)
        self.assertIn('table ip filter', text)
        self.assertIn('chain INPUT { type filter hook input', text)
        self.assertIn('chain FORWARD { type filter hook forward', text)

    def test_foreign_reference_includes_filter_table_mutations(self):
        with patch.object(traffic, 'nft', side_effect=[{'nftables': []}, None]):
            self.assertIsNone(traffic.foreign_state())
        with patch.object(traffic, 'nft', side_effect=[{'nftables': []}, {'nftables': []}]):
            self.assertEqual(set(traffic.foreign_state()), {'external', 'filter'})

    def test_unauthorized_packet_is_never_retried_until_denial(self):
        session = Mock()
        session.exchange.return_value = True
        with patch.object(packets, 'Session', return_value=session) as factory:
            with self.assertRaisesRegex(RuntimeError, '^lab_unauthorized_tcp_reply_observed$'):
                packets.probe('isolated-lab', allowed=False)
        factory.assert_called_once()
        session.close.assert_called_once()

    def test_positive_transport_setup_can_wait_without_relaxing_permissions(self):
        first, tcp, udp = Mock(), Mock(), Mock()
        first.exchange.return_value = False
        tcp.exchange.return_value = udp.exchange.return_value = True
        with patch.object(packets, 'Session', side_effect=[first, tcp, udp]), patch.object(packets.time, 'sleep'):
            packets.probe('isolated-lab')
        first.close.assert_called_once()
        tcp.close.assert_called_once()
        udp.close.assert_called_once()

    def test_authorized_transport_failure_remains_a_mandatory_failure(self):
        session = Mock()
        session.exchange.return_value = False
        with patch.object(packets, 'Session', return_value=session), patch.object(packets.time, 'monotonic', side_effect=[0, 26]):
            with self.assertRaisesRegex(RuntimeError, '^lab_authorized_tcp_reply_missing$'):
                packets.probe('isolated-lab')

    def test_positive_deadline_is_checked_before_another_helper_after_sleep(self):
        session = Mock()
        session.exchange.side_effect = RuntimeError('lab_packet_probe_timeout')
        # First exchange finishes just before the deadline. The capped sleep
        # reaches it; a second Docker exec must never be started.
        with patch.object(packets, 'Session', return_value=session) as factory, \
             patch.object(packets.time, 'monotonic', side_effect=[0, 0, 24.9, 25]), \
             patch.object(packets.time, 'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, '^lab_authorized_tcp_reply_missing$'):
                packets.probe('isolated-lab')
        factory.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 0.1)
        session.close.assert_called_once()

    def test_positive_probe_cleanup_cannot_start_an_attempt_after_deadline(self):
        session = Mock()
        session.exchange.return_value = False
        with patch.object(packets, 'Session', return_value=session) as factory, \
             patch.object(packets.time, 'monotonic', side_effect=[0, 0, 26]):
            with self.assertRaisesRegex(RuntimeError, '^lab_authorized_tcp_reply_missing$'):
                packets.probe('isolated-lab')
        factory.assert_called_once()
        session.close.assert_called_once()

    def test_session_io_and_docker_lookup_share_remaining_budget(self):
        import select
        process = Mock()
        process.stdout.readline.return_value = '{"reply":true}\n'
        instance = Mock()
        instance.compose.return_value.stdout = 'synthetic-container'
        with patch.object(packets.time, 'monotonic', return_value=24) as clock, \
             patch.object(packets.subprocess, 'Popen', return_value=process), \
             patch.object(packets.os, 'read', return_value=b'{"reply":true}\n'), \
             patch.object(select, 'select', return_value=([process.stdout], [], [])) as wait:
            session = packets.Session(instance, 'peer', '198.18.10.100', 'tcp', deadline=25)
            clock.return_value = 24.8
            self.assertTrue(session.exchange())
        self.assertEqual(instance.compose.call_args.kwargs['timeout'], 1)
        self.assertAlmostEqual(wait.call_args.args[3], 0.2)
        with patch.object(packets.time, 'monotonic', return_value=25):
            with self.assertRaisesRegex(RuntimeError, '^lab_packet_probe_timeout$'):
                session.exchange()
        self.assertEqual(process.stdin.write.call_count, 1)

    def test_docker_lookup_timeout_is_a_probe_failure(self):
        instance = Mock()
        instance.compose.side_effect = subprocess.TimeoutExpired('docker', 1)
        with patch.object(packets.time, 'monotonic', return_value=24):
            with self.assertRaisesRegex(RuntimeError, '^lab_packet_probe_timeout$'):
                packets.Session(instance, 'peer', '198.18.10.100', 'tcp', deadline=25)

    def test_lookup_returning_at_deadline_cannot_launch_packet_helper(self):
        instance = Mock()
        instance.compose.return_value.stdout = 'synthetic-container'
        with patch.object(packets.time, 'monotonic', side_effect=[24, 25]), \
             patch.object(packets.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(RuntimeError, '^lab_packet_probe_timeout$'):
                packets.Session(instance, 'peer', '198.18.10.100', 'tcp', deadline=25)
        launch.assert_not_called()

    def test_foreign_reference_detects_startup_changes_not_only_later_changes(self):
        initial = {'nftables': [{'table': {'name': 'lab_foreign', 'family': 'inet'}}]}
        changed = {'nftables': initial['nftables'] + [{'rule': {'chain': 'forward', 'expr': [{'accept': None}]}}]}
        with tempfile.TemporaryDirectory() as d, patch.object(traffic, 'FOREIGN_REFERENCE', Path(d) / 'reference'):
            with patch.object(traffic, 'foreign_state', return_value=initial):
                traffic.record_foreign_reference()
            self.assertTrue(traffic.foreign_matches_reference(initial))
            self.assertFalse(traffic.foreign_matches_reference(changed))
            traffic.FOREIGN_REFERENCE.chmod(0o644)
            self.assertFalse(traffic.foreign_matches_reference(initial))

    def test_foreign_reference_cannot_be_overwritten_or_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as d, patch.object(traffic, 'FOREIGN_REFERENCE', Path(d) / 'reference'):
            target = Path(d) / 'other'
            target.write_text('unrelated')
            traffic.FOREIGN_REFERENCE.symlink_to(target)
            self.assertFalse(traffic.foreign_matches_reference({'nftables': []}))
            with patch.object(traffic, 'foreign_state', return_value={'nftables': []}):
                with self.assertRaises(FileExistsError):
                    traffic.record_foreign_reference()
            self.assertEqual(target.read_text(), 'unrelated')

    def test_client_build_digest_binds_native_and_dependency_patches(self):
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            native, dependency = Path(d) / 'native.patch', Path(d) / 'dependency.patch'
            native.write_bytes(b'native')
            dependency.write_bytes(b'ABI-u8')
            with patch.object(lab, 'PATCH', native), patch.object(lab, 'DEPENDENCY_PATCH', dependency):
                first = lab.patchset_digest()
                wire = hashlib.sha256(b'native').hexdigest() + '\n' + hashlib.sha256(b'ABI-u8').hexdigest() + '\n'
                self.assertEqual(first, hashlib.sha256(wire.encode()).hexdigest())
                dependency.write_bytes(b'other ABI')
                self.assertNotEqual(first, lab.patchset_digest())

    def test_each_withdrawal_replaces_old_sessions_and_proves_fresh_exchanges(self):
        old = Mock()
        tcp, udp = Mock(), Mock()
        tcp.exchange.return_value = udp.exchange.return_value = True
        sessions = [old]
        with patch.object(packets, 'Session', side_effect=[tcp, udp]) as factory:
            packets.establish_sessions('isolated-lab', sessions)
        old.close.assert_called_once()
        old.exchange.assert_not_called()
        self.assertEqual(sessions, [tcp, udp])
        self.assertEqual([c.args[-1] for c in factory.call_args_list], ['tcp', 'udp'])
        tcp.exchange.assert_called_once()
        udp.exchange.assert_called_once()

    def test_unusable_new_session_cannot_prove_established_withdrawal(self):
        bad = Mock()
        bad.exchange.return_value = False
        sessions = []
        with patch.object(packets, 'Session', return_value=bad):
            with self.assertRaisesRegex(RuntimeError, '^lab_established_connection_missing$'):
                packets.establish_sessions('isolated-lab', sessions)
        self.assertEqual(sessions, [bad])  # caller finally still closes it

    def test_outage_failure_classes_are_preserved_without_raw_diagnostics(self):
        for reason in ('lab_controller_loss_gate_remained_open',
                       'lab_controller_loss_existing_flow_allowed', 'lab_controller_reconnect_missing'):
            value = lab.safe_summary({'stage': 'packet_gates',
                         'cases': {'real_client_gate_failure': reason}}, 1)
            self.assertEqual(value['packet_error_class'], reason)

    def test_unconfirmed_policy_error_is_classified_without_private_context(self):
        text = 'BINAT native gate closure unconfirmed synthetic-private-material'
        with patch.object(traffic.Path, 'exists', return_value=True), \
             patch.object(traffic.Path, 'read_text', return_value=text), \
             patch.object(traffic, 'inspect', return_value={'serial': 17, 'open': False, 'tun_exists': True}):
            value = traffic.client_diagnostics()
        self.assertTrue(value['binat_unconfirmed_error'])
        self.assertNotIn('synthetic-private-material', json.dumps(value))

    def test_successful_native_publication_is_not_diagnosed_as_an_error(self):
        text = 'BINAT native kernel policy applied locally: serial=17 controller_ack=false'
        with patch.object(traffic.Path, 'exists', return_value=True), \
             patch.object(traffic.Path, 'read_text', return_value=text), \
             patch.object(traffic, 'inspect', return_value={'serial': 17, 'open': True, 'tun_exists': True}):
            value = traffic.client_diagnostics()
        self.assertFalse(value['binat_native_error'])
        self.assertTrue(value['local_publication_seen'])
        self.assertTrue(value['gate_open'])

    def test_client_diagnostics_export_only_fixed_flags_not_log_content(self):
        secret = 'synthetic-private-material'
        text = 'certificate signed by unknown authority ' + secret
        with patch.object(traffic.Path, 'exists', return_value=True), \
             patch.object(traffic.Path, 'read_text', return_value=text), \
             patch.object(traffic, 'inspect', return_value={'serial': -1, 'open': False, 'tun_exists': False}):
            value = traffic.client_diagnostics()
        self.assertTrue(value['tls_error'])
        self.assertTrue(all(type(v) is bool for v in value.values()))
        self.assertNotIn(secret, json.dumps(value))

    def test_translation_only_accepts_exact_laboratory_scopes(self):
        for virtual, dev in [('0.0.0.0/0', 'eth0'), ('198.18.10.0/24', 'eth0";accept')]:
            with self.assertRaisesRegex(RuntimeError, 'lab_translation_scope_refused'):
                container.translation_script(virtual, dev)

    def test_summary_never_emits_unclassified_failure_or_case_text(self):
        secret = 'synthetic-private-material'
        report = {'stage': secret, 'error_class': secret, 'cases': {secret: secret}}
        result = lab.safe_summary(report, 1)
        self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(result['stage'], 'unknown')
        self.assertEqual(result['error_class'], 'unclassified')
        self.assertEqual(result['failed_or_blocked_cases'], 1)

    def test_summary_preserves_safe_integration_failure_stage(self):
        result = lab.safe_summary({'stage': 'start_primary-a', 'error_class': 'lab_local_nft_failed',
                                  'cases': {'bootstrap': 'pass'}}, 1)
        self.assertEqual(result['stage'], 'start_primary-a')
        self.assertEqual(result['error_class'], 'lab_local_nft_failed')
        self.assertEqual(result['passed_cases'], 1)
        self.assertFalse(any(result['lifecycle_gates_passed'].values()))
        completed = lab.safe_summary({'cases': {'engine_restart_same_identity': 'pass'}}, 1)
        self.assertTrue(completed['lifecycle_gates_passed']['engine_restart_same_identity'])

    def test_failed_preparation_before_compose_still_removes_credentials(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            runtime = root / 'runtime'
            runtime.mkdir()
            (runtime / 'private-lab-material').write_text('synthetic-only')
            owner = lab.Lab(root)
            self.assertFalse(owner.file.exists())
            def wipe(*args, **kwargs):
                self.assertEqual(args[0], 'run')
                self.assertIn('none', args)
                self.assertIn(str(root) + ':/lab', args)
                self.assertIn('p.is_symlink()', args[-1])
                shutil.rmtree(runtime)
                return subprocess.CompletedProcess(args, 0, '', '')
            with patch.object(lab, 'docker', side_effect=wipe):
                self.assertTrue(owner.cleanup())
            self.assertFalse(runtime.exists())

    def test_failed_credential_cleanup_cannot_report_complete(self):
        with tempfile.TemporaryDirectory() as d:
            owner = lab.Lab(Path(d))
            failed = subprocess.CompletedProcess([], 1, '', '')
            with patch.object(lab, 'docker', return_value=failed):
                self.assertFalse(owner.cleanup())

    def test_all_runtime_networks_internal_no_host_ports_or_socket(self):
        doc = lab.topology(Path('/tmp/disposable'), 'om-binat-test')
        self.assertEqual(len(doc['networks']), 3)
        self.assertTrue(all(n['internal'] for n in doc['networks'].values()))
        for service in doc['services'].values():
            self.assertNotIn('ports', service)
            self.assertNotIn('privileged', service)
            self.assertNotIn('network_mode', service)
            self.assertNotIn('pid', service)
            self.assertEqual(service['cap_drop'], ['ALL'])
            self.assertEqual(service['restart'], 'no')
            self.assertNotIn('docker.sock', json.dumps(service))
            self.assertNotIn('SYS_ADMIN', service.get('cap_add', []))
            self.assertEqual(service['environment']['HTTPS_PROXY'], '')
            self.assertEqual(service['logging']['driver'], 'none')
        state = '/tmp/disposable/runtime/server-state:/var/lib/netbird'
        self.assertIn(state, doc['services']['controller']['volumes'])
        self.assertNotIn('/var/lib/netbird:mode=0700', doc['services']['controller']['tmpfs'])

    def test_four_separate_real_lan_router_namespaces(self):
        doc = lab.topology(Path('/tmp/disposable'), 'om-binat-test')
        for name in lab.ROUTERS:
            service = doc['services'][name]
            self.assertEqual(set(service['networks']), {'control', 'lan-' + name[-1]})
            self.assertEqual(set(service['cap_add']), {'NET_ADMIN', 'NET_RAW', 'MKNOD'})
            self.assertEqual(service['device_cgroup_rules'], ['c 10:200 rwm'])
            self.assertNotIn('/var/lib/netbird:', ' '.join(service['volumes']))
            self.assertIn('/tmp/disposable/runtime/ca/cert.pem:/lab-ca/cert.pem:ro', service['volumes'])
            self.assertNotIn('/tmp/disposable/runtime/ca:/lab-ca:ro', service['volumes'])
            self.assertIn('/tmp/disposable/runtime/keys/' + name + ':/lab-key:ro', service['volumes'])
        self.assertIn('@sha256:', doc['services']['controller']['image'])
        self.assertEqual(doc['services']['controller']['environment']['NB_DISABLE_GEOLOCATION'], 'true')

    def test_tls_redirect_and_arbitrary_endpoint_refusal(self):
        for url in ('https://example.invalid/api/peers', '/api/../peers', '/foo'):
            with self.assertRaisesRegex(RuntimeError, 'lab_api_path_refused'):
                container.api(url)
        with self.assertRaisesRegex(RuntimeError, 'lab_api_redirect_refused'):
            container.NoRedirect().redirect_request(None)

    def test_secret_creation_0600_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'new-test-key'
            container.write(f, 'synthetic-only')
            self.assertEqual(f.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                container.write(f, 'replacement')
            self.assertEqual(f.read_text(), 'synthetic-only')

    def test_blocked_gates_cannot_be_reported_pass(self):
        class NotConnected:
            def exec(self, *args, **kwargs):
                raise RuntimeError('lab_fresh_local_publication_missing')
        result = packets.acceptance(NotConnected())
        self.assertTrue(set(packets.REQUIRED).issubset(result))
        self.assertTrue(all(result[k].startswith('blocked:') for k in packets.REQUIRED))
        self.assertNotIn('pass', result.values())

    def test_build_real_cli_not_fixture_and_pin_upstream(self):
        build = (DIR / 'Dockerfile.client').read_text()
        self.assertIn('ab453b7d015fd657c912c40986868784f77c3460f296dc07bc2c7f07f4bce7b7', build)
        self.assertIn('-o /out/netbird ./client', build)
        commands = '\n'.join(line for line in build.splitlines() if not line.lstrip().startswith('#'))
        self.assertNotIn('cmd/small-lab', commands)
        self.assertNotIn('--insecure', build)
        self.assertIn('git apply --check', build)


if __name__ == '__main__':
    unittest.main()
