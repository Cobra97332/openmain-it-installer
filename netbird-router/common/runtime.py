#!/usr/bin/env python3
"""Pure validation and atomic local operations shared by every platform."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile


class Invalid(ValueError):
    pass


def atomic(path, text, mode=0o600, owner=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        os.fchmod(fd, mode)
        if owner is not None:
            os.fchown(fd, *owner)
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def config(path):
    """Data only, no shell evaluation; refuse symlinks and insecure ownership."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o077 or not stat.S_ISREG(info.st_mode):
            raise Invalid('Konfiguration muss root gehoeren und Modus 0600 besitzen.')
        values = json.load(stream)
    allowed = {'NB_CUSTOMER_NAME', 'NB_ROLE', 'NB_SETUP_KEY', 'NB_API_TOKEN',
               'NB_ZABBIX_API_TOKEN', 'NB_ZABBIX_API_URL', 'NB_MANAGEMENT_URL',
               'NB_ZABBIX_SERVER', 'NB_LAN_INTERFACE', 'NB_VIRTUAL_NETWORK',
               'NB_HOSTNAME', 'NB_SSH_PUBLIC_KEY', 'NB_METRICS_ENABLED',
               'NB_ZABBIX_ENABLED', 'NB_CUSTOMER_ACCESS_GROUP', 'NB_PLATFORM'}
    if not isinstance(values, dict) or values.keys() - allowed:
        raise Invalid('Unbekannte Konfigurationsfelder.')
    for key, value in values.items():
        if not isinstance(value, str) or '\x00' in value or '\n' in value or '\r' in value:
            raise Invalid('Konfigurationswerte muessen einzeilige Texte sein.')
    return values


def platform(os_release, arch, model='', memory_kib=0):
    if os_release.get('ID') not in ('debian', 'raspbian') or os_release.get('VERSION_ID') != '13':
        raise Invalid('Debian 13 / Raspberry Pi OS Trixie erforderlich.')
    if arch not in ('x86_64', 'aarch64'):
        raise Invalid('64-bit amd64 oder ARM64 erforderlich.')
    if model:
        if arch != 'aarch64' or not re.match(r'Raspberry Pi (3|4|5) Model ', model):
            raise Invalid('Unterstuetzt: Raspberry Pi 3/4/5 mit ARM64-System.')
        if memory_kib < 700000:
            raise Invalid('Raspberry Pi: mindestens 1 GB nominaler RAM erforderlich.')
    elif memory_kib < 450000:
        raise Invalid('Mindestens 512 MB RAM erforderlich; 1 GB empfohlen.')


def lan(routes, addresses, override=''):
    if override and not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,15}', override):
        raise Invalid('Ungueltiger Schnittstellenname.')
    candidates = {r.get('dev') for r in routes if r.get('dst') == 'default' and r.get('type', 'unicast') == 'unicast'}
    candidates.discard(None)
    if not override and len(candidates) != 1:
        raise Invalid('LAN nicht eindeutig: NB_LAN_INTERFACE angeben.')
    iface = override or candidates.pop()
    if iface == 'lo' or iface.startswith(('wt', 'tun', 'wg', 'docker', 'veth')):
        raise Invalid('Overlay/virtuelles Interface nicht als Kunden-LAN verwenden.')
    values = []
    for item in addresses:
        if item.get('ifname') != iface:
            continue
        for addr in item.get('addr_info', []):
            if addr.get('family') == 'inet' and addr.get('scope') == 'global':
                net = ipaddress.ip_interface(f"{addr['local']}/{addr['prefixlen']}")
                if net.ip.is_link_local or net.ip.is_loopback or not 16 <= net.network.prefixlen <= 24:
                    continue
                values.append(net)
    if len(values) != 1:
        raise Invalid('Keine eindeutige LAN-IPv4 mit Praefix /16 bis /24.')
    return {'interface': iface, 'address': str(values[0].ip), 'network': str(values[0].network)}


def mapping(lan_net, used, existing='', requested=''):
    network = ipaddress.ip_network(lan_net)
    occupied = [ipaddress.ip_network(n, strict=False) for n in used]
    occupied += [network, ipaddress.ip_network('100.64.0.0/10')]
    if existing or requested:
        candidate = ipaddress.ip_network(existing or requested)
        if existing and requested and existing != requested:
            raise Invalid('Angefordertes Mapping weicht vom Bestand ab.')
        if candidate.version != 4 or candidate.prefixlen != network.prefixlen or not candidate.is_private:
            raise Invalid('BINAT muss privates IPv4-Netz mit gleichem Praefix sein.')
        if any(candidate.overlaps(n) for n in occupied):
            raise Invalid('BINAT kollidiert mit LAN, Route oder anderer Ressource.')
        return str(candidate)
    pools = ['10.30.0.0/16'] if network.prefixlen == 24 else [f'10.{n}.0.0/16' for n in range(40, 50)]
    for pool in pools:
        for candidate in ipaddress.ip_network(pool).subnets(new_prefix=network.prefixlen):
            if not any(candidate.overlaps(n) for n in occupied):
                return str(candidate)
    raise Invalid('BINAT-Pool erschoepft.')


def nft_config(iface, overlay, virtual, network):
    for name in (iface, overlay):
        if not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,15}', name):
            raise Invalid('Ungueltiges Firewall-Interface.')
    a, b = ipaddress.ip_network(virtual), ipaddress.ip_network(network)
    if a.version != 4 or b.version != 4 or a.prefixlen != b.prefixlen or a.overlaps(b):
        raise Invalid('Ungueltige BINAT-Netze.')
    # NetBird owns access policy enforcement. Only translate authorized overlay
    # traffic; never flush a foreign table or broadly masquerade other LAN traffic.
    return f'''table ip openmain_binat {{
 chain prerouting {{
  type nat hook prerouting priority dstnat - 1; policy accept;
  iifname "{overlay}" ip saddr 100.64.0.0/10 ip daddr {a} dnat ip prefix to ip daddr map {{ {a} : {b} }}
 }}
 chain postrouting {{
  type nat hook postrouting priority srcnat; policy accept;
  iifname "{overlay}" oifname "{iface}" ct status dnat ip daddr {b} masquerade
 }}
}}
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['config', 'platform', 'lan', 'mapping', 'nft'])
    parser.add_argument('args', nargs='*')
    args = parser.parse_args()
    if args.operation == 'config':
        for key, value in config(args.args[0]).items():
            sys.stdout.write(key + '=' + value + '\0')
    elif args.operation == 'platform':
        values = dict(line.strip().split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
        values = {k: v.strip('"') for k, v in values.items()}
        model_file = Path('/proc/device-tree/model')
        model = model_file.read_text().rstrip('\0') if model_file.exists() else ''
        memory = int(re.search(r'MemTotal:\s+(\d+)', Path('/proc/meminfo').read_text())[1])
        platform(values, os.uname().machine, model, memory)
    elif args.operation == 'lan':
        def ip(*parts):
            return json.loads(subprocess.check_output(['ip', '-j', '-4', *parts], stderr=subprocess.DEVNULL))
        print(json.dumps(lan(ip('route', 'show', 'table', 'main'), ip('addr', 'show'), args.args[0] if args.args else '')))
    elif args.operation == 'mapping':
        data = json.load(sys.stdin)
        print(mapping(**data))
    elif args.operation == 'nft':
        print(nft_config(*args.args), end='')


if __name__ == '__main__':
    try:
        main()
    except (Invalid, ValueError, KeyError, OSError, subprocess.SubprocessError):
        print('Vorpruefung fehlgeschlagen: Plattform, Konfiguration oder Netzwerk nicht eindeutig/kompatibel.', file=sys.stderr)
        sys.exit(1)
