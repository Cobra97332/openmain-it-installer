#!/usr/bin/env python3
"""Compiled native Go publisher, actual nftables/conntrack, synthetic map input.

Only a new --network none Docker with NET_ADMIN and loopback can run this.
No existing test/gate is replaced. Any unexpected packet aborts this test.
"""
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import threading
import time

qemu_lab = ('openmain.lab=vm' in Path('/proc/cmdline').read_text() and Path('/sys/class/dmi/id/sys_vendor').exists() and Path('/sys/class/dmi/id/sys_vendor').read_text().strip() == 'QEMU')
if os.environ.get('OPENMAIN_ISOLATED_TEST') != '1' or not (Path('/.dockerenv').exists() or qemu_lab) or {n for _,n in socket.if_nameindex()} != {'lo'}:
    raise SystemExit('Requires new explicitly isolated network-none container')
def run(*args):return subprocess.check_output(args,text=True,stderr=subprocess.STDOUT)
def batch(text):subprocess.run(['nft','-f','-'],input=text,text=True,check=True)
def mac(d):return bytes.fromhex(json.loads(run('ip','-j','link','show','dev',d))[0]['address'].replace(':',''))
def checksum(data):
    if len(data)%2:data+=b'\0'
    total=sum(struct.unpack('!'+str(len(data)//2)+'H',data))
    while total>>16:total=(total&65535)+(total>>16)
    return (~total)&65535

def frame(dev,target,src,dst,sport=50001,dport=12345,proto=17,flags=0x10,seq=123,ack=523):
    payload=b'native-snapshot-kernel'
    if proto==17:transport=struct.pack('!HHHH',sport,dport,8+len(payload),0)+payload
    elif proto==6:
        transport=struct.pack('!HHIIBBHHH',sport,dport,seq,ack,0x50,flags,65535,0,0)+payload
        pseudo=socket.inet_aton(src)+socket.inet_aton(dst)+struct.pack('!BBH',0,6,len(transport))
        transport=transport[:16]+struct.pack('!H',checksum(pseudo+transport))+transport[18:]
    else:
        transport=struct.pack('!BBHHH',8 if dev!='lp' else 0,0,0,sport,dport)+payload
        transport=transport[:2]+struct.pack('!H',checksum(transport))+transport[4:]
    ip=struct.pack('!BBHHHBBH4s4s',0x45,0,20+len(transport),sport,0,64,proto,0,socket.inet_aton(src),socket.inet_aton(dst))
    ip=ip[:10]+struct.pack('!H',checksum(ip))+ip[12:]
    with socket.socket(socket.AF_PACKET,socket.SOCK_RAW) as sender:
        sender.bind((dev,0));sender.send(mac(target)+mac(dev)+b'\x08\x00'+ip+transport)

def related_error(source_port,target_port):
    payload=b'native-snapshot-kernel'
    udp=struct.pack('!HHHH',source_port,target_port,8+len(payload),0)+payload
    quote=struct.pack('!BBHHHBBH4s4s',0x45,0,20+len(udp),99,0,64,17,0,socket.inet_aton('192.168.80.1'),socket.inet_aton('192.168.80.20'))
    quote=quote[:10]+struct.pack('!H',checksum(quote))+quote[12:]+udp
    icmp=struct.pack('!BBHI',3,3,0,0)+quote
    icmp=icmp[:2]+struct.pack('!H',checksum(icmp))+icmp[4:]
    ip=struct.pack('!BBHHHBBH4s4s',0x45,0,20+len(icmp),100,0,64,1,0,socket.inet_aton('192.168.80.20'),socket.inet_aton('192.168.80.1'))
    ip=ip[:10]+struct.pack('!H',checksum(ip))+ip[12:]
    with socket.socket(socket.AF_PACKET,socket.SOCK_RAW) as sender:
        sender.bind(('lp',0));sender.send(mac('lan0')+mac('lp')+b'\x08\x00'+ip+icmp)

captures={}
def fresh(key):
    if key in captures:captures[key].close()
    s=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(3));s.bind((key,0));s.settimeout(.25);captures[key]=s

def receive(key,allowed=True,proto=17):
    end=time.monotonic()+1
    while time.monotonic()<end:
        try:data,meta=captures[key].recvfrom(4096)
        except socket.timeout:continue
        if meta[2]==socket.PACKET_OUTGOING or data[12:14]!=b'\x08\x00' or not data.endswith(b'native-snapshot-kernel') or data[23]!=proto:continue
        if not allowed:raise AssertionError('Unauthorized packet reached '+key)
        ihl=(data[14]&15)*4
        return struct.unpack('!HH',data[14+ihl:18+ihl]) if proto in (6,17) else (50001,12345)
    if allowed:raise AssertionError('Authorized packet missing on '+key)

native_process=subprocess.Popen(['/native/native-publisher-lab'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
assert json.loads(native_process.stdout.readline()).get('Ready'), 'Native bootstrap failed'
forwarding=Path('/proc/sys/net/ipv4/ip_forward')
if qemu_lab and not Path('/.dockerenv').exists():
    forwarding.write_text('1\n')  # new unshared QEMU namespace, never host/router
assert forwarding.read_text().strip()=='1', 'Isolated IPv4 forwarding required'
for a,b in [('nb0','np'),('backup0','bp'),('lan0','lp')]:
    run('ip','link','add',a,'type','veth','peer','name',b)
    for d in (a,b):run('ip','link','set',d,'up')
for address,dev in [('100.64.0.1/24','nb0'),('100.64.1.1/24','backup0'),('192.168.80.1/24','lan0')]:run('ip','addr','add',address,'dev',dev)
for address,dev,peer in [('100.64.0.10','nb0','np'),('100.64.0.11','nb0','np'),('100.64.1.10','backup0','bp'),('192.168.80.20','lan0','lp')]:
    run('ip','neigh','replace',address,'lladdr',':'.join(f'{b:02x}' for b in mac(peer)),'nud','permanent','dev',dev)
batch('''table inet unrelated {
 chain endpoint { type filter hook prerouting priority -450; policy accept;
  iifname { "np", "bp", "lp" } drop
 }
 chain marker { ip saddr 203.0.113.250 counter drop; }
}
table ip synthetic_native {
 chain shortcut { type filter hook forward priority filter; policy accept;
  ct state established ct protocol tcp counter accept comment "tcp-established"
  ct state established ct protocol udp counter accept comment "udp-established"
  ct state established,related counter accept
 }
}
table ip synthetic_nat {
 chain dn { type nat hook prerouting priority -101; policy accept;
  iifname "nb0" ip daddr 10.80.1.0/24 dnat ip prefix to ip daddr map { 10.80.1.0/24 : 192.168.80.0/24 }
  iifname "backup0" ip daddr 10.81.1.0/24 dnat ip prefix to ip daddr map { 10.81.1.0/24 : 192.168.80.0/24 }
 }
 chain sn { type nat hook postrouting priority srcnat; policy accept;
  oifname "lan0" ct status dnat masquerade
 }
}
''')
foreign=run('nft','-s','-j','list','table','inet','unrelated')
scopes=[dict(virtual='10.80.1.0/24',real='192.168.80.0/24',overlay='nb0',lan='lan0'),dict(virtual='10.81.1.0/24',real='192.168.80.0/24',overlay='backup0',lan='lan0')]
class NativeOwner:
    def __init__(self):
        self.process=native_process
    def command(self,operation,serial=0,items=None,digest=""):
        rules=[dict(Source=r['source'],Destination=r['destination'],Protocol=r['protocol'],Action=r['action'],Ports=r['ports']) for r in items or []]
        self.process.stdin.write(json.dumps(dict(Operation=operation,Serial=serial,Rules=rules,Digest=digest))+'\n');self.process.stdin.flush()
        return json.loads(self.process.stdout.readline())
    def publish(self,serial,items):
        ack=self.command('publish',serial,items)
        assert ack.get('Serial')==serial and len(ack.get('KernelDigest',''))==64 and ack.get('ControllerConfirmed') is False, ack
    def close(self):assert self.command('close').get('Closed')
# Bootstrap must precede creating the test interfaces; move driver start above.
owner=NativeOwner();ticket=0
rules=[dict(source=s,destination=d,protocol='all',ports=[],action='accept') for s,d in [('100.64.0.10/32','10.80.1.0/24'),('100.64.0.11/32','10.80.1.0/24'),('100.64.1.10/32','10.81.1.0/24')]]
def publish(items,callback=lambda:None):
    global ticket
    ticket+=1
    assert owner.command('begin',ticket,items).get('Prepared')
    callback()
    ack=owner.command('commit')
    assert ack.get('Serial')==ticket and len(ack.get('KernelDigest',''))==64 and ack.get('ControllerConfirmed') is False, ack
def request(src='100.64.0.10',port=50001,proto=17,allowed=True,**kw):
    fresh('lp');frame('np','nb0',src,'10.80.1.20',port,12345,proto,**kw);return receive('lp',allowed,proto)
def reply(sport,dport,proto=17,allowed=True,**kw):
    fresh('np');frame('lp','lan0','192.168.80.20','192.168.80.1',sport,dport,proto,**kw);return receive('np',allowed,proto)
try:
    publish(rules)
    udp_source,udp_target=request();time.sleep(.15);reply(udp_target,udp_source)
    # Only a UDP policy is present: ICMP errors must use current parent-tuple
    # authorization rather than an unconditional RELATED/ICMP grant.
    publish([dict(rules[0],protocol='udp',ports=[[12345,12345]])])
    fresh('np');related_error(udp_source,udp_target);receive('np',proto=1)
    publish(rules)
    request(port=50002,proto=6,flags=2,seq=100,ack=0)
    time.sleep(.15);reply(12345,50002,6,flags=0x12,seq=500,ack=123)
    request(port=50002,proto=6,flags=0x10,seq=123,ack=523)
    # Native ESTABLISHED shortcut counter must prove this is genuinely stateful.
    native=json.loads(run('nft','-j','list','table','ip','synthetic_native'))
    for name in ('tcp-established','udp-established'):
        rule=next(x['rule'] for x in native['nftables'] if x.get('rule',{}).get('comment')==name)
        assert any(e.get('counter',{}).get('packets',0)>0 for e in rule['expr']), name
    publish(rules[1:])
    for port,proto in [(50001,17),(50003,17),(50002,6),(50004,6)]:request(port=port,proto=proto,allowed=False,flags=2 if port==50004 else 0x10)
    reply(udp_target,udp_source,allowed=False);reply(12345,50002,6,allowed=False)
    fresh('np');related_error(udp_source,udp_target);receive('np',allowed=False,proto=1)
    request(src='100.64.0.11',port=50010)
    # Restoration and policy/DROP publication after last successful return.
    publish(rules);request(port=50001)
    deny=dict(rules[0],action='drop')
    publish(rules+[deny])
    request(port=50001,allowed=False);request(port=50011,allowed=False)
    reply(udp_target,udp_source,allowed=False)
    # During native refresh the gate is closed, including established traffic.
    entered=threading.Event();release=threading.Event();errors=[]
    def callback():entered.set();assert release.wait(5)
    def worker():
        try:publish(rules,callback)
        except BaseException as exc:errors.append(exc)
    t=threading.Thread(target=worker);t.start();assert entered.wait(3)
    request(port=50001,allowed=False);request(port=50012,allowed=False)
    release.set();t.join(3);assert not t.is_alive() and not errors
    request(port=50001)
    # Unexpected native processing failure does not republish old permits.
    def failure():raise RuntimeError('synthetic native failure')
    try:publish(rules,failure)
    except RuntimeError:pass
    else:raise AssertionError('Native failure swallowed')
    request(port=50001,allowed=False)
    # Native table deletion/recreation while persistent guard remains.
    def delete_native():
        run('nft','delete','table','ip','synthetic_native')
        raise RuntimeError('native table unavailable')
    try:publish(rules,delete_native)
    except RuntimeError:pass
    else:raise AssertionError('Missing native table admitted')
    request(port=50001,allowed=False)
    batch('table ip synthetic_native {\n chain shortcut {\n type filter hook forward priority filter; policy accept;\n ct state established,related counter accept\n }\n}\n')
    request(port=50001,allowed=False);publish(rules);request(port=50001)
    # Missing ACL chain cannot remove a referenced fail-closed boundary.
    attempt=subprocess.run(['nft','delete','chain','ip','netbird_binat_experiment_v3','current'],capture_output=True)
    assert attempt.returncode!=0
    # Invalid action is rejected by the native Go compiler, keeping it closed.
    ticket+=1
    assert owner.command('begin',ticket,[dict(rules[0],action='unsupported')]).get('Prepared') is False
    request(port=50001,allowed=False)
    # Explicitly untracked virtual traffic has no original tuple and no DNAT.
    # A route/neighbor makes delivery possible; denial must come from the guard,
    # not merely the absence of a route. No unrelated real-LAN policy is changed.
    run('ip','route','add','10.80.1.0/24','dev','lan0')
    run('ip','neigh','replace','10.80.1.20','lladdr',mac('lp').hex(':'),'dev','lan0','nud','permanent')
    batch('table ip untracked_probe {\n chain raw {\n type filter hook prerouting priority -301; policy accept;\n iifname "nb0" ip daddr 10.80.1.20 udp sport 50099 notrack\n }\n}\n')
    publish(rules);request(port=50099,allowed=False)
    guard=json.loads(run('nft','-j','list','chain','ip','netbird_binat_experiment_v3','guard'))
    assert any(any(e.get('counter',{}).get('packets',0)>0 for e in row.get('rule',{}).get('expr',[])) and
               any(e.get('match',{}).get('left',{}).get('payload',{}).get('field')=='daddr' for e in row.get('rule',{}).get('expr',[]))
               for row in guard['nftables']), 'untracked virtual denial not observed in guard'
    run('nft','delete','table','ip','untracked_probe')
    run('ip','route','del','10.80.1.0/24','dev','lan0')
    # Selective protocol/port grants, ICMP and denied sibling port.
    publish([dict(rules[0],protocol='udp',ports=[[12345,12345]])]);request(port=50020)
    fresh('lp');frame('np','nb0','100.64.0.10','10.80.1.20',50021,12346);receive('lp',False)
    request(port=50022,proto=6,allowed=False)
    publish([dict(rules[0],protocol='icmp')]);request(port=50023,proto=1)
    request(port=50024,allowed=False)
    # Close invalidates a prepared publication; old Commit cannot reopen it.
    ticket+=1;assert owner.command('begin',ticket,rules).get('Prepared')
    owner.close();assert owner.command('commit').get('Error')
    request(port=50001,allowed=False)
    # Parsing can fail before Begin; receipt must bind that observed generation.
    publish(rules);ticket+=100
    assert owner.command('observe',ticket,digest='a'*64).get('Observed')
    request(port=50001,allowed=False)
    assert owner.command('restart').get('Restarted')
    assert owner.command('observe',ticket-1,digest='a'*64).get('Observed') is False
    assert owner.command('observe',ticket,digest='b'*64).get('Observed') is False
    request(port=50001,allowed=False)
    # A real Go owner release/reload adopts its protected receipt and closes first.
    publish(rules);request(port=50001)
    assert owner.command('restart').get('Restarted')
    request(port=50001,allowed=False);reply(udp_target,udp_source,allowed=False)
    assert owner.command('begin',ticket-1,rules).get('Prepared') is False
    request(port=50001,allowed=False)
    # Simulate the preceding OWN v3 layout and a last committed authorization.
    # Protected receipt and exact kernel fingerprints are retained. Never adopt
    # foreign layouts. Resume must close before verified guard replacement.
    publish(rules)
    previous=json.loads(run('nft','-j','list','chain','ip','netbird_binat_experiment_v3','current'))
    assert owner.command('release').get('Released')
    prior=json.loads(run('nft','-j','list','chain','ip','netbird_binat_experiment_v3','guard'))
    for row in prior['nftables']:
        rule=row.get('rule',{})
        exprs=rule.get('expr',[])
        if any('counter' in e for e in exprs) and any('payload' in e.get('match',{}).get('left',{}) for e in exprs):
            run('nft','delete','rule','ip','netbird_binat_experiment_v3','guard','handle',str(rule['handle']))
    restored=[]
    for row in previous['nftables']:
        if 'rule' in row:
            rule=dict(row['rule']);rule.pop('handle',None)
            restored.append({'add':{'rule':rule}})
    subprocess.run(['nft','-j','-f','-'],input=json.dumps({'nftables':restored}),text=True,check=True)
    def fingerprint(chain):
        rows=[]
        for row in json.loads(run('nft','-j','list','chain','ip','netbird_binat_experiment_v3',chain))['nftables']:
            if 'rule' not in row:continue
            rule=dict(row['rule'])
            for key in ['handle','table','family','chain']:rule.pop(key,None)
            for expr in rule['expr']:
                if 'counter' in expr:expr['counter']={}
            rows.append(rule)
        return hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    receipt_path=Path('/run/netbird-binat-lab/receipt.json')
    receipt=json.loads(receipt_path.read_text())
    receipt['Guard']=fingerprint('guard');receipt['Current']=fingerprint('current')
    receipt_path.write_text(json.dumps(receipt));os.chmod(receipt_path,0o600)
    # Keep NOTRACK routing active THROUGH resume, unlike the earlier test.
    run('ip','route','add','10.80.1.0/24','dev','lan0')
    run('ip','neigh','replace','10.80.1.20','lladdr',mac('lp').hex(':'),'dev','lan0','nud','permanent')
    batch('table ip upgrade_notrack {\n chain raw {\n type filter hook prerouting priority -301; policy accept;\n iifname "nb0" ip daddr 10.80.1.20 udp sport 50100 notrack\n }\n}\n')
    request(port=50100)  # Prove tuple-only legacy guard permits this path.
    request(port=50001)  # Prove the old committed grant actually exists.
    # Transparent syscall-boundary spy: real nft still executes every command.
    # Reject lucky packet timing as proof: the FIRST live mutation itself must
    # close current and replace the guard in the same real kernel transaction.
    nft_path=Path('/usr/sbin/nft');real_nft=Path('/usr/sbin/nft.openmain-real')
    nft_path.rename(real_nft)
    nft_path.write_text('''#!/usr/bin/python3
import json,os,subprocess,sys
from pathlib import Path
script=sys.stdin.read()
if 'flush chain ip netbird_binat_experiment_v3 current\\n' in script:
    path=Path('/run/netbird-binat-lab/upgrade-batches.json')
    rows=json.loads(path.read_text()) if path.exists() else []
    rows.append('flush chain ip netbird_binat_experiment_v3 guard\\n' in script and 'ct state invalid,untracked' in script)
    path.write_text(json.dumps(rows));os.chmod(path,0o600)
sys.exit(subprocess.run(['/usr/sbin/nft.openmain-real',*sys.argv[1:]],input=script,text=True).returncode)
''')
    os.chmod(nft_path,0o755)
    try:
        assert owner.command('resume').get('Resumed')
        batches=json.loads(Path('/run/netbird-binat-lab/upgrade-batches.json').read_text())
        assert batches and all(batches), 'legacy authorization was closed separately from guard replacement'
    finally:
        nft_path.unlink();real_nft.rename(nft_path)
    request(port=50100,allowed=False)
    run('nft','delete','table','ip','upgrade_notrack')
    run('ip','route','del','10.80.1.0/24','dev','lan0')
    request(port=50001,allowed=False);reply(udp_target,udp_source,allowed=False)
    publish(rules);request(port=50001)
    # Matching receipt alone cannot authorize an unknown guard layout.
    assert owner.command('release').get('Released')
    protected=receipt_path.read_text()
    batch('add rule ip netbird_binat_experiment_v3 guard ip saddr 203.0.113.251 counter drop comment "foreign-layout"\n')
    unknown=run('nft','-s','-j','list','chain','ip','netbird_binat_experiment_v3','guard')
    forged=json.loads(protected);forged['Guard']=fingerprint('guard')
    receipt_path.write_text(json.dumps(forged));os.chmod(receipt_path,0o600)
    assert owner.command('resume').get('Resumed') is False
    assert run('nft','-s','-j','list','chain','ip','netbird_binat_experiment_v3','guard')==unknown
    request(port=50001,allowed=False)
    extra=next(row['rule']['handle'] for row in json.loads(unknown)['nftables'] if row.get('rule',{}).get('comment')=='foreign-layout')
    run('nft','delete','rule','ip','netbird_binat_experiment_v3','guard','handle',str(extra))
    receipt_path.write_text(protected);os.chmod(receipt_path,0o600)
    assert owner.command('resume').get('Resumed')
    # Backup path uses separate virtual network despite overlapping real LAN.
    publish(rules);fresh('lp');frame('bp','backup0','100.64.1.10','10.81.1.20',50030);receive('lp')
    publish(rules[:2]);fresh('lp');frame('bp','backup0','100.64.1.10','10.81.1.20',50030);receive('lp',False)
    # Independent foreign DROP still prevails over a matching snapshot grant.
    batch('add chain inet unrelated foreign_drop { type filter hook forward priority 1; policy accept; }\nadd rule inet unrelated foreign_drop ip saddr 100.64.0.11 drop\n')
    request(src='100.64.0.11',port=50040,allowed=False)
    run('nft','delete','chain','inet','unrelated','foreign_drop')
    assert run('nft','-s','-j','list','table','inet','unrelated')==foreign
    # Mandatory hard-owner-loss gate: graceful lifecycle tests are insufficient.
    # No expected failure: a surviving permission keeps native release BLOCKED.
    if os.environ.get('OPENMAIN_NATIVE_OWNER_DEATH_GATE') == '1':
        publish(rules);request(port=50001)
        print('RELEASE BLOCKED GATE: P1_NATIVE_OWNER_DEATH_RETAINS_GRANT', flush=True)
        native_process.kill();assert native_process.wait(timeout=5) != 0
        request(port=50001,allowed=False)
        reply(udp_target,udp_source,allowed=False)
    print('PASS: compiled native Go publisher kernel UDP/TCP established request/reply withdrawal, new flows, DROP, concurrent close, failure/restart, multiple ACLs, overlapping-real-LAN backup path, ICMP/ports/RELATED parent, untracked virtual denial, observed-map/receipt restart, verified old-guard upgrade, unknown-layout refusal, foreign rules')
    print('LIMIT: native Go publisher with synthetic complete-map input; actual NetBird Engine/controller execution, abrupt process death and real failover remain unverified')
finally:
    if native_process.poll() is None:
        owner.close()
        native_process.stdin.close();assert native_process.wait(timeout=10)==0
    for s in captures.values():s.close()
