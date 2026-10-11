"""Admission regression: synthetic files only, read commands mocked, no APIs."""
import importlib.util
import json
import base64
import os
from unittest.mock import patch
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
        owner=patch.object(admission,'PROFILE_UID',os.geteuid());owner.start();self.addCleanup(owner.stop)
        self.fs=self.root/'fs';self.fs.mkdir()
        self.desired=dict(customer='Synthetic',role='primary',management_url='https://api.invalid',
                          zabbix_server='100.64.0.20',zabbix_enabled='1',lan_interface='')
        self.calls=[];self.tables=[];self.connected=True;self.live='';self.network='192.0.2.10'
        self.binaries={'nft','ip'}
        self.modern_status=None
        self.overlay_interface='wt-lab'
    def put(self,name,value):
        p=self.fs/name.lstrip('/');p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps(value) if isinstance(value,dict) else value);p.chmod(0o600)
        return p
    def command(self,*args):
        self.calls.append(args)
        if args==('nft','-j','list','tables'):
            return json.dumps({'nftables':[{'table':{'name':name}} for name in self.tables]})
        if args==('nft','-nn','list','ruleset'):return self.live
        if args==('nft','-s','list','table','ip','openmain_binat'):return self.live
        if args==('netbird','status','--json'):return json.dumps(self.modern_status or {'management':{'connected':self.connected}})
        if args==('ip','-j','-4','route','show','default'):return '[{"dst":"default","dev":"eth0"}]'
        if args==('ip','-j','-4','address','show'):
            return json.dumps([{'ifname':'eth0','addr_info':[{'family':'inet','scope':'global','local':self.network,'prefixlen':24}]}, {'ifname':self.overlay_interface,'addr_info':[{'family':'inet','scope':'global','local':'100.107.35.72','prefixlen':16}]}])
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
    def modern(self, name='default', user=''):
        self.managed();(self.fs/'etc/netbird/config.json').unlink()
        self.put('/var/lib/netbird-kundenrouter/progress.json',dict(customer='Synthetic',role='primary',phase='failed'))
        config=dict(ManagementURL='https://api.invalid',DisableFirewall=False,WgIface='wt-lab',
                    PrivateKey=base64.b64encode(bytes(range(32))).decode())
        filename='/var/lib/netbird/'+('default.json' if name=='default' else user+'/'+name+'.json')
        self.put(filename,config)
        self.put('/var/lib/netbird/active_profile.json',dict(name=name,username=user))
        self.put('/var/lib/netbird/state.json',dict(routes={},test='synthetic-state'))
        self.put('/var/lib/netbird/service.json',dict(LogFile='synthetic-service-state'))
        self.modern_status=dict(management=dict(connected=True,url='https://api.invalid'),
                                profileName=name,netbirdIp='100.107.35.72/16',daemonStatus='Connected')
        return self.fs/filename.lstrip('/')

    def test_netbird_080_default_and_state_files_resume_read_only(self):
        self.modern();before=self.snapshot()
        for _ in range(2):
            info=admission.inspect(self.fs,self.desired,self.command,lambda name:name in self.binaries,report=True)
            self.assertEqual(info,dict(path='/var/lib/netbird/default.json',interface='wt-lab'))
        self.assertEqual(self.snapshot(),before)
        self.assertTrue(all(c[0] in ('netbird','ip','nft') for c in self.calls))

    def test_named_active_profile_selected_over_default_and_inactive_profiles(self):
        selected=self.modern('profile-id','root')
        self.put('/var/lib/netbird/default.json',dict(ManagementURL='https://other.invalid',DisableFirewall=True,Name='inactive default'))
        self.put('/var/lib/netbird/root/another.json',dict(Name='inactive profile',ManagementURL='https://other.invalid'))
        self.put('/var/lib/netbird/root/profile-id.state.json',dict(email='synthetic'))
        before=self.snapshot();self.inspect()
        self.assertEqual(admission.resolve_profile(self.fs)['path'],'/'+str(selected.relative_to(self.fs)))
        self.assertEqual(self.snapshot(),before)

    def test_invalid_active_assignment_multiple_and_orphan_profiles_are_refused(self):
        self.modern();selector=self.fs/'var/lib/netbird/active_profile.json'
        for value in ({},dict(name='default'),dict(name='../default',username=''),dict(name='state',username=''),
                      dict(name='other',username=''),dict(name='other',username='../root'),dict(name='missing',username='root'),
                      dict(name=None,username=''),dict(name='default',username=False)):
            selector.write_text(json.dumps(value));self.rejected()
        selector.unlink();self.rejected()
        self.put('/var/lib/netbird/active_profile.json',dict(name='default',username=''))
        self.put('/var/lib/netbird/root/other.json',dict(Name='default'));self.rejected()

    def test_active_profile_security_endpoint_firewall_status_and_interface_conflicts(self):
        profile=self.modern();original=profile.read_text()
        for changes in (dict(DisableFirewall=True),dict(DisableFirewall='false'),dict(ManagementURL='https://other.invalid'),
                        dict(WgIface='bad/iface'),dict(PrivateKey='invalid')):
            value=json.loads(original);value.update(changes);profile.write_text(json.dumps(value));self.rejected()
        profile.write_text(original)
        for change in (dict(management=dict(connected=False,url='https://api.invalid')),
                       dict(management=dict(connected=True,url='https://other.invalid')),dict(profileName='other'),
                       dict(netbirdIp='100.107.35.73/16'),dict(daemonStatus='Connecting')):
            old=self.modern_status;self.modern_status=dict(old,**change);self.rejected();self.modern_status=old
        profile.chmod(0o644);self.rejected();profile.chmod(0o600)
        target=self.root/'outside.json';target.write_text(original);profile.unlink();profile.symlink_to(target);self.rejected()

    def test_binat_uses_selected_profile_interface_and_never_exposes_key(self):
        profile=self.modern();before=profile.read_bytes()
        helper=self.root/'netbird-router/common/preflight.py';helper.parent.mkdir(parents=True)
        helper.write_text((ROOT/'netbird-router/common/preflight.py').read_text().replace("root=Path('/')",f'root=Path({str(self.fs)!r})').replace('PROFILE_UID = 0','PROFILE_UID = os.geteuid()'))
        (self.root/'status.json').write_text(json.dumps(self.modern_status))
        (self.root/'addresses.json').write_text(self.command('ip','-j','-4','address','show'))
        self.executable('netbird','#!/bin/sh\n[ "$*" = "status --json" ] || exit 98\ncat "$MOCK_ROOT/status.json"\n')
        self.executable('ip','#!/bin/sh\ncat "$MOCK_ROOT/addresses.json"\n')
        self.executable('nft',"""#!/bin/sh
if [ "$1" = -c ] && [ "$2" = -f ]; then cp "$3" "$MOCK_ROOT/generated.nft"; fi
exit 98
""")
        body=functions('router-install.sh',['die','load_netbird_profile','write_nft']).replace('/etc/nftables.d',str(self.fs/'etc/nftables.d'))
        result=self.bash('set -Eeuo pipefail\n'+body+f"""
SCRIPT_DIR={self.root}
RUNTIME={ROOT}/netbird-router/common/runtime.py
NETBIRD_PROFILE=/var/lib/netbird/default.json
MGMT_URL=https://api.invalid
LAN_IF=eth0
VIRTUAL_NET=10.30.1.0/24
LAN_NET=192.0.2.0/24
write_nft
""")
        self.assertNotEqual(result.returncode,0)
        generated=(self.root/'generated.nft').read_text()
        self.assertIn('iifname "wt-lab"',generated);self.assertNotIn('iifname "wt0"',generated)
        self.assertEqual(profile.read_bytes(),before)
        self.assertNotIn(json.loads(before)['PrivateKey'],result.stdout+result.stderr)

    def test_older_management_url_object_remains_supported_and_checks_credentials(self):
        self.managed();self.put('/etc/netbird/config.json',dict(ManagementURL=dict(Scheme='https',Host='api.invalid:443',Path=''),WgIface='wt-old',DisableFirewall=False))
        before=self.snapshot();self.inspect();self.assertEqual(before,self.snapshot())
        self.assertEqual(admission.resolve_profile(self.fs)['interface'],'wt-old')
        self.put('/etc/netbird/config.json',dict(ManagementURL=dict(Scheme='https',Host='api.invalid',User=dict(Username='secret')),DisableFirewall=False))
        self.rejected()

    def test_profile_switch_during_readonly_status_check_is_refused(self):
        self.modern();before=self.snapshot()
        def switch(*args):
            response=self.command(*args)
            if args==('netbird','status','--json'):
                self.put('/var/lib/netbird/active_profile.json',dict(name='missing',username='root'))
            return response
        with self.assertRaises((admission.Rejected,OSError)):
            admission.inspect(self.fs,self.desired,switch,lambda name:name in self.binaries)
        # Only the simulated concurrent actor changed a file, never the checker.
        after=self.snapshot();changed=[k for k in before if before[k]!=after[k]]
        self.assertEqual(changed,['var/lib/netbird/active_profile.json'])

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

    def test_own_incomplete_agent_is_readonly_and_manual_changes_are_rejected(self):
        self.zabbix()
        (self.fs/'etc/openmain-router/zabbix-sync.json').unlink()
        intent=self.put('/var/lib/netbird-kundenrouter/zabbix-intent.json',dict(customer='Synthetic',proxy='proxy-test-primary',host='router-test-primary'))
        intent.parent.chmod(0o700)
        agent=self.put('/etc/zabbix/zabbix_agent2.conf',admission.incomplete_agent_config('router-test-primary'))
        before=self.snapshot();self.inspect();self.assertEqual(before,self.snapshot())
        original=agent.read_text()
        for changed in (original+'Timeout=15\n',original+'TLSConnect=psk\n',original.replace('127.0.0.1:10051','192.0.2.10:10051')):
            agent.write_text(changed);self.rejected()
        agent.write_text(original);intent.chmod(0o644);self.rejected()
    def test_pristine_and_supported_connected_router_are_read_only(self):
        before=self.snapshot();self.inspect();self.assertEqual(before,self.snapshot())
        self.zabbix()
        self.live=runtime.nft_config('eth0','wt0','10.30.1.0/24','192.0.2.0/24')
        self.put('/etc/nftables.d/openmain-binat.nft',self.live);self.tables=['openmain_binat','foreign_table']
        before=self.snapshot()
        for _ in range(2):self.inspect()
        self.assertEqual(self.snapshot(),before)
        self.assertIn(('netbird','status','--json'),self.calls)
    def test_unconfigured_resume_preserves_filter_rules_and_rejects_nat(self):
        good='table inet filter { chain input { type filter hook input priority 0; policy drop; ct status dnat accept; } }'
        self.live=good;self.tables=['filter']
        file=self.put('/etc/nftables.d/filter.nft',good)
        before=self.snapshot();self.inspect();self.assertEqual(self.snapshot(),before)
        for bad in ('table ip other { chain x { type nat hook prerouting priority -100; } }',
                    'table inet netbird { chain input { accept } }',
                    'table ip other { chain x { dnat to 192.0.2.2 } }'):
            with self.subTest(rules=bad):
                self.live=bad;self.rejected()
        self.live=good;file.write_text('table ip other { chain x { masquerade } }');self.rejected()

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
        checker=(ROOT/'netbird-router/common/preflight.py').read_text().replace("root=Path('/')",f'root=Path({str(self.fs)!r})').replace("PROFILE_UID = 0", "PROFILE_UID = os.geteuid()")
        (common/'preflight.py').write_text(checker)
        (common/'runtime.py').write_text('import sys\nassert sys.argv[1] == "platform"\n')
        for name in ('apt-get','systemctl','curl','sysctl','netbird','install','nft'):
            self.executable(name, '#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "$MOCK_ROOT/effects"\nexit 98\n')
        self.executable('nft','#!/bin/sh\n[ "$*" = "-j list tables" ] || exit 98\necho \'{"nftables":[]}\'\n')
        self.executable('ip','#!/bin/sh\ncase "$*" in *route*) echo \'[{"dst":"default","dev":"eth0"}]\';; *) echo \'[{"ifname":"eth0","addr_info":[{"family":"inet","scope":"global","local":"192.0.2.10","prefixlen":24}]}]\';; esac\n')
        self.env.update(NB_CUSTOMER_NAME='Synthetic',NB_ROLE='primary',NB_MANAGEMENT_URL='https://api.invalid',NB_ZABBIX_SERVER='100.64.0.20')
        # Profile-only disabled firewall must fail even without an old NFT file.
        self.managed();self.put('/etc/netbird/config.json',dict(ManagementURL='https://api.invalid',DisableFirewall=True))
        for scenario in ('disabled-profile','legacy-binat','manual-zabbix','changed-lan','modern-disconnected','modern-ambiguous'):
            with self.subTest(scenario=scenario):
                if scenario=='legacy-binat':self.put('/etc/nftables.d/netbird-kundenrouter.nft','table ip mein_binat {}')
                elif scenario=='manual-zabbix':
                    (self.fs/'etc/nftables.d/netbird-kundenrouter.nft').unlink()
                    self.managed();self.put('/etc/zabbix/zabbix_agent2.conf','Hostname=manual\n')
                    self.executable('netbird','#!/bin/sh\n[ "$*" = "status --json" ] || exit 98\necho \'{"management":{"connected":true}}\'\n')
                elif scenario=='changed-lan':self.put('/var/lib/netbird-kundenrouter/state.json',dict(customer='Synthetic',role='primary',lan='198.51.100.0/24'))
                elif scenario.startswith('modern-'):
                    self.env['NB_SETUP_KEY']=''
                    (self.fs/'etc/zabbix/zabbix_agent2.conf').unlink(missing_ok=True)
                    self.modern()
                    if scenario=='modern-disconnected':
                        self.executable('netbird','#!/bin/sh\necho \'{"management":{"connected":false}}\'\n')
                    else:self.put('/var/lib/netbird/root/other.json',dict(Name='default'))
                before=self.snapshot()
                result=subprocess.run(['bash',str(script)],env=self.env,capture_output=True,text=True,timeout=10)
                self.assertNotEqual(result.returncode,0);self.clean_output(result)
                self.assertIn('Bestandspruefung abgelehnt',result.stderr)
                self.assertFalse((self.root/'effects').exists())
                self.assertEqual(self.snapshot(),before)
    def test_connected_profile_never_starts_stops_or_reenrolls(self):
        profile=self.put('/etc/netbird/config.json',dict(ManagementURL='https://api.invalid',DisableFirewall=False))
        body=functions('router-install.sh',['die','connect_netbird'])
        helper=self.root/'netbird-router/common/preflight.py';helper.parent.mkdir(parents=True)
        helper.write_text((ROOT/'netbird-router/common/preflight.py').read_text().replace("root=Path('/')",f'root=Path({str(self.fs)!r})').replace('PROFILE_UID = 0','PROFILE_UID = os.geteuid()'))
        body=functions('router-install.sh',['die','load_netbird_profile','connect_netbird'])
        self.executable('netbird','#!/bin/sh\n[ "$*" = "status --json" ] || exit 98\necho \'{"management":{"connected":true}}\'\n')
        self.executable('systemctl','#!/bin/sh\necho forbidden >> "$MOCK_ROOT/effects"\nexit 98\n')
        r=self.bash('set -Eeuo pipefail\n'+body+f'\nSCRIPT_DIR={self.root}; PROFILE_EXISTED=1; NETBIRD_PROFILE=/etc/netbird/config.json; MGMT_URL=https://api.invalid; connect_netbird')
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
        for name in ('etc/netbird','etc/zabbix','var/lib/netbird-kundenrouter','etc/nftables.d/netbird-kundenrouter.nft','etc/nftables.d/openmain-binat.nft'):
            p=fs/name;p.parent.mkdir(parents=True,exist_ok=True);p.mkdir()
            self.assertNotEqual(check().returncode,0);self.assertTrue(p.is_dir());p.rmdir()

    def test_filter_rules_preserved_and_nat_or_legacy_rejected_without_writes(self):
        fs=self.root/'guest';fs.mkdir()
        source=(ROOT/'netbird-router/common/pristine.sh').read_text()
        source=re.sub(r'/(?:etc|var)/',lambda m:str(fs)+m[0],source)
        script=self.root/'pristine.sh';script.write_text(source)
        # Use only real shell/grep and intercepted nft; client sentinels are absent.
        isolated=self.root/'isolated-bin';isolated.mkdir()
        (isolated/'grep').symlink_to('/usr/bin/grep')
        nft=isolated/'nft';nft.write_text('#!/bin/sh\n[ "$*" = "-nn list ruleset" ] || exit 98\ncat "$MOCK_ROOT/rules"\n')
        nft.chmod(0o755);(isolated/'cat').symlink_to('/bin/cat')
        env=dict(self.env,PATH=str(isolated))
        live=self.root/'rules'
        conf=fs/'etc/nftables.conf';conf.parent.mkdir(parents=True)
        snippets=fs/'etc/nftables.d';snippets.mkdir()
        rulefile=snippets/'filter.nft'
        rulefile.write_text('table inet filter { chain input { type filter hook input priority 0; policy drop; ct state established accept; } }\n')
        conf.write_text('include "'+str(rulefile)+'"\n')
        # Avoid remapping an already absolute test fixture Include a second time.
        rules=rulefile.read_text();live.write_text(rules)
        def check():return subprocess.run(['/bin/bash',str(script)],env=env,capture_output=True,text=True,timeout=5)
        before={p:p.read_bytes() for p in (conf,rulefile,live)}
        self.assertEqual(check().returncode,0)
        for text in ('table ip mein_binat {}','table ip openmain_binat {}','table inet netbird {}',
                     'table ip other { chain x { type nat hook prerouting priority -100; } }',
                     'table ip other { chain x { ip daddr 192.0.2.1 dnat to 192.0.2.2 } }',
                     'table ip other { chain x { masquerade } }'):
            with self.subTest(rules=text):
                live.write_text(text);self.assertNotEqual(check().returncode,0)
                self.assertEqual(rulefile.read_bytes(),before[rulefile]);self.assertEqual(conf.read_bytes(),before[conf])
                self.assertEqual(live.read_text(),text)
        live.write_text(rules)
        rulefile.write_text('table ip legacy { chain x { type nat hook postrouting priority 100; } }')
        self.assertNotEqual(check().returncode,0)
        self.assertIn('type nat',rulefile.read_text())
