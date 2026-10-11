#!/usr/bin/env python3
"""Guarded NetBird 0.80 BINAT ACL compatibility.

NetBird's FORWARD ACL matches the virtual destination, after PREROUTING DNAT
has translated it. Replace *only* the native authorization rule; never create
an independent permit. The NetBird dynamic source set and ACL lifecycle remain
authoritative. Unknown NetBird versions/layouts fail closed.
"""
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

MARKER = 'openmain-binat-ct-original-v1'
SOURCE_RE = re.compile(r'@nb-[0-9a-f]{8}$')
IFACE_RE = re.compile(r'[a-zA-Z0-9_.:-]{1,15}$')
CONFIG = '/etc/nftables.d/openmain-binat-acl.json'


class Refused(Exception):
    pass


def configuration(path=CONFIG):
    item = Path(path)
    if item.is_symlink() or not item.is_file():
        raise Refused('Missing or symbolic-link BINAT configuration')
    meta = item.stat()
    if meta.st_uid != 0 or stat.S_IMODE(meta.st_mode) & 0o022:
        raise Refused('BINAT configuration has unsafe ownership or write access')
    cfg = json.loads(item.read_text())
    if not isinstance(cfg, dict) or set(cfg) != {'overlay', 'lan', 'virtual', 'real'}:
        raise Refused('Invalid BINAT configuration keys')
    if not all(isinstance(cfg[key], str) and IFACE_RE.fullmatch(cfg[key])
               for key in ('overlay', 'lan')):
        raise Refused('Invalid interface')
    if cfg['overlay'] == cfg['lan']:
        raise Refused('BINAT interfaces overlap')
    virtual, real = (ipaddress.IPv4Network(cfg[key], strict=True)
                     for key in ('virtual', 'real'))
    if virtual.prefixlen != real.prefixlen or virtual.overlaps(real):
        raise Refused('Incompatible BINAT networks')
    cfg['virtual'], cfg['real'] = str(virtual), str(real)
    return cfg


def _match(expr, field, value):
    obj = expr.get('match') if isinstance(expr, dict) else None
    return (isinstance(obj, dict) and obj.get('op') == '==' and
            obj.get('left') == {'payload': {'protocol': 'ip', 'field': field}} and
            obj.get('right') == value)


def _prefix(network):
    net = ipaddress.IPv4Network(network)
    return {'prefix': {'addr': str(net.network_address), 'len': net.prefixlen}}


def native(rule, cfg):
    expr = rule.get('expr')
    if not isinstance(expr, list) or len(expr) != 4 or any(
            not isinstance(x, dict) for x in expr):
        return None
    match = expr[0].get('match')
    if not isinstance(match, dict) or match.get('op') != '==' or (
            match.get('left') != {'payload': {'protocol': 'ip', 'field': 'saddr'}}):
        return None
    source = match.get('right')
    if not isinstance(source, str) or not SOURCE_RE.fullmatch(source):
        return None
    if (not _match(expr[1], 'daddr', _prefix(cfg['virtual'])) or
            set(expr[2]) != {'counter'} or expr[3] != {'accept': None}):
        return None
    return source


def managed(rule, cfg):
    # In nft JSON comments may be rule attributes or expressions.
    text = json.dumps(rule, sort_keys=True)
    def original_address(expr):
        if not isinstance(expr, dict) or not isinstance(expr.get('match'), dict):
            return False
        left = expr['match'].get('left')
        conntrack = left.get('ct') if isinstance(left, dict) else None
        # Real nft JSON also includes "family": "ip" on conntrack addresses.
        return (isinstance(conntrack, dict) and conntrack.get('key') == 'daddr'
                and conntrack.get('dir') == 'original'
                and conntrack.get('family', 'ip') == 'ip')

    return (MARKER in text and str(ipaddress.IPv4Network(cfg['virtual']).network_address) in text and
            str(ipaddress.IPv4Network(cfg['real']).network_address) in text and
            cfg['overlay'] in text and cfg['lan'] in text and
            any(original_address(x) for x in rule.get('expr', [])))


def transaction(rules, cfg):
    if not isinstance(rules, dict) or not isinstance(rules.get('nftables'), list):
        raise Refused('Invalid nftables response')
    candidates, existing = [], []
    for entry in rules['nftables']:
        rule = entry.get('rule') if isinstance(entry, dict) else None
        if (not isinstance(rule, dict) or rule.get('family') != 'ip' or
                rule.get('table') != 'netbird' or rule.get('chain') != 'netbird-rt-fwd'):
            continue
        if managed(rule, cfg):
            existing.append(rule)
            continue
        expr = rule.get('expr', [])
        if any(_match(x, 'daddr', _prefix(cfg['virtual'])) for x in expr):
            source = native(rule, cfg)
            handle = rule.get('handle')
            if source is None or type(handle) is not int or handle <= 0:
                raise Refused('NetBird route rule format changed; no unsafe rewrite')
            candidates.append((handle, source))
    if existing and candidates:
        raise Refused('Ambiguous mixed NetBird ACL generations')
    if existing:
        if len(existing) != 1:
            raise Refused('Ambiguous managed route ACLs')
        return ''
    if len(candidates) != 1:
        raise Refused('Expected exactly one NetBird policy-generated ACL for the virtual network')
    handle, source = candidates[0]
    return (f'replace rule ip netbird netbird-rt-fwd handle {handle} '
            f'iifname "{cfg["overlay"]}" oifname "{cfg["lan"]}" '
            f'ip saddr {source} ct status dnat '
            f'ct original daddr & {ipaddress.IPv4Network(cfg["virtual"]).netmask} == {ipaddress.IPv4Network(cfg["virtual"]).network_address} ip daddr {cfg["real"]} '
            f'counter accept comment "{MARKER}"\n')


def nft(*args):
    result = subprocess.run(['nft', *args], capture_output=True, timeout=8)
    if result.returncode:
        raise Refused('nftables read/validation/apply failed')
    return result.stdout


def main():
    cfg = configuration()
    rules = json.loads(nft('-j', 'list', 'chain', 'ip', 'netbird', 'netbird-rt-fwd'))
    command = transaction(rules, cfg)
    if not command:
        return
    source = re.search(r'ip saddr (@nb-[0-9a-f]{8}) ', command)
    if not source:
        raise Refused('Missing NetBird source-set binding')
    nft('-j', 'list', 'set', 'ip', 'netbird', source.group(1)[1:])
    with tempfile.NamedTemporaryFile(mode='w', prefix='openmain-acl-', delete=False) as file:
        file.write(command)
        path = file.name
    try:
        nft('-c', '-f', path)
        nft('-f', path)
    finally:
        os.unlink(path)


if __name__ == '__main__':
    try:
        main()
    except (Refused, ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        print('BINAT/NetBird-ACL nicht kompatibel oder nicht bereit; keine Policy-Freigabe hinzugefuegt.', file=sys.stderr)
        sys.exit(1)
