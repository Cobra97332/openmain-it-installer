#!/usr/bin/env python3
"""Real kernel refusal checks; synthetic driver, not a real NetBird daemon."""
import json
import os
from pathlib import Path
import socket
import subprocess

if (os.environ.get('OPENMAIN_ISOLATED_TEST') != '1' or not Path('/.dockerenv').exists()
        or {n for _, n in socket.if_nameindex()} != {'lo'}):
    raise SystemExit('Requires fresh network-none Docker lab')

def nft(*args):
    return subprocess.check_output(['nft', *args], text=True)

def batch(text):
    subprocess.run(['nft', '-f', '-'], input=text, text=True, check=True)

owner = subprocess.Popen(['/native/small-native-lab'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
assert json.loads(owner.stdout.readline()).get('Ready')

def check(expected):
    before = nft('-s', '-j', 'list', 'ruleset')
    owner.stdin.write('foreign-preflight\n')
    owner.stdin.flush()
    assert json.loads(owner.stdout.readline())['OK'] is expected
    assert nft('-s', '-j', 'list', 'ruleset') == before, 'Read-only preflight changed rules'
    assert 'return' not in nft('list', 'chain', 'ip', 'netbird_binat_native_gate_v1', 'ready'), 'Admission opened'

try:
    check(True)
    # Exact untagged iptables-nft patterns and tagged native external-chain
    # equivalents, including reply ESTABLISHED/RELATED and wildcard names.
    rules = ['iifname "nb0" accept', 'oifname "nb0" ct state established,related accept',
             'iifname "nb0" counter accept comment "frwacceptiif"',
             'iifname "nb*" accept', 'iifname "nb0" jump bypass']
    for family, table, chain in [('ip', 'filter', 'FORWARD'), ('ip', 'filter', 'INPUT'), ('inet', 'stale', 'forward')]:
        hook = 'input' if chain == 'INPUT' else 'forward'
        for rule in rules:
            batch(f'table {family} {table} {{ chain bypass {{ accept; }}\n chain {chain} {{ type filter hook {hook} priority 0; policy drop; {rule}; }}\n }}\n')
            check(False)
            batch(f'delete table {family} {table}\n')  # only our synthetic fixture
            check(True)
    batch('table inet stale { set interfaces { type ifname; elements = { "nb0" }; }\n chain forward { type filter hook forward priority 0; policy drop; iifname @interfaces accept; }\n }\n')
    check(False)
    batch('delete table inet stale\n')
    batch('table inet firewalld { chain forward { type filter hook forward priority 0; policy accept; }\n }\n')
    check(False)
    batch('delete table inet firewalld\n')
    # Valid unrelated interface permits and administrator drops are untouched.
    batch('table inet administrator { chain forward { type filter hook forward priority 0; policy accept; iifname "eth9" accept; iifname "nb0" drop; }\n }\n')
    check(True)
    # A stale-rule restart check does not authorize creating/reusing a TUN.
    assert {n for _, n in socket.if_nameindex()} == {'lo'}
    print('PASS: real kernel stale INPUT/FORWARD and reply ACCEPT, tags, wildcard, interface set, firewalld state refused; no rule mutation or admission; foreign DROP preserved')
finally:
    owner.stdin.close()
    assert owner.wait(timeout=15) == 0
