#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

UID = "openmain-netbird-relay-connections"
TITLE = "NetBird / Relay Connections"


def ds():
    return {"type": "prometheus", "uid": "${datasource}"}


def target(expr, legend="__auto", ref="A", instant=False, table=False):
    value = {
        "datasource": ds(),
        "editorMode": "code",
        "expr": expr,
        "legendFormat": legend,
        "range": not instant,
        "refId": ref,
    }
    if instant:
        value["instant"] = True
    if table:
        value["format"] = "table"
    return value


def row(pid, title, y):
    return {
        "id": pid,
        "type": "row",
        "title": title,
        "collapsed": False,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        "panels": [],
    }


def stat(pid, title, expr, x, y, w=6, unit="none", description=""):
    return {
        "id": pid,
        "type": "stat",
        "title": title,
        "description": description,
        "datasource": ds(),
        "gridPos": {"h": 4, "w": w, "x": x, "y": y},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "decimals": 0,
                "mappings": [],
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "yellow", "value": 1},
                        {"color": "red", "value": 5},
                    ],
                },
                "unit": unit,
            },
            "overrides": [],
        },
        "options": {
            "colorMode": "value",
            "graphMode": "area",
            "justifyMode": "auto",
            "orientation": "auto",
            "reduceOptions": {
                "calcs": ["lastNotNull"],
                "fields": "",
                "values": False,
            },
            "showPercentChange": False,
            "textMode": "auto",
            "wideLayout": True,
        },
        "targets": [target(expr, instant=True)],
    }


def timeseries(pid, title, expr, legend, x, y, w=12, h=8, unit="none", description=""):
    return {
        "id": pid,
        "type": "timeseries",
        "title": title,
        "description": description,
        "datasource": ds(),
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {
            "defaults": {
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
            },
            "overrides": [],
        },
        "options": {
            "legend": {
                "calcs": ["lastNotNull", "mean", "max"],
                "displayMode": "table",
                "placement": "bottom",
                "showLegend": True,
            },
            "tooltip": {"hideZeros": False, "mode": "multi", "sort": "desc"},
        },
        "targets": [target(expr, legend=legend)],
    }


def bargauge(pid, title, expr, legend, x, y, w=12, h=8, unit="none"):
    return {
        "id": pid,
        "type": "bargauge",
        "title": title,
        "datasource": ds(),
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
        "targets": [target(expr, legend=legend, instant=True)],
    }


def connection_table(panel_id, title, expr, y, relay_only=False):
    description = (
        "Aktuelle Peer-Beziehungen aus Sicht der überwachten Clients. "
        "Bei P2P bleibt 'Relay Server' leer; bei Relay zeigt die Spalte den tatsächlich verwendeten Relay-Endpunkt."
    )
    if relay_only:
        description = (
            "Nur aktuelle Relay-Beziehungen. Jede Zeile zeigt Quelle, Ziel und den tatsächlich verwendeten Relay-Server. "
            "Wenn beide Endpunkte überwacht werden, kann dieselbe Verbindung in beiden Richtungen erscheinen."
        )

    return {
        "id": panel_id,
        "type": "table",
        "title": title,
        "description": description,
        "datasource": ds(),
        "gridPos": {"h": 10, "w": 24, "x": 0, "y": y},
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
                    "matcher": {"id": "byName", "options": "Verbindung"},
                    "properties": [
                        {
                            "id": "mappings",
                            "value": [
                                {
                                    "type": "value",
                                    "options": {
                                        "p2p": {"color": "green", "index": 0, "text": "P2P"},
                                        "relay": {"color": "orange", "index": 1, "text": "Relay"},
                                        "unknown": {"color": "yellow", "index": 2, "text": "Unknown"},
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
            "footer": {
                "countRows": True,
                "enablePagination": True,
                "fields": "",
                "reducer": ["count"],
                "show": True,
            },
            "showHeader": True,
        },
        "targets": [
            target(
                expr,
                legend="{{host}} -> {{peer}}",
                instant=True,
                table=True,
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
                        "Value": True,
                    },
                    "indexByName": {
                        "customer": 0,
                        "host": 1,
                        "peer": 2,
                        "peer_ip": 3,
                        "connection_type": 4,
                        "relay_address": 5,
                    },
                    "renameByName": {
                        "customer": "Kunde",
                        "host": "Quelle",
                        "peer": "Ziel",
                        "peer_ip": "Ziel NetBird-IP",
                        "connection_type": "Verbindung",
                        "relay_address": "Relay Server",
                    },
                },
            }
        ],
    }


def relay_table():
    return connection_table(
        7,
        "Aktuelle Relay-Verbindungen – wer mit wem",
        'openmain_netbird_peer_connection_info{job="netbird-client-detail",'
        'customer=~"$customer",host=~"$source",connection_type="relay"}',
        20,
        relay_only=True,
    )


def all_connections_table():
    return connection_table(
        6,
        "Alle aktuellen Verbindungen",
        'openmain_netbird_peer_connection_info{job="netbird-client-detail",'
        'customer=~"$customer",host=~"$source"}',
        10,
        relay_only=False,
    )

def build():
    relay_filter = (
        'job="netbird-client-detail",customer=~"$customer",'
        'host=~"$source",connection_type="relay"'
    )

    panels = [
        row(1, "Aktueller Relay-Status", 0),
        stat(
            2,
            "Relay-Beziehungen",
            f"sum(openmain_netbird_peer_connection_info{{{relay_filter}}}) or vector(0)",
            0,
            1,
            description="Gerichtete Relay-Beziehungen aus Sicht der überwachten Clients.",
        ),
        stat(
            3,
            "Clients mit Relay",
            f'count(count by (host) (openmain_netbird_peer_connection_info{{{relay_filter}}})) or vector(0)',
            6,
            1,
        ),
        stat(
            4,
            "Ziel-Peers über Relay",
            f'count(count by (peer) (openmain_netbird_peer_connection_info{{{relay_filter}}})) or vector(0)',
            12,
            1,
        ),
        stat(
            5,
            "Verwendete Relay-Server",
            f'count(count by (relay_address) (openmain_netbird_peer_connection_info{{{relay_filter},relay_address!=""}})) or vector(0)',
            18,
            1,
        ),
        all_connections_table(),
        relay_table(),
        row(8, "Verteilung & Verlauf", 25),
        timeseries(
            9,
            "Relay-Verbindungen nach Quelle",
            f'sum by (host) (openmain_netbird_peer_connection_info{{{relay_filter}}})',
            "{{host}}",
            0,
            26,
            w=12,
            h=8,
            unit="none",
        ),
        timeseries(
            10,
            "Relay-Verbindungen nach Relay-Server",
            f'sum by (relay_address) (openmain_netbird_peer_connection_info{{{relay_filter},relay_address!=""}})',
            "{{relay_address}}",
            12,
            26,
            w=12,
            h=8,
            unit="none",
        ),
        row(11, "Traffic & Handshake", 34),
        bargauge(
            12,
            "Relay Traffic gesendet",
            f'topk(15, rate(openmain_netbird_peer_transfer_sent_bytes{{{relay_filter}}}[2m]))',
            "{{host}} -> {{peer}}",
            0,
            35,
            w=12,
            h=8,
            unit="Bps",
        ),
        bargauge(
            13,
            "Relay Traffic empfangen",
            f'topk(15, rate(openmain_netbird_peer_transfer_received_bytes{{{relay_filter}}}[2m]))',
            "{{host}} <- {{peer}}",
            12,
            35,
            w=12,
            h=8,
            unit="Bps",
        ),
        bargauge(
            14,
            "Zeit seit letztem WireGuard-Handshake",
            f'topk(15, time() - openmain_netbird_peer_last_handshake_timestamp_seconds{{{relay_filter}}})',
            "{{host}} -> {{peer}}",
            0,
            43,
            w=24,
            h=8,
            unit="s",
        ),
    ]

    return {
        "annotations": {"list": []},
        "description": (
            "Zeigt anhand des lokalen NetBird Status der überwachten Clients, "
            "welcher Client mit welchem Peer über Relay verbunden ist und welchen Relay-Server er verwendet."
        ),
        "editable": False,
        "graphTooltip": 1,
        "id": None,
        "links": [],
        "panels": panels,
        "refresh": "30s",
        "schemaVersion": 41,
        "tags": ["netbird", "relay", "connections", "openmain"],
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
                    "datasource": ds(),
                    "definition": 'label_values(openmain_netbird_peer_connection_info{job="netbird-client-detail"},customer)',
                    "query": {
                        "qryType": 1,
                        "query": 'label_values(openmain_netbird_peer_connection_info{job="netbird-client-detail"},customer)',
                        "refId": "RelayCustomer",
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
                    "name": "source",
                    "type": "query",
                    "label": "Quelle",
                    "datasource": ds(),
                    "definition": (
                        'label_values(openmain_netbird_peer_connection_info{job="netbird-client-detail",'
                        'customer=~"$customer"},host)'
                    ),
                    "query": {
                        "qryType": 1,
                        "query": (
                            'label_values(openmain_netbird_peer_connection_info{job="netbird-client-detail",'
                            'customer=~"$customer"},host)'
                        ),
                        "refId": "RelaySource",
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
        "title": TITLE,
        "uid": UID,
        "version": 1,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    print(f"{output}: {TITLE} erzeugt")


if __name__ == "__main__":
    main()
