"""LAB ONLY: single-owner original-tuple publication reference.

Not a NetBird adapter. Never import this from the installer. A production
adapter must own the complete management-map lifetime, restart and error paths.
"""
import ipaddress
import re
import subprocess
import threading

TABLE = 'nb_binat_snapshot_lab'


class Rejected(Exception):
    pass


def network(value):
    try:
        net = ipaddress.IPv4Network(value, strict=True)
    except (ValueError, TypeError) as exc:
        raise Rejected('Invalid IPv4 prefix') from exc
    if net.prefixlen < 8:
        raise Rejected('Unbounded prefix')
    return str(net)


def scopes(items):
    if not isinstance(items, list) or not 1 <= len(items) <= 16:
        raise Rejected('Invalid scope count')
    result = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {'virtual', 'real', 'overlay', 'lan'}:
            raise Rejected('Invalid scope')
        virtual, real = network(item['virtual']), network(item['real'])
        v, r = ipaddress.IPv4Network(virtual), ipaddress.IPv4Network(real)
        if v.prefixlen != r.prefixlen or v.overlaps(r):
            raise Rejected('Incompatible translation')
        for key in ('overlay', 'lan'):
            if not isinstance(item[key], str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}', item[key]):
                raise Rejected('Invalid interface')
        if item['overlay'] == item['lan'] or any(v.overlaps(ipaddress.IPv4Network(x['virtual'])) for x in result):
            raise Rejected('Ambiguous scope')
        result.append(dict(item, virtual=virtual, real=real))
    return result


def compile_rules(rules, mappings):
    if not isinstance(rules, list) or len(rules) > 256:
        raise Rejected('Invalid complete snapshot')
    lines = []
    validated = []
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {'source', 'destination', 'protocol', 'ports', 'action'}:
            raise Rejected('Incomplete selector')
        source, dest = network(rule['source']), network(rule['destination'])
        if not any(ipaddress.IPv4Network(dest).subnet_of(ipaddress.IPv4Network(x['virtual'])) for x in mappings):
            raise Rejected('Unmanaged destination')
        proto, action, ports = rule['protocol'], rule['action'], rule['ports']
        if proto not in ('all', 'tcp', 'udp', 'icmp') or action not in ('accept', 'drop'):
            raise Rejected('Unknown action or protocol')
        if not isinstance(ports, list) or len(ports) > 32 or (ports and proto not in ('tcp', 'udp')):
            raise Rejected('Invalid ports')
        for pair in ports:
            if not isinstance(pair, list) or len(pair) != 2 or any(type(n) is not int for n in pair) or not 1 <= pair[0] <= pair[1] <= 65535:
                raise Rejected('Invalid port range')
        validated.append((source, dest, proto, action, ports))
    # Native DROP takes precedence over overlapping ACCEPT, including new DROP.
    for source, dest, proto, action, ports in sorted(validated, key=lambda x: x[3] != 'drop'):
        expr = f'ct original ip saddr {source} ct original ip daddr {dest}'
        if proto != 'all':
            expr += f' ct protocol {proto}'
        for pair in ports or [None]:
            port = '' if pair is None else f' ct original proto-dst {pair[0]}-{pair[1]}'
            lines.append(f'add rule ip {TABLE} current {expr}{port} counter {action}\n')
    return ''.join(lines)


class Publisher:
    """One serialized owner; no nft read/copy/reconcile, marks or CT deletion.

    Close BEFORE processing a map; reopen only after every native operation
    succeeds. A rejected/stale call leaves authorization empty. IDs are local
    monotonic publication tickets, not assumed management-server revisions.
    """
    def __init__(self, mappings, execute=None):
        self.mappings = scopes(mappings)
        self.execute = execute or self._execute
        self.lock = threading.Lock()
        self.ticket = -1

    @staticmethod
    def _execute(script):
        subprocess.run(['nft', '-f', '-'], input=script, text=True, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)

    def bootstrap(self):
        # New lab namespace ONLY. Never adopt/delete an unknown existing table.
        script = f'create table ip {TABLE}\nadd chain ip {TABLE} current\n'
        script += f'add chain ip {TABLE} guard {{ type filter hook postrouting priority -200; policy accept; }}\n'
        for item in self.mappings:
            base = f'ct original ip daddr {item["virtual"]}'
            script += f'add rule ip {TABLE} guard {base} ct state invalid,untracked,related counter drop\n'
            for direction, incoming, outgoing, address in [('original', item['overlay'], item['lan'], 'daddr'), ('reply', item['lan'], item['overlay'], 'saddr')]:
                match = f'{base} ct direction {direction} iifname "{incoming}" oifname "{outgoing}" ip {address} {item["real"]}'
                script += f'add rule ip {TABLE} guard {match} jump current\n'
            script += f'add rule ip {TABLE} guard {base} counter drop\n'
        self.execute(script)

    def publish(self, ticket, rules, native_apply=lambda: None):
        with self.lock:
            self.execute(f'flush chain ip {TABLE} current\n')
            if type(ticket) is not int or ticket <= self.ticket:
                raise Rejected('Stale publication ticket')
            # Consume ticket even on error; failure must not admit stale reopen.
            self.ticket = ticket
            batch = compile_rules(rules, self.mappings)
            native_apply()  # Represents synchronous native full-map processing.
            self.execute(f'flush chain ip {TABLE} current\n' + batch)

    def close(self):
        with self.lock:
            self.execute(f'flush chain ip {TABLE} current\n')
