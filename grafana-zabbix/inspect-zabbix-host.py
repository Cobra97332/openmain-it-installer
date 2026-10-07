#!/usr/bin/env python3
import argparse
import json
import os
import urllib.request
from pathlib import Path

ENV_FILE = Path(os.environ.get("OPENMAIN_ZABBIX_ENV_FILE", "/etc/openmain-zabbix-metadata.env"))

def load_env(path):
    env = {}
    if not path.is_file():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env

env = load_env(ENV_FILE)
API_URL = os.environ.get("ZABBIX_API_URL") or env.get("ZABBIX_API_URL")
API_TOKEN = os.environ.get("ZABBIX_ADMIN_API_TOKEN") or env.get("ZABBIX_ADMIN_API_TOKEN")

if not API_URL or not API_TOKEN:
    raise SystemExit("ZABBIX_API_URL oder ZABBIX_ADMIN_API_TOKEN fehlt.")

def api(method, params):
    req = urllib.request.Request(
        API_URL,
        data=json.dumps({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        }).encode("utf-8"),
        headers={
            "Content-Type": "application/json-rpc",
            "Authorization": f"Bearer {API_TOKEN}",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    if "error" in data:
        raise RuntimeError(data["error"])
    return data["result"]

parser = argparse.ArgumentParser(description="Zabbix-Items eines Hosts inventarisieren")
parser.add_argument("host", help="Technischer oder sichtbarer Hostname, z.B. OpnsenseCobranet")
args = parser.parse_args()

hosts = api("host.get", {
    "output": ["hostid", "host", "name"],
    "search": {"host": args.host, "name": args.host},
    "searchByAny": True,
})

if not hosts:
    raise SystemExit(f"Host nicht gefunden: {args.host}")

host = hosts[0]
items = api("item.get", {
    "hostids": [host["hostid"]],
    "output": [
        "itemid", "name", "key_", "value_type", "units",
        "status", "state", "error", "lastvalue", "lastclock"
    ],
    "sortfield": "name",
})

print(f"# Host: {host['host']} ({host['name']})")
print(f"# Host-ID: {host['hostid']}")
print(f"# Items: {len(items)}")
print("#")
print("# status/state: 0 = aktiv/normal")
print("# value_type: 0=float, 1=char, 2=log, 3=uint, 4=text, 5=binary")
print("#")

for item in items:
    name = item.get("name", "")
    key = item.get("key_", "")
    units = item.get("units", "")
    value = item.get("lastvalue", "")
    vtype = item.get("value_type", "")
    status = item.get("status", "")
    state = item.get("state", "")
    error = item.get("error", "")
    print(
        f"{name}\tkey={key}\tunits={units}\ttype={vtype}"
        f"\tstatus={status}\tstate={state}\tlast={value}\terror={error}"
    )
