"""Public synthetic GPG certificates only; no package network or real credentials."""
import importlib.util
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_router_security import ROOT, Sandbox, functions

spec=importlib.util.spec_from_file_location('repository',ROOT/'netbird-router/common/apt_repository.py')
repository=importlib.util.module_from_spec(spec);spec.loader.exec_module(repository)


class RepositoryTests(Sandbox):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('gpg'):raise unittest.SkipTest('GPG public-certificate contract runs in Debian 13 CI image')
        cls.keys=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.keys.cleanup)
        cls.home=Path(cls.keys.name)
        cls.options=['gpg','--batch','--no-options','--homedir',str(cls.home)]
        subprocess.run(cls.options+['--pinentry-mode','loopback','--passphrase','','--quick-generate-key','Synthetic offline signing fixture','ed25519','sign','0'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
        cls.armored=subprocess.check_output(cls.options+['--armor','--export'],stderr=subprocess.DEVNULL)
        cls.binary=repository.public_key(cls.armored)
        cls.message=cls.home/'Release';cls.message.write_text('synthetic offline release\n')
        cls.signature=cls.home/'Release.gpg'
        subprocess.run(cls.options+['--pinentry-mode','loopback','--passphrase','','--detach-sign','--output',str(cls.signature),str(cls.message)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
    def setUp(self):
        super().setUp()
        for constant,value in [('ROOT_UID',os.geteuid()),('ROOT_GID',os.getegid())]:
            p=patch.object(repository,constant,value);p.start();self.addCleanup(p.stop)
        self.fs=self.root/'fs';self.fs.mkdir(mode=0o755)
        for directory in ('usr/share/keyrings','etc/apt/sources.list.d'):
            (self.fs/directory).mkdir(parents=True,exist_ok=True)
        self.fs.chmod(0o755)
        for directory in self.fs.rglob('*'):
            if directory.is_dir():directory.chmod(0o755)
        self.key=self.fs/repository.KEY.lstrip('/')
        self.source=self.fs/repository.SOURCE.lstrip('/')
        self.foreign=self.key.parent/'unrelated.gpg';self.foreign.write_bytes(b'unrelated fixture');self.foreign.chmod(0o600)
        self.secret=self.fs/'private.psk';self.secret.write_text('synthetic-only');self.secret.chmod(0o600)
    def reconcile(self,install=False,download=None):
        repository.reconcile(install,self.fs,download or (lambda:self.armored))
    def existing(self):
        self.key.write_bytes(self.binary);self.key.chmod(0o600)
        self.source.write_text(repository.LINE);self.source.chmod(0o600)
    def snapshot(self):
        return {str(p.relative_to(self.fs)):(p.lstat().st_mode,p.lstat().st_uid,p.lstat().st_gid,p.read_bytes() if p.is_file() and not p.is_symlink() else str(p.readlink()) if p.is_symlink() else None) for p in self.fs.rglob('*')}
    def assert_public_only(self):
        for path in (self.key,self.source):
            self.assertEqual(path.stat().st_mode & 0o777,0o644)
            self.assertEqual(path.stat().st_uid,os.geteuid())
            self.assertEqual(path.stat().st_gid,os.getegid())
        self.assertEqual(self.foreign.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.foreign.read_bytes(),b'unrelated fixture')
        self.assertEqual(self.secret.stat().st_mode & 0o777,0o600)
    def test_fresh_under_umask_077_and_repeat_preserve_public_and_private_files(self):
        previous=os.umask(0o077)
        try:self.reconcile(install=True)
        finally:os.umask(previous)
        self.assert_public_only();before=self.snapshot()
        self.reconcile(install=True);self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.key.read_bytes(),self.binary)
        self.assertEqual(self.source.read_text(),repository.LINE)
    def test_resume_repairs_0600_files_without_replacing_contents(self):
        self.existing();before={p:(p.read_bytes(),p.stat().st_ino) for p in (self.key,self.source)}
        self.reconcile();self.assert_public_only()
        self.assertEqual({p:(p.read_bytes(),p.stat().st_ino) for p in (self.key,self.source)},before)
    def test_unexpected_source_key_and_network_failure_have_no_mutations(self):
        for kind in ('source','key','transport'):
            with self.subTest(kind=kind):
                self.existing()
                if kind=='source':self.source.write_text(repository.LINE+'deb https://unrelated.invalid stable main\n')
                if kind=='key':self.key.write_bytes(self.binary+b'unexpected')
                before=self.snapshot()
                download=(lambda: (_ for _ in ()).throw(OSError('synthetic failure'))) if kind=='transport' else None
                with self.assertRaises((repository.Rejected,OSError)):self.reconcile(download=download)
                self.assertEqual(self.snapshot(),before)
    def test_symlinks_hardlinks_and_writable_files_refused_unchanged(self):
        self.existing();self.key.unlink();self.key.symlink_to(self.foreign)
        before=self.snapshot()
        with self.assertRaises(OSError):self.reconcile()
        self.assertEqual(self.snapshot(),before)
        self.key.unlink();self.existing();self.source.unlink();self.source.symlink_to(self.foreign)
        before=self.snapshot()
        with self.assertRaises(OSError):self.reconcile()
        self.assertEqual(self.snapshot(),before)
        self.source.unlink();self.existing();os.link(self.key,self.key.parent/'alias.gpg')
        before=self.snapshot()
        with self.assertRaises(repository.Rejected):self.reconcile()
        self.assertEqual(self.snapshot(),before)
        (self.key.parent/'alias.gpg').unlink();self.key.chmod(0o666)
        before=self.snapshot()
        with self.assertRaises(repository.Rejected):self.reconcile()
        self.assertEqual(self.snapshot(),before)
    def test_fresh_repair_needs_no_gpg_or_download(self):
        before=self.snapshot()
        with patch.object(repository,'public_key') as gpg:
            self.reconcile(download=lambda:self.fail('Unexpected download'))
        gpg.assert_not_called();self.assertEqual(self.snapshot(),before)
    def package_fixture(self):
        helper=self.root/'apt_repository.py'
        code=(ROOT/'netbird-router/common/apt_repository.py').read_text().replace("root=Path('/')",f'root=Path({str(self.fs)!r})').replace('ROOT_UID = 0','ROOT_UID = os.geteuid()').replace('ROOT_GID = 0','ROOT_GID = os.getegid()')
        code=code.replace('def fetch():','def original_fetch():')
        code=code.replace('def public_key(data):',f'def fetch():\n    return bytes.fromhex({self.armored.hex()!r})\n\ndef public_key(data):')
        helper.write_text(code)
        body=functions('router-install.sh',['die','install_packages']).replace('$SCRIPT_DIR/netbird-router/common/apt_repository.py',str(helper))
        self.env['MOCK_KEY']=str(self.key);self.env['MOCK_SOURCE']=str(self.source)
        self.executable('apt-get','''#!/usr/bin/env python3
import os,sys
from pathlib import Path
for name in ('MOCK_KEY','MOCK_SOURCE'):
 assert Path(os.environ[name]).stat().st_mode & 0o777 == 0o644
Path(os.environ['MOCK_ROOT'],'apt-called').write_text('verified before update')
sys.exit(42)
''')
        return body
    def test_repair_runs_before_first_apt_update(self):
        self.existing();body=self.package_fixture()
        r=self.bash('set -Eeuo pipefail\numask 077\n'+body+'\ninstall_packages')
        self.assertEqual(r.returncode,42,r.stderr);self.assertTrue((self.root/'apt-called').exists())
        self.assert_public_only()
    def test_unsafe_repository_stops_before_any_apt_command(self):
        self.existing();body=self.package_fixture()
        for kind in ('source','symlink'):
            with self.subTest(kind=kind):
                if kind=='source':self.source.write_text(repository.LINE+'# unexpected content\n')
                else:
                    self.source.write_text(repository.LINE)
                    self.key.unlink();self.key.symlink_to(self.foreign)
                before=self.snapshot()
                r=self.bash('set -Eeuo pipefail\numask 077\n'+body+'\ninstall_packages')
                self.assertNotEqual(r.returncode,0)
                self.assertFalse((self.root/'apt-called').exists())
                self.assertEqual(self.snapshot(),before)
    def test_apt_user_can_verify_public_key_but_cannot_read_private_fixture(self):
        if os.geteuid()!=0:self.skipTest('Actual _apt permission test runs in root Debian 13 CI container')
        try:user=pwd.getpwnam('_apt')
        except KeyError:self.skipTest('Debian APT sandbox account unavailable')
        self.existing();self.reconcile();self.root.chmod(0o755)
        release=self.fs/'Release';release.write_bytes(self.message.read_bytes());release.chmod(0o644)
        signature=self.fs/'Release.gpg';signature.write_bytes(self.signature.read_bytes());signature.chmod(0o644)
        def demote():os.setgroups([]);os.setgid(user.pw_gid);os.setuid(user.pw_uid)
        command=(['sqv','--keyring',str(self.key),'--signature-file',str(signature),str(release)] if shutil.which('sqv')
                 else ['gpgv','--keyring',str(self.key),str(signature),str(release)])
        r=subprocess.run(command,preexec_fn=demote,capture_output=True,timeout=5)
        self.assertEqual(r.returncode,0,'_apt could not verify synthetic repository signature')
        source_probe=subprocess.run([sys.executable,'-c','import sys;open(sys.argv[1]).read()',str(self.source)],preexec_fn=demote,capture_output=True,timeout=5)
        self.assertEqual(source_probe.returncode,0)
        release.write_text('tampered synthetic release\n')
        invalid=subprocess.run(command,preexec_fn=demote,capture_output=True,timeout=5)
        self.assertNotEqual(invalid.returncode,0,'Invalid signature must never be accepted')
        probe=subprocess.run([sys.executable,'-c','import sys;open(sys.argv[1]).read()',str(self.secret)],preexec_fn=demote,capture_output=True,timeout=5)
        self.assertNotEqual(probe.returncode,0)
