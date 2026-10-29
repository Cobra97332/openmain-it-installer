#!/usr/bin/env python3
"""Zabbix 7.4 host/agent reconciliation. Credentials stay in a root-only file."""
import ipaddress
import json
import os
import stat
from pathlib import Path
import subprocess
import sys
import urllib.request
from runtime import Invalid, atomic, lan
from preflight import incomplete_agent_config
import zabbix_registration
from zabbix_access import context, require_access
from zabbix_registration import RPCError, TLSStore, key_bytes, rpc_result


class SyncError(Invalid):
    """Only fixed, secret-free error classes may reach the terminal."""
    def __init__(self, code):
        super().__init__('Zabbix-Abgleich: ' + code + '; kein erfolgreicher Installationsstatus.')


def checked(code, action, *args):
    try:
        return action(*args)
    except (SyncError, RPCError):
        raise
    except Exception:
        raise SyncError(code) from None


def resume_initial_agent(old, c, psk):
    """Admit only the exact old skeleton, bound to a private unfinished intent."""
    if old != incomplete_agent_config(c['host']):
        raise SyncError('AGENT_INITIAL_CONFIG_MODIFIED')
    receipt = Path(c.get('receipt_file', '/var/lib/netbird-kundenrouter/zabbix-registration.json'))
    intent = receipt.parent / 'zabbix-intent.json'
    sync_file = Path(c.get('sync_file', '/etc/openmain-router/zabbix-sync.json'))
    if sync_file.exists() or sync_file.is_symlink():
        raise SyncError('AGENT_INITIAL_ALREADY_COMPLETED')
    try:
        if any(p.is_symlink() for p in (intent.parent, *intent.parent.parents)):
            raise ValueError()
        info = intent.parent.stat()
        if info.st_uid != zabbix_registration.ROOT_UID or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError()
        fd = os.open(intent, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if (info.st_uid != zabbix_registration.ROOT_UID or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1 or not stat.S_ISREG(info.st_mode)):
                raise ValueError()
            def unique_fields(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError()
                    result[key] = value
                return result
            if json.load(stream, object_pairs_hook=unique_fields) != {k: c[k] for k in ('customer', 'proxy', 'host')}:
                raise ValueError()
    except Exception:
        raise SyncError('AGENT_INITIAL_INTENT_UNVERIFIED') from None
    # Do not create a receipt as evidence for an existing file.
    if not receipt.is_file() or receipt.is_symlink():
        raise SyncError('AGENT_INITIAL_PROXY_UNCONFIRMED')
    store = checked('TLS_RECEIPT_INVALID', TLSStore, receipt, c, psk)
    proxy = checked('TLS_RECEIPT_INVALID', store.entry, 'proxy')
    host = checked('TLS_RECEIPT_INVALID', store.entry, 'host')
    if not proxy or proxy['id'] is None or proxy['name'] != c['proxy']:
        raise SyncError('AGENT_INITIAL_PROXY_UNCONFIRMED')
    if host and (host['name'] != c['host'] or host['group'] != proxy['group']):
        raise SyncError('AGENT_INITIAL_HOST_CONFLICT')


class API:
    def __init__(self, url, token, access=None):
        if not url.startswith('https://'):
            raise SyncError('API_HTTPS_REQUIRED')
        self.url, self.token, self.access = url, token, access

    def __call__(self, method, params):
        if method.endswith(('.create', '.update')):
            checked('NETBIRD_HTTPS_ACCESS_UNCONFIRMED', require_access, self.url, self.access)
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
            raise SyncError('API_TRANSPORT_OR_JSON_INVALID') from None
        return rpc_result(result)



def unique(values):
    if not isinstance(values, list) or len(values) > 1 or any(not isinstance(x, dict) for x in values):
        raise SyncError('API_OBJECT_AMBIGUOUS')
    return values[0] if values else None


def ensure_group(api, name):
    group = unique(api('hostgroup.get', {'output': ['groupid'], 'filter': {'name': [name]}}))
    return group['groupid'] if group else api('hostgroup.create', {'name': name})['groupids'][0]


def register_host(api, c, address, psk):
    checked('NETBIRD_HTTPS_ACCESS_UNCONFIRMED', require_access, c['url'], c.get('access'))
    store = checked('TLS_RECEIPT_INVALID', TLSStore, c.get('receipt_file', '/var/lib/netbird-kundenrouter/zabbix-registration.json'), c, psk)
    proxy = unique(api('proxy.get', {'output': ['proxyid','name','description','proxy_groupid','local_address','local_port','operating_mode','tls_accept'], 'filter': {'name': [c['proxy']]}}))
    if not proxy or proxy.get('proxy_groupid', '0') == '0':
        raise SyncError('PROXY_GROUP_OR_TLS_CONFLICT')
    gid = proxy['proxy_groupid']
    checked('PROXY_TLS_RECEIPT_UNVERIFIED', store.verify, 'proxy', proxy, gid)
    host = unique(api('host.get', {'output': ['hostid','host','monitored_by','proxy_groupid','tls_connect','tls_accept'], 'filter': {'host': [c['host']]},
                                 'selectInterfaces': 'extend', 'selectTags': 'extend'}))
    if host:
        checked('HOST_TLS_RECEIPT_UNVERIFIED', store.verify, 'host', host, gid)
        # selectTags=extend may return hosttagid/automatic metadata. Require
        # exactly one matching customer tag by its actual name/value, rather
        # than comparing whole API dictionaries byte-for-byte.
        tags = host.get('tags')
        customer_tags = ([tag for tag in tags if isinstance(tag, dict)
                          and tag.get('tag') == 'OpenMain customer']
                         if isinstance(tags, list) else [])
        if (str(host.get('monitored_by')) != '2' or host.get('proxy_groupid') != gid
            or str(host.get('tls_connect')) != '2'
            or str(host.get('tls_accept')) != '2'
            or len(customer_tags) != 1 or customer_tags[0].get('value') != c['customer']):
            raise SyncError('HOST_OWNERSHIP_TLS_OR_MONITORING_CONFLICT')
        interfaces = [i for i in host['interfaces'] if str(i.get('type')) == '1' and str(i.get('main')) == '1']
        if len(interfaces) != 1 or str(interfaces[0].get('useip')) != '1' or interfaces[0].get('port') != '10050':
            raise SyncError('HOST_AGENT_INTERFACE_CONFLICT')
        if interfaces[0]['ip'] != address:
            api('hostinterface.update', {'interfaceid': interfaces[0]['interfaceid'], 'ip': address})
    else:
        marker = checked('HOST_REGISTRATION_INTENT_CONFLICT', store.prepare, 'host', c['host'], gid)
        template = unique(api('template.get', {'output': ['templateid'], 'filter': {'host': ['Linux by Zabbix agent']}}))
        if not template:
            raise SyncError('STANDARD_AGENT_TEMPLATE_MISSING')
        # Separate active key avoids collisions with the official passive template.
        active = unique(api('template.get', {'output': ['templateid'], 'filter': {'host': ['OpenMain router active']}}))
        if not active:
            groups = api('templategroup.get', {'output': ['groupid'], 'filter': {'name': ['Templates/Operating systems']}})
            group = unique(groups)
            if not group:
                raise SyncError('STANDARD_TEMPLATE_GROUP_MISSING')
            active = {'templateid': api('template.create', {'host': 'OpenMain router active', 'groups': [group]})['templateids'][0]}
        item = unique(api('item.get', {'output': ['itemid', 'type', 'value_type'], 'hostids': [active['templateid']],
                                       'filter': {'key_': ['openmain.agent.active']}}))
        if item and (str(item['type']) != '7' or str(item['value_type']) != '3'):
            raise SyncError('ACTIVE_TEMPLATE_ITEM_CONFLICT')
        if not item:
            api('item.create', {'hostid': active['templateid'], 'name': 'OpenMain active agent heartbeat',
                'key_': 'openmain.agent.active', 'type': 7, 'value_type': 3, 'delay': '1m'})
        role = c.get('role', 'primary')
        if role not in ('primary', 'backup'):
            raise SyncError('HOST_ROLE_INVALID')
        display = c['customer'] + ' \u2013 NetBird Router ' + ('1' if role == 'primary' else '2')
        api('host.create', {'host': c['host'], 'name': display, 'monitored_by': 2,
            'proxy_groupid': gid, 'groups': [{'groupid': ensure_group(api, 'OpenMain customer: ' + c['customer'])}],
            'templates': [template, active], 'tags': [{'tag': 'OpenMain customer', 'value': c['customer']}, {'tag': 'OpenMain registration', 'value': marker}],
            'interfaces': [{'type': 1, 'main': 1, 'useip': 1, 'ip': address, 'dns': '', 'port': '10050'}],
            'tls_connect': 2, 'tls_accept': 2, 'tls_psk_identity': c['identity'], 'tls_psk': psk})
    members = api('proxy.get', {'output': ['proxyid', 'local_address', 'local_port'], 'proxy_groupids': [gid]})
    addresses = []
    network = ipaddress.ip_network(c['network'])
    for member in members:
        ip = address if member['proxyid'] == proxy['proxyid'] else member['local_address']
        if ipaddress.ip_address(ip) not in network or member['local_port'] != '10051':
            raise SyncError('PROXY_MEMBER_LAN_OR_PORT_CONFLICT')
        addresses.append(ip)
    if address not in addresses:
        raise SyncError('PROXY_GROUP_MEMBERSHIP_MISSING')
    if proxy['local_address'] != address:
        api('proxy.update', {'proxyid': proxy['proxyid'], 'local_address': address, 'local_port': '10051'})
    confirmed = unique(api('host.get', {'output': ['hostid','host','monitored_by','proxy_groupid','tls_connect','tls_accept'], 'filter': {'host': [c['host']]},
        'selectInterfaces': 'extend', 'selectTags': 'extend'}))
    if (not confirmed or str(confirmed.get('monitored_by')) != '2'
        or confirmed.get('proxy_groupid') != gid
        or not any(str(i.get('type')) == '1' and i.get('ip') == address for i in confirmed.get('interfaces', []))):
        raise SyncError('HOST_REGISTRATION_OR_ADDRESS_UNCONFIRMED')
    checked('HOST_TLS_CONFIRMATION_FAILED', store.confirm, 'host', confirmed, gid)
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
                    raise SyncError('AGENT_PARAMETER_DUPLICATE')
                values[key] = value
    for key, value in expected.items():
        if key in values and values[key] != value:
            raise SyncError('AGENT_TLS_OR_HOSTNAME_CONFLICT')
    if any(key not in values for key in expected):
        resume_initial_agent(old, c, checked('PSK_FILE_INVALID', key_bytes, c['psk_file']).hex())
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
    detected = checked('LAN_NOT_UNIQUE', lan, routes, addresses, c['interface'])
    if detected['network'] != c['network']:
        raise SyncError('LAN_CHANGED_NO_AUTOMATIC_MIGRATION')
    target = Path(c['agent_config'])
    def read_agent():
        if any(p.is_symlink() for p in (target, *target.parents)):
            raise SyncError('AGENT_FILE_UNSAFE')
        if not target.exists():
            return '', None
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != zabbix_registration.ROOT_UID or info.st_mode & 0o022):
                raise SyncError('AGENT_FILE_UNSAFE')
            return stream.read(), info
    old, info = checked('AGENT_FILE_UNREADABLE', read_agent)
    mode = info.st_mode & 0o777 if info else 0o644
    owner = (info.st_uid, info.st_gid) if info else None
    if info and not old.startswith('# OpenMain managed agent configuration v1\n'):
        raise SyncError('AGENT_FILE_UNMANAGED')
    psk = checked('PSK_FILE_INVALID', key_bytes, c['psk_file']).hex()
    # Validate ownership/TLS before touching remote objects, then only change
    # endpoint lines; retain manual tuning and Includes byte-for-byte.
    prepared = merge_agent(old, c, [detected['address']])
    peers = checked('HOST_REGISTRATION_UNCONFIRMED', register_host, api, c, detected['address'], psk)
    content = merge_agent(prepared, c, peers)
    if old != content:
        temporary = str(target) + '.candidate'
        atomic(temporary, content, 0o640)
        try:
            if subprocess.run(['zabbix_agent2', '-T', '-c', temporary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
                raise SyncError('AGENT_VALIDATION_FAILED')
            if old:
                atomic(str(target) + '.previous', old, 0o640)
            atomic(target, content, mode, owner)
            if subprocess.run(['systemctl', 'try-restart', 'zabbix-agent2'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
                if old:
                    atomic(target, old, mode, owner)
                raise SyncError('AGENT_RESTART_FAILED_CONFIG_RESTORED')
        finally:
            Path(temporary).unlink(missing_ok=True)


def main():
    filename = Path('/etc/openmain-router/zabbix-sync.json')
    if len(sys.argv) > 1 and sys.argv[1] == 'install':
        c = {key: os.environ['SYNC_' + key.upper()] for key in (
            'customer', 'host', 'proxy', 'identity', 'psk_file', 'interface', 'network', 'agent_config')}
        c['role'] = os.environ.get('SYNC_ROLE', 'primary')
        c.update(url=os.environ.get('NB_ZABBIX_API_URL', 'https://zabbix.openmain-it.de/api_jsonrpc.php'),
                 token=os.environ['NB_ZABBIX_API_TOKEN'])
        c['receipt_file'] = os.environ.get('NB_ZABBIX_RECEIPT_FILE', '/var/lib/netbird-kundenrouter/zabbix-registration.json')
        c['access'] = context()
        sync(c)
        filename.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic(filename, json.dumps(c))
    else:
        info = filename.stat()
        if info.st_uid != 0 or info.st_mode & 0o077 or filename.is_symlink():
            raise SyncError('SYNC_CONFIG_UNSAFE')
        sync(json.loads(filename.read_text()))


if __name__ == '__main__':
    try:
        main()
    except RPCError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except SyncError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except Exception:
        print('Zabbix-Abgleich: SYNC_CONTEXT_OR_INTERNAL_ERROR; Details zum Schutz der Secrets unterdrueckt.', file=sys.stderr)
        sys.exit(1)
