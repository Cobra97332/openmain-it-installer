"""Offline platform/API contracts. No production endpoints or host installation."""
import copy
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'netbird-router/common'))
import runtime
import network
import zabbix_sync
import pi_firstboot


class RuntimeTests(unittest.TestCase):
    def test_pi_timer_rejection_precedes_probe_api_or_config_write(self):
        with patch.object(pi_firstboot, 'config', return_value={'NB_CUSTOMER_NAME':'Synthetic','NB_ROLE':'primary'}), \
             patch.object(pi_firstboot, 'inspect', side_effect=ValueError('rejected')), \
             patch.object(pi_firstboot.subprocess, 'run') as run, \
             patch.object(pi_firstboot.subprocess, 'check_output') as read, \
             patch.object(pi_firstboot.urllib.request, 'urlopen') as api, \
             patch.object(pi_firstboot, 'atomic') as write:
            with self.assertRaises(ValueError):pi_firstboot.main()
            run.assert_not_called();read.assert_not_called();api.assert_not_called();write.assert_not_called()

    def test_platform_matrix(self):
        debian = {'ID': 'debian', 'VERSION_ID': '13'}
        for arch in ('x86_64', 'aarch64'):
            runtime.platform(debian, arch, memory_kib=1000000)
        for model in (3, 4, 5):
            runtime.platform(debian, 'aarch64', f'Raspberry Pi {model} Model B Rev 1.0', 800000)
        for osinfo, arch, model, ram in [({'ID':'debian','VERSION_ID':'12'}, 'aarch64','', 1000000),
                (debian,'armv7l','Raspberry Pi 3 Model B',800000),
                (debian,'aarch64','Raspberry Pi 2 Model B',800000),
                (debian,'aarch64','Raspberry Pi 5 Model B',400000)]:
            with self.assertRaises(runtime.Invalid):runtime.platform(osinfo,arch,model,ram)

    @staticmethod
    def addresses(iface='enp1s0', ip='192.168.50.10', prefix=24):
        return [{'ifname': iface, 'addr_info':[{'family':'inet','scope':'global','local':ip,'prefixlen':prefix}]}]

    def test_lan_ambiguity_missing_address_and_explicit_selection(self):
        routes = [{'dst':'default','dev':'enp1s0'}]
        self.assertEqual(runtime.lan(routes,self.addresses())['network'],'192.168.50.0/24')
        routes += [{'dst':'default','dev':'eth1'}]
        with self.assertRaises(runtime.Invalid):runtime.lan(routes,self.addresses())
        self.assertEqual(runtime.lan(routes,self.addresses(),'enp1s0')['address'],'192.168.50.10')
        with self.assertRaises(runtime.Invalid):runtime.lan([],[])  # DHCP not ready
        with self.assertRaises(runtime.Invalid):runtime.lan(routes,self.addresses(prefix=32),'enp1s0')
        with self.assertRaises(runtime.Invalid):runtime.lan([{'dst':'default','dev':'wt0'}],self.addresses('wt0'))

    def test_all_supported_prefixes_collision_and_reuse(self):
        for prefix in range(16,25):
            lan=f'192.168.0.0/{prefix}'
            mapped=runtime.mapping(lan,[])
            self.assertEqual(ipaddress.ip_network(mapped).prefixlen,prefix)
            self.assertEqual(runtime.mapping(lan,[],mapped),mapped)
        self.assertEqual(runtime.mapping('10.30.0.0/24',[]),'10.30.1.0/24')
        with self.assertRaises(runtime.Invalid):runtime.mapping('192.168.0.0/24',['10.30.0.0/16'])
        with self.assertRaises(runtime.Invalid):runtime.mapping('192.168.0.0/24',[],'10.40.0.0/16')
        with self.assertRaises(runtime.Invalid):runtime.mapping('192.168.0.0/24',['10.30.1.0/24'],'10.30.1.0/24')

    def test_nft_scopes_nat_and_never_flushes_foreign_rules(self):
        text=runtime.nft_config('enp1s0','wt0','10.30.1.0/24','192.168.0.0/24')
        self.assertNotIn('flush ruleset',text)
        self.assertIn('ct status dnat',text)
        self.assertIn('ip daddr 10.30.1.0/24',text)
        with self.assertRaises(runtime.Invalid):runtime.nft_config('eth0";drop','wt0','10.30.1.0/24','192.168.0.0/24')

    def test_pi_office_detection_including_same_prefix(self):
        routes=[{'dst':'default','dev':'enp1s0','gateway':'192.168.50.1'}]
        neighbors=[{'dst':'192.168.50.1','dev':'enp1s0','lladdr':'00:11:22:33:44:55'}]
        office=pi_firstboot.fingerprint(routes,self.addresses(),neighbors)
        self.assertFalse(pi_firstboot.moved(office,office))
        self.assertTrue(pi_firstboot.moved(office,dict(office,mac='00:11:22:33:44:66')))
        with self.assertRaises(runtime.Invalid):pi_firstboot.fingerprint(routes,self.addresses(),[])

    def test_atomic_state_and_insecure_config_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'config.json'
            runtime.atomic(path,'{"NB_CUSTOMER_NAME":"Example"}')
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            # Ownership mocked so this is also runnable as non-root in GitHub CI.
            actual=os.fstat
            def root_stat(fd):
                values=list(actual(fd));values[4]=0;return os.stat_result(values)
            with patch('runtime.os.fstat',side_effect=root_stat):
                self.assertEqual(runtime.config(path)['NB_CUSTOMER_NAME'],'Example')
                path.chmod(0o644)
                with self.assertRaises(runtime.Invalid):runtime.config(path)
            link=Path(temp)/'link';link.symlink_to(path)
            with self.assertRaises(OSError):runtime.config(link)


class NetBirdMock:
    def __init__(self):
        self.data={'/networks':[], '/routes':[], '/policies':[]}
        self.writes=[]
        self.counter=0
        self.fail=None

    def __call__(self,method,path,data=None):
        if self.fail==(method,path):raise runtime.Invalid('simulated outage')
        if method=='GET':return copy.deepcopy(self.data[path])
        self.writes.append((method,path,copy.deepcopy(data)))
        if method=='POST':
            self.counter+=1
            obj=dict(data,id=str(self.counter))
            self.data[path].append(obj)
            if path=='/networks':
                for child in ('resources','routers'):self.data[f"/networks/{obj['id']}/{child}"]=[]
            return copy.deepcopy(obj)
        if method=='PUT':
            parent,id=path.rsplit('/',1)
            obj=next(x for x in self.data[parent] if x['id']==id);obj.update(data)
            return copy.deepcopy(obj)
        raise AssertionError(method)


class NetworkTests(unittest.TestCase):
    def setUp(self):self.api=NetBirdMock()
    def run_router(self,role='primary',peer='p1',customer='Example'):
        return network.reconcile(self.api,customer,role,'192.168.50.0/24',peer,'customer-group',[])
    def test_fresh_repeat_backup_and_metric_repair(self):
        first=self.run_router();writes=len(self.api.writes)
        self.assertEqual(self.run_router(),first)
        self.assertEqual(len(self.api.writes),writes)
        backup=self.run_router('backup','p2')
        self.assertEqual(first['virtual'],backup['virtual'])
        routers=self.api.data['/networks/1/routers']
        self.assertEqual([r['metric'] for r in routers],[100,200])
        routers[0]['metric']=150
        self.run_router()
        self.assertEqual(routers[0]['metric'],100)
        with self.assertRaises(runtime.Invalid):self.run_router('primary','duplicate-primary')
    def test_backup_before_primary_and_partial_api_failures(self):
        with self.assertRaises(runtime.Invalid):self.run_router('backup','p2')
        self.api.fail=('POST','/networks/1/routers')
        with self.assertRaises(runtime.Invalid):self.run_router()
        self.api.fail=None
        result=self.run_router()
        self.assertEqual(len(self.api.data['/networks']),1)
        self.assertEqual(len(self.api.data['/networks/1/resources']),1)
        self.assertEqual(result['virtual'],'10.30.0.0/24')
    def test_foreign_groups_and_extra_policy_fields_preserved(self):
        self.run_router()
        resource=self.api.data['/networks/1/resources'][0]
        resource['groups'].append('keep-extra')
        policy=self.api.data['/policies'][0]
        policy['source_posture_checks']=['keep']
        before=copy.deepcopy(self.api.data)
        self.run_router()
        self.assertEqual(self.api.data,before)
        resource['groups']=['foreign']
        with self.assertRaises(runtime.Invalid):self.run_router()
        self.assertEqual(resource['groups'],['foreign'])
    def test_duplicate_and_disabled_resources_fail_closed(self):
        self.run_router()
        resource=self.api.data['/networks/1/resources'][0]
        resource.update(enabled=False,description='Manually disabled')
        with self.assertRaises(runtime.Invalid):self.run_router()
        self.api.data['/networks'].append({'id':'duplicate','name':'Example'})
        with self.assertRaises(runtime.Invalid):self.run_router()
    def test_other_customer_and_local_routes_cannot_overlap(self):
        self.run_router()
        result=network.reconcile(self.api,'Other','primary','192.168.60.0/24','p3','g2',[])
        self.assertEqual(result['virtual'],'10.30.1.0/24')
        with self.assertRaises(runtime.Invalid):
            network.reconcile(self.api,'Example','primary','192.168.50.0/24','p1','customer-group',[{'dst':'10.30.0.0/24','dev':'eth1'}])
    def test_malformed_lists_and_api_error_redacted(self):
        self.api.data['/networks']={}
        with self.assertRaises(runtime.Invalid):self.run_router()
        with patch('network.urllib.request.urlopen',side_effect=ValueError('synthetic-secret')):
            with self.assertRaises(runtime.Invalid) as error:network.API('https://api.invalid','synthetic-secret')('GET','/groups')
            self.assertNotIn('synthetic-secret',str(error.exception))


class ZabbixHostTests(unittest.TestCase):
    def setUp(self):
        access=patch.object(zabbix_sync,'require_access');access.start();self.addCleanup(access.stop)
        self.c={'url':'https://api.invalid','customer':'Example','host':'router-example-primary','proxy':'proxy-example-primary',
                'identity':'test-only-identity','psk_file':'/tmp/mock.psk','network':'192.168.50.0/24'}
        self.proxy={'proxyid':'p1','proxy_groupid':'g1','local_address':'192.168.50.10',
                    'local_port':'10051','tls_psk_identity':self.c['identity']}
        self.host=None;self.calls=[];self.extra=[]
    def api(self,method,params):
        self.calls.append((method,copy.deepcopy(params)))
        if method=='proxy.get':return [self.proxy]+(self.extra if 'proxy_groupids' in params else [])
        if method=='host.get':return [self.host] if self.host else []
        if method=='template.get':return [{'templateid':'t1' if params['filter']['host']==['Linux by Zabbix agent'] else 't2'}]
        if method=='item.get':return [{'itemid':'item1','type':'7','value_type':'3'}]
        if method=='hostgroup.get':return [{'groupid':'hg1'}]
        if method=='host.create':
            self.host=dict(params,hostid='h1')
            self.host['interfaces'][0]['interfaceid']='i1'
            return {'hostids':['h1']}
        if method=='hostinterface.update':self.host['interfaces'][0]['ip']=params['ip'];return {'interfaceids':['i1']}
        if method=='proxy.update':self.proxy.update(params);return {'proxyids':['p1']}
        raise AssertionError(method)
    def test_host_create_repeat_and_backup_failover_discovery(self):
        peers=zabbix_sync.register_host(self.api,self.c,'192.168.50.10','ab'*32)
        self.assertEqual(peers,['192.168.50.10'])
        self.assertEqual(self.host['monitored_by'],2)
        self.assertEqual(self.host['tls_connect'],2)
        count=len(self.calls)
        zabbix_sync.register_host(self.api,self.c,'192.168.50.10','ab'*32)
        self.assertFalse(any(m.endswith(('create','update')) for m,p in self.calls[count:]))
        self.extra=[{'proxyid':'p2','local_address':'192.168.50.11','local_port':'10051'}]
        peers=zabbix_sync.register_host(self.api,self.c,'192.168.50.10','ab'*32)
        conf=zabbix_sync.agent_config(self.c,peers)
        self.assertIn('ServerActive=192.168.50.10:10051;192.168.50.11:10051',conf)
        self.assertIn('Server=127.0.0.1,192.168.50.10,192.168.50.11',conf)
        self.assertIn('TLSAccept=psk',conf)
    def test_dhcp_address_update_without_tls_update(self):
        zabbix_sync.register_host(self.api,self.c,'192.168.50.10','ab'*32)
        zabbix_sync.register_host(self.api,self.c,'192.168.50.20','ab'*32)
        self.assertEqual(self.proxy['local_address'],'192.168.50.20')
        self.assertEqual(self.host['interfaces'][0]['ip'],'192.168.50.20')
        for method,params in self.calls:
            if method.endswith('update'):self.assertNotIn('tls_psk',params)
    def test_foreign_hosts_and_other_customer_addresses_rejected(self):
        zabbix_sync.register_host(self.api,self.c,'192.168.50.10','ab'*32)
        self.host['proxy_groupid']='foreign'
        with self.assertRaises(runtime.Invalid):zabbix_sync.register_host(self.api,self.c,'192.168.50.10','ab'*32)
        self.host['proxy_groupid']='g1'
        self.extra=[{'proxyid':'p2','local_address':'192.168.99.10','local_port':'10051'}]
        with self.assertRaises(runtime.Invalid):zabbix_sync.register_host(self.api,self.c,'192.168.50.10','ab'*32)
    def test_managed_agent_updates_preserve_custom_parameters_and_reject_tls_conflict(self):
        original=zabbix_sync.agent_config(self.c,['192.168.50.10'])+'Timeout=15\n# customer tuning\n'
        updated=zabbix_sync.merge_agent(original,self.c,['192.168.50.10','192.168.50.11'])
        self.assertIn('Timeout=15\n# customer tuning\n',updated)
        self.assertIn('192.168.50.11:10051',updated)
        self.assertEqual(zabbix_sync.merge_agent(updated,self.c,['192.168.50.10','192.168.50.11']),updated)
        with self.assertRaises(runtime.Invalid):
            zabbix_sync.merge_agent(original.replace('TLSConnect=psk','TLSConnect=cert'),self.c,['192.168.50.10'])

    def test_rpc_reflected_secrets_are_not_printed(self):
        with patch('zabbix_sync.urllib.request.urlopen',side_effect=ValueError('test-only-secret')):
            with self.assertRaises(runtime.Invalid) as error:zabbix_sync.API('https://api.invalid','test-only-secret')('host.get',{})
            self.assertNotIn('test-only-secret',str(error.exception))


class BundleTests(unittest.TestCase):
    def test_iso_bundle_is_explicit_and_contains_all_shared_dependencies(self):
        files=(ROOT/'netbird-router/bundle.files').read_text().splitlines()
        self.assertEqual(len(files),len(set(files)))
        for name in files:
            self.assertTrue((ROOT/name).is_file(),name)
            self.assertNotIn('..',name)
            self.assertNotIn('customer.json',name)
        for name in ('router-install.sh','netbird-router/common/runtime.py','netbird-router/common/network.py',
                     'netbird-router/common/zabbix_sync.py','firstboot-router.sh'):
            self.assertIn(name,files)
    def test_preseed_requires_disk_confirmation_and_contains_no_credentials(self):
        seed=(ROOT/'preseed.cfg').read_text()
        self.assertNotIn('partman/confirm boolean true',seed)
        self.assertNotIn('password-crypted',seed)
        self.assertIn('/opt/openmain-router',seed)
    def test_all_platforms_use_shared_core(self):
        for filename in ('netbird-router/pve/install.sh','netbird-router/vm/install.sh','firstboot-router.sh','netbird-router/common/pi_firstboot.py'):
            self.assertIn('router-install.sh',(ROOT/filename).read_text())
        self.assertEqual((ROOT/'netbird-router/pve/install-no-zabbix.sh').read_text().count('install.sh'),1)


if __name__=='__main__':unittest.main()
