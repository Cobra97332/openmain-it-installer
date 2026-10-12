#!/usr/bin/env python3
"""Small native gate, actual nftables/conntrack, synthetic native ACL backend.

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

native_process=subprocess.Popen(['/native/small-native-lab'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
assert json.loads(native_process.stdout.readline()).get('Ready'), 'Small gate bootstrap failed'
def command(name, ok=True):
    native_process.stdin.write(name+'\n');native_process.stdin.flush()
    result=json.loads(native_process.stdout.readline())
    assert result.get('OK') is ok, result
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

batch("""table inet unrelated {
 chain endpoint { type filter hook prerouting priority -450; policy accept;
  iifname { "np", "bp", "lp" } drop
 }
 chain marker { ip saddr 203.0.113.250 counter drop; }
}
table ip synthetic_native {
 chain route_acl { }
 chain shortcut { type filter hook forward priority filter; policy accept;
  ct state established,related counter accept
 }
 chain native_binat { type filter hook postrouting priority -201; policy accept;
  ct original ip daddr 10.80.1.0/24 iifname "nb0" oifname "lan0" jump route_acl
  ct original ip daddr 10.80.1.0/24 iifname "lan0" oifname "nb0" jump route_acl
  ct original ip daddr 10.80.1.0/24 drop
  ct original ip daddr 10.81.1.0/24 iifname "backup0" oifname "lan0" jump route_acl
  ct original ip daddr 10.81.1.0/24 iifname "lan0" oifname "backup0" jump route_acl
  ct original ip daddr 10.81.1.0/24 drop
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
""")
foreign=run('nft','-s','-j','list','table','inet','unrelated')
def request(src='100.64.0.10',port=51001,proto=17,allowed=True,**kw):
    fresh('lp');frame('np','nb0',src,'10.80.1.20',port,proto=proto,**kw);return receive('lp',allowed,proto)
def reply(sport,dport,proto=17,allowed=True,**kw):
    fresh('np');frame('lp','lan0','192.168.80.20','192.168.80.1',sport,dport,proto=proto,**kw);receive('np',allowed,proto)
def policy(allow=True,drop=False):
    # Mirrors native rule construction, not a real NetworkMap/Engine execution.
    rules='flush chain ip synthetic_native route_acl\n'
    if drop:rules+='add rule ip synthetic_native route_acl ct original ip saddr 100.64.0.10 ct original ip daddr 10.80.1.0/24 drop\n'
    if allow:
        rules+='add rule ip synthetic_native route_acl ct original ip saddr {100.64.0.10,100.64.0.11} ct original ip daddr 10.80.1.0/24 ct protocol {tcp,udp} ct original proto-dst 12345 accept\n'
        rules+='add rule ip synthetic_native route_acl ct original ip saddr 100.64.1.10 ct original ip daddr 10.81.1.0/24 accept\n'
    batch(rules)
try:
    policy();request(allowed=False) # native ACL valid, gate still unknown/closed
    command('open');udp_source,udp_target=request();reply(udp_target,udp_source)
    before=run('nft','-s','-j','list','ruleset')
    command('absent-opt-in',False)
    assert run('nft','-s','-j','list','ruleset')==before
    request(src='100.64.0.11',port=51002);request(src='100.64.0.99',port=51003,allowed=False)
    # Synchronous close BEFORE incremental native ACL work, including failure.
    command('close');request(port=51004,allowed=False)
    policy(False);request(allowed=False);reply(udp_target,udp_source,allowed=False)
    command('open');request(port=51005,allowed=False);request(allowed=False)
    policy();command('open');request(port=51006)
    # Direct late native DROP: no stale mirrored ACCEPT is present anywhere.
    policy(drop=True);request(port=51007,allowed=False);request(port=51006,allowed=False)
    command('prepare')
    request(port=51008,allowed=False)
    entered=threading.Event();release=threading.Event();errors=[]
    def refresh():
        try:
            policy(False);entered.set();assert release.wait(5);policy()
        except BaseException as e:errors.append(e);entered.set()
    worker=threading.Thread(target=refresh);worker.start();assert entered.wait(5)
    request(port=51009,allowed=False);release.set();worker.join(5)
    assert not worker.is_alive() and not errors, errors
    command('close');command('open',False) # native reset invalidates old completion
    command('prepare');command('open')
    # Genuine bidirectional TCP conntrack, then withdrawal and new SYN denial.
    tcp_source,tcp_target=request(port=51100,proto=6,flags=0x02)
    reply(tcp_target,tcp_source,proto=6,flags=0x12,seq=500,ack=124)
    request(port=51100,proto=6,flags=0x10,seq=124,ack=501)
    policy(False);request(port=51100,proto=6,flags=0x10,seq=124,ack=501,allowed=False)
    request(port=51101,proto=6,flags=0x02,allowed=False);reply(tcp_target,tcp_source,proto=6,allowed=False)
    command('close');policy();command('open')
    udp_source,udp_target=request(port=51103)
    fresh('np');related_error(udp_source,udp_target);receive('np',True,1)
    policy(False);fresh('np');related_error(udp_source,udp_target);receive('np',False,1)
    command('close');policy();command('open')
    fresh('lp');frame('bp','backup0','100.64.1.10','10.81.1.20',51110);receive('lp')
    command('restart');request(port=51120,allowed=False) # never reopen cached ACL readiness
    command('open');request(port=51121)
    # A malformed/foreign gate is refused without modifying it.
    table='netbird_binat_native_gate_v1'
    batch('add rule ip '+table+' ready ct original ip saddr 203.0.113.99 drop\n')
    before=run('nft','-s','-j','list','table','ip',table)
    command('open',False);assert run('nft','-s','-j','list','table','ip',table)==before
    # Remove only this synthetic test injection to allow the normal close proof.
    batch('flush chain ip '+table+' ready\n');command('close');command('restart');command('open')
    assert run('nft','-s','-j','list','table','inet','unrelated')==foreign
    print('PASS: small native gate, UDP/TCP original/reply withdrawal, new flows, direct late DROP, restart closed, multiple sources, synthetic backup/overlapping LAN, unknown-layout refusal, foreign tables',flush=True)
    if os.environ.get('OPENMAIN_SMALL_OWNER_DEATH_GATE')=='1':
        native_process.kill();assert native_process.wait(timeout=5)!=0
        # Explicit NEW tuple, not merely the previously accepted established flow.
        print('RELEASE BLOCKED GATE: P1_SMALL_NATIVE_OWNER_DEATH_NEW_FLOW',flush=True)
        request(port=51999,allowed=False)
    print('LIMIT: compiled Go gate + synthetic native nft ACLs, not actual Engine/controller or hardware failover',flush=True)
finally:
    if native_process.poll() is None:
        command('close');native_process.stdin.close();native_process.wait(timeout=15)
    for s in captures.values():s.close()
