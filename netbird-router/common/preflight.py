#!/usr/bin/env python3
"""Read-only local admission check. No writes, network requests or service changes.

Keep self-contained: Proxmox streams this source to an existing running guest
before copying a bundle or credentials. Missing tools/state fail closed.
"""
import argparse
import base64
import glob
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from urllib.parse import urlsplit


class Rejected(ValueError):
    pass


def incomplete_agent_config(host):
    """Exact legacy first-install skeleton; never accept arbitrary TLS-less files."""
    return '\n'.join([
        '# OpenMain managed agent configuration v1', 'PidFile=/run/zabbix/zabbix_agent2.pid',
        'LogFile=/var/log/zabbix/zabbix_agent2.log', 'LogFileSize=0',
        'Server=127.0.0.1', 'ServerActive=127.0.0.1:10051', 'Hostname=' + host,
        'Include=/etc/zabbix/zabbix_agent2.d/*.conf', 'ControlSocket=/tmp/agent.sock', ''])


def run(*args):
    try:
        return subprocess.check_output(args, stderr=subprocess.DEVNULL, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        raise Rejected('Read-only check unavailable') from None


def read_json(path):
    if path.is_symlink() or not path.is_file():
        raise Rejected('Invalid state file')
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise Rejected('Invalid state object')
    return value


def settings(path):
    values = {}
    seen = set()
    def read(p):
        if p.is_symlink() or not p.is_file() or p in seen:
            raise Rejected('Unsafe or recursive configuration')
        seen.add(p)
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                raise Rejected('Invalid configuration')
            key, value = (part.strip() for part in line.split('=', 1))
            if key == 'Include':
                if not value.startswith('/'):
                    raise Rejected('Relative Include')
                for include in sorted(glob.glob(value)):
                    read(Path(include))
            else:
                values.setdefault(key, []).append(value)
    read(path)
    return values


def single(values, key):
    found = values.get(key, [])
    if len(found) != 1 or not found[0]:
        raise Rejected('Missing/duplicate parameter')
    return found[0]


def url(value):
    if isinstance(value, dict):
        if any(value.get(k) for k in ('User', 'Opaque', 'RawQuery', 'Fragment', 'ForceQuery')):
            raise Rejected('Invalid management endpoint')
        value = value.get('Scheme', '') + '://' + value.get('Host', '') + value.get('Path', '')
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise Rejected('Invalid management endpoint')
    return parsed.hostname.lower(), parsed.port or 443, parsed.path.rstrip('/')


PROFILE_UID = 0


def profile_json(path):
    # Profiles contain private keys: reject links, shared files and permissive modes.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != PROFILE_UID or info.st_nlink != 1 or info.st_mode & 0o077:
        os.close(fd)
        raise Rejected('Unsafe NetBird profile/state')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise Rejected('Duplicate profile field')
            result[key] = value
        return result
    with os.fdopen(fd) as stream:
        value = json.load(stream, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise Rejected('Invalid NetBird profile/state')
    return value


def profile_component(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value))


def resolve_profile(root=Path('/')):
    old = root / 'etc/netbird'
    directory = root / 'var/lib/netbird'
    for base in (old, directory):
        if base.exists() and (not base.is_dir() or base.stat().st_uid != PROFILE_UID or base.stat().st_mode & 0o022):
            raise Rejected('Unsafe profile directory')
        if base.is_symlink():
            raise Rejected('Symlinked profile directory')
        for entry in base.rglob('*'):
            if entry.is_symlink() or (entry.is_dir() and (entry.stat().st_uid != PROFILE_UID or entry.stat().st_mode & 0o022)):
                raise Rejected('Unsafe profile entry')
    legacy = old / 'config.json'
    old_files = list(old.rglob('*.json'))
    if any(p != legacy for p in old_files):
        raise Rejected('Unsupported legacy profile layout')
    selector = directory / 'active_profile.json'
    modern = selector.exists()
    if modern:
        active = profile_json(selector)
        name, user = active.get('name'), active.get('username')
        if not profile_component(name) or not isinstance(user, str) or (user and not profile_component(user)):
            raise Rejected('Invalid active profile assignment')
        if name == 'default':
            selected = directory / 'default.json'
        elif user:
            selected = directory / user / (name + '.json')
        else:
            raise Rejected('Named profile requires user assignment')
    else:
        # Never assume default.json is active when its selector is absent.
        if any(p not in (directory / 'state.json', directory / 'service.json') for p in directory.rglob('*.json')):
            raise Rejected('Missing active profile assignment')
        if not legacy.exists():
            if old.exists() or directory.exists():
                raise Rejected('Orphan NetBird profile state')
            return None
        selected, name = legacy, 'default'
    config = profile_json(selected)
    if modern or 'PrivateKey' in config:
        key = config.get('PrivateKey')
        if not isinstance(key, str) or len(base64.b64decode(key, validate=True)) != 32:
            raise Rejected('Invalid profile identity')
    interface = config.get('WgIface', 'wt0' if not modern else None)
    if not isinstance(interface, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', interface) or interface == 'lo':
        raise Rejected('Invalid NetBird interface')
    display = config.get('Name', config.get('name', name)) or name
    if not isinstance(display, str) or not display:
        raise Rejected('Invalid profile name')
    # A display-name status cannot disambiguate two profiles with the same name.
    if modern:
        for other in directory.rglob('*.json'):
            if other == selected or other == selector or other in (directory / 'state.json', directory / 'service.json') or other.name.endswith('.state.json'):
                continue
            relative = other.relative_to(directory)
            if other != directory / 'default.json' and (len(relative.parts) != 2 or not profile_component(relative.parts[0]) or not profile_component(other.stem)):
                raise Rejected('Unsupported named profile location')
            other_config = profile_json(other)
            other_name = other_config.get('Name', other_config.get('name', other.stem)) or other.stem
            if other_name == display:
                raise Rejected('Ambiguous daemon profile name')
    return dict(path='/' + str(selected.relative_to(root)), interface=interface,
                display=display, modern=modern, config=config)


def connected_profile(profile, desired_url, command=run, available=shutil.which):
    config = profile['config']
    if config.get('DisableFirewall', False) is not False or url(config.get('ManagementURL', '')) != url(desired_url):
        raise Rejected('Disabled firewall or different management endpoint')
    if not available('netbird'):
        raise Rejected('Existing identity without client')
    status = json.loads(command('netbird', 'status', '--json'))
    if not isinstance(status, dict):
        raise Rejected('Invalid client status')
    management = status.get('management', {})
    if not isinstance(management, dict) or management.get('connected') is not True:
        raise Rejected('Existing profile is not connected; no automatic reconnect')
    if 'daemonStatus' in status and status['daemonStatus'] != 'Connected':
        raise Rejected('Daemon is not connected')
    if profile['modern'] or 'url' in management:
        if url(management.get('url', '')) != url(desired_url):
            raise Rejected('Daemon management endpoint differs')
    if profile['modern'] or 'profileName' in status:
        if status.get('profileName') != profile['display']:
            raise Rejected('Daemon active profile differs')
    if profile['modern']:
        peer_ip = ipaddress.ip_interface(status.get('netbirdIp', '')).ip
        if peer_ip not in ipaddress.ip_network('100.64.0.0/10'):
            raise Rejected('Invalid connected peer IP')
        addresses = json.loads(command('ip', '-j', '-4', 'address', 'show'))
        matches = [a for item in addresses if item.get('ifname') == profile['interface']
                   for a in item.get('addr_info', []) if a.get('family') == 'inet' and a.get('local') == str(peer_ip)]
        if len(matches) != 1:
            raise Rejected('Active profile interface/IP differs')
    return profile


def profile_report(profile):
    return dict(path=profile['path'], interface=profile['interface']) if profile else dict(path='', interface='')


def unsafe_firewall(text):
    return re.search(r'(?<![\w])(mein_binat|openmain_binat|netbird|masquerade|redirect)(?![\w])|type\s+nat(?![\w])|(?:dnat|snat)\s+(?:ip6?\s+)?(?:prefix\s+)?to(?![\w])', text) is not None


def inspect(root=Path('/'), desired=None, command=run, available=shutil.which, report=False):
    desired = desired or {}
    def path(name):
        return root / name.lstrip('/')
    def exists(name):
        p = path(name)
        return p.exists() or p.is_symlink()
    for directory in ('/etc/netbird', '/var/lib/netbird', '/etc/nftables.d', '/etc/zabbix', '/var/lib/zabbix', '/var/lib/netbird-kundenrouter', '/etc/openmain-router'):
        if path(directory).is_symlink():
            raise Rejected('Symlinked installation directory')
    # Fail even on empty files, dangling symlinks, disabled or unreferenced rules.
    if exists('/etc/nftables.d/netbird-kundenrouter.nft'):
        raise Rejected('Legacy BINAT')
    pending = [path('/etc/nftables.conf'), *path('/etc/nftables.d').glob('**/*')]
    seen_rules = set()
    saved_firewall = []
    while pending:
        p = pending.pop()
        if p in seen_rules:
            continue
        seen_rules.add(p)
        if p.is_symlink():
            raise Rejected('Ambiguous firewall configuration')
        if p.is_file():
            text = p.read_text()
            saved_firewall.append(text)
            if re.search(r'\bmein_binat\b', text):
                raise Rejected('Legacy BINAT configuration')
            for include in re.findall(r'(?m)^\s*include\s+"([^"]+)"', text):
                if not include.startswith('/'):
                    raise Rejected('Ambiguous firewall include')
                pending.extend(Path(name) for name in glob.glob(str(path(include))))
    tables = []
    if available('nft'):
        rules = json.loads(command('nft', '-j', 'list', 'tables'))
        tables = [entry['table']['name'] for entry in rules['nftables'] if 'table' in entry]
        if 'mein_binat' in tables:
            raise Rejected('Active legacy BINAT')
    elif exists('/etc/netbird') or exists('/etc/nftables.d/openmain-binat.nft'):
        raise Rejected('Cannot inspect existing firewall')

    state_dir = path('/var/lib/netbird-kundenrouter')
    state, progress = {}, {}
    for filename in ('state.json', 'progress.json'):
        p = state_dir / filename
        if p.exists() or p.is_symlink():
            data = read_json(p)
            if (data.get('customer') != desired.get('customer') or data.get('role') != desired.get('role')
                or not desired.get('customer') or desired.get('role') not in ('primary', 'backup')):
                raise Rejected('Different owner/role')
            if filename == 'state.json':
                state = data
            else:
                progress = data
    owned = bool(state or progress)
    if not owned:
        if any(unsafe_firewall(text) for text in saved_firewall):
            raise Rejected('Unmanaged NAT configuration')
        if available('nft') and unsafe_firewall(command('nft', '-nn', 'list', 'ruleset')):
            raise Rejected('Unmanaged active NAT')
    lan_interface = desired.get('lan_interface', '')
    if owned:
        if not available('ip'):
            raise Rejected('Cannot inspect existing LAN')
        routes = json.loads(command('ip', '-j', '-4', 'route', 'show', 'default'))
        devices = {r.get('dev') for r in routes if r.get('dst') == 'default' and r.get('type', 'unicast') == 'unicast'} - {None}
        if not lan_interface and len(devices) != 1:
            raise Rejected('Ambiguous existing LAN')
        lan_interface = lan_interface or devices.pop()
        if lan_interface == 'lo' or lan_interface.startswith(('wt', 'tun', 'wg', 'docker', 'veth')):
            raise Rejected('Invalid LAN interface')
        addresses = json.loads(command('ip', '-j', '-4', 'address', 'show'))
        networks = []
        for item in addresses:
            if item.get('ifname') == lan_interface:
                for address in item.get('addr_info', []):
                    if address.get('family') == 'inet' and address.get('scope') == 'global':
                        net = ipaddress.ip_interface(str(address['local']) + '/' + str(address['prefixlen']))
                        if not net.ip.is_link_local and not net.ip.is_loopback and 16 <= net.network.prefixlen <= 24:
                            networks.append(str(net.network))
        if len(networks) != 1 or (state.get('lan') and state['lan'] != networks[0]):
            raise Rejected('Changed or ambiguous existing LAN')
    profile = resolve_profile(root)
    if profile:
        if not owned:
            raise Rejected('Unmanaged NetBird profile')
        connected_profile(profile, desired.get('management_url', ''), command, available)
        if resolve_profile(root) != profile:
            raise Rejected('Profile changed during admission')
    elif state.get('peer_id') or available('netbird'):
        raise Rejected('Existing client/state without identifiable profile')

    own_rules = path('/etc/nftables.d/openmain-binat.nft')
    if own_rules.exists() or own_rules.is_symlink() or 'openmain_binat' in tables:
        if not owned or not state.get('lan') or not state.get('virtual') or own_rules.is_symlink() or not own_rules.is_file():
            raise Rejected('Unowned/incomplete BINAT state')
        text = own_rules.read_text()
        if 'openmain_binat' in tables:
            active = command('nft', '-s', 'list', 'table', 'ip', 'openmain_binat')
            active = active.replace('priority -101;', 'priority dstnat - 1;').replace('priority 100;', 'priority srcnat;')
            if ' '.join(active.split()) != ' '.join(text.split()):
                raise Rejected('Live BINAT differs from owned configuration')
        # Only the exact generated grammar is admitted; hand-edited NAT needs review.
        from re import fullmatch
        pattern = (r'table ip openmain_binat \{ chain prerouting \{ type nat hook prerouting priority dstnat - 1; policy accept; '
                   r'iifname "([\w.:-]+)" ip saddr 100.64.0.0/10 ip daddr ([\d./]+) dnat ip prefix to ip daddr map \{ ([\d./]+) : ([\d./]+) \} '
                   r'\} chain postrouting \{ type nat hook postrouting priority srcnat; policy accept; '
                   r'iifname "([\w.:-]+)" oifname "([\w.:-]+)" ct status dnat ip daddr ([\d./]+) masquerade \} \}')
        match = fullmatch(pattern, ' '.join(text.split()))
        if not match or match[2] != state['virtual'] or match[3] != state['virtual'] or match[4] != state['lan'] or match[7] != state['lan'] or match[1] != match[5]:
            raise Rejected('Modified BINAT configuration')
        if profile and match[1] != profile['interface']:
            raise Rejected('Different active NetBird BINAT interface')
        if lan_interface and match[6] != lan_interface:
            raise Rejected('Different BINAT interface')
    if exists('/etc/sysctl.d/99-netbird-kundenrouter.conf'):
        p = path('/etc/sysctl.d/99-netbird-kundenrouter.conf')
        if not owned or p.is_symlink() or p.read_text().strip() != 'net.ipv4.ip_forward=1':
            raise Rejected('Unmanaged forwarding configuration')

    proxy = path('/etc/zabbix/zabbix_proxy.conf')
    agent = path('/etc/zabbix/zabbix_agent2.conf')
    if proxy.exists() or proxy.is_symlink() or agent.exists() or agent.is_symlink():
        if not owned or desired.get('zabbix_enabled', '1') != '1':
            raise Rejected('Unmanaged Zabbix installation')
        intent_path = state_dir / 'zabbix-intent.json'
        sync_path = path('/etc/openmain-router/zabbix-sync.json')
        owner = read_json(sync_path) if sync_path.exists() else read_json(intent_path)
        if sync_path.exists() and (owner.get('network') != networks[0] or owner.get('interface') != lan_interface):
            raise Rejected('Changed Zabbix LAN')
        if owner.get('customer') != desired.get('customer') or not owner.get('proxy', '').endswith('-' + desired.get('role', '')):
            raise Rejected('Different Zabbix owner')
        v = settings(proxy)
        if single(v, 'Hostname') != owner['proxy'] or single(v, 'Server') != desired.get('zabbix_server') or single(v, 'TLSConnect') != 'psk' or single(v, 'ProxyMode') != '0':
            raise Rejected('Proxy identity/server/mode conflict')
        key_path = Path(single(v, 'TLSPSKFile'))
        if not key_path.is_absolute() or not Path(single(v, 'DBName')).is_absolute():
            raise Rejected('Non-absolute key/database')
        # root remapping is for pure offline fixtures, never a CLI bypass.
        key_path = path(str(key_path))
        if key_path.is_symlink() or not key_path.is_file() or key_path.stat().st_mode & 0o007:
            raise Rejected('Missing/insecure PSK')
        key = key_path.read_text().strip()
        if not re.fullmatch(r'[a-fA-F0-9]{32,512}', key) or len(key) % 2:
            raise Rejected('Invalid PSK')
        identity = single(v, 'TLSPSKIdentity')
        if owner.get('identity', identity) != identity or owner.get('psk_file', str(Path(single(v, 'TLSPSKFile')))) != single(v, 'TLSPSKFile'):
            raise Rejected('Changed TLS ownership')
        if not agent.is_file() or agent.is_symlink() or not agent.read_text().startswith('# OpenMain managed agent configuration v1\n'):
            raise Rejected('Unmanaged agent')
        info = agent.stat()
        if info.st_uid != PROFILE_UID or info.st_nlink != 1 or info.st_mode & 0o022:
            raise Rejected('Unsafe agent configuration')
        av = settings(agent)
        if single(av, 'Hostname') != owner['host']:
            raise Rejected('Agent hostname conflict')
        # A core-owned interrupted initial install may not have added TLS yet.
        initial = intent_path.exists() and not sync_path.exists()
        tls_fields = {'TLSConnect':'psk', 'TLSAccept':'psk', 'TLSPSKIdentity':identity, 'TLSPSKFile':single(v, 'TLSPSKFile')}
        if any(k not in av for k in tls_fields):
            info = intent_path.parent.stat()
            if (not initial or any(p.is_symlink() for p in (intent_path.parent, *intent_path.parent.parents))
                or info.st_uid != PROFILE_UID or stat.S_IMODE(info.st_mode) != 0o700
                or profile_json(intent_path) != {k: owner[k] for k in ('customer', 'proxy', 'host')}
                or agent.read_text() != incomplete_agent_config(owner['host'])):
                raise Rejected('Incomplete agent differs from owned initial configuration')
        for k, expected in tls_fields.items():
            if not (initial and k not in av) and single(av, k) != expected:
                raise Rejected('Agent TLS conflict')
        for binary, filename in [('zabbix_proxy', proxy), ('zabbix_agent2', agent)]:
            if not available(binary):
                raise Rejected('Existing configuration cannot be validated')
            command(binary, '-T', '-c', str(filename))
    elif any(exists(p) for p in ('/etc/zabbix/zabbix_proxy.psk', '/var/lib/zabbix/zabbix_proxy.db', '/var/lib/netbird-kundenrouter/zabbix-proxy.env', '/etc/openmain-router/zabbix-sync.json')):
        raise Rejected('Orphan Zabbix state')
    return profile_report(profile) if report else state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config')
    parser.add_argument('--report-profile', action='store_true')
    parser.add_argument('--profile-info', action='store_true')
    parser.add_argument('--expected-profile')
    for key in ('customer', 'role', 'management-url', 'zabbix-server', 'lan-interface', 'zabbix-enabled'):
        parser.add_argument('--' + key)
    args = parser.parse_args()
    fields = {'customer':'NB_CUSTOMER_NAME', 'role':'NB_ROLE', 'management_url':'NB_MANAGEMENT_URL',
              'zabbix_server':'NB_ZABBIX_SERVER', 'lan_interface':'NB_LAN_INTERFACE', 'zabbix_enabled':'NB_ZABBIX_ENABLED'}
    values = {}
    if args.config:
        p = Path(args.config)
        if p.is_symlink() or p.stat().st_uid != 0 or stat.S_IMODE(p.stat().st_mode) & 0o077:
            raise Rejected('Insecure input file')
        values = read_json(p)
    desired = {k: getattr(args, k) or os.environ.get(env) or values.get(env, '') for k, env in fields.items()}
    desired['management_url'] = desired['management_url'] or 'https://netbird.openmain-it.de'
    desired['zabbix_server'] = desired['zabbix_server'] or '100.107.91.6'
    desired['zabbix_enabled'] = desired['zabbix_enabled'] or '1'
    if args.profile_info:
        profile = resolve_profile()
        if not profile or (args.expected_profile and profile['path'] != args.expected_profile):
            raise Rejected('Expected active profile missing/changed')
        connected_profile(profile, desired['management_url'])
        if resolve_profile() != profile:
            raise Rejected('Profile changed during status check')
        print(json.dumps(profile_report(profile)))
    else:
        result = inspect(desired=desired, report=args.report_profile)
        if args.report_profile:
            print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Bestandspruefung abgelehnt: Legacy-, Profil-, Firewall- oder Zabbix-Konflikt. Keine automatische Migration; keine Aenderungen ausgefuehrt.', file=sys.stderr)
        sys.exit(1)
