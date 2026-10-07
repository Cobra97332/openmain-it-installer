#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENV_FILE = Path(os.environ.get("OPENMAIN_ZABBIX_ENV_FILE", "/etc/openmain-zabbix-metadata.env"))

def load_env_file(path):
    """Load KEY=VALUE pairs without shell evaluation.

    Existing process environment variables take precedence.
    This intentionally supports regex values containing characters such as
    parentheses and pipes, which are awkward to source from a shell.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)

load_env_file(ENV_FILE)

API_URL = os.environ.get("ZABBIX_API_URL", "https://zabbix.openmain-it.de/api_jsonrpc.php")
API_TOKEN = os.environ.get("ZABBIX_ADMIN_API_TOKEN") or os.environ.get("NB_ZABBIX_API_TOKEN", "")
CRITICAL_PLATFORMS = {
    x.strip().lower()
    for x in os.environ.get(
        "OPENMAIN_CRITICAL_PLATFORMS",
        "opnsense,pve,idrac,nas,qnap,synology,netbird"
    ).split(",")
    if x.strip()
}
CRITICAL_HOST_REGEX = os.environ.get("OPENMAIN_CRITICAL_HOST_REGEX", "").strip()

GROUPS = {
    "opnsense": "OpenMain/OPNsense",
    "pve": "OpenMain/PVE",
    "idrac": "OpenMain/iDRAC",
    "nas": "OpenMain/NAS",
    "qnap": "OpenMain/QNAP",
    "synology": "OpenMain/Synology",
    "netbird": "OpenMain/NetBird",
}
CRITICAL_GROUP = "OpenMain/Critical"

def api(method, params):
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": 1,
    }).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json-rpc",
            "Authorization": f"Bearer {API_TOKEN}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Zabbix API nicht erreichbar: {exc}") from exc
    if "error" in data:
        e = data["error"]
        raise RuntimeError(f"{method}: {e.get('message')}: {e.get('data', '')}")
    return data["result"]

def normalize(text):
    return re.sub(r"\s+", " ", text or "").strip().lower()

def classify(host):
    hostname = normalize(host.get("host"))
    visible = normalize(host.get("name"))
    templates = " | ".join(
        normalize(t.get("name") or t.get("host"))
        for t in host.get("parentTemplates", [])
    )
    haystack = " | ".join([hostname, visible, templates])

    platforms = set()
    if re.search(r"\bopnsense\b", haystack):
        platforms.add("opnsense")
    if re.search(r"\bnetbird\b", haystack):
        platforms.add("netbird")
    if re.search(r"\bproxmox\b|\bpve\b", templates):
        platforms.add("pve")
    if re.search(r"\bidrac\b|dell poweredge", templates):
        platforms.add("idrac")
    if re.search(r"\bqnap\b", haystack):
        platforms.update({"qnap", "nas"})
    if re.search(r"\bsynology\b|\bdiskstation\b", haystack):
        platforms.update({"synology", "nas"})

    if not platforms.intersection({"pve", "idrac", "opnsense", "qnap", "synology", "netbird"}):
        if re.search(r"\bnas\b", haystack):
            platforms.add("nas")

    return platforms

def get_or_create_group(name, dry_run=False):
    result = api("hostgroup.get", {
        "output": ["groupid", "name"],
        "filter": {"name": [name]},
    })
    if result:
        return result[0]["groupid"]
    if dry_run:
        return f"DRYRUN:{name}"
    created = api("hostgroup.create", {"name": name})
    return created["groupids"][0]

def existing_tag_map(tags):
    return {(t.get("tag", ""), t.get("value", "")) for t in tags}

def main():
    parser = argparse.ArgumentParser(description="OpenMain Zabbix Hostgruppen automatisch synchronisieren.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if not API_TOKEN:
        print("ZABBIX_ADMIN_API_TOKEN fehlt.", file=sys.stderr)
        return 2

    hosts = api("host.get", {
        "output": ["hostid", "host", "name", "status"],
        "selectParentTemplates": ["templateid", "name", "host"],
        "selectHostGroups": ["groupid", "name"],
        "selectTags": ["tag", "value"],
        "filter": {"status": 0},
    })

    all_needed = set(GROUPS.values()) | {CRITICAL_GROUP}
    group_ids = {name: get_or_create_group(name, args.dry_run) for name in sorted(all_needed)}

    changed = 0
    classified = 0

    for host in hosts:
        platforms = classify(host)
        current_tagset = existing_tag_map(host.get("tags", []))
        critical_by_tag = ("openmain.critical", "true") in current_tagset
        critical_by_regex = False
        if CRITICAL_HOST_REGEX:
            try:
                critical_by_regex = bool(
                    re.search(
                        CRITICAL_HOST_REGEX,
                        f"{host.get('host', '')} {host.get('name', '')}",
                    )
                )
            except re.error as exc:
                raise RuntimeError(f"OPENMAIN_CRITICAL_HOST_REGEX ist ungültig: {exc}") from exc

        is_critical = bool(platforms & CRITICAL_PLATFORMS) or critical_by_tag or critical_by_regex
        if not platforms and not is_critical:
            continue
        classified += 1

        current_groups = {g["groupid"]: g["name"] for g in host.get("hostgroups", [])}
        desired_group_names = {GROUPS[p] for p in platforms if p in GROUPS}
        if is_critical:
            desired_group_names.add(CRITICAL_GROUP)

        desired_group_ids = set(current_groups.keys())
        for name in desired_group_names:
            gid = group_ids[name]
            if not gid.startswith("DRYRUN:"):
                desired_group_ids.add(gid)

        tags = [{"tag": t.get("tag", ""), "value": t.get("value", "")} for t in host.get("tags", [])]
        tagset = existing_tag_map(tags)
        for platform in sorted(platforms):
            pair = ("openmain.platform", platform)
            if pair not in tagset:
                tags.append({"tag": pair[0], "value": pair[1]})
                tagset.add(pair)
        if is_critical:
            pair = ("openmain.critical", "true")
            if pair not in tagset:
                tags.append({"tag": pair[0], "value": pair[1]})

        current_group_names = set(current_groups.values())
        group_change = not desired_group_names.issubset(current_group_names)
        current_tags = existing_tag_map(host.get("tags", []))
        tag_change = existing_tag_map(tags) != current_tags

        if not group_change and not tag_change:
            if args.verbose:
                print(f"OK       {host['name']}: {', '.join(sorted(platforms))}")
            continue

        changed += 1
        labels = sorted(platforms) or ["critical"]
        print(f"UPDATE   {host['name']}: {', '.join(labels)}")
        if args.dry_run:
            continue

        api("host.update", {
            "hostid": host["hostid"],
            "groups": [{"groupid": gid} for gid in sorted(desired_group_ids, key=int)],
            "tags": tags,
        })

    print(f"Fertig: {classified} klassifiziert, {changed} geändert, {len(hosts)} aktive Hosts geprüft.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
