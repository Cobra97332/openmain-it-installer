#!/usr/bin/env python3
"""Real stock-NetBird controller + packet tests; never a mocked ACL publisher.
All Docker networks are internal. Only synthetic controller credentials exist.
"""
import json
import os
from pathlib import Path
import secrets
import re
import subprocess
import sys
import tempfile
import time

IMAGE = os.environ.get('ROUTER_TEST_IMAGE', 'openmain-router:test')
SERVER = 'netbirdio/netbird-server:0.80.0@sha256:05b3d8d6d056e5062a2965d4d6e5d6d83f09f2bf9133947a8e5972e0e0e7a261'
HERE = Path(__file__).resolve().parent
PREFIX = 'om-docker-test-' + secrets.token_hex(5)
NAMES = ('peer','probe','primary-a','backup-a','primary-b','backup-b')
WORK = Path(tempfile.mkdtemp(prefix=PREFIX+'-'))
NETWORKS, CONTAINERS, RESULTS = [], [], {}


def docker(*args, data=None, check=True, timeout=120):
    env = dict(os.environ)
    for k in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):
        env.pop(k,None)
    r = subprocess.run(['docker','--host=unix:///var/run/docker.sock',*args], input=data,
                       text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       env=env, timeout=timeout)
    if check and r.returncode:
        error = 'docker_'+args[0]+'_failed'
        if 'executable file not found' in r.stderr:
            error = 'test_image_entrypoint_missing'
        elif 'pull access denied' in r.stderr.lower() or 'failed to resolve reference' in r.stderr.lower():
            error = 'test_image_unavailable'
        elif 'permission denied' in r.stderr.lower():
            error = 'test_bind_or_socket_permission_denied'
        elif 'no space left on device' in r.stderr.lower():
            error = 'test_host_storage_exhausted'
        elif '/dev/net/tun' in r.stderr and ('no such file' in r.stderr.lower() or 'not exist' in r.stderr.lower()):
            error = 'test_host_tun_missing'
        elif 'overlaps' in r.stderr.lower():
            error = 'test_subnet_overlap'
        else:
            classified = re.search(r'RuntimeError: (lab_[a-z_0-9]+)\s*$', r.stderr)
            if classified:
                error = classified.group(1)
            else:
                classified = re.search(r'(NETLINK_NETFILTER_UNAVAILABLE|KERNEL_PERMISSION_DENIED|COMMAND_(?:IP|NFT)_FAILED|INTERFACE_AMBIGUOUS|LOCAL_COMMAND_FAILED)\s*$',r.stderr)
                if classified:
                    error=classified.group(1).lower()
        raise RuntimeError(error)
    return r


def run(name, image=IMAGE, networks=(), caps=(), volumes=(), env=(), devices=(), args=('sleep','infinity')):
    actual=PREFIX+'-'+name
    cmd=['run','-d','--name',actual,'--cap-drop','ALL','--security-opt','no-new-privileges:true','--log-driver','none']
    for c in caps: cmd+=['--cap-add',c]
    if name.startswith(('primary-', 'backup-', 'nat-')):
        cmd+=['--sysctl','net.ipv4.ip_forward=1']
    for v in volumes: cmd+=['-v',v]
    for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
        cmd+=['-e',key+'=']
    for e in env: cmd+=['-e',e]
    for d in devices: cmd+=['--device',d]
    cmd+=['--network',networks[0], '--entrypoint', args[0],image,*args[1:]]
    CONTAINERS.append(actual)  # also remove a container created before run failed
    docker(*cmd)
    for n in networks[1:]: docker('network','connect',n,actual)
    return actual


def execute(name,*args,data=None,check=True):
    return docker('exec','-i',PREFIX+'-'+name,*args,data=data,check=check)


def control(code):
    wrapper='import sys\nsys.path.insert(0,"/fixtures")\nimport controller as c\n'+code
    return execute('tools','python3','-',data=wrapper).stdout


def network(suffix, subnet):
    n=PREFIX+'-'+suffix
    docker('network','create','--internal','--subnet',subnet,n); NETWORKS.append(n)
    return n


def probe(name, lan, protocol='tcp', expected=True):
    target='10.30.'+('1' if lan=='a' else '2')+'.100'
    code='''import socket,sys
s=socket.socket(socket.AF_INET, socket.SOCK_STREAM if sys.argv[2]=='tcp' else socket.SOCK_DGRAM)
s.settimeout(2)
try:
 s.connect((sys.argv[1],8080 if sys.argv[2]=='tcp' else 8081)); s.sendall(b'OM-TEST')
 result=s.recv(100)==sys.argv[3].encode()+b':OM-TEST'
except OSError: result=False
print('DELIVERED' if result else 'BLOCKED')
'''
    found=execute(name,'python3','-c',code,target,protocol,lan.upper()).stdout.strip()=='DELIVERED'
    if found != expected: raise RuntimeError('packet_permission_mismatch')
    return found


def wait_for(fn, seconds=45):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        try:
            fn(); return
        except RuntimeError:
            time.sleep(1)
    raise RuntimeError('packet_convergence_timeout')


def setup_root(name, number):
    env = dict(CUSTOMER='lab',ROUTER_ROLE='primary' if name.startswith('primary') else 'backup',
               NB_MANAGEMENT_URL='https://controller.lab.test',VIRTUAL_NETWORK='10.30.'+('1' if name.endswith('a') else '2')+'.0/24',
               LAN_NETWORK_CIDR='192.168.178.0/24',LAN_IP='192.168.178.'+('1' if name.startswith('primary') else '3'),
               TRANSIT_SUBNET=f'172.30.{243+number}.0/24',
               NB_ZABBIX_API_URL='https://zabbix.lab.test/api_jsonrpc.php',NB_ZABBIX_SERVER='100.64.0.10')
    transit=network('transit-'+name,env['TRANSIT_SUBNET'])
    # Dynamically inspect only nonsecret interface addresses, never environment.
    docker('network','connect',transit,PREFIX+'-'+name)
    lan_net=LAN_A if name.endswith('a') else LAN_B
    nat=run('nat-'+name,networks=(transit,lan_net),caps=('NET_ADMIN',))
    def address(container, n):
        info=json.loads(docker('inspect','-f','{{json .NetworkSettings.Networks}}',container).stdout)
        return info[n]['IPAddress']
    env['EDGE_IP']=address(PREFIX+'-'+name,transit)
    env['NAT_IP']=address(nat,transit)
    payload=json.dumps(env)
    code='''import json,sys
sys.path.insert(0,'/opt/openmain')
import router as r
e=r.configuration(json.loads(sys.argv[1])); dev=r.interface(e['EDGE_IP'])
r.apply_rules(r.edge_rules(e,dev),['openmain_container_boundary'])
for n in (e['VIRTUAL_NETWORK'],e['LAN_NETWORK_CIDR']): r.command('ip','route','replace',n,'via',e['NAT_IP'],'dev',dev)
'''
    execute(name,'python3','-c',code,payload)
    code='''import json,sys
sys.path.insert(0,'/opt/openmain')
import router as r
e=json.loads(sys.argv[1]); dev=r.interface(sys.argv[2])
r.command('ip','address','add',e['LAN_IP']+'/24','dev',dev)
r.command('ip','route','replace','100.64.0.0/10','via',e['EDGE_IP'],'dev',r.interface(e['NAT_IP']))
r.apply_rules(r.nat_rules(e,r.interface(e['NAT_IP']),dev),['openmain_binat','openmain_container_boundary'])
# Observation only, no verdict or permission changes; omitted from runtime.
r.command('nft','add','chain','ip','openmain_container_boundary','observed',
          '{ type filter hook postrouting priority 150; policy accept; }')
r.command('nft','add','rule','ip','openmain_container_boundary','observed',
          'ip','daddr','192.168.178.100','tcp','dport','8080','ct','status','dnat','counter')
'''
    execute('nat-'+name,'python3','-c',code,payload,address(nat,lan_net))
    return env


def route_packets(router):
    document=json.loads(execute('nat-'+router,'nft','-j','list','chain','ip',
                               'openmain_container_boundary','observed').stdout)
    return sum(expr.get('counter',{}).get('packets',0)
               for row in document['nftables'] for expr in row.get('rule',{}).get('expr',[]))


def enroll(name, resume=False):
    # No runtime patch, Injector env, debug bypass or external ACL helper.
    args=['netbird','up','--foreground-mode','--management-url','https://controller.lab.test',
          '--interface-name','wt0','--disable-dns','--hostname',name,'--log-file','console']
    if not resume: args+=['--setup-key-file','/lab-key']
    command='umask 077; exec '+__import__('shlex').join(args)+' >/run/client.log 2>&1'
    docker('exec','-d',PREFIX+'-'+name,'sh','-c',command)


def main():
    global LAN_A, LAN_B
    os.chmod(WORK,0o700)
    RESULTS['stage']='networks'
    control_net=network('control','172.30.240.0/24')
    LAN_A=network('lan-a','172.30.241.0/24'); LAN_B=network('lan-b','172.30.242.0/24')
    run('init',networks=(control_net,),caps=('DAC_OVERRIDE',),volumes=(str(WORK)+':/lab',str(HERE)+':/fixtures:ro'),
        args=('python3','-c','import sys;sys.path.insert(0,"/fixtures");import controller;c=controller;c.initialize()'))
    if docker('wait',PREFIX+'-init').stdout.strip() != '0':
        raise RuntimeError('private_fixture_initialization_failed')
    RESULTS['stage']='controller_bootstrap'
    run('controller',SERVER,networks=(control_net,),env=('NB_SETUP_PAT_ENABLED=true','NB_DISABLE_GEOLOCATION=true'),
        volumes=(str(WORK/'runtime/controller')+':/etc/netbird:ro',str(WORK/'runtime/server-state')+':/var/lib/netbird'),
        args=('/go/bin/netbird-server','--config','/etc/netbird/config.yaml'))
    # Alias rather than host DNS or public endpoints.
    docker('network','disconnect',control_net,PREFIX+'-controller')
    docker('network','connect','--alias','controller.lab.test',control_net,PREFIX+'-controller')
    run('tools',networks=(control_net,),volumes=(str(WORK/'runtime')+':/lab',str(HERE)+':/fixtures:ro'),
        env=('HTTPS_PROXY=','HTTP_PROXY=','ALL_PROXY='))
    control('c.bootstrap(); c.api_contract(); c.keys(); print("CONTROLLER_READY")')
    RESULTS['stage']='client_creation'
    for name in NAMES:
        run(name,networks=(control_net,),caps=('NET_ADMIN','NET_RAW'),devices=('/dev/net/tun:/dev/net/tun',),
            env=('NB_WG_KERNEL_DISABLED=true','SSL_CERT_FILE=/lab-ca.crt','HTTP_PROXY=','HTTPS_PROXY=','ALL_PROXY='),
            volumes=(str(WORK/'runtime/ca/cert.pem')+':/lab-ca.crt:ro',str(WORK/'runtime/keys'/name)+':/lab-key:ro'))
    router_env={name:setup_root(name,i) for i,name in enumerate(n for n in NAMES if n not in ('peer','probe'))}
    RESULTS['stage']='lan_and_enrollment'
    # Two different LAN namespaces, identical real addresses, no default-LAN route.
    for suffix,net in (('a',LAN_A),('b',LAN_B)):
        n=run('lan-'+suffix,networks=(net,),caps=('NET_ADMIN',))
        execute('lan-'+suffix,'sh','-c','ip address add 192.168.178.100/24 dev eth0')
        code='''import socket,threading,sys
marker=sys.argv[1].encode()+b':'
def tcp():
 s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('192.168.178.100',8080));s.listen()
 while True:
  c,_=s.accept();data=c.recv(100);c.sendall(marker+data);c.close()
threading.Thread(target=tcp,daemon=True).start()
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(('192.168.178.100',8081))
while True:
 data,a=s.recvfrom(100);s.sendto(marker+data,a)
'''
        docker('exec','-d',n,'python3','-c',code,suffix.upper())
    for name in NAMES: enroll(name)
    control('c.configure(); print("NATIVE_MAP_CONFIGURED")')
    execute('primary-a','python3','-c',
            'import sys;sys.path.insert(0,"/opt/openmain/netbird-router/common");'
            'from preflight import resolve_profile;assert resolve_profile() is not None;'
            'print("STOCK_IDENTITY_LAYOUT_ACCEPTED")')
    RESULTS['stock_profile_layout']=True
    # Real packets for the passive Agent/Proxy LAN transport, including the
    # local Proxy polling its own LAN alias (NAT hairpin). No TLS claim here.
    server='''import socket
s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
s.bind((__import__('sys').argv[1],10050));s.listen()
while True:
 c,_=s.accept();c.sendall(c.recv(100));c.close()
'''
    for name,e in router_env.items():
        docker('exec','-d',PREFIX+'-'+name,'python3','-c',server,e['EDGE_IP'])
    transport='''import socket,json,subprocess,sys
addresses=json.loads(subprocess.check_output(['ip','-j','-4','addr','show','dev','wt0']))
source=[a['local'] for a in addresses[0]['addr_info'] if a['family']=='inet'][0]
s=socket.socket();s.settimeout(3);s.bind((source,0));s.connect((sys.argv[1],10050))
s.sendall(b'OM-MONITOR');assert s.recv(100)==b'OM-MONITOR'
'''
    for name,e in router_env.items():
        execute(name,'python3','-c',transport,e['LAN_IP'])
    execute('primary-a','python3','-c',transport,router_env['backup-a']['LAN_IP'])
    execute('backup-a','python3','-c',transport,router_env['primary-a']['LAN_IP'])
    RESULTS['passive_monitoring_hairpin_and_backup_transport']=True
    RESULTS['stage']='packet_gates'
    for lan in ('a','b'):
        for proto in ('tcp','udp'):
            wait_for(lambda: probe('peer',lan,proto)); probe('probe',lan,proto,False)
    probe('primary-a','b','tcp',False)
    probe('primary-b','a','tcp',False)
    RESULTS['native_policy_and_prefix_nat']=True
    control('c.mutate({"operation":"restrict_a"}); print("PORT_POLICY_CHANGED")')
    wait_for(lambda: probe('peer','a','udp',False)); probe('peer','a','tcp')
    RESULTS['native_port_policy']=True
    control('c.mutate({"operation":"restore_a"}); print("PORT_POLICY_RESTORED")')
    wait_for(lambda: probe('peer','a','udp'))
    # A non-admin stays denied to both customers; changing the Admin source
    # transfers access without granting a customer peer a source policy.
    control('c.mutate({"operation":"admin_swap"}); print("ADMIN_SOURCE_CHANGED")')
    for lan in ('a','b'):
        wait_for(lambda lan=lan: probe('peer',lan,'tcp',False))
        wait_for(lambda lan=lan: probe('probe',lan,'tcp'))
    control('c.mutate({"operation":"membership","allow":True}); print("ADMIN_SOURCE_RESTORED")')
    for lan in ('a','b'):
        wait_for(lambda lan=lan: probe('peer',lan,'tcp'))
        wait_for(lambda lan=lan: probe('probe',lan,'tcp',False))
    RESULTS['tenant_separation']=True
    # Keep a replied UDP tuple open across the policy change. A fresh socket
    # alone does not prove revocation of an established conntrack entry.
    established_probe='''import pathlib,socket,time
ready=pathlib.Path("/tmp/openmain-established-ready")
go=pathlib.Path("/tmp/openmain-established-go")
result=pathlib.Path("/tmp/openmain-established-result")
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
s.settimeout(3)
s.connect(("10.30.1.100",8081))
s.send(b"OM-TEST")
assert s.recv(100)==b"A:OM-TEST"
ready.touch()
until=time.monotonic()+90
while not go.exists() and time.monotonic()<until: time.sleep(0.05)
if not go.exists(): raise RuntimeError("revocation_probe_not_released")
try:
 s.send(b"OM-TEST")
 delivered=s.recv(100)==b"A:OM-TEST"
except OSError: delivered=False
result.write_text("DELIVERED" if delivered else "BLOCKED")
'''
    docker('exec','-d',PREFIX+'-peer','python3','-c',established_probe)
    wait_for(lambda: execute('peer','test','-f','/tmp/openmain-established-ready'))
    control('c.mutate({"operation":"membership","allow":False}); print("GROUP_WITHDRAWN")')
    for proto in ('tcp','udp'): wait_for(lambda: probe('peer','a',proto,False))
    RESULTS['withdrawal_new_flows']=True
    execute('peer','touch','/tmp/openmain-established-go')
    wait_for(lambda: execute('peer','test','-f','/tmp/openmain-established-result'))
    if execute('peer','cat','/tmp/openmain-established-result').stdout.strip()!='BLOCKED':
        raise RuntimeError('established_session_policy_bypass')
    RESULTS['withdrawal_established_flow']=True
    control('c.mutate({"operation":"membership","allow":True}); print("GROUP_RESTORED")')
    wait_for(lambda: probe('peer','a'))
    # Prove selected data paths, not just successful traffic after a stop.
    def primary_path():
        primary_before=route_packets('primary-a')
        backup_before=route_packets('backup-a')
        probe('peer','a')
        if not (route_packets('primary-a')>primary_before
                and route_packets('backup-a')==backup_before):
            raise RuntimeError('primary_metric_not_selected')
    # Policy and route updates are asynchronous. Require real packets on the
    # primary path within the bounded convergence window.
    wait_for(primary_path)
    RESULTS['primary_metric_100_selected']=True
    backup_before=route_packets('backup-a')
    docker('stop',PREFIX+'-primary-a')
    wait_for(lambda: probe('peer','a'),seconds=90)
    if route_packets('backup-a')<=backup_before:
        raise RuntimeError('backup_data_path_not_observed')
    probe('probe','a','tcp',False)
    RESULTS['primary_backup_100_200']=True
    # Both gateway processes absent -> no alternate/Docker bridge access.
    docker('stop',PREFIX+'-backup-a')
    wait_for(lambda: probe('peer','a','tcp',False))
    probe('probe','a','udp',False)
    RESULTS['no_tunnel_no_access']=True
    RESULTS['existing_session_instant_revocation']='NOT_CLAIMED_STOCK_NETBIRD'
    RESULTS['production_entrypoint_zabbix_e2e']='NOT_TESTED_BY_NETWORK_CONTRACT'


if __name__=='__main__':
    status=1
    try:
        main(); status=0
    except Exception as e:
        RESULTS['error_class']=str(e) if isinstance(e,RuntimeError) and re.fullmatch(r'[a-z_0-9]+',str(e)) else type(e).__name__
    finally:
        for c in reversed(CONTAINERS): docker('rm','-f',c,check=False)
        for n in reversed(NETWORKS): docker('network','rm',n,check=False)
        # Credentials are local only; cleanup through root in isolated container.
        docker('run','--rm','--network','none','--cap-drop','ALL','--cap-add','DAC_OVERRIDE',
               '-v',str(WORK)+':/lab','--entrypoint','python3',IMAGE,'-c',
               'import shutil; shutil.rmtree("/lab/runtime",ignore_errors=True)',check=False)
        RESULTS['exit_status']=status
        report=Path(os.environ.get('ROUTER_GATEWAY_REPORT','gateway-report.json'))
        report.write_text(json.dumps(RESULTS,indent=2)+'\n')
        print(json.dumps(RESULTS))
    sys.exit(status)
