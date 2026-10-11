#!/usr/bin/env python3
"""Zabbix 7.4 host/agent reconciliation. Credentials stay in a root-only file."""
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request
from runtime import Invalid, atomic, lan
from zabbix_access import context, require_access
from zabbix_registration import RPCError, TLSStore, key_bytes, rpc_result


class API:
    def __init__(self, url, token, access=None):
        if not url.startswith('https://'):
            raise Invalid('HTTPS required')
        self.url, self.token, self.access = url, token, access

    def __call__(self, method, params):
        if method.endswith(('.create', '.update')):
            require_access(self.url, self.access)
        data = json.dumps({'jsonrpc': '2.0', 'method': method, 'params': params, 'id': 1}).encode()
        headers = {'Content-Type': 'application/json-rpc'}
        if method == 'apiinfo.version':
            if params != []:
                raise RPCError('Zabbix-API JSON-RPC -32602: apiinfo.version verlangt leeres Array, ohne Authentifizierung.')
        else:
            headers['Authorization'] = 'Bearer ' + self.token
        request = urllib.request.Request(self.url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
        except Exception:
            raise Invalid('Zabbix API fehlgeschlagen (Details unterdrueckt)') from None
        return rpc_result(result)



def unique(values):
    if not isinstance(values, list) or len(values) > 1 or any(not isinstance(x, dict) for x in values):
        raise Invalid('API object not unique')
    return values[0] if values else None


def ensure_group(api, name):
    group = unique(api('hostgroup.get', {'output': ['groupid'], 'filter': {'name': [name]}}))
    return group['groupid'] if group else api('hostgroup.create', {'name': name})['groupids'][0]


def register_host(api, c, address, psk):
    require_access(c['url'], c.get('access'))
    store = TLSStore(c.get('receipt_file', '/var/lib/netbird-kundenrouter/zabbix-registration.json'), c, psk)
    proxy = unique(api('proxy.get', {'output': ['proxyid','name','description','proxy_groupid','local_address','local_port','operating_mode','tls_accept'], 'filter': {'name': [c['proxy']]}}))
    if not proxy or proxy.get('proxy_groupid', '0') == '0':
        raise Invalid('Proxy group/TLS conflict')
    gid = proxy['proxy_groupid']
    store.verify('proxy', proxy, gid)
    host = unique(api('host.get', {'output': ['hostid','host','monitored_by','proxy_groupid','tls_connect','tls_accept'], 'filter': {'host': [c['host']]},
                                 'selectInterfaces': 'extend', 'selectTags': 'extend'}))
    if host:
        store.verify('host', host, gid)
        if (str(host.get('monitored_by')) != '2' or host.get('proxy_groupid') != gid
            or str(host.get('tls_connect')) != '2'
            or str(host.get('tls_accept')) != '2'
            or {'tag': 'OpenMain customer', 'value': c['customer']} not in host.get('tags', [])):
            raise Invalid('Existing host ownership/TLS/monitoring conflict')
        interfaces = [i for i in host['interfaces'] if str(i.get('type')) == '1' and str(i.get('main')) == '1']
        if len(interfaces) != 1 or str(interfaces[0].get('useip')) != '1' or interfaces[0].get('port') != '10050':
            raise Invalid('Existing host agent interface conflict')
        if interfaces[0]['ip'] != address:
            api('hostinterface.update', {'interfaceid': interfaces[0]['interfaceid'], 'ip': address})
    else:
        marker = store.prepare('host', c['host'], gid)
        template = unique(api('template.get', {'output': ['templateid'], 'filter': {'host': ['Linux by Zabbix agent']}}))
        if not template:
            raise Invalid('Standard template Linux by Zabbix agent missing')
        # Separate active key avoids collisions with the official passive template.
        active = unique(api('template.get', {'output': ['templateid'], 'filter': {'host': ['OpenMain router active']}}))
        if not active:
            groups = api('templategroup.get', {'output': ['groupid'], 'filter': {'name': ['Templates/Operating systems']}})
            group = unique(groups)
            if not group:
                raise Invalid('Standard template group missing')
            active = {'templateid': api('template.create', {'host': 'OpenMain router active', 'groups': [group]})['templateids'][0]}
        item = unique(api('item.get', {'output': ['itemid', 'type', 'value_type'], 'hostids': [active['templateid']],
                                       'filter': {'key_': ['openmain.agent.active']}}))
        if item and (str(item['type']) != '7' or str(item['value_type']) != '3'):
            raise Invalid('Active template item conflict')
        if not item:
            api('item.create', {'hostid': active['templateid'], 'name': 'OpenMain active agent heartbeat',
                'key_': 'openmain.agent.active', 'type': 7, 'value_type': 3, 'delay': '1m'})
        api('host.create', {'host': c['host'], 'name': c['host'], 'monitored_by': 2,
            'proxy_groupid': gid, 'groups': [{'groupid': ensure_group(api, c['customer'])}],
            'templates': [template, active], 'tags': [{'tag': 'OpenMain customer', 'value': c['customer']}, {'tag': 'OpenMain registration', 'value': marker}],
            'interfaces': [{'type': 1, 'main': 1, 'useip': 1, 'ip': address, 'dns': '', 'port': '10050'}],
            'tls_connect': 2, 'tls_accept': 2, 'tls_psk_identity': c['identity'], 'tls_psk': psk})
    members = api('proxy.get', {'output': ['proxyid', 'local_address', 'local_port'], 'proxy_groupids': [gid]})
    addresses = []
    network = ipaddress.ip_network(c['network'])
    for member in members:
        ip = address if member['proxyid'] == proxy['proxyid'] else member['local_address']
        if ipaddress.ip_address(ip) not in network or member['local_port'] != '10051':
            raise Invalid('Proxy member outside customer LAN or unsupported port')
        addresses.append(ip)
    if address not in addresses:
        raise Invalid('Own proxy absent from customer group')
    if proxy['local_address'] != address:
        api('proxy.update', {'proxyid': proxy['proxyid'], 'local_address': address, 'local_port': '10051'})
    confirmed = unique(api('host.get', {'output': ['hostid','host','monitored_by','proxy_groupid','tls_connect','tls_accept'], 'filter': {'host': [c['host']]},
        'selectInterfaces': 'extend', 'selectTags': 'extend'}))
    if (not confirmed or str(confirmed.get('monitored_by')) != '2'
        or confirmed.get('proxy_groupid') != gid
        or not any(str(i.get('type')) == '1' and i.get('ip') == address for i in confirmed.get('interfaces', []))):
        raise Invalid('Host registration/address not confirmed')
    store.confirm('host', confirmed, gid)
    return sorted(set(addresses))


def agent_config(c, addresses):
    return '\n'.join([
        '# OpenMain managed agent configuration v1', 'PidFile=/run/zabbix/zabbix_agent2.pid',
        'LogFile=/var/log/zabbix/zabbix_agent2.log', 'LogFileSize=0',
        'Server=127.0.0.1,' + ','.join(addresses),
        'ServerActive=' + ';'.join(ip + ':10051' for ip in addresses),
        'Hostname=' + c['host'], 'TLSConnect=psk', 'TLSAccept=psk',
        'TLSPSKIdentity=' + c['identity'], 'TLSPSKFile=' + c['psk_file'],
        'Include=/etc/zabbix/zabbix_agent2.d/*.conf', 'ControlSocket=/run/zabbix/agent.sock', ''])


def merge_agent(old, c, addresses):
    if not old:
        return agent_config(c, addresses)
    expected = {'Hostname': c['host'], 'TLSConnect': 'psk', 'TLSAccept': 'psk',
                'TLSPSKIdentity': c['identity'], 'TLSPSKFile': c['psk_file']}
    values = {}
    for line in old.splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            key, value = key.strip(), value.strip()
            if key in expected or key in ('Server', 'ServerActive'):
                if key in values:
                    raise Invalid('Duplicate managed agent parameter')
                values[key] = value
    for key, value in expected.items():
        if key in values and values[key] != value:
            raise Invalid('Existing managed agent TLS/hostname differs')
        if key not in values and not c.get('initial_agent'):
            raise Invalid('Existing managed agent TLS/hostname missing')
    updates = {'Server': '127.0.0.1,' + ','.join(addresses),
               'ServerActive': ';'.join(ip + ':10051' for ip in addresses)}
    result = []
    for line in old.splitlines():
        key = line.split('=', 1)[0].strip()
        if not line.lstrip().startswith('#') and '=' in line and key in updates:
            result.append(key + '=' + updates.pop(key))
        else:
            result.append(line)
    result += [key + '=' + value for key, value in updates.items()]
    result += [key + '=' + value for key, value in expected.items() if key not in values]
    return '\n'.join(result) + '\n'


def sync(c, api=None):
    api = api or API(c['url'], c['token'], c.get('access'))
    addresses = json.loads(subprocess.check_output(['ip', '-j', '-4', 'addr', 'show']))
    routes = json.loads(subprocess.check_output(['ip', '-j', '-4', 'route', 'show', 'table', 'main']))
    detected = lan(routes, addresses, c['interface'])
    if detected['network'] != c['network']:
        raise Invalid('LAN changed; no unattended customer migration')
    target = Path(c['agent_config'])
    old = target.read_text() if target.exists() else ''
    info = target.stat() if target.exists() else None
    mode = info.st_mode & 0o777 if info else 0o644
    owner = (info.st_uid, info.st_gid) if info else None
    if old and not old.startswith('# OpenMain managed agent configuration v1\n'):
        raise Invalid('Existing agent config is not managed; explicit migration required')
    psk = key_bytes(c['psk_file']).hex()
    # Validate ownership/TLS before touching remote objects, then only change
    # endpoint lines; retain manual tuning and Includes byte-for-byte.
    merge_agent(old, c, [detected['address']])
    peers = register_host(api, c, detected['address'], psk)
    content = merge_agent(old, c, peers)
    if old != content:
        temporary = str(target) + '.candidate'
        atomic(temporary, content, 0o640)
        try:
            subprocess.run(['zabbix_agent2', '-T', '-c', temporary], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if old:
                atomic(str(target) + '.previous', old, 0o640)
            atomic(target, content, mode, owner)
            if subprocess.run(['systemctl', 'try-restart', 'zabbix-agent2'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
                if old:
                    atomic(target, old, mode, owner)
                raise Invalid('Agent restart failed; previous config restored')
        finally:
            Path(temporary).unlink(missing_ok=True)


def main():
    filename = Path('/etc/openmain-router/zabbix-sync.json')
    if len(sys.argv) > 1 and sys.argv[1] == 'install':
        c = {key: os.environ['SYNC_' + key.upper()] for key in (
            'customer', 'host', 'proxy', 'identity', 'psk_file', 'interface', 'network', 'agent_config')}
        c.update(url=os.environ.get('NB_ZABBIX_API_URL', 'https://zabbix.openmain-it.de/api_jsonrpc.php'),
                 token=os.environ['NB_ZABBIX_API_TOKEN'])
        c['receipt_file'] = os.environ.get('NB_ZABBIX_RECEIPT_FILE', '/var/lib/netbird-kundenrouter/zabbix-registration.json')
        c['access'] = context()
        c['initial_agent'] = os.environ.get('SYNC_INITIAL_AGENT') == '1'
        sync(c)
        c.pop('initial_agent')
        filename.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic(filename, json.dumps(c))
    else:
        info = filename.stat()
        if info.st_uid != 0 or info.st_mode & 0o077 or filename.is_symlink():
            raise Invalid('Insecure sync configuration')
        sync(json.loads(filename.read_text()))


if __name__ == '__main__':
    try:
        main()
    except RPCError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except Exception:
        print('Zabbix-Abgleich fehlgeschlagen: NetBird-/HTTPS-Zugriff, API-, LAN- oder Bestandskonflikt; Details zum Schutz der Secrets unterdrueckt.', file=sys.stderr)
        sys.exit(1)
