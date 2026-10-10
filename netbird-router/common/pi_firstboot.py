#!/usr/bin/env python3
import json
from pathlib import Path
import subprocess
import sys
import urllib.request
from runtime import Invalid, atomic, config, lan


def fingerprint(routes, addresses, neighbors, interface=''):
    detected = lan(routes, addresses, interface)
    gateways = {r['gateway'] for r in routes if r.get('dst') == 'default' and r.get('dev') == detected['interface'] and r.get('gateway')}
    if len(gateways) != 1:
        raise Invalid('Gateway not unique')
    gateway = gateways.pop()
    macs = {n.get('lladdr') for n in neighbors if n.get('dst') == gateway and n.get('dev') == detected['interface'] and n.get('lladdr')}
    if len(macs) != 1:
        raise Invalid('Gateway MAC not yet known')
    return {'network': detected['network'], 'gateway': gateway, 'mac': macs.pop()}


def moved(office, current):
    return office != current


def main():
    target = Path('/etc/openmain-router/office.json')
    c = config('/etc/openmain-router/pi.json')
    def ip(*args):
        return json.loads(subprocess.check_output(['ip', '-j', '-4', *args], stderr=subprocess.DEVNULL))
    routes, addresses = ip('route', 'show', 'table', 'main'), ip('addr', 'show')
    # A bounded gateway probe populates ARP, not a management/production API call.
    for route in routes:
        if route.get('dst') == 'default' and route.get('gateway'):
            subprocess.run(['ping', '-c', '1', '-W', '1', route['gateway']], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    current = fingerprint(routes, addresses, ip('neigh', 'show'), c.get('NB_LAN_INTERFACE', ''))
    if sys.argv[1] == 'prepare':
        if target.exists():
            print('Vorbereitungsnetz beibehalten; erneute Vorbereitung aendert keine Standortkennung.')
        else:
            atomic(target, json.dumps(current))
        return
    if not moved(json.loads(target.read_text()), current):
        print('Warte auf Kundenstandort (Vorbereitungsnetz erkannt).')
        return
    url = c.get('NB_MANAGEMENT_URL', 'https://netbird.openmain-it.de')
    if not url.startswith('https://'):
        raise Invalid('HTTPS required')
    # No credentials in this readiness request. Enrollment happens in the core.
    with urllib.request.urlopen(url, timeout=10):
        pass
    subprocess.run(['bash', '/opt/openmain-router/router-install.sh', '--config', '/etc/openmain-router/pi.json'], check=True)
    atomic('/var/lib/openmain-pi-deployed', 'complete\n')
    Path('/etc/openmain-router/pi.json').unlink()
    subprocess.run(['systemctl', 'disable', '--now', 'openmain-pi-firstboot.timer'], check=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Kunden-Rollout noch nicht abgeschlossen; sicherer Retry folgt. Netzwerk/Status pruefen.', file=sys.stderr)
        sys.exit(1)
