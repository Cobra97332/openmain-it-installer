"""Fresh-host failures before jq installation; all effective commands intercepted."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from unittest.mock import patch

from test_router_security import Sandbox, ROOT

spec=importlib.util.spec_from_file_location('bootstrap_runtime',ROOT/'netbird-router/common/runtime.py')
runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)


class BootstrapTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.fs=self.root/'fs';self.fs.mkdir()
        bundle=self.root/'bundle';common=bundle/'netbird-router/common';common.mkdir(parents=True)
        source=(ROOT/'router-install.sh').read_text().replace('[[ $EUID -eq 0 ]]','[[ 0 -eq 0 ]]')
        source=re.sub(r'/(?:etc|var|usr/local|root)/',lambda m:str(self.fs)+m[0],source)
        self.script=bundle/'router-install.sh';self.script.write_text(source)
        (common/'apt_repository.py').write_text((ROOT/'netbird-router/common/apt_repository.py').read_text().replace("root=Path('/')",f'root=Path({str(self.fs)!r})').replace('ROOT_UID = 0','ROOT_UID = os.geteuid()').replace('ROOT_GID = 0','ROOT_GID = os.getegid()'))
        for directory in ('usr/share/keyrings','etc/apt/sources.list.d'):
            (self.fs/directory).mkdir(parents=True,exist_ok=True)
        # Match Debian's public system directories even if the test runner uses 077.
        self.fs.chmod(0o755)
        for directory in self.fs.rglob('*'):
            if directory.is_dir():directory.chmod(0o755)
        self.runtime=common/'runtime.py'
        # Local host version is irrelevant to this filesystem-isolated bootstrap.
        self.runtime.write_text((ROOT/'netbird-router/common/runtime.py').read_text().replace('platform(values, os.uname().machine, model, memory)','pass # isolated platform fixture'))
        checker=(ROOT/'netbird-router/common/preflight.py').read_text().replace("root=Path('/')",f'root=Path({str(self.fs)!r})')
        checker=checker.replace('PROFILE_UID = 0','PROFILE_UID = os.geteuid()')
        checker=checker.replace('available=shutil.which',"available=lambda name: name in ('nft','ip')")
        (common/'preflight.py').write_text(checker)
        # PATH contains no jq. Use the same fixture in the Debian 13 container.
        for name in ('bash','dirname','install','flock','hostname','locale'):
            (self.bin/name).symlink_to(shutil.which(name))
        (self.bin/'python3').symlink_to(sys.executable)
        self.env['PATH']=str(self.bin)
        self.assertIsNone(shutil.which('jq',path=self.env['PATH']))
        self.executable('ip', '''#!/bin/sh
case "$*" in *route*) echo '[{"dst":"default","dev":"eth0"}]';;
 *) echo '[{"ifname":"eth0","addr_info":[{"family":"inet","scope":"global","local":"192.168.178.158","prefixlen":24}]}]';; esac
''')
        self.executable('nft', '''#!/bin/sh
case "$*" in '-j list tables') echo '{"nftables":[]}';;
 '-nn list ruleset') echo 'table inet filter {}';; *) exit 98;; esac
''')
        self.executable('apt-get', '''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
p=Path(os.environ['MOCK_ROOT']);state=p/'fs/var/lib/netbird-kundenrouter/progress.json'
d=json.loads(state.read_text());assert d['phase']=='preflight'
assert state.stat().st_mode & 0o777 == 0o600
with (p/'packages').open('a') as f:f.write(d['phase']+'\\n')
sys.exit(int(os.environ.get('MOCK_APT_RESULT','42')))
''')
        self.executable('systemctl', '#!/bin/sh\nexit 31\n')
        self.env.update(NB_CUSTOMER_NAME='Synthetic "customer"',NB_ROLE='primary',NB_ZABBIX_ENABLED='0',NB_MANAGEMENT_URL='https://api.invalid')
        self.progress=self.fs/'var/lib/netbird-kundenrouter/progress.json'
    def run_core(self):
        r=subprocess.run(['/bin/bash',str(self.script)],env=self.env,capture_output=True,text=True,timeout=15)
        self.clean_output(r);return r
    def test_minimal_debian_installs_forwarding_dependency(self):
        # Execute the real package step, intercepting only external actions.
        source=(ROOT/'router-install.sh').read_text()
        function=source[source.index('install_packages(){'):source.index('\nsetup_access(){')]
        (self.bin/'python3').unlink()
        self.executable('python3', '#!/bin/sh\nexit 0\n')
        self.executable('netbird', '#!/bin/sh\nexit 0\n')
        self.executable('apt-get', '#!/bin/sh\nprintf "%s\\n" "$*" >> "$MOCK_ROOT/apt-arguments"\n')
        result=subprocess.run(['/bin/bash','-c',function+'\nSCRIPT_DIR=/fixture; install_packages'],
                              env=self.env,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        calls=(self.root/'apt-arguments').read_text().splitlines()
        self.assertEqual(calls[0],'update -y')
        self.assertIn('procps',calls[1].split())

    def test_no_jq_initial_checkpoint_failure_and_retry_are_safe(self):
        for _ in range(2):
            r=self.run_core();self.assertEqual(r.returncode,42,r.stderr)
            self.assertNotIn('jq: command not found',r.stderr)
            d=json.loads(self.progress.read_text())
            self.assertEqual(d,dict(phase='failed',customer=self.env['NB_CUSTOMER_NAME'],role='primary'))
            self.assertEqual(self.progress.stat().st_mode & 0o777,0o600)
            self.assertEqual(self.progress.parent.stat().st_mode & 0o777,0o700)
            self.assertEqual(sorted(p.name for p in self.progress.parent.iterdir()),['install.lock','progress.json'])
        self.assertEqual((self.root/'packages').read_text().splitlines(),['preflight','preflight'])
    def test_explicit_die_also_records_failure_without_jq(self):
        self.env['MOCK_APT_RESULT']='0'
        r=self.run_core();self.assertNotEqual(r.returncode,0)
        self.assertEqual(json.loads(self.progress.read_text())['phase'],'failed')
    def test_failed_error_checkpoint_keeps_original_exit_status_without_recursion(self):
        self.runtime.write_text(self.runtime.read_text().replace('    atomic(path, json.dumps',"    if phase == 'failed':\n        raise OSError('synthetic write failure')\n    atomic(path, json.dumps"))
        r=self.run_core();self.assertEqual(r.returncode,42,r.stderr)
        self.assertEqual(json.loads(self.progress.read_text())['phase'],'preflight')
        self.assertIn('Fehlerstatus konnte nicht gespeichert werden',r.stderr)
        self.assertNotIn('complete',self.progress.read_text())
    def test_checkpoint_replace_failure_preserves_old_file_and_cleans_temporary(self):
        runtime.checkpoint(self.progress,'preflight','Synthetic','primary')
        before=self.progress.read_bytes()
        with patch.object(runtime.os,'replace',side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):runtime.checkpoint(self.progress,'complete','Synthetic','primary')
        self.assertEqual(self.progress.read_bytes(),before)
        self.assertEqual([p.name for p in self.progress.parent.iterdir()],['progress.json'])


class InterfaceConfigurationTests(Sandbox):
    def test_ifupdown_sources_static_and_duplicate_methods_are_safe(self):
        config=self.root/'interfaces';directory=self.root/'interfaces.d';directory.mkdir()
        config.write_text('source-directory '+str(directory)+'\n')
        child=directory/'eth0';child.write_text('auto eth0\niface eth0 inet dhcp\n')
        ignored=directory/'eth0.disabled';ignored.write_text('iface eth0 inet static\n')
        before={p:p.read_bytes() for p in (config,child,ignored)}
        self.assertTrue(runtime.ifupdown_dhcp('eth0',config))
        child.write_text('iface eth0 inet static\n');self.assertFalse(runtime.ifupdown_dhcp('eth0',config))
        child.write_bytes(before[child]);config.write_text('source '+str(directory/'*')+'\n')
        self.assertFalse(runtime.ifupdown_dhcp('eth0',config))
        self.assertEqual(ignored.read_bytes(),before[ignored])
    def test_real_debian_ifquery_empty_output(self):
        binary=shutil.which('ifquery',path=os.environ['PATH'])
        if not binary:self.skipTest('Real ifupdown contract runs in Debian 13 CI image')
        config=self.root/'interfaces';config.write_text('auto eth0\niface eth0 inet dhcp\n')
        before=config.read_bytes()
        r=subprocess.run([binary,'--no-mappings','--no-scripts','--interfaces',str(config),'eth0'],capture_output=True,text=True,timeout=5)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(r.stdout,'')
        self.assertTrue(runtime.ifupdown_dhcp('eth0',config))
        self.assertEqual(config.read_bytes(),before)
