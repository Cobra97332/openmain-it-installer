"""Lab isolation/report contracts. These mocks are NOT real NetBird tests."""
import importlib.util
import json
from pathlib import Path
import tempfile
import shutil
import subprocess
import unittest
from unittest.mock import patch

DIR = Path(__file__).resolve().parents[1] / 'netbird-router/experimental/native-binat/docker-lab'


def load(name):
    spec = importlib.util.spec_from_file_location('native_lab_' + name, DIR / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lab = load('lab')
packets = load('packets')
container = load('container')


class NativeDockerLabTests(unittest.TestCase):
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
