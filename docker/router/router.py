#!/usr/bin/env python3
"""Container adapter for existing OpenMain helpers, not another ACL engine."""
import fcntl
from contextlib import closing
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
import urllib.request
from urllib.parse import urlsplit

sys.path.insert(0, '/opt/openmain/netbird-router/common')
from runtime import Invalid, atomic, nft_config
from network import API, reconcile, rows, one
from preflight import resolve_profile, connected_profile
from zabbix_access import group_members, NoRedirect, api_endpoint, PRIVATE_API

STATE = Path('/state')
PRIVATE = STATE / 'private'
CONFIG = STATE / 'zabbix'
CHILDREN = []
ROOT_UID = 0
NAT_STATE = Path('/nat-state')


def nat_context(e):
    return {k:e[k] for k in ('CUSTOMER','ROUTER_ROLE','NB_MANAGEMENT_URL',
        'VIRTUAL_NETWORK','LAN_NETWORK_CIDR','LAN_IP','TRANSIT_SUBNET','EDGE_IP','NAT_IP')}


def nat_ready(e):
    try:
        if json.loads((NAT_STATE / 'binding.json').read_text()) != nat_context(e):
            return False
        fd = os.open(NAT_STATE / 'ready.lock', os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as lock:
            info = os.fstat(lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != ROOT_UID or stat.S_IMODE(info.st_mode) != 0o600:
                return False
            try:
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True  # readiness exists only while the NAT owner lives
    except (OSError, ValueError, KeyError):
        pass
    return False


def existing_profile(root=Path('/')):
    directory = root / 'var/lib/netbird'
    legacy = root / 'etc/netbird'
    # A brand-new named Docker volume exists before the client creates files.
    # All nonempty/ambiguous states still go through the original strict guard.
    if not legacy.exists() and directory.is_dir() and not directory.is_symlink() and not any(directory.iterdir()):
        info = directory.stat()
        if info.st_uid != ROOT_UID or info.st_mode & 0o022:
            raise Invalid('EMPTY_IDENTITY_VOLUME_UNSAFE')
        return None
    return resolve_profile(root)


def command(*args, **kw):
    r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=45, **kw)
    if r.returncode:
        if b'Unsupported netlink protocol' in r.stderr or b'Protocol not supported' in r.stderr:
            raise Invalid('NETLINK_NETFILTER_UNAVAILABLE')
        if b'Operation not permitted' in r.stderr:
            raise Invalid('KERNEL_PERMISSION_DENIED')
        if args[0] in ('ip', 'nft'):
            raise Invalid('COMMAND_' + args[0].upper() + '_FAILED')
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
    names = ('Kunden', e['CUSTOMER'], 'Kunde:' + e['CUSTOMER'],
             e.get('NB_ZABBIX_PROXY_GROUP','Zabbix-Kunden-Proxies'))
    if len(set(names)) != 4 or any(not n or len(n)>128 or any(c in n for c in '\r\n\x00') for n in names):
        raise Invalid('CUSTOMER_GLOBAL_GROUP_COLLISION')
    for k in ('NB_MANAGEMENT_URL',):
        u = urlsplit(e[k])
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise Invalid('API_URL_INVALID')
    api_endpoint(e['NB_ZABBIX_API_URL'])
    if e['NB_ZABBIX_API_URL'] == PRIVATE_API and e['NB_ZABBIX_SERVER'] != '100.107.91.6':
        raise Invalid('PRIVATE_API_ENDPOINT_MISMATCH')
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
  iifname "{transit}" oifname != "{lan}" oifname != "{transit}" drop
  iifname "{transit}" oifname "{transit}" ct original ip daddr != {e['LAN_IP']} drop
  iifname "{transit}" oifname "{transit}" ct direction original ip daddr != {e['EDGE_IP']} drop
  iifname "{transit}" oifname "{transit}" ct direction reply ip saddr != {e['EDGE_IP']} drop
  iifname "{transit}" oifname "{transit}" ct status & dnat != dnat drop
  iifname "{transit}" oifname "{transit}" meta l4proto != tcp drop
  iifname "{transit}" oifname "{transit}" ct direction original tcp dport != {{ 10050, 10051 }} drop
  iifname "{transit}" oifname "{transit}" ct direction reply tcp sport != {{ 10050, 10051 }} drop
  iifname "{lan}" oifname != "{transit}" drop
  iifname "{lan}" oifname "{transit}" ct state != {{ established, related }} ip daddr != {e['EDGE_IP']} drop
  iifname "{lan}" ip daddr {e['EDGE_IP']} meta l4proto != tcp drop
  iifname "{lan}" ip daddr {e['EDGE_IP']} tcp dport != {{ 10050, 10051 }} drop
  iifname "{lan}" ip saddr != {e['LAN_NETWORK_CIDR']} drop
 }}
 chain lan_monitoring {{ type nat hook prerouting priority dstnat; policy accept;
  iifname "{lan}" ip daddr {e['LAN_IP']} ip saddr {e['LAN_NETWORK_CIDR']} tcp dport {{ 10050, 10051 }} dnat to {e['EDGE_IP']}
  iifname "{transit}" ip daddr {e['LAN_IP']} ip saddr {{ {e['EDGE_IP']}, 100.64.0.0/10 }} tcp dport {{ 10050, 10051 }} dnat to {e['EDGE_IP']}
 }}
 chain monitoring {{ type nat hook postrouting priority srcnat; policy accept;
  iifname "{transit}" oifname "{lan}" ip saddr {{ {e['EDGE_IP']}, 100.64.0.0/10 }} ip daddr {e['LAN_NETWORK_CIDR']} masquerade
  iifname "{transit}" oifname "{transit}" ct original ip daddr {e['LAN_IP']} ip daddr {e['EDGE_IP']} ct status dnat tcp dport {{ 10050, 10051 }} snat to {e['NAT_IP']}
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


def member(api, name, peer, allow_write=True, expected=None):
    # Same preservation/read-before-write/readback contract as router-install.sh.
    current = one(api('GET', '/groups'), 'name', name)
    if current is None:
        if not allow_write:
            raise Invalid('GROUP_MISSING_NO_REGRANT')
        created = api('POST', '/groups', {'name': name, 'peers': []})
        current = one(api('GET', '/groups'), 'name', name)
        if not current or current['id'] != created.get('id'):
            raise Invalid('GROUP_CREATE_UNCONFIRMED')
    gid = current['id']
    if expected is not None and gid != expected:
        raise Invalid('GROUP_BINDING_CHANGED')
    current = api('GET', '/groups/' + gid)
    peers = group_members(current, gid)
    if peer not in peers:
        if not allow_write:
            raise Invalid('GROUP_MEMBERSHIP_REVOKED_NO_REGRANT')
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
    for name in ('proxy.sqlite3','proxy.sqlite3-wal','proxy.sqlite3-shm','proxy.log','proxy.log.old','agent.log','agent.log.old'):
        p = Path('/var/lib/zabbix') / name
        if p.is_symlink():
            raise Invalid('ZABBIX_DATA_SYMLINK')
        if p.exists():
            os.chown(p, z.pw_uid, z.pw_gid)
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
    os.chown(psk_file, 0, z.pw_gid)  # preserve bytes across image UID/GID changes
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
    agent_config = (f'Hostname={c["host"]}\nServer=127.0.0.1,' + e['NAT_IP'] + ',' + ','.join(addresses) + '\n'
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
        with closing(sqlite3.connect(temporary)) as sql:
            try:
                with sql:
                    sql.executescript(data)
            except Exception:
                temporary.unlink(missing_ok=True)  # only this new, unused schema
                raise
        os.chown(temporary, z.pw_uid, z.pw_gid); temporary.chmod(0o600)
        os.replace(temporary, db)
    command('zabbix_agent2', '-T', '-c', str(CONFIG / 'agent.conf'))
    spawn(['zabbix_proxy', '-f', '-c', str(CONFIG / 'proxy.conf')], PRIVATE / 'proxy.log', 'zabbix')
    spawn(['zabbix_agent2', '-f', '-c', str(CONFIG / 'agent.conf')], PRIVATE / 'agent.log', 'zabbix')


def check_binding(marker, binding):
    if not marker.exists():
        return
    old = json.loads(marker.read_text())
    if old == binding:
        return
    # Explicit endpoint correction after a failed access check, before any
    # Zabbix registration. Never rebind completed/ambiguous TLS receipts.
    previous = old.get('NB_ZABBIX_API_URL') if isinstance(old, dict) else None
    pristine_zabbix = not any((PRIVATE / name).exists() for name in (
        'zabbix-registration.json', 'psk.pending', 'config-hashes.json',
        'config-intent.json', 'ready'))
    if (binding['NB_ZABBIX_API_URL'] == PRIVATE_API
        and isinstance(previous, str) and previous.startswith('https://')
        and dict(old, NB_ZABBIX_API_URL=PRIVATE_API) == binding and pristine_zabbix):
        api_endpoint(previous)
        return
    raise Invalid('PERSISTENT_IDENTITY_BINDING_CONFLICT')


def gateway(e):
    if STATE.exists() and any(STATE.iterdir()) and not (PRIVATE / 'binding.json').is_file():
        raise Invalid('UNKNOWN_PERSISTENT_STATE_NO_MIGRATION')
    if any(p.is_symlink() for p in (STATE, PRIVATE, CONFIG)):
        raise Invalid('STATE_SYMLINK')
    binding = {k: e[k] for k in ('CUSTOMER', 'ROUTER_ROLE', 'NB_MANAGEMENT_URL', 'VIRTUAL_NETWORK',
                                 'LAN_NETWORK_CIDR', 'LAN_IP', 'EDGE_IP', 'NAT_IP', 'NB_ZABBIX_API_URL', 'NB_ZABBIX_SERVER')}
    marker = PRIVATE / 'binding.json'
    if STATE.exists() and any(p.is_symlink() for p in STATE.rglob('*')):
        raise Invalid('STATE_FILE_SYMLINK')
    check_binding(marker, binding)
    profile = existing_profile()
    if profile is not None and not marker.exists():
        raise Invalid('IDENTITY_WITHOUT_CUSTOMER_BINDING_NO_ADOPTION')
    if profile is None and (PRIVATE / 'netbird-enrollment.json').exists():
        raise Invalid('IDENTITY_VOLUME_MISSING_NO_REENROLLMENT')
    if profile is not None:
        from preflight import url
        config = profile['config']
        if (config.get('DisableFirewall', False) is not False
            or url(config.get('ManagementURL', '')) != url(e['NB_MANAGEMENT_URL'])
            or profile['interface'] != 'wt0'):
            raise Invalid('EXISTING_PROFILE_CONFLICT')
    for _ in range(30):
        if nat_ready(e):
            break
        time.sleep(1)
    else:
        raise Invalid('NAT_OWNER_NOT_READY')
    directories()
    lock = (PRIVATE / 'owner.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    check_binding(marker, binding)
    ready = PRIVATE / 'ready'
    ready.unlink(missing_ok=True)
    atomic(marker, json.dumps(binding))
    transit = interface(e['EDGE_IP'])
    apply_rules(edge_rules(e, transit), ['openmain_container_boundary'])
    for network in (e['VIRTUAL_NETWORK'], e['LAN_NETWORK_CIDR']):
        command('ip', 'route', 'replace', network, 'via', e['NAT_IP'], 'dev', transit)
    existed = profile is not None
    spawn(['netbird', 'service', 'run', '--log-level', 'info', '--log-file', str(PRIVATE / 'netbird.log')], PRIVATE / 'netbird-bootstrap.log')
    for _ in range(30):
        if Path('/run/netbird.sock').exists():
            break
        if CHILDREN[0].poll() is not None:
            raise Invalid('NETBIRD_SERVICE_EXIT')
        time.sleep(1)
    if not existed:
        secret('nb_setup_key')
        atomic(PRIVATE / 'netbird-enrollment.json', json.dumps({'phase':'requested'}))
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
    atomic(PRIVATE / 'netbird-enrollment.json', json.dumps({'phase':'connected','ip':peer_ip}))
    os.environ['NB_API_TOKEN'] = secret('nb_api_token')
    os.environ['NB_ZABBIX_API_TOKEN'] = secret('zabbix_api_token')
    api = API(e['NB_MANAGEMENT_URL'], os.environ['NB_API_TOKEN'])
    peers = [p for p in rows(api('GET', '/peers')) if p.get('ip') == peer_ip]
    if len(peers) != 1:
        raise Invalid('NETBIRD_PEER_AMBIGUOUS')
    pid = peers[0]['id']
    group_file = PRIVATE / 'groups.json'
    group_names = (e['CUSTOMER'], 'Kunden', 'Kunde:' + e['CUSTOMER'],
                   e.get('NB_ZABBIX_PROXY_GROUP','Zabbix-Kunden-Proxies'))
    known = json.loads(group_file.read_text()) if group_file.exists() else None
    if known is not None and set(known) != set(group_names):
        raise Invalid('GROUP_CONTEXT_CHANGED')
    initial = known is None and not (PRIVATE / 'network.json').exists()
    groups = {name:member(api, name, pid, allow_write=initial,
                         expected=known[name] if known is not None else None)
              for name in group_names}
    atomic(group_file, json.dumps(groups))
    customer_group = groups[e['CUSTOMER']]
    # LAN/transit are legitimate local routes; only the NAT destination's real
    # LAN is excluded from allocation collision checks, as in the VM mapping.
    routes = json.loads(command('ip', '-j', '-4', 'route'))
    routes = [r for r in routes if r.get('dst') not in (e['VIRTUAL_NETWORK'], e['LAN_NETWORK_CIDR'])]
    result = mapping_binding(api, e, pid, customer_group, routes)
    if result['virtual'] != e['VIRTUAL_NETWORK']:
        raise Invalid('VIRTUAL_MAPPING_CONFLICT')
    monitoring(e, peer_ip)
    atomic(ready, str(os.getpid()))
    while all(p.poll() is None for p in CHILDREN) and nat_ready(e):
        time.sleep(1)
    raise Invalid('CHILD_OR_NAT_PROCESS_EXIT')


def nat(e):
    state = NAT_STATE
    if state.is_symlink():
        raise Invalid('NAT_BINDING_SYMLINK')
    state.mkdir(mode=0o700, exist_ok=True)
    state.chmod(0o700)
    marker = state / 'binding.json'
    binding = nat_context(e)
    if marker.is_symlink() or (marker.exists() and json.loads(marker.read_text()) != binding):
        raise Invalid('NAT_MAPPING_CHANGED_NO_AUTOMATIC_MIGRATION')
    atomic(marker, json.dumps(binding))
    transit, lan = interface(e['NAT_IP']), interface(e['LAN_IP'])
    command('ip', 'route', 'replace', '100.64.0.0/10', 'via', e['EDGE_IP'], 'dev', transit)
    apply_rules(nat_rules(e, transit, lan), ['openmain_binat', 'openmain_container_boundary'])
    fd = os.open(state / 'ready.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        while True:
            time.sleep(30)


def main():
    os.umask(0o077)
    # Reused API helpers must never forward bearer tokens across redirects.
    proxies = urllib.request.getproxies()
    if not proxies.get('https') and proxies.get('all'):
        os.environ['https_proxy'] = proxies['all']
    urllib.request.install_opener(urllib.request.build_opener(NoRedirect()))
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
