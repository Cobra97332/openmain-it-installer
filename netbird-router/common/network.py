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


def policy_access(api, customer_group, global_group, admin_group, resource_groups, resource_id=''):
    """Require the existing one-way Admin policy; reject other grants to this resource."""
    policies = rows(api('GET', '/policies'))
    central = one(policies, 'name', 'Admin \u2192 Kunden')
    if not central or central.get('enabled') is not True:
        raise Invalid('Zentrale Admin-Policy fehlt oder ist deaktiviert')
    def expected(rule):
        return (rule.get('enabled') is True and rule.get('action') == 'accept'
                and rule.get('protocol') == 'all' and rule.get('bidirectional') is False
                and ids(rule.get('sources', [])) == [admin_group]
                and ids(rule.get('destinations', [])) == [global_group]
                and rule.get('ports') in (None, []) and rule.get('port_ranges') in (None, [])
                and not rule.get('destinationResource') and not rule.get('sourceResource'))
    central_rules = central.get('rules')
    if not isinstance(central_rules, list) or not any(
            isinstance(rule, dict) and expected(rule) for rule in central_rules):
        raise Invalid('Zentrale Admin-Policy hat keine einseitige Admin-Freigabe')
    for policy in policies:
        if policy.get('enabled') is not True:
            continue
        rules = policy.get('rules')
        if not isinstance(rules, list):
            raise Invalid('Aktive NetBird-Policy ohne pruefbare Regeln')
        for rule in rules:
            if not isinstance(rule, dict):
                raise Invalid('Aktive NetBird-Policy mit ungueltiger Regel')
            if rule.get('enabled') is not True or rule.get('action') != 'accept':
                continue
            sources = ids(rule.get('sources', []))
            destinations = ids(rule.get('destinations', []))
            direct = rule.get('destinationResource')
            direct_id = direct.get('id') if isinstance(direct, dict) else ''
            from_resource = rule.get('sourceResource')
            from_id = from_resource.get('id') if isinstance(from_resource, dict) else ''
            if customer_group in sources or global_group in sources or (
                    resource_id and from_id == resource_id):
                raise Invalid('Kundenrouter/-ressource als Quelle aktiver Policy; keine automatische Freigabe')
            if set(destinations) & set(resource_groups) or (
                    resource_id and direct_id == resource_id):
                if policy.get('id') != central['id'] or not expected(rule):
                    raise Invalid('Weitere aktive Policy fuer Kunden-BINAT; manuelle Pruefung erforderlich')
    return central['id']


def reconcile(api, customer, role, lan, peer, customer_group, global_group, admin_group, routes, requested=''):
    if (any(not isinstance(x, str) or not x for x in
            (customer_group, global_group, admin_group))
            or len({customer_group, global_group, admin_group}) != 3):
        raise Invalid('Kunden-, globale und Admin-Gruppe muessen getrennt sein')
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
    if own and own.get('description') != marker:
        raise Invalid('Bestehende BINAT-Ressource ist nicht eindeutig OpenMain-verwaltet')
    existing_groups = ids(own.get('groups')) if own else []
    desired_groups = list(dict.fromkeys(existing_groups + [global_group, customer_group]))
    policy_access(api, customer_group, global_group, admin_group,
                  desired_groups, own['id'] if own else '')
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
                  'description': marker, 'enabled': False, 'groups': desired_groups})
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
    if own.get('enabled') is not True or set(ids(own.get('groups'))) != set(desired_groups):
        payload = {k: own[k] for k in ('name', 'address', 'description') if k in own}
        payload.update(groups=desired_groups, enabled=True)
        api('PUT', base + '/resources/' + own['id'], payload)
    observed_resource = one(api('GET', base + '/resources'), 'id', own['id'])
    if (not observed_resource or observed_resource.get('name') != 'BINAT-' + customer
        or observed_resource.get('description') != marker
        or observed_resource.get('address') != virtual
        or observed_resource.get('enabled') is not True
        or set(ids(observed_resource.get('groups'))) != set(desired_groups)):
        raise Invalid('BINAT-Ressourcengruppen nach API-Aenderung nicht bestaetigt')
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
    observed = one(api('GET', base + '/routers'), 'peer', peer)
    if not observed or any(observed.get(k) != v for k, v in payload.items()):
        raise Invalid('Routing-Peer nach Aenderung nicht bestaetigt')
    return {'virtual': virtual, 'network': network['id'], 'resource': own['id'], 'router': router['id']}


if __name__ == '__main__':
    try:
        routes = json.loads(subprocess.check_output(['ip', '-j', '-4', 'route', 'show', 'table', 'main']))
        print(json.dumps(reconcile(API(os.environ['NB_MANAGEMENT_URL'], os.environ['NB_API_TOKEN']),
              *sys.argv[1:], routes, os.environ.get('NB_VIRTUAL_NETWORK', ''))))
    except Invalid as exc:
        print(f'NetBird-Netzwerkabgleich abgebrochen: {exc}', file=sys.stderr)
        sys.exit(1)
    except Exception:
        print('NetBird-Netzwerkabgleich abgebrochen: API- oder Bestandsfehler. Keine Secrets protokolliert.', file=sys.stderr)
        sys.exit(1)
