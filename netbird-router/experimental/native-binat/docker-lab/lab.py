#!/usr/bin/env python3
"""Disposable real-controller lab. Never uses host profiles, ports or networking."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile

CONTROLLER = ('netbirdio/netbird-server:0.80.0@sha256:'
              '05b3d8d6d056e5062a2965d4d6e5d6d83f09f2bf9133947a8e5972e0e0e7a261')
TOOLS = 'openmain-native-lab-tools:debian13'
CLIENT = 'openmain-native-lab-client:0.80.0-small'
ROOT = Path(__file__).resolve().parents[4]
PATCH = ROOT / 'netbird-router/experimental/native-binat/netbird-v0.80.0-small-native.patch'
ROUTERS = ('primary-a', 'backup-a', 'primary-b', 'backup-b')
PEERS = ('peer', 'probe') + ROUTERS


def docker(*args, data=None, check=True):
    env = dict(os.environ)
    for k in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
        env.pop(k, None)
    r = subprocess.run(['docker', '--host=unix:///var/run/docker.sock', *args],
                       input=data, text=True, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, env=env, timeout=600)
    if check and r.returncode:
        # Never return raw Docker output: logs/build errors can contain secrets/URLs.
        raise RuntimeError('docker_operation_failed:' + args[0])
    return r


def topology(work, project, tools=TOOLS):
    services = {}
    proxies = {k: '' for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
                              'http_proxy', 'https_proxy', 'all_proxy')}
    base = dict(cap_drop=['ALL'], security_opt=['no-new-privileges:true'],
                logging={'driver': 'none'}, restart='no')
    services['controller'] = dict(base, image=CONTROLLER,
        command=['--config', '/etc/netbird/config.yaml'],
        environment=dict(proxies, NB_SETUP_PAT_ENABLED='true', NB_DISABLE_GEOLOCATION='true'),
        networks={'control': {'aliases': ['controller.lab.test']}},
        volumes=[str(work / 'runtime/controller') + ':/etc/netbird:ro'],
        tmpfs=['/var/lib/netbird:mode=0700', '/tmp:mode=1777'])
    # Tools hold ONLY lab credentials. No Docker socket is mounted into containers.
    services['tools'] = dict(base, image=tools, entrypoint=['sleep', 'infinity'],
        environment=proxies, networks=['control'],
        volumes=[str(work / 'runtime') + ':/lab', str(Path(__file__).parent) + ':/opt/lab:ro'])
    for index, name in enumerate(PEERS):
        router = name in ROUTERS
        networks = {'control': {}}
        if router:
            networks['lan-' + name[-1]] = {}
        services[name] = dict(base, image=CLIENT, hostname=name,
            cap_add=['NET_ADMIN', 'NET_RAW', 'MKNOD'],
            device_cgroup_rules=['c 10:200 rwm'],
            environment=dict(proxies, NB_WG_KERNEL_DISABLED='true', OPENMAIN_ISOLATED_TEST='1'),
            entrypoint=['sleep', 'infinity'], networks=networks,
            volumes=[str(work / 'runtime/ca/cert.pem') + ':/lab-ca/cert.pem:ro',
                     str(work / 'runtime/keys' / name) + ':/lab-key:ro',
                     str(Path(__file__).parent) + ':/opt/lab:ro'],
            tmpfs=['/run:mode=0755', '/var/lib/netbird:mode=0700', '/tmp:mode=1777'],
            sysctls={'net.ipv4.ip_forward': '1' if router else '0'})
    for lan in ('a', 'b'):
        services['lan-' + lan] = dict(base, image=tools, hostname='lan-' + lan,
            cap_add=['NET_ADMIN', 'NET_RAW'], environment=proxies,
            entrypoint=['python3', '/opt/lab/container.py', 'lan'],
            networks={'lan-' + lan: {}},
            volumes=[str(Path(__file__).parent) + ':/opt/lab:ro'])
    # Distinct Docker transport subnets avoid overlapping IPAM; identical real
    # customer addresses are assigned INSIDE their separate namespaces.
    networks = {n: {'internal': True, 'enable_ipv6': False,
                   'ipam': {'config': [{'subnet': subnet}]}}
                for n, subnet in zip(('control', 'lan-a', 'lan-b'),
                                     ('172.30.240.0/24', '172.30.241.0/24', '172.30.242.0/24'))}
    return {'name': project, 'services': services, 'networks': networks}


class Lab:
    def __init__(self, work, tools=TOOLS):
        self.work = work
        self.project = 'om-binat-' + work.name.rsplit('-', 1)[-1]
        self.tools = tools
        self.file = work / 'compose.json'

    def compose(self, *args, check=True):
        return docker('compose', '-p', self.project, '-f', str(self.file), *args, check=check)

    def exec(self, name, *args, data=None):
        cid = self.compose('ps', '-q', name).stdout.strip()
        if not cid or '\n' in cid:
            raise RuntimeError('missing_lab_container:' + name)
        r = docker('exec', '-i', cid, *args, data=data, check=False)
        if r.returncode:
            try:
                error = json.loads(r.stdout)['error_class']
                if re.fullmatch(r'lab_[a-z_0-9]+', error):
                    raise RuntimeError(error)
            except (KeyError, ValueError, TypeError):
                pass
            raise RuntimeError('docker_operation_failed:exec')
        return r.stdout

    def prepare(self):
        init = docker('run', '--rm', '--network', 'none', '--cap-drop', 'ALL', '--cap-add', 'DAC_OVERRIDE',
                      '--security-opt', 'no-new-privileges:true',
                      '-v', str(self.work) + ':/lab',
                      '-v', str(Path(__file__).parent) + ':/opt/lab:ro',
                      '--entrypoint', 'python3', self.tools, '/opt/lab/container.py', 'initialize')
        if init.stdout.strip() != 'initialized':
            raise RuntimeError('unexpected_initializer_output')
        self.file.write_text(json.dumps(topology(self.work, self.project, self.tools), indent=2))
        self.file.chmod(0o600)

    def control(self, operation, **kw):
        response = self.exec('tools', 'python3', '/opt/lab/container.py', 'control',
                             data=json.dumps(dict(operation=operation, **kw)))
        return json.loads(response)

    def cleanup(self):
        # Only this random, exclusively allocated project. No prune, host nft or
        # unrelated containers/volumes. Private evidence directory is retained.
        stopped = True
        if self.file.exists():
            try:
                stopped = self.compose('down', '--remove-orphans', check=False).returncode == 0
            except (RuntimeError, OSError, subprocess.TimeoutExpired):
                stopped = False
        # Initialization can fail BEFORE compose.json exists. Credential cleanup
        # is therefore independent of Compose creation and stack teardown.
        try:
            r = docker('run', '--rm', '--network', 'none', '--cap-drop', 'ALL',
                       '--cap-add', 'DAC_OVERRIDE', '--security-opt', 'no-new-privileges:true',
                       '-v', str(self.work) + ':/lab', '--entrypoint', 'python3', self.tools,
                       '-c', 'from pathlib import Path\nimport shutil\np=Path("/lab/runtime")\n'
                             'if p.is_symlink(): raise SystemExit(1)\n'
                             'if p.exists(): shutil.rmtree(p)\n'
                             'if p.exists() or p.is_symlink(): raise SystemExit(1)\n', check=False)
            return stopped and r.returncode == 0
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            return False


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--controller-only', action='store_true', help='bootstrap/API proof, NOT client acceptance')
    p.add_argument('--tools-image', default=TOOLS)
    p.add_argument('--report', type=Path, required=True)
    args = p.parse_args()
    if os.environ.get('OPENMAIN_ISOLATED_TEST') != '1':
        p.error('OPENMAIN_ISOLATED_TEST=1 required')
    args.report = args.report.resolve()
    if args.report.exists():
        p.error('report already exists; refusing overwrite')
    work = Path(tempfile.mkdtemp(prefix='om-binat-lab-', dir=os.environ.get('TMPDIR', '/tmp')))
    os.chmod(work, 0o700)
    lab = Lab(work, args.tools_image)
    report = {'release': 'blocked', 'patch_sha256': hashlib.sha256(PATCH.read_bytes()).hexdigest(),
              'upstream': 'fca64287cf51a85552a41b022013abbfdb335452',
              'controller_image': CONTROLLER, 'real_clients_executed': False, 'cases': {},
              'mode': 'controller_only' if args.controller_only else 'real_clients',
              'controller_ack': False, 'private_work_directory': str(work)}
    status = 1
    try:
        lab.prepare()
        if args.controller_only:
            lab.compose('up', '-d', '--pull', 'never', 'controller', 'tools')
            report['cases'].update(lab.control('bootstrap'))
            report['cases'].update(lab.control('api_contract'))
            status = 0  # explicitly ONLY the controller/API command succeeds
        else:
            image = docker('image', 'inspect', CLIENT, check=False)
            if image.returncode or not image.stdout:
                raise RuntimeError('patched_client_image_missing')
            # Generate individual setup-key files before Docker creates the
            # client bind mounts; each client sees only its own one-off key.
            lab.compose('up', '-d', '--pull', 'never', 'controller', 'tools', 'lan-a', 'lan-b')
            report['cases'].update(lab.control('bootstrap'))
            report['cases'].update(lab.control('enrollment_keys'))
            lab.compose('up', '-d', '--pull', 'never', *PEERS)
            for name in PEERS:
                report['cases'].update(json.loads(lab.exec(name, 'python3', '/opt/lab/container.py',
                                                          'start', name, report['patch_sha256'])))
            report['cases'].update(lab.control('configure'))
            report['real_clients_executed'] = True
            packet_cases = json.loads(run_packets(lab))
            if 'backup_observed_after_seconds' in packet_cases:
                report['observations'] = {'backup_observed_after_seconds': packet_cases.pop('backup_observed_after_seconds')}
            report['cases'].update(packet_cases)
            # No success override: unimplemented required adversarial gates fail.
            status = 0 if all(v == 'pass' for v in report['cases'].values()) else 1
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as e:
        report['error_class'] = type(e).__name__
        # Only our classified messages, never Docker/API response bodies.
        if isinstance(e, RuntimeError) and (re.fullmatch(r'lab_[a-z_0-9]+', str(e)) or str(e).startswith(('docker_operation_failed:', 'missing_lab_container:',
                                                             'patched_client_image_missing', 'unexpected_initializer_output'))):
            report['error_class'] = str(e)
    finally:
        report['cleanup_complete'] = lab.cleanup()
        if not report['cleanup_complete']:
            status = 1
        fd = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(report, f, indent=2)
        print(json.dumps({'report': str(args.report), 'release': 'blocked', 'exit_status': status}))
    return status


def run_packets(lab):
    # Kept in a separate module: orchestration lives outside client namespaces.
    sys.path.insert(0, str(Path(__file__).parent))
    from packets import acceptance
    return json.dumps(acceptance(lab))


if __name__ == '__main__':
    sys.exit(main())
