"""Offline security regression tests. Never execute a host installer or real API."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
# Synthetic sentinels only; no production credentials or endpoints.
TOKEN = "test-api-token-do-not-log"
PASSWORD = "test-password-do-not-log"
PSK = "ab" * 32


def functions(path, names):
    source = (ROOT / path).read_text()
    return "\n".join(re.search(r"^" + name + r"\(\)\{.*?^\}", source,
                                 re.M | re.S).group(0) for name in names)


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        NB_API_TOKEN=TOKEN, NB_SETUP_KEY="test-setup-key-do-not-log",
                        NB_CT_PASSWORD=PASSWORD, NB_ZABBIX_API_TOKEN=TOKEN,
                        MOCK_ROOT=str(self.root), NB_ZABBIX_API_URL="https://api.invalid")
        # Every externally effective command is intercepted. Unexpected calls fail.
        for cmd in ("curl", "netbird", "pct", "pvesh", "nft", "systemctl", "apt-get", "dpkg"):
            self.executable(cmd, "#!/bin/sh\nexit 97\n")

    def executable(self, name, text):
        path = self.bin / name
        path.write_text(text)
        path.chmod(0o755)
        return path

    def bash(self, text, *args):
        return subprocess.run(["bash", "-c", text, "test", *args], env=self.env,
                              capture_output=True, text=True, timeout=20)

    def clean_output(self, result):
        for secret in (PSK, TOKEN, PASSWORD, self.env["NB_SETUP_KEY"]):
            self.assertNotIn(secret, result.stdout + result.stderr)


class NetBirdTests(Sandbox):
    def run_function(self, name, response, args=(), extra=""):
        (self.root / "response.json").write_text(json.dumps(response))
        body = functions("router-install.sh", ["die", name])
        mock = r'''
api(){
  if [[ "$1" == GET ]]; then cat "$MOCK_ROOT/response.json";
  else printf '%s\n' "$1 $2" >> "$MOCK_ROOT/writes";
    printf '%s' "${3:-}" > "$MOCK_ROOT/payload.json";
    if [[ "$1" == PUT ]]; then
      jq --arg id group '. + {id:$id}' "$MOCK_ROOT/payload.json" > "$MOCK_ROOT/response.json"
    fi
    printf '{"id":"created"}'
  fi
}
log(){ :; }
'''
        result = self.bash('set -Eeuo pipefail\n' + body + mock + '''
METRICS_POLICY_NAME=metrics
MONITORING_GROUP_ID=monitor
METRICS_GROUP_ID=metrics
METRICS_PORT=9191
METRICS_DETAIL_PORT=9192
PEER_ID=new-peer
''' + extra + '\n' + name + ' "$@"', *args)
        self.clean_output(result)
        return result

    @staticmethod
    def policy():
        return {"id": "policy", "name": "metrics", "enabled": True,
                "source_posture_checks": ["keep-posture"],
                "rules": [{"enabled": True, "action": "accept", "protocol": "tcp",
                           "sources": ["monitor"], "destinations": [{"id": "metrics"}],
                           "ports": ["9191", "9192"]}, {"name": "keep-extra", "enabled": False}]}

    def test_existing_policy_and_posture_untouched(self):
        result = self.run_function("ensure_metrics_policy", [self.policy()])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "writes").exists())

    def test_conflicting_or_duplicate_policy_never_written(self):
        for policies in ([dict(self.policy(), enabled=False)], [self.policy(), self.policy()], {}):
            with self.subTest(policies=policies):
                result = self.run_function("ensure_metrics_policy", policies)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "writes").exists())

    def test_new_policy_still_created(self):
        result = self.run_function("ensure_metrics_policy", [])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "writes").read_text(), "POST /policies\n")

    def test_group_addition_preserves_members_and_resources_and_retry_is_noop(self):
        group = {"id": "group", "name": "Customer", "peers": [{"id": "old-peer"}],
                 "resources": [{"id": "resource", "type": "network"}]}
        result = self.run_function("ensure_peer_in_group", group, ("group",),
                                   'ensure_peer_in_group group')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "writes").read_text(), "PUT /groups/group\n")
        payload = json.loads((self.root / "payload.json").read_text())
        self.assertEqual(payload["peers"], ["new-peer", "old-peer"])
        self.assertEqual(payload["resources"], group["resources"])

    def test_incomplete_group_cannot_erase_members(self):
        result = self.run_function("ensure_peer_in_group", {"id": "group", "name": "Customer"}, ("group",))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "writes").exists())

    def test_duplicate_group_is_rejected(self):
        result = self.run_function("ensure_group", [{"id": "a", "name": "Customer"},
                                                   {"id": "b", "name": "Customer"}], ("Customer",))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "writes").exists())

    def test_transport_and_http_errors_do_not_echo_credentials(self):
        self.executable("curl", '''#!/usr/bin/env python3
import os,sys
from pathlib import Path
args=sys.argv
Path(args[args.index('-o')+1]).write_text(os.environ['NB_API_TOKEN'])
print(os.environ['NB_CT_PASSWORD'], file=sys.stderr)
print('403', end='')
sys.exit(int(os.environ.get('MOCK_CURL_EXIT','0')))
''')
        for rc in (0, 7):
            self.env["MOCK_CURL_EXIT"] = str(rc)
            result = self.bash('set -euo pipefail\n' + functions('router-install.sh', ['die', 'api']) +
                               '\nAPI_ROOT=https://api.invalid; API_TOKEN=$NB_API_TOKEN; api GET /groups')
            self.assertNotEqual(result.returncode, 0)
            self.clean_output(result)


    def test_enrollment_error_does_not_echo_setup_key(self):
        self.executable('netbird', '#!/bin/sh\nprintf "%s\\n" "$NB_SETUP_KEY" >&2\nexit 1\n')
        result = self.bash('set -euo pipefail\n' + functions('router-install.sh', ['die', 'connect_netbird']) + '''
wait_netbird(){ :; }
systemctl(){ :; }
SETUP_KEY=$NB_SETUP_KEY
MGMT_URL=https://api.invalid
METRICS_ENABLED=0
connect_netbird
''')
        self.assertNotEqual(result.returncode, 0)
        self.clean_output(result)

    def test_entrypoints_disable_inherited_tracing(self):
        for file in ('router-install.sh', 'firstboot-router.sh', 'netbird-router/pve/install.sh',
                     'netbird-router/pve/install-no-zabbix.sh', 'netbird-router/raspberry-pi/install-router.sh'):
            # Execute only initialization up to argument parsing/first prompt.
            source = (ROOT / file).read_text()
            if 'while [[ $# -gt 0 ]]' in source:
                source = source.split('while [[ $# -gt 0 ]]', 1)[0]
            else:
                source = source.split('echo "===', 1)[0]
            script = self.root / 'entrypoint.sh'
            script.write_text(source)
            result = subprocess.run(['bash', '-x', str(script)], env=self.env,
                                    capture_output=True, text=True, timeout=10)
            self.clean_output(result)


class ZabbixAPITests(Sandbox):
    def setUp(self):
        super().setUp()
        self.psk = self.root / "key.psk"
        self.psk.write_text(PSK)
        self.pending = self.root / "psk.pending"
        self.env["NB_ZABBIX_PSK_PENDING_FILE"] = str(self.pending)
        self.state = self.root / "api.json"
        self.state.write_text(json.dumps({"proxies": [], "groups": [], "writes": []}))
        self.executable("curl", '''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv
request=json.loads(args[args.index('--data')+1])
path=Path(os.environ['MOCK_ROOT'])/'api.json'
s=json.loads(path.read_text());m=request['method'];p=request['params']
if os.environ.get('MOCK_ERROR'):
 print(json.dumps({'jsonrpc':'2.0','id':1,'error':{'message':os.environ['NB_ZABBIX_API_TOKEN'],'data': 'ab'*32}}))
 sys.exit(0)
if os.environ.get('MOCK_MALFORMED'):
 print('{}');sys.exit(0)
if m=='proxy.get': result=s['proxies']
elif m=='proxygroup.get': result=s['groups']
elif m=='proxygroup.create':
 s['groups']=[dict(p,proxy_groupid='g')];result={'proxy_groupids':['g']}
elif m=='proxy.create':
 s['proxies']=[dict(p,proxyid='p')];result={'proxyids':['p']}
elif m=='proxy.update':
 s['proxies'][0].update(p);result={'proxyids':['p']}
else: sys.exit(98)
if m.endswith(('.create','.update')):s['writes'].append({'method':m,'params':p})
path.write_text(json.dumps(s))
print(json.dumps({'jsonrpc':'2.0','id':1,'result':result}))
''')

    def run_api(self, identity="Existing identity"):
        result = subprocess.run(['bash', '-x', str(ROOT / 'zabbix-api-register.sh'), 'customer',
                                 'primary', 'proxy-customer-primary', '192.0.2.10', identity, str(self.psk)],
                                env=self.env, capture_output=True, text=True, timeout=20)
        self.clean_output(result)
        return result

    def existing(self, **kwargs):
        proxy = dict(proxyid='p', name='proxy-customer-primary', operating_mode='0',
                     tls_accept='2', tls_psk_identity='Existing identity', tls_psk=PSK,
                     proxy_groupid='g', local_address='192.0.2.10', local_port='10051')
        proxy.update(kwargs)
        self.state.write_text(json.dumps({'proxies':[proxy], 'groups':[{'proxy_groupid':'g','name':'customer'}], 'writes':[]}))

    def test_create_then_repeat_preserves_tls(self):
        self.pending.touch()
        self.assertEqual(self.run_api().returncode, 0)
        self.assertFalse(self.pending.exists())
        self.assertEqual(self.run_api().returncode, 0)
        s = json.loads(self.state.read_text())
        self.assertEqual(len(s['proxies']), 1)
        self.assertEqual(len(s['groups']), 1)
        self.assertEqual(s['proxies'][0]['tls_psk'], PSK)
        updates = [x['params'] for x in s['writes'] if x['method']=='proxy.update']
        self.assertEqual(len(updates), 1)
        self.assertFalse(any(k.startswith('tls_') for k in updates[0]))

    def test_tls_or_group_conflict_has_no_writes(self):
        for options in ({'tls_psk_identity':'different'}, {'tls_accept':'1'}, {'tls_accept':TOKEN},
                        {'operating_mode':'1'}, {'proxy_groupid':'other'}):
            with self.subTest(options=options):
                self.existing(**options)
                self.assertNotEqual(self.run_api().returncode, 0)
                self.assertEqual(json.loads(self.state.read_text())['writes'], [])

    def test_unconfirmed_new_key_never_attaches_to_existing_proxy(self):
        self.existing()
        self.pending.touch()
        self.assertNotEqual(self.run_api().returncode, 0)
        self.assertTrue(self.pending.exists())
        self.assertEqual(json.loads(self.state.read_text())['writes'], [])

    def test_error_and_malformed_responses_are_safe(self):
        for option in ('MOCK_ERROR', 'MOCK_MALFORMED'):
            self.env[option] = '1'
            self.assertNotEqual(self.run_api().returncode, 0)
            self.env.pop(option)
        self.assertEqual(json.loads(self.state.read_text())['writes'], [])


class ZabbixInstallerTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.fs = self.root / 'fs'
        # Only the test copy is remapped. Production paths and guards stay fixed.
        source = (ROOT / 'zabbix-proxy-install.sh').read_text()
        source = source.replace('[[ $EUID -eq 0 ]]', '[[ 0 -eq 0 ]]')
        source = re.sub(r'/(?:etc|var|usr/local|root|home|tmp)/',
                        lambda match: str(self.fs) + match.group(0), source)
        self.script = self.root / 'installer.sh'
        self.script.write_text(source)
        for directory in ('root', 'etc/profile.d', 'tmp', 'var/log/zabbix'):
            (self.fs / directory).mkdir(parents=True, exist_ok=True)
        self.proxy = self.fs / 'etc/zabbix/zabbix_proxy.conf'
        self.agent = self.fs / 'etc/zabbix/zabbix_agent2.conf'
        self.key = self.fs / 'etc/zabbix/zabbix_proxy.psk'
        self.items = self.fs / 'etc/zabbix/zabbix_agent2.d/netbird-router.conf'
        for cmd in ('apt-get', 'dpkg', 'chown', 'sshd', 'systemctl', 'zabbix_proxy', 'zabbix_agent2'):
            self.executable(cmd, '''#!/bin/sh
printf '%s\\n' "$0 $*" >> "$MOCK_ROOT/commands"
exit 0
''')
        self.executable('id', '#!/bin/sh\nexit 1\n')
        self.executable('install', '''#!/usr/bin/env python3
import pathlib,sys
args=sys.argv[1:];i=0
while i<len(args):
 if args[i] in ('-o','-g','-m'):i+=2;continue
 if args[i]=='-d':i+=1;continue
 pathlib.Path(args[i]).mkdir(parents=True,exist_ok=True);i+=1
''')
        self.executable('ip', '''#!/bin/sh
case "$*" in
 *route*) echo 'default via 192.0.2.1 dev eth0';;
 *addr*) echo '2: eth0 inet 192.0.2.10/24 scope global eth0';;
 *) exit 97;;
esac
''')
        self.executable('curl', '''#!/usr/bin/env python3
import sys
from pathlib import Path
if not sys.argv[sys.argv.index('-o')-1].startswith('https://repo.zabbix.com/'):
 sys.exit(97)
Path(sys.argv[sys.argv.index('-o')+1]).write_text('fake package')
''')

    def run_installer(self):
        result = subprocess.run(['bash', '-x', str(self.script), '--customer', 'customer',
                                 '--server', '192.0.2.20', '--hostname', 'router-test'],
                                env=self.env, capture_output=True, text=True, timeout=20)
        self.clean_output(result)
        return result

    def existing(self, include=False):
        self.proxy.parent.mkdir(parents=True, exist_ok=True)
        self.items.parent.mkdir(parents=True, exist_ok=True)
        self.key.write_text(PSK + '\n')
        tls = f'TLSConnect=psk\nTLSPSKIdentity=Existing identity\nTLSPSKFile={self.key}\n'
        if include:
            included = self.proxy.parent / 'tls.conf'
            included.write_text(tls)
            tls = f'Include={included}\n'
        self.proxy.write_text('Hostname=proxy-customer-primary\nServer=192.0.2.20\n' + tls + f'DBName={self.fs}/var/lib/zabbix/zabbix_proxy.db\nCacheSize=64M\n')
        self.agent.write_text('Hostname=custom-agent\nServer=192.0.2.99\nTLSConnect=psk\n')
        self.items.write_text('UserParameter=keep,echo keep\n')

    def test_existing_files_key_identity_and_database_survive_reruns(self):
        self.existing(include=True)
        database = self.fs / 'var/lib/zabbix/zabbix_proxy.db'
        database.parent.mkdir(parents=True, exist_ok=True)
        import sqlite3
        with sqlite3.connect(database) as connection:
            connection.execute('CREATE TABLE sentinel (value TEXT)')
            connection.execute("INSERT INTO sentinel VALUES ('existing customer data')")
        files = (self.proxy, self.agent, self.items, self.key, database)
        before = {p:p.read_bytes() for p in files}
        self.key.chmod(0o640)
        for _ in range(2):
            result = self.run_installer()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual({p:p.read_bytes() for p in files}, before)
        with sqlite3.connect(self.fs / 'var/lib/netbird-kundenrouter/zabbix-proxy.sqlite3.previous') as backup:
            self.assertEqual(backup.execute('SELECT value FROM sentinel').fetchone()[0], 'existing customer data')
        self.assertEqual(self.key.stat().st_mode & 0o777, 0o640)
        info = (self.fs / 'etc/netbird-zabbix-info.conf').read_text()
        self.assertIn('Existing\\ identity', info)
        self.assertNotIn(PSK, (self.fs / 'var/lib/netbird-kundenrouter/zabbix-proxy.env').read_text())

    def test_missing_or_invalid_existing_key_is_not_regenerated(self):
        self.existing()
        for key in ('', 'invalid', None):
            if key is None:self.key.unlink()
            else:self.key.write_text(key)
            result = self.run_installer()
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((self.root / 'commands').exists())
            if key is not None:self.assertEqual(self.key.read_text(), key)
            else:self.assertFalse(self.key.exists())

    def test_orphan_key_and_duplicate_identity_abort_before_changes(self):
        self.existing()
        self.proxy.write_text(self.proxy.read_text() + 'TLSPSKIdentity=other\n')
        self.assertNotEqual(self.run_installer().returncode, 0)
        self.proxy.unlink()
        self.assertNotEqual(self.run_installer().returncode, 0)
        self.assertEqual(self.key.read_text(), PSK+'\n')
        self.assertFalse((self.root / 'commands').exists())

    def test_fresh_install_generates_key_once_and_hides_it(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        generated = self.key.read_text()
        self.assertRegex(generated, r'^[0-9a-f]{64}\n$')
        self.assertNotIn(generated.strip(), result.stdout + result.stderr)
        self.assertEqual(self.key.stat().st_mode & 0o777, 0o600)
        self.assertTrue((self.fs / 'var/lib/netbird-kundenrouter/zabbix-psk.pending').exists())
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.key.read_text(), generated)
        self.assertNotIn(generated.strip(), result.stdout + result.stderr)


    def test_validation_error_preserves_existing_files(self):
        self.existing()
        before = self.proxy.read_bytes()
        self.executable('zabbix_proxy', '#!/bin/sh\nprintf "%s\\n" "$NB_API_TOKEN" >&2\nexit 1\n')
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.proxy.read_bytes(), before)
        commands = (self.root / 'commands').read_text()
        self.assertNotIn('restart zabbix', commands)

    def test_api_failure_stops_before_service_restart(self):
        self.existing()
        helper = self.fs / 'usr/local/sbin/zabbix-api-register'
        helper.parent.mkdir(parents=True, exist_ok=True)
        helper.write_text('#!/bin/sh\nexit 1\n')
        helper.chmod(0o755)
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('restart zabbix', (self.root / 'commands').read_text())
        self.assertEqual(self.key.read_text(), PSK+'\n')


class PrometheusSyncTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('sync_targets', ROOT / 'netbird-prometheus/sync-client-targets.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_existing_policy_is_preserved_and_conflicts_never_put(self):
        policy = NetBirdTests.policy()
        before = copy.deepcopy(policy)
        with patch.object(self.module, 'api_request') as api:
            self.assertFalse(self.module.ensure_policy('', TOKEN, [policy], 'metrics', 'monitor', 'metrics', [9191,9192]))
            self.assertEqual(policy, before)
            policy['enabled'] = False
            with self.assertRaises(RuntimeError):
                self.module.ensure_policy('', TOKEN, [policy], 'metrics', 'monitor', 'metrics', [9191,9192])
            api.assert_not_called()

    def test_error_body_does_not_escape(self):
        error = urllib.error.HTTPError('https://api.invalid', 403, TOKEN, {}, io.BytesIO(PSK.encode()))
        with patch.object(self.module.urllib.request, 'urlopen', side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                self.module.api_request('https://api.invalid', TOKEN, 'GET', '/groups')
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertNotIn(PSK, str(caught.exception))


if __name__ == '__main__':
    unittest.main()
