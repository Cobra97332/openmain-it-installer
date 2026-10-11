#!/usr/bin/env python3
"""Read-only local admission check. No writes, network requests or service changes.

Keep self-contained: Proxmox streams this source to an existing running guest
before copying a bundle or credentials. Missing tools/state fail closed.
"""
import argparse
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
        value = value.get('Scheme', '') + '://' + value.get('Host', '') + value.get('Path', '')
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise Rejected('Invalid management endpoint')
    return parsed.hostname.lower(), parsed.port or 443, parsed.path.rstrip('/')


def unsafe_firewall(text):
    return re.search(r'(?<![\w])(mein_binat|openmain_binat|netbird|masquerade|redirect)(?![\w])|type\s+nat(?![\w])|(?:dnat|snat)\s+(?:ip6?\s+)?(?:prefix\s+)?to(?![\w])', text) is not None


def inspect(root=Path('/'), desired=None, command=run, available=shutil.which):
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
    # Alternate configs and orphan identity stores must not be treated as fresh.
    profile = path('/etc/netbird/config.json')
    if exists('/etc/netbird'):
        if not profile.is_file() or profile.is_symlink():
            raise Rejected('Missing/ambiguous NetBird profile')
        if list(path('/etc/netbird').rglob('*.json')) != [profile]:
            raise Rejected('Multiple NetBird profiles')
    if profile.exists():
        config = read_json(profile)
        if not owned or config.get('DisableFirewall', False) is not False:
            raise Rejected('Unmanaged/disabled-firewall profile')
        if url(config.get('ManagementURL', '')) != url(desired.get('management_url', '')):
            raise Rejected('Different management endpoint')
        if not available('netbird'):
            raise Rejected('Existing identity without client')
        # A read-only status request cannot enroll, start or reconnect a peer.
        status = json.loads(command('netbird', 'status', '--json'))
        if status.get('management', {}).get('connected') is not True:
            raise Rejected('Existing profile is not connected; no automatic reconnect')
    elif state.get('peer_id') or exists('/var/lib/netbird') or available('netbird'):
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
        av = settings(agent)
        if single(av, 'Hostname') != owner['host']:
            raise Rejected('Agent hostname conflict')
        # A core-owned interrupted initial install may not have added TLS yet.
        initial = intent_path.exists() and not sync_path.exists()
        for k, expected in {'TLSConnect':'psk', 'TLSAccept':'psk', 'TLSPSKIdentity':identity, 'TLSPSKFile':single(v, 'TLSPSKFile')}.items():
            if not (initial and k not in av) and single(av, k) != expected:
                raise Rejected('Agent TLS conflict')
        for binary, filename in [('zabbix_proxy', proxy), ('zabbix_agent2', agent)]:
            if not available(binary):
                raise Rejected('Existing configuration cannot be validated')
            command(binary, '-T', '-c', str(filename))
    elif any(exists(p) for p in ('/etc/zabbix/zabbix_proxy.psk', '/var/lib/zabbix/zabbix_proxy.db', '/var/lib/netbird-kundenrouter/zabbix-proxy.env', '/etc/openmain-router/zabbix-sync.json')):
        raise Rejected('Orphan Zabbix state')
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config')
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
    inspect(desired=desired)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Bestandspruefung abgelehnt: Legacy-, Profil-, Firewall- oder Zabbix-Konflikt. Keine automatische Migration; keine Aenderungen ausgefuehrt.', file=sys.stderr)
        sys.exit(1)
