"""Offline admission tests: no real DNS, NetBird, Zabbix or router connections."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
import urllib.error
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'netbird-router/common'))
import zabbix_access as access
import zabbix_sync
from runtime import Invalid


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.c = dict(api_url='https://api.invalid/api_jsonrpc.php', server='100.64.0.20', group='Zabbix-Kunden-Proxies',
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
        elif path == '/reverse-proxies/services': raise access.ServiceUnavailable('unsupported')
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
            self.run_gate(target_resolver=lambda url: {('100.64.0.99', 443)},
                          service_resolver=lambda host, port: {'100.64.0.30'})
        self.groups['duplicate'] = dict(self.groups['source'], id='duplicate')
        with self.assertRaises(Invalid): self.run_gate()
        self.https.assert_not_called()

    def service_gate(self, **kwargs):
        self.peers = [p for p in self.peers if p['id'] != 'api-peer']
        self.policies = [self.policies[0]]  # Service ACL is separate from peer rules.
        kwargs.setdefault('service_resolver', lambda host, port: {'100.64.0.30'})
        return self.run_gate(**kwargs)

    def service(self, **kwargs):
        result = dict(id='service', domain='api.invalid', enabled=True, mode='http',
                      private=True, access_groups=['source'])
        result.update(kwargs)
        return result

    def with_services(self, services):
        def api(path):
            if path == '/reverse-proxies/services':
                self.calls.append(path)
                return copy.deepcopy(services)
            return self.api(path)
        return api

    def test_service_ip_without_peer_fallback_requires_real_https_and_preserves_state(self):
        self.service_gate()
        self.tcp.assert_called_once_with('100.64.0.20')
        self.https.assert_called_once_with(self.c['api_url'], {('100.64.0.30', 443)})
        self.assertEqual(self.calls.count('/reverse-proxies/services'), 2)
        self.assertEqual(self.calls.count('/groups/source'), 2)
        self.assertEqual(self.groups['source']['peers'], ['router', 'old'])
        self.assertEqual(self.groups['source']['resources'], [dict(id='keep-resource',type='network')])

    def test_service_acl_is_verified_when_api_available(self):
        service=self.service()
        self.service_gate(api=self.with_services([service]))
        self.assertEqual(service, self.service())  # No policy/service changes.
        self.https.assert_called_once()

    def test_service_api_denials_and_ambiguity_never_fall_back(self):
        for services in ([self.service(access_groups=[])], [self.service(private=False)],
                         [self.service(enabled=False)], [self.service(terminated=True)],
                         [self.service(mode='tcp')], [self.service(access_groups=None)],
                         [self.service(), self.service(id='other')], {'items': []}):
            with self.subTest(services=services), self.assertRaises(Invalid):
                self.service_gate(api=self.with_services(services))
        self.https.assert_not_called()

    def test_service_api_without_acl_or_domain_record_still_requires_https(self):
        older=self.service();del older['private'];del older['access_groups']
        for services in ([], [older]):
            with self.subTest(services=services):
                self.service_gate(api=self.with_services(services))
        self.assertEqual(self.https.call_count, 2)

    def test_service_api_transport_error_is_not_treated_as_unavailable(self):
        def api(path):
            if path == '/reverse-proxies/services': raise Invalid('API error')
            return self.api(path)
        with self.assertRaises(Invalid): self.service_gate(api=api)
        self.https.assert_not_called()

    def test_service_wrong_domain_or_ip_literal_never_probes(self):
        self.c['api_url']='https://configured.invalid/api_jsonrpc.php'
        with self.assertRaisesRegex(Invalid, 'Domain/Pfad'):
            self.service_gate()
        self.c['api_url']='https://api.invalid/api_jsonrpc.php'
        with self.assertRaisesRegex(Invalid, 'Domain'):
            self.service_gate(service_resolver=lambda host, port: {'100.64.0.99'})
        with self.assertRaises(Invalid):
            access.service_access(self.api, 'https://100.64.0.30/api_jsonrpc.php',
                                  '100.64.0.30', 443, 'wt-lab', 'wt-lab', 'source', Mock())
        self.https.assert_not_called()

    def test_service_wrong_route_or_connect_proxy_never_probes(self):
        for device in ('eth0','lo','other-wg'):
            with self.subTest(device=device), self.assertRaises(Invalid):
                self.service_gate(route_check=lambda ip: device)
        with self.assertRaises(Invalid):
            self.service_gate(target_resolver=lambda url: {('100.64.0.30', 3128)})
        self.https.assert_not_called(); self.tcp.assert_not_called()

    def test_service_tls_failure_blocks_registration_and_bounded_retry(self):
        response = json.dumps(dict(jsonrpc='2.0',id=1,result='7.4.15')).encode()
        response += b'\n200\n100.64.0.30\nhttps://api.invalid/api_jsonrpc.php'
        with patch.object(access.subprocess,'run',return_value=subprocess.CompletedProcess([],60,response)):
            with self.assertRaises(access.Pending): self.service_gate(https_probe=access.https)
        self.assertEqual(self.pause.call_count, 2)
        self.assertEqual(self.groups['source']['peers'], ['router','old'])

    def test_service_valid_7415_reply_via_configured_domain_and_netbird_route(self):
        body=json.dumps(dict(jsonrpc='2.0',id=1,result='7.4.15')).encode()
        body+=b'\n200\n100.64.0.30\nhttps://api.invalid/api_jsonrpc.php'
        with patch.object(access.subprocess,'run',return_value=subprocess.CompletedProcess([],0,body)) as run:
            self.service_gate(https_probe=access.https)
            self.assertIn(self.c['api_url'],run.call_args.args[0])
        self.tcp.assert_called_once_with('100.64.0.20')

    def test_service_cannot_replace_missing_or_duplicate_server_router_peers(self):
        for pid in ('router','server-peer'):
            with self.subTest(pid=pid):
                original=self.peers[:]
                self.peers=[p for p in original if p['id']!=pid]
                with self.assertRaises(Invalid): self.service_gate()
                self.peers=original+[dict(next(p for p in original if p['id']==pid),id='duplicate')]
                with self.assertRaises(Invalid): self.service_gate()
                self.peers=original
        self.https.assert_not_called(); self.tcp.assert_not_called()

    def test_service_dns_or_route_changed_after_probe_prevents_registration(self):
        dns=Mock(side_effect=[{'100.64.0.30'}, {'100.64.0.99'}])
        with self.assertRaisesRegex(Invalid, 'Domain'):
            self.service_gate(service_resolver=dns)
        device=Mock(side_effect=['wt-lab','eth0'])
        with self.assertRaisesRegex(Invalid, 'Interface'):
            self.service_gate(route_check=device)

    def test_service_membership_lost_still_prevents_registration(self):
        self.groups['source'].update(peers=['old'],peers_count=1)
        with self.assertRaisesRegex(Invalid, 'Mitgliedschaft'): self.service_gate()
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
        success = json.dumps(dict(jsonrpc='2.0', id=1, result='7.4.2')).encode() + b'\n200\n100.64.0.30\nhttps://api.invalid/api'
        targets = {('100.64.0.30', 443)}
        with patch.object(access.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, success)) as run:
            access.https('https://api.invalid/api', targets)
            args = run.call_args.args[0]
            self.assertNotIn('-k', args); self.assertNotIn('-L', args)
            self.assertNotIn('Authorization', ' '.join(args))
            self.assertEqual(json.loads(args[args.index('--data') + 1])['method'], 'apiinfo.version')
            self.assertEqual(json.loads(args[args.index('--data') + 1])['params'], [])
        for rc, body in [(7, success), (0, b'<html>secret</html>\n200\n100.64.0.30\nhttps://api.invalid/api'),
                         (0, success.replace(b'100.64.0.30', b'100.64.0.20')),
                         (0, success.replace(b'7.4.2', b'7.0.2'))]:
            with self.subTest(rc=rc, body=body), patch.object(access.subprocess, 'run',
                 return_value=subprocess.CompletedProcess([], rc, body, b'test-secret')):
                with self.assertRaises(access.Pending) as error: access.https('https://api.invalid/api', targets)
                self.assertNotIn('secret', str(error.exception))
        with patch.object(access.subprocess, 'run', side_effect=subprocess.TimeoutExpired('curl', 10)):
            with self.assertRaises(access.Pending): access.https('https://api.invalid', targets)

    def test_https_requires_200_exact_domain_valid_tls_and_ignores_curlrc(self):
        body=json.dumps(dict(jsonrpc='2.0',id=1,result='7.4.15')).encode()
        good=body+b'\n200\n100.64.0.30\nhttps://api.invalid/api'
        for rc,response in ((60,good), (0,good.replace(b'200',b'302')),
                            (0,good.replace(b'api.invalid',b'evil.invalid')),
                            (0,good.replace(b'100.64.0.30',b'100.64.0.99'))):
            with self.subTest(rc=rc,response=response), patch.object(access.subprocess,'run',
                 return_value=subprocess.CompletedProcess([],rc,response)):
                with self.assertRaises(access.Pending): access.https('https://api.invalid/api',{('100.64.0.30',443)})
        with patch.object(access.subprocess,'run',return_value=subprocess.CompletedProcess([],0,good)) as run:
            access.https('https://api.invalid/api',{('100.64.0.30',443)})
            args=run.call_args.args[0]
            self.assertEqual(args[:2],['curl','--disable'])
            self.assertIn('--no-insecure',args);self.assertIn('--no-location',args)

    def test_unreachable_or_ambiguous_route_is_not_admitted(self):
        for rows in ([], [dict(dev='wt-lab'),dict(dev='eth0')],
                     [dict(dev='wt-lab',type='blackhole')], [dict(type='unreachable')]):
            with self.subTest(rows=rows), patch.object(access.subprocess,'check_output',return_value=json.dumps(rows)):
                with self.assertRaises(Invalid): access.route('100.64.0.30')
        with patch.object(access.subprocess,'check_output',return_value='[{"dev":"wt-lab","type":"unicast"}]'):
            self.assertEqual(access.route('100.64.0.30'),'wt-lab')

    def test_optional_service_inventory_http_statuses_are_narrowly_scoped(self):
        with patch.object(access.urllib.request, 'build_opener') as build:
            client=access.NetBird(dict(management='https://netbird.invalid',token='synthetic-secret'))
            for status in (403,404,405,501):
                build.return_value.open.side_effect=urllib.error.HTTPError('https://netbird.invalid',status,'secret',{},None)
                with self.assertRaises(access.ServiceUnavailable): client('/reverse-proxies/services')
                with self.assertRaises(Invalid) as error: client('/peers')
                self.assertNotIsInstance(error.exception, access.ServiceUnavailable)
            for status in (401,429,500):
                build.return_value.open.side_effect=urllib.error.HTTPError('https://netbird.invalid',status,'secret',{},None)
                with self.assertRaises(Invalid) as error: client('/reverse-proxies/services')
                self.assertNotIsInstance(error.exception, access.ServiceUnavailable)
                self.assertNotIn('secret',str(error.exception))

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
