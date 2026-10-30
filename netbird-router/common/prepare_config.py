#!/usr/bin/env python3
"""Collect credentials without arguments, shell evaluation or log output."""
import getpass
import os
from pathlib import Path
import sys
from runtime import atomic, config
import json

try:
    args = sys.argv[1:]
    require_setup = '--require-setup-key' in args
    args = [value for value in args if value != '--require-setup-key']
    target = Path(args[0])
    values = config(args[1]) if len(args) > 1 else {}
    for name in ('NB_CUSTOMER_NAME', 'NB_ROLE', 'NB_SETUP_KEY', 'NB_API_TOKEN', 'NB_ZABBIX_API_TOKEN',
                 'NB_MANAGEMENT_URL', 'NB_ZABBIX_API_URL', 'NB_ZABBIX_SERVER', 'NB_LAN_INTERFACE',
                 'NB_VIRTUAL_NETWORK', 'NB_REMOTE_ACCESS_GROUP', 'NB_METRICS_ENABLED', 'NB_ZABBIX_ENABLED', 'NB_HOSTNAME', 'NB_SSH_PUBLIC_KEY'):
        if name in os.environ:
            values[name] = os.environ[name]
    fields = [('NB_CUSTOMER_NAME', 'Kundenname', False), ('NB_ROLE', 'Rolle (primary/backup)', False),
              ('NB_API_TOKEN', 'NetBird API-Token', True)]
    if require_setup:
        fields.append(('NB_SETUP_KEY', 'NetBird Setup-Key', True))
    if values.get('NB_SETUP_KEY') and any(c in values['NB_SETUP_KEY'] for c in '\r\n\x00'):
        raise ValueError('Invalid setup key')
    if values.get('NB_ZABBIX_ENABLED', '1') == '1':
        fields.append(('NB_ZABBIX_API_TOKEN', 'Zabbix API-Token', True))
    for key, label, secret in fields:
        if not values.get(key):
            if not sys.stdin.isatty():
                raise ValueError('Missing noninteractive input')
            values[key] = getpass.getpass(label + ': ') if secret else input(label + ': ')
        if not values[key] or any(c in values[key] for c in '\r\n\x00'):
            raise ValueError('Invalid input')
    if values['NB_ROLE'] not in ('primary', 'backup'):
        raise ValueError('Invalid role')
    atomic(target, json.dumps(values))
except Exception:
    print('Konfiguration unvollstaendig/ungueltig. Rootgeschuetzte JSON-Datei mit --config angeben.', file=sys.stderr)
    sys.exit(1)
