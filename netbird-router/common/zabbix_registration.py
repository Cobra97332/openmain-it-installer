#!/usr/bin/env python3
"""Private write-only TLS provenance, never a claim to read back remote PSKs."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import sys
from runtime import Invalid, atomic

ROOT_UID = 0


class RPCError(Invalid):
    pass


def rpc_result(response):
    if not isinstance(response, dict) or response.get('jsonrpc') != '2.0' or type(response.get('id')) is not int or response['id'] != 1:
        raise RPCError('Zabbix-API: ungueltige JSON-RPC-Antwort (Details unterdrueckt).')
    if 'error' in response:
        error = response['error']
        code = error.get('code') if isinstance(error, dict) else None
        causes = {-32700: 'JSON nicht parsebar', -32600: 'ungueltiger Request',
                  -32601: 'Methode nicht verfuegbar', -32602: 'ungueltige/nicht unterstuetzte Parameter oder fehlende Berechtigung',
                  -32603: 'interner API-Fehler', -32500: 'Anwendungs-/Berechtigungs- oder Bestandsfehler',
                  -32400: 'Systemfehler', -32300: 'Transportfehler'}
        label = str(code) if type(code) is int and -32768 <= code <= 32767 else 'ungueltig'
        raise RPCError('Zabbix-API JSON-RPC ' + label + ': ' + (causes.get(code, 'nicht klassifizierter API-Fehler') if type(code) is int else 'ungueltiger Fehlercode') + ' (Details unterdrueckt).')
    if 'result' not in response or response['result'] is None:
        raise RPCError('Zabbix-API: Ergebnis fehlt (Details unterdrueckt).')
    return response['result']


def key_bytes(filename):
    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        owners = {ROOT_UID}
        try:
            owners.add(pwd.getpwnam('zabbix').pw_uid)
        except KeyError:
            pass
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid not in owners or stat.S_IMODE(info.st_mode) not in (0o600, 0o640):
            raise Invalid('Unsichere PSK-Datei; kein Ersatzschluessel.')
        value = stream.read().strip()
        if not re.fullmatch(r'[0-9a-fA-F]{32,512}', value) or len(value) % 2:
            raise Invalid('Ungueltiger PSK; kein Ersatzschluessel.')
        return bytes.fromhex(value)


class TLSStore:
    def __init__(self, filename, c, psk):
        if not re.fullmatch(r'[0-9a-fA-F]{32,512}', psk.strip()) or len(psk.strip()) % 2:
            raise Invalid('Ungueltiger PSK; kein Ersatzschluessel.')
        self.path = Path(filename)
        self.binding = {key: c[key] for key in ('url', 'customer', 'proxy', 'identity')}
        if any(not isinstance(value, str) or not value for value in self.binding.values()):
            raise Invalid('Unvollstaendiger TLS-Zuordnungskontext.')
        self.binding['key_digest'] = hashlib.sha256(bytes.fromhex(psk.strip())).hexdigest()
        parent = self.path.parent
        if not parent.is_absolute() or any(p.is_symlink() for p in (parent, *parent.parents)):
            raise Invalid('Unsicherer Registrierungsnachweis-Pfad.')
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = parent.stat()
        if info.st_uid != ROOT_UID or stat.S_IMODE(info.st_mode) != 0o700:
            raise Invalid('Registrierungsverzeichnis muss root gehoeren und 0700 sein.')
        self.data = dict(version=1, binding=self.binding, objects={})
        if self.path.exists() or self.path.is_symlink():
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd) as stream:
                info = os.fstat(stream.fileno())
                if info.st_uid != ROOT_UID or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or not stat.S_ISREG(info.st_mode):
                    raise Invalid('Unsicherer Registrierungsnachweis.')
                self.data = json.load(stream)
            if self.data.get('version') != 1 or self.data.get('binding') != self.binding or not isinstance(self.data.get('objects'), dict):
                raise Invalid('Lokaler TLS-/Kunden-/API-Nachweis weicht ab; keine Aenderung.')

    def entry(self, kind):
        entry = self.data['objects'].get(kind)
        if entry is not None and (not isinstance(entry, dict) or not isinstance(entry.get('marker'), str)
            or not re.fullmatch(r'OpenMain managed [0-9a-f]{48}', entry['marker'])
            or not isinstance(entry.get('name'), str) or not entry['name']
            or not isinstance(entry.get('group'), str) or not re.fullmatch(r'[1-9][0-9]*', entry['group'])
            or 'id' not in entry or (entry['id'] is not None and (not isinstance(entry['id'], str) or not re.fullmatch(r'[1-9][0-9]*', entry['id'])))):
            raise Invalid('Registrierungsnachweis unvollstaendig/mehrdeutig.')
        return entry

    def prepare(self, kind, name, group):
        entry = self.entry(kind)
        if entry is None:
            entry = dict(name=name, group=group, marker='OpenMain managed ' + secrets.token_hex(24), id=None)
            self.data['objects'][kind] = entry
            atomic(self.path, json.dumps(self.data) + '\n')
        if entry.get('name') != name or entry.get('group') != group or entry.get('id') is not None:
            raise Invalid('Registrierungsbestand fehlt oder weicht ab; keine automatische Neuerstellung.')
        return entry['marker']

    def verify(self, kind, obj, group):
        entry = self.entry(kind)
        oid = obj.get(kind + 'id')
        markers = [obj.get('description')] if kind == 'proxy' else [x.get('value') for x in obj.get('tags', []) if x.get('tag') == 'OpenMain registration']
        marker = markers[0] if len(markers) == 1 else None
        name = obj.get('name') if kind == 'proxy' else obj.get('host')
        if not entry or entry.get('name') != name or entry.get('group') != group or obj.get('proxy_groupid') != group or not isinstance(oid, str) or not re.fullmatch(r'[1-9][0-9]*', oid) or entry.get('id') not in (None, oid) or marker != entry.get('marker'):
            raise Invalid('Proxy/Host-TLS-Zuordnung nicht lokal belegt; fremden/mehrdeutigen Bestand manuell pruefen. Keine Aenderung.')
        if str(obj.get('tls_accept')) != '2' or (kind == 'proxy' and str(obj.get('operating_mode')) != '0') or (kind == 'host' and (str(obj.get('tls_connect')) != '2' or str(obj.get('monitored_by')) != '2')):
            raise Invalid('Bestehender TLS-Modus/Betriebsart weicht ab; keine Aenderung.')
        return entry

    def confirm(self, kind, obj, group):
        self.verify(kind, obj, group)['id'] = obj[kind + 'id']
        atomic(self.path, json.dumps(self.data) + '\n')


def main():
    action = sys.argv[1]
    if action == 'result':
        print(json.dumps(rpc_result(json.load(sys.stdin))))
        return
    c = {key: os.environ['REG_' + key.upper()] for key in ('url', 'customer', 'proxy', 'identity')}
    psk = key_bytes(os.environ['REG_PSK_FILE']).hex()
    store = TLSStore(os.environ['REG_FILE'], c, psk)
    if action == 'check':
        return
    if action == 'prepare':
        if bytes.fromhex(sys.stdin.read().strip()) != bytes.fromhex(psk):
            raise Invalid('PSK waehrend Registrierung geaendert; keine Aenderung.')
        print(store.prepare('proxy', c['proxy'], os.environ['REG_GROUP']))
    else:
        obj = json.load(sys.stdin)
        getattr(store, action)('proxy', obj, obj['proxy_groupid'])


if __name__ == '__main__':
    try:
        main()
    except RPCError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except Exception:
        print('TLS-Registrierungsnachweis ungueltig/unvollstaendig; PSK und Bestand bleiben erhalten. Manuell pruefen, keine automatische Migration.', file=sys.stderr)
        sys.exit(1)
