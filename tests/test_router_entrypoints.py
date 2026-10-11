"""Exercise wrapper control flow with intercepted system commands and fake data."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_router_security import Sandbox, ROOT


class PVEWrapperTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.env.update(NB_CUSTOMER_NAME='Synthetic customer', NB_ROLE='primary')
        source=(ROOT/'netbird-router/pve/install.sh').read_text()
        source=source.replace('[[ $EUID -ne 0 ]]','[[ 0 -ne 0 ]]')
        source=source.replace('[[ -c /dev/net/tun ]]',': # mocked TUN availability')
        source=source.replace('ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)',f'ROOT="{ROOT}"')
        source=source.replace('/var/lib/openmain-router-pve',str(self.root/'pve-state'))
        self.script=self.root/'install.sh';self.script.write_text(source)
        self.executable('pct', '''#!/usr/bin/env python3
import json,os,sys,subprocess
from pathlib import Path
p=Path(os.environ['MOCK_ROOT']);state=p/'ct.json';a=sys.argv[1:]
with (p/'pct-calls').open('a') as f:f.write(json.dumps(a)+'\\n')
s=json.loads(state.read_text()) if state.exists() else None
if a[0]=='status':
 if s: print('status: '+os.environ.get('MOCK_CT_STATUS','running'))
 else: sys.exit(1)
elif a[0]=='create':
 mask=os.umask(0);os.umask(mask)
 (p/'pct-create-umask').write_text(oct(mask))
 if os.environ.get('MOCK_CREATE_FAIL'):sys.exit(2)
 state.write_text(json.dumps({'id':a[1],'description':a[a.index('--description')+1]}))
elif a[0]=='config':print('description: '+s['description']+os.environ.get('MOCK_DESCRIPTION_SUFFIX',''))
elif a[0]=='exec' and a[3:5]==['python3','-c'] and os.environ.get('MOCK_REJECT_PREFLIGHT'):sys.exit(1)
elif a[0]=='exec' and a[3:5]==['bash','-c'] and 'Read-only bootstrap gate' in a[5] and os.environ.get('MOCK_REJECT_PRISTINE'):sys.exit(1)
elif a[0]=='exec' and a[3:5]==['python3','-c'] and os.environ.get('MOCK_PREFLIGHT_ROOT'):
 code=a[5].replace("root=Path('/')", "root=Path("+repr(os.environ['MOCK_PREFLIGHT_ROOT'])+")")
 code=code.replace('available=shutil.which', "available=lambda name: name in ('nft','ip')")
 sys.exit(subprocess.run([sys.executable,'-c',code,*a[6:]]).returncode)
elif a[0]=='exec' and a[3:5]==['python3','-c']:
 print(json.dumps({'path':os.environ.get('MOCK_PROFILE_PATH',''),'interface':'wt0'}))
elif a[0] in ('push','exec','start'):pass
else:sys.exit(98)
''')
        self.executable('pvesh','#!/bin/sh\necho 101\n')
        self.executable('pvesm','#!/bin/sh\nprintf "Name Type Status\\nlocal dir active\\n"\n')
        self.executable('pveam','#!/bin/sh\nif [ "$1" = available ]; then echo "system debian-13-standard_13.1-1_amd64.tar.zst"; fi\n')
        self.executable('modprobe','#!/bin/sh\nexit 0\n')
        self.executable('ip','#!/bin/sh\nexit 0\n')
    def run_wrapper(self,*args):
        r=subprocess.run(['bash',str(self.script),*args],env=self.env,capture_output=True,text=True,timeout=20)
        self.clean_output(r);return r
    def test_create_and_resume_same_ct_without_new_id_or_password_arguments(self):
        for _ in range(2):
            r=self.run_wrapper();self.assertEqual(r.returncode,0,r.stderr)
        calls=[json.loads(x) for x in (self.root/'pct-calls').read_text().splitlines()]
        create=[x for x in calls if x[0]=='create']
        self.assertEqual(len(create),1)
        self.assertEqual(create[0][create[0].index('--unprivileged')+1],'1')
        self.assertIn('firewall=1',create[0][create[0].index('--net0')+1])
        self.assertEqual((self.root/'pct-create-umask').read_text(),'0o22')
        self.assertNotIn('--password',create[0])
        joined=json.dumps(calls)
        for token in (self.env['NB_API_TOKEN'],self.env['NB_SETUP_KEY']):self.assertNotIn(token,joined)
    def test_resume_connected_profile_without_setup_key_and_reject_bad_guest(self):
        self.assertEqual(self.run_wrapper('--ctid','110').returncode,0)
        self.env['NB_SETUP_KEY']='';self.env['MOCK_PROFILE_PATH']='/var/lib/netbird/default.json'
        journal=self.root/'pct-calls';journal.unlink()
        before=(self.root/'ct.json').read_bytes()
        result=self.run_wrapper('--ctid','110');self.assertEqual(result.returncode,0,result.stderr)
        calls=[json.loads(line) for line in journal.read_text().splitlines()]
        self.assertFalse(any(call[0] in ('create','start','destroy') for call in calls))
        self.assertEqual((self.root/'ct.json').read_bytes(),before)
        journal.unlink();self.env['MOCK_REJECT_PREFLIGHT']='1'
        self.assertNotEqual(self.run_wrapper('--ctid','110').returncode,0)
        calls=[json.loads(line) for line in journal.read_text().splitlines()]
        self.assertFalse(any(call[0] in ('push','create','start','destroy') for call in calls))

    def test_new_container_requires_real_key_before_creation(self):
        self.env['NB_SETUP_KEY']=''
        result=self.run_wrapper('--ctid','110');self.assertNotEqual(result.returncode,0)
        calls=[json.loads(line) for line in (self.root/'pct-calls').read_text().splitlines()]
        self.assertFalse(any(call[0] in ('create','push','start') for call in calls))
        self.assertFalse((self.root/'ct.json').exists())

    def test_failed_creation_keeps_protected_retry_id_without_guest_changes(self):
        self.env['MOCK_CREATE_FAIL']='1'
        result=self.run_wrapper('--ctid','110')
        self.assertNotEqual(result.returncode,0)
        calls=[json.loads(x) for x in (self.root/'pct-calls').read_text().splitlines()]
        self.assertEqual(len([x for x in calls if x[0]=='create']),1)
        self.assertFalse(any(x[0] in ('exec','push','start') for x in calls))
        marker=next((self.root/'pve-state').glob('*.ctid'))
        self.assertEqual(marker.read_text(),'110\n')
        self.assertEqual(marker.stat().st_mode & 0o777,0o600)
        del self.env['MOCK_CREATE_FAIL']
        self.assertEqual(self.run_wrapper('--ctid','110').returncode,0)
        calls=[json.loads(x) for x in (self.root/'pct-calls').read_text().splitlines()]
        self.assertEqual([x[1] for x in calls if x[0]=='create'],['110','110'])

    def test_unrelated_ct_refused(self):
        (self.root/'ct.json').write_text(json.dumps({'id':'101','description':'Do not touch'}))
        result=self.run_wrapper('--ctid','101')
        self.assertNotEqual(result.returncode,0)
        calls=[json.loads(x)[0] for x in (self.root/'pct-calls').read_text().splitlines()]
        self.assertNotIn('exec',calls);self.assertNotIn('push',calls);self.assertNotIn('create',calls)
    def test_rejected_existing_guest_has_no_push_packages_services_or_start(self):
        self.assertEqual(self.run_wrapper().returncode,0)
        journal=self.root/'pct-calls'
        for env,value in [('MOCK_REJECT_PREFLIGHT','1'),('MOCK_CT_STATUS','stopped')]:
            with self.subTest(case=env):
                journal.unlink();self.env[env]=value
                before=(self.root/'ct.json').read_bytes()
                result=self.run_wrapper()
                self.assertNotEqual(result.returncode,0)
                calls=[json.loads(x) for x in journal.read_text().splitlines()]
                self.assertTrue(all(x[0] in ('status','config') or (x[0]=='exec' and x[3:5]==['python3','-c']) for x in calls))
                self.assertEqual((self.root/'ct.json').read_bytes(),before)
                del self.env[env]

    def test_encoded_terminal_newline_accepted_but_foreign_descriptions_refused(self):
        self.assertEqual(self.run_wrapper('--ctid','110').returncode,0)
        journal=self.root/'pct-calls'
        for suffix,accepted in [('%0A',True),('%0Aextra',False),('%0A%0A',False),('%250A',False),('%0D%0A',False),(' extra',False),('\ndescription: forged',False)]:
            with self.subTest(suffix=suffix):
                journal.unlink();self.env['MOCK_DESCRIPTION_SUFFIX']=suffix
                result=self.run_wrapper('--ctid','110')
                self.assertEqual(result.returncode==0,accepted,result.stderr)
                calls=[json.loads(x) for x in journal.read_text().splitlines()]
                self.assertFalse(any(x[0] in ('create','start','destroy') for x in calls))
                if not accepted:self.assertFalse(any(x[0] in ('push','exec') for x in calls))
        del self.env['MOCK_DESCRIPTION_SUFFIX']

    def test_resume_after_creation_before_guest_initialization_checks_before_push(self):
        self.env['MOCK_REJECT_PRISTINE']='1'
        result=self.run_wrapper('--ctid','110')
        self.assertNotEqual(result.returncode,0)
        journal=self.root/'pct-calls'
        first=[json.loads(x) for x in journal.read_text().splitlines()]
        self.assertEqual(len([x for x in first if x[0]=='create']),1)
        self.assertFalse(any(x[0]=='push' for x in first))
        before=(self.root/'ct.json').read_bytes()
        journal.unlink();del self.env['MOCK_REJECT_PRISTINE']
        self.env['MOCK_DESCRIPTION_SUFFIX']='%0A'
        guest=self.root/'guest';guest.mkdir()
        self.env['MOCK_PREFLIGHT_ROOT']=str(guest)
        self.executable('nft', """#!/bin/sh
case "$*" in
 "-j list tables") echo '{"nftables":[{"table":{"family":"inet","name":"filter"}}]}';;
 "-nn list ruleset") echo 'table inet filter { chain input { type filter hook input priority 0; policy drop; } }';;
 *) exit 98;;
esac
""")
        result=self.run_wrapper('--ctid','110')
        self.assertEqual(result.returncode,0,result.stderr)
        second=[json.loads(x) for x in journal.read_text().splitlines()]
        self.assertFalse(any(x[0] in ('create','start','destroy') for x in second))
        check=next(i for i,x in enumerate(second) if x[0]=='exec' and x[3:5]==['python3','-c'])
        push=next(i for i,x in enumerate(second) if x[0]=='push')
        self.assertLess(check,push)
        self.assertEqual((self.root/'ct.json').read_bytes(),before)
        self.assertEqual(next((self.root/'pve-state').glob('*.ctid')).read_text(),'110\n')

    def test_static_address_skips_dhcp_dependency(self):
        result=self.run_wrapper('--ip','192.0.2.20/24','--gateway','192.0.2.1')
        self.assertEqual(result.returncode,0,result.stderr)
        calls=[json.loads(x) for x in (self.root/'pct-calls').read_text().splitlines()]
        create=next(x for x in calls if x[0]=='create')
        self.assertIn('ip=192.0.2.20/24,gw=192.0.2.1',create[create.index('--net0')+1])


class FirstbootTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.fs=self.root/'fs';self.fs.mkdir()
        self.bundle=self.fs/'opt/openmain-router';self.bundle.mkdir(parents=True)
        (self.bundle/'router-install.sh').write_text('# fake core - never executed directly\n')
        self.config=self.fs/'etc/openmain-router/firstboot.json';self.config.parent.mkdir(parents=True)
        checker=self.bundle/'netbird-router/common/preflight.py'
        checker.parent.mkdir(parents=True)
        checker.write_text('import os,sys; sys.exit(int(os.environ.get("MOCK_REJECT_PREFLIGHT", "0")))')
        self.config.write_text('{"NB_CUSTOMER_NAME":"Synthetic"}')
        self.done=self.fs/'var/lib/openmain-router-firstboot.done'
        source=(ROOT/'firstboot-router.sh').read_text()
        for prefix in ('/var/','/etc/','/opt/'):
            source=source.replace(prefix,str(self.fs)+prefix)
        self.script=self.root/'firstboot.sh';self.script.write_text(source)
        self.executable('bash','#!/bin/sh\necho run >> "$MOCK_ROOT/core-calls"\nexit "${MOCK_CORE_RESULT:-0}"\n')
    def run_wrapper(self):
        return subprocess.run(['/bin/bash',str(self.script)],env=self.env,capture_output=True,text=True,timeout=10)
    def test_rejected_firstboot_keeps_input_and_never_calls_core(self):
        self.env['MOCK_REJECT_PREFLIGHT']='1'
        before=self.config.read_bytes()
        self.assertNotEqual(self.run_wrapper().returncode,0)
        self.assertEqual(self.config.read_bytes(),before)
        self.assertFalse((self.root/'core-calls').exists())
        self.assertFalse(self.done.exists())

    def test_failed_install_keeps_credentials_and_retry_marks_only_success(self):
        self.env['MOCK_CORE_RESULT']='1'
        self.assertNotEqual(self.run_wrapper().returncode,0)
        self.assertTrue(self.config.exists());self.assertFalse(self.done.exists())
        self.env['MOCK_CORE_RESULT']='0'
        r=self.run_wrapper();self.assertEqual(r.returncode,0,r.stderr)
        self.assertFalse(self.config.exists());self.assertTrue(self.done.exists())
        self.assertEqual(self.run_wrapper().returncode,0)
        self.assertEqual((self.root/'core-calls').read_text().splitlines(),['run','run'])


class FirewallTransactionTests(Sandbox):
    def test_invalid_candidate_does_not_apply_or_flush(self):
        source=(ROOT/'netbird-router/common/apply-binat.sh').read_text()
        rules=self.root/'rules.nft';rules.write_text('broken candidate')
        source=source.replace('/etc/nftables.d/openmain-binat.nft',str(rules))
        self.executable('nft','''#!/bin/sh
printf '%s\\n' "$*" >> "$MOCK_ROOT/nft-calls"
case "$1" in list) exit 0;; -c) exit 1;; *) exit 98;; esac
''')
        result=self.bash(source)
        self.assertNotEqual(result.returncode,0)
        calls=(self.root/'nft-calls').read_text().splitlines()
        self.assertEqual(len(calls),2)
        self.assertNotIn('flush ruleset',' '.join(calls))


class ISOBuilderTests(Sandbox):
    def setUp(self):
        super().setUp()
        import hashlib
        self.iso=self.root/'debian-13.1.0-amd64-netinst.iso'
        self.iso.write_bytes(b'synthetic ISO fixture, not bootable')
        self.output=self.root/'output.iso'
        self.env.update(DEBIAN_ISO=str(self.iso), DEBIAN_ISO_SHA512=hashlib.sha512(self.iso.read_bytes()).hexdigest(),
                        OUTPUT_ISO=str(self.output))
        self.executable('gpgv','#!/bin/sh\nexit 98\n') # local trusted checksum path needs no signature call
        self.executable('xorriso', '''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
a=sys.argv[1:];root=Path(os.environ['MOCK_ROOT'])
if '-extract' in a:
 dest=Path(a[a.index('-extract')+2])
 for folder in ('.disk','isolinux','boot/grub','EFI/BOOT'):(dest/folder).mkdir(parents=True,exist_ok=True)
 (dest/'.disk/info').write_text('Debian GNU/Linux '+os.environ.get('MOCK_DEBIAN_VERSION','13.1')+' netinst')
 (dest/'isolinux/txt.cfg').write_text('label install\\n append initrd=/install.amd/initrd.gz --- quiet\\n')
 (dest/'isolinux/menu.cfg').write_text('default install\\n')
 for file in ('boot/grub/grub.cfg','EFI/BOOT/grub.cfg'):
  (dest/file).write_text('set timeout=5\\nlinux /install.amd/vmlinuz --- quiet\\n')
elif '-outdev' in a:
 Path(a[a.index('-outdev')+1]).write_bytes(b'synthetic built image')
 tree=Path(a[a.index('-update_r')+1])
 (root/'iso-tree.json').write_text(json.dumps({str(p.relative_to(tree)):p.read_text() for p in tree.rglob('*') if p.is_file()}))
elif '-report_el_torito' in a:print('mock BIOS and EFI entries')
else:sys.exit(98)
''')
    def run_builder(self):
        return subprocess.run(['bash',str(ROOT/'build-iso.sh')],env=self.env,capture_output=True,text=True,timeout=20)
    def test_local_verified_netinst_patches_bios_uefi_and_copies_only_source(self):
        result=self.run_builder();self.assertEqual(result.returncode,0,result.stderr)
        tree=json.loads((self.root/'iso-tree.json').read_text())
        for menu in ('isolinux/txt.cfg','boot/grub/grub.cfg','EFI/BOOT/grub.cfg'):
            self.assertIn('file=/cdrom/preseed.cfg',tree[menu])
        self.assertIn('openmain-installer/router-install.sh',tree)
        self.assertIn('openmain-installer/netbird-router/common/network.py',tree)
        for secret in (self.env['NB_API_TOKEN'],self.env['NB_SETUP_KEY']):self.assertNotIn(secret,json.dumps(tree))
        self.assertTrue(Path(str(self.output)+'.sha512').exists())
        self.assertNotEqual(self.run_builder().returncode,0) # no overwrite
    def test_wrong_version_or_checksum_refused(self):
        self.env['MOCK_DEBIAN_VERSION']='14.0'
        self.assertNotEqual(self.run_builder().returncode,0)
        self.assertFalse(self.output.exists())
        self.env['DEBIAN_ISO_SHA512']='0'*128
        self.assertNotEqual(self.run_builder().returncode,0)
        self.assertFalse(self.output.exists())


class DHCPRecoveryTests(Sandbox):
    def setUp(self):
        super().setUp()
        devices=self.root/'interfaces';(devices/'enp1s0').mkdir(parents=True)
        source=(ROOT/'netbird-router/common/dhcp-recovery.sh').read_text()
        source=source.replace('/sys/class/net/*',str(devices/'*')).replace('/run/openmain-dhcp-recovery.lock',str(self.root/'dhcp.lock'))
        configuration=self.root/'interfaces.conf'
        configuration.write_text('auto enp1s0\niface enp1s0 inet static\n')
        self.configuration=configuration
        helper=self.root/'runtime.py'
        helper.write_text((ROOT/'netbird-router/common/runtime.py').read_text().replace("configuration='/etc/network/interfaces'",f'configuration={str(configuration)!r}'))
        self.script=self.root/'dhcp.sh';self.script.write_text(source)
        self.executable('systemctl','#!/bin/sh\nexit 1\n')
        self.executable('ifquery','#!/bin/sh\nprintf "iface enp1s0 inet %s\\n" "${MOCK_ADDRESS_MODE:-static}"\n')
        self.executable('ifup','#!/bin/sh\necho retry >> "$MOCK_ROOT/retries"\n')
        self.executable('ip','#!/bin/sh\nif [ "${MOCK_HAS_ADDRESS:-0}" = 1 ]; then echo "2: enp1s0 inet 192.0.2.1/24"; fi\n')
    def run_recovery(self,*args):
        return subprocess.run(['bash',str(self.script),*args],env=self.env,capture_output=True,text=True,timeout=10)
    def test_static_is_skipped_and_only_unaddressed_dhcp_retried(self):
        self.assertNotEqual(self.run_recovery('--detect').returncode,0)
        self.assertEqual(self.run_recovery().returncode,0)
        self.assertFalse((self.root/'retries').exists())
        self.env['MOCK_ADDRESS_MODE']='dhcp'
        self.configuration.write_text('auto enp1s0\niface enp1s0 inet dhcp\n')
        self.assertEqual(self.run_recovery('--detect').returncode,0)
        self.env['MOCK_HAS_ADDRESS']='1'
        self.assertEqual(self.run_recovery().returncode,0)
        self.assertFalse((self.root/'retries').exists())
        self.env['MOCK_HAS_ADDRESS']='0'
        self.assertEqual(self.run_recovery().returncode,0)
        self.assertEqual((self.root/'retries').read_text(),'retry\n')

    def test_classic_ifquery_empty_output_is_dhcp_and_existing_ipv4_is_untouched(self):
        self.configuration.write_text('auto enp1s0\niface enp1s0 inet dhcp\n')
        self.executable('ifquery','#!/bin/sh\nexit 0\n')
        self.env['MOCK_HAS_ADDRESS']='1'
        before=self.configuration.read_bytes()
        self.assertEqual(self.run_recovery('--detect').returncode,0)
        self.assertEqual(self.run_recovery().returncode,0)
        self.assertFalse((self.root/'retries').exists())
        self.assertEqual(self.configuration.read_bytes(),before)

    def test_networkd_detection_needs_no_jq_and_does_not_renew_existing_address(self):
        network=self.root/'eth0.network';network.write_text('[Network]\nDHCP=ipv4\n')
        self.executable('systemctl','#!/bin/sh\n[ "$3" = systemd-networkd ]\n')
        self.env['MOCK_NETWORK_FILE']=str(network)
        self.executable('networkctl', """#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
if 'status' in sys.argv:print(json.dumps({'NetworkFile':os.environ['MOCK_NETWORK_FILE']}))
else:
 (Path(os.environ['MOCK_ROOT'])/'retries').write_text('forbidden')
 sys.exit(98)
""")
        self.executable('jq','#!/bin/sh\necho forbidden >> "$MOCK_ROOT/jq-calls"\nexit 98\n')
        self.env['MOCK_HAS_ADDRESS']='1'
        self.assertEqual(self.run_recovery('--detect').returncode,0)
        self.assertEqual(self.run_recovery().returncode,0)
        self.assertFalse((self.root/'retries').exists());self.assertFalse((self.root/'jq-calls').exists())
