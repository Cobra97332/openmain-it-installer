#!/usr/bin/env python3
"""Read-only classification report for Zabbix hosts.

Classifications are suggestions, not verified NetBox inventory facts.
Only explicit Zabbix metadata can establish VM vs. device. No API writes.
"""
import ipaddress
import json
import logging
import os
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
TENANT_MAP_PATH = os.getenv("TENANT_MAP_FILE", "/app/tenant-map.json")


# Role hints only: monitoring templates never establish physical vs virtual type.
# Ignore Zabbix health templates that are attached to unrelated Docker/agent hosts.
ROLE_PATTERNS = (
    ("firewall", ("opnsense", "pfsense", "fortigate", "firewall")),
    ("nas", ("synology", "qnap", "truenas", "nas by", "nas snmp")),
    ("hardware-management", ("idrac", "ilo ", "ipmi", "redfish")),
    ("hypervisor", ("proxmox", "esxi", "hyper-v", "xenserver")),
    ("network", ("cisco ios", "mikrotik", "routeros", "switch", "network device")),
    ("vpn", ("netbird",)),
    ("mail", ("mailcow", "postfix", "sogo")),
    ("docker-host", ("docker by", "docker hosts")),
)
ROLE_GROUPS = {
    "zabbix servers": "monitoring",
    "workstation": "workstation",
    "interne it/hypervisor": "hypervisor",
}
IGNORED_ROLE_TEMPLATES = {"zabbix server health", "zabbix proxy health"}
VALID_ROLE_TAGS = {"firewall", "nas", "hypervisor", "vpn", "mail", "monitoring",
                   "network", "hardware-management", "docker-host", "workstation"}


def load_tenant_map():
    """Map exact Zabbix group names to NetBox tenant hints, without any API writes."""
    try:
        with open(TENANT_MAP_PATH, "r", encoding="utf-8") as file:
            mapping = json.load(file)
    except FileNotFoundError:
        return {}
    if not isinstance(mapping, dict):
        raise ValueError("tenant-map.json must be a JSON object")
    result = {}
    for group, tenant in mapping.items():
        if not isinstance(group, str) or not group.strip() or not isinstance(tenant, str) or not tenant.strip():
            raise ValueError("Invalid tenant-map.json: group/tenant names must be nonempty strings")
        result[group.strip().casefold()] = bounded(tenant, 120)
    return result


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


def classify(host, tenant_map=None):
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
    role_evidence = []
    for key in sorted(ROLE_TAG_KEYS):
        explicit = tags.get(key, "").lower()
        if explicit in VALID_ROLE_TAGS:
            role = explicit
            role_evidence.append("role_tag:" + key)
            break
    if role == "unknown":
        for group in groups:
            matched = ROLE_GROUPS.get(group.casefold())
            if matched:
                role = matched
                role_evidence.append("role_group:" + group)
                break
    if role == "unknown":
        # Strong product-specific template/group matches only, not health checks.
        evidence_text = " | ".join(
            template.lower() for template in templates
            if template.casefold() not in IGNORED_ROLE_TEMPLATES
        ) + " | " + " | ".join(
            group.lower() for group in groups
            if group.casefold() not in {"linux servers", "zabbix servers"}
        )
        for candidate, needles in ROLE_PATTERNS:
            if any(needle in evidence_text for needle in needles):
                role = candidate
                role_evidence.append("role_template_or_group:" + candidate)
                break

    tenants = set()
    tenant_evidence = []
    for key in sorted(TENANT_TAG_KEYS):
        if tags.get(key):
            tenants.add(tags[key])
            tenant_evidence.append("tenant_tag:" + key)
    for group in groups:
        match = re.match(r"^(?:customers|kunden|tenants|mandanten)/(.+)$", group, re.IGNORECASE)
        if match:
            tenants.add(bounded(match.group(1).split("/")[0]))
            tenant_evidence.append("tenant_prefixed_group:" + group)
        if tenant_map and group.casefold() in tenant_map:
            tenants.add(tenant_map[group.casefold()])
            tenant_evidence.append("tenant_mapped_group:" + group)
    tenant_hint = next(iter(tenants)) if len(tenants) == 1 else None
    if len(tenants) > 1:
        evidence.append("conflicting_tenant_evidence")

    return {
        "hostid": bounded(host.get("hostid"), 32),
        "name": bounded(host.get("name") or host.get("host")),
        "kind": kind,
        "role_hint": role,
        "role_evidence": role_evidence,
        "tenant_hint": tenant_hint,  # NEVER automatically create a tenant from this
        "tenant_evidence": tenant_evidence,
        "candidate_ips": safe_ips(host),  # NEVER automatically import these into IPAM
        "groups": groups,
        "templates": templates,
        "evidence": evidence,
    }


def report_inventory(hosts):
    summary = Counter()
    groups, templates = set(), set()
    tenant_map = load_tenant_map()
    LOG.info("DISCOVERY START: read-only; no NetBox writes; customer_mappings=%d", len(tenant_map))
    for host in sorted(hosts, key=lambda h: int(h.get("hostid") or 0)):
        record = classify(host, tenant_map)
        summary[record["kind"]] += 1
        groups.update(record["groups"])
        templates.update(record["templates"])
        LOG.info("DISCOVERY %s", json.dumps(record, ensure_ascii=False, sort_keys=True))
    LOG.info("DISCOVERY SUMMARY hosts=%d vm=%d device=%d unknown=%d",
             len(hosts), summary["vm"], summary["device"], summary["unknown"])
    LOG.info("DISCOVERY GROUPS %s", json.dumps(sorted(groups), ensure_ascii=False))
    LOG.info("DISCOVERY TEMPLATES %s", json.dumps(sorted(templates), ensure_ascii=False))
