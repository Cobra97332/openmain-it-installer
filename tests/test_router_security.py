"""Offline security regression tests. Never execute a host installer or real API."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'netbird-router/common'))
import zabbix_registration as registration
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
            if secret:self.assertNotIn(secret, result.stdout + result.stderr)


class NetBirdTests(Sandbox):
    def run_function(self, name, response, args=(), extra="", reads=None):
        (self.root / "response.json").write_text(json.dumps(response))
        for file in ('writes','payload.json','reads','responses.json'):
            (self.root/file).unlink(missing_ok=True)
        if reads is not None:(self.root/'responses.json').write_text(json.dumps(reads))
        body = functions("router-install.sh", ["die"] + (["valid_group"] if name=="ensure_peer_in_group" else []) + [name])
        mock = r'''
api(){
  if [[ "$1" == GET ]]; then
    printf '%s\n' "$2" >> "$MOCK_ROOT/reads"
    if [[ -f "$MOCK_ROOT/responses.json" ]]; then
      local index
      index=$(wc -l < "$MOCK_ROOT/reads")
      jq -c --argjson index "$((index-1))" '.[$index]' "$MOCK_ROOT/responses.json"
    else cat "$MOCK_ROOT/response.json"; fi
  else printf '%s\n' "$1 $2" >> "$MOCK_ROOT/writes";
    printf '%s' "${3:-}" > "$MOCK_ROOT/payload.json";
    if [[ "$1" == PUT ]]; then
      jq --arg id group '. + {id:$id,peers_count:(.peers|length),resources_count:(.resources|length)}' "$MOCK_ROOT/payload.json" > "$MOCK_ROOT/response.json"
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

    def test_zabbix_source_membership_is_confirmed_before_policy_and_repeat_is_noop(self):
        source=dict(id='source',name='Zabbix-Kunden-Proxies',peers=['old-peer'],peers_count=1,
                    resources=[dict(id='keep-resource',type='network')],resources_count=1)
        server=dict(id='server',name='Zabbix-Server',peers=['server-peer'],peers_count=1,
                    resources=None,resources_count=0)
        policy=dict(self.policy(),name='Zabbix Proxy to Server')
        policy['rules'][0].update(sources=['source'],destinations=[dict(id='server')],ports=['10051'])
        data=dict(groups=[source,server],peers=[dict(id='server-peer',ip='100.64.0.20')],policies=[policy])
        state=self.root/'access.json';state.write_text(json.dumps(data))
        self.executable('mock-api', r'''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
root=Path(os.environ['MOCK_ROOT']);state=root/'access.json'
m,path=sys.argv[1:3];s=json.loads(state.read_text())
with (root/'access-calls').open('a') as log:log.write(m+' '+path+'\n')
if path=='/groups':r=s['groups']
elif path=='/peers':r=s['peers']
elif path=='/policies':r=s['policies']
else:
 r=next(g for g in s['groups'] if g['id']==path.rsplit('/',1)[1])
 if m=='PUT':
  p=json.loads(sys.argv[3]);r.update(p,peers_count=len(p['peers']),resources_count=len(p['resources']))
  state.write_text(json.dumps(s))
print(json.dumps(r))
''')
        helper=self.root/'netbird-router/common/zabbix_access.py';helper.parent.mkdir(parents=True)
        helper.write_text('''import json,os
from pathlib import Path
s=json.loads((Path(os.environ['MOCK_ROOT'])/'access.json').read_text())
assert 'new-peer' in s['groups'][0]['peers']
''')
        body=functions('router-install.sh',['die','valid_group','ensure_group','ensure_peer_in_group',
                                           'ensure_metrics_policy','setup_zabbix_access'])
        r=self.bash('set -Eeuo pipefail\n'+body+'''
api(){ mock-api "$@"; }
log(){ :; }
ZABBIX_ENABLED=1
ZABBIX_SERVER=100.64.0.20
ZABBIX_PROXY_GROUP=Zabbix-Kunden-Proxies
ZABBIX_SERVER_GROUP=Zabbix-Server
ZABBIX_POLICY_NAME='Zabbix Proxy to Server'
MGMT_URL=https://netbird.invalid
API_TOKEN=$NB_API_TOKEN
PEER_ID=new-peer
SCRIPT_DIR=$MOCK_ROOT
setup_zabbix_access
[[ "$PEER_ID" == new-peer ]]
setup_zabbix_access
''')
        self.assertEqual(r.returncode,0,r.stderr);self.clean_output(r)
        journal=(self.root/'access-calls').read_text().splitlines()
        self.assertEqual([x for x in journal if x.startswith(('PUT','POST'))],['PUT /groups/source'])
        update=journal.index('PUT /groups/source');policy_read=journal.index('GET /policies')
        self.assertEqual(journal[update+1],'GET /groups/source')
        self.assertLess(update+1,policy_read)
        after=json.loads(state.read_text())
        self.assertEqual(after['groups'][0]['peers'],['new-peer','old-peer'])
        self.assertEqual(after['groups'][0]['resources'],source['resources'])
        self.assertEqual(after['policies'],data['policies'])

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
        group = {"id": "group", "name": "Kunden", "peers": [{"id": "old-peer"}, "second-peer", {"id":"third-peer"}],
                 "resources": [{"id": "resource", "type": "network"}], "peers_count":3, "resources_count":1}
        result = self.run_function("ensure_peer_in_group", group, ("group",),
                                   'ensure_peer_in_group group')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "writes").read_text(), "PUT /groups/group\n")
        payload = json.loads((self.root / "payload.json").read_text())
        self.assertEqual(payload["peers"], ["new-peer", "old-peer", "second-peer", "third-peer"])
        self.assertEqual(len((self.root/'reads').read_text().splitlines()),4)
        self.assertEqual(payload["resources"], group["resources"])

    @staticmethod
    def empty_group():
        return dict(id='group',name='Bauer-PR30',peers=None,resources=None,peers_count=0,resources_count=0)

    def test_null_empty_groups_are_reused_and_repeat_is_noop(self):
        for name in ('Bauer-PR30','Kunde:Bauer-PR30'):
            with self.subTest(name=name):
                group=dict(self.empty_group(),name=name)
                r=self.run_function('ensure_group',[group],(name,))
                self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(r.stdout,'group')
                self.assertFalse((self.root/'writes').exists())
                r=self.run_function('ensure_peer_in_group',group,('group',),'ensure_peer_in_group group')
                self.assertEqual(r.returncode,0,r.stderr)
                self.assertEqual((self.root/'writes').read_text(),'PUT /groups/group\n')
                self.assertEqual(json.loads((self.root/'payload.json').read_text()),dict(name=name,peers=['new-peer'],resources=[]))
                self.assertEqual(len((self.root/'reads').read_text().splitlines()),4)

    def test_invalid_counters_collections_and_ids_never_written(self):
        cases=[]
        for field,count in (('peers','peers_count'),('resources','resources_count')):
            for value in (None,'0',False,-1,0.5,1,{},[]):cases.append(dict(self.empty_group(),**{count:value}))
            for missing in (field,count):
                group=self.empty_group();del group[missing];cases.append(group)
            for value in ({},'empty',0,True):cases.append(dict(self.empty_group(),**{field:value}))
        cases += [dict(self.empty_group(),id='other'),dict(self.empty_group(),id='../group'),
                  dict(self.empty_group(),peers=[''],peers_count=1),
                  dict(self.empty_group(),peers=[{}],peers_count=1),
                  dict(self.empty_group(),peers=['bad/id'],peers_count=1),
                  dict(self.empty_group(),peers=['peer\n'],peers_count=1),
                  dict(self.empty_group(),peers=['peer'],peers_count=None),
                  dict(self.empty_group(),peers=['peer','peer'],peers_count=2),
                  dict(self.empty_group(),peers=['peer'],peers_count=0),
                  dict(self.empty_group(),resources=[dict(id='r')],resources_count=1),
                  dict(self.empty_group(),resources=[dict(id='r',type='network')],resources_count=0),
                  dict(self.empty_group(),resources=[dict(id='r',type='network'),dict(id='r',type='host')],resources_count=2),
                  dict(self.empty_group(),resources=['r'],resources_count=1),[],None]
        for group in cases:
            with self.subTest(group=group):
                r=self.run_function('ensure_peer_in_group',group,('group',))
                self.assertNotEqual(r.returncode,0);self.assertFalse((self.root/'writes').exists())
        for identifier in ('','bad/id','null id'):
            r=self.run_function('ensure_peer_in_group',self.empty_group(),(identifier,))
            self.assertNotEqual(r.returncode,0);self.assertFalse((self.root/'reads').exists())
        r=self.run_function('ensure_peer_in_group',self.empty_group(),('group',),'PEER_ID=bad/peer')
        self.assertNotEqual(r.returncode,0);self.assertFalse((self.root/'reads').exists())

    def test_changed_group_before_update_is_not_overwritten(self):
        original=self.empty_group()
        for changed in (dict(original,peers=['other'],peers_count=1),dict(original,name='other'),{}):
            r=self.run_function('ensure_peer_in_group',original,('group',),reads=[original,changed])
            self.assertNotEqual(r.returncode,0);self.assertFalse((self.root/'writes').exists())

    def test_update_verification_checks_all_members_and_resources(self):
        original=dict(self.empty_group(),peers=['old-peer'],peers_count=1,
                      resources=[dict(id='resource',type='network')],resources_count=1)
        updated=dict(original,peers=['old-peer','new-peer'],peers_count=2)
        bad=[dict(updated,peers=['new-peer'],peers_count=1),
             dict(updated,peers=['old-peer'],peers_count=1),
             dict(updated,resources=[],resources_count=0),
             dict(updated,resources=[dict(id='resource',type='host')]),
             dict(updated,name='changed'),dict(updated,id='other'),
             dict(updated,peers_count=0),{},None]
        for current in bad:
            with self.subTest(current=current):
                r=self.run_function('ensure_peer_in_group',original,('group',),reads=[original,original,current])
                self.assertNotEqual(r.returncode,0)
                self.assertEqual((self.root/'writes').read_text(),'PUT /groups/group\n')
                self.assertEqual(len((self.root/'reads').read_text().splitlines()),3)
        r=self.run_function('ensure_peer_in_group',original,('group',),reads=[original,original,updated])
        self.assertEqual(r.returncode,0,r.stderr)

    def test_invalid_group_list_does_not_create_duplicate(self):
        for groups in ([dict(id=None,name='Bauer-PR30')],[dict(id='../group',name='Bauer-PR30')],[dict(id='group\n',name='Bauer-PR30')],
                       [dict(id='group',name='Bauer-PR30'),dict(id='group',name='other')],[{}],{}):
            r=self.run_function('ensure_group',groups,('Bauer-PR30',))
            self.assertNotEqual(r.returncode,0);self.assertFalse((self.root/'writes').exists())

    def test_setup_groups_reuses_all_three_groups_without_losing_inventory(self):
        groups=[dict(self.empty_group(),id='company'),dict(self.empty_group(),id='marker',name='Kunde:Bauer-PR30'),
                dict(self.empty_group(),id='global',name='Kunden',peers=['old-a','old-b','old-c'],peers_count=3,
                     resources=[dict(id='resource',type='network')],resources_count=1)]
        state=self.root/'groups.json';state.write_text(json.dumps(groups))
        self.executable('group-api',"""#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
root=Path(os.environ['MOCK_ROOT']);state=root/'groups.json';groups=json.loads(state.read_text())
method,path=sys.argv[1:3]
if method=='GET':
 print(json.dumps(groups if path=='/groups' else next(g for g in groups if g['id']==path.rsplit('/',1)[1])))
elif method=='PUT':
 g=next(g for g in groups if g['id']==path.rsplit('/',1)[1]);payload=json.loads(sys.argv[3]);g.update(payload)
 g.update(peers_count=len(g['peers']),resources_count=len(g['resources']))
 state.write_text(json.dumps(groups))
 with (root/'writes').open('a') as stream:stream.write(path+'\\n')
 print(json.dumps(g))
else:sys.exit(97)
""")
        body=functions('router-install.sh',['die','valid_group','ensure_group','ensure_peer_in_group','setup_groups'])
        result=self.bash('set -Eeuo pipefail\n'+body+"""
api(){ group-api "$@"; }
log(){ :; }
PEER_ID=new-peer
GLOBAL_GROUP=Kunden
CUSTOMER=Bauer-PR30
CUSTOMER_MARKER_PREFIX=Kunde:
setup_groups
setup_groups
""")
        self.clean_output(result);self.assertEqual(result.returncode,0,result.stderr)
        current=json.loads(state.read_text());self.assertEqual(len(current),3)
        for before,after in zip(groups,current):
            self.assertEqual(before['id'],after['id']);self.assertEqual(before['name'],after['name'])
            self.assertEqual(set(after['peers']),set(before['peers'] or [])|{'new-peer'})
            self.assertEqual(after['resources'],before['resources'] or [])
        self.assertEqual(len((self.root/'writes').read_text().splitlines()),3)

    def test_multiple_json_documents_are_rejected_before_update(self):
        group=self.empty_group()
        for function,args,response in (('ensure_peer_in_group',('group',),json.dumps(group)),
                                       ('ensure_group',('Bauer-PR30',),json.dumps([group]))):
            body=functions('router-install.sh',['die']+(['valid_group'] if function=='ensure_peer_in_group' else [])+[function])
            (self.root/'responses.txt').write_text(response+'\n'+response)
            result=self.bash('set -Eeuo pipefail\n'+body+"""
api(){
 if [[ "$1" == GET ]]; then cat "$MOCK_ROOT/responses.txt";
 else touch "$MOCK_ROOT/unexpected-write"; fi
}
PEER_ID=new-peer
"""+function+' "$@"',*args)
            self.assertNotEqual(result.returncode,0);self.assertFalse((self.root/'unexpected-write').exists())

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


    def test_resume_080_after_group_failure_without_setup_key_preserves_identity(self):
        from test_router_bootstrap import BootstrapTests
        import base64
        import shutil
        fixture=BootstrapTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.env['NB_SETUP_KEY']='';fixture.env['MOCK_APT_RESULT']='0'
        common=fixture.runtime.parent
        (common/'preflight.py').write_text((ROOT/'netbird-router/common/preflight.py').read_text()
            .replace("root=Path('/')",f'root=Path({str(fixture.fs)!r})').replace('PROFILE_UID = 0','PROFILE_UID = os.geteuid()'))
        directory=fixture.fs/'var/lib/netbird';directory.mkdir(parents=True)
        files={'default.json':dict(PrivateKey=base64.b64encode(bytes(range(32))).decode(),ManagementURL='https://api.invalid',DisableFirewall=False,WgIface='wt-lab'),
               'active_profile.json':dict(name='default',username=''), 'state.json':{},'service.json':{}}
        for name,value in files.items():
            p=directory/name;p.write_text(json.dumps(value));p.chmod(0o600)
        before={p:(p.read_bytes(),p.stat().st_mode,p.stat().st_ino) for p in directory.iterdir()}
        fixture.progress.parent.mkdir(parents=True,exist_ok=True)
        fixture.progress.parent.chmod(0o700)  # managed installer state must be root-private
        fixture.progress.write_text(json.dumps(dict(customer=fixture.env['NB_CUSTOMER_NAME'],role='primary',phase='failed')))
        fixture.progress.chmod(0o600)
        status=dict(management=dict(connected=True,url='https://api.invalid'),profileName='default',netbirdIp='100.107.35.72/16',daemonStatus='Connected')
        (fixture.root/'status.json').write_text(json.dumps(status))
        for name in ('awk','mktemp','rm','cat','sleep','jq'):
            (fixture.bin/name).symlink_to(shutil.which(name))
        fixture.executable('netbird',"""#!/bin/sh
printf '%s\\n' "$*" >> "$MOCK_ROOT/netbird-calls"
case "$*" in 'status --json') cat "$MOCK_ROOT/status.json";;
 'status --ipv4') echo 100.107.35.72;; *) exit 98;; esac
""")
        fixture.executable('ip',"""#!/bin/sh
case "$*" in *route*) echo '[{"dst":"default","dev":"eth0"}]';;
 *) echo '[{"ifname":"eth0","addr_info":[{"family":"inet","scope":"global","local":"192.168.178.158","prefixlen":24}]},{"ifname":"wt-lab","addr_info":[{"family":"inet","scope":"global","local":"100.107.35.72","prefixlen":16}]}]';; esac
""")
        fixture.executable('curl',"""#!/usr/bin/env python3
import json,sys
from pathlib import Path
args=sys.argv
assert args[args.index('-X')+1]=='GET'
value=[dict(id='synthetic-existing-peer',ip='100.107.35.72')] if any(arg.endswith('/peers') for arg in args) else [dict(id='a',name='Kunden'),dict(id='b',name='Kunden')]
Path(args[args.index('-o')+1]).write_text(json.dumps(value));print('200',end='')
""")
        fixture.executable('systemctl','#!/bin/sh\necho forbidden >> "$MOCK_ROOT/service-effects"\nexit 98\n')
        for _ in range(2):
            r=fixture.run_core();self.assertNotEqual(r.returncode,0)
            self.assertIn('Gruppenliste',r.stderr)
            self.assertNotIn('Setup Key',r.stdout+r.stderr)
            self.assertEqual(json.loads(fixture.progress.read_text())['phase'],'failed')
            self.assertEqual({p:(p.read_bytes(),p.stat().st_mode,p.stat().st_ino) for p in directory.iterdir()},before)
        self.assertFalse((fixture.root/'service-effects').exists())
        calls=(fixture.root/'netbird-calls').read_text().splitlines()
        self.assertTrue(all(call in ('status --json','status --ipv4') for call in calls))
        self.assertEqual(calls.count('status --ipv4'),2)

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
        bundle=self.root/'bundle';helper=bundle/'netbird-router/common/zabbix_access.py'
        helper.parent.mkdir(parents=True)
        helper.write_text('import os,sys\nsys.exit(int(os.environ.get("MOCK_ACCESS_DENIED","0")))\n')
        self.env['NB_BUNDLE_DIR']=str(bundle)
        for name in ('runtime.py','zabbix_registration.py'):
            source=(ROOT/'netbird-router/common'/name).read_text().replace('ROOT_UID = 0','ROOT_UID = '+str(os.getuid()))
            (helper.parent/name).write_text(source)
        self.receipt=self.root/'zabbix-registration.json'
        self.env['NB_ZABBIX_RECEIPT_FILE']=str(self.receipt)
        self.psk = self.root / "key.psk"
        self.psk.write_text(PSK);self.psk.chmod(0o600)
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
 print(json.dumps({'jsonrpc':'2.0','id':1,'error':{'code':int(os.environ.get('MOCK_CODE','-32602')),'message':os.environ['NB_ZABBIX_API_TOKEN'],'data': 'ab'*32}}))
 sys.exit(0)
if os.environ.get('MOCK_MALFORMED'):
 print('{}');sys.exit(0)
if m=='proxy.get':
 allowed={'proxyid','name','description','operating_mode','local_address','local_port','proxy_groupid','tls_accept'}
 if not isinstance(p['output'],list) or not set(p['output'])<=allowed:
  print(json.dumps({'jsonrpc':'2.0','id':1,'error':{'code':-32602,'data':'write-only or unsupported field'}}));sys.exit(0)
 result=[{key:r[key] for key in p['output'] if key in r} for r in s['proxies']]
elif m=='proxygroup.get':
 assert set(p)=={'output','filter'} and set(p['output'])<= {'proxy_groupid','name','min_online','failover_delay'}
 result=[{key:r[key] for key in p['output'] if key in r} for r in s['groups']]
elif m=='proxygroup.create':
 assert set(p)=={'name','failover_delay','min_online'}
 s['groups']=[dict(p,proxy_groupid='201')];result={'proxy_groupids':['201']}
elif m=='proxy.create':
 assert set(p)=={'name','description','proxy_groupid','local_address','local_port','operating_mode','tls_accept','tls_psk_identity','tls_psk'}
 s['proxies']=[dict(p,proxyid='101')];result={'proxyids':['101']}
elif m=='proxy.update':
 s['proxies'][0].update(p);result={'proxyids':['101']}
else: sys.exit(98)
if m.endswith(('.create','.update')):s['writes'].append({'method':m,'params':p})
path.write_text(json.dumps(s))
if m=='proxy.create' and os.environ.get('MOCK_LOST_CREATE'):
 sys.exit(7)
if m=='proxy.get' and os.environ.get('MOCK_READBACK_FAIL') and s['proxies']:
 print(json.dumps({'jsonrpc':'2.0','id':1,'error':{'code':-32602,'data':os.environ['NB_ZABBIX_API_TOKEN']}}));sys.exit(0)
print(json.dumps({'jsonrpc':'2.0','id':1,'result':result}))
''')

    def run_api(self, identity="Existing identity"):
        result = subprocess.run(['bash', '-x', str(ROOT / 'zabbix-api-register.sh'), 'customer',
                                 'primary', 'proxy-customer-primary', '192.0.2.10', identity, str(self.psk)],
                                env=self.env, capture_output=True, text=True, timeout=20)
        self.clean_output(result)
        return result

    def existing(self, trusted=False, **kwargs):
        proxy = dict(proxyid='101', name='proxy-customer-primary', operating_mode='0',
                     tls_accept='2', tls_psk_identity='Existing identity', tls_psk=PSK,
                     proxy_groupid='201', local_address='192.0.2.10', local_port='10051')
        if trusted:
            c=dict(url=self.env['NB_ZABBIX_API_URL'],customer='customer',proxy=proxy['name'],identity='Existing identity')
            with patch.object(registration,'ROOT_UID',os.getuid()):
                store=registration.TLSStore(self.receipt,c,PSK)
                proxy['description']=store.prepare('proxy',proxy['name'],'201')
                store.confirm('proxy',proxy,'201')
        proxy.update(kwargs)
        self.state.write_text(json.dumps({'proxies':[proxy], 'groups':[{'proxy_groupid':'201','name':'customer','min_online':'1','failover_delay':'1m'}], 'writes':[]}))

    def test_access_denied_preserves_psk_pending_and_status_without_any_api_call(self):
        self.existing();self.pending.write_text('pending');self.psk.chmod(0o600)
        status=self.root/'progress.json';status.write_text('{"phase":"netbird"}');status.chmod(0o600)
        files=[self.psk,self.pending,status,self.state]
        before=[(p.read_bytes(),p.stat().st_mode,p.stat().st_ino) for p in files]
        self.env['MOCK_ACCESS_DENIED']='1'
        self.executable('curl', '#!/bin/sh\ntouch "$MOCK_ROOT/unexpected-api"\nexit 98\n')
        r=self.run_api()
        self.assertNotEqual(r.returncode,0);self.assertIn('Freigabe nicht bestaetigt',r.stderr)
        self.assertFalse((self.root/'unexpected-api').exists())
        self.assertEqual([(p.read_bytes(),p.stat().st_mode,p.stat().st_ino) for p in files],before)

    def test_access_revoked_after_reads_blocks_proxy_update(self):
        self.existing(trusted=True)
        state=json.loads(self.state.read_text());state['groups'][0].update(min_online='1',failover_delay='1m')
        self.state.write_text(json.dumps(state))
        helper=Path(self.env['NB_BUNDLE_DIR'])/'netbird-router/common/zabbix_access.py'
        helper.write_text('''import os,sys
from pathlib import Path
marker=Path(os.environ['MOCK_ROOT'])/'gate-count'
n=int(marker.read_text())+1 if marker.exists() else 1
marker.write_text(str(n))
sys.exit(0 if n==1 else 1)
''')
        r=self.run_api();self.assertNotEqual(r.returncode,0)
        self.assertEqual(json.loads(self.state.read_text())['writes'],[])
        self.assertEqual((self.root/'gate-count').read_text(),'2')
        self.assertEqual(self.psk.read_text(),PSK)

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

    def test_key_changed_before_intent_never_creates_receipt(self):
        env=dict(self.env,REG_FILE=str(self.receipt),REG_URL=self.env['NB_ZABBIX_API_URL'],
                 REG_CUSTOMER='customer',REG_PROXY='proxy-customer-primary',REG_IDENTITY='Existing identity',
                 REG_PSK_FILE=str(self.psk),REG_GROUP='201')
        helper=Path(self.env['NB_BUNDLE_DIR'])/'netbird-router/common/zabbix_registration.py'
        r=subprocess.run(['python3',str(helper),'prepare'],input='cd'*32,env=env,capture_output=True,text=True)
        self.assertNotEqual(r.returncode,0);self.clean_output(r)
        self.assertFalse(self.receipt.exists());self.assertEqual(self.psk.read_text(),PSK)

    def test_7415_write_only_fields_are_rejected_by_mock_api(self):
        payload=json.dumps(dict(jsonrpc='2.0',id=1,method='proxy.get',params={'output':['tls_psk_identity']}))
        r=subprocess.run(['curl','--data',payload],env=self.env,capture_output=True,text=True)
        self.assertEqual(json.loads(r.stdout)['error']['code'],-32602)
        self.assertEqual(self.run_api().returncode,0)
        self.assertEqual(json.loads(self.state.read_text())['proxies'][0]['tls_psk'],PSK)

    def test_failed_readback_or_lost_create_response_resume_same_key_and_proxy(self):
        for failure in ('MOCK_LOST_CREATE','MOCK_READBACK_FAIL'):
            with self.subTest(failure=failure):
                self.state.write_text(json.dumps(dict(proxies=[],groups=[],writes=[])))
                self.receipt.unlink(missing_ok=True);self.pending.touch()
                before=(self.psk.read_bytes(),self.psk.stat().st_ino)
                self.env[failure]='1'
                self.assertNotEqual(self.run_api().returncode,0)
                self.assertTrue(self.pending.exists())
                receipt=json.loads(self.receipt.read_text())
                self.assertIsNone(receipt['objects']['proxy']['id'])
                self.env.pop(failure)
                self.assertEqual(self.run_api().returncode,0)
                self.assertFalse(self.pending.exists())
                data=json.loads(self.state.read_text())
                self.assertEqual(sum(x['method']=='proxy.create' for x in data['writes']),1)
                self.assertEqual(data['proxies'][0]['tls_psk'],PSK)
                self.assertEqual((self.psk.read_bytes(),self.psk.stat().st_ino),before)
                self.assertEqual(self.receipt.stat().st_mode & 0o777,0o600)
                self.assertEqual(json.loads(self.receipt.read_text())['objects']['proxy']['id'],'101')

    def test_foreign_proxy_without_local_receipt_has_no_writes(self):
        self.existing()
        self.assertNotEqual(self.run_api().returncode,0)
        self.assertEqual(json.loads(self.state.read_text())['writes'],[])
        self.assertFalse(self.receipt.exists())

    def test_local_identity_or_psk_change_rejected_without_tls_update(self):
        self.existing(trusted=True)
        self.assertNotEqual(self.run_api(identity='other').returncode,0)
        self.psk.write_text('cd'*32)
        self.assertNotEqual(self.run_api().returncode,0)
        self.assertEqual(json.loads(self.state.read_text())['writes'],[])
        self.assertEqual(json.loads(self.state.read_text())['proxies'][0]['tls_psk'],PSK)

    def test_safe_rpc_error_code_and_classification_do_not_echo_server_data(self):
        self.env['MOCK_ERROR']='1'
        for code in (-32602,-32500,-32603):
            self.env['MOCK_CODE']=str(code)
            r=self.run_api();self.assertNotEqual(r.returncode,0)
            self.assertIn('JSON-RPC '+str(code),r.stderr)
        self.assertEqual(json.loads(self.state.read_text())['writes'],[])

    def test_tls_or_group_conflict_has_no_writes(self):
        for options in ({'description':'different'}, {'tls_accept':'1'}, {'tls_accept':TOKEN},
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
        source = re.sub(r'/(?:etc|var|usr/local|usr/sbin|root|home|tmp)/',
                        lambda match: str(self.fs) + match.group(0), source)
        self.script = self.root / 'installer.sh'
        self.script.write_text(source)
        apihelper=self.fs/'usr/local/sbin/zabbix-api-register'
        apihelper.parent.mkdir(parents=True,exist_ok=True)
        apihelper.write_text('#!/bin/sh\nexit 0\n');apihelper.chmod(0o755)
        for directory in ('root', 'etc/profile.d', 'tmp', 'var/log/zabbix','usr/sbin'):
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
        agent=self.agent.read_text()
        self.assertIn('TLSConnect=psk\nTLSAccept=psk\n',agent)
        self.assertIn('TLSPSKIdentity=',agent)
        self.assertIn('TLSPSKFile='+str(self.key)+'\n',agent)
        self.assertNotIn(generated.strip(),agent)
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

    def test_package_hooks_are_deferred_and_existing_policy_is_preserved(self):
        self.existing()
        policy=self.fs/'usr/sbin/policy-rc.d'
        self.env['MOCK_POLICY']=str(policy)
        self.executable('apt-get', '''#!/bin/sh
printf '%s\\n' "$0 $*" >> "$MOCK_ROOT/commands"
case "$*" in *install*) "$MOCK_POLICY" zabbix-proxy restart; test "$?" = 101 || exit 98;; esac
exit 0
''')
        self.assertEqual(self.run_installer().returncode,0)
        self.assertFalse(policy.exists())
        policy.write_text('#!/bin/sh\nexit 101\n');policy.chmod(0o755)
        before=(policy.read_bytes(),policy.stat().st_ino)
        self.assertEqual(self.run_installer().returncode,0)
        self.assertEqual((policy.read_bytes(),policy.stat().st_ino),before)
        policy.write_text('#!/bin/sh\nexit 0\n')
        self.assertNotEqual(self.run_installer().returncode,0)
        self.assertEqual(policy.read_text(),'#!/bin/sh\nexit 0\n')
        policy.write_text('#!/bin/true\nexit 101\n')
        self.assertNotEqual(self.run_installer().returncode,0)
        self.assertEqual(policy.read_text(),'#!/bin/true\nexit 101\n')

    def test_missing_token_keeps_key_and_never_starts_or_restarts_services(self):
        self.existing();before=self.key.read_bytes()
        self.env.pop('NB_ZABBIX_API_TOKEN')
        result=self.run_installer()
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('systemctl',(self.root/'commands').read_text())
        self.assertEqual(self.key.read_bytes(),before)

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

    def test_fresh_api_abort_keeps_tls_agent_and_reuses_key_on_retry(self):
        helper=self.fs/'usr/local/sbin/zabbix-api-register'
        helper.write_text('#!/bin/sh\nexit 1\n')
        result=self.run_installer()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('TLSConnect=psk\nTLSAccept=psk\n',self.agent.read_text())
        self.assertNotIn('systemctl',(self.root/'commands').read_text())
        before={p:p.read_bytes() for p in (self.agent,self.proxy,self.key)}
        helper.write_text('#!/bin/sh\nexit 0\n')
        self.assertEqual(self.run_installer().returncode,0)
        self.assertEqual({p:p.read_bytes() for p in before},before)


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
