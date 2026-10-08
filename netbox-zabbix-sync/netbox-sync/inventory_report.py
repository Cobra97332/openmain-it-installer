#!/usr/bin/env python3
"""Read-only classification report for Zabbix hosts.

Classifications are suggestions, not verified NetBox inventory facts.
Only explicit Zabbix metadata can establish VM vs. device. No API writes.
"""
import ipaddress
import json
import logging
import re
from collections import Counter

LOG = logging.getLogger("netbox-zabbix-sync")

VM_WORDS = {"vm", "vms", "virtual machine", "virtual machines", "virtual servers",
            "virtual guests", "vmware guests", "proxmox vms", "virtualisierung/vms"}
PHYSICAL_WORDS = {"physical", "physical servers", "bare metal", "baremetal",
                  "physical devices", "hardware devices", "physical hosts"}
VM_TAGS = {"vm", "virtual", "virtual machine", "guest", "lxc", "container"}
DEVICE_TAGS = {"physical", "baremetal", "bare metal", "hardware", "physical server"}
TYPE_TAG_KEYS = {"asset_type", "host_type", "netbox_type", "netbox.kind", "inventory_type"}
TENANT_TAG_KEYS = {"customer", "kunde", "tenant", "client"}
ROLE_TAG_KEYS = {"role", "device_role", "service"}

# Order from specific to general. Roles are advisory (firewalls/NAS may be VMs).
ROLE_PATTERNS = (
    ("firewall", ("opnsense", "pfsense", "fortigate", "firewall")),
    ("nas", ("synology", "qnap", "truenas", "nas by", "nas snmp")),
    ("hardware-management", ("idrac", "ilo ", "ipmi", "redfish")),
    ("hypervisor", ("proxmox", "esxi", "hyper-v", "xenserver")),
    ("network", ("cisco ios", "mikrotik", "routeros", "switch", "network device")),
    ("vpn", ("netbird",)),
    ("mail", ("mailcow", "postfix", "sogo")),
    ("monitoring", ("zabbix server", "zabbix proxy")),
)


def bounded(value, max_chars=120):
    """Prevent multiline log injection and oversized log entries."""
    return re.sub(r"\s+", " ", str(value or "")).strip()[:max_chars]


def fields(host, key, field):
    source = host.get(key) or []
    return sorted({bounded(item.get(field)) for item in source
                   if isinstance(item, dict) and bounded(item.get(field))})


def safe_ips(host):
    valid = set()
    for item in host.get("interfaces") or []:
        candidate = str(item.get("ip") or "").strip()
        try:
            address = ipaddress.ip_address(candidate)
            if address.is_unspecified or address.is_loopback or address.is_link_local:
                continue
            valid.add(str(address))
        except ValueError:
            continue
    return sorted(valid)


def classify(host):
    groups = fields(host, "hostgroups", "name")
    templates = fields(host, "parentTemplates", "name")
    tags = {str(tag.get("tag") or "").strip().lower(): bounded(tag.get("value"))
            for tag in (host.get("tags") or []) if isinstance(tag, dict)}
    inventory = host.get("inventory") or {}
    if not isinstance(inventory, dict):
        inventory = {}

    evidence = []
    candidates = set()
    for key in sorted(TYPE_TAG_KEYS):
        v = tags.get(key, "").lower()
        if v in VM_TAGS:
            candidates.add("vm")
            evidence.append("tag:" + key + "=vm")
        elif v in DEVICE_TAGS:
            candidates.add("device")
            evidence.append("tag:" + key + "=physical")

    inv_type = bounded(inventory.get("type")).lower()
    if inv_type in VM_TAGS:
        candidates.add("vm")
        evidence.append("inventory:type=virtual")
    elif inv_type in DEVICE_TAGS:
        candidates.add("device")
        evidence.append("inventory:type=physical")

    for name in groups:
        final = re.split(r"[/\\]", name)[-1].strip().lower()
        if final in VM_WORDS:
            candidates.add("vm")
            evidence.append("group:" + name)
        elif final in PHYSICAL_WORDS:
            candidates.add("device")
            evidence.append("group:" + name)

    # VMware Guest specifically identifies a VM; monitoring templates for
    # Linux, OPNsense, Proxmox etc. DO NOT establish virtual/physical type.
    for name in templates:
        if "vmware guest" in name.lower():
            candidates.add("vm")
            evidence.append("template:" + name)

    kind = next(iter(candidates)) if len(candidates) == 1 else "unknown"
    if len(candidates) > 1:
        evidence.append("conflicting_type_evidence")

    role = "unknown"
    for key in ROLE_TAG_KEYS:
        explicit = tags.get(key, "").lower()
        if explicit in {"firewall", "nas", "hypervisor", "vpn", "mail",
                        "monitoring", "network", "hardware-management"}:
            role = explicit
            break
    if role == "unknown":
        haystack = " | ".join(templates + groups).lower()
        for candidate, needles in ROLE_PATTERNS:
            if any(needle in haystack for needle in needles):
                role = candidate
                break

    tenants = set()
    for key in TENANT_TAG_KEYS:
        if tags.get(key):
            tenants.add(tags[key])
    for group in groups:
        match = re.match(r"^(?:customers|kunden|tenants|mandanten)/(.+)$", group, re.IGNORECASE)
        if match:
            tenants.add(bounded(match.group(1).split("/")[0]))
    tenant_hint = next(iter(tenants)) if len(tenants) == 1 else None
    if len(tenants) > 1:
        evidence.append("conflicting_tenant_evidence")

    return {
        "hostid": bounded(host.get("hostid"), 32),
        "name": bounded(host.get("name") or host.get("host")),
        "kind": kind,
        "role_hint": role,
        "tenant_hint": tenant_hint,  # NEVER automatically create a tenant from this
        "candidate_ips": safe_ips(host),  # NEVER automatically import these into IPAM
        "groups": groups,
        "templates": templates,
        "evidence": evidence,
    }


def report_inventory(hosts):
    summary = Counter()
    groups, templates = set(), set()
    LOG.info("DISCOVERY START: read-only; no NetBox writes")
    for host in sorted(hosts, key=lambda h: int(h.get("hostid") or 0)):
        record = classify(host)
        summary[record["kind"]] += 1
        groups.update(record["groups"])
        templates.update(record["templates"])
        LOG.info("DISCOVERY %s", json.dumps(record, ensure_ascii=False, sort_keys=True))
    LOG.info("DISCOVERY SUMMARY hosts=%d vm=%d device=%d unknown=%d",
             len(hosts), summary["vm"], summary["device"], summary["unknown"])
    LOG.info("DISCOVERY GROUPS %s", json.dumps(sorted(groups), ensure_ascii=False))
    LOG.info("DISCOVERY TEMPLATES %s", json.dumps(sorted(templates), ensure_ascii=False))
