#!/usr/bin/env python3
"""Run ONLY in disposable network-none Docker or the explicitly marked no-NIC
QEMU lab guest. Docker requires NET_ADMIN; the QEMU guest uses its own kernel.
Tests real forward/reverse prefix NAT and preservation of unrelated tables.
"""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys

qemu_lab = (not Path('/.dockerenv').exists() and 'openmain.lab=vm' in Path('/proc/cmdline').read_text()
            and Path('/sys/class/dmi/id/sys_vendor').exists()
            and Path('/sys/class/dmi/id/sys_vendor').read_text().strip() == 'QEMU'
            and {name for _, name in socket.if_nameindex()} == {'lo'})
if os.environ.get('OPENMAIN_ISOLATED_TEST') != '1' or not (Path('/.dockerenv').exists() or qemu_lab):
    raise SystemExit('Requires explicitly isolated Docker or a marked no-NIC QEMU lab')
sys.path.insert(0, '/src/netbird-router/common')
from runtime import nft_config


def run(*args):
    return subprocess.check_output(args, stderr=subprocess.STDOUT, text=True)


def mac(device):
    return bytes.fromhex(Path('/sys/class/net',device,'address').read_text().strip().replace(':',''))


def frame(device, target, source_ip, target_ip, source_port, target_port):
    payload=b'openmain-nat-test'
    udp=struct.pack('!HHHH',source_port,target_port,8+len(payload),0)+payload
    ip=struct.pack('!BBHHHBBH4s4s',0x45,0,20+len(udp),123,0,64,17,0,socket.inet_aton(source_ip),socket.inet_aton(target_ip))
    total=sum(struct.unpack('!10H',ip))
    while total>>16:total=(total&0xffff)+(total>>16)
    ip=ip[:10]+struct.pack('!H',~total&0xffff)+ip[12:]
    sender=socket.socket(socket.AF_PACKET,socket.SOCK_RAW)
    try:
        sender.bind((device,0));sender.send(mac(target)+mac(device)+b'\x08\x00'+ip+udp)
    finally:sender.close()


def receive(sock, source, dest):
    for _ in range(12):
        data,metadata=sock.recvfrom(4096)
        if metadata[2] == socket.PACKET_OUTGOING:
            continue
        if data[12:14]==b'\x08\x00' and data.endswith(b'openmain-nat-test'):
            assert socket.inet_ntoa(data[26:30])==source, data.hex()
            assert socket.inet_ntoa(data[30:34])==dest, data.hex()
            return struct.unpack('!HH', data[34:38])
    raise AssertionError('NAT packet not received')


for a,b in [('wt0','overlay_peer'),('enp1s0','lan_peer')]:
    run('ip','link','add',a,'type','veth','peer','name',b)
    run('ip','link','set',a,'up');run('ip','link','set',b,'up')
run('ip','addr','add','100.64.0.1/10','dev','wt0')
run('ip','addr','add','192.168.50.1/24','dev','enp1s0')
for ip,device,peer in [('100.64.0.10','wt0','overlay_peer'),('192.168.50.20','enp1s0','lan_peer')]:
    run('ip','neigh','replace',ip,'lladdr',':'.join(f'{n:02x}' for n in mac(peer)),'nud','permanent','dev',device)
# A fresh Debian guest may already have an ordinary inet filter table.
run('nft','add','table','inet','filter')
run('nft','add','chain','inet','filter','input','{ type filter hook input priority 0; policy accept; }')
filter_before=run('nft','-j','list','table','inet','filter')
run('bash','/src/netbird-router/common/pristine.sh')
assert run('nft','-j','list','table','inet','filter')==filter_before
Path('/etc/nftables.d').mkdir(exist_ok=True)
Path('/etc/nftables.d/openmain-binat.nft').write_text(nft_config('enp1s0','wt0','10.30.1.0/24','192.168.50.0/24'))
run('nft','add','table','inet','foreign_fixture')
for _ in range(2):run('bash','/src/netbird-router/common/apply-binat.sh')
# Admission must recognize actual nft's normalized output, not just mocks.
from preflight import inspect, Rejected
state_dir=Path('/var/lib/netbird-kundenrouter');state_dir.mkdir(parents=True,mode=0o700,exist_ok=True)
state_dir.chmod(0o700)
(state_dir/'state.json').write_text(json.dumps(dict(customer='Synthetic',role='primary',lan='192.168.50.0/24',virtual='10.30.1.0/24')))
(state_dir/'state.json').chmod(0o600)
inspect(desired=dict(customer='Synthetic',role='primary',lan_interface='enp1s0'),command=run)
run('nft','add','rule','ip','openmain_binat','postrouting','counter')
try:
    inspect(desired=dict(customer='Synthetic',role='primary',lan_interface='enp1s0'),command=run)
    raise AssertionError('Modified live rules admitted')
except Rejected:
    pass
run('bash','/src/netbird-router/common/apply-binat.sh')
before=run('nft','-j','list','table','ip','openmain_binat')
Path('/etc/nftables.d/openmain-binat.nft').write_text('INVALID CANDIDATE')
assert subprocess.run(['bash','/src/netbird-router/common/apply-binat.sh'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode!=0
assert run('nft','-j','list','table','ip','openmain_binat')==before
run('nft','list','table','inet','foreign_fixture')
lan=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(3));lan.bind(('lan_peer',0));lan.settimeout(3)
overlay=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(3));overlay.bind(('overlay_peer',0));overlay.settimeout(3)
try:
    # Reproduce NetBird 0.80 route ACL: the native rule matches the virtual
    # destination, but by FORWARD the DNAT target is already 192.168.50.20.
    run('nft','add','table','ip','netbird')
    run('nft','add','set','ip','netbird','nb-7d0b7d19','{ type ipv4_addr; }')
    run('nft','add','element','ip','netbird','nb-7d0b7d19','{ 100.64.0.10 }')
    run('nft','add','chain','ip','netbird','netbird-rt-fwd')
    run('nft','add','chain','ip','netbird','netbird-acl-forward-filter',
        '{ type filter hook forward priority filter; policy accept; }')
    run('nft','add','rule','ip','netbird','netbird-acl-forward-filter',
        'iifname','wt0','jump','netbird-rt-fwd')
    run('nft','add','rule','ip','netbird','netbird-acl-forward-filter',
        'iifname','wt0','drop')
    run('nft','add','rule','ip','netbird','netbird-rt-fwd',
        'ip','saddr','@nb-7d0b7d19','ip','daddr','10.30.1.0/24','counter','accept')
    run('nft','add','rule','ip','netbird','netbird-rt-fwd',
        'ip','saddr','100.64.0.11','ip','daddr','10.30.1.0/24','counter','accept')
    from binat_acl import transaction, configuration
    acl=dict(overlay='wt0',lan='enp1s0',virtual='10.30.1.0/24',real='192.168.50.0/24')
    netbird_acl=run('nft','-j','list','chain','ip','netbird','netbird-rt-fwd')
    import json as _json
    command=transaction(_json.loads(netbird_acl),acl)
    assert len(command.strip().splitlines()) == 2
    assert 'ip saddr @nb-7d0b7d19' in command
    assert 'ip saddr 100.64.0.11' in command
    assert command.count('ct original daddr & 255.255.255.0 == 10.30.1.0') == 2
    lan.settimeout(1)
    frame('overlay_peer','wt0','100.64.0.10','10.30.1.20',45678,12345)
    try:
        receive(lan,'192.168.50.1','192.168.50.20')
    except socket.timeout:
        pass  # Native NetBird ACL drops packets after DNAT.
    else:
        raise AssertionError('Reproduced NetBird ACL unexpectedly allowed DNAT')
    Path('/etc/nftables.d/openmain-binat-acl.json').write_text(_json.dumps(acl))
    Path('/etc/nftables.d/openmain-binat-acl.json').chmod(0o600)
    run('python3','/src/netbird-router/common/binat_acl.py')
    observed = _json.loads(run('nft','-j','list','chain','ip','netbird','netbird-rt-fwd'))
    assert not transaction(observed,acl)
    run('python3','/src/netbird-router/common/binat_acl.py')  # idempotent
    lan.settimeout(3)
    # AF_PACKET frame injection is asynchronous. A packet visible at lan_peer
    # can precede completion of the originating kernel forwarding path.
    # Wait for conntrack's NAT tuple to settle before injecting the response.
    import time
    overlay.settimeout(2)
    reverse_ok = False
    for attempt in range(3):
        port = 45679 + attempt
        frame('overlay_peer','wt0','100.64.0.10','10.30.1.20',port,12345)
        mapped_port, target_port = receive(lan,'192.168.50.1','192.168.50.20')
        time.sleep(0.3)
        frame('lan_peer','enp1s0','192.168.50.20','192.168.50.1',target_port,mapped_port)
        try:
            receive(overlay,'10.30.1.20','100.64.0.10')
        except (AssertionError, socket.timeout):
            continue
        reverse_ok = True
        break
    assert reverse_ok, 'Kernel reverse NAT failed with NetBird ACL'
    # The separately authorized backup-to-primary overlay peer must still pass.
    frame('overlay_peer','wt0','100.64.0.11','10.30.1.20',45691,12345)
    receive(lan,'192.168.50.1','192.168.50.20')
    # A third unrelated peer is NOT allowed by either NetBird rule.
    lan.settimeout(1)
    frame('overlay_peer','wt0','100.64.0.12','10.30.1.20',45692,12345)
    try:
        receive(lan,'192.168.50.1','192.168.50.20')
    except socket.timeout:
        pass
    else:
        raise AssertionError('BINAT grants unauthorized third NetBird peer')
    # NetBird refresh: introduce a new native group while OLD rewritten ACLs
    # are still present. The reconciler must revoke both stale managed grants
    # atomically; only the freshly authorized group may access BINAT.
    run('nft','add','set','ip','netbird','nb-ffeeddcc','{ type ipv4_addr; }')
    run('nft','add','element','ip','netbird','nb-ffeeddcc','{ 100.64.0.12 }')
    run('nft','add','rule','ip','netbird','netbird-rt-fwd',
        'ip','saddr','@nb-ffeeddcc','ip','daddr','10.30.1.0/24','counter','accept')
    mixed=_json.loads(run('nft','-j','list','chain','ip','netbird','netbird-rt-fwd'))
    mixed_commands=transaction(mixed,acl)
    assert mixed_commands.count('delete rule')==2
    assert mixed_commands.count('replace rule')==1
    run('python3','/src/netbird-router/common/binat_acl.py')
    updated=run('nft','list','chain','ip','netbird','netbird-rt-fwd').decode()
    assert 'nb-7d0b7d19' not in updated and '100.64.0.11' not in updated
    assert 'nb-ffeeddcc' in updated
    assert not transaction(_json.loads(run('nft','-j','list','chain','ip','netbird','netbird-rt-fwd')),acl)
    lan.settimeout(1)
    frame('overlay_peer','wt0','100.64.0.10','10.30.1.20',45693,12345)
    try:
        receive(lan,'192.168.50.1','192.168.50.20')
    except socket.timeout:
        pass
    else:
        raise AssertionError('Old NetBird ACL still grants revoked peer')
    lan.settimeout(3)
    frame('overlay_peer','wt0','100.64.0.12','10.30.1.20',45694,12345)
    receive(lan,'192.168.50.1','192.168.50.20')
    # A previously authorized explicit peer must also be revoked.
    lan.settimeout(1)
    frame('overlay_peer','wt0','100.64.0.11','10.30.1.20',45695,12345)
    try:
        receive(lan,'192.168.50.1','192.168.50.20')
    except socket.timeout:
        pass
    else:
        raise AssertionError('Stale explicit peer grant not revoked')
    # Simulate NetBird revoking toweri5 membership: the managed ACL must
    # immediately stop forwarding new flows even though its rule still exists.
    run('nft','delete','element','ip','netbird','nb-7d0b7d19','{ 100.64.0.10 }')
    lan.settimeout(1)
    frame('overlay_peer','wt0','100.64.0.10','10.30.1.20',45680,12345)
    try:
        receive(lan,'192.168.50.1','192.168.50.20')
    except socket.timeout:
        pass
    else:
        raise AssertionError('BINAT bypasses NetBird source-group revocation')
finally:
    lan.close();overlay.close()
assert run('nft','-j','list','table','inet','filter')==filter_before
print('PASS: prefix NAT, NetBird ACL fail-closed repro/fix, idempotent ACL reconciliation, reverse translation, foreign firewall preservation')
