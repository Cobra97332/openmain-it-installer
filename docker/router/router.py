#!/usr/bin/env python3
"""Container adapter for existing OpenMain helpers, not another ACL engine."""
import fcntl
import gzip
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import signal
import sqlite3
import stat
import subprocess
import sys
import time
import tempfile
from urllib.parse import urlsplit

sys.path.insert(0, '/opt/openmain/netbird-router/common')
from runtime import Invalid, atomic, nft_config
from network import API, reconcile, rows, one
from preflight import resolve_profile, connected_profile
from zabbix_access import group_members

STATE = Path('/state')
PRIVATE = STATE / 'private'
CONFIG = STATE / 'zabbix'
CHILDREN = []


def command(*args, **kw):
    r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       timeout=45, **kw)
    if r.returncode:
        raise Invalid('LOCAL_COMMAND_FAILED')
    return r.stdout.decode()


def configuration(env=None):
    e = dict(os.environ if env is None else env)
    required = ('CUSTOMER', 'ROUTER_ROLE', 'NB_MANAGEMENT_URL', 'VIRTUAL_NETWORK',
                'LAN_NETWORK_CIDR', 'LAN_IP', 'TRANSIT_SUBNET', 'EDGE_IP', 'NAT_IP',
                'NB_ZABBIX_API_URL', 'NB_ZABBIX_SERVER')
    if any(not e.get(k) or any(x in e[k] for x in '\r\n\x00') for k in required):
        raise Invalid('CONFIG_MISSING_OR_MULTILINE')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', e['CUSTOMER']):
        raise Invalid('CUSTOMER_INVALID')
    if e['ROUTER_ROLE'] not in ('primary', 'backup'):
        raise Invalid('ROLE_INVALID')
    for k in ('NB_MANAGEMENT_URL', 'NB_ZABBIX_API_URL'):
        u = urlsplit(e[k])
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise Invalid('API_URL_INVALID')
    v, lan, transit = [ipaddress.IPv4Network(e[k]) for k in
                       ('VIRTUAL_NETWORK', 'LAN_NETWORK_CIDR', 'TRANSIT_SUBNET')]
    overlay = ipaddress.IPv4Network('100.64.0.0/10')
    if v.prefixlen != lan.prefixlen or any(a.overlaps(b) for a, b in
            ((v, lan), (v, transit), (lan, transit), (overlay, v), (overlay, lan), (overlay, transit))):
        raise Invalid('NETWORK_OVERLAP_OR_PREFIX')
    for key, net in (('LAN_IP', lan), ('EDGE_IP', transit), ('NAT_IP', transit)):
        ip = ipaddress.IPv4Address(e[key])
        if ip not in net or ip in (net.network_address, net.broadcast_address):
            raise Invalid('ADDRESS_INVALID')
    if e['EDGE_IP'] == e['NAT_IP'] or ipaddress.IPv4Address(e['NB_ZABBIX_SERVER']) not in overlay:
        raise Invalid('PEER_OR_TRANSIT_INVALID')
    return e


def interface(address):
    entries = json.loads(command('ip', '-j', '-4', 'address'))
    found = [x['ifname'] for x in entries if any(a.get('local') == address for a in x.get('addr_info', []))]
    if len(found) != 1 or not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', found[0]):
        raise Invalid('INTERFACE_AMBIGUOUS')
    return found[0]


def nat_rules(e, transit, lan):
    # Preserve the existing prefix-DNAT and narrowly scoped masquerade verbatim.
    rules = nft_config(lan, transit, e['VIRTUAL_NETWORK'], e['LAN_NETWORK_CIDR'])
    # Locally generated Zabbix monitoring reaches the real LAN through this hop.
    # These are namespace boundaries, never copies of customer access policies.
    rules += f'''table ip openmain_container_boundary {{
 chain forward {{ type filter hook forward priority -10; policy accept;
  iifname "{transit}" ip saddr != {{ 100.64.0.0/10, {e['EDGE_IP']} }} drop
  iifname "{transit}" oifname != "{lan}" drop
  iifname "{lan}" oifname != "{transit}" drop
  iifname "{lan}" oifname "{transit}" ct state != {{ established, related }} ip daddr != {e['EDGE_IP']} drop
  iifname "{lan}" ip daddr {e['EDGE_IP']} meta l4proto != tcp drop
  iifname "{lan}" ip daddr {e['EDGE_IP']} tcp dport != {{ 10050, 10051 }} drop
  iifname "{lan}" ip saddr != {e['LAN_NETWORK_CIDR']} drop
 }}
 chain lan_monitoring {{ type nat hook prerouting priority dstnat; policy accept;
  iifname "{lan}" ip daddr {e['LAN_IP']} ip saddr {e['LAN_NETWORK_CIDR']} tcp dport {{ 10050, 10051 }} dnat to {e['EDGE_IP']}
 }}
 chain monitoring {{ type nat hook postrouting priority srcnat; policy accept;
  iifname "{transit}" oifname "{lan}" ip saddr {{ {e['EDGE_IP']}, 100.64.0.0/10 }} ip daddr {e['LAN_NETWORK_CIDR']} masquerade
 }}
}}
'''
    return rules


def edge_rules(e, transit):
    # DROP-only topology constraints; native NetBird retains every ACL decision.
    return f'''table ip openmain_container_boundary {{
 chain forward {{ type filter hook forward priority -10; policy accept;
  oifname "{transit}" iifname != "wt0" drop
  iifname "wt0" oifname != "{transit}" drop
  iifname "wt0" ip saddr != 100.64.0.0/10 drop
  iifname "wt0" ip daddr != {e['VIRTUAL_NETWORK']} drop
  iifname "{transit}" oifname != "wt0" drop
  iifname "{transit}" ct state != {{ established, related }} drop
 }}
}}
'''


def apply_rules(text, tables):
    transaction = ''
    for table in tables:
        r = subprocess.run(['nft', 'list', 'table', 'ip', table], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5)
        if r.returncode == 0:
            transaction += 'delete table ip ' + table + '\n'
    transaction += text
    command('nft', '--check', '-f', '-', input=transaction.encode())
    command('nft', '-f', '-', input=transaction.encode())


def secret(name, optional=False):
    path = Path('/run/secrets') / name
    if optional and not path.exists():
        return ''
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != 0:
            raise Invalid('SECRET_PERMISSIONS_UNSAFE')
        value = stream.read(8193).strip()
    if len(value) > 8192 or (not value and not optional) or any(x in value for x in '\r\n\x00'):
        raise Invalid('SECRET_INVALID')
    return value


def member(api, name, peer):
    # Same preservation/read-before-write/readback contract as router-install.sh.
    current = one(api('GET', '/groups'), 'name', name)
    if current is None:
        created = api('POST', '/groups', {'name': name, 'peers': []})
        current = one(api('GET', '/groups'), 'name', name)
        if not current or current['id'] != created.get('id'):
            raise Invalid('GROUP_CREATE_UNCONFIRMED')
    gid = current['id']
    current = api('GET', '/groups/' + gid)
    peers = group_members(current, gid)
    if peer not in peers:
        before = api('GET', '/groups/' + gid)
        group_members(before, gid)
        if before != current:
            raise Invalid('GROUP_CHANGED_CONCURRENTLY')
        resources = current['resources'] or []
        payload = {'name': name, 'peers': sorted(peers | {peer}),
                   'resources': [{'id': r['id'], 'type': r['type']} for r in resources]}
        api('PUT', '/groups/' + gid, payload)
        after = api('GET', '/groups/' + gid)
        if not (peers | {peer}) <= group_members(after, gid):
            raise Invalid('GROUP_MEMBERSHIP_NOT_PRESERVED')
        if not {(r['id'], r['type']) for r in resources} <= {
                (r['id'], r['type']) for r in after['resources'] or []}:
            raise Invalid('GROUP_RESOURCES_NOT_PRESERVED')
    return gid


def spawn(args, log, user=None):
    def demote():
        account = pwd.getpwnam(user)
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    with log.open('ab') as stream:
        child_env = {k: v for k, v in os.environ.items()
                     if k not in ('NB_API_TOKEN', 'NB_ZABBIX_API_TOKEN')}
        p = subprocess.Popen(args, stdout=stream, stderr=stream,
                             env=child_env,
                             preexec_fn=demote if user else None)
    CHILDREN.append(p)
    return p


def stop(*_):
    for child in CHILDREN:
        if child.poll() is None:
            child.terminate()
    deadline = time.monotonic() + 8
    for child in CHILDREN:
        try:
            child.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            child.kill()
    raise SystemExit(1)


def directories():
    z = pwd.getpwnam('zabbix')
    STATE.mkdir(exist_ok=True)
    os.chown(STATE, 0, z.pw_gid); STATE.chmod(0o710)
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    PRIVATE.chmod(0o700)
    CONFIG.mkdir(mode=0o750, exist_ok=True)
    os.chown(CONFIG, 0, z.pw_gid); CONFIG.chmod(0o750)
    for name in ('/run/zabbix', '/var/lib/zabbix'):
        p = Path(name); p.mkdir(parents=True, exist_ok=True)
        os.chown(p, z.pw_uid, z.pw_gid); p.chmod(0o750)
    return z


def managed_configs(contents, owner):
    manifest, intent = PRIVATE / 'config-hashes.json', PRIVATE / 'config-intent.json'
    known = json.loads(manifest.read_text()) if manifest.exists() else {}
    pending = json.loads(intent.read_text()) if intent.exists() else {}
    digests = {n: hashlib.sha256(body.encode()).hexdigest() for n, body in contents.items()}
    for name in contents:
        p = CONFIG / name
        if p.is_symlink():
            raise Invalid('MONITORING_CONFIG_SYMLINK')
        if p.exists():
            observed = hashlib.sha256(p.read_bytes()).hexdigest()
            if observed not in (known.get(name), pending.get(name)):
                raise Invalid('MONITORING_CONFIG_MANUALLY_MODIFIED')
    atomic(intent, json.dumps(digests))
    for name, body in contents.items():
        atomic(CONFIG / name, body, 0o640, owner)
    atomic(manifest, json.dumps(digests))
    intent.unlink()


def mapping_binding(api, e, peer, group, routes):
    receipt = PRIVATE / 'network.json'
    if not receipt.exists():
        result = reconcile(api, e['CUSTOMER'], e['ROUTER_ROLE'], e['LAN_NETWORK_CIDR'],
                           peer, group, routes, e['VIRTUAL_NETWORK'])
        atomic(receipt, json.dumps(result))
        return result
    result = json.loads(receipt.read_text())
    if any(not re.fullmatch(r'[A-Za-z0-9_-]+', result[k]) for k in ('network','resource','router')):
        raise Invalid('NETWORK_RECEIPT_INVALID')
    base = '/networks/' + result['network']
    resource = [r for r in rows(api('GET', base+'/resources')) if r['id']==result['resource']]
    router = [r for r in rows(api('GET', base+'/routers')) if r['id']==result['router']]
    from network import ids
    metric = {'primary':100,'backup':200}[e['ROUTER_ROLE']]
    if (len(resource)!=1 or resource[0].get('enabled') is not True
        or resource[0].get('address')!=e['VIRTUAL_NETWORK'] or group not in ids(resource[0].get('groups'))
        or len(router)!=1 or any(router[0].get(k)!=v for k,v in
            dict(peer=peer,metric=metric,masquerade=False,enabled=True).items())):
        raise Invalid('NETWORK_ASSIGNMENT_CHANGED_NO_OVERWRITE')
    # Centrally tightened/removed policies remain authoritative on restart.
    # Do not recreate defaults or reinterpret their authorization rules here.
    return result


def monitoring(e, peer_ip):
    from zabbix_sync import API as ZAPI, register_host, context
    z = pwd.getpwnam('zabbix')
    proxy = 'proxy-' + e['CUSTOMER'].lower() + '-' + e['ROUTER_ROLE']
    psk_file = CONFIG / 'router.psk'
    intent = PRIVATE / 'psk-intent'
    if not psk_file.exists():
        if intent.exists() and (intent.read_text() != proxy or any(p.exists() for p in (
                PRIVATE / 'zabbix-registration.json', PRIVATE / 'config-hashes.json', PRIVATE / 'ready'))):
            raise Invalid('PSK_MISSING_NO_REGENERATION')
        atomic(intent, proxy)
        atomic(psk_file, os.urandom(32).hex() + '\n', 0o640, (0, z.pw_gid))
    psk = __import__('zabbix_registration').key_bytes(psk_file).hex()
    identity = 'PSK-' + proxy
    os.environ.update(NB_ZABBIX_PROXY_NAME=proxy,
        NB_ZABBIX_RECEIPT_FILE=str(PRIVATE / 'zabbix-registration.json'),
        NB_ZABBIX_PSK_PENDING_FILE=str(PRIVATE / 'psk.pending'),
        NB_BUNDLE_DIR='/opt/openmain')
    # Reuse the checked 7.4 proxy-group/TLS registration unchanged.
    command('bash', '/opt/openmain/zabbix-api-register.sh', e['CUSTOMER'],
            e['ROUTER_ROLE'], proxy, e['LAN_IP'], identity, str(psk_file))
    c = dict(customer=e['CUSTOMER'], host='router-' + e['CUSTOMER'].lower() + '-' + e['ROUTER_ROLE'],
             proxy=proxy, identity=identity, psk_file=str(psk_file),
             receipt_file=os.environ['NB_ZABBIX_RECEIPT_FILE'], url=e['NB_ZABBIX_API_URL'],
             network=e['LAN_NETWORK_CIDR'], access=context())
    addresses = register_host(ZAPI(c['url'], os.environ['NB_ZABBIX_API_TOKEN'], c['access']), c, e['LAN_IP'], psk)
    # Only two TLS-PSK ports on this router's LAN address are translated into
    # this namespace; no host ports and no public listeners are published.
    common = f'TLSConnect=psk\nTLSAccept=psk\nTLSPSKIdentity={identity}\nTLSPSKFile={psk_file}\n'
    proxy_config = (f'ProxyMode=0\nServer={e["NB_ZABBIX_SERVER"]}\nHostname={proxy}\n'
                    f'DBName=/var/lib/zabbix/proxy.sqlite3\nLogType=file\nLogFile=/var/lib/zabbix/proxy.log\nLogFileSize=10\n'
                    f'ListenIP=0.0.0.0\nSourceIP={peer_ip}\n' + common)
    agent_config = (f'Hostname={c["host"]}\nServer=127.0.0.1,' + ','.join(addresses) + '\n'
                    f'ServerActive=127.0.0.1:10051\nListenIP=0.0.0.0\n'
                    f'LogType=file\nLogFile=/var/lib/zabbix/agent.log\nLogFileSize=10\nControlSocket=/run/zabbix/agent.sock\n' + common)
    agent_config += 'UserParameter=openmain.agent.active,echo 1\n'
    managed_configs({'proxy.conf': proxy_config, 'agent.conf': agent_config}, (0, z.pw_gid))
    schema = Path('/usr/share/zabbix/sql-scripts/sqlite3/proxy.sql')
    db = Path('/var/lib/zabbix/proxy.sqlite3')
    if not db.exists():
        data = schema.read_text() if schema.exists() else gzip.decompress(Path(str(schema) + '.gz').read_bytes()).decode()
        fd, temporary = tempfile.mkstemp(prefix='.initial-schema-', dir=db.parent)
        os.close(fd)
        temporary = Path(temporary)
        with sqlite3.connect(temporary) as sql:
            sql.executescript(data)
        os.chown(temporary, z.pw_uid, z.pw_gid); temporary.chmod(0o600)
        os.replace(temporary, db)
    command('zabbix_agent2', '-T', '-c', str(CONFIG / 'agent.conf'))
    spawn(['zabbix_proxy', '-f', '-c', str(CONFIG / 'proxy.conf')], PRIVATE / 'proxy.log', 'zabbix')
    spawn(['zabbix_agent2', '-f', '-c', str(CONFIG / 'agent.conf')], PRIVATE / 'agent.log', 'zabbix')


def gateway(e):
    if STATE.exists() and any(STATE.iterdir()) and not (PRIVATE / 'binding.json').is_file():
        raise Invalid('UNKNOWN_PERSISTENT_STATE_NO_MIGRATION')
    if any(p.is_symlink() for p in (STATE, PRIVATE, CONFIG)):
        raise Invalid('STATE_SYMLINK')
    binding = {k: e[k] for k in ('CUSTOMER', 'ROUTER_ROLE', 'NB_MANAGEMENT_URL', 'VIRTUAL_NETWORK',
                                 'LAN_NETWORK_CIDR', 'LAN_IP', 'EDGE_IP', 'NAT_IP', 'NB_ZABBIX_API_URL', 'NB_ZABBIX_SERVER')}
    marker = PRIVATE / 'binding.json'
    if marker.exists() and json.loads(marker.read_text()) != binding:
        raise Invalid('PERSISTENT_IDENTITY_BINDING_CONFLICT')
    directories()
    lock = (PRIVATE / 'owner.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if marker.exists() and json.loads(marker.read_text()) != binding:
        raise Invalid('PERSISTENT_IDENTITY_BINDING_CONFLICT')
    ready = PRIVATE / 'ready'
    ready.unlink(missing_ok=True)
    atomic(marker, json.dumps(binding))
    transit = interface(e['EDGE_IP'])
    apply_rules(edge_rules(e, transit), ['openmain_container_boundary'])
    for network in (e['VIRTUAL_NETWORK'], e['LAN_NETWORK_CIDR']):
        command('ip', 'route', 'replace', network, 'via', e['NAT_IP'], 'dev', transit)
    existed = resolve_profile() is not None
    if existed:
        from preflight import url
        config = resolve_profile()['config']
        if config.get('DisableFirewall', False) is not False or url(config.get('ManagementURL', '')) != url(e['NB_MANAGEMENT_URL']):
            raise Invalid('EXISTING_PROFILE_CONFLICT')
    spawn(['netbird', 'service', 'run', '--log-level', 'info', '--log-file', str(PRIVATE / 'netbird.log')], PRIVATE / 'netbird-bootstrap.log')
    for _ in range(30):
        if Path('/run/netbird.sock').exists():
            break
        if CHILDREN[0].poll() is not None:
            raise Invalid('NETBIRD_SERVICE_EXIT')
        time.sleep(1)
    if not existed:
        secret('nb_setup_key')
        command('netbird', 'up', '--management-url', e['NB_MANAGEMENT_URL'],
                '--hostname', 'router-' + e['CUSTOMER'] + '-' + e['ROUTER_ROLE'],
                '--interface-name', 'wt0', '--disable-dns', '--setup-key-file', '/run/secrets/nb_setup_key')
    for _ in range(45):
        try:
            profile = resolve_profile()
            connected_profile(profile, e['NB_MANAGEMENT_URL'])
            peer_ip = str(ipaddress.IPv4Address(command('netbird', 'status', '--ipv4').strip().split('/')[0]))
            break
        except Exception:
            time.sleep(1)
    else:
        raise Invalid('NETBIRD_NOT_CONNECTED')
    os.environ['NB_API_TOKEN'] = secret('nb_api_token')
    os.environ['NB_ZABBIX_API_TOKEN'] = secret('zabbix_api_token')
    api = API(e['NB_MANAGEMENT_URL'], os.environ['NB_API_TOKEN'])
    peers = [p for p in rows(api('GET', '/peers')) if p.get('ip') == peer_ip]
    if len(peers) != 1:
        raise Invalid('NETBIRD_PEER_AMBIGUOUS')
    pid = peers[0]['id']
    customer_group = member(api, e['CUSTOMER'], pid)
    for name in ('Kunden', 'Kunde:' + e['CUSTOMER'], e.get('NB_ZABBIX_PROXY_GROUP', 'Zabbix-Kunden-Proxies')):
        member(api, name, pid)
    # LAN/transit are legitimate local routes; only the NAT destination's real
    # LAN is excluded from allocation collision checks, as in the VM mapping.
    routes = json.loads(command('ip', '-j', '-4', 'route'))
    routes = [r for r in routes if r.get('dst') not in (e['VIRTUAL_NETWORK'], e['LAN_NETWORK_CIDR'])]
    result = mapping_binding(api, e, pid, customer_group, routes)
    if result['virtual'] != e['VIRTUAL_NETWORK']:
        raise Invalid('VIRTUAL_MAPPING_CONFLICT')
    monitoring(e, peer_ip)
    atomic(ready, str(os.getpid()))
    while all(p.poll() is None for p in CHILDREN):
        time.sleep(1)
    raise Invalid('CHILD_PROCESS_EXIT')


def nat(e):
    transit, lan = interface(e['NAT_IP']), interface(e['LAN_IP'])
    command('ip', 'route', 'replace', '100.64.0.0/10', 'via', e['EDGE_IP'], 'dev', transit)
    apply_rules(nat_rules(e, transit, lan), ['openmain_binat', 'openmain_container_boundary'])
    while True:
        time.sleep(30)


def main():
    os.umask(0o077)
    if sys.argv[1:] == ['health']:
        pid = int((PRIVATE / 'ready').read_text())
        os.kill(pid, 0)
        interface(command('netbird', 'status', '--ipv4').strip().split('/')[0])
        return
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    e = configuration()
    if sys.argv[1:] == ['gateway']:
        gateway(e)
    elif sys.argv[1:] == ['nat']:
        nat(e)
    else:
        raise Invalid('UNKNOWN_MODE')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Upstream API errors and subprocess output can echo credentials.
        label = str(error) if isinstance(error, Invalid) and re.fullmatch(r'[A-Z_]+', str(error)) else type(error).__name__
        print('OpenMain container stopped: ' + label + '; details suppressed.', file=sys.stderr)
        stop()
