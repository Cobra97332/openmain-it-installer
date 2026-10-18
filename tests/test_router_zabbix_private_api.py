"""Private HTTP exception: synthetic identities/responses, no production access."""
import json
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'netbird-router/common'))
import zabbix_access as a
import zabbix_sync as sync
from runtime import Invalid
import test_router_zabbix_access as fixtures

URL = a.PRIVATE_API
C = dict(api_url=URL, server='100.107.91.6', management='https://controller.invalid')
IDENTITY = ('100.107.82.65', 'wt0')
PAYLOAD = b'{"jsonrpc":"2.0","id":1,"method":"host.get","params":{}}'


class PrivateAdmissionTests(unittest.TestCase):
    setUp = fixtures.AdmissionTests.setUp
    policy = staticmethod(fixtures.AdmissionTests.policy)
    api = fixtures.AdmissionTests.api
    # Use the original HTTPS tests unchanged; private cases below exercise the
    # same membership and policy admission using the real private port.
    def private_gate(self, **kwargs):
        self.c.update(C)
        self.peers[1]['ip'] = C['server']
        self.policies.append(self.policy('private', 'Private API', 'server', 8083))
        options = dict(api=self.api, identity=('100.64.0.10', 'wt0'),
                       route_check=lambda ip: 'wt0', tcp_probe=self.tcp,
                       https_probe=self.https, http_probe=Mock(), attempts=1)
        options.update(kwargs)
        return a.require_access(URL, self.c, **options)

    def test_private_uses_server_peer_8083_preserves_members_and_never_https(self):
        probe = Mock()
        self.private_gate(http_probe=probe)
        probe.assert_called_once_with(URL, {(C['server'], 8083)})
        self.tcp.assert_called_once_with(C['server'])
        self.https.assert_not_called()
        self.assertEqual(self.calls.count('/groups/source'), 2)

    def test_private_no_proxy_or_dns_resolution(self):
        resolver = Mock(side_effect=AssertionError('DNS forbidden'))
        bypass = Mock(side_effect=AssertionError('proxy forbidden'))
        self.assertEqual(a.endpoints(URL, {'http': 'http://evil.invalid'}, bypass, resolver),
                         {(C['server'], 8083)})
        resolver.assert_not_called(); bypass.assert_not_called()

    def test_private_wrong_route_denied_before_probe(self):
        probe = Mock()
        with self.assertRaises(Invalid):
            self.private_gate(route_check=lambda ip: 'eth0', http_probe=probe)
        probe.assert_not_called()

    def test_private_explicit_configuration_and_server_required(self):
        for c in ({}, dict(C, server='100.64.0.20'), dict(C, api_url='https://api.invalid')):
            with self.subTest(c=c), self.assertRaises(Invalid):
                a.require_access(URL, c or {'server': '100.64.0.20'}, api=Mock())

    def test_private_missing_membership_or_8083_policy_denied(self):
        self.groups['source'].update(peers=['old'], peers_count=1)
        probe = Mock()
        with self.assertRaisesRegex(Invalid, 'Mitgliedschaft'):
            self.private_gate(http_probe=probe)
        probe.assert_not_called()
        self.setUp()
        self.c.update(C)
        self.peers[1]['ip'] = C['server']
        with self.assertRaises(a.Pending):
            a.require_access(URL, self.c, api=self.api, identity=('100.64.0.10', 'wt0'),
                route_check=lambda ip: 'wt0', tcp_probe=self.tcp, http_probe=probe, attempts=1)
        probe.assert_not_called()


class PrivateTransportTests(unittest.TestCase):
    def setUp(self):
        self.sock = Mock()
        self.sock.getpeername.return_value = (C['server'], 8083)
        self.sock.getsockname.return_value = (IDENTITY[0], 12345)
        self.conn = Mock()
        self.conn.getresponse.return_value.status = 200
        self.conn.getresponse.return_value.read.return_value = b'{"jsonrpc":"2.0","id":1,"result":[]}'
        for name, value in [('active_identity', Mock(return_value=IDENTITY)),
                            ('route', Mock(return_value='wt0'))]:
            patcher = patch.object(a, name, value)
            setattr(self, name, patcher.start()); self.addCleanup(patcher.stop)
        patcher = patch.object(a.socket, 'socket', return_value=self.sock)
        self.factory = patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.object(a.http.client, 'HTTPConnection', return_value=self.conn)
        patcher.start(); self.addCleanup(patcher.stop)

    def request(self, token='synthetic-token'):
        return a.private_request(URL, PAYLOAD, token, C)

    def test_bound_socket_rechecked_before_token_and_psk_transmission(self):
        self.assertEqual(self.request()['result'], [])
        self.sock.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b'wt0\0')
        self.sock.bind.assert_called_once_with((IDENTITY[0], 0))
        self.sock.connect.assert_called_once_with((C['server'], 8083))
        self.assertEqual(self.active_identity.call_count, 2)
        self.assertEqual(self.route.call_count, 2)
        args = self.conn.request.call_args.args
        self.assertEqual(args[:3], ('POST', '/api_jsonrpc.php', PAYLOAD))
        self.assertEqual(args[3]['Authorization'], 'Bearer synthetic-token')
        self.conn.close.assert_called_once(); self.sock.close.assert_called_once()

    def test_no_token_sent_if_disconnected_or_route_changed_before_send(self):
        for status in ('disconnected', 'route-before', 'route-after', 'identity-after'):
            self.active_identity.side_effect = None; self.route.side_effect = None
            self.conn.request.reset_mock()
            if status == 'disconnected': self.active_identity.side_effect = Invalid('disconnected')
            if status == 'route-before': self.route.side_effect = ['eth0']
            if status == 'route-after': self.route.side_effect = ['wt0', 'eth0']
            if status == 'identity-after': self.active_identity.side_effect = [IDENTITY, ('100.64.1.2', 'wt1')]
            with self.subTest(status=status), self.assertRaises(Invalid): self.request()
            self.conn.request.assert_not_called()

    def test_device_binding_failure_never_falls_back_to_default_route(self):
        self.sock.setsockopt.side_effect = PermissionError('synthetic-token')
        with self.assertRaises(Invalid) as error: self.request()
        self.assertNotIn('synthetic-token', str(error.exception))
        self.sock.connect.assert_not_called(); self.conn.request.assert_not_called()

    def test_wrong_connected_source_or_destination_never_transmits(self):
        for source, destination in [('192.0.2.10', C['server']), (IDENTITY[0], '192.0.2.20')]:
            self.sock.getsockname.return_value = (source, 12345)
            self.sock.getpeername.return_value = (destination, 8083)
            with self.assertRaises(Invalid): self.request()
            self.conn.request.assert_not_called()

    def test_redirect_and_other_statuses_rejected_without_followup(self):
        for code in (301, 302, 303, 307, 308, 401, 500):
            self.conn.getresponse.return_value.status = code
            self.conn.request.reset_mock()
            with self.subTest(code=code), self.assertRaisesRegex(Invalid, 'HTTP_STATUS_REJECTED'): self.request()
            self.conn.request.assert_called_once()

    def test_only_exact_configured_http_endpoint_accepted(self):
        for url in (URL.replace('100.107.91.6','100.107.91.7'), URL.replace('8083','80'),
                    URL+'/', URL+'?x=y', URL+'#fragment', URL.replace('http://','http://user@'),
                    URL.replace('100.107.91.6','server.invalid'), 'http://127.0.0.1:8083/api_jsonrpc.php'):
            with self.subTest(url=url), self.assertRaises(Invalid): a.api_endpoint(url)
        self.assertEqual(a.api_endpoint(URL).port, 8083)

    def test_unauthenticated_version_validated_without_bearer_header(self):
        self.conn.getresponse.return_value.read.return_value = b'{"jsonrpc":"2.0","id":1,"result":"7.4.15"}'
        # Probe context is the explicit environment, not a guessed endpoint.
        with patch.object(a, 'context', return_value=C):
            a.private_probe(URL, {(C['server'], 8083)})
        headers = self.conn.request.call_args.args[3]
        self.assertNotIn('Authorization', headers)
        self.assertEqual(json.loads(self.conn.request.call_args.args[2])['params'], [])
        for result in ('7.0.2', None, {'untrusted': 'synthetic-token'}):
            self.conn.getresponse.return_value.read.return_value = json.dumps(dict(jsonrpc='2.0', id=1, result=result)).encode()
            with patch.object(a, 'context', return_value=C), self.assertRaises(a.Pending):
                a.private_probe(URL, {(C['server'], 8083)})

    def test_each_authenticated_get_and_write_rechecks_access(self):
        with patch.object(sync, 'require_access') as guard, patch.object(sync, 'private_request', return_value=dict(jsonrpc='2.0', id=1, result=[])) as transport:
            api = sync.API(URL, 'synthetic-token', C)
            for method in ('proxy.get', 'proxygroup.get', 'host.get', 'host.create', 'host.update'):
                api(method, {})
            self.assertEqual(guard.call_count, 5)
            self.assertEqual(transport.call_count, 5)
            guard.side_effect = Invalid('revoked')
            with self.assertRaises(Invalid): api('host.get', {})
            self.assertEqual(transport.call_count, 5)


class DockerEndpointTests(unittest.TestCase):
    def test_docker_configuration_accepts_only_exact_private_exception(self):
        from test_docker_router import r, ENV
        e = dict(ENV, NB_ZABBIX_API_URL=URL, NB_ZABBIX_SERVER=C['server'])
        self.assertEqual(r.configuration(e), e)
        for changes in (dict(NB_ZABBIX_SERVER='100.64.0.10'), dict(NB_ZABBIX_API_URL=URL.replace('8083','8084'))):
            with self.subTest(changes=changes), self.assertRaises(Invalid):
                r.configuration(dict(e, **changes))

    def test_endpoint_correction_only_before_registration_preserves_existing_state(self):
        import tempfile
        from test_docker_router import r, ENV
        with tempfile.TemporaryDirectory() as d, patch.object(r, 'PRIVATE', Path(d)):
            marker = Path(d) / 'binding.json'
            old = dict(ENV, NB_ZABBIX_SERVER=C['server'])
            binding = dict(old, NB_ZABBIX_API_URL=URL)
            marker.write_text(json.dumps(old))
            key = Path(d) / 'router.psk'
            key.write_bytes(b'synthetic-existing-key')
            before = marker.read_bytes(), key.read_bytes()
            r.check_binding(marker, binding)
            self.assertEqual((marker.read_bytes(), key.read_bytes()), before)
            for state in ('zabbix-registration.json','psk.pending','config-hashes.json','config-intent.json','ready'):
                p = Path(d) / state; p.touch()
                with self.subTest(state=state), self.assertRaises(Invalid): r.check_binding(marker, binding)
                p.unlink()
            for change in (dict(CUSTOMER='other'), dict(NB_MANAGEMENT_URL='https://other.invalid'),
                           dict(NB_ZABBIX_SERVER='100.64.0.10')):
                with self.subTest(change=change), self.assertRaises(Invalid):
                    r.check_binding(marker, dict(binding, **change))
