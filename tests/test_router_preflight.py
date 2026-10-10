"""Admission regression: synthetic files only, read commands mocked, no APIs."""
import importlib.util
import json
from pathlib import Path
import re
import subprocess

from test_router_security import Sandbox, ROOT, PSK, functions

spec = importlib.util.spec_from_file_location('admission', ROOT/'netbird-router/common/preflight.py')
admission = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admission)
spec = importlib.util.spec_from_file_location('router_runtime', ROOT/'netbird-router/common/runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class AdmissionTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.fs=self.root/'fs';self.fs.mkdir()
        self.desired=dict(customer='Synthetic',role='primary',management_url='https://api.invalid',
                          zabbix_server='100.64.0.20',zabbix_enabled='1',lan_interface='')
        self.calls=[];self.tables=[];self.connected=True;self.live='';self.network='192.0.2.10'
        self.binaries={'nft','ip'}
    def put(self,name,value):
        p=self.fs/name.lstrip('/');p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps(value) if isinstance(value,dict) else value);p.chmod(0o600)
        return p
    def command(self,*args):
        self.calls.append(args)
        if args==('nft','-j','list','tables'):
            return json.dumps({'nftables':[{'table':{'name':name}} for name in self.tables]})
        if args==('nft','-s','list','table','ip','openmain_binat'):return self.live
        if args==('netbird','status','--json'):return json.dumps({'management':{'connected':self.connected}})
        if args==('ip','-j','-4','route','show','default'):return '[{"dst":"default","dev":"eth0"}]'
        if args==('ip','-j','-4','address','show'):
            return json.dumps([{'ifname':'eth0','addr_info':[{'family':'inet','scope':'global','local':self.network,'prefixlen':24}]}])
        if args[0] in ('zabbix_proxy','zabbix_agent2') and args[1:3]==('-T','-c'):return ''
        self.fail('Unexpected command: '+str(args))
    def inspect(self):
        return admission.inspect(self.fs,self.desired,self.command,lambda name:name in self.binaries)
    def snapshot(self):
        return {str(p.relative_to(self.fs)):(p.lstat().st_mode,p.read_bytes() if p.is_file() and not p.is_symlink() else str(p.readlink()) if p.is_symlink() else None)
                for p in self.fs.rglob('*')}
    def managed(self):
        self.put('/var/lib/netbird-kundenrouter/state.json',dict(customer='Synthetic',role='primary',lan='192.0.2.0/24',virtual='10.30.1.0/24',peer_id='synthetic-peer'))
        self.put('/etc/netbird/config.json',dict(ManagementURL='https://api.invalid',DisableFirewall=False))
        self.binaries.add('netbird')
    def zabbix(self):
        self.managed()
        self.put('/etc/openmain-router/zabbix-sync.json',dict(customer='Synthetic',proxy='proxy-test-primary',host='router-test-primary',identity='Test identity',psk_file='/etc/zabbix/zabbix_proxy.psk',interface='eth0',network='192.0.2.0/24'))
        self.put('/etc/zabbix/zabbix_proxy.psk',PSK+'\n')
        tls='TLSConnect=psk\nTLSPSKIdentity=Test identity\nTLSPSKFile=/etc/zabbix/zabbix_proxy.psk\n'
        self.put('/etc/zabbix/zabbix_proxy.conf','Hostname=proxy-test-primary\nProxyMode=0\nServer=100.64.0.20\nDBName=/var/lib/zabbix/zabbix_proxy.db\n'+tls)
        self.put('/etc/zabbix/zabbix_agent2.conf','# OpenMain managed agent configuration v1\nHostname=router-test-primary\nTLSAccept=psk\n'+tls)
        self.binaries.update(('zabbix_proxy','zabbix_agent2'))
    def rejected(self):
        before=self.snapshot()
        with self.assertRaises((admission.Rejected,ValueError,OSError)):self.inspect()
        self.assertEqual(self.snapshot(),before)
    def test_pristine_and_supported_connected_router_are_read_only(self):
        before=self.snapshot();self.inspect();self.assertEqual(before,self.snapshot())
        self.zabbix()
        self.live=runtime.nft_config('eth0','wt0','10.30.1.0/24','192.0.2.0/24')
        self.put('/etc/nftables.d/openmain-binat.nft',self.live);self.tables=['openmain_binat','foreign_table']
        before=self.snapshot()
        for _ in range(2):self.inspect()
        self.assertEqual(self.snapshot(),before)
        self.assertIn(('netbird','status','--json'),self.calls)
    def test_disabled_unowned_alternate_and_disconnected_profiles_rejected(self):
        self.managed()
        profile=self.fs/'etc/netbird/config.json'
        for config in (dict(ManagementURL='https://api.invalid',DisableFirewall=True),dict(ManagementURL='https://other.invalid',DisableFirewall=False)):
            profile.write_text(json.dumps(config));self.rejected()
        self.managed();self.connected=False;self.rejected();self.connected=True
        extra=self.put('/etc/netbird/other.json',{});self.rejected();extra.unlink()
        (self.fs/'var/lib/netbird-kundenrouter/state.json').unlink();self.rejected()
    def test_old_rules_inactive_active_and_external_include_rejected(self):
        legacy=self.put('/etc/nftables.d/netbird-kundenrouter.nft','');self.rejected();legacy.unlink()
        self.tables=['mein_binat'];self.rejected();self.tables=[]
        self.put('/etc/nftables.conf','include "/opt/legacy/*.nft"\n')
        self.put('/opt/legacy/old.nft','table ip mein_binat {}');self.rejected()
    def test_changed_owned_rules_or_live_rules_rejected(self):
        self.managed()
        good=runtime.nft_config('eth0','wt0','10.30.1.0/24','192.0.2.0/24')
        self.put('/etc/nftables.d/openmain-binat.nft',good+'table ip extra {}');self.rejected()
        self.put('/etc/nftables.d/openmain-binat.nft',good)
        self.tables=['openmain_binat'];self.live=good.replace('100.64.0.0/10','0.0.0.0/0');self.rejected()
    def test_changed_customer_role_lan_and_missing_inspection_tool_rejected(self):
        self.managed()
        for key,value in [('customer','Other'),('role','backup')]:
            old=self.desired[key];self.desired[key]=value;self.rejected();self.desired[key]=old
        self.network='198.51.100.10';self.rejected();self.network='192.0.2.10'
        self.binaries.remove('nft');self.rejected()
    def test_manual_agent_changed_identity_missing_or_exposed_psk_rejected(self):
        self.zabbix()
        agent=self.fs/'etc/zabbix/zabbix_agent2.conf';saved=agent.read_text()
        agent.write_text('Hostname=manual\n');self.rejected();agent.write_text(saved)
        agent.write_text(saved.replace('Test identity','Different identity'));self.rejected();agent.write_text(saved)
        key=self.fs/'etc/zabbix/zabbix_proxy.psk';key.chmod(0o644);self.rejected()
        key.unlink();self.rejected()
    def test_symlink_and_orphan_state_rejected(self):
        p=self.put('/etc/zabbix/zabbix_proxy.psk',PSK);self.rejected();p.unlink()
        (self.fs/'etc/netbird').symlink_to(self.root/'absent');self.rejected()
    def test_actual_core_rejects_before_any_mutation(self):
        # Execute the complete script, remapping only filesystem paths/root guard
        # in a temporary copy. Every external effect is a recording fail sentinel.
        bundle=self.root/'bundle';common=bundle/'netbird-router/common';common.mkdir(parents=True)
        source=(ROOT/'router-install.sh').read_text().replace('[[ $EUID -eq 0 ]]','[[ 0 -eq 0 ]]')
        source=re.sub(r'/(?:etc|var|usr/local|root)/',lambda m:str(self.fs)+m[0],source)
        script=bundle/'router-install.sh';script.write_text(source)
        checker=(ROOT/'netbird-router/common/preflight.py').read_text().replace("root=Path('/')",f'root=Path({str(self.fs)!r})')
        (common/'preflight.py').write_text(checker)
        (common/'runtime.py').write_text('import sys\nassert sys.argv[1] == "platform"\n')
        for name in ('apt-get','systemctl','curl','sysctl','netbird','install','nft'):
            self.executable(name, '#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "$MOCK_ROOT/effects"\nexit 98\n')
        self.executable('nft','#!/bin/sh\n[ "$*" = "-j list tables" ] || exit 98\necho \'{"nftables":[]}\'\n')
        self.executable('ip','#!/bin/sh\ncase "$*" in *route*) echo \'[{"dst":"default","dev":"eth0"}]\';; *) echo \'[{"ifname":"eth0","addr_info":[{"family":"inet","scope":"global","local":"192.0.2.10","prefixlen":24}]}]\';; esac\n')
        self.env.update(NB_CUSTOMER_NAME='Synthetic',NB_ROLE='primary',NB_MANAGEMENT_URL='https://api.invalid',NB_ZABBIX_SERVER='100.64.0.20')
        # Profile-only disabled firewall must fail even without an old NFT file.
        self.managed();self.put('/etc/netbird/config.json',dict(ManagementURL='https://api.invalid',DisableFirewall=True))
        for scenario in ('disabled-profile','legacy-binat','manual-zabbix','changed-lan'):
            with self.subTest(scenario=scenario):
                if scenario=='legacy-binat':self.put('/etc/nftables.d/netbird-kundenrouter.nft','table ip mein_binat {}')
                elif scenario=='manual-zabbix':
                    (self.fs/'etc/nftables.d/netbird-kundenrouter.nft').unlink()
                    self.managed();self.put('/etc/zabbix/zabbix_agent2.conf','Hostname=manual\n')
                    self.executable('netbird','#!/bin/sh\n[ "$*" = "status --json" ] || exit 98\necho \'{"management":{"connected":true}}\'\n')
                elif scenario=='changed-lan':self.put('/var/lib/netbird-kundenrouter/state.json',dict(customer='Synthetic',role='primary',lan='198.51.100.0/24'))
                before=self.snapshot()
                result=subprocess.run(['bash',str(script)],env=self.env,capture_output=True,text=True,timeout=10)
                self.assertNotEqual(result.returncode,0);self.clean_output(result)
                self.assertIn('Bestandspruefung abgelehnt',result.stderr)
                self.assertFalse((self.root/'effects').exists())
                self.assertEqual(self.snapshot(),before)
    def test_connected_profile_never_starts_stops_or_reenrolls(self):
        profile=self.put('/etc/netbird/config.json',dict(ManagementURL='https://api.invalid',DisableFirewall=False))
        body=functions('router-install.sh',['die','connect_netbird']).replace('/etc/netbird/config.json',str(profile))
        self.executable('netbird','#!/bin/sh\n[ "$*" = "status --json" ] || exit 98\necho \'{"management":{"connected":true}}\'\n')
        self.executable('systemctl','#!/bin/sh\necho forbidden >> "$MOCK_ROOT/effects"\nexit 98\n')
        r=self.bash('set -Eeuo pipefail\n'+body+'\nPROFILE_EXISTED=1; MGMT_URL=https://api.invalid; connect_netbird')
        self.assertEqual(r.returncode,0,r.stderr);self.assertFalse((self.root/'effects').exists())


class PristineBootstrapTests(Sandbox):
    def test_fresh_template_admitted_but_any_existing_router_refused_without_effects(self):
        fs=self.root/'guest';fs.mkdir()
        source=(ROOT/'netbird-router/common/pristine.sh').read_text()
        source=re.sub(r'/(?:etc|var)/',lambda m:str(fs)+m[0],source)
        script=self.root/'pristine.sh';script.write_text(source)
        empty=self.root/'empty-bin';empty.mkdir()
        env=dict(self.env,PATH=str(empty))
        def check():return subprocess.run(['/bin/bash',str(script)],env=env,capture_output=True,text=True,timeout=5)
        self.assertEqual(check().returncode,0)
        for name in ('etc/netbird','etc/zabbix','etc/nftables.d','var/lib/netbird-kundenrouter'):
            p=fs/name;p.parent.mkdir(parents=True,exist_ok=True);p.mkdir()
            self.assertNotEqual(check().returncode,0);self.assertTrue(p.is_dir());p.rmdir()
