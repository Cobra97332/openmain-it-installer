#!/usr/bin/env python3
import argparse
import ipaddress
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_MANAGEMENT_URL = "https://netbird.openmain-it.de"
DEFAULT_METRICS_GROUP = "NetBird-Metrics"
DEFAULT_MONITORING_GROUP = "Monitoring"
DEFAULT_POLICY_NAME = "OpenMain Prometheus -> NetBird Client Metrics"
DEFAULT_TARGET_FILE = "/opt/openmain-netbird-prometheus/targets/netbird-clients.json"
DEFAULT_CUSTOMER = "intern"
DEFAULT_CUSTOMER_ROOT_GROUP = "Kunden"
DEFAULT_UNASSIGNED_CUSTOMER = "Unzugeordnet"
DEFAULT_CUSTOMER_MARKER_PREFIX = "Kunde:"
DEFAULT_IGNORE_GROUPS = {
    "All",
    "Kunden",
    "Monitoring",
    "NetBird-Metrics",
    "Admins",
    "Administrators",
    "Servers",
    "Clients",
    "Windows",
    "Linux",
    "PVE",
    "PBS",
    "Routers",
    "Gateways",
}


def log(message):
    print(f"[+] {message}")


def warn(message):
    print(f"[!] {message}", file=sys.stderr)


def normalize_url(url):
    return url.rstrip("/")


def api_request(base_url, token, method, path, payload=None):
    url = f"{normalize_url(base_url)}/api{path}"
    data = None
    headers = {
        "Authorization": f"Token {token}",
        "Accept": "application/json",
        "User-Agent": "OpenMain-NetBird-Prometheus-Sync/1.0",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            body = response.read()
            if not body:
                return None
            return json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{method} {path} fehlgeschlagen: {exc}") from exc


def group_id(group):
    return str(group.get("id") or "")


def ensure_group(base_url, token, groups, name):
    for group in groups:
        if group.get("name") == name:
            return group_id(group), groups

    log(f"Erstelle NetBird-Gruppe: {name}")
    created = api_request(
        base_url,
        token,
        "POST",
        "/groups",
        {"name": name, "peers": []},
    )
    gid = group_id(created or {})
    if not gid:
        raise RuntimeError(f"Gruppe '{name}' wurde erstellt, aber ohne ID zurückgegeben.")
    groups = list(groups) + [created]
    return gid, groups


def peer_ids_from_group(group):
    ids = []
    for peer in group.get("peers") or []:
        if isinstance(peer, dict):
            peer_id = peer.get("id")
        else:
            peer_id = peer
        if peer_id:
            ids.append(str(peer_id))
    return ids


def ensure_peer_in_group(base_url, token, group_id_value, peer_id, group_name):
    group = api_request(base_url, token, "GET", f"/groups/{group_id_value}")
    current = peer_ids_from_group(group or {})
    if peer_id in current:
        return False

    updated = sorted(set(current + [peer_id]))
    payload = {
        "name": (group or {}).get("name") or group_name,
        "peers": updated,
    }
    resources = (group or {}).get("resources")
    if resources:
        payload["resources"] = resources

    api_request(base_url, token, "PUT", f"/groups/{group_id_value}", payload)
    log(f"Peer {peer_id} zu Gruppe '{group_name}' hinzugefügt.")
    return True


def ids_from_rule_side(value):
    result = set()
    for item in value or []:
        if isinstance(item, dict):
            item_id = item.get("id")
        else:
            item_id = item
        if item_id:
            result.add(str(item_id))
    return result


def policy_is_correct(policy, monitoring_group_id, metrics_group_id, ports):
    if not policy.get("enabled"):
        return False

    rules = policy.get("rules") or []
    if len(rules) != 1:
        return False

    rule = rules[0]
    expected_ports = {str(port) for port in ports}
    rule_ports = {str(port) for port in (rule.get("ports") or [])}
    return (
        rule.get("enabled") is True
        and rule.get("action") == "accept"
        and rule.get("protocol") == "tcp"
        and rule.get("bidirectional") is False
        and rule_ports == expected_ports
        and ids_from_rule_side(rule.get("sources")) == {monitoring_group_id}
        and ids_from_rule_side(rule.get("destinations")) == {metrics_group_id}
    )


def ensure_policy(
    base_url,
    token,
    policies,
    policy_name,
    monitoring_group_id,
    metrics_group_id,
    ports,
):
    existing = next((p for p in policies if p.get("name") == policy_name), None)

    ports = [int(port) for port in ports]
    port_text = ",".join(str(port) for port in ports)

    payload = {
        "name": policy_name,
        "description": (
            "Automatisch verwaltete OpenMain-Policy: nur Prometheus/Monitoring "
            f"darf NetBird Client Metrics über TCP/{port_text} abfragen."
        ),
        "enabled": True,
        "rules": [
            {
                "name": f"Prometheus TCP {port_text}",
                "description": "Prometheus Zugriff auf NetBird Client Metrics und Peer-Details",
                "enabled": True,
                "action": "accept",
                "bidirectional": False,
                "protocol": "tcp",
                "ports": [str(port) for port in ports],
                "sources": [monitoring_group_id],
                "destinations": [metrics_group_id],
            }
        ],
        "source_posture_checks": [],
    }

    if existing and policy_is_correct(
        existing, monitoring_group_id, metrics_group_id, ports
    ):
        return False

    if existing:
        policy_id = str(existing.get("id") or "")
        if not policy_id:
            raise RuntimeError(f"Policy '{policy_name}' hat keine ID.")
        api_request(base_url, token, "PUT", f"/policies/{policy_id}", payload)
        log(f"NetBird-Policy aktualisiert: {policy_name}")
    else:
        api_request(base_url, token, "POST", "/policies", payload)
        log(f"NetBird-Policy erstellt: {policy_name}")
    return True


def valid_ipv4(value):
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return ip.version == 4 and not ip.is_unspecified


def peer_group_names(peer):
    result = []
    for group in peer.get("groups") or []:
        if isinstance(group, dict):
            name = group.get("name")
        else:
            name = None
        if name:
            result.append(str(name))
    return result


def infer_customer(
    peer,
    metrics_group,
    monitoring_group,
    fallback,
    ignore_groups,
    customer_root_group=DEFAULT_CUSTOMER_ROOT_GROUP,
    unassigned_customer=DEFAULT_UNASSIGNED_CUSTOMER,
    customer_marker_prefix=DEFAULT_CUSTOMER_MARKER_PREFIX,
):
    """Resolve customer only from explicit customer marker groups.

    Operational/technical NetBird groups are intentionally ignored. This
    prevents groups such as Monitoring-NetbirdClient, Zabbix-Kunden-Proxies,
    Server, Daheim or Admins from ever being mistaken for a customer.
    """
    names = peer_group_names(peer)
    prefixes = [customer_marker_prefix.casefold(), "customer:"]

    markers = []
    for name in names:
        lowered = name.casefold()
        for prefix in prefixes:
            if lowered.startswith(prefix):
                customer = name[len(prefix):].strip()
                if customer:
                    markers.append(customer)
                break

    unique_markers = sorted(set(markers), key=str.casefold)
    if len(unique_markers) == 1:
        return unique_markers[0]

    if len(unique_markers) > 1:
        warn(
            "Mehrere Kundenzuordnungen für Peer "
            f"{peer.get('hostname') or peer.get('name') or peer.get('id')}: "
            + ", ".join(unique_markers)
        )
        return unassigned_customer

    if any(name.casefold() == customer_root_group.casefold() for name in names):
        return unassigned_customer

    return fallback

def make_auto_targets(
    peers,
    metrics_group,
    monitoring_group,
    port,
    customer_fallback,
    ignore_groups,
    customer_root_group=DEFAULT_CUSTOMER_ROOT_GROUP,
    unassigned_customer=DEFAULT_UNASSIGNED_CUSTOMER,
    customer_marker_prefix=DEFAULT_CUSTOMER_MARKER_PREFIX,
):
    result = []
    for peer in peers:
        groups = peer_group_names(peer)
        if metrics_group not in groups:
            continue

        ip = str(peer.get("ip") or "").strip()
        if not valid_ipv4(ip):
            warn(f"Überspringe Peer ohne gültige IPv4: {peer.get('name') or peer.get('id')}")
            continue

        host = (
            str(peer.get("hostname") or "").strip()
            or str(peer.get("name") or "").strip()
            or str(peer.get("dns_label") or "").strip()
            or ip
        )
        customer = infer_customer(
            peer,
            metrics_group,
            monitoring_group,
            customer_fallback,
            ignore_groups,
            customer_root_group=customer_root_group,
            unassigned_customer=unassigned_customer,
            customer_marker_prefix=customer_marker_prefix,
        )

        labels = {
            "host": host,
            "customer": customer,
            "managed_by": "netbird-api",
        }
        peer_id = str(peer.get("id") or "").strip()
        if peer_id:
            labels["peer_id"] = peer_id

        result.append(
            {
                "targets": [f"{ip}:{port}"],
                "labels": labels,
            }
        )

    result.sort(
        key=lambda item: (
            item["labels"].get("customer", "").casefold(),
            item["labels"].get("host", "").casefold(),
            item["targets"][0],
        )
    )
    return result


def load_existing_targets(path):
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Ungültige Target-Datei {path}: {exc}") from exc

    if not isinstance(data, list):
        raise RuntimeError(f"{path} muss eine JSON-Liste enthalten.")
    return data


def merge_targets(existing, automatic):
    auto_targets = {
        target
        for item in automatic
        for target in (item.get("targets") or [])
        if isinstance(target, str)
    }
    auto_hosts = {
        str((item.get("labels") or {}).get("host") or "").casefold()
        for item in automatic
        if (item.get("labels") or {}).get("host")
    }

    manual = []
    for item in existing:
        labels = item.get("labels") or {}
        if labels.get("managed_by") == "netbird-api":
            continue

        targets = {
            target
            for target in (item.get("targets") or [])
            if isinstance(target, str)
        }
        host = str(labels.get("host") or "").casefold()

        # Sobald ein bisher manueller Eintrag eindeutig durch NetBird API
        # entdeckt wird, wird er in einen automatisch verwalteten Eintrag
        # überführt. Das verhindert veraltete Targets nach IP-Wechseln.
        if targets & auto_targets:
            continue
        if host and host in auto_hosts:
            continue

        manual.append(item)

    combined = manual + automatic
    combined.sort(
        key=lambda item: (
            (item.get("labels") or {}).get("customer", "").casefold(),
            (item.get("labels") or {}).get("host", "").casefold(),
            (item.get("targets") or [""])[0],
        )
    )
    return combined


def atomic_write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    if path.exists() and path.read_text(encoding="utf-8") == rendered:
        return False

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
    return True


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Synchronisiert NetBird Peers aus der Gruppe NetBird-Metrics "
            "automatisch in Prometheus file_sd und verwaltet die sichere "
            "Monitoring-Policy."
        )
    )
    parser.add_argument(
        "--management-url",
        default=os.getenv("NETBIRD_MANAGEMENT_URL", DEFAULT_MANAGEMENT_URL),
    )
    parser.add_argument("--api-token", default=os.getenv("NETBIRD_API_TOKEN", ""))
    parser.add_argument(
        "--metrics-group",
        default=os.getenv("NETBIRD_METRICS_GROUP", DEFAULT_METRICS_GROUP),
    )
    parser.add_argument(
        "--monitoring-group",
        default=os.getenv("NETBIRD_MONITORING_GROUP", DEFAULT_MONITORING_GROUP),
    )
    parser.add_argument(
        "--policy-name",
        default=os.getenv("NETBIRD_METRICS_POLICY_NAME", DEFAULT_POLICY_NAME),
    )
    parser.add_argument(
        "--prometheus-peer-ip",
        default=os.getenv("NETBIRD_PROMETHEUS_PEER_IP", ""),
    )
    parser.add_argument(
        "--target-file",
        default=os.getenv("NETBIRD_CLIENT_TARGET_FILE", DEFAULT_TARGET_FILE),
    )
    parser.add_argument(
        "--customer-fallback",
        default=os.getenv("NETBIRD_CUSTOMER_FALLBACK", DEFAULT_CUSTOMER),
    )
    parser.add_argument(
        "--customer-root-group",
        default=os.getenv("NETBIRD_CUSTOMER_ROOT_GROUP", DEFAULT_CUSTOMER_ROOT_GROUP),
    )
    parser.add_argument(
        "--unassigned-customer",
        default=os.getenv("NETBIRD_UNASSIGNED_CUSTOMER", DEFAULT_UNASSIGNED_CUSTOMER),
    )
    parser.add_argument(
        "--customer-marker-prefix",
        default=os.getenv(
            "NETBIRD_CUSTOMER_MARKER_PREFIX", DEFAULT_CUSTOMER_MARKER_PREFIX
        ),
    )
    parser.add_argument(
        "--ignore-groups",
        default=os.getenv("NETBIRD_CUSTOMER_IGNORE_GROUPS", ""),
        help="Zusätzliche technische Gruppennamen, Komma-separiert",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("NETBIRD_CLIENT_METRICS_PORT", "9191")),
    )
    parser.add_argument(
        "--detail-port",
        type=int,
        default=int(os.getenv("NETBIRD_CLIENT_DETAIL_PORT", "9192")),
    )
    parser.add_argument(
        "--no-policy",
        action="store_true",
        default=os.getenv("NETBIRD_AUTO_POLICY", "1") in {"0", "false", "False"},
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.api_token:
        raise SystemExit("NETBIRD_API_TOKEN fehlt.")

    if args.port < 1 or args.port > 65535:
        raise SystemExit(f"Ungültiger Port: {args.port}")
    if args.detail_port < 1 or args.detail_port > 65535:
        raise SystemExit(f"Ungültiger Detail-Port: {args.detail_port}")
    if args.detail_port == args.port:
        raise SystemExit("Metrics- und Detail-Port müssen verschieden sein.")

    target_path = Path(args.target_file)
    ignore_groups = set(DEFAULT_IGNORE_GROUPS)
    ignore_groups.update(
        item.strip() for item in args.ignore_groups.split(",") if item.strip()
    )

    groups = api_request(args.management_url, args.api_token, "GET", "/groups")
    if not isinstance(groups, list):
        raise RuntimeError("NetBird /groups lieferte keine Liste.")

    metrics_gid, groups = ensure_group(
        args.management_url, args.api_token, groups, args.metrics_group
    )
    monitoring_gid, groups = ensure_group(
        args.management_url, args.api_token, groups, args.monitoring_group
    )

    peers = api_request(args.management_url, args.api_token, "GET", "/peers")
    if not isinstance(peers, list):
        raise RuntimeError("NetBird /peers lieferte keine Liste.")

    existing = load_existing_targets(target_path)

    # Bereits manuell eingetragene Prometheus-Targets automatisch in die
    # NetBird-Metrics-Gruppe übernehmen. Damit werden bestehende Installationen
    # ohne erneute Handarbeit auf die API-basierte Verwaltung migriert.
    peer_by_ip = {
        str(peer.get("ip") or ""): peer
        for peer in peers
        if str(peer.get("ip") or "")
    }
    for item in existing:
        if (item.get("labels") or {}).get("managed_by") == "netbird-api":
            continue
        for target in item.get("targets") or []:
            if not isinstance(target, str):
                continue
            target_ip = target.rsplit(":", 1)[0]
            peer = peer_by_ip.get(target_ip)
            if not peer:
                continue
            ensure_peer_in_group(
                args.management_url,
                args.api_token,
                metrics_gid,
                str(peer.get("id")),
                args.metrics_group,
            )

    if args.prometheus_peer_ip:
        prometheus_peer = next(
            (peer for peer in peers if str(peer.get("ip") or "") == args.prometheus_peer_ip),
            None,
        )
        if prometheus_peer:
            ensure_peer_in_group(
                args.management_url,
                args.api_token,
                monitoring_gid,
                str(prometheus_peer.get("id")),
                args.monitoring_group,
            )
        else:
            warn(
                "Prometheus-Peer mit NetBird-IP "
                f"{args.prometheus_peer_ip} wurde nicht gefunden."
            )

    if not args.no_policy:
        policies = api_request(args.management_url, args.api_token, "GET", "/policies")
        if not isinstance(policies, list):
            raise RuntimeError("NetBird /policies lieferte keine Liste.")
        ensure_policy(
            args.management_url,
            args.api_token,
            policies,
            args.policy_name,
            monitoring_gid,
            metrics_gid,
            [args.port, args.detail_port],
        )

    # Gruppenmitgliedschaften können sich durch ensure_peer_in_group geändert haben.
    peers = api_request(args.management_url, args.api_token, "GET", "/peers")
    automatic = make_auto_targets(
        peers,
        args.metrics_group,
        args.monitoring_group,
        args.port,
        args.customer_fallback,
        ignore_groups,
        customer_root_group=args.customer_root_group,
        unassigned_customer=args.unassigned_customer,
        customer_marker_prefix=args.customer_marker_prefix,
    )
    merged = merge_targets(existing, automatic)
    changed = atomic_write_json(target_path, merged)

    managed_count = sum(
        1
        for item in merged
        if (item.get("labels") or {}).get("managed_by") == "netbird-api"
    )
    manual_count = len(merged) - managed_count

    if changed:
        log(
            f"Prometheus Targets aktualisiert: {managed_count} automatisch, "
            f"{manual_count} manuell."
        )
    else:
        log(
            f"Prometheus Targets unverändert: {managed_count} automatisch, "
            f"{manual_count} manuell."
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[FEHLER] {exc}", file=sys.stderr)
        raise SystemExit(1)
