#!/usr/bin/env python3
"""Real kernel experiment; synthetic policy owner, no native daemon/controller.

Only a new --network none Docker with NET_ADMIN and loopback can run this.
No existing test/gate is replaced. Any unexpected packet aborts this test.
"""
import importlib.util
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
path=Path('/src/netbird-router/experimental/native-binat/snapshot.py')
spec=importlib.util.spec_from_file_location('snapshot',path)
snapshot=importlib.util.module_from_spec(spec);spec.loader.exec_module(snapshot)

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
owner=snapshot.Publisher(scopes);owner.bootstrap();ticket=0
rules=[dict(source=s,destination=d,protocol='all',ports=[],action='accept') for s,d in [('100.64.0.10/32','10.80.1.0/24'),('100.64.0.11/32','10.80.1.0/24'),('100.64.1.10/32','10.81.1.0/24')]]
def publish(items,callback=lambda:None):
    global ticket
    ticket+=1;owner.publish(ticket,items,callback)
def request(src='100.64.0.10',port=50001,proto=17,allowed=True,**kw):
    fresh('lp');frame('np','nb0',src,'10.80.1.20',port,12345,proto,**kw);return receive('lp',allowed,proto)
def reply(sport,dport,proto=17,allowed=True,**kw):
    fresh('np');frame('lp','lan0','192.168.80.20','192.168.80.1',sport,dport,proto,**kw);return receive('np',allowed,proto)
try:
    publish(rules)
    udp_source,udp_target=request();time.sleep(.15);reply(udp_target,udp_source)
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
    attempt=subprocess.run(['nft','delete','chain','ip',snapshot.TABLE,'current'],capture_output=True)
    assert attempt.returncode!=0
    # A malformed policy cannot retain prior authorization.
    try:publish([dict(rules[0],action='unsupported')])
    except snapshot.Rejected:pass
    else:raise AssertionError('Invalid snapshot accepted')
    request(port=50001,allowed=False)
    # Selective protocol/port grants, ICMP and denied sibling port.
    publish([dict(rules[0],protocol='udp',ports=[[12345,12345]])]);request(port=50020)
    fresh('lp');frame('np','nb0','100.64.0.10','10.80.1.20',50021,12346);receive('lp',False)
    request(port=50022,proto=6,allowed=False)
    publish([dict(rules[0],protocol='icmp')]);request(port=50023,proto=1)
    request(port=50024,allowed=False)
    # Real kernel commit followed by synthetic lost ACK; immediately re-close.
    real_execute=owner.execute
    def lost_ack(script):
        real_execute(script)
        if 'add rule' in script:raise TimeoutError('synthetic ACK loss after real commit')
    owner.execute=lost_ack
    try:
        try:publish(rules)
        except snapshot.Rejected:pass
        else:raise AssertionError('Indeterminate commit reported success')
    finally:owner.execute=real_execute
    request(port=50001,allowed=False);request(port=50025,allowed=False)
    reply(udp_target,udp_source,allowed=False)
    # Stale prepared publication cannot restore a newer withdrawn policy.
    try:owner.publish(ticket-1,rules)
    except snapshot.Rejected:pass
    else:raise AssertionError('Stale policy published')
    request(port=50001,allowed=False)
    # Backup path uses separate virtual network despite overlapping real LAN.
    publish(rules);fresh('lp');frame('bp','backup0','100.64.1.10','10.81.1.20',50030);receive('lp')
    publish(rules[:2]);fresh('lp');frame('bp','backup0','100.64.1.10','10.81.1.20',50030);receive('lp',False)
    # Independent foreign DROP still prevails over a matching snapshot grant.
    batch('add chain inet unrelated foreign_drop { type filter hook forward priority 1; policy accept; }\nadd rule inet unrelated foreign_drop ip saddr 100.64.0.11 drop\n')
    request(src='100.64.0.11',port=50040,allowed=False)
    run('nft','delete','chain','inet','unrelated','foreign_drop')
    assert run('nft','-s','-j','list','table','inet','unrelated')==foreign
    print('PASS: real kernel UDP/TCP established request/reply withdrawal, new flows, DROP, concurrent close, failure/restart, multiple ACLs, overlapping-real-LAN backup path, ICMP/ports, foreign rules')
    print('LIMIT: synthetic snapshot owner; native NetBird adapter, real failover and RELATED ICMP remain unverified')
finally:
    for s in captures.values():s.close()
