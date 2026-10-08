#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

DEFAULT_FILE = "/opt/openmain-netbird-prometheus/targets/netbird-clients.json"

parser = argparse.ArgumentParser(description="NetBird Client Metrics Target verwalten")
parser.add_argument("target", help="NetBird-IP oder NetBird-IP:Port")
parser.add_argument("host", help="Hostname/Anzeigename")
parser.add_argument("customer", nargs="?", default="intern", help="Kundenname (Standard: intern)")
parser.add_argument("--file", default=DEFAULT_FILE, help="Prometheus file_sd Zieldatei")
args = parser.parse_args()

target = args.target.strip()
if target.startswith("http://"):
    target = target[7:]
elif target.startswith("https://"):
    target = target[8:]
target = target.split("/", 1)[0]
if ":" not in target:
    target += ":9191"

path = Path(args.file)
path.parent.mkdir(parents=True, exist_ok=True)
if path.exists():
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"Ungültige JSON-Datei {path}: {exc}")
else:
    data = []

if not isinstance(data, list):
    raise SystemExit(f"{path} muss eine JSON-Liste enthalten.")

labels = {"host": args.host, "customer": args.customer or "intern"}

entry = {"targets": [target], "labels": labels}
for idx, current in enumerate(data):
    if target in current.get("targets", []):
        data[idx] = entry
        action = "aktualisiert"
        break
else:
    data.append(entry)
    action = "hinzugefügt"

data.sort(
    key=lambda item: (
        item.get("labels", {}).get("customer", ""),
        item.get("labels", {}).get("host", ""),
        item.get("targets", [""])[0],
    )
)
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
print(f"{action}: {target} -> {args.host}")
