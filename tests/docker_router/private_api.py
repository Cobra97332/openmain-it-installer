#!/usr/bin/env python3
"""Real Linux socket/route isolation; NetBird status is a synthetic fixture.

No controller or production API is contacted. The two internal Docker networks
have no host ports or external uplink. This complements (not replaces) the real
stock-NetBird gateway test. Only this test code substitutes profile/status.
"""
import json
import os
from pathlib import Path
import secrets
import subprocess

PREFIX = 'om-private-api-' + secrets.token_hex(5)
IMAGE = os.environ.get('PRIVATE_API_TEST_IMAGE', 'openmain-router:test')
ROOT = Path(__file__).resolve().parents[2]
CREATED = []


def docker(*args, data=None, check=True):
    env = dict(os.environ)
    for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
        env.pop(key, None)
    result = subprocess.run(['docker', '--host=unix:///var/run/docker.sock', *args],
                            input=data, text=True, capture_output=True, timeout=90, env=env)
    if check and result.returncode:
        raise RuntimeError('PRIVATE_API_LAB_' + args[0].upper() + '_FAILED')
    return result.stdout


def execute(name, code):
    return docker('exec', '-i', PREFIX + '-' + name, 'python3', '-', data=code)


def main():
    nets = [PREFIX + '-tunnel', PREFIX + '-fallback']
    try:
        for name, subnet in zip(nets, ('100.107.0.0/16', '172.31.250.0/24')):
            docker('network', 'create', '--internal', '--subnet', subnet, name)
            CREATED.append(('network', name))
        for name, address in (('server', '100.107.91.6'), ('router', '100.107.82.65')):
            container = PREFIX + '-' + name
            CREATED.append(('container', container))
            docker('run', '-d', '--name', container, '--network', nets[0], '--ip', address,
                   '--cap-drop', 'ALL', '--cap-add', 'NET_ADMIN', '--cap-add', 'NET_RAW',
                   '--cap-add', 'DAC_OVERRIDE',  # read-only source may be owner-only in cloud
                   '--security-opt', 'no-new-privileges:true', '--log-driver', 'none',
                   '-v', str(ROOT) + ':/src:ro', '--entrypoint', 'sleep', IMAGE, 'infinity')
        docker('network', 'connect', nets[1], PREFIX + '-router')
        docker('exec', PREFIX + '-router', 'ip', 'link', 'set', 'eth0', 'name', 'wt0')
        server = '''import http.server,json,threading,time
class Handler(http.server.BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_POST(self):
  payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  version=payload['method']=='apiinfo.version'
  assert self.headers.get('Authorization')==(None if version else 'Bearer synthetic-lab-token')
  body=json.dumps(dict(jsonrpc='2.0',id=1,result='7.4.15' if version else [])).encode()
  self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
http.server.ThreadingHTTPServer(('0.0.0.0',8083),Handler).serve_forever()
'''
        # Detached process, no printed headers, logs or persistent credentials.
        docker('exec', '-d', PREFIX + '-server', 'python3', '-c', server)
        result = execute('router', '''import json,os,sys,time
from unittest.mock import patch
sys.path.insert(0,'/src/netbird-router/common')
import zabbix_access as a
from runtime import Invalid
c=dict(api_url=a.PRIVATE_API,server='100.107.91.6',management='https://controller.invalid')
data=json.dumps(dict(jsonrpc='2.0',id=1,method='host.get',params={})).encode()
# Poison all proxy settings: neither probe nor bearer request may use them.
for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
 os.environ[key]='http://192.0.2.123:9999'
with patch.object(a,'active_identity',return_value=('100.107.82.65','wt0')):
 for attempt in range(30):
  try:
   result=a.private_request(a.PRIVATE_API,data,'synthetic-lab-token',c);break
  except Invalid:
   if attempt==29: raise
   time.sleep(.1)
 assert result['result']==[]
 with patch.object(a,'context',return_value=c):
  a.private_probe(a.PRIVATE_API,{('100.107.91.6',8083)})
 # Route changes after connect, before header transmission must fail closed.
 actual=a.route
 def changed(ip):
  changed.count+=1
  if changed.count==2:
   import subprocess
   subprocess.run(['ip','route','replace',ip+'/32','dev','eth1'],check=True)
  return actual(ip)
 changed.count=0
 with patch.object(a,'route',side_effect=changed):
  try: a.private_request(a.PRIVATE_API,data,'synthetic-lab-token',c)
  except Invalid: pass
  else: raise AssertionError('changed_route_accepted')
 # A still-connected status cannot authorize an outside-tunnel route.
 try: a.private_request(a.PRIVATE_API,data,'synthetic-lab-token',c)
 except Invalid: pass
 else: raise AssertionError('outside_tunnel_route_accepted')
print(json.dumps(dict(bound_http_socket=True,proxy_bypass=True,version_without_token=True,
 route_change_before_token_blocked=True,outside_tunnel_blocked=True,
 netbird_status='SYNTHETIC_FIXTURE_NOT_A_CLIENT_TEST')))
''')
        print(result.strip())
    finally:
        for kind, name in reversed(CREATED):
            docker(kind, 'rm', *(['-f'] if kind == 'container' else []), name, check=False)


if __name__ == '__main__':
    main()
