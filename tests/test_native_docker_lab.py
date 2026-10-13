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


class NativeDockerLabTests(unittest.TestCase):
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

    def test_foreign_reference_detects_startup_changes_not_only_later_changes(self):
        initial = {'nftables': [{'table': {'name': 'lab_foreign', 'family': 'inet'}}]}
        changed = {'nftables': initial['nftables'] + [{'rule': {'chain': 'forward', 'expr': [{'accept': None}]}}]}
        with tempfile.TemporaryDirectory() as d, patch.object(traffic, 'FOREIGN_REFERENCE', Path(d) / 'reference'):
            with patch.object(traffic, 'nft', return_value=initial):
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
            with patch.object(traffic, 'nft', return_value={'nftables': []}):
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
