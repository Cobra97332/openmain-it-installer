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
import zabbix_registration
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
        self.proxy={'proxyid':'101','proxy_groupid':'201','local_address':'192.168.50.10',
                    'local_port':'10051','name':self.c['proxy'],'operating_mode':'0','tls_accept':'2'}
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.c['receipt_file']=str(Path(tmp.name)/'receipt.json')
        uid=patch.object(zabbix_registration,'ROOT_UID',os.getuid());uid.start();self.addCleanup(uid.stop)
        store=zabbix_registration.TLSStore(self.c['receipt_file'],self.c,'ab'*32)
        self.proxy['description']=store.prepare('proxy',self.c['proxy'],'201')
        store.confirm('proxy',self.proxy,'201')
        self.host=None;self.calls=[];self.extra=[]
    def api(self,method,params):
        self.calls.append((method,copy.deepcopy(params)))
        if method=='proxy.get':
            self.assertNotIn('tls_psk_identity',params['output']);self.assertNotIn('tls_psk',params['output'])
            return [{k:v for k,v in row.items() if k in params['output']} for row in [self.proxy]+(self.extra if 'proxy_groupids' in params else [])]
        if method=='host.get':return [{k:v for k,v in self.host.items() if k not in ('tls_psk_identity','tls_psk')}] if self.host else []
        if method=='template.get':return [{'templateid':'t1' if params['filter']['host']==['Linux by Zabbix agent'] else 't2'}]
        if method=='item.get':return [{'itemid':'item1','type':'7','value_type':'3'}]
        if method=='hostgroup.get':return [{'groupid':'hg1'}]
        if method=='host.create':
            self.host=dict(params,hostid='301')
            self.host['interfaces'][0]['interfaceid']='i1'
            return {'hostids':['301']}
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
    def test_host_get_extra_tag_metadata_is_idempotent_and_wrong_customer_denied(self):
        zabbix_sync.register_host(self.api,self.c,'192.168.50.10','ab'*32)
        self.host['tags'] = [dict(tag=tag['tag'],value=tag['value'],
                                  hosttagid=str(i + 100),automatic='0')
                             for i,tag in enumerate(self.host['tags'])]
        before = len(self.calls)
        zabbix_sync.register_host(self.api,self.c,'192.168.50.10','ab'*32)
        self.assertFalse(any(method.endswith(('.create','.update'))
                             for method,params in self.calls[before:]))
        for bad_tags in (
            [dict(tag='OpenMain customer',value='Other'),
             self.host['tags'][1]],
            [self.host['tags'][0],self.host['tags'][0],self.host['tags'][1]],
            [self.host['tags'][1]]
        ):
            with self.subTest(tags=bad_tags):
                self.host['tags'] = bad_tags
                before = len(self.calls)
                with self.assertRaisesRegex(zabbix_sync.SyncError,
                                            'HOST_OWNERSHIP_TLS_OR_MONITORING_CONFLICT'):
                    zabbix_sync.register_host(self.api,self.c,'192.168.50.10','ab'*32)
                self.assertFalse(any(method.endswith(('.create','.update'))
                                     for method,params in self.calls[before:]))
        # Restore real metadata for a final successful lookup.
        store=zabbix_registration.TLSStore(self.c['receipt_file'],self.c,'ab'*32)
        self.host['tags']=[
            dict(tag='OpenMain customer',value='Example',hosttagid='100',automatic='0'),
            dict(tag='OpenMain registration',value=store.entry('host')['marker'],
                 hosttagid='101',automatic='0')]
        zabbix_sync.register_host(self.api,self.c,'192.168.50.10','ab'*32)

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
        self.host['proxy_groupid']='201'
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

    def agent_resume_fixture(self):
        root = Path(self.c['receipt_file']).parent
        self.c.update(psk_file=str(root/'proxy.psk'), agent_config=str(root/'agent.conf'),
                      sync_file=str(root/'sync.json'), interface='eth0')
        runtime.atomic(self.c['psk_file'], 'ab'*32+'\n')
        runtime.atomic(root/'zabbix-intent.json', json.dumps({k:self.c[k] for k in ('customer','proxy','host')}))
        runtime.atomic(self.c['agent_config'], zabbix_sync.incomplete_agent_config(self.c['host']), 0o644)
        def network_read(args):
            if args == ['ip','-j','-4','addr','show']:
                return json.dumps([dict(ifname='eth0',addr_info=[dict(family='inet',scope='global',local='192.168.50.10',prefixlen=24)])]).encode()
            self.assertEqual(args, ['ip','-j','-4','route','show','table','main'])
            return b'[{"dst":"default","dev":"eth0"}]'
        read=patch.object(zabbix_sync.subprocess,'check_output',side_effect=network_read)
        read.start();self.addCleanup(read.stop)
        run=patch.object(zabbix_sync.subprocess,'run')
        self.run=run.start();self.addCleanup(run.stop);self.run.return_value.returncode=0
        return root

    def test_resume_after_proxy_registration_before_host_creation_is_idempotent(self):
        root=self.agent_resume_fixture()
        agent=Path(self.c['agent_config']);original=agent.read_bytes()
        key=Path(self.c['psk_file']);key_before=(key.read_bytes(),key.stat().st_ino,key.stat().st_mode)
        proxy_before=copy.deepcopy(zabbix_registration.TLSStore(self.c['receipt_file'],self.c,'ab'*32).entry('proxy'))
        created={};fail=True
        def api(method,params):
            nonlocal fail
            if method=='template.get' and params['filter']['host']==['OpenMain router active']:
                return [{'templateid':'t2'}] if 'template.create' in created else []
            if method=='item.get':return [{'itemid':'item1','type':'7','value_type':'3'}] if 'item.create' in created else []
            if method=='templategroup.get':return [{'groupid':'tg1'}]
            if method=='hostgroup.get':return [{'groupid':'hg1'}] if 'hostgroup.create' in created else []
            if method in ('template.create','item.create','hostgroup.create'):
                self.assertNotIn(method,created);created[method]=copy.deepcopy(params)
                field,oid={'template.create':('templateids','t2'),'item.create':('itemids','item1'),
                           'hostgroup.create':('groupids','hg1')}[method]
                return {field:[oid]}
            if method=='host.create' and fail:
                fail=False
                raise zabbix_registration.RPCError('Zabbix-API JSON-RPC -32500: simulierter Abbruch.')
            return self.api(method,params)
        with self.assertRaises(zabbix_registration.RPCError):zabbix_sync.sync(self.c,api)
        self.assertEqual(agent.read_bytes(),original);self.assertIsNone(self.host);self.run.assert_not_called()
        self.assertEqual(zabbix_registration.TLSStore(self.c['receipt_file'],self.c,'ab'*32).entry('proxy'),proxy_before)
        zabbix_sync.sync(self.c,api)
        completed=agent.read_bytes()
        self.assertIn(b'TLSConnect=psk\nTLSAccept=psk\n',completed)
        self.assertEqual(self.run.call_count,2)
        store=zabbix_registration.TLSStore(self.c['receipt_file'],self.c,'ab'*32)
        self.assertEqual(store.entry('host')['id'],'301');self.assertEqual(store.entry('proxy'),proxy_before)
        self.assertEqual((key.read_bytes(),key.stat().st_ino,key.stat().st_mode),key_before)
        call_count=len(self.calls);self.run.reset_mock()
        zabbix_sync.sync(self.c,api)
        self.assertEqual(agent.read_bytes(),completed);self.run.assert_not_called()
        self.assertFalse(any(m.endswith(('.create','.update')) for m,p in self.calls[call_count:]))
        self.assertEqual(len(created),3)
        self.assertEqual(store.path.stat().st_mode & 0o777,0o600)

    def test_incomplete_agent_requires_unchanged_intent_and_proxy_receipt(self):
        root=self.agent_resume_fixture()
        agent=Path(self.c['agent_config']);original=agent.read_text()
        intent=root/'zabbix-intent.json';intent_text=intent.read_text()
        receipt=Path(self.c['receipt_file']);receipt_text=receipt.read_text()
        def rejected(code):
            before=agent.read_bytes();self.calls.clear();self.run.reset_mock()
            with self.assertRaisesRegex(zabbix_sync.SyncError,code) as error:zabbix_sync.sync(self.c,self.api)
            self.assertNotIn('ab'*32,str(error.exception));self.assertNotIn(self.c['identity'],str(error.exception))
            self.assertEqual(agent.read_bytes(),before);self.assertEqual(self.calls,[]);self.run.assert_not_called()
        for changed in ('',original+'Timeout=15\n',original.replace('Server=127.0.0.1','Server=192.168.50.10'),
                        original+'TLSConnect=psk\n',original+'TLSConnect=cert\n',original.replace('# OpenMain','# foreign')):
            agent.write_text(changed);rejected('AGENT_INITIAL_CONFIG_MODIFIED|AGENT_TLS_OR_HOSTNAME_CONFLICT|AGENT_FILE_UNMANAGED')
        agent.write_text(original)
        intent.unlink();rejected('AGENT_INITIAL_INTENT_UNVERIFIED')
        runtime.atomic(intent,intent_text,0o644);rejected('AGENT_INITIAL_INTENT_UNVERIFIED')
        runtime.atomic(intent,json.dumps(dict(customer='Other',proxy=self.c['proxy'],host=self.c['host'])));rejected('AGENT_INITIAL_INTENT_UNVERIFIED')
        runtime.atomic(intent,intent_text.replace('{','{"customer":"Other",',1));rejected('AGENT_INITIAL_INTENT_UNVERIFIED')
        runtime.atomic(intent,intent_text)
        receipt.unlink();rejected('AGENT_INITIAL_PROXY_UNCONFIRMED')
        data=json.loads(receipt_text);data['objects']['proxy']['id']=None
        runtime.atomic(receipt,json.dumps(data));rejected('AGENT_INITIAL_PROXY_UNCONFIRMED')
        data=json.loads(receipt_text);data['binding']['identity']='other-identity'
        runtime.atomic(receipt,json.dumps(data));rejected('TLS_RECEIPT_INVALID')
        runtime.atomic(receipt,receipt_text)
        runtime.atomic(self.c['sync_file'],'{}');rejected('AGENT_INITIAL_ALREADY_COMPLETED')
        Path(self.c['sync_file']).unlink()
        store=zabbix_registration.TLSStore(self.c['receipt_file'],self.c,'ab'*32)
        store.prepare('host','other-host','201')
        runtime.atomic(receipt,json.dumps(store.data));rejected('AGENT_INITIAL_HOST_CONFLICT')

    def test_confirmed_host_with_failed_agent_validation_can_resume_without_duplicates(self):
        self.agent_resume_fixture();agent=Path(self.c['agent_config']);before=agent.read_bytes()
        self.run.return_value.returncode=1
        with self.assertRaisesRegex(zabbix_sync.SyncError,'AGENT_VALIDATION_FAILED'):zabbix_sync.sync(self.c,self.api)
        self.assertEqual(agent.read_bytes(),before)
        self.assertFalse(Path(self.c['sync_file']).exists())
        self.assertEqual(zabbix_registration.TLSStore(self.c['receipt_file'],self.c,'ab'*32).entry('host')['id'],'301')
        call_count=len(self.calls);self.run.reset_mock();self.run.return_value.returncode=0
        zabbix_sync.sync(self.c,self.api)
        self.assertIn('TLSAccept=psk',agent.read_text())
        self.assertFalse(any(m.endswith(('.create','.update')) for m,p in self.calls[call_count:]))

    def test_resume_revalidates_remote_proxy_before_agent_write_or_service_start(self):
        self.agent_resume_fixture();agent=Path(self.c['agent_config']);before=agent.read_bytes()
        self.proxy['description']='foreign marker'
        with self.assertRaisesRegex(zabbix_sync.SyncError,'PROXY_TLS_RECEIPT_UNVERIFIED'):
            zabbix_sync.sync(self.c,self.api)
        self.assertEqual(agent.read_bytes(),before);self.run.assert_not_called()
        self.assertFalse(any(m.endswith(('.create','.update')) for m,p in self.calls))

    def test_agent_symlinks_and_shared_files_are_rejected_without_api_writes(self):
        root=self.agent_resume_fixture();agent=Path(self.c['agent_config']);before=agent.read_bytes()
        os.link(agent,root/'shared')
        with self.assertRaisesRegex(zabbix_sync.SyncError,'AGENT_FILE_UNSAFE'):zabbix_sync.sync(self.c,self.api)
        agent.unlink();agent.symlink_to(root/'shared')
        with self.assertRaisesRegex(zabbix_sync.SyncError,'AGENT_FILE_UNSAFE'):zabbix_sync.sync(self.c,self.api)
        self.assertEqual((root/'shared').read_bytes(),before);self.assertEqual(self.calls,[]);self.run.assert_not_called()


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
