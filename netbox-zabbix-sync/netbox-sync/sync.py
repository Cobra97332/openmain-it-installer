#!/usr/bin/env python3
"""Conservative Zabbix -> NetBox host importer. Only standard-library dependencies."""
import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("netbox-zabbix-sync")
ZABBIX_URL = os.environ["ZABBIX_URL"].rstrip("/")
ZABBIX_TOKEN = os.environ["ZABBIX_TOKEN"]
NETBOX_URL = os.getenv("NETBOX_URL", "http://127.0.0.1:8080").rstrip("/")
NETBOX_TOKEN = os.environ["NETBOX_TOKEN"]
SITE_SLUG = os.getenv("NETBOX_SITE_SLUG", "zabbix-import")
SITE_NAME = os.getenv("NETBOX_SITE_NAME", "Zabbix Import (Unclassified)")
INTERVAL = max(60, int(os.getenv("SYNC_INTERVAL_SECONDS", "900")))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
VERIFY_TLS = os.getenv("VERIFY_TLS", "true").lower() == "true"

def call(url, payload=None, token=None, auth="Bearer"):
    headers = {"Accept": "application/json", "User-Agent": "netbox-zabbix-sync/1.1"}
    if token:
        headers["Authorization"] = f"{auth} {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode() if payload is not None else None, headers=headers
    )
    ctx = None if VERIFY_TLS else ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(request, timeout=25, context=ctx) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read(400).decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {exc.code} {url}: {detail}") from exc

def zabbix_hosts():
    response = call(f"{ZABBIX_URL}/api_jsonrpc.php", {
        "jsonrpc": "2.0", "method": "host.get",
        "params": {
            "output": ["hostid", "host", "name", "status", "description"],
            "selectInterfaces": ["ip", "dns", "useip", "main"],
            "selectHostGroups": ["name"],
        }, "id": 1,
    }, ZABBIX_TOKEN, "Bearer")
    if "error" in response:
        raise RuntimeError(f"Zabbix API error: {response['error']}")
    return response["result"]

def api(path, payload=None):
    return call(f"{NETBOX_URL}/api/{path.lstrip('/')}", payload, NETBOX_TOKEN, "Token")

def find_one(path, **params):
    response = api(f"{path}?{urllib.parse.urlencode(params)}")
    matches = response.get("results", [])
    if len(matches) > 1:
        raise RuntimeError(f"Ambiguous lookup at {path}: {params}")
    return matches[0] if matches else None

def ensure(path, filters, payload):
    item = find_one(path, **filters)
    if item:
        return item["id"]
    if DRY_RUN:
        LOG.info("DRY RUN would create %s: %s", path, payload)
        return None
    item = api(path, payload)
    LOG.info("Created %s id=%s", path, item["id"])
    return item["id"]

def run():
    hosts = zabbix_hosts()
    LOG.info("Read %d Zabbix hosts (dry_run=%s)", len(hosts), DRY_RUN)
    if not hosts:
        return
    site_id = ensure("dcim/sites/", {"slug": SITE_SLUG},
                     {"name": SITE_NAME, "slug": SITE_SLUG, "status": "active"})
    manuf_id = ensure("dcim/manufacturers/", {"slug": "zabbix-import"},
                      {"name": "Zabbix import (unclassified)", "slug": "zabbix-import"})
    type_id = ensure("dcim/device-types/", {"slug": "zabbix-unclassified"},
                     {"manufacturer": manuf_id, "model": "Unclassified Zabbix Host", "slug": "zabbix-unclassified"}) if manuf_id else None
    role_id = ensure("dcim/device-roles/", {"slug": "zabbix-import"},
                     {"name": "Zabbix import (unclassified)", "slug": "zabbix-import", "color": "9e9e9e"})
    tag_id = ensure("extras/tags/", {"slug": "zabbix-managed-import"},
                    {"name": "Zabbix managed import", "slug": "zabbix-managed-import"})
    for host in hosts:
        hostid = str(host["hostid"])
        name = f"zbx-{hostid}"
        display = host.get("name") or host.get("host") or name
        addresses = sorted(set(
            i.get("ip") or i.get("dns") for i in host.get("interfaces", [])
            if i.get("ip") or i.get("dns")
        ))
        groups = ", ".join(g["name"] for g in host.get("hostgroups", []))[:500]
        description = (
            f"Imported from Zabbix; host={display[:100]}; "
            f"addresses={', '.join(addresses)[:300]}; groups={groups}"
        )[:500]
        existing = find_one("dcim/devices/", name=name)
        if existing:
            tags = {t.get("slug") for t in existing.get("tags", [])}
            if "zabbix-managed-import" not in tags:
                LOG.warning("Skipping name collision without sync tag: %s", name)
            else:
                LOG.info("Already imported: %s", name)
            continue
        if DRY_RUN:
            LOG.info("DRY RUN would create device=%s source=%s address=%s", name, display, ",".join(addresses))
            continue
        if any(v is None for v in (site_id, type_id, role_id, tag_id)):
            raise RuntimeError("Required NetBox reference object is missing")
        device = api("dcim/devices/", {
            "name": name, "site": site_id, "device_type": type_id,
            "role": role_id, "status": "planned", "description": description,
            "tags": [tag_id],
            "comments": f"Zabbix source hostid: {hostid}. Unclassified placeholder, not verified hardware.",
        })
        LOG.info("Created placeholder device %s id=%s", name, device["id"])

if __name__ == "__main__":
    LOG.info("Starting (dry_run=%s, interval=%ds)", DRY_RUN, INTERVAL)
    while True:
        try:
            run()
        except Exception:
            LOG.exception("Synchronization iteration failed")
        time.sleep(INTERVAL)
