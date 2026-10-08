#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

DASHBOARD_UID = "openmain-netbird-overview"
DASHBOARD_TITLE = "NetBird / Overview"


def datasource():
    return {"type": "prometheus", "uid": "${datasource}"}


def prometheus_target(expr, ref_id="A", legend="__auto", instant=False, fmt="time_series"):
    target = {
        "datasource": datasource(),
        "editorMode": "code",
        "expr": expr,
        "legendFormat": legend,
        "range": not instant,
        "refId": ref_id,
    }
    if instant:
        target["instant"] = True
    if fmt == "table":
        target["format"] = "table"
    return target


def thresholds(good_from=1):
    return {
        "mode": "absolute",
        "steps": [
            {"color": "red", "value": None},
            {"color": "green", "value": good_from},
        ],
    }


def stat_panel(panel_id, title, expr, x, y, w=4, h=4, unit="none",
               description="", mappings=None, threshold_steps=None,
               color_mode="value"):
    defaults = {
        "color": {"mode": "thresholds"},
        "decimals": 0,
        "mappings": mappings or [],
        "thresholds": threshold_steps or thresholds(1),
        "unit": unit,
    }
    return {
        "id": panel_id,
        "type": "stat",
        "title": title,
        "description": description,
        "datasource": datasource(),
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {
            "defaults": defaults,
            "overrides": [],
        },
        "options": {
            "colorMode": color_mode,
            "graphMode": "area",
            "justifyMode": "auto",
            "orientation": "auto",
            "percentChangeColorMode": "standard",
            "reduceOptions": {
                "calcs": ["lastNotNull"],
                "fields": "",
                "values": False,
            },
            "showPercentChange": False,
            "textMode": "auto",
            "wideLayout": True,
        },
        "targets": [prometheus_target(expr, instant=True)],
    }


def timeseries_panel(panel_id, title, targets, x, y, w=12, h=8,
                     unit="short", description="", min_value=None,
                     legend_mode="table"):
    defaults = {
        "color": {"mode": "palette-classic"},
        "custom": {
            "axisCenteredZero": False,
            "axisColorMode": "text",
            "axisLabel": "",
            "axisPlacement": "auto",
            "barAlignment": 0,
            "drawStyle": "line",
            "fillOpacity": 12,
            "gradientMode": "none",
            "hideFrom": {"legend": False, "tooltip": False, "viz": False},
            "lineInterpolation": "smooth",
            "lineWidth": 2,
            "pointSize": 5,
            "scaleDistribution": {"type": "linear"},
            "showPoints": "never",
            "spanNulls": True,
            "stacking": {"group": "A", "mode": "none"},
            "thresholdsStyle": {"mode": "off"},
        },
        "mappings": [],
        "thresholds": {
            "mode": "absolute",
            "steps": [{"color": "green", "value": None}],
        },
        "unit": unit,
    }
    if min_value is not None:
        defaults["min"] = min_value
    return {
        "id": panel_id,
        "type": "timeseries",
        "title": title,
        "description": description,
        "datasource": datasource(),
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "legend": {
                "calcs": ["lastNotNull", "mean", "max"] if legend_mode == "table" else [],
                "displayMode": legend_mode,
                "placement": "bottom",
                "showLegend": True,
            },
            "tooltip": {
                "hideZeros": False,
                "mode": "multi",
                "sort": "desc",
            },
        },
        "targets": targets,
    }


def pie_panel(panel_id, title, expr, legend, x, y, w=8, h=7, unit="none"):
    return {
        "id": panel_id,
        "type": "piechart",
        "title": title,
        "datasource": datasource(),
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "mappings": [],
                "unit": unit,
            },
            "overrides": [],
        },
        "options": {
            "displayLabels": ["name", "percent", "value"],
            "legend": {
                "displayMode": "table",
                "placement": "right",
                "showLegend": True,
                "values": ["value", "percent"],
            },
            "pieType": "donut",
            "reduceOptions": {
                "calcs": ["lastNotNull"],
                "fields": "",
                "values": False,
            },
            "tooltip": {"hideZeros": False, "mode": "single", "sort": "none"},
        },
        "targets": [prometheus_target(expr, legend=legend, instant=True)],
    }


def bargauge_panel(panel_id, title, expr, legend, x, y, w=8, h=7, unit="s"):
    return {
        "id": panel_id,
        "type": "bargauge",
        "title": title,
        "datasource": datasource(),
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "continuous-GrYlRd"},
                "mappings": [],
                "min": 0,
                "unit": unit,
            },
            "overrides": [],
        },
        "options": {
            "displayMode": "gradient",
            "maxVizHeight": 300,
            "minVizHeight": 10,
            "minVizWidth": 0,
            "namePlacement": "auto",
            "orientation": "horizontal",
            "reduceOptions": {
                "calcs": ["lastNotNull"],
                "fields": "",
                "values": False,
            },
            "showUnfilled": True,
            "sizing": "auto",
            "valueMode": "color",
        },
        "targets": [prometheus_target(expr, legend=legend, instant=True)],
    }


def row(panel_id, title, y):
    return {
        "id": panel_id,
        "type": "row",
        "title": title,
        "collapsed": False,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        "panels": [],
    }


def client_status_table(panel_id, x, y, w=24, h=9):
    return {
        "id": panel_id,
        "type": "table",
        "title": "Client Status",
        "description": "Prometheus scrape status der überwachten NetBird-Clients. 1 = Online, 0 = nicht erreichbar.",
        "datasource": datasource(),
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {
            "defaults": {
                "custom": {
                    "align": "auto",
                    "cellOptions": {"type": "auto"},
                    "footer": {"reducers": []},
                    "inspect": False,
                },
                "mappings": [],
            },
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "Status"},
                    "properties": [
                        {
                            "id": "mappings",
                            "value": [
                                {
                                    "type": "value",
                                    "options": {
                                        "0": {"color": "red", "index": 0, "text": "OFFLINE"},
                                        "1": {"color": "green", "index": 1, "text": "ONLINE"},
                                    },
                                }
                            ],
                        },
                        {
                            "id": "custom.cellOptions",
                            "value": {"type": "color-text"},
                        },
                    ],
                }
            ],
        },
        "options": {
            "cellHeight": "sm",
            "footer": {"countRows": False, "enablePagination": False, "fields": "", "reducer": ["sum"], "show": False},
            "showHeader": True,
        },
        "targets": [
            prometheus_target(
                'up{job="netbird-client",customer=~"$customer",host=~"$host"}',
                legend="{{host}}",
                instant=True,
                fmt="table",
            )
        ],
        "transformations": [
            {
                "id": "organize",
                "options": {
                    "excludeByName": {
                        "Time": True,
                        "__name__": True,
                        "instance": True,
                        "job": True,
                        "managed_by": True,
                        "peer_id": True,
                    },
                    "indexByName": {
                        "customer": 0,
                        "host": 1,
                        "Value": 2,
                    },
                    "renameByName": {
                        "customer": "Kunde",
                        "host": "Host",
                        "Value": "Status",
                    },
                },
            }
        ],
    }


def build_dashboard():
    online_expr = 'sum(up{job="netbird-client",customer=~"$customer",host=~"$host"}) or vector(0)'
    offline_expr = 'sum(1 - up{job="netbird-client",customer=~"$customer",host=~"$host"}) or vector(0)'
    mgmt_expr = 'sum(netbird_management_connected{job="netbird-client",customer=~"$customer",host=~"$host"}) or vector(0)'
    signal_expr = 'sum(netbird_signal_connected{job="netbird-client",customer=~"$customer",host=~"$host"}) or vector(0)'
    p2p_expr = 'sum(netbird_peers_connected{job="netbird-client",customer=~"$customer",host=~"$host",connection_type="p2p"}) or vector(0)'
    relay_expr = 'sum(netbird_peers_connected{job="netbird-client",customer=~"$customer",host=~"$host",connection_type="relay"}) or vector(0)'

    panels = [
        row(1, "Übersicht", 0),
        stat_panel(2, "Clients online", online_expr, 0, 1, description="Aktuell erfolgreich von Prometheus erreichbare NetBird-Clients."),
        stat_panel(
            3,
            "Clients offline",
            offline_expr,
            4,
            1,
            description="Überwachte Clients, deren Metrics-Endpunkt aktuell nicht erreichbar ist.",
            threshold_steps={
                "mode": "absolute",
                "steps": [
                    {"color": "green", "value": None},
                    {"color": "red", "value": 1},
                ],
            },
        ),
        stat_panel(4, "Management verbunden", mgmt_expr, 8, 1, description="Clients mit aktiver Verbindung zum NetBird Management."),
        stat_panel(5, "Signal verbunden", signal_expr, 12, 1, description="Clients mit aktiver Verbindung zum NetBird Signal-Service."),
        stat_panel(6, "P2P Verbindungen", p2p_expr, 16, 1, description="Direkte Peer-to-Peer-Verbindungen der ausgewählten Clients."),
        stat_panel(
            7,
            "Relay Verbindungen",
            relay_expr,
            20,
            1,
            description="Verbindungen, die aktuell über Relay laufen.",
            threshold_steps={
                "mode": "absolute",
                "steps": [
                    {"color": "green", "value": None},
                    {"color": "yellow", "value": 1},
                    {"color": "red", "value": 5},
                ],
            },
        ),
        stat_panel(
            8,
            "Bekannte Peers",
            'sum(netbird_peers{job="netbird-client",customer=~"$customer",host=~"$host"}) or vector(0)',
            0,
            5,
        ),
        stat_panel(
            9,
            "Verbundene Peers",
            'sum(netbird_peers_connected{job="netbird-client",customer=~"$customer",host=~"$host"}) or vector(0)',
            4,
            5,
        ),
        stat_panel(
            10,
            "P2P Anteil",
            '100 * (sum(netbird_peers_connected{job="netbird-client",customer=~"$customer",host=~"$host",connection_type="p2p"}) or vector(0)) / clamp_min(sum(netbird_peers_connected{job="netbird-client",customer=~"$customer",host=~"$host"}) or vector(0), 1)',
            8,
            5,
            unit="percent",
            threshold_steps={
                "mode": "absolute",
                "steps": [
                    {"color": "red", "value": None},
                    {"color": "yellow", "value": 70},
                    {"color": "green", "value": 90},
                ],
            },
        ),
        stat_panel(
            11,
            "Ø P2P Latenz",
            'avg(netbird_peer_latency_seconds{job="netbird-client",customer=~"$customer",host=~"$host"}) or vector(0)',
            12,
            5,
            unit="s",
            threshold_steps={
                "mode": "absolute",
                "steps": [
                    {"color": "green", "value": None},
                    {"color": "yellow", "value": 0.05},
                    {"color": "red", "value": 0.15},
                ],
            },
        ),
        stat_panel(
            12,
            "Management Streams",
            'max(management_grpc_connected_streams_ratio{job="netbird-server"}) or vector(0)',
            16,
            5,
            description="Aktuelle gRPC-Streams am NetBird Management-Server.",
        ),
        stat_panel(
            13,
            "Relay aktive Peers",
            'max(relay_peers_active{job="netbird-server"}) or vector(0)',
            20,
            5,
            description="Aktuell aktive Peers am NetBird Relay.",
        ),
        row(14, "Verbindungen & Traffic", 9),
        timeseries_panel(
            15,
            "P2P vs. Relay Verbindungen",
            [
                prometheus_target(
                    'sum by (connection_type) (netbird_peers_connected{job="netbird-client",customer=~"$customer",host=~"$host"})',
                    legend="{{connection_type}}",
                )
            ],
            0,
            10,
            w=12,
            h=8,
            unit="none",
            min_value=0,
            description="Entwicklung der direkten und über Relay aufgebauten Peer-Verbindungen.",
        ),
        timeseries_panel(
            16,
            "Relay Traffic",
            [
                prometheus_target(
                    'rate(relay_transfer_received_bytes_total{job="netbird-server"}[2m])',
                    ref_id="A",
                    legend="Empfangen",
                ),
                prometheus_target(
                    'rate(relay_transfer_sent_bytes_total{job="netbird-server"}[2m])',
                    ref_id="B",
                    legend="Gesendet",
                ),
            ],
            12,
            10,
            w=12,
            h=8,
            unit="Bps",
            min_value=0,
            description="Datenrate des zentralen NetBird Relay.",
        ),
        timeseries_panel(
            17,
            "Management Requests",
            [
                prometheus_target(
                    'rate(management_grpc_sync_request_counter_total{job="netbird-server"}[2m])',
                    ref_id="A",
                    legend="Sync/s",
                ),
                prometheus_target(
                    'rate(management_grpc_login_request_counter_total{job="netbird-server"}[2m])',
                    ref_id="B",
                    legend="Login/s",
                ),
                prometheus_target(
                    'rate(management_grpc_key_request_counter_total{job="netbird-server"}[2m])',
                    ref_id="C",
                    legend="Key/s",
                ),
            ],
            0,
            18,
            w=12,
            h=8,
            unit="reqps",
            min_value=0,
        ),
        timeseries_panel(
            18,
            "P2P Latenz",
            [
                prometheus_target(
                    'netbird_peer_latency_seconds{job="netbird-client",customer=~"$customer",host=~"$host"}',
                    legend="{{host}} → {{peer}}",
                )
            ],
            12,
            18,
            w=12,
            h=8,
            unit="s",
            min_value=0,
            description="Round-trip-Latenz direkter P2P-Verbindungen. Relay-Verbindungen liefern hier keine Peer-RTT.",
        ),
        row(19, "Verteilung & Clients", 26),
        pie_panel(
            20,
            "Verbindungstypen",
            'sum by (connection_type) (netbird_peers_connected{job="netbird-client",customer=~"$customer",host=~"$host"})',
            "{{connection_type}}",
            0,
            27,
            w=8,
            h=7,
        ),
        pie_panel(
            21,
            "Überwachte Clients je Kunde",
            'count by (customer) (up{job="netbird-client",customer=~"$customer",host=~"$host"})',
            "{{customer}}",
            8,
            27,
            w=8,
            h=7,
        ),
        bargauge_panel(
            22,
            "Top P2P Latenzen",
            'topk(10, netbird_peer_latency_seconds{job="netbird-client",customer=~"$customer",host=~"$host"})',
            "{{host}} → {{peer}}",
            16,
            27,
            w=8,
            h=7,
            unit="s",
        ),
        client_status_table(23, 0, 34, w=24, h=9),
        row(24, "Client Performance", 43),
        timeseries_panel(
            25,
            "Verbindungsaufbau p50",
            [
                prometheus_target(
                    'histogram_quantile(0.5, sum(increase(netbird_peer_connection_stage_duration_seconds_bucket{job="netbird-client",customer=~"$customer",host=~"$host",stage="total"}[2m])) by (le,connection_type))',
                    legend="{{connection_type}}",
                )
            ],
            0,
            44,
            w=8,
            h=8,
            unit="s",
            min_value=0,
        ),
        timeseries_panel(
            26,
            "Sync Verarbeitung p50",
            [
                prometheus_target(
                    'histogram_quantile(0.5, sum(increase(netbird_sync_duration_seconds_bucket{job="netbird-client",customer=~"$customer",host=~"$host"}[2m])) by (le))',
                    legend="Sync",
                )
            ],
            8,
            44,
            w=8,
            h=8,
            unit="s",
            min_value=0,
        ),
        timeseries_panel(
            27,
            "Login p50",
            [
                prometheus_target(
                    'histogram_quantile(0.5, sum(increase(netbird_login_duration_seconds_bucket{job="netbird-client",customer=~"$customer",host=~"$host"}[2m])) by (le,success))',
                    legend="success={{success}}",
                )
            ],
            16,
            44,
            w=8,
            h=8,
            unit="s",
            min_value=0,
        ),
    ]

    return {
        "annotations": {"list": []},
        "description": (
            "OpenMain NetBird Gesamtübersicht im Statistics-Dashboard-Stil: "
            "Status, P2P/Relay, Traffic, Latenz und Client-Inventar auf einer Seite."
        ),
        "editable": False,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "id": None,
        "links": [],
        "liveNow": False,
        "panels": panels,
        "refresh": "30s",
        "schemaVersion": 41,
        "tags": ["netbird", "openmain", "overview", "statistics"],
        "templating": {
            "list": [
                {
                    "name": "datasource",
                    "type": "datasource",
                    "label": "Datasource",
                    "query": "prometheus",
                    "current": {},
                    "refresh": 1,
                    "hide": 2,
                },
                {
                    "name": "customer",
                    "type": "query",
                    "label": "Kunde",
                    "datasource": datasource(),
                    "definition": 'label_values(up{job="netbird-client"},customer)',
                    "query": {
                        "qryType": 1,
                        "query": 'label_values(up{job="netbird-client"},customer)',
                        "refId": "PrometheusVariableQueryEditor-Customer",
                    },
                    "refresh": 1,
                    "sort": 1,
                    "multi": True,
                    "includeAll": True,
                    "allValue": ".*",
                    "current": {"selected": True, "text": "All", "value": "$__all"},
                    "hide": 0,
                },
                {
                    "name": "host",
                    "type": "query",
                    "label": "Host",
                    "datasource": datasource(),
                    "definition": 'label_values(up{job="netbird-client",customer=~"$customer"},host)',
                    "query": {
                        "qryType": 1,
                        "query": 'label_values(up{job="netbird-client",customer=~"$customer"},host)',
                        "refId": "PrometheusVariableQueryEditor-Host",
                    },
                    "refresh": 1,
                    "sort": 1,
                    "multi": True,
                    "includeAll": True,
                    "allValue": ".*",
                    "current": {"selected": True, "text": "All", "value": "$__all"},
                    "hide": 0,
                },
            ]
        },
        "time": {"from": "now-2d", "to": "now"},
        "timepicker": {},
        "timezone": "browser",
        "title": DASHBOARD_TITLE,
        "uid": DASHBOARD_UID,
        "version": 1,
        "weekStart": "",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", help="Zieldatei für das Grafana-Dashboard")
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    dashboard = build_dashboard()
    output.write_text(json.dumps(dashboard, indent=2) + "\n", encoding="utf-8")
    print(f"{output}: {DASHBOARD_TITLE} erzeugt")


if __name__ == "__main__":
    main()
