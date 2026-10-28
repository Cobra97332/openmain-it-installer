#!/usr/bin/env python3
"""Isolated kernel gate for the PVE BINAT gateway policy boundary.

Run in a disposable Docker container with --network none, NET_ADMIN, NET_RAW,
ip_forward=1 and OPENMAIN_ISOLATED_TEST=1. This models the NetBird 0.81 route
chain, including its established shortcut. It must exit nonzero if revocation
leaves either direction of an established tuple active.
"""
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

if os.environ.get("OPENMAIN_ISOLATED_TEST") != "1" or not Path("/.dockerenv").exists():
    raise SystemExit("Requires explicitly isolated Docker")

def run(*args):
    return subprocess.check_output(args, stderr=subprocess.STDOUT, text=True)

def mac(iface):
    return bytes.fromhex(Path("/sys/class/net", iface, "address").read_text().strip().replace(":", ""))

def send(peer, router, src, dst, sport, dport):
    payload = b"openmain-pve-policy-gate"
    udp = struct.pack("!HHHH", sport, dport, 8 + len(payload), 0) + payload
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 121, 0, 64, 17, 0,
                     socket.inet_aton(src), socket.inet_aton(dst))
    checksum = sum(struct.unpack("!10H", ip))
    while checksum >> 16:
        checksum = (checksum & 0xffff) + (checksum >> 16)
    ip = ip[:10] + struct.pack("!H", ~checksum & 0xffff) + ip[12:]
    with socket.socket(socket.AF_PACKET, socket.SOCK_RAW) as sock:
        sock.bind((peer, 0))
        sock.send(mac(router) + mac(peer) + b"\x08\x00" + ip + udp)

def observed(sock, src, dst, timeout):
    sock.settimeout(timeout)
    try:
        while True:
            data, metadata = sock.recvfrom(4096)
            if metadata[2] == socket.PACKET_OUTGOING or data[12:14] != b"\x08\x00":
                continue
            if not data.endswith(b"openmain-pve-policy-gate"):
                continue
            if socket.inet_ntoa(data[26:30]) == src and socket.inet_ntoa(data[30:34]) == dst:
                return True
    except socket.timeout:
        return False

for router, peer in (("wt0", "overlay_peer"), ("transit0", "transit_peer")):
    run("ip", "link", "add", router, "type", "veth", "peer", "name", peer)
    run("ip", "link", "set", router, "up")
    run("ip", "link", "set", peer, "up")
run("ip", "addr", "add", "100.64.0.1/10", "dev", "wt0")
run("ip", "addr", "add", "10.30.1.1/24", "dev", "transit0")
for addr, router, peer in (
    ("100.64.0.10", "wt0", "overlay_peer"),
    ("100.64.0.11", "wt0", "overlay_peer"),
    ("10.30.1.20", "transit0", "transit_peer"),
):
    run("ip", "neigh", "replace", addr, "lladdr",
        ":".join(f"{b:02x}" for b in mac(peer)), "nud", "permanent", "dev", router)
# Endpoint devices are only frame injectors/observers; prevent forwarding loops.
run("nft", "add", "table", "inet", "endpoints")
run("nft", "add", "chain", "inet", "endpoints", "ingress",
    "{ type filter hook prerouting priority -450; policy accept; }")
for peer in ("overlay_peer", "transit_peer"):
    run("nft", "add", "rule", "inet", "endpoints", "ingress", "iifname", peer, "drop")

run("nft", "add", "table", "ip", "netbird_fixture")
run("nft", "add", "set", "ip", "netbird_fixture", "approved", "{ type ipv4_addr; }")
run("nft", "add", "element", "ip", "netbird_fixture", "approved", "{ 100.64.0.10 }")
run("nft", "add", "chain", "ip", "netbird_fixture", "forward",
    "{ type filter hook forward priority filter; policy drop; }")
run("nft", "add", "rule", "ip", "netbird_fixture", "forward",
    "iifname", "wt0", "ct", "state", "established,related", "accept")
run("nft", "add", "rule", "ip", "netbird_fixture", "forward",
    "iifname", "wt0", "ip", "saddr", "@approved", "ip", "daddr",
    "10.30.1.0/24", "accept")
run("nft", "add", "rule", "ip", "netbird_fixture", "forward",
    "oifname", "wt0", "ct", "state", "established,related", "accept")

transit = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
overlay = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
transit.bind(("transit_peer", 0))
overlay.bind(("overlay_peer", 0))
try:
    send("overlay_peer", "wt0", "100.64.0.10", "10.30.1.20", 46000, 8006)
    assert observed(transit, "100.64.0.10", "10.30.1.20", 2), "Allowed peer blocked"
    send("overlay_peer", "wt0", "100.64.0.11", "10.30.1.20", 46010, 8006)
    assert not observed(transit, "100.64.0.11", "10.30.1.20", 0.5), "Denied peer passed"
    time.sleep(0.2)
    send("transit_peer", "transit0", "10.30.1.20", "100.64.0.10", 8006, 46000)
    assert observed(overlay, "10.30.1.20", "100.64.0.10", 2), "Return packet blocked"
    run("nft", "delete", "element", "ip", "netbird_fixture", "approved", "{ 100.64.0.10 }")
    send("overlay_peer", "wt0", "100.64.0.10", "10.30.1.20", 46001, 8006)
    assert not observed(transit, "100.64.0.10", "10.30.1.20", 0.5), "Revoked new tuple passed"
    failures = []
    send("overlay_peer", "wt0", "100.64.0.10", "10.30.1.20", 46000, 8006)
    if observed(transit, "100.64.0.10", "10.30.1.20", 0.5):
        failures.append("P1_ESTABLISHED_CONNTRACK_BYPASSES_WITHDRAWAL")
    send("transit_peer", "transit0", "10.30.1.20", "100.64.0.10", 8006, 46000)
    if observed(overlay, "10.30.1.20", "100.64.0.10", 0.5):
        failures.append("P1_REPLY_ALLOWED_VIA_NETBIRD_EXTERNAL_ESTABLISHED")
    print("PASS: allowed, denied and newly revoked flows", flush=True)
    for failure in failures:
        print("RELEASE BLOCKED: " + failure, flush=True)
    assert not failures, "Established-flow withdrawal is not enforced"
finally:
    transit.close()
    overlay.close()