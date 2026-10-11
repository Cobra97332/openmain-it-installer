"""Offline admission tests: no real DNS, NetBird, Zabbix or router connections."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'netbird-router/common'))
import zabbix_access as access
import zabbix_sync
from runtime import Invalid


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.c = dict(server='100.64.0.20', group='Zabbix-Kunden-Proxies',
                      policy='Zabbix Proxy to Server')
        self.groups = {}
        for gid, name, peers in [('source', self.c['group'], ['router', 'old']),
                                 ('server', 'Zabbix-Server', ['server-peer']),
                                 ('api', 'HTTPS reverse proxy', ['api-peer']),
                                 ('forward', 'Forward proxy', ['forward-peer'])]:
            self.groups[gid] = dict(id=gid, name=name, peers=peers, peers_count=len(peers),
                                    resources=[dict(id='keep-resource', type='network')], resources_count=1)
        self.peers = [dict(id=pid, ip=ip) for pid, ip in
                      [('router', '100.64.0.10'), ('server-peer', '100.64.0.20'),
                       ('api-peer', '100.64.0.30'), ('forward-peer', '100.64.0.40')]]
        self.policies = [self.policy('metrics', self.c['policy'], 'server', 10051),
                         self.policy('web', 'API HTTPS', 'api', 443),
                         self.policy('forward', 'CONNECT proxy', 'forward', 3128)]
        self.calls = []
        self.tcp = Mock()
        self.https = Mock()
        self.pause = Mock()

    @staticmethod
    def policy(pid, name, destination, port):
        return dict(id=pid, name=name, enabled=True, source_posture_checks=['keep-posture'],
                    rules=[dict(enabled=True, action='accept', protocol='tcp', sources=['source'],
                                destinations=[dict(id=destination)], ports=[str(port)]),
                           dict(name='keep-extra', enabled=False, action='drop')])

    def api(self, path):
        self.calls.append(path)
        if path == '/peers': result = self.peers
        elif path == '/groups': result = list(self.groups.values())
        elif path == '/policies': result = self.policies
        else: result = self.groups[path.removeprefix('/groups/')]
        return copy.deepcopy(result)

    def run_gate(self, **kwargs):
        options = dict(api=self.api, identity=('100.64.0.10', 'wt-lab'),
                       target_resolver=lambda url: {('100.64.0.30', 443)},
                       route_check=lambda ip: 'wt-lab', tcp_probe=self.tcp,
                       https_probe=self.https, pause=self.pause, attempts=3)
        options.update(kwargs)
        return access.require_access('https://api.invalid/api_jsonrpc.php', self.c, **options)

    def test_distinct_server_and_api_peer_readonly_and_reconfirmed(self):
        before = copy.deepcopy((self.groups, self.policies))
        self.run_gate()
        self.tcp.assert_called_once_with('100.64.0.20')
        self.https.assert_called_once_with('https://api.invalid/api_jsonrpc.php', {('100.64.0.30', 443)})
        self.assertEqual(self.calls.count('/groups/source'), 2)
        self.assertEqual(self.calls.count('/policies'), 2)
        self.assertEqual((self.groups, self.policies), before)

    def test_delayed_policy_propagation_continues_without_manual_change(self):
        self.https.side_effect = [access.Pending('pending'), access.Pending('pending'), None]
        before = copy.deepcopy((self.groups, self.policies))
        self.run_gate()
        self.assertEqual(self.https.call_count, 3)
        self.assertEqual(self.pause.call_count, 2)
        self.assertEqual(self.calls.count('/groups/source'), 4)
        self.assertEqual((self.groups, self.policies), before)

    def test_delayed_policy_api_view_is_rechecked_before_probe(self):
        def api(path):
            result = self.api(path)
            if path == '/policies' and self.calls.count(path) == 1:
                return [result[0]]
            return result
        self.run_gate(api=api)
        self.pause.assert_called_once_with(5)
        self.https.assert_called_once()

    def test_missing_membership_never_probes_or_retries(self):
        self.groups['source'].update(peers=['old'], peers_count=1)
        before = copy.deepcopy((self.groups, self.policies))
        with self.assertRaisesRegex(Invalid, 'Mitgliedschaft'):
            self.run_gate()
        self.tcp.assert_not_called(); self.https.assert_not_called(); self.pause.assert_not_called()
        self.assertEqual((self.groups, self.policies), before)

    def test_membership_removed_during_probe_prevents_registration(self):
        self.https.side_effect = lambda *args: self.groups['source'].update(peers=['old'], peers_count=1)
        with self.assertRaisesRegex(Invalid, 'Mitgliedschaft'):
            self.run_gate()
        self.assertEqual(self.calls.count('/groups/source'), 2)

    def test_server_443_rule_does_not_authorize_different_api_peer(self):
        self.policies = [self.policies[0], self.policy('wrong', 'wrong target', 'server', 443)]
        with self.assertRaisesRegex(access.Pending, 'Zielpeer'):
            self.run_gate()
        self.https.assert_not_called(); self.tcp.assert_not_called()
        self.assertEqual(self.pause.call_count, 2)

    def test_blocked_443_has_bounded_retry_and_no_configuration_changes(self):
        before = copy.deepcopy((self.groups, self.policies))
        self.https.side_effect = access.Pending('HTTPS/443 blockiert')
        with self.assertRaisesRegex(access.Pending, 'HTTPS/443'):
            self.run_gate()
        self.assertEqual(self.https.call_count, 3)
        self.assertEqual(self.pause.call_count, 2)
        self.assertEqual((self.groups, self.policies), before)

    def test_unknown_overlay_peer_and_duplicate_groups_fail_closed(self):
        with self.assertRaises(Invalid):
            self.run_gate(target_resolver=lambda url: {('100.64.0.99', 443)})
        self.groups['duplicate'] = dict(self.groups['source'], id='duplicate')
        with self.assertRaises(Invalid): self.run_gate()
        self.https.assert_not_called()

    def test_forward_proxy_uses_its_actual_peer_and_port(self):
        self.run_gate(target_resolver=lambda url: {('100.64.0.40', 3128)})
        self.https.assert_called_once()
        self.policies.pop()
        with self.assertRaises(access.Pending):
            self.run_gate(target_resolver=lambda url: {('100.64.0.40', 3128)})

    def test_public_api_uses_real_https_probe_without_invented_overlay_peer(self):
        self.run_gate(target_resolver=lambda url: {('192.0.2.50', 443)}, route_check=lambda ip: 'eth0')
        self.https.assert_called_once_with('https://api.invalid/api_jsonrpc.php', {('192.0.2.50', 443)})

    def test_conflicting_counts_refuse_even_with_valid_peer(self):
        for count in (None, '2', False, -1, 0, 3):
            with self.subTest(count=count):
                self.groups['source']['peers_count'] = count
                with self.assertRaises(Invalid): self.run_gate()
        self.https.assert_not_called()


class TransportTests(unittest.TestCase):
    def test_dns_reverse_proxy_forward_proxy_and_no_proxy(self):
        resolver = Mock(return_value={'100.64.0.30'})
        self.assertEqual(access.endpoints('https://api.invalid/api', {}, lambda h: False, resolver),
                         {('100.64.0.30', 443)})
        resolver.assert_called_with('api.invalid', 443)
        proxies = {'https': 'http://forward.invalid:3128'}
        access.endpoints('https://api.invalid/api', proxies, lambda h: False, resolver)
        resolver.assert_called_with('forward.invalid', 3128)
        access.endpoints('https://api.invalid/api', proxies, lambda h: True, resolver)
        resolver.assert_called_with('api.invalid', 443)
        for url in ('http://api.invalid', 'https://user:password@api.invalid', 'https://api.invalid:444'):
            with self.subTest(url=url), self.assertRaises(Invalid):
                access.endpoints(url, {}, lambda h: False, resolver)

    def test_https_tls_version_remote_address_and_no_credentials(self):
        success = json.dumps(dict(jsonrpc='2.0', id=1, result='7.4.2')).encode() + b'\n100.64.0.30'
        targets = {('100.64.0.30', 443)}
        with patch.object(access.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, success)) as run:
            access.https('https://api.invalid/api', targets)
            args = run.call_args.args[0]
            self.assertNotIn('-k', args); self.assertNotIn('-L', args)
            self.assertNotIn('Authorization', ' '.join(args))
            self.assertEqual(json.loads(args[args.index('--data') + 1])['method'], 'apiinfo.version')
        for rc, body in [(7, success), (0, b'<html>secret</html>\n100.64.0.30'),
                         (0, success.replace(b'100.64.0.30', b'100.64.0.20')),
                         (0, success.replace(b'7.4.2', b'7.0.2'))]:
            with self.subTest(rc=rc, body=body), patch.object(access.subprocess, 'run',
                 return_value=subprocess.CompletedProcess([], rc, body, b'test-secret')):
                with self.assertRaises(access.Pending) as error: access.https('https://api.invalid', targets)
                self.assertNotIn('secret', str(error.exception))
        with patch.object(access.subprocess, 'run', side_effect=subprocess.TimeoutExpired('curl', 10)):
            with self.assertRaises(access.Pending): access.https('https://api.invalid', targets)

    def test_netbird_transport_get_only_no_redirect_or_secret_diagnostics(self):
        with patch.object(access.urllib.request, 'build_opener') as build:
            client = access.NetBird(dict(management='https://netbird.invalid', token='synthetic-secret'))
            build.return_value.open.side_effect = RuntimeError('synthetic-secret')
            with self.assertRaises(Invalid) as error: client('/groups')
            self.assertNotIn('synthetic-secret', str(error.exception))
            self.assertEqual(build.return_value.open.call_args.args[0].get_method(), 'GET')
        self.assertIsNone(access.NoRedirect().redirect_request(None, None, None, None, None, None))

    def test_all_proxy_environment_is_restored_for_same_transport(self):
        c = dict(proxy_env={'ALL_PROXY': 'http://proxy.invalid:3128'}, server='100.64.0.20')
        with patch.dict(access.os.environ, {'https_proxy': 'http://wrong.invalid'}, clear=True), \
             patch.object(access, 'check'), patch.object(access, 'NetBird'):
            access.require_access('https://api.invalid', c, identity=('100.64.0.10','wt-lab'),
                                  target_resolver=lambda url: set(), tcp_probe=Mock(), https_probe=Mock())
            self.assertEqual(access.os.environ['https_proxy'], c['proxy_env']['ALL_PROXY'])

    def test_denied_access_precedes_each_zabbix_mutation_and_host_registration(self):
        with patch.object(zabbix_sync, 'require_access', side_effect=Invalid('blocked 443')) as gate, \
             patch.object(zabbix_sync.urllib.request, 'urlopen') as transport:
            api = zabbix_sync.API('https://api.invalid', 'synthetic-secret', {'token': 'synthetic-netbird'})
            for method in ('host.create', 'host.update', 'proxy.update', 'proxygroup.create'):
                with self.subTest(method=method), self.assertRaises(Invalid): api(method, {})
            transport.assert_not_called()
            self.assertEqual(gate.call_count, 4)
            fake = Mock()
            with self.assertRaises(Invalid):
                zabbix_sync.register_host(fake, {'url': 'https://api.invalid'}, '192.0.2.10', 'ab'*32)
            fake.assert_not_called()
