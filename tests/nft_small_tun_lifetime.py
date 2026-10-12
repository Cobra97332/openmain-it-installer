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

# Actual TUN requires /dev/net/tun in the disposable QEMU VM, not the host.
if not qemu_lab or Path('/.dockerenv').exists():
    raise SystemExit('TUN lifetime proof requires a fresh isolated QEMU namespace')
run('ip','link','set','lo','up')
run('modprobe','tun')
Path('/dev/net').mkdir(exist_ok=True)
if not Path('/dev/net/tun').exists():os.mknod('/dev/net/tun',0o600|0o20000,os.makedev(10,200))
owner=subprocess.Popen(['/native/small-native-lab'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
assert json.loads(owner.stdout.readline()).get('Ready')
def command(s):
    owner.stdin.write(s+'\n');owner.stdin.flush();response=json.loads(owner.stdout.readline());assert response.get('OK'),response
command('tun-start')
for dev in ['nb0']:run('ip','link','set',dev,'up')
run('ip','addr','add','100.64.0.1/24','dev','nb0')
run('ip','link','add','lan0','type','veth','peer','name','lp')
for dev in ['lan0','lp']:run('ip','link','set',dev,'up')
run('ip','addr','add','192.168.80.1/24','dev','lan0')
run('ip','neigh','replace','192.168.80.20','lladdr',':'.join(f'{b:02x}' for b in mac('lp')),'nud','permanent','dev','lan0')
Path('/proc/sys/net/ipv4/ip_forward').write_text('1\n')
batch("""table inet unrelated {
 chain endpoint {type filter hook prerouting priority -450; policy accept; iifname "lp" drop;}
 chain marker {ip saddr 203.0.113.250 counter drop;}
}
table ip synthetic_native {
 chain route_acl {ct original ip saddr 100.64.0.10 ct original ip daddr 10.80.1.0/24 accept;}
 chain native_binat {type filter hook postrouting priority -201; policy accept;
  ct original ip daddr 10.80.1.0/24 iifname "nb0" oifname "lan0" jump route_acl
  ct original ip daddr 10.80.1.0/24 drop;
 }
}
table ip synthetic_nat {
 chain dn {type nat hook prerouting priority -101; policy accept;
 iifname "nb0" ip daddr 10.80.1.0/24 dnat ip prefix to ip daddr map {10.80.1.0/24 : 192.168.80.0/24};}
 chain sn {type nat hook postrouting priority srcnat; policy accept; oifname "lan0" ct status dnat masquerade;}
}
""")
foreign=run('nft','-s','-j','list','table','inet','unrelated')
def inject(sport):
    payload=b'native-snapshot-kernel';udp=struct.pack('!HHHH',sport,12345,8+len(payload),0)+payload
    packet=struct.pack('!BBHHHBBH4s4s',0x45,0,20+len(udp),sport,0,64,17,0,socket.inet_aton('100.64.0.10'),socket.inet_aton('10.80.1.20'))
    packet=packet[:10]+struct.pack('!H',checksum(packet))+packet[12:]+udp
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sender:sender.sendto(packet,('127.0.0.1',52183))
try:
    fresh('lp');inject(53001);receive('lp',False)
    command('open');fresh('lp');inject(53002);receive('lp')
    command('close');fresh('lp');inject(53003);receive('lp',False)
    command('open');fresh('lp');inject(53004);receive('lp')
    if os.environ.get('OPENMAIN_TUN_CLOSURE_FAILURE')=='1':
        # Unknown owned-table layout: refuse modification and terminate TUN owner.
        batch('add rule ip netbird_binat_native_gate_v1 ready ct original ip saddr 203.0.113.99 drop\n')
        before=run('nft','-s','-j','list','table','ip','netbird_binat_native_gate_v1')
        owner.stdin.write('close\n');owner.stdin.flush()
        assert owner.wait(timeout=5)!=0
        assert run('nft','-s','-j','list','table','ip','netbird_binat_native_gate_v1')==before
    else:
        owner.kill();assert owner.wait(timeout=5)!=0
    # Kernel removes the nonpersistent TUN when the owner's last FD disappears.
    assert not any(n['ifname']=='nb0' for n in json.loads(run('ip','-j','link','show')))
    fresh('lp');inject(53999);receive('lp',False)
    assert run('nft','-s','-j','list','table','inet','unrelated')==foreign
    print('PASS: real QEMU nonpersistent TUN + owner UDP transport, valid flow, closed gate, '+('unconfirmed-closure fail-stop' if os.environ.get('OPENMAIN_TUN_CLOSURE_FAILURE')=='1' else 'SIGKILL')+' removes interface and blocks NEW tuple; foreign rules preserved',flush=True)
    print('LIMIT: synthetic plaintext transport, no real NetBird daemon/controller/encryption acceptance',flush=True)
finally:
    if owner.poll() is None:command('close');owner.stdin.close();owner.wait(timeout=15)
    for s in captures.values():s.close()
