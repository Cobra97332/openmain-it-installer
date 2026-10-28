#!/usr/bin/env python3
"""Reconcile NetBird network resources without replacing customer-owned objects."""
import json
import os
import subprocess
import sys
import urllib.request
from runtime import Invalid, mapping


class API:
    def __init__(self, url, token):
        if not url.startswith('https://') or any(c in token for c in '\r\n'):
            raise Invalid('HTTPS/token validation')
        self.url, self.token = url.rstrip('/') + '/api', token

    def __call__(self, method, path, data=None):
        request = urllib.request.Request(self.url + path, method=method,
            headers={'Authorization': 'Token ' + self.token, 'Content-Type': 'application/json'},
            data=json.dumps(data).encode() if data is not None else None)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except Exception:
            raise Invalid('NetBird API fehlgeschlagen; keine automatische Wiederholung schreibender Requests.') from None


def rows(value):
    if not isinstance(value, list) or any(not isinstance(x, dict) or not x.get('id') for x in value):
        raise Invalid('Unvollstaendige API-Liste')
    return value


def one(values, key, value):
    found = [x for x in rows(values) if x.get(key) == value]
    if len(found) > 1:
        raise Invalid('Doppelte API-Objekte; keine automatische Auswahl')
    return found[0] if found else None


def identifier(value):
    if not isinstance(value, dict) or not isinstance(value.get('id'), str) or not value['id']:
        raise Invalid('API-Schreibvorgang nicht bestaetigt')
    return value['id']


def ids(values):
    if not isinstance(values, list):
        raise Invalid('Unvollstaendige Gruppenliste')
    result = [x.get('id') if isinstance(x, dict) else x for x in values]
    if any(not isinstance(x, str) or not x for x in result):
        raise Invalid('Ungueltige Gruppenreferenz')
    return result


def reconcile(api, customer, role, lan, peer, group, access_group, routes, requested=''):
    if not isinstance(access_group, str) or not access_group or access_group == group:
        raise Invalid('Separate OpenMain-IT-Zugriffsgruppe erforderlich')
    metric = {'primary': 100, 'backup': 200}[role]
    marker = 'OpenMain managed BINAT v1'
    networks = rows(api('GET', '/networks'))
    network = one(networks, 'name', customer)
    all_resources = []
    for item in networks:
        for resource in rows(api('GET', f"/networks/{item['id']}/resources")):
            all_resources.append((item['id'], resource))
    own = one([r for n, r in all_resources if network and n == network['id']], 'name', 'BINAT-' + customer)
    if role == 'backup' and not own:
        raise Invalid('Primary muss zuerst ein Mapping reservieren')
    used = [r['address'] for _, r in all_resources if r is not own and r.get('type', 'subnet') in ('subnet', 'host')]
    # Include legacy NetBird routes and local non-default IPv4 routes.
    for route in rows(api('GET', '/routes')):
        if route.get('network') and route['network'] != '0.0.0.0/0':
            used.append(route['network'])
    for route in routes:
        dest = route.get('dst', 'default')
        if dest != 'default' and not (own and dest == own['address'] and route.get('dev', '').startswith('wt')):
            used.append(dest)
    virtual = mapping(lan, used, own['address'] if own else '', requested)
    if own:
        if group not in ids(own.get('groups')):
            raise Invalid('Bestehende Ressource hat andere Zugriffsgruppen; keine Erweiterung')
        if own.get('enabled') is not True and own.get('description') != marker:
            raise Invalid('Manuell deaktivierte Ressource bleibt unveraendert')
    if not network:
        network = api('POST', '/networks', {'name': customer, 'description': 'OpenMain customer router'})
        identifier(network)
        # Detect an ambiguous successful/parallel create rather than selecting first.
        check = one(api('GET', '/networks'), 'name', customer)
        if not check or check['id'] != network['id']:
            raise Invalid('Netzwerkanlage nicht eindeutig bestaetigt')
    base = f"/networks/{network['id']}"
    if not own:
        own = api('POST', base + '/resources', {'name': 'BINAT-' + customer, 'address': virtual,
                  'description': marker, 'enabled': False, 'groups': [group]})
        identifier(own)
    # Before enabling the reservation, re-read every allocation. Competing
    # installers must serialize per tenant if their API is eventually consistent.
    current = rows(api('GET', '/networks'))
    occupied = []
    for item in current:
        entries = rows(api('GET', f"/networks/{item['id']}/resources"))
        if item['id'] == network['id']:
            check = one(entries, 'name', 'BINAT-' + customer)
            if not check or check['id'] != own['id']:
                raise Invalid('Ressourcenanlage mehrdeutig')
        occupied += [r['address'] for r in entries if r['id'] != own['id'] and r.get('type', 'subnet') in ('subnet', 'host')]
    mapping(lan, occupied, virtual)
    if own.get('enabled') is not True:
        payload = {k: own[k] for k in ('name', 'address', 'description') if k in own}
        payload.update(groups=ids(own.get('groups')), enabled=True)
        api('PUT', base + '/resources/' + own['id'], payload)
    routers = rows(api('GET', base + '/routers'))
    router = one(routers, 'peer', peer)
    if any(r.get('peer') != peer and r.get('enabled') is True and r.get('metric') == metric for r in routers):
        raise Invalid('Andere aktive Routing-Peers verwenden bereits diese Rolle/Metrik')
    payload = {'peer': peer, 'metric': metric, 'masquerade': False, 'enabled': True}
    if router:
        if router.get('enabled') is not True or router.get('masquerade') is not False:
            raise Invalid('Bestehender Routing-Peer deaktiviert oder andere NAT-Konfiguration')
        if router.get('metric') != metric:
            api('PUT', base + '/routers/' + router['id'], payload)
    else:
        router = api('POST', base + '/routers', payload)
        identifier(router)
    # Customer access remains scoped to its own group; never all-customer access.
    policies = rows(api('GET', '/policies'))
    name = 'OpenMain customer ' + customer
    policy = one(policies, 'name', name)
    if policy:
        if policy.get('enabled') is not True or not any(
            r.get('enabled') is True and r.get('action') == 'accept' and r.get('protocol') == 'all'
            and access_group in ids(r.get('sources', [])) and group in ids(r.get('destinations', []))
            for r in policy.get('rules', [])):
            raise Invalid('Kundenpolicy widerspricht erforderlichem Zugriff; bleibt unveraendert')
    else:
        api('POST', '/policies', {'name': name, 'description': 'Customer-scoped BINAT access',
            'enabled': True, 'source_posture_checks': [], 'rules': [{'name': name, 'enabled': True,
            'action': 'accept', 'protocol': 'all', 'bidirectional': True,
            'sources': [access_group], 'destinations': [group]}]})
    observed = one(api('GET', base + '/routers'), 'peer', peer)
    if not observed or any(observed.get(k) != v for k, v in payload.items()):
        raise Invalid('Routing-Peer nach Aenderung nicht bestaetigt')
    return {'virtual': virtual, 'network': network['id'], 'resource': own['id'], 'router': router['id']}


if __name__ == '__main__':
    try:
        routes = json.loads(subprocess.check_output(['ip', '-j', '-4', 'route', 'show', 'table', 'main']))
        print(json.dumps(reconcile(API(os.environ['NB_MANAGEMENT_URL'], os.environ['NB_API_TOKEN']),
              *sys.argv[1:], routes, os.environ.get('NB_VIRTUAL_NETWORK', ''))))
    except Exception:
        print('NetBird-Netzwerkabgleich abgebrochen: API-, Bestands- oder Adresskonflikt. Keine Secrets protokolliert.', file=sys.stderr)
        sys.exit(1)
