#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path

EXPECTED = {"management.json", "signal.json", "relay.json", "client.json"}

parser = argparse.ArgumentParser(
    description="Normalize official NetBird Grafana dashboards for OpenMain."
)
parser.add_argument("dashboard_dir", type=Path)
parser.add_argument(
    "--rate-interval",
    default="2m",
    help="Fixed PromQL range for rate()/increase(); default: 2m",
)
args = parser.parse_args()

if not re.fullmatch(r"[1-9][0-9]*(ms|s|m|h|d|w|y)", args.rate_interval):
    raise SystemExit(f"Ungültiges rate interval: {args.rate_interval}")

root = args.dashboard_dir
found = {p.name for p in root.glob("*.json")}
if found != EXPECTED:
    raise SystemExit(f"Dashboard-Satz unvollständig: {sorted(found)}")

uids = set()
for path in sorted(root.glob("*.json")):
    data = json.loads(path.read_text(encoding="utf-8"))

    if not data.get("title"):
        raise SystemExit(f"Dashboard ohne Titel: {path}")

    uid = data.get("uid")
    if not uid or uid in uids:
        raise SystemExit(f"Fehlende/doppelte Dashboard-UID: {path}")
    uids.add(uid)

    data["id"] = None
    replacements = {"$__rate_interval": 0, "$interval": 0}

    def walk(value):
        if isinstance(value, str):
            for token in ("$__rate_interval", "$interval"):
                count = value.count(token)
                if count:
                    replacements[token] += count
                    value = value.replace(token, args.rate_interval)
            return value
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        return value

    data = walk(data)

    rendered = json.dumps(data, indent=2) + "\n"
    leftovers = [token for token in ("$__rate_interval", "$interval") if token in rendered]
    if leftovers:
        raise SystemExit(f"Unersetzte Intervall-Variablen in {path}: {leftovers}")

    path.write_text(rendered, encoding="utf-8")
    print(
        f"{path.name}: "
        f"{replacements['$__rate_interval']} $__rate_interval + "
        f"{replacements['$interval']} $interval "
        f"ersetzt durch {args.rate_interval}"
    )
