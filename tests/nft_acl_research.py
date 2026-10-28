"""Isolated rejected-design research; NOT an installer/runtime dependency.

Called by nft_kernel inside its explicitly isolated namespace. All resources
created here belong to this experiment; existing client rules are untouched.
"""
import hashlib
from pathlib import Path
import socket
import time

from nft_netlink_experiment import Netlink, Rejected


def exercise(run, frame, receive, mac):
    """Prove identity preservation/CAS, then expose live DROP bypass.

    Return a release-blocking finding; never treat a reproduced bypass as a
    successful deployment test. No conntrack entries are deleted.
    """
    for first, second in [('rn0', 'rnpeer'), ('rl0', 'rlpeer')]:
        run('ip', 'link', 'add', first, 'type', 'veth', 'peer', 'name', second)
        run('ip', 'link', 'set', first, 'up')
        run('ip', 'link', 'set', second, 'up')
    run('ip', 'addr', 'add', '100.65.0.1/10', 'dev', 'rn0')
    run('ip', 'addr', 'add', '172.20.50.1/24', 'dev', 'rl0')
    # More specific route prevents the pre-existing lab overlay route from
    # sending this experiment's return frames through the other fixture.
    run('ip', 'route', 'add', '100.65.0.10/32', 'dev', 'rn0')
    for address, device, peer in [('100.65.0.10', 'rn0', 'rnpeer'),
                                  ('172.20.50.20', 'rl0', 'rlpeer')]:
        run('ip', 'neigh', 'replace', address, 'lladdr',
            ':'.join(f'{n:02x}' for n in mac(peer)), 'nud', 'permanent', 'dev', device)

    def batch(text):
        Path('/tmp/openmain-research.nft').write_text(text)
        run('nft', '-f', '/tmp/openmain-research.nft')

    batch('''table ip research_native {
 set nb-a1b2c3d4 { type ipv4_addr; elements = { 100.65.0.10 }; }
 chain routed {
  ct state established,related counter accept
  ip saddr @nb-a1b2c3d4 ip daddr 10.40.1.0/24 counter accept
 }
 chain delegate {
  type filter hook forward priority -1; policy accept;
  iifname "rn0" oifname "rl0" ct original ip daddr 10.40.1.0/24 ct status dnat jump routed
  iifname "rl0" oifname "rn0" ct original ip daddr 10.40.1.0/24 ct status dnat jump routed
  ct original ip daddr 10.40.1.0/24 ct status dnat drop
 }
}
table ip research_gate {
 chain endpoint { type filter hook prerouting priority -450; policy accept;
  iifname "rnpeer" drop
  iifname "rlpeer" drop
 }
 chain clear { type filter hook prerouting priority filter; policy accept;
  ct original ip daddr 10.40.1.0/24 ct status dnat meta mark set meta mark & 0x7fffffff
 }
 chain nat { type nat hook prerouting priority -102; policy accept;
  iifname "rn0" ip daddr 10.40.1.0/24 dnat ip prefix to ip daddr map { 10.40.1.0/24 : 172.20.50.0/24 }
 }
 chain proof { type filter hook postrouting priority -200; policy accept;
  ct original ip daddr 10.40.1.0/24 ct status dnat meta mark & 0x80000000 == 0 drop
  ct original ip daddr 10.40.1.0/24 ct status dnat meta mark set meta mark & 0x7fffffff
 }
 chain snat_stage { type nat hook postrouting priority srcnat; policy accept;
  iifname "rn0" oifname "rl0" ct status dnat masquerade
 }
}
table ip research_compiler {
 set nb-a1b2c3d4 { type ipv4_addr; }
 chain compiled {
  ct original ip daddr != 10.40.1.0/24 ct state established,related counter accept
  ct status dnat ct original ip saddr @nb-a1b2c3d4 ct original ip daddr 10.40.1.0/24 counter meta mark set meta mark | 0x80000000 accept
 }
}
''')
    uid = b'10.40.1.0/24-' + hashlib.sha256(b'isolated-native-rule').hexdigest()[:16].encode()
    with Netlink() as api:
        native = list(api.rules('research_native', 'routed').values())
        original = native[1]
        api.replace('research_native', 'routed', [dict(original, userdata=uid)], api.generation())
        native = list(api.rules('research_native', 'routed').values())
        compiled = list(api.rules('research_compiler', 'compiled').values())
        replacements = [dict(rule, expressions=compiled[i]['expressions']) for i, rule in enumerate(native)]
        api.replace('research_native', 'routed', replacements, api.generation())
        live = api.rules('research_native', 'routed')[original['handle']]
        assert live['handle'] == original['handle'] and live['userdata'] == uid
        generation = api.generation()
        run('nft', 'add', 'chain', 'ip', 'research_native', 'concurrent_writer')
        try:
            api.replace('research_native', 'routed', [dict(live, expressions=original['expressions'])], generation)
        except Rejected:
            pass
        else:
            raise AssertionError('Stale-generation compare-and-replace succeeded')
        assert api.rules('research_native', 'routed')[original['handle']] == live
    run('nft', 'delete', 'table', 'ip', 'research_compiler')
    lan = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
    overlay = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
    try:
        lan.bind(('rlpeer', 0)); overlay.bind(('rnpeer', 0))
        lan.settimeout(2); overlay.settimeout(2)
        frame('rnpeer', 'rn0', '100.65.0.10', '10.40.1.20', 47101, 12345)
        source_port, target_port = receive(lan, '172.20.50.1', '172.20.50.20')
        time.sleep(0.3)
        frame('rlpeer', 'rl0', '172.20.50.20', '172.20.50.1', target_port, source_port)
        receive(overlay, '10.40.1.20', '100.65.0.10')
        # A supported native DROP with higher priority arrives after the last
        # compatibility observation. This is an additional writer, not a
        # mocked NFT call, and both new/existing requests MUST be denied.
        run('nft', 'insert', 'rule', 'ip', 'research_native', 'routed',
            'ip', 'saddr', '@nb-a1b2c3d4', 'ip', 'daddr', '10.40.1.0/24', 'counter', 'drop')
        bypasses = []
        for port in (47101, 47102):
            frame('rnpeer', 'rn0', '100.65.0.10', '10.40.1.20', port, 12345)
            lan.settimeout(0.5)
            try:
                receive(lan, '172.20.50.1', '172.20.50.20')
            except socket.timeout:
                continue
            bypasses.append(port)
        print('PASS: experimental opaque RuleID/handle preservation and kernel generation fence', flush=True)
        return ['P1_REJECTED_PROOF_DESIGN_BYPASSES_NEW_NATIVE_DROP:' + str(p) for p in bypasses]
    finally:
        lan.close(); overlay.close()
