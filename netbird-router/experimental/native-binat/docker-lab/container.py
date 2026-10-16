#!/usr/bin/env python3
"""Container-side laboratory operations; API bodies and keys never reach logs."""
import base64
import ipaddress
import json
import os
import re
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = 'https://controller.lab.test'
NAMES = ('peer', 'probe', 'primary-a', 'backup-a', 'primary-b', 'backup-b')


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(value)


def cmd(*args, data=None):
    r = subprocess.run(args, input=data, text=True, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=45)
    if r.returncode:
        raise RuntimeError('lab_local_' + Path(args[0]).name + '_failed')
    return r.stdout


def initialize():
    os.umask(0o077)
    root = Path('/lab/runtime')
    root.mkdir(mode=0o700)  # never reinitialize or overwrite an existing identity
    for name in ('controller', 'ca', 'keys', 'server-state'):
        (root / name).mkdir(mode=0o700)
    ca, controller = root / 'ca', root / 'controller'
    cmd('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
        '-keyout', str(ca / 'key.pem'), '-out', str(ca / 'cert.pem'),
        '-subj', '/CN=OpenMain disposable laboratory CA',
        '-addext', 'basicConstraints=critical,CA:TRUE',
        '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
    cmd('openssl', 'req', '-newkey', 'rsa:2048', '-nodes',
        '-keyout', str(controller / 'key.pem'), '-out', str(controller / 'request.pem'),
        '-subj', '/CN=controller.lab.test')
    write(controller / 'extensions', 'subjectAltName=DNS:controller.lab.test\n'
          'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n'
          'extendedKeyUsage=serverAuth\n')
    cmd('openssl', 'x509', '-req', '-in', str(controller / 'request.pem'),
        '-CA', str(ca / 'cert.pem'), '-CAkey', str(ca / 'key.pem'), '-CAcreateserial',
        '-days', '2', '-out', str(controller / 'cert.pem'),
        '-extfile', str(controller / 'extensions'))
    # JSON is valid YAML. No production endpoint/IdP/update/download dependency.
    config = {'server': {'listenAddress': ':443', 'exposedAddress': BASE + ':443',
        'healthcheckAddress': ':9000', 'metricsPort': 9090, 'stunPorts': [3478],
        'logLevel': 'warn', 'logFile': 'console', 'authSecret': secrets.token_hex(32),
        'dataDir': '/var/lib/netbird', 'disableAnonymousMetrics': True,
        'disableGeoliteUpdate': True,
        'tls': {'certFile': '/etc/netbird/cert.pem', 'keyFile': '/etc/netbird/key.pem'},
        'auth': {'issuer': BASE + '/oauth2', 'localAuthDisabled': False,
                 'dashboardRedirectURIs': [BASE + '/nb-auth'],
                 'cliRedirectURIs': ['http://localhost:53000/']},
        'store': {'engine': 'sqlite', 'encryptionKey': base64.b64encode(secrets.token_bytes(32)).decode()}}}
    write(controller / 'config.yaml', json.dumps(config))
    for file in root.rglob('*'):
        if file.is_file():
            file.chmod(0o600)
    print('initialized')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('lab_api_redirect_refused')


def api(path, method='GET', body=None, authenticated=True):
    if not path.startswith('/api/') or '://' in path or '..' in path:
        raise RuntimeError('lab_api_path_refused')
    context = ssl.create_default_context(cafile='/lab/ca/cert.pem')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                urllib.request.HTTPSHandler(context=context), NoRedirect())
    headers = {'Content-Type': 'application/json'}
    if authenticated:
        headers['Authorization'] = 'Token ' + Path('/lab/token').read_text().strip()
    req = urllib.request.Request(BASE + path, headers=headers, method=method,
                                 data=None if body is None else json.dumps(body).encode())
    try:
        with opener.open(req, timeout=10) as r:
            raw = r.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise RuntimeError('lab_api_body_limit')
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise RuntimeError('lab_api_http_' + str(e.code)) from None


def wait(fn, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = fn()
            if result:
                return result
        except (RuntimeError, urllib.error.URLError, OSError):
            pass
        time.sleep(0.3)
    raise RuntimeError('lab_wait_timeout')


def bootstrap():
    response = wait(lambda: api('/api/instance', authenticated=False))
    if response.get('setup_required') is not True:
        raise RuntimeError('lab_controller_not_pristine')
    owner = api('/api/setup', 'POST', {'email': 'owner@lab.test', 'name': 'Disposable lab',
        'password': secrets.token_urlsafe(32), 'create_pat': True, 'pat_expire_in': 1}, False)
    token = owner.get('personal_access_token')
    if not isinstance(token, str) or not token:
        raise RuntimeError('lab_setup_pat_missing')
    write('/lab/token', token)
    if api('/api/instance', authenticated=False).get('setup_required') is not False:
        raise RuntimeError('lab_setup_not_confirmed')
    # Remove only initial controller-generated Default policy in this fresh lab.
    policies = api('/api/policies')
    if any(p.get('name') != 'Default' for p in policies):
        raise RuntimeError('lab_controller_has_foreign_policy')
    for p in policies:
        api('/api/policies/' + p['id'], 'DELETE')
    if api('/api/instance/version').get('management_current_version') != '0.80.0':
        raise RuntimeError('lab_controller_version_mismatch')
    return {'real_controller_tls_bootstrap': 'pass', 'default_policy_removed_in_fresh_lab': 'pass',
            'controller_v0_80_0_verified': 'pass'}


def group(name, peers=None):
    return api('/api/groups', 'POST', {'name': name, 'peers': peers or []})['id']


def policy(name, src, dst, protocol='all', action='accept', ports=None):
    rule = {'name': name, 'enabled': True, 'action': action, 'protocol': protocol,
            'bidirectional': protocol in ('all', 'icmp'), 'sources': [src], 'destinations': [dst]}
    if ports:
        rule['ports'] = [str(p) for p in ports]
    return api('/api/policies', 'POST', {'name': name, 'enabled': True, 'rules': [rule]})['id']


def replace_lab_policy(state, lan, rules):
    # Only IDs from this disposable controller's protected bootstrap record.
    pid = state['policies'][lan]
    body = {'name': 'lab-allow-' + lan, 'enabled': True, 'rules': rules}
    api('/api/policies/' + pid, 'PUT', body)
    verify_lab_policy(pid, rules)
    return {'controller_policy_changed': True, 'controller_kernel_ack': False}


def verify_lab_policy(pid, rules):
    actual = api('/api/policies/' + pid)
    if actual.get('enabled') is not True:
        raise RuntimeError('lab_policy_readback_mismatch')
    fields = ('enabled', 'action', 'protocol', 'bidirectional', 'sources', 'destinations', 'ports')
    def selectors(items):
        result = []
        for rule in items:
            value = {k: (rule.get(k) or []) if k == 'ports' else rule.get(k) for k in fields}
            for key in ('sources', 'destinations'):
                if not isinstance(value[key], list):
                    raise RuntimeError('lab_policy_readback_mismatch')
                value[key] = [g['id'] if isinstance(g, dict) else g for g in value[key]]
            result.append(value)
        return result
    if selectors(actual.get('rules', [])) != selectors(rules):
        raise RuntimeError('lab_policy_readback_mismatch')


def save_objects(state):
    temporary = '/lab/objects.' + secrets.token_hex(8)
    write(temporary, json.dumps(state))
    os.replace(temporary, '/lab/objects')


def keys():
    for name in NAMES:
        k = api('/api/setup-keys', 'POST', {'name': name, 'type': 'one-off',
             'expires_in': 3600, 'auto_groups': [], 'usage_limit': 1, 'ephemeral': True})
        key = k.get('key')
        if not isinstance(key, str) or not key:
            raise RuntimeError('lab_setup_key_missing')
        write('/lab/keys/' + name, key)
    return {'synthetic_controller_setup_keys': 'pass'}


def api_contract():
    # Actual controller API; no mocked JSON-RPC/HTTP responses.
    src, dst = group('contract-source'), group('contract-destination')
    pid = policy('contract-drop', src, dst, protocol='tcp', action='drop', ports=[8443])
    found = api('/api/policies/' + pid)
    if found['rules'][0]['action'] != 'drop' or found['rules'][0]['ports'] != ['8443']:
        raise RuntimeError('lab_controller_policy_contract')
    api('/api/policies/' + pid, 'DELETE')
    api('/api/groups/' + src, 'DELETE')
    api('/api/groups/' + dst, 'DELETE')
    return {'real_controller_accepts_drop_ports': 'pass'}


def configure():
    def connected():
        peers = api('/api/peers')
        names = {p.get('name'): p for p in peers}
        return names if set(names) == set(NAMES) and all(p.get('connected') for p in peers) else None
    peers = wait(connected)
    peer_ids = {n: peers[n]['id'] for n in NAMES}
    clients = group('lab-route-distribution', [peer_ids['peer'], peer_ids['probe']])
    allowed = group('lab-authorized', [peer_ids['peer']])
    excluded = group('lab-excluded', [peer_ids['probe']])
    # Distinct destination groups isolate authorization of the two overlapping LANs.
    dest = {lan: group('lab-customer-' + lan, [peer_ids['primary-' + lan], peer_ids['backup-' + lan]])
            for lan in ('a', 'b')}
    # Peer connectivity also controls real routed ACL generation. Route policy
    # destination group is bound via access_control_groups, never an All fallback.
    policies = {}
    for lan, virtual in (('a', '198.18.10.0/24'), ('b', '198.18.20.0/24')):
        policies[lan] = policy('lab-allow-' + lan, allowed, dest[lan])
        for role, metric in (('primary', 100), ('backup', 200)):
            api('/api/routes', 'POST', {'description': 'lab-' + lan, 'network': virtual,
                 'network_id': 'lab-customer-' + lan, 'peer': peer_ids[role + '-' + lan],
                 'metric': metric, 'masquerade': False, 'enabled': True,
                 'keep_route': False, 'groups': [clients], 'access_control_groups': [dest[lan]]})
    state = {'peers': peer_ids, 'allowed': allowed, 'excluded': excluded, 'dest': dest,
             'policies': policies}
    write('/lab/objects', json.dumps(state))
    return {'real_six_clients_connected': 'pass', 'controller_routes_100_200': 'pass'}


def mutate(req):
    state = json.loads(Path('/lab/objects').read_text())
    if req['operation'] in ('extra_udp', 'extra_icmp', 'remove_udp', 'remove_icmp'):
        op = req['operation']
        protocol = op.split('_')[1]
        extra = state.setdefault('protocol_policies', {})
        if op.startswith('extra_'):
            if protocol in extra:
                raise RuntimeError('lab_policy_stage_repeated')
            ports = [8081] if protocol == 'udp' else None
            pid = policy('lab-extra-' + protocol, state['allowed'], state['dest']['a'], protocol=protocol, ports=ports)
            rule = {'enabled': True, 'action': 'accept', 'protocol': protocol, 'bidirectional': protocol == 'icmp',
                    'sources': [state['allowed']], 'destinations': [state['dest']['a']],
                    **({'ports': ['8081']} if ports else {})}
            verify_lab_policy(pid, [rule])
            extra[protocol] = pid
        else:
            pid = extra.pop(protocol)
            api('/api/policies/' + pid, 'DELETE')
            if any(p['id'] == pid for p in api('/api/policies')):
                raise RuntimeError('lab_policy_readback_mismatch')
        save_objects(state)
        return {'controller_policy_changed': True, 'controller_kernel_ack': False}
    if req['operation'] in ('split_sources', 'restore_b', 'restrict_a', 'restore_a'):
        op = req['operation']
        lan = 'b' if op in ('split_sources', 'restore_b') else 'a'
        source = state['excluded'] if op == 'split_sources' else state['allowed']
        protocols = [('tcp', ['8080'])] if op == 'restrict_a' else [('all', [])]
        rules = [{'name': 'lab-' + protocol, 'enabled': True, 'action': 'accept',
                  'protocol': protocol, 'bidirectional': protocol in ('all', 'icmp'),
                  'sources': [source], 'destinations': [state['dest'][lan]],
                  **({'ports': ports} if ports else {})} for protocol, ports in protocols]
        return replace_lab_policy(state, lan, rules)
    if req['operation'] == 'membership':
        members = [state['peers']['peer']] if req['allow'] else []
        api('/api/groups/' + state['allowed'], 'PUT', {'name': 'lab-authorized', 'peers': members})
        response = api('/api/groups/' + state['allowed'])
        observed = response.get('peers')
        if observed is None and type(response.get('peers_count')) is int and response['peers_count'] == 0:
            observed = []
        if not isinstance(observed, list):
            raise RuntimeError('lab_membership_reply_incomplete')
        if sorted(p['id'] for p in observed) != sorted(members):
            raise RuntimeError('lab_membership_not_confirmed')
        return {'controller_membership_changed': True, 'controller_kernel_ack': False}
    if req['operation'] == 'drop':
        pid = policy('lab-drop', state['allowed'], state['dest']['a'], action='drop')
        return {'policy_id': pid, 'controller_kernel_ack': False}
    if req['operation'] == 'same_identity':
        peers = api('/api/peers')
        now = {p.get('name'): p['id'] for p in peers}
        if now != state['peers']:
            raise RuntimeError('lab_peer_identity_changed')
        return {'unchanged_real_peer_ids': True}
    raise RuntimeError('lab_operation_refused')


def interfaces():
    return json.loads(cmd('ip', '-j', '-4', 'addr'))


def lan_dev():
    found = [i['ifname'] for i in interfaces() if any(
        ipaddress.ip_address(a['local']) in ipaddress.ip_network(prefix)
        for a in i.get('addr_info', []) for prefix in ('172.30.241.0/24', '172.30.242.0/24'))]
    if len(found) != 1:
        raise RuntimeError('lab_lan_interface_ambiguous')
    return found[0]


def translation_script(virtual, dev):
    if virtual not in ('198.18.10.0/24', '198.18.20.0/24') or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,14}', dev):
        raise RuntimeError('lab_translation_scope_refused')
    return f'''table ip lab_translation {{
 chain related_observe {{ type filter hook postrouting priority -202; policy accept;
  iifname "{dev}" oifname "wt0" ip protocol icmp ct state related ct original ip daddr {virtual} ct original proto-dst 8086 counter
 }}
 chain dn {{ type nat hook prerouting priority -101; policy accept;
  iifname "wt0" ip daddr {virtual} counter dnat ip prefix to ip daddr map {{ {virtual} : 192.168.88.0/24 }}
 }}
 chain sn {{ type nat hook postrouting priority srcnat; policy accept;
  oifname "{dev}" ct status dnat counter masquerade
 }}
}}
table inet lab_foreign {{
 chain marker {{ type filter hook forward priority 0; policy accept;
  ip saddr 203.0.113.250 counter drop
 }}
}}
table ip filter {{
 chain INPUT {{ type filter hook input priority 0; policy accept;
  ip saddr 203.0.113.250 counter drop
 }}
 chain FORWARD {{ type filter hook forward priority 0; policy accept;
  ip saddr 203.0.113.250 counter drop
 }}
}}
'''


def start(name, patch_hash, resume=False, opt_in=True):
    os.umask(0o077)
    if name not in NAMES or Path('/opt/native/patch.sha256').read_text().split()[0] != patch_hash:
        raise RuntimeError('lab_client_build_mismatch')
    if Path('/opt/native/upstream.sha').read_text().strip() != 'fca64287cf51a85552a41b022013abbfdb335452':
        raise RuntimeError('lab_client_upstream_mismatch')
    if not Path('/dev/net/tun').exists():
        Path('/dev/net').mkdir(exist_ok=True)
        os.mknod('/dev/net/tun', 0o20600, os.makedev(10, 200))
    env = dict(os.environ, SSL_CERT_FILE='/lab-ca/cert.pem', NB_STATE_DIR='/var/lib/netbird')
    router = name.startswith(('primary-', 'backup-'))
    if router:
        dev = lan_dev()
        virtual = '198.18.10.0/24' if name.endswith('-a') else '198.18.20.0/24'
        if not resume:
            real = '192.168.88.1/24' if name.startswith('primary-') else '192.168.88.3/24'
            cmd('ip', 'addr', 'add', real, 'dev', dev)
            Path('/run/netbird-binat-small-lab').mkdir(mode=0o700)
            write('/run/netbird-binat-small-lab/config.json', json.dumps([
                {'Virtual': virtual, 'Real': '192.168.88.0/24', 'Overlay': 'wt0', 'LAN': dev}]))
            # Translation only; there is NO added filter ACCEPT or ACL copy.
            cmd('nft', '-f', '-', data=translation_script(virtual, dev))
            # Capture BEFORE the native Engine can alter any foreign chain.
            cmd('python3', '/opt/lab/traffic.py', 'foreign-reference')
        if opt_in:
            env['NB_EXPERIMENTAL_BINAT_CONFIG'] = '/run/netbird-binat-small-lab/config.json'
        else:
            env.pop('NB_EXPERIMENTAL_BINAT_CONFIG', None)
    args = ['/opt/native/netbird', 'up', '--foreground-mode', '--management-url', BASE,
            '--interface-name', 'wt0', '--disable-dns', '--hostname', name,
            '--log-level', 'info', '--log-file', 'console']
    if not resume:
        args += ['--setup-key-file', '/lab-key']
    fd = os.open('/run/lab-client.log', os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    with os.fdopen(fd, 'w') as log:
        proc = subprocess.Popen(args, env=env, stdout=log, stderr=log, start_new_session=True)
    Path('/run/lab-client.pid').write_text(str(proc.pid))
    return {name + ('_launched_resume_cli' if resume else '_launched_real_cli'): 'pass'}


def echo_tcp(conn):
    with conn:
        while True:
            data = conn.recv(2048)
            if not data:
                return
            conn.sendall(data)


def lan():
    dev = lan_dev()
    cmd('ip', 'addr', 'add', '192.168.88.100/24', 'dev', dev)
    from related import record
    threading.Thread(target=record, daemon=True).start()
    from passive import capture
    threading.Thread(target=capture, args=(dev,), daemon=True).start()
    def serve_tcp(port):
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('192.168.88.100', port))
            s.listen()
            while True:
                threading.Thread(target=echo_tcp, args=(s.accept()[0],), daemon=True).start()
    def serve_udp(port):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.bind(('192.168.88.100', port))
            while True:
                data, peer = udp.recvfrom(2048)
                udp.sendto(data, peer)
    def stream(conn, peer=None):
        # Real server-originated data after one initial client handshake.
        try:
            while True:  # lifetime is the disposable LAN container, not 60 s
                payload = ('openmain-passive:%d\n' % time.monotonic_ns()).encode()
                if peer is None:
                    conn.sendall(payload)
                else:
                    conn.sendto(payload, peer)
                time.sleep(0.05)
        except OSError:
            pass
        finally:
            if peer is None:
                conn.close()
    def passive_tcp():
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('192.168.88.100', 8084)); s.listen()
            while True:
                threading.Thread(target=stream, args=(s.accept()[0],), daemon=True).start()
    def passive_udp():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(('192.168.88.100', 8085))
            while True:
                _, peer = s.recvfrom(2048)
                threading.Thread(target=stream, args=(s, peer), daemon=True).start()
    for function, args in [(serve_tcp, (8080,)), (serve_tcp, (8081,)), (serve_tcp, (8082,)),
                           (serve_udp, (8080,)), (serve_udp, (8081,)), (serve_udp, (8083,)),
                           (passive_tcp, ()), (passive_udp, ())]:
        threading.Thread(target=function, args=args, daemon=True).start()
    threading.Event().wait()


def control(req):
    op = req['operation']
    return {'bootstrap': bootstrap, 'api_contract': api_contract, 'enrollment_keys': keys,
            'configure': configure}.get(op, lambda: mutate(req))()


def main():
    try:
        if sys.argv[1] == 'initialize':
            initialize()
            return
        if sys.argv[1] == 'control':
            result = control(json.load(sys.stdin))
        elif sys.argv[1] in ('start', 'resume', 'resume-no-binat'):
            result = start(sys.argv[2], sys.argv[3], sys.argv[1] != 'start', sys.argv[1] != 'resume-no-binat')
        elif sys.argv[1] == 'lan':
            lan()
            return
        else:
            raise RuntimeError('lab_command_refused')
        print(json.dumps(result))
    except Exception as e:
        # Never print exception text / API response / profile contents.
        classified = str(e) if isinstance(e, RuntimeError) and re.fullmatch(r'lab_[a-z_0-9]+', str(e)) else type(e).__name__
        print(json.dumps({'error_class': classified}))
        sys.exit(1)


if __name__ == '__main__':
    main()
