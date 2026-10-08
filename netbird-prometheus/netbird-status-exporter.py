#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def prom_escape(value):
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace('"', '\\"')
    )


def timestamp(value):
    if not value:
        return 0.0
    text = str(value)
    if text.startswith("0001-01-01"):
        return 0.0
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.timestamp()
    except ValueError:
        return 0.0


def netbird_status(netbird_bin):
    proc = subprocess.run(
        [netbird_bin, "status", "--json"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
    )
    return json.loads(proc.stdout)


def render_metrics(netbird_bin):
    data = netbird_status(netbird_bin)
    lines = [
        "# HELP openmain_netbird_status_exporter_up Whether the detailed NetBird status exporter could query the local daemon.",
        "# TYPE openmain_netbird_status_exporter_up gauge",
        "openmain_netbird_status_exporter_up 1",
        "# HELP openmain_netbird_peer_connection_info Connected NetBird peer relation with connection type and relay address.",
        "# TYPE openmain_netbird_peer_connection_info gauge",
        "# HELP openmain_netbird_peer_transfer_received_bytes WireGuard bytes received from a connected peer.",
        "# TYPE openmain_netbird_peer_transfer_received_bytes gauge",
        "# HELP openmain_netbird_peer_transfer_sent_bytes WireGuard bytes sent to a connected peer.",
        "# TYPE openmain_netbird_peer_transfer_sent_bytes gauge",
        "# HELP openmain_netbird_peer_last_handshake_timestamp_seconds Unix timestamp of the last WireGuard handshake.",
        "# TYPE openmain_netbird_peer_last_handshake_timestamp_seconds gauge",
    ]

    details = ((data.get("peers") or {}).get("details") or [])
    relay_count = 0
    connected_count = 0

    for peer in details:
        if str(peer.get("status") or "").lower() != "connected":
            continue

        conn_type_raw = str(peer.get("connectionType") or "").strip()
        conn_type = conn_type_raw.lower()
        if conn_type == "relayed":
            conn_type = "relay"
        elif conn_type == "p2p":
            conn_type = "p2p"
        elif not conn_type:
            conn_type = "unknown"

        if conn_type == "relay":
            relay_count += 1
        connected_count += 1

        relay_address = peer.get("relayAddress") or ""
        if conn_type != "relay":
            relay_address = ""

        labels = {
            "peer": peer.get("fqdn") or peer.get("netbirdIp") or "unknown",
            "peer_ip": peer.get("netbirdIp") or "",
            "connection_type": conn_type,
            "relay_address": relay_address,
        }
        label_text = ",".join(
            f'{key}="{prom_escape(value)}"' for key, value in labels.items()
        )

        lines.append(f"openmain_netbird_peer_connection_info{{{label_text}}} 1")
        lines.append(
            "openmain_netbird_peer_transfer_received_bytes"
            f"{{{label_text}}} {int(peer.get('transferReceived') or 0)}"
        )
        lines.append(
            "openmain_netbird_peer_transfer_sent_bytes"
            f"{{{label_text}}} {int(peer.get('transferSent') or 0)}"
        )

        handshake = timestamp(peer.get("lastWireguardHandshake"))
        lines.append(
            "openmain_netbird_peer_last_handshake_timestamp_seconds"
            f"{{{label_text}}} {handshake:.3f}"
        )

    lines.extend(
        [
            "# HELP openmain_netbird_connected_peer_relations Number of connected peer relations visible from this client.",
            "# TYPE openmain_netbird_connected_peer_relations gauge",
            f"openmain_netbird_connected_peer_relations {connected_count}",
            "# HELP openmain_netbird_relay_peer_relations Number of relayed peer relations visible from this client.",
            "# TYPE openmain_netbird_relay_peer_relations gauge",
            f"openmain_netbird_relay_peer_relations {relay_count}",
        ]
    )

    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    netbird_bin = "netbird"

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        if self.path not in ("/metrics", "/metrics/"):
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"not found\n")
            return

        try:
            body = render_metrics(self.netbird_bin).encode("utf-8")
            code = 200
        except Exception as exc:
            body = (
                "# HELP openmain_netbird_status_exporter_up Whether the detailed NetBird status exporter could query the local daemon.\n"
                "# TYPE openmain_netbird_status_exporter_up gauge\n"
                "openmain_netbird_status_exporter_up 0\n"
                f"# ERROR {type(exc).__name__}: {str(exc).replace(chr(10), ' ')}\n"
            ).encode("utf-8")
            code = 500

        self.send_response(code)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(
        description="Expose per-peer NetBird connection details as Prometheus metrics."
    )
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9192)
    parser.add_argument("--netbird-bin", default="netbird")
    args = parser.parse_args()

    if not (1 <= args.port <= 65535):
        raise SystemExit("invalid port")

    Handler.netbird_bin = args.netbird_bin
    server = ThreadingHTTPServer((args.listen, args.port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
