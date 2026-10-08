#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path

EXPECTED = {"management.json", "signal.json", "relay.json", "client.json"}

# The upstream v0.80.0 management dashboard contains several metric names
# that don't match the names exported by the v0.80.0 OpenTelemetry
# Prometheus exporter. Keep these rewrites explicit and pinned to the
# downloaded dashboard copy.
MANAGEMENT_METRIC_REWRITES = {
    "management_grpc_sync_request_counter_ratio_total":
        "management_grpc_sync_request_counter_total",
    "management_grpc_login_request_counter_ratio_total":
        "management_grpc_login_request_counter_total",
    "management_grpc_key_request_counter_ratio_total":
        "management_grpc_key_request_counter_total",
    "management_grpc_sync_request_duration_ms_bucket":
        "management_grpc_sync_request_duration_ms_milliseconds_bucket",
    "management_grpc_login_request_duration_ms_bucket":
        "management_grpc_login_request_duration_ms_milliseconds_bucket",
    "management_http_request_duration_ms_bucket":
        "management_http_request_duration_ms_milliseconds_bucket",
    "management_updatechannel_close_one_duration_micro_bucket":
        "management_updatechannel_close_one_duration_micro_microseconds_bucket",
    "management_updatechannel_close_one_duration_micro_count":
        "management_updatechannel_close_one_duration_micro_microseconds_count",
    "management_updatechannel_send_duration_micro_bucket":
        "management_updatechannel_send_duration_micro_microseconds_bucket",
    "management_updatechannel_send_duration_micro_count":
        "management_updatechannel_send_duration_micro_microseconds_count",
    "management_updatechannel_create_duration_micro_bucket":
        "management_updatechannel_create_duration_micro_microseconds_bucket",
    "management_updatechannel_create_duration_micro_count":
        "management_updatechannel_create_duration_micro_microseconds_count",
    "management_updatechannel_create_duration_micro_sum":
        "management_updatechannel_create_duration_micro_microseconds_sum",
    "management_updatechannel_get_all_duration_micro_bucket":
        "management_updatechannel_get_all_duration_micro_microseconds_bucket",
    "management_updatechannel_get_all_duration_micro_count":
        "management_updatechannel_get_all_duration_micro_microseconds_count",
    "management_updatechannel_haschannel_duration_micro_bucket":
        "management_updatechannel_haschannel_duration_micro_microseconds_bucket",
    "management_updatechannel_haschannel_duration_micro_count":
        "management_updatechannel_haschannel_duration_micro_microseconds_count",
    "management_account_network_map_object_count_bucket":
        "management_account_network_map_object_count_objects_bucket",
}

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
    metric_rewrites = [0]

    def walk(value):
        if isinstance(value, str):
            for token in ("$__rate_interval", "$interval"):
                count = value.count(token)
                if count:
                    replacements[token] += count
                    value = value.replace(token, args.rate_interval)

            if path.name == "management.json":
                for old, new in MANAGEMENT_METRIC_REWRITES.items():
                    count = value.count(old)
                    if count:
                        metric_rewrites[0] += count
                        value = value.replace(old, new)
            return value
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        return value

    data = walk(data)

    if path.name == "management.json":
        def tune_panels(value):
            if isinstance(value, list):
                for item in value:
                    tune_panels(item)
                return
            if not isinstance(value, dict):
                return

            if value.get("title") == "Connected peers" and value.get("type") == "stat":
                defaults = value.setdefault("fieldConfig", {}).setdefault("defaults", {})
                defaults["decimals"] = 0
                defaults["unit"] = "none"
                defaults["thresholds"] = {
                    "mode": "absolute",
                    "steps": [
                        {"color": "red", "value": None},
                        {"color": "green", "value": 1},
                    ],
                }
                value.setdefault("options", {})["showPercentChange"] = False

            for item in value.values():
                tune_panels(item)

        tune_panels(data)

    rendered = json.dumps(data, indent=2) + "\n"
    leftovers = [token for token in ("$__rate_interval", "$interval") if token in rendered]
    if leftovers:
        raise SystemExit(f"Unersetzte Intervall-Variablen in {path}: {leftovers}")

    path.write_text(rendered, encoding="utf-8")
    print(
        f"{path.name}: "
        f"{replacements['$__rate_interval']} $__rate_interval + "
        f"{replacements['$interval']} $interval "
        f"ersetzt durch {args.rate_interval}; "
        f"{metric_rewrites[0]} Metriknamen korrigiert"
    )
