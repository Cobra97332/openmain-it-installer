#!/usr/bin/env bash
# Runs only inside the disposable Debian 13 CI container.
set -Eeuo pipefail
[[ "$(cat /etc/os-release)" == *'VERSION_ID="13"'* || "$(cat /etc/os-release)" == *'VERSION_ID=13'* ]]
[[ $EUID -eq 0 ]]
[[ $# -eq 1 && "$1" =~ ^[0-9a-f]{40}$ ]]
ref="$1"
mock=$(mktemp -d)
export PATH="$mock:$PATH"
for binary in netbird zabbix_agent2 visudo; do
  printf '#!/bin/sh\nexit 0\n' >"$mock/$binary"
  chmod +x "$mock/$binary"
done
cat >"$mock/nft" <<'EOF'
#!/bin/sh
[ "$1 $2 $3 $4" = "list table ip ${BINAT_TABLE:-openmain_binat}" ]
EOF
chmod +x "$mock/nft"
cat >"$mock/systemctl" <<'EOF'
#!/bin/sh
case "$1" in
  is-enabled|is-active) exit 1 ;;
  *) exit 0 ;;
esac
EOF
cat >"$mock/ip" <<'EOF'
#!/bin/sh
if [ "$2" = route ]; then
  echo '192.0.2.2 via 192.0.2.254 dev eth0 src 192.0.2.1'
else
  echo '2: eth0 inet 192.0.2.1/24 scope global eth0'
fi
EOF
chmod +x "$mock/systemctl" "$mock/ip"
install -d -m 0700 /var/lib/netbird-kundenrouter /etc/nftables.d /etc/zabbix /etc/netbird
printf '{"role":"primary","lan":"192.0.2.0/24","virtual":"10.30.2.0/24"}\n' >/var/lib/netbird-kundenrouter/state.json
printf 'table ip openmain_binat {}\n' >/etc/nftables.d/netbird-kundenrouter.nft
printf 'netbird-sentinel\n' >/etc/netbird/config.json
printf 'zabbix-sentinel\nServerActive=127.0.0.1:10051\n' >/etc/zabbix/zabbix_agent2.conf
before=$(sha256sum /var/lib/netbird-kundenrouter/state.json /etc/nftables.d/netbird-kundenrouter.nft /etc/netbird/config.json /etc/zabbix/zabbix_agent2.conf)
if python3 netbird-router/updates/retrofit.py --install --ref "$ref" 2>"$mock/ambiguous.log"; then
  echo 'Primary ohne --single wurde unerwartet installiert' >&2; exit 1
fi
[[ ! -e /etc/systemd/system/openmain-router-update.timer ]]
if BINAT_TABLE=none python3 netbird-router/updates/retrofit.py --install --ref "$ref" --single --no-auto-reboot 2>"$mock/no-binat.log"; then
  echo 'Fehlende BINAT-Tabelle unerwartet akzeptiert' >&2; exit 1
fi
grep -q 'Keine bestehende BINAT-Tabelle' "$mock/no-binat.log"
python3 netbird-router/updates/retrofit.py --install --ref "$ref" --single --no-auto-reboot | tee "$mock/first.log"
grep -Fxq 'ROLE=primary' /etc/openmain-router-update.conf
grep -Fxq 'TOPOLOGY=single' /etc/openmain-router-update.conf
grep -Fxq 'AUTO_REBOOT=false' /etc/openmain-router-update.conf
grep -Fxq 'OnCalendar=Sun *-*-* 04:00:00 Europe/Berlin' /etc/systemd/system/openmain-router-update.timer
grep -Fxq 'Persistent=true' /etc/systemd/system/openmain-router-update.timer
grep -Fxq 'RandomizedDelaySec=0' /etc/systemd/system/openmain-router-update.timer
first=$(sed -n 's/.*Sicherung //p' "$mock/first.log")
[[ -s "/var/backups/openmain-router-updates/$first/manifest.json" ]]
python3 netbird-router/updates/retrofit.py --install --ref "$ref" --single >/dev/null
grep -Fxq 'AUTO_REBOOT=false' /etc/openmain-router-update.conf
[[ "$(find /etc/systemd/system -maxdepth 1 -name 'openmain-router-update.timer' | wc -l)" -eq 1 ]]
after=$(sha256sum /var/lib/netbird-kundenrouter/state.json /etc/nftables.d/netbird-kundenrouter.nft /etc/netbird/config.json /etc/zabbix/zabbix_agent2.conf)
[[ "$before" == "$after" ]]

# Simulate a running update holding the exact lock used by router-update.sh.
installed=$(sha256sum /usr/local/sbin/openmain-router-update /etc/systemd/system/openmain-router-update.timer)
backups=$(find /var/backups/openmain-router-updates -mindepth 1 -maxdepth 1 -type d | wc -l)
exec 8>/run/lock/openmain-router-update.lock
flock -n 8
if python3 netbird-router/updates/retrofit.py --rollback "$first" 8>&- 2>"$mock/locked-rollback.log"; then
  echo 'Rollback waehrend Update unerwartet erfolgreich' >&2; exit 1
fi
grep -q 'keine Aenderungen vorgenommen' "$mock/locked-rollback.log"
if python3 netbird-router/updates/retrofit.py --install --ref "$ref" --single 8>&- 2>"$mock/locked-install.log"; then
  echo 'Parallele Nachruestung unerwartet erfolgreich' >&2; exit 1
fi
grep -q 'keine Aenderungen vorgenommen' "$mock/locked-install.log"
[[ "$installed" == "$(sha256sum /usr/local/sbin/openmain-router-update /etc/systemd/system/openmain-router-update.timer)" ]]
[[ "$backups" -eq "$(find /var/backups/openmain-router-updates -mindepth 1 -maxdepth 1 -type d | wc -l)" ]]
flock -u 8
exec 8>&-

python3 netbird-router/updates/retrofit.py --rollback "$first"
[[ ! -e /etc/systemd/system/openmain-router-update.timer ]]
[[ ! -e /usr/local/sbin/openmain-router-update ]]
[[ "$before" == "$(sha256sum /var/lib/netbird-kundenrouter/state.json /etc/nftables.d/netbird-kundenrouter.nft /etc/netbird/config.json /etc/zabbix/zabbix_agent2.conf)" ]]
python3 netbird-router/updates/retrofit.py --rollback "$first"
[[ ! -e /etc/systemd/system/openmain-router-update.timer ]]

# Paired backup: existing agent addresses determine the partner; no credentials change.
printf '{"role":"backup","lan":"192.0.2.0/24","virtual":"10.30.2.0/24"}\n' >/var/lib/netbird-kundenrouter/state.json
export BINAT_TABLE=mein_binat
printf 'table ip mein_binat {}\n' >/etc/nftables.d/netbird-kundenrouter.nft
printf 'zabbix-sentinel\nServerActive=192.0.2.1:10051;192.0.2.2:10051\n' >/etc/zabbix/zabbix_agent2.conf
before=$(sha256sum /var/lib/netbird-kundenrouter/state.json /etc/nftables.d/netbird-kundenrouter.nft /etc/netbird/config.json /etc/zabbix/zabbix_agent2.conf)
python3 netbird-router/updates/retrofit.py --install --ref "$ref" | tee "$mock/backup.log"
grep -Fxq 'OnCalendar=Sun *-*-* 05:00:00 Europe/Berlin' /etc/systemd/system/openmain-router-update.timer
grep -Fxq 'PARTNER_LAN_IP=192.0.2.2' /etc/openmain-router-update.conf
[[ "$before" == "$(sha256sum /var/lib/netbird-kundenrouter/state.json /etc/nftables.d/netbird-kundenrouter.nft /etc/netbird/config.json /etc/zabbix/zabbix_agent2.conf)" ]]
echo 'Retrofit: Primary single, Backup pair, idempotence, rollback and preserved router config PASS'
