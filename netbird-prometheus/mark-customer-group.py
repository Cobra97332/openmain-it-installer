#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_MANAGEMENT_URL = "https://netbird.openmain-it.de"
DEFAULT_MARKER_PREFIX = "Kunde:"


def normalize_url(url):
    return url.rstrip("/")


def api_request(base_url, token, method, path, payload=None):
    url = f"{normalize_url(base_url)}/api{path}"
    data = None
    headers = {
        "Authorization": f"Token {token}",
        "Accept": "application/json",
        "User-Agent": "OpenMain-NetBird-Customer-Marker/1.0",
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
    return str((group or {}).get("id") or "")


def peer_ids(group):
    result = []
    for peer in (group or {}).get("peers") or []:
        if isinstance(peer, dict):
            pid = peer.get("id")
        else:
            pid = peer
        if pid:
            result.append(str(pid))
    return sorted(set(result))


def get_group(groups, name):
    return next((g for g in groups if g.get("name") == name), None)


def ensure_group(base_url, token, groups, name):
    existing = get_group(groups, name)
    if existing:
        return existing, groups

    created = api_request(
        base_url,
        token,
        "POST",
        "/groups",
        {"name": name, "peers": []},
    )
    if not group_id(created):
        raise RuntimeError(f"Gruppe '{name}' wurde ohne ID erstellt.")
    return created, list(groups) + [created]


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Übernimmt alle Peers einer bestehenden Kundengruppe in eine "
            "explizite Kunde:<Name>-Markergruppe, ohne bestehende Gruppen oder "
            "Policies zu verändern."
        )
    )
    parser.add_argument("customer", help="Kundenname, z. B. Bauer")
    parser.add_argument(
        "--source-group",
        help="Bestehende operative Kundengruppe; Standard = Kundenname",
    )
    parser.add_argument(
        "--management-url",
        default=os.getenv("NETBIRD_MANAGEMENT_URL", DEFAULT_MANAGEMENT_URL),
    )
    parser.add_argument("--api-token", default=os.getenv("NETBIRD_API_TOKEN", ""))
    parser.add_argument(
        "--marker-prefix",
        default=os.getenv("NETBIRD_CUSTOMER_MARKER_PREFIX", DEFAULT_MARKER_PREFIX),
    )
    args = parser.parse_args()

    customer = args.customer.strip()
    source_name = (args.source_group or customer).strip()
    marker_name = f"{args.marker_prefix}{customer}"

    if not customer:
        raise SystemExit("Kundenname fehlt.")
    if not args.api_token:
        raise SystemExit("NETBIRD_API_TOKEN fehlt.")

    groups = api_request(args.management_url, args.api_token, "GET", "/groups")
    if not isinstance(groups, list):
        raise RuntimeError("NetBird /groups lieferte keine Liste.")

    source = get_group(groups, source_name)
    if not source:
        raise RuntimeError(f"Quellgruppe '{source_name}' wurde nicht gefunden.")

    source_detail = api_request(
        args.management_url, args.api_token, "GET", f"/groups/{group_id(source)}"
    )
    source_peers = peer_ids(source_detail)

    marker, groups = ensure_group(
        args.management_url, args.api_token, groups, marker_name
    )
    marker_detail = api_request(
        args.management_url, args.api_token, "GET", f"/groups/{group_id(marker)}"
    )
    current = peer_ids(marker_detail)
    merged = sorted(set(current + source_peers))

    if merged != current:
        payload = {
            "name": marker_name,
            "peers": merged,
        }
        resources = (marker_detail or {}).get("resources")
        if resources:
            payload["resources"] = resources
        api_request(
            args.management_url,
            args.api_token,
            "PUT",
            f"/groups/{group_id(marker)}",
            payload,
        )

    print(f"[+] Kunde: {customer}")
    print(f"[+] Quelle: {source_name}")
    print(f"[+] Marker: {marker_name}")
    print(f"[+] Peers übernommen: {len(source_peers)}")
    print("[+] Bestehende Gruppen/Policies wurden nicht verändert.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[FEHLER] {exc}", file=sys.stderr)
        raise SystemExit(1)
