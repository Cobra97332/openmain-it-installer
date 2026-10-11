#!/usr/bin/env python3
"""Read-only NetBird admission and authenticated-TLS reachability, without PSKs."""
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from preflight import connected_profile, resolve_profile
from runtime import Invalid


class Pending(Invalid):
    pass


class ServiceUnavailable(Invalid):
    """Optional reverse-proxy inventory is unsupported or inaccessible."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def context():
    return dict(api_url=os.environ.get('NB_ZABBIX_API_URL', 'https://zabbix.openmain-it.de/api_jsonrpc.php'),
                management=os.environ.get('NB_MANAGEMENT_URL', 'https://netbird.openmain-it.de'),
                token=os.environ.get('NB_API_TOKEN', ''),
                server=os.environ.get('NB_ZABBIX_SERVER', '100.107.91.6'),
                group=os.environ.get('NB_ZABBIX_PROXY_GROUP', 'Zabbix-Kunden-Proxies'),
                policy=os.environ.get('NB_ZABBIX_POLICY_NAME', 'Zabbix Proxy to Server'),
                proxy_env={k: v for k, v in os.environ.items() if k.lower() in
                           ('https_proxy', 'http_proxy', 'all_proxy', 'no_proxy')})


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise Invalid('NetBird-Antwort unvollstaendig/mehrdeutig; keine Zabbix-Registrierung.')
    return value


class NetBird:
    def __init__(self, c):
        endpoint = urlsplit(c['management'])
        if endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or not c['token'] or any(x in c['token'] for x in '\r\n'):
            raise Invalid('NetBird-Zugriffskonfiguration fehlt/ungueltig; API-Token erforderlich.')
        self.base, self.token = c['management'].rstrip('/') + '/api', c['token']
        self.opener = urllib.request.build_opener(NoRedirect())

    def __call__(self, path):
        try:
            request = urllib.request.Request(self.base + path, headers={'Authorization': 'Token ' + self.token})
            with self.opener.open(request, timeout=10) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if path.startswith('/reverse-proxies/services') and error.code in (403, 404, 405, 501):
                raise ServiceUnavailable('Reverse-Proxy-Inventar nicht verfuegbar.') from None
            raise Invalid('NetBird-Zugriffspruefung fehlgeschlagen; keine Zabbix-Registrierung (Details unterdrueckt).') from None
        except Exception:
            raise Invalid('NetBird-Zugriffspruefung fehlgeschlagen; keine Zabbix-Registrierung (Details unterdrueckt).') from None


def group_members(group, gid):
    if not isinstance(group, dict) or group.get('id') != gid or not isinstance(group.get('name'), str) or not group['name']:
        raise Invalid('NetBird-Gruppenbestand unvollstaendig.')
    for field in ('peers', 'resources'):
        count = group.get(field + '_count')
        values = group.get(field)
        if field not in group or isinstance(count, bool) or not isinstance(count, (int, float)) or count < 0 or int(count) != count:
            raise Invalid('NetBird-Gruppenzaehler ungueltig.')
        if values is None and count == 0:
            values = []
        if not isinstance(values, list) or len(values) != count:
            raise Invalid('NetBird-Gruppenbestand widerspruechlich.')
        ids = []
        for value in values:
            ids.append(identifier(value.get('id') if isinstance(value, dict) else value))
            if field == 'resources' and (not isinstance(value, dict) or not value.get('type') or not isinstance(value['type'], str)):
                raise Invalid('NetBird-Ressourcenzuordnung unvollstaendig.')
        if len(set(ids)) != len(ids):
            raise Invalid('NetBird-Gruppenzuordnung mehrdeutig.')
        if field == 'peers':
            peers = set(ids)
    return peers


def rows(value):
    if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
        raise Invalid('NetBird-Antwort unvollstaendig.')
    ids = [identifier(x.get('id')) for x in value]
    if len(set(ids)) != len(ids):
        raise Invalid('NetBird-IDs mehrdeutig.')
    return value


def references(values):
    if not isinstance(values, list):
        raise Invalid('NetBird-Policy-Zuordnung unvollstaendig.')
    return [identifier(x.get('id') if isinstance(x, dict) else x) for x in values]


def permits(policy, source, target, port, members):
    if policy.get('enabled') is not True:
        return False
    rules = policy.get('rules')
    if not isinstance(rules, list):
        raise Invalid('NetBird-Policy-Regeln unvollstaendig.')
    for rule in rules:
        if not isinstance(rule, dict):
            raise Invalid('NetBird-Policy-Regel ungueltig.')
        if rule.get('enabled') is not True or rule.get('action') != 'accept' or rule.get('protocol') not in ('tcp', 'all'):
            continue
        if source not in references(rule.get('sources')):
            continue
        ports = rule.get('ports', [])
        ranges = rule.get('port_ranges', [])
        if not isinstance(ports, list) or not isinstance(ranges, list):
            raise Invalid('NetBird-Policy-Ports ungueltig.')
        covered = rule['protocol'] == 'all' or (not ports and not ranges) or str(port) in [str(p) for p in ports]
        for interval in ranges:
            if not isinstance(interval, dict) or type(interval.get('start')) is not int or type(interval.get('end')) is not int or not 1 <= interval['start'] <= interval['end'] <= 65535:
                raise Invalid('NetBird-Policy-Portbereich ungueltig.')
            covered |= interval['start'] <= port <= interval['end']
        if covered and any(target in members(gid) for gid in references(rule.get('destinations'))):
            return True
    return False


def resolve(host, port):
    values = {str(ipaddress.ip_address(x[4][0])) for x in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    if not values:
        raise Pending('DNS fuer den Zabbix-API-Zugriffsweg noch nicht verfuegbar.')
    return values


def api_endpoint(api_url):
    endpoint = urlsplit(api_url)
    if endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.fragment or endpoint.port not in (None, 443) or any(ord(ch) < 33 for ch in api_url):
        raise Invalid('Zabbix-API benoetigt einen eindeutigen HTTPS/443-Endpunkt ohne URL-Zugangsdaten.')
    return endpoint


def service_access(api, api_url, ip, port, interface, device, source, resolver):
    """A service IP is not a peer. Admission still requires a real HTTPS probe."""
    endpoint = api_endpoint(api_url)
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', endpoint.hostname) or '.' not in endpoint.hostname:
        raise Invalid('Reverse-Proxy-Zugriff erfordert den konfigurierten DNS-Domainnamen.')
    try:
        ipaddress.ip_address(endpoint.hostname)
    except ValueError:
        pass
    else:
        raise Invalid('Reverse-Proxy-Zugriff ueber beliebige IP-URLs wird nicht zugelassen.')
    if port != 443 or device != interface or ipaddress.ip_address(ip) not in ipaddress.ip_network('100.64.0.0/10'):
        raise Invalid('Reverse-Proxy-Service muss ueber HTTPS/443 am aktiven NetBird-Interface erreichbar sein.')
    if ip not in resolver(endpoint.hostname, 443):
        raise Invalid('Reverse-Proxy-Service-IP gehoert nicht zur konfigurierten Zabbix-API-Domain.')
    try:
        services = rows(api('/reverse-proxies/services'))
    except ServiceUnavailable:
        return  # DNS/interface + verified TLS/HTTP/Zabbix response remain mandatory.
    if any(not isinstance(service.get('domain'), str) for service in services):
        raise Invalid('Reverse-Proxy-Dienstbestand unvollstaendig.')
    matches = [service for service in services if service['domain'].lower() == endpoint.hostname]
    if not matches:
        return  # This API supplies no information about the configured service.
    if len(matches) != 1:
        raise Invalid('Reverse-Proxy-Domain nicht eindeutig; keine Registrierung.')
    service = matches[0]
    if service.get('enabled') is not True or service.get('terminated', False) is not False or service.get('mode', 'http') != 'http':
        raise Invalid('Konfigurierter Reverse-Proxy-Dienst ist nicht fuer HTTPS aktiviert.')
    # These optional fields describe private services in NetBird 0.80. Never
    # ignore an explicit denial; older deployments may omit both ACL fields.
    if 'private' in service or 'access_groups' in service:
        if service.get('private') is not True or source not in references(service.get('access_groups')):
            raise Invalid('Reverse-Proxy-Dienst erlaubt Zabbix-Kunden-Proxies nicht.')


def endpoints(api_url, proxies=None, bypass=None, resolver=resolve):
    endpoint = api_endpoint(api_url)
    proxies = urllib.request.getproxies() if proxies is None else proxies
    bypass = urllib.request.proxy_bypass if bypass is None else bypass
    proxy = None if bypass(endpoint.hostname) else proxies.get('https') or proxies.get('all')
    if proxy:
        proxy = urlsplit(proxy if '://' in proxy else 'http://' + proxy)
        if proxy.scheme not in ('http', 'https') or not proxy.hostname or proxy.path not in ('', '/') or proxy.query or proxy.fragment:
            raise Invalid('Nicht unterstuetzter HTTPS-Proxy-Zugriffsweg.')
        host, port = proxy.hostname, proxy.port or (443 if proxy.scheme == 'https' else 80)
    else:
        host, port = endpoint.hostname, 443
    return {(ip, port) for ip in resolver(host, port)}


def route(ip):
    try:
        value = json.loads(subprocess.check_output(['ip', '-j', 'route', 'get', ip], stderr=subprocess.DEVNULL, timeout=5))
        if not isinstance(value, list) or len(value) != 1 or not value[0].get('dev') or value[0].get('type', 'unicast') != 'unicast':
            raise ValueError()
        return value[0]['dev']
    except Exception:
        raise Invalid('Zabbix-API-Route nicht eindeutig; kein geratenes Ziel-Peer-Mapping.') from None


def https(api_url, targets):
    # No token/PSK. TLS, SNI, CA trust and endpoint correctness are checked using
    # the same curl proxy/NO_PROXY environment as the shell registration path.
    request = json.dumps(dict(jsonrpc='2.0', method='apiinfo.version', params=[], id=1))
    try:
        result = subprocess.run(['curl', '--disable', '--no-insecure', '--no-proxy-insecure', '--no-location', '-fsS', '--proto', '=https', '--connect-timeout', '5', '--max-time', '8',
        api_url, '-H', 'Content-Type: application/json-rpc', '--data', request, '-w', '\n%{http_code}\n%{remote_ip}\n%{url_effective}'],
            capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        raise Pending('Zabbix-API HTTPS/443 noch nicht erreichbar; Zugriff wird erneut geprueft.') from None
    try:
        body, status, remote, effective = result.stdout.decode().rsplit('\n', 3)
        data = json.loads(body)
        if result.returncode or status != '200' or effective != api_url or remote not in {ip for ip, port in targets} or data.get('jsonrpc') != '2.0' or type(data.get('id')) is not int or data['id'] != 1 or 'error' in data or not isinstance(data.get('result'), str) or not re.fullmatch(r'7\.4(?:\.[0-9]+)*', data['result']):
            raise ValueError()
    except Exception:
        raise Pending('Zabbix-API ueber HTTPS/443 noch nicht erreichbar/verifiziert; DNS, Proxy, TLS und NetBird-Freigabe pruefen.') from None


def tcp(server):
    try:
        with socket.create_connection((server, 10051), timeout=3):
            pass
    except OSError:
        raise Pending('Zabbix-Server TCP/10051 noch nicht erreichbar; NetBird-Freigabe pruefen.') from None


def check(api, c, peer_ip, interface, targets, route_check=route, api_url=None, service_resolver=resolve):
    peers = rows(api('/peers'))
    def peer(ip):
        matches = [p for p in peers if p.get('ip') == ip]
        if len(matches) != 1:
            raise Invalid('Zabbix-Zugriffsziel/Router-Peer nicht eindeutig; kein Hostnamen-Fallback.')
        return matches[0]['id']
    own, server = peer(peer_ip), peer(c['server'])
    if own == server:
        raise Invalid('Zabbix-Server darf nicht der Kundenrouter sein.')
    groups = rows(api('/groups'))
    matches = [g for g in groups if g.get('name') == c['group']]
    if len(matches) != 1:
        raise Invalid('NetBird-Gruppe Zabbix-Kunden-Proxies fehlt/mehrdeutig; keine Registrierung.')
    source = matches[0]['id']
    cache = {}
    def members(gid):
        if gid not in cache:
            cache[gid] = group_members(api('/groups/' + identifier(gid)), gid)
        return cache[gid]
    if own not in members(source):
        raise Invalid('Router-Mitgliedschaft in Zabbix-Kunden-Proxies nicht bestaetigt; keine Zabbix-Registrierung.')
    policies = rows(api('/policies'))
    named = [p for p in policies if p.get('name') == c['policy']]
    if len(named) != 1 or not permits(named[0], source, server, 10051, members):
        raise Pending('Bestehende Zabbix Proxy to Server Policy erlaubt den bestaetigten Server nicht auf TCP/10051.')
    for ip, port in targets:
        device = route_check(ip)
        overlay = ipaddress.ip_address(ip) in ipaddress.ip_network('100.64.0.0/10')
        if overlay or device == interface:
            if device != interface:
                raise Invalid('Overlay-API-Ziel wird nicht ueber das aktive NetBird-Interface geroutet.')
            matches = [p for p in peers if p.get('ip') == ip]
            if not matches:
                service_access(api, api_url, ip, port, interface, device, source, service_resolver)
                continue
            target = peer(ip)
            if not any(permits(p, source, target, port, members) for p in policies):
                raise Pending('NetBird-Policy fuer den tatsaechlichen HTTPS-API-/Proxy-Zielpeer fehlt; vorhandene Policies bleiben unveraendert.')


def require_access(api_url, c=None, *, api=None, identity=None, target_resolver=endpoints,
                   route_check=route, tcp_probe=tcp, https_probe=https, pause=time.sleep, attempts=12,
                   service_resolver=resolve):
    c = c or context()
    api_endpoint(api_url)
    if api_url != c.get('api_url', api_url):
        raise Invalid('Zabbix-API-Domain/Pfad weicht vom konfigurierten Zugriffskontext ab.')
    # Scheduled reconciliation must use the same proxy route as registration.
    if 'proxy_env' in c:
        for key in list(os.environ):
            if key.lower() in ('https_proxy', 'http_proxy', 'all_proxy', 'no_proxy'):
                del os.environ[key]
        os.environ.update(c['proxy_env'])
        proxies = urllib.request.getproxies()
        if not proxies.get('https') and proxies.get('all'):
            os.environ['https_proxy'] = proxies['all']
    api = api or NetBird(c)
    if identity is None:
        profile = resolve_profile()
        if not profile:
            raise Invalid('Aktives NetBird-Profil fehlt; keine Zabbix-Registrierung.')
        connected_profile(profile, c['management'])
        peer_ip = subprocess.check_output(['netbird', 'status', '--ipv4'], stderr=subprocess.DEVNULL, text=True, timeout=5).strip().split('/')[0]
        identity = peer_ip, profile['interface']
    ipaddress.IPv4Address(identity[0])
    last = None
    for attempt in range(attempts):
        try:
            targets = target_resolver(api_url)
            check(api, c, *identity, targets, route_check, api_url, service_resolver)
            tcp_probe(c['server'])
            https_probe(api_url, targets)
            # Reconfirm memberships/policy after successful propagation/probe.
            check(api, c, *identity, targets, route_check, api_url, service_resolver)
            return
        except (Pending, socket.gaierror, TimeoutError) as error:
            last = error
            if attempt + 1 < attempts:
                pause(5)
    if isinstance(last, Pending):
        raise last
    raise Invalid('Zabbix-Zugriff nach Wartezeit nicht freigegeben; Bestand bleibt erhalten. Auftrag erneut starten.')


if __name__ == '__main__':
    try:
        require_access(os.environ.get('NB_ZABBIX_API_URL', 'https://zabbix.openmain-it.de/api_jsonrpc.php'))
    except Invalid as error:
        print(str(error) + ' Keine PSK-/Policy-/Identitaetsaenderung durch die Zugriffskontrolle.', file=sys.stderr)
        sys.exit(1)
    except Exception:
        print('Zabbix-Zugriff nicht sicher bestaetigt; Details zum Schutz der Secrets unterdrueckt. Auftrag spaeter erneut starten.', file=sys.stderr)
        sys.exit(1)
