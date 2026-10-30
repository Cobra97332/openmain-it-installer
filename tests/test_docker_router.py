"""Pure container-adapter checks. Actual authorization requires gateway.py."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'netbird-router/common'))
spec = importlib.util.spec_from_file_location('docker_router', ROOT / 'docker/router/router.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

ENV = dict(CUSTOMER='lab', ROUTER_ROLE='primary', NB_MANAGEMENT_URL='https://controller.lab.test',
           VIRTUAL_NETWORK='10.30.1.0/24', LAN_NETWORK_CIDR='192.168.178.0/24',
           LAN_IP='192.168.178.2', TRANSIT_SUBNET='172.28.250.0/29',
           EDGE_IP='172.28.250.2', NAT_IP='172.28.250.3',
           NB_ZABBIX_API_URL='https://zabbix.lab.test/api_jsonrpc.php', NB_ZABBIX_SERVER='100.64.0.10')


class ContainerTests(unittest.TestCase):
    def test_fresh_monitoring_names_and_existing_identity_are_stable(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d, patch.object(r,'PRIVATE',Path(d)):
            self.assertEqual(r.monitoring_names(ENV),('lab-Proxy-Primary','lab-Router-Primary'))
            (Path(d)/'psk-intent').write_text('proxy-lab-primary')
            self.assertEqual(r.monitoring_names(ENV),('proxy-lab-primary','router-lab-primary'))
            (Path(d)/'psk-intent').write_text('another-customer')
            with self.assertRaises(r.Invalid): r.monitoring_names(ENV)

    def test_configuration_and_backup(self):
        self.assertEqual(r.configuration(ENV), ENV)
        self.assertEqual(r.configuration(dict(ENV, ROUTER_ROLE='backup'))['ROUTER_ROLE'], 'backup')

    def test_unsafe_configuration(self):
        for key, value in (('CUSTOMER', 'bad\nServer=x'), ('LAN_IP', '10.30.1.8'),
                           ('NAT_IP', ENV['EDGE_IP']), ('NB_MANAGEMENT_URL', 'http://insecure'),
                           ('VIRTUAL_NETWORK', '192.168.178.0/24'), ('ROUTER_ROLE', 'other')):
            with self.subTest(key=key), self.assertRaises(r.Invalid):
                r.configuration(dict(ENV, **{key:value}))

    def test_existing_nat_generator_preserved(self):
        from runtime import nft_config
        rules = r.nat_rules(ENV, 'transit0', 'lan0')
        self.assertTrue(rules.startswith(nft_config('lan0', 'transit0', ENV['VIRTUAL_NETWORK'], ENV['LAN_NETWORK_CIDR'])))
        self.assertIn('100.64.0.0/10', rules)
        self.assertNotIn('flush ruleset', rules)

    def test_boundary_is_not_acl_copy(self):
        text = r.edge_rules(ENV, 'eth1')
        self.assertNotIn('counter accept', text)
        self.assertNotIn('jump', text)
        self.assertNotIn('ct state established accept', text)
        self.assertIn('iifname != "wt0" drop', text)

    def test_members_resources_and_repeat(self):
        state=dict(id='g1', name='lab', peers=[{'id':'old'}], peers_count=1,
                   resources=[{'id':'resource1','type':'host'}], resources_count=1)
        writes=[]
        def api(method, path, data=None):
            if method == 'GET':
                return [copy.deepcopy(state)] if path == '/groups' else copy.deepcopy(state)
            writes.append(data)
            state.update(copy.deepcopy(data))
            state['peers']=[{'id':x} for x in data['peers']]
            state['peers_count']=len(data['peers'])
            return state
        self.assertEqual(r.member(api, 'lab', 'new'), 'g1')
        r.member(api, 'lab', 'new')
        self.assertEqual(len(writes), 1)
        self.assertEqual({x['id'] for x in state['peers']}, {'old','new'})
        self.assertEqual(state['resources'][0]['id'], 'resource1')

    def test_group_probe_does_not_add_peer(self):
        state=dict(id='g1',name='lab',peers=[],peers_count=0,resources=[],resources_count=0)
        calls=[]
        def api(method,path,data=None):
            calls.append(method)
            return [state] if path=='/groups' else state
        self.assertEqual(r.member(api,'lab','new',add_peer=False),'g1')
        self.assertEqual(calls,['GET','GET'])

    def test_incomplete_group_no_write(self):
        for bad in (None, 'invalid', [1]):
            calls=[]
            def api(method,path,data=None):
                calls.append(method)
                group=dict(id='g1',name='lab',peers=bad,peers_count=1,resources=None,resources_count=0)
                return [group] if path == '/groups' else group
            with self.assertRaises(Exception): r.member(api,'lab','new')
            self.assertNotIn('PUT',calls)

    def test_runtime_contains_no_custom_netbird(self):
        docker=(ROOT/'docker/router/Dockerfile').read_text()
        self.assertIn('COPY --from=netbird /usr/local/bin/netbird',docker)
        self.assertNotIn('git apply',docker)
        self.assertNotIn('injectors/',docker)


class RestartPolicyTests(unittest.TestCase):
    def test_resume_rechecks_central_policy_without_creating_customer_policy(self):
        import tempfile
        from unittest.mock import patch
        from test_router_platforms import NetBirdMock
        from network import reconcile
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)
            api=NetBirdMock()
            api.data['/policies']=[dict(id='central',name='Admin \u2192 Kunden',enabled=True,
                rules=[dict(enabled=True,action='accept',protocol='all',bidirectional=False,
                            sources=['admins'],destinations=['global'],ports=[])])]
            result=reconcile(api,'lab','primary',ENV['LAN_NETWORK_CIDR'],'peer1',
                             'customer','global','admins',[],ENV['VIRTUAL_NETWORK'])
            (directory/'network.json').write_text(json.dumps(result))
            api.writes.clear()
            with patch.object(r,'PRIVATE',directory):
                self.assertEqual(r.mapping_binding(api, ENV, 'peer1','customer','global','admins',[]),result)
                self.assertEqual(api.writes,[])
                api.data['/policies']=[]
                with self.assertRaises(r.Invalid):
                    r.mapping_binding(api, ENV, 'peer1','customer','global','admins',[])
            self.assertFalse(any(path=='/policies' for _,path,_ in api.writes))


class IdentityVolumeTests(unittest.TestCase):
    def test_only_empty_new_identity_volume_is_admitted(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); directory=root/'var/lib/netbird'
            directory.mkdir(parents=True,mode=0o700)
            with patch.object(r,'ROOT_UID',os.getuid()):
                self.assertIsNone(r.existing_profile(root))
            (directory/'unknown.json').write_text('{}')
            with self.assertRaises(Exception): r.existing_profile(root)


class ForeignIdentityTests(unittest.TestCase):
    def test_unbound_existing_identity_does_not_start_or_modify_anything(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            state=Path(d)/'state';state.mkdir()
            with patch.object(r,'STATE',state), patch.object(r,'PRIVATE',state/'private'), \
                 patch.object(r,'CONFIG',state/'zabbix'), patch.object(r,'existing_profile',return_value={}), \
                 patch.object(r,'spawn') as start, patch.object(r,'command') as command:
                with self.assertRaisesRegex(r.Invalid,'NO_ADOPTION'):
                    r.gateway(ENV)
                start.assert_not_called();command.assert_not_called()
                self.assertEqual(list(state.iterdir()),[])


class RedirectTests(unittest.TestCase):
    def test_no_api_authorization_forwarded_across_redirect(self):
        import http.server
        import threading
        import urllib.request
        import urllib.error
        received=[]
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                received.append(self.path)
                self.send_response(302)
                self.send_header('Location','/unexpected-target')
                self.end_headers()
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),r.NoRedirect())
            request=urllib.request.Request('http://127.0.0.1:%d/initial'%server.server_port,
                headers={'Authorization':'Bearer synthetic-test-only'})
            with self.assertRaises(urllib.error.HTTPError): opener.open(request,timeout=3)
            self.assertEqual(received,['/initial'])
        finally:
            server.shutdown();server.server_close();thread.join()


class CentralAuthorityTests(unittest.TestCase):
    def test_peer_memberships_require_api_visibility_and_exclude_admins(self):
        from unittest.mock import patch
        expected={'Kunden':'global','lab':'customer'}
        responses=[[dict(id='router',groups=['customer'])],
                   [dict(id='router',groups=['customer','global'])]]
        with patch.object(r,'time') as clock:
            r.verified_peer_groups(lambda *args: responses.pop(0), 'router', expected, 'admins')
            self.assertEqual(clock.sleep.call_count,1)
        with self.assertRaisesRegex(r.Invalid,'ROUTER_IN_ADMINS_GROUP'):
            r.verified_peer_groups(lambda *args: [dict(id='router',groups=['customer','global','admins'])],
                                   'router', expected, 'admins')

    def test_native_acl_wait_requires_netbird_source_set(self):
        from unittest.mock import patch
        prefix={'prefix':{'addr':'10.30.1.0','len':24}}
        def rule(source):
            return {'nftables':[{'rule':{'expr':[
                {'match':{'op':'==','left':{'payload':{'protocol':'ip','field':'saddr'}},'right':source}},
                {'match':{'op':'==','left':{'payload':{'protocol':'ip','field':'daddr'}},'right':prefix}},
                {'counter':{'packets':0,'bytes':0}}, {'accept':None}]}}]}
        with patch.object(r,'command',return_value=json.dumps(rule('@nb-12345678'))):
            r.wait_native_route_acl('10.30.1.0/24')
        with patch.object(r,'command',return_value=json.dumps(rule('100.64.0.10'))), \
             patch.object(r.time,'sleep'):
            with self.assertRaisesRegex(r.Invalid,'NOT_READY'):
                r.wait_native_route_acl('10.30.1.0/24')

    def test_customer_cannot_be_shared_access_group(self):
        for name in ('Kunden','Zabbix-Kunden-Proxies'):
            with self.assertRaisesRegex(r.Invalid,'GROUP_COLLISION'):
                r.configuration(dict(ENV,CUSTOMER=name))

    def test_revoked_membership_never_regranted_on_resume(self):
        calls=[]
        def api(method,path,data=None):
            calls.append(method)
            g=dict(id='g1',name='lab',peers=None,peers_count=0,resources=None,resources_count=0)
            return [g] if path=='/groups' else g
        with self.assertRaisesRegex(r.Invalid,'REVOKED_NO_REGRANT'):
            r.member(api,'lab','old-peer',allow_write=False,expected='g1')
        self.assertTrue(all(x=='GET' for x in calls))


class TranslationReadinessTests(unittest.TestCase):
    def test_readiness_released_by_actual_owner_sigkill(self):
        import multiprocessing
        import tempfile
        from unittest.mock import patch
        def owner(path,ready):
            import fcntl,signal
            fd=os.open(Path(path)/'ready.lock',os.O_CREAT|os.O_RDWR,0o600)
            fcntl.flock(fd,fcntl.LOCK_EX)
            ready.send(True)
            signal.pause()
        with tempfile.TemporaryDirectory() as d:
            path=Path(d);(path/'binding.json').write_text(json.dumps(r.nat_context(ENV)))
            context=multiprocessing.get_context('fork')
            reader,writer=context.Pipe()
            process=context.Process(target=owner,args=(d,writer));process.start()
            try:
                self.assertTrue(reader.poll(5));reader.recv()
                with patch.object(r,'NAT_STATE',path),patch.object(r,'ROOT_UID',os.getuid()):
                    self.assertTrue(r.nat_ready(ENV))
                    self.assertFalse(r.nat_ready(dict(ENV,CUSTOMER='other')))
                    process.kill();process.join(5)
                    self.assertTrue((path/'ready.lock').exists())
                    self.assertFalse(r.nat_ready(ENV))
            finally:
                if process.is_alive(): process.kill();process.join()
                reader.close();writer.close()


if __name__ == '__main__': unittest.main()
