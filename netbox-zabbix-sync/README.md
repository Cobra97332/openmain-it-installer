# NetBox Zabbix Sync

Safe Zabbix host import into NetBox. Creates placeholder devices `zbx-<hostid>` (status `planned`) and does not change existing devices or NetBox IPAM. Default `DRY_RUN=true`.

## Network (verified 2026-10-08)

- NetBox NetBird sidecar: `100.107.6.83`.
- **Embedded NetBird reverse-proxy peer:** `100.107.239.95:443` (reachable over `wt0`).
- `zabbix.openmain-it.de` remains the HTTPS hostname for correct SNI, Host header, and TLS certificate validation.
- A verified request through that peer returned HTTP 200 with Zabbix API version 7.4.15, while the NetBird-only group restriction was enabled.
- The separate server at `10.20.0.18` does **not** run a NetBird client. Do not route this job to the management server LAN address.
- `compose.sync.yml` provides a per-container `extra_hosts` mapping to the proxy-peer address. This does **not** change system DNS. If the embedded proxy peer's NetBird IP changes, adjust this mapping after re-verifying it.

## Prerequisites

Existing `/opt/netbox/docker-compose.yml` provides `netbird` and `netbox`, with NetBox using `network_mode: service:netbird`. The NetBox API must work at `http://127.0.0.1:8080` in the shared namespace.

## Install or update from GitHub

```bash
cd /opt/netbox
git clone --depth 1 https://github.com/Cobra97332/openmain-it-installer.git /tmp/openmain-it-installer 2>/dev/null || git -C /tmp/openmain-it-installer pull --ff-only
install -d -m 700 ./netbox-sync ./env
cp -a compose.sync.yml "compose.sync.yml.bak.$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
cp /tmp/openmain-it-installer/netbox-zabbix-sync/compose.sync.yml ./compose.sync.yml
cp /tmp/openmain-it-installer/netbox-zabbix-sync/netbox-sync/sync.py ./netbox-sync/sync.py
if [ ! -e env/sync.env ]; then
  cp /tmp/openmain-it-installer/netbox-zabbix-sync/sync.env.example env/sync.env
fi
chmod 600 env/sync.env
nano env/sync.env
docker compose -f docker-compose.yml -f compose.sync.yml config -q
docker compose -f docker-compose.yml -f compose.sync.yml up -d --no-deps --force-recreate netbox-sync
docker compose -f docker-compose.yml -f compose.sync.yml logs --tail=100 netbox-sync
```

In `env/sync.env`, use `ZABBIX_URL=https://zabbix.openmain-it.de`, `VERIFY_TLS=true`, and `DRY_RUN=true`. Supply separate restricted Zabbix and NetBox API tokens. Never commit secrets. A Zabbix token must have read access to intended host groups; a NetBox token needs read/create permissions for placeholder records.

## Verify networking

```bash
docker compose -f docker-compose.yml -f compose.sync.yml exec -T netbox-sync python -c 'import socket; print(socket.gethostbyname("zabbix.openmain-it.de"))'
docker compose -f docker-compose.yml -f compose.sync.yml exec -T netbox-sync python -c 'import urllib.request; print(urllib.request.urlopen("https://zabbix.openmain-it.de", timeout=10).status)'
```

The first command must print `100.107.239.95`. The second checks HTTPS reachability only; depending on how the service routes `/`, a 403/404 on the root path is not an API failure. Review the sync logs for the real API result.

Review `DRY_RUN=true` output before enabling writes. Make a full NetBox backup first. The initial importer only creates unclassified placeholder devices, **not real VM/device classifications or IPAM records**.

**Important:** Always include both compose files for these commands. `docker compose up -d` without `-f compose.sync.yml` will not load the sync service.
