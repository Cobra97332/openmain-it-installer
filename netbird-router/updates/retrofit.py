#!/usr/bin/env python3
"""Install or roll back only the OpenMain router update component."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid
from datetime import datetime, timezone

REPOSITORY = "Cobra97332/openmain-it-installer"
HELPERS = {
    "install-router-updates.sh": "c55896501a70ca245f420718b4cd7dcd5f653b3def989e452ec69428cadb1bcc",
    "router-update.sh": "4563f9b9ae8f881c39b3b9fbf1671b139926be9aa601380eaa7298b7399c6791",
    "router-update-health.sh": "5983f6281c8ffbd766ee3d793ba13a96c6508d1753e8dcca0ee26c2bd8721c50",
    "router-update-zabbix-query.py": "b74051122d4d712149d7110ffb856818a06982ea4b7d2520bd576203be3273e2",
}
TARGETS = (
    "/usr/local/sbin/openmain-router-update",
    "/usr/local/sbin/openmain-router-update-health",
    "/usr/local/sbin/openmain-zabbix-query",
    "/etc/openmain-router-update.conf",
    "/etc/systemd/system/openmain-router-update.service",
    "/etc/systemd/system/openmain-router-update.timer",
    "/etc/sudoers.d/openmain-router-update-health",
    "/etc/zabbix/zabbix_agent2.d/openmain-router-update.conf",
)
PRESERVED = (
    "/var/lib/netbird-kundenrouter/state.json",
    "/etc/nftables.d/netbird-kundenrouter.nft",
    "/etc/nftables.conf",
    "/etc/netbird/config.json",
    "/etc/zabbix/zabbix_agent2.conf",
    "/etc/zabbix/zabbix_proxy.conf",
    "/etc/zabbix/zabbix_proxy.psk",
)
BACKUPS = Path("/var/backups/openmain-router-updates")


def fail(message):
    raise RuntimeError(message)


def command(*args, check=True):
    return subprocess.run(args, check=check, text=True, capture_output=True)


def digest(path):
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint():
    return {path: digest(Path(path)) for path in PRESERVED}


def platform_check():
    release = Path("/etc/os-release").read_text()
    if not re.search(r'^ID=(?:"?)(debian|raspbian)(?:"?)$', release, re.M) or not re.search(
        r'^VERSION_ID=(?:"?)13(?:"?)$', release, re.M
    ):
        fail("Nur Debian 13 beziehungsweise Raspberry Pi OS 13 wird unterstützt")
    arch = command("dpkg", "--print-architecture").stdout.strip()
    if arch not in ("amd64", "arm64"):
        fail(f"Nicht unterstützte Architektur: {arch}")
    if not shutil.which("systemctl") or not shutil.which("netbird"):
        fail("systemd oder vorhandener NetBird-Client fehlt")
    state_path = Path("/var/lib/netbird-kundenrouter/state.json")
    if not state_path.is_file() or not Path("/etc/nftables.d/netbird-kundenrouter.nft").is_file():
        fail("Aktivierter Routerzustand oder BINAT-Datei fehlt; keine Neuinstallation")
    state = json.loads(state_path.read_text())
    if state.get("role") not in ("primary", "backup") or not state.get("lan") or not state.get("virtual"):
        fail("Rolle oder BINAT-Zuordnung im Routerzustand unklar")
    command("nft", "list", "table", "ip", "mein_binat")
    virt = command("systemd-detect-virt", check=False).stdout.strip() or "bare-metal"
    model = Path("/proc/device-tree/model")
    if model.is_file() and b"Raspberry Pi" in model.read_bytes() and arch != "arm64":
        fail("Raspberry Pi erfordert ARM64")
    return state, arch, virt


def detect_pair(role, explicit_single=False):
    config = Path("/etc/zabbix/zabbix_agent2.conf")
    active = ""
    if config.is_file():
        match = re.search(r'^\s*ServerActive\s*=\s*(.*?)\s*$', config.read_text(), re.M)
        active = match.group(1) if match else ""
    if ";" in active:
        if explicit_single:
            fail("Zabbix-Konfiguration zeigt ein Routerpaar; --single unzulässig")
        parts = active.split(";")
        if len(parts) != 2:
            fail("Partnerzuordnung nicht eindeutig")
        try:
            addresses = [str(ipaddress.IPv4Address(p.removesuffix(":10051"))) for p in parts]
        except ipaddress.AddressValueError:
            fail("Ungültige Router-LAN-IP in ServerActive")
        if any(not p.endswith(":10051") for p in parts) or addresses[0] == addresses[1]:
            fail("Partnerzuordnung nicht eindeutig")
        own = set(re.findall(r'\binet\s+(\d+\.\d+\.\d+\.\d+)/', command("ip", "-4", "-o", "addr", "show").stdout))
        if len(own.intersection(addresses)) != 1:
            fail("Eigene LAN-IP in Routerpaar nicht eindeutig")
        if not shutil.which("zabbix_agent2"):
            fail("Zabbix Agent 2 fehlt für die Partnerprüfung")
        return ",".join(addresses)
    if role == "backup":
        fail("Backup ohne nachgewiesene Partnerzuordnung")
    if not explicit_single:
        fail("Primary ohne eindeutige Partnerzuordnung: --single nur für nachweislichen Einzelrouter")
    return ""


def snapshot():
    BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(BACKUPS, 0o700)
    identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    directory = BACKUPS / identifier
    directory.mkdir(mode=0o700)
    timer = "openmain-router-update.timer"
    manifest = {
        "targets": {},
        "preserved": fingerprint(),
        "timer_enabled": command("systemctl", "is-enabled", timer, check=False).returncode == 0,
        "timer_active": command("systemctl", "is-active", timer, check=False).returncode == 0,
    }
    for index, path in enumerate(TARGETS):
        source = Path(path)
        if source.is_symlink() or (source.exists() and not source.is_file()):
            fail(f"Unerwarteter Dateityp: {path}")
        present = source.is_file()
        manifest["targets"][path] = {"present": present, "sha256": digest(source)}
        if present:
            shutil.copy2(source, directory / str(index))
    protected = directory / "preserved"
    protected.mkdir(mode=0o700)
    for index, path in enumerate(PRESERVED):
        source = Path(path)
        if source.is_file():
            shutil.copy2(source, protected / str(index))
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    os.chmod(directory / "manifest.json", 0o600)
    return directory, manifest


def restore(directory, manifest):
    command("systemctl", "disable", "--now", "openmain-router-update.timer", check=False)
    for index, path in enumerate(TARGETS):
        target = Path(path)
        item = manifest["targets"][path]
        if item["present"]:
            saved = directory / str(index)
            if digest(saved) != item["sha256"]:
                fail(f"Sicherung beschädigt: {saved}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(saved, target)
        elif target.exists() or target.is_symlink():
            target.unlink()
    command("systemctl", "daemon-reload")
    if manifest["timer_enabled"]:
        command("systemctl", "enable", "openmain-router-update.timer")
    if manifest["timer_active"]:
        command("systemctl", "start", "openmain-router-update.timer")
    command("systemctl", "try-restart", "zabbix-agent2.service", check=False)


def download(ref, directory):
    if not re.fullmatch(r"[0-9a-f]{40}", ref):
        fail("Ein vollständiger, fester Commit-SHA ist erforderlich")
    for name, expected in HELPERS.items():
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            fail("Interne SHA256-Prüfsummen fehlen")
        url = f"https://raw.githubusercontent.com/{REPOSITORY}/{ref}/netbird-router/pve/{name}"
        with urllib.request.urlopen(url, timeout=20) as response:
            data = response.read(200000)
        if hashlib.sha256(data).hexdigest() != expected:
            fail(f"SHA256-Abweichung: {name}")
        path = directory / name
        path.write_bytes(data)
        os.chmod(path, 0o700)


def install(ref, single):
    state, arch, virt = platform_check()
    addresses = detect_pair(state["role"], single)
    with tempfile.TemporaryDirectory(prefix="openmain-update-") as temp:
        download(ref, Path(temp))
        backup, manifest = snapshot()
        try:
            args = ["bash", str(Path(temp) / "install-router-updates.sh"), "--role", state["role"]]
            if addresses:
                args += ["--agent-group-ips", addresses]
            result = command(*args, check=False)
            if result.returncode:
                fail(f"Update-Installer fehlgeschlagen: {result.stderr.strip()[-2000:]}")
            if fingerprint() != manifest["preserved"]:
                fail("Bestehende Routerkonfiguration hat sich verändert")
        except Exception:
            restore(backup, manifest)
            raise
    print(f"Nachrüstung installiert: {state['role']}, {arch}, {virt}; Sicherung {backup.name}")


def rollback(identifier):
    if not re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", identifier):
        fail("Ungültige Sicherungs-ID")
    directory = BACKUPS / identifier
    manifest = json.loads((directory / "manifest.json").read_text())
    if set(manifest["targets"]) != set(TARGETS):
        fail("Sicherungsmanifest passt nicht zu diesem Installer")
    restore(directory, manifest)
    print(f"Update-Komponente aus Sicherung {identifier} zurückgesetzt")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--install", action="store_true")
    action.add_argument("--rollback", metavar="SICHERUNGS-ID")
    parser.add_argument("--ref", help="vollständiger Commit-SHA für die Update-Helfer")
    parser.add_argument("--single", action="store_true", help="Primary ist nachweislich ein Einzelrouter")
    args = parser.parse_args()
    if os.geteuid() != 0:
        fail("Root-Rechte erforderlich")
    if args.install:
        if not args.ref:
            fail("--ref fehlt")
        install(args.ref, args.single)
    else:
        rollback(args.rollback)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Nachrüstung fehlgeschlagen: {exc}", file=sys.stderr)
        sys.exit(1)
