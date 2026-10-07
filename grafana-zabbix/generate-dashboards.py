#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path

DS_UID = "zabbix-main"
DS_TYPE = "alexanderzobnin-zabbix-datasource"
SCHEMA_VERSION = 41

PROFILES = {
    "opnsense": {
        "title": "OpenMain - OPNsense",
        "group_regex": r"^OpenMain/OPNsense(?:/|$)",
        "panels": [
            ("Packet filter status", r"/(OPNsense: )?Packet filter running status/", "short", "stat"),
            ("State table utilization", r"/(OPNsense: )?States table utilization in %/", "percent", "timeseries"),
            ("State table current / limit", r"/(OPNsense: )?States table (current|limit)/", "short", "timeseries"),
            ("Source tracking current / limit", r"/(OPNsense: )?Source tracking table (current|limit)/", "short", "timeseries"),
            ("Source tracking utilization", r"/(OPNsense: )?Source tracking table utilization/", "percent", "timeseries"),
            ("Firewall rules", r"/(OPNsense: )?Firewall rules count/", "short", "stat"),
            ("Packet filter anomalies", r"/(OPNsense: )?(Packets with bad offset|Fragmented packets|Short packets|Normalized packets|Packets dropped due to memory limitation)/", "short", "timeseries"),
            ("Interface traffic", r"/(OPNsense: )?Interface .*:.*(traffic|Traffic|bits|Bits)/", "bps", "timeseries"),
            ("Interface errors / drops", r"/(OPNsense: )?Interface .*:.*(error|Error|discard|Discard|blocked|Blocked)/", "short", "timeseries"),
            ("CPU utilization", r"/(CPU|Processor).*(utilization|usage)/", "percent", "timeseries"),
            ("Load average", r"/[Ll]oad average/", "short", "timeseries"),
            ("Memory utilization", r"/(Memory|RAM).*(utilization|usage)/", "percent", "timeseries"),
            ("Gateway latency", r"/[Gg]ateway.*latency|latency.*[Gg]ateway/", "ms", "timeseries"),
            ("Gateway packet loss", r"/[Gg]ateway.*packet loss|packet loss.*[Gg]ateway/", "percent", "timeseries"),
            ("VPN status", r"/(WireGuard|OpenVPN|IPsec|VPN).*(status|Status|state|State)/", "short", "stat"),
        ],
    },
    "pve": {
        "title": "OpenMain - Proxmox VE",
        "group_regex": r"^OpenMain/PVE(?:/|$)",
        "panels": [
            ("CPU", r"/(Cluster|Node .*|QEMU .*|LXC .*):.*CPU.*(utilization|usage)/", "percent", "timeseries"),
            ("Memory", r"/(Cluster|Node .*|QEMU .*|LXC .*):.*Memory.*(utilization|usage)/", "percent", "timeseries"),
            ("Storage utilization", r"/(Cluster: Storage utilization|Storage .*:.*[Uu]tilization|Node .*:.*[Ff]ilesystem.*[Uu]tilization)/", "percent", "timeseries"),
            ("Storage capacity", r"/(Cluster: Storage (used|total)|Storage .*:.*(used|Used|total|Total|available|Available)|Node .*:.*[Ff]ilesystem.*(used|Used|total|Total|available|Available))/", "bytes", "timeseries"),
            ("VM / LXC counts", r"/Cluster: Number of (running|stopped) (virtual machines|LXC containers)/", "short", "timeseries"),
            ("VM / LXC status", r"/(QEMU|LXC).*:.*(Status|status)/", "short", "stat"),
            ("Cluster / Node health", r"/Cluster: Quorum status|Node .*:.*(Status|status)/", "short", "stat"),
            ("Cluster node count", r"/Cluster: Number of cluster nodes.*/", "short", "stat"),
            ("Physical disks / SMART status", r"/Node .*: Disk .*:.*(SMART|Health|health|Status|status|Wearout|wearout)/", "short", "stat"),
            ("Disk temperatures", r"/Node .*: Disk .*:.*(Temperature|temperature)/", "celsius", "timeseries"),
            ("Network", r"/(QEMU|LXC|Node).*:.*(network|Network|traffic|Traffic|received|Received|sent|Sent)/", "bps", "timeseries"),
            ("Uptime / Version", r"/(Node .*:.*(Uptime|uptime|Version|version)|Cluster:.*[Vv]ersion)/", "short", "stat"),
        ],
    },
    "idrac": {
        "title": "OpenMain - Dell iDRAC",
        "group_regex": r"^OpenMain/iDRAC(?:/|$)",
        "panels": [
            ("Overall health", r"/Overall system health status|(System|Global).*(health|Health|status|Status)|Rollup/", "short", "stat"),
            ("Temperatures", r"/(Temperature|Temp|temperature|temp).*(Reading|reading|Value|value)/", "celsius", "timeseries"),
            ("Fan speed", r"/Fan .*: Speed/", "rpm", "timeseries"),
            ("Fan status", r"/Fan .*: Status/", "short", "stat"),
            ("Power supplies", r"/(Power supply|PSU|Supply).*([Ss]tatus|[Ss]tate)/", "short", "stat"),
            ("Voltage", r"/(Voltage|voltage).*(Reading|reading|Value|value)/", "volt", "timeseries"),
            ("Physical disks", r"/(Physical disk|Disk|Drive).*([Ss]tatus|[Ss]tate|[Hh]ealth|failure|Failure)/", "short", "stat"),
            ("RAID / Virtual disks", r"/(Virtual disk|Controller|RAID).*([Ss]tatus|[Ss]tate|[Hh]ealth|Battery|battery)/", "short", "stat"),
            ("Memory / CPU health", r"/(Memory|CPU|Processor).*([Ss]tatus|[Ss]tate|[Hh]ealth)/", "short", "stat"),
            ("NIC link status", r"/NIC .*: Link status/", "short", "stat"),
            ("System / Firmware", r"/(Firmware version|BIOS version|Hardware model|Hardware serial|Operating system|Uptime)/", "short", "stat"),
        ],
    },
    "nas": {
        "title": "OpenMain - NAS",
        "group_regex": r"^OpenMain/NAS(?:/|$)",
        "panels": [
            ("System health", r"/(System|Overall).*(health|status)/", "short", "stat"),
            ("CPU", r"/(CPU|Processor).*(utilization|usage)/", "percent", "timeseries"),
            ("Load", r"/(CPU|System|Processor).*[Ll]oad|[Ll]oad average/", "short", "timeseries"),
            ("Memory utilization", r"/(Memory|RAM).*(utilization|usage)/", "percent", "timeseries"),
            ("Storage utilization", r"/(Volume|Storage|Pool|filesystem).*(utilization|usage|used percent|used %)/", "percent", "timeseries"),
            ("Storage status", r"/(Volume|Storage|Pool|filesystem).*([Ss]tatus|[Ss]tate|[Hh]ealth)/", "short", "stat"),
            ("Disks / SMART", r"/(Disk|Drive|SMART).*([Ss]tatus|[Hh]ealth|bad|error)/", "short", "stat"),
            ("Disk temperatures", r"/(Disk|Drive|SMART).*(temperature|Temperature|temp|Temp)/", "celsius", "timeseries"),
            ("RAID", r"/(RAID|Array).*(status|Status|health|Health|degraded|Degraded)/", "short", "stat"),
            ("System temperatures", r"/(System|CPU).*(Temperature|temperature|Temp|temp)/", "celsius", "timeseries"),
            ("Network", r"/(network|Network|traffic|Traffic|received|Received|sent|Sent|Incoming|Outgoing)/", "bps", "timeseries"),
        ],
    },
    "qnap": {
        "title": "OpenMain - QNAP",
        "group_regex": r"^OpenMain/QNAP(?:/|$)",
        "panels": [
            ("System health", r"/(System|Overall).*(health|Health|status|Status)/", "short", "stat"),
            ("CPU", r"/(CPU|Processor).*(utilization|usage)/", "percent", "timeseries"),
            ("Load", r"/(CPU|System|Processor).*[Ll]oad|[Ll]oad average/", "short", "timeseries"),
            ("Memory utilization", r"/(Memory|RAM).*(utilization|usage)/", "percent", "timeseries"),
            ("Storage utilization", r"/(Pool|Volume|Storage).*(utilization|usage|used percent|used %)/", "percent", "timeseries"),
            ("Storage status", r"/(Pool|Volume|Storage).*([Ss]tatus|[Ss]tate|[Hh]ealth)/", "short", "stat"),
            ("Disks / SMART", r"/(Disk|Drive|SMART).*([Ss]tatus|[Hh]ealth|bad|error)/", "short", "stat"),
            ("Disk temperatures", r"/(Disk|Drive|SMART).*(temperature|Temperature|temp|Temp)/", "celsius", "timeseries"),
            ("RAID", r"/(RAID|Array).*(status|Status|health|Health|degraded|Degraded)/", "short", "stat"),
            ("System temperatures", r"/(System|CPU).*(Temperature|temperature|Temp|temp)/", "celsius", "timeseries"),
            ("Network", r"/(network|Network|traffic|Traffic|received|Received|sent|Sent|Incoming|Outgoing)/", "bps", "timeseries"),
        ],
    },
    "synology": {
        "title": "OpenMain - Synology",
        "group_regex": r"^OpenMain/Synology(?:/|$)",
        "panels": [
            ("System health", r"/(System|Overall|DiskStation).*(health|Health|status|Status)/", "short", "stat"),
            ("CPU", r"/(CPU|Processor).*(utilization|usage)/", "percent", "timeseries"),
            ("Load", r"/(CPU|System|Processor).*[Ll]oad|[Ll]oad average/", "short", "timeseries"),
            ("Memory utilization", r"/(Memory|RAM).*(utilization|usage)/", "percent", "timeseries"),
            ("Storage utilization", r"/(Volume|Storage|Pool).*(utilization|usage|used percent|used %)/", "percent", "timeseries"),
            ("Storage status", r"/(Volume|Storage|Pool).*([Ss]tatus|[Ss]tate|[Hh]ealth)/", "short", "stat"),
            ("Disks / SMART", r"/(Disk|Drive|SMART).*([Ss]tatus|[Hh]ealth|bad|error)/", "short", "stat"),
            ("Disk temperatures", r"/(Disk|Drive|SMART).*(temperature|Temperature|temp|Temp)/", "celsius", "timeseries"),
            ("RAID", r"/(RAID|Array).*(status|Status|health|Health|degraded|Degraded)/", "short", "stat"),
            ("System temperatures", r"/(System|CPU).*(Temperature|temperature|Temp|temp)/", "celsius", "timeseries"),
            ("Network", r"/(network|Network|traffic|Traffic|received|Received|sent|Sent|Incoming|Outgoing)/", "bps", "timeseries"),
        ],
    },
}

def ds():
    return {"type": DS_TYPE, "uid": DS_UID}

def annotations():
    return {"list": [{
        "builtIn": 1,
        "datasource": {"type": "grafana", "uid": "-- Grafana --"},
        "enable": True,
        "hide": True,
        "iconColor": "rgba(0, 211, 255, 1)",
        "name": "Annotations & Alerts",
        "type": "dashboard",
    }]}

def metric_target(item_filter, group="$group", host="$host", ref="A"):
    return {
        "application": {"filter": ""},
        "countTriggers": False,
        "countTriggersBy": "",
        "datasource": ds(),
        "evaltype": "0",
        "functions": [],
        "group": {"filter": group},
        "host": {"filter": host},
        "item": {"filter": item_filter},
        "itemTag": {"filter": ""},
        "macro": {"filter": ""},
        "mode": 0,
        "options": {
            "count": False,
            "disableDataAlignment": False,
            "showDisabledItems": False,
            "skipEmptyValues": True,
            "useTrends": "default",
            "useZabbixValueMapping": True,
        },
        "proxy": {"filter": ""},
        "queryType": "0",
        "refId": ref,
        "resultFormat": "time_series",
        "schema": 12,
        "table": {"skipEmptyValues": True},
        "tags": {"filter": ""},
        "textFilter": "",
        "trigger": {"filter": ""},
    }

def problem_target(group="/.*/", host="/.*/", min_severity=0, count=False, ref="A"):
    return {
        "application": {"filter": ""},
        "countTriggersBy": "",
        "datasource": ds(),
        "evaltype": "0",
        "functions": [],
        "group": {"filter": group},
        "host": {"filter": host},
        "item": {"filter": ""},
        "itemTag": {"filter": ""},
        "macro": {"filter": ""},
        "mode": 4,
        "options": {
            "acknowledged": 2,
            "count": count,
            "disableDataAlignment": False,
            "hostProxy": True,
            "hostsInMaintenance": False,
            "limit": 500,
            "minSeverity": min_severity,
            "showDisabledItems": False,
            "skipEmptyValues": False,
            "sortProblems": "severity",
            "useTimeRange": False,
            "useTrends": "default",
            "useZabbixValueMapping": True,
        },
        "proxy": {"filter": ""},
        "queryType": "4",
        "refId": ref,
        "resultFormat": "time_series",
        "schema": 12,
        "showProblems": "problems",
        "table": {"skipEmptyValues": False},
        "tags": {"filter": ""},
        "target": "",
        "textFilter": "",
        "trigger": {"filter": ""},
        "triggers": {"acknowledged": 2, "count": count, "minSeverity": min_severity},
    }

def base_dashboard(title, uid, tags=None, refresh="30s", from_time="now-6h"):
    return {
        "annotations": annotations(),
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "id": None,
        "links": [],
        "panels": [],
        "refresh": refresh,
        "schemaVersion": SCHEMA_VERSION,
        "tags": tags or ["OpenMain", "Zabbix"],
        "templating": {"list": []},
        "time": {"from": from_time, "to": "now"},
        "timepicker": {"refresh_intervals": ["10s", "30s", "1m", "5m", "15m", "30m", "1h"]},
        "timezone": "browser",
        "title": title,
        "uid": uid,
        "version": 1,
        "weekStart": "monday",
    }

def variables(group_regex):
    # Grafana-Zabbix 6.x uses structured template-variable queries.
    # Legacy string queries such as "*" or "$group.*" leave the variable
    # dropdowns empty on current plugin versions.
    return [
        {
            "allFormat": "regex values",
            "current": {},
            "datasource": ds(),
            "definition": "Zabbix - group",
            "hide": 0,
            "includeAll": False,
            "label": "Gruppe",
            "multi": False,
            "multiFormat": "glob",
            "name": "group",
            "options": [],
            "query": {
                "application": "",
                "group": "/.*/",
                "host": "",
                "item": "",
                "queryType": "group",
            },
            "refresh": 1,
            "refresh_on_load": False,
            "regex": "/" + group_regex.replace("/", r"\\/") + "/",
            "skipUrlSync": False,
            "sort": 0,
            "tagValuesQuery": "",
            "tagsQuery": "",
            "type": "query",
            "useTags": False,
        },
        {
            "allFormat": "glob",
            "current": {},
            "datasource": ds(),
            "definition": "Zabbix - host",
            "hide": 0,
            "includeAll": False,
            "label": "Host",
            "multi": False,
            "multiFormat": "glob",
            "name": "host",
            "options": [],
            "query": {
                "application": "",
                "group": "$group",
                "host": "/.*/",
                "item": "",
                "queryType": "host",
            },
            "refresh": 1,
            "refresh_on_load": False,
            "regex": "",
            "skipUrlSync": False,
            "sort": 0,
            "tagValuesQuery": "",
            "tagsQuery": "",
            "type": "query",
            "useTags": False,
        },
    ]


def customer_variables():
    # Generic customer view: choose an existing Zabbix host group as customer
    # scope, then optionally narrow down to a host.
    return [
        {
            "allFormat": "regex values",
            "current": {},
            "datasource": ds(),
            "definition": "Zabbix - group",
            "hide": 0,
            "includeAll": False,
            "label": "Kunde / Gruppe",
            "multi": False,
            "multiFormat": "glob",
            "name": "customer_group",
            "options": [],
            "query": {
                "application": "",
                "group": "/.*/",
                "host": "",
                "item": "",
                "queryType": "group",
            },
            "refresh": 1,
            "refresh_on_load": False,
            "regex": "",
            "skipUrlSync": False,
            "sort": 1,
            "tagValuesQuery": "",
            "tagsQuery": "",
            "type": "query",
            "useTags": False,
        },
        {
            "allFormat": "regex values",
            "current": {},
            "datasource": ds(),
            "definition": "Zabbix - host",
            "hide": 0,
            "includeAll": True,
            "allValue": "/.*/",
            "label": "Host",
            "multi": True,
            "multiFormat": "regex values",
            "name": "customer_host",
            "options": [],
            "query": {
                "application": "",
                "group": "$customer_group",
                "host": "/.*/",
                "item": "",
                "queryType": "host",
            },
            "refresh": 1,
            "refresh_on_load": False,
            "regex": "",
            "skipUrlSync": False,
            "sort": 1,
            "tagValuesQuery": "",
            "tagsQuery": "",
            "type": "query",
            "useTags": False,
        },
    ]

def field_defaults(unit="short", stat=False):
    # Generic device status values use vendor/Zabbix value mappings whose
    # numeric enums differ by template. Do not apply a universal >=1=red
    # threshold here; that produced red panels even when the mapped value was
    # "ok". Problem counters have their own explicit threshold logic.
    d = {
        "color": {"mode": "palette-classic"},
        "mappings": [],
        "unit": unit,
    }
    if not stat:
        d["custom"] = {
            "axisBorderShow": False,
            "axisCenteredZero": False,
            "axisColorMode": "text",
            "axisLabel": "",
            "axisPlacement": "auto",
            "barAlignment": 0,
            "barWidthFactor": 0.6,
            "drawStyle": "line",
            "fillOpacity": 15,
            "gradientMode": "none",
            "hideFrom": {"legend": False, "tooltip": False, "viz": False},
            "insertNulls": False,
            "lineInterpolation": "linear",
            "lineWidth": 2,
            "pointSize": 4,
            "scaleDistribution": {"type": "linear"},
            "showPoints": "never",
            "spanNulls": True,
            "stacking": {"group": "A", "mode": "none"},
            "thresholdsStyle": {"mode": "off"},
        }
    return d

def timeseries_panel(pid, title, item_filter, unit, x, y, w=12, h=8):
    return {
        "datasource": ds(),
        "fieldConfig": {"defaults": field_defaults(unit, False), "overrides": []},
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "id": pid,
        "options": {
            "legend": {"calcs": ["lastNotNull"], "displayMode": "table", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "targets": [metric_target(item_filter)],
        "title": title,
        "type": "timeseries",
    }

def stat_panel(pid, title, item_filter, unit, x, y, w=12, h=6):
    return {
        "datasource": ds(),
        "fieldConfig": {"defaults": field_defaults(unit, True), "overrides": []},
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "id": pid,
        "options": {
            "colorMode": "none",
            "graphMode": "none",
            "justifyMode": "auto",
            "orientation": "horizontal",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "auto",
            "wideLayout": True,
        },
        "targets": [metric_target(item_filter)],
        "title": title,
        "type": "stat",
    }

def problems_table(pid, title, group="$group", host="$host", min_severity=0, x=0, y=0, w=24, h=9):
    return {
        "datasource": ds(),
        "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}}, "mappings": []}, "overrides": []},
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "id": pid,
        "options": {
            "cellHeight": "sm",
            "footer": {"countRows": False, "fields": "", "reducer": ["sum"], "show": False},
            "showHeader": True,
        },
        "targets": [problem_target(group, host, min_severity, False)],
        "title": title,
        "type": "table",
    }

def problem_stat(pid, title, min_severity, x, y, group="/.*/", w=6, h=5):
    return {
        "datasource": ds(),
        "fieldConfig": {"defaults": {
            "color": {"mode": "thresholds"},
            "mappings": [],
            "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]},
            "unit": "none",
        }, "overrides": []},
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "id": pid,
        "options": {
            "colorMode": "background",
            "graphMode": "none",
            "justifyMode": "center",
            "orientation": "horizontal",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "value_and_name",
            "wideLayout": True,
        },
        "targets": [problem_target(group, "/.*/", min_severity, True)],
        "title": title,
        "type": "stat",
    }

def make_device_dashboard(key, profile):
    d = base_dashboard(profile["title"], f"openmain-{key}", ["OpenMain", "Zabbix", key])
    d["templating"]["list"] = variables(profile["group_regex"])
    d["panels"].append(problems_table(1, "Aktive Probleme", y=0, h=8))
    pid = 2
    y = 8
    for idx, (title, item_filter, unit, panel_type) in enumerate(profile["panels"]):
        x = 0 if idx % 2 == 0 else 12
        if idx and idx % 2 == 0:
            y += 8
        if panel_type == "stat":
            panel = stat_panel(pid, title, item_filter, unit, x, y, 12, 8)
        else:
            panel = timeseries_panel(pid, title, item_filter, unit, x, y, 12, 8)
        d["panels"].append(panel)
        pid += 1
    return d

def make_problems_dashboard():
    d = base_dashboard("OpenMain - Alle Probleme", "openmain-problems", ["OpenMain", "Zabbix", "Problems"], "30s", "now-24h")
    d["panels"] = [
        problem_stat(1, "Disaster", 5, 0, 0),
        problem_stat(2, "High+", 4, 6, 0),
        problem_stat(3, "Average+", 3, 12, 0),
        problem_stat(4, "Warning+", 2, 18, 0),
        problems_table(5, "Alle aktiven Probleme", "/.*/", "/.*/", 0, 0, 5, 24, 14),
    ]
    return d


def make_customer_dashboard():
    d = base_dashboard(
        "OpenMain - Kundenübersicht",
        "openmain-customers",
        ["OpenMain", "Zabbix", "Customers"],
        "30s",
        "now-24h",
    )
    d["templating"]["list"] = customer_variables()
    d["panels"] = [
        problem_stat(1, "Disaster", 5, 0, 0, "$customer_group", 6, 5),
        problem_stat(2, "High+", 4, 6, 0, "$customer_group", 6, 5),
        problem_stat(3, "Average+", 3, 12, 0, "$customer_group", 6, 5),
        problem_stat(4, "Warning+", 2, 18, 0, "$customer_group", 6, 5),
        problems_table(
            5,
            "Aktive Probleme des Kunden",
            "$customer_group",
            "$customer_host",
            0,
            0,
            5,
            24,
            14,
        ),
    ]
    return d

def make_tv_dashboard():
    d = base_dashboard("OpenMain - TV / NOC", "openmain-tv", ["OpenMain", "Zabbix", "TV", "NOC"], "30s", "now-3h")
    d["editable"] = False
    d["panels"] = [
        problem_stat(1, "Kritische Systeme: High+", 4, 0, 0, "OpenMain/Critical", 8, 5),
        problem_stat(2, "Gesamt: High+", 4, 8, 0, "/.*/", 8, 5),
        problem_stat(3, "Gesamt: Warning+", 2, 16, 0, "/.*/", 8, 5),

        problem_stat(10, "OPNsense", 2, 0, 5, "OpenMain/OPNsense", 4, 4),
        problem_stat(11, "PVE", 2, 4, 5, "OpenMain/PVE", 4, 4),
        problem_stat(12, "iDRAC", 2, 8, 5, "OpenMain/iDRAC", 4, 4),
        problem_stat(13, "QNAP", 2, 12, 5, "OpenMain/QNAP", 4, 4),
        problem_stat(14, "Synology", 2, 16, 5, "OpenMain/Synology", 4, 4),
        problem_stat(15, "NAS gesamt", 2, 20, 5, "OpenMain/NAS", 4, 4),

        problems_table(4, "Kritische Systeme - aktive Probleme", "OpenMain/Critical", "/.*/", 0, 0, 9, 24, 10),
        problems_table(5, "Alle High/Disaster Probleme", "/.*/", "/.*/", 4, 0, 19, 24, 10),
    ]
    return d

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="generated")
    parser.add_argument("--datasource-uid", default=os.environ.get("GRAFANA_ZABBIX_UID", "zabbix-main"))
    args = parser.parse_args()
    global DS_UID
    DS_UID = args.datasource_uid
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    dashboards = {f"{key}.json": make_device_dashboard(key, profile) for key, profile in PROFILES.items()}
    dashboards["problems.json"] = make_problems_dashboard()
    dashboards["customers.json"] = make_customer_dashboard()
    dashboards["tv.json"] = make_tv_dashboard()
    for filename, data in dashboards.items():
        (out / filename).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(dashboards)} Dashboards erzeugt in {out}")

if __name__ == "__main__":
    main()
