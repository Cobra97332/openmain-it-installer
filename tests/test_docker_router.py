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
    def test_resume_never_recreates_central_policy(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)
            (directory/'network.json').write_text(json.dumps(dict(network='network1',resource='resource1',router='router1',virtual='10.30.1.0/24')))
            calls=[]
            def api(method,path,data=None):
                calls.append((method,path))
                if path.endswith('/resources'):
                    return [dict(id='resource1',address='10.30.1.0/24',groups=['g1'],enabled=True)]
                if path.endswith('/routers'):
                    return [dict(id='router1',peer='peer1',metric=100,enabled=True,masquerade=False)]
                raise AssertionError('unexpected policy call')
            with patch.object(r,'PRIVATE',directory):
                r.mapping_binding(api, ENV, 'peer1','g1',[])
            self.assertTrue(all(method=='GET' and '/policies' not in path for method,path in calls))


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


if __name__ == '__main__': unittest.main()
