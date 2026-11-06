#!/usr/bin/env python3
"""Isolated update runner tests; commands and all absolute paths are redirected."""
import os
import pathlib
import json
import subprocess
import tempfile
import unittest

SOURCE = pathlib.Path(__file__).with_name("router-update.sh")


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.config = self.root / "config"
        self.marker = self.root / "reboot-required"
        self.log = self.root / "calls"
        self.lock = self.root / "lock"
        script = SOURCE.read_text().replace(
            "/etc/openmain-router-update.conf", str(self.config)
        ).replace("/run/lock/openmain-router-update.lock", str(self.lock)).replace(
            "/var/run/reboot-required", str(self.marker)
        ).replace(
            "/usr/local/sbin/openmain-zabbix-query", str(self.bin / "zabbix_get")
        )
        self.script = self.root / "update.sh"
        self.script.write_text(script)
        self.script.chmod(0o755)
        for name, body in {
            "apt-get": 'echo "apt:$*" >> "$CALLS"; [[ " $* " != *" ${APT_FAIL:-never-match} "* ]]',
            "systemctl": 'echo "systemctl:$*" >> "$CALLS"',
            "date": 'if [[ "$*" == *"+%u"* ]]; then echo "${DAY:-7}"; else echo "${HOUR:-04}"; fi',
            "zabbix_get": 'if [[ "$*" == *"openmain.router.role"* ]]; then echo "${PARTNER_ROLE:-backup}"; else echo "${PARTNER_READY-1}"; fi',
        }.items():
            path = self.bin / name
            path.write_text("#!/usr/bin/env bash\n" + body + "\n")
            path.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update(PATH=str(self.bin) + os.pathsep + self.env["PATH"], CALLS=str(self.log))

    def run_update(self, role="primary", topology="single", marker=True, **env):
        self.config.write_text(f"ROLE={role}\nTOPOLOGY={topology}\nPARTNER_LAN_IP=192.0.2.2\n")
        if marker:
            self.marker.touch()
        self.env.update(env)
        result = subprocess.run(["bash", str(self.script)], env=self.env, capture_output=True, text=True)
        return result, self.log.read_text() if self.log.exists() else ""

    def test_primary_window(self):
        result, calls = self.run_update()
        self.assertEqual(result.returncode, 0)
        self.assertIn("apt:", calls)
        self.assertIn("systemctl:reboot", calls)

    def test_backup_window(self):
        result, calls = self.run_update(role="backup", HOUR="05")
        self.assertEqual(result.returncode, 0)
        self.assertIn("systemctl:reboot", calls)

    def test_no_reboot_marker(self):
        _, calls = self.run_update(marker=False)
        self.assertNotIn("reboot", calls)

    def test_partner_ready(self):
        _, calls = self.run_update(topology="pair")
        self.assertIn("systemctl:reboot", calls)

    def test_partner_unreachable(self):
        _, calls = self.run_update(topology="pair", PARTNER_READY="")
        self.assertNotIn("reboot", calls)

    def test_partner_routing_failed(self):
        _, calls = self.run_update(topology="pair", PARTNER_READY="0")
        self.assertNotIn("reboot", calls)

    def test_update_failure(self):
        result, calls = self.run_update(APT_FAIL="upgrade")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("reboot", calls)

    def test_apt_update_failure(self):
        result, calls = self.run_update(APT_FAIL="update")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("upgrade -y", calls)
        self.assertNotIn("reboot", calls)

    def test_catchup(self):
        _, calls = self.run_update(DAY="1")
        self.assertIn("apt:", calls)
        self.assertNotIn("reboot", calls)

    def test_backup_requires_primary(self):
        _, calls = self.run_update(role="backup", topology="pair", HOUR="05", PARTNER_ROLE="backup")
        self.assertNotIn("reboot", calls)

    def test_primary_outage_does_not_reboot_backup(self):
        _, calls = self.run_update(role="backup", topology="pair", HOUR="05", PARTNER_ROLE="primary", PARTNER_READY="0")
        self.assertIn("upgrade -y", calls)
        self.assertNotIn("reboot", calls)

    def test_roles_do_not_reboot_in_same_hour(self):
        _, calls = self.run_update(role="primary", HOUR="05")
        self.assertNotIn("reboot", calls)
        self.log.unlink()
        _, calls = self.run_update(role="backup", HOUR="04")
        self.assertNotIn("reboot", calls)

    def test_install_twice_keeps_one_timer(self):
        source = pathlib.Path(__file__).with_name("install-router-updates.sh").read_text()
        state = self.root / "state.json"
        state.write_text(json.dumps({"role": "primary"}))
        mappings = {
            "/var/lib/netbird-kundenrouter/state.json": str(state),
            "/etc/zabbix/zabbix_agent2.d": str(self.root / "agent"),
            "/etc/sudoers.d": str(self.root / "sudoers"),
            "/etc/systemd/system": str(self.root / "systemd"),
            "/etc/openmain-router-update.conf": str(self.config),
            "/usr/local/sbin": str(self.root / "sbin"),
            "[[ $EUID -eq 0 ]]": "true",
        }
        for old, new in mappings.items():
            source = source.replace(old, new)
        script = self.root / "install-router-updates.sh"
        script.write_text(source)
        script.chmod(0o755)
        (self.root / "sbin").mkdir()
        (self.root / "systemd").mkdir()
        (self.root / "sudoers").mkdir()
        for helper in ("router-update.sh", "router-update-health.sh", "router-update-zabbix-query.py"):
            (self.root / helper).write_bytes(pathlib.Path(__file__).with_name(helper).read_bytes())
        for name, body in {
            "ip": 'echo "192.0.2.1 via 192.0.2.254 dev eth0 src 192.0.2.1"',
            "timedatectl": 'echo Europe/Berlin',
            "systemd-analyze": 'exit 0',
            "visudo": 'exit 0',
            "zabbix_agent2": 'exit 0',
        }.items():
            path = self.bin / name
            path.write_text("#!/usr/bin/env bash\n" + body + "\n")
            path.chmod(0o755)
        snapshot = None
        for _ in range(2):
            result = subprocess.run(
                ["bash", str(script), "--role", "primary", "--agent-group-ips", "192.0.2.1,192.0.2.2"],
                env=self.env, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            current = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file() and ("systemd" in p.parts or "agent" in p.parts)}
            if snapshot is not None:
                self.assertEqual(snapshot, current)
            snapshot = current
            # The CI user is unprivileged; root can overwrite the 0440 sudoers file.
            (self.root / "sudoers" / "openmain-router-update-health").chmod(0o644)
        self.assertEqual(len(list((self.root / "systemd").glob("*.timer"))), 1)
        timer = (self.root / "systemd" / "openmain-router-update.timer").read_text()
        self.assertIn("OnCalendar=Sun *-*-* 04:00:00 Europe/Berlin", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("RandomizedDelaySec=0", timer)
        self.assertIn("AccuracySec=1us", timer)
        state.write_text(json.dumps({"role": "backup"}))
        result = subprocess.run(["bash", str(script), "--role", "backup"], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Backup benötigt", result.stderr)
        result = subprocess.run(
            ["bash", str(script), "--role", "backup", "--agent-group-ips", "192.0.2.1,192.0.2.2"],
            env=self.env, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        timer = (self.root / "systemd" / "openmain-router-update.timer").read_text()
        self.assertIn("OnCalendar=Sun *-*-* 05:00:00 Europe/Berlin", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("RandomizedDelaySec=0", timer)
        self.assertIn("AccuracySec=1us", timer)


if __name__ == "__main__":
    unittest.main()
