"""Platform lifecycle simulations; no real OS installation or remote services."""
import copy
from contextlib import nullcontext
import json
import os
from pathlib import Path
import sys
import subprocess
from unittest.mock import patch

from test_router_security import Sandbox, ROOT
import runtime
import pi_firstboot


class BundleStagingTests(Sandbox):
    def test_pi_stages_only_manifest_and_preserves_customer_files(self):
        source=self.root/'source';source.mkdir()
        import shutil
        shutil.copytree(ROOT/'netbird-router',source/'netbird-router')
        for name in (ROOT/'netbird-router/bundle.files').read_text().splitlines():
            target=source/name;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes((ROOT/name).read_bytes())
        (source/'netbird-router/customer.json').write_text('{"secret":"synthetic-do-not-copy"}')
        (source/'netbird-prometheus/customer.env').write_text('TOKEN=synthetic-do-not-copy')
        target=self.root/'installed';target.mkdir()
        (target/'customer.json').write_text('existing synthetic input')
        for _ in range(2):runtime.bundle(source,target)
        expected=set((ROOT/'netbird-router/bundle.files').read_text().splitlines())|{'customer.json'}
        self.assertEqual({str(p.relative_to(target)) for p in target.rglob('*') if p.is_file()},expected)
        self.assertEqual((target/'customer.json').read_text(),'existing synthetic input')
        self.assertFalse((target/'netbird-router/customer.json').exists())

    def test_invalid_manifest_and_source_or_target_symlinks_never_partially_copy(self):
        src=self.root/'src';(src/'netbird-router').mkdir(parents=True)
        manifest=src/'netbird-router/bundle.files';(src/'first').write_text('safe')
        dst=self.root/'dst'
        for value in ('first\n../secret\n','first\nfirst\n','first\nmissing\n','first\n/absolute\n'):
            manifest.write_text(value)
            with self.assertRaises(runtime.Invalid):runtime.bundle(src,dst)
            self.assertFalse(dst.exists())
        (src/'second').symlink_to(src/'first');manifest.write_text('first\nsecond\n')
        with self.assertRaises(runtime.Invalid):runtime.bundle(src,dst)
        self.assertFalse(dst.exists())
        (src/'second').unlink();(src/'second').write_text('second');dst.mkdir()
        outside=self.root/'outside';outside.mkdir();(dst/'second').symlink_to(outside/'secret')
        with self.assertRaises(runtime.Invalid):runtime.bundle(src,dst)
        self.assertFalse((dst/'first').exists());self.assertFalse((outside/'secret').exists())


class PiDeploymentTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.fs=self.root/'fs';self.fs.mkdir()
        self.paths=lambda value:Path(value) if str(value).startswith(str(self.fs)+'/') else self.fs/str(value).lstrip('/')
        self.config=self.paths('/etc/openmain-router/pi.json');self.config.parent.mkdir(parents=True)
        self.values=dict(NB_CUSTOMER_NAME='Synthetic',NB_ROLE='primary',NB_MANAGEMENT_URL='https://management.invalid')
        runtime.atomic(self.config,json.dumps(self.values))
        self.office=self.paths('/etc/openmain-router/office.json')
        self.deployed=self.paths('/var/lib/openmain-pi-deployed')
        self.routes=[dict(dst='default',dev='eth0',gateway='192.0.2.1')]
        self.addresses=[dict(ifname='eth0',addr_info=[dict(family='inet',scope='global',local='192.0.2.10',prefixlen=24)])]
        self.neighbors=[dict(dst='192.0.2.1',dev='eth0',lladdr='00:11:22:33:44:55')]
        runtime.atomic(self.office,json.dumps(pi_firstboot.fingerprint(self.routes,self.addresses,self.neighbors)))
        self.api_attempts=0;self.core_attempts=0;self.commands=[];self.failed=False
    def read(self,args,**kwargs):
        self.assertEqual(args[:3],['ip','-j','-4'])
        return json.dumps(self.routes if args[3]=='route' else self.addresses if args[3]=='addr' else self.neighbors).encode()
    def execute(self,args,**kwargs):
        self.commands.append(args)
        if args[0]=='bash':
            self.core_attempts+=1
            if self.failed:raise subprocess.CalledProcessError(42,args)
        elif args[0] not in ('ping','systemctl'):self.fail('Unexpected command')
    def readiness(self,*args,**kwargs):
        self.api_attempts+=1
        if self.failed:raise OSError('synthetic unavailable API')
        return nullcontext()
    def invoke(self,mode='deploy'):
        with (patch.object(pi_firstboot,'Path',side_effect=self.paths),
              patch.object(pi_firstboot,'atomic',side_effect=lambda filename,text:runtime.atomic(self.paths(filename),text)),
              patch.object(pi_firstboot,'config',return_value=copy.deepcopy(self.values)),
              patch.object(pi_firstboot,'inspect'),patch.object(sys,'argv',['pi_firstboot.py',mode]),
              patch.object(pi_firstboot.subprocess,'check_output',side_effect=self.read),
              patch.object(pi_firstboot.subprocess,'run',side_effect=self.execute),
              patch.object(pi_firstboot.urllib.request,'urlopen',side_effect=self.readiness)):
            pi_firstboot.main()
    def test_office_wait_same_subnet_customer_move_api_failure_and_success(self):
        before=(self.office.read_bytes(),self.config.read_bytes())
        self.invoke();self.assertEqual(self.api_attempts,0);self.assertEqual(self.core_attempts,0)
        self.assertEqual((self.office.read_bytes(),self.config.read_bytes()),before)
        self.neighbors[0]['lladdr']='00:11:22:33:44:66';self.failed=True
        with self.assertRaises(OSError):self.invoke()
        self.assertFalse(self.deployed.exists());self.assertEqual(self.config.read_bytes(),before[1])
        self.failed=False;self.invoke()
        self.assertEqual(self.core_attempts,1);self.assertEqual(self.deployed.read_text(),'complete\n')
        self.assertFalse(self.config.exists())
        self.assertIn(['systemctl','disable','--now','openmain-pi-firstboot.timer'],self.commands)
    def test_core_failure_retains_credentials_and_never_marks_deployed(self):
        self.neighbors[0]['lladdr']='00:11:22:33:44:66';before=self.config.read_bytes()
        self.failed=True
        with patch.object(self,'readiness',return_value=nullcontext()):
            with self.assertRaises(subprocess.CalledProcessError):self.invoke()
        self.assertEqual(self.config.read_bytes(),before);self.assertFalse(self.deployed.exists())
        self.assertFalse(any(x[0]=='systemctl' for x in self.commands))
        self.failed=False;self.invoke();self.assertEqual(self.core_attempts,2)
    def test_repeated_preparation_keeps_original_office_fingerprint(self):
        before=self.office.read_bytes();self.neighbors[0]['lladdr']='00:11:22:33:44:66'
        self.invoke('prepare');self.assertEqual(self.office.read_bytes(),before)
        self.assertEqual(self.api_attempts,0);self.assertEqual(self.core_attempts,0)


class PiPreparationTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.fs=self.root/'fs';self.fs.mkdir()
        (self.fs/'etc/systemd/system').mkdir(parents=True)
        self.bundle=self.root/'source';runtime.bundle(ROOT,self.bundle)
        common=self.bundle/'netbird-router/common'
        validator=(common/'runtime.py').read_text().replace('info.st_uid != 0','info.st_uid != os.geteuid()')
        validator=validator.replace('platform(values, os.uname().machine, model, memory)','pass # simulated ARM64/model')
        (common/'runtime.py').write_text(validator)
        (common/'preflight.py').write_text('import json,os,sys; print(json.dumps({"path":""}));sys.exit(int(os.environ.get("MOCK_REJECT_PREFLIGHT","0")))')
        (common/'pi_firstboot.py').write_text('import os;from pathlib import Path; p=Path(os.environ["MOCK_ROOT"])/"office-calls";p.write_text(p.read_text()+"prepare\\n" if p.exists() else "prepare\\n")')
        self.model=self.root/'model';self.model.write_text('Raspberry Pi 4 Model B Rev 1.4\0')
        script=self.bundle/'netbird-router/raspberry-pi/install-router.sh'
        source=script.read_text().replace('[[ $EUID -eq 0 ]]','[[ 0 -eq 0 ]]')
        for prefix in ('/etc/','/var/','/opt/'):
            source=source.replace(prefix,str(self.fs)+prefix)
        script.write_text(source.replace('/proc/device-tree/model',str(self.model)))
        self.script=script;self.config=self.fs/'etc/openmain-router/pi.json'
        self.env.update(NB_CUSTOMER_NAME='Synthetic',NB_ROLE='primary',NB_MANAGEMENT_URL='https://management.invalid')
        for name in ('jq','ping','bash'):
            self.executable(name,'#!/bin/sh\nexit 0\n')
        self.executable('systemctl','#!/bin/sh\necho "$*" >> "$MOCK_ROOT/services"\n')
    def invoke(self):
        return subprocess.run(['/bin/bash',str(self.script)],env=self.env,capture_output=True,text=True,timeout=15)
    def test_prepare_retry_uses_saved_credentials_and_stages_only_manifest(self):
        result=self.invoke();self.assertEqual(result.returncode,0,result.stderr)
        saved=json.loads(self.config.read_text());self.assertIn('NB_SETUP_KEY',saved)
        self.assertEqual(self.config.stat().st_mode & 0o777,0o600)
        for key in ('NB_API_TOKEN','NB_SETUP_KEY','NB_ZABBIX_API_TOKEN'):self.env.pop(key,None)
        result=self.invoke();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(self.config.read_text()),saved)
        self.assertNotIn('API-Token:',result.stderr);self.assertNotIn('Setup-Key:',result.stderr)
        self.assertEqual((self.root/'office-calls').read_text(),'prepare\nprepare\n')
        staged=self.fs/'opt/openmain-router'
        self.assertTrue((staged/'netbird-router/bundle.files').exists())
        self.assertFalse((staged/'pi.json').exists())
    def test_prepare_admission_rejection_precedes_config_packages_and_services(self):
        self.env['MOCK_REJECT_PREFLIGHT']='1'
        result=self.invoke();self.assertNotEqual(result.returncode,0)
        self.assertFalse(self.config.exists());self.assertFalse((self.root/'services').exists())
        self.assertFalse((self.root/'office-calls').exists())


class VMPreparationTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.bundle=self.root/'source';runtime.bundle(ROOT,self.bundle)
        common=self.bundle/'netbird-router/common'
        (common/'runtime.py').write_text((common/'runtime.py').read_text().replace('info.st_uid != 0','info.st_uid != os.geteuid()'))
        (common/'preflight.py').write_text('import json,os; print(json.dumps({"path":os.environ.get("MOCK_PROFILE_PATH","")})); raise SystemExit(int(os.environ.get("MOCK_REJECT_PREFLIGHT","0")))')
        self.config=self.root/'etc/openmain-router/vm.json'
        self.script=self.bundle/'netbird-router/vm/install.sh'
        self.script.write_text(self.script.read_text().replace('[[ $EUID -eq 0 ]]','[[ 0 -eq 0 ]]').replace('/etc/openmain-router/vm.json',str(self.config)))
        self.env.update(NB_CUSTOMER_NAME='Synthetic',NB_ROLE='primary',NB_MANAGEMENT_URL='https://management.invalid')
        self.executable('bash','#!/bin/sh\necho run >> "$MOCK_ROOT/core-calls"\nexit "${MOCK_CORE_RESULT:-0}"\n')
    def invoke(self,*args):
        return subprocess.run(['/bin/bash',str(self.script),*args],env=self.env,capture_output=True,text=True,timeout=10)
    def test_failed_vm_install_reuses_saved_credentials_without_new_prompts(self):
        self.env['MOCK_CORE_RESULT']='42'
        result=self.invoke();self.assertEqual(result.returncode,42,result.stderr)
        saved=json.loads(self.config.read_text());self.assertIn('NB_SETUP_KEY',saved)
        self.assertEqual(self.config.stat().st_mode & 0o777,0o600)
        for key in ('NB_API_TOKEN','NB_SETUP_KEY','NB_ZABBIX_API_TOKEN'):self.env.pop(key,None)
        self.env['MOCK_CORE_RESULT']='0'
        result=self.invoke();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(self.config.read_text()),saved)
        self.assertNotIn('API-Token:',result.stderr);self.assertNotIn('Setup-Key:',result.stderr)
        self.assertEqual((self.root/'core-calls').read_text(),'run\nrun\n')
    def test_connected_vm_profile_needs_no_setup_key_and_explicit_json_is_reused(self):
        self.env.pop('NB_SETUP_KEY',None);self.env['MOCK_PROFILE_PATH']='/var/lib/netbird/default.json'
        explicit=self.root/'provided.json';runtime.atomic(explicit,json.dumps(self.env_subset()))
        result=self.invoke('--config',str(explicit));self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn('NB_SETUP_KEY',json.loads(explicit.read_text()))
        self.assertFalse(self.config.exists())
    def env_subset(self):
        return {k:self.env[k] for k in ('NB_CUSTOMER_NAME','NB_ROLE','NB_API_TOKEN','NB_ZABBIX_API_TOKEN','NB_MANAGEMENT_URL')}
    def test_rejected_vm_has_no_saved_input_or_core_calls(self):
        self.env['MOCK_REJECT_PREFLIGHT']='1'
        result=self.invoke();self.assertNotEqual(result.returncode,0)
        self.assertFalse(self.config.exists());self.assertFalse((self.root/'core-calls').exists())
