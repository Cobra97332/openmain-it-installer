# NetBox Zabbix Sync (safe initial import)

Syncs Zabbix host records into **unclassified placeholder** NetBox devices named `zbx-<hostid>`, status `planned`. It does **not** classify VMs vs physical devices, configure IPAM, modify existing NetBox devices, or delete any records. Start with `DRY_RUN=true`.

## Prerequisites

Existing `/opt/netbox/docker-compose.yml` must provide `netbird` and `netbox`, with NetBox using `network_mode: service:netbird`; `http://127.0.0.1:8080` must reach NetBox in that shared namespace. Keep the same Compose project name and volumes.

## Installation on Docker host

```bash
cd /opt/netbox
git clone --depth 1 https://github.com/Cobra97332/openmain-it-installer.git /tmp/openmain-it-installer
install -d -m 700 ./netbox-sync ./env
cp /tmp/openmain-it-installer/netbox-zabbix-sync/compose.sync.yml ./compose.sync.yml
cp /tmp/openmain-it-installer/netbox-zabbix-sync/netbox-sync/sync.py ./netbox-sync/sync.py
if [ ! -e env/sync.env ]; then
  cp /tmp/openmain-it-installer/netbox-zabbix-sync/sync.env.example env/sync.env
fi
chmod 600 env/sync.env
nano env/sync.env
docker compose -f docker-compose.yml -f compose.sync.yml config -q
docker compose -f docker-compose.yml -f compose.sync.yml up -d netbox-sync
docker compose -f docker-compose.yml -f compose.sync.yml logs -f --tail=100 netbox-sync
```

Use dedicated read-only Zabbix API token with access only to required host groups. Create a restricted NetBox API token with read/create permissions for sites, manufacturers, device types, device roles, tags and devices. Don't commit credentials. Keep TLS verification enabled.

Review dry-run output and make a NetBox backup before switching `DRY_RUN=false`. Restart with `docker compose -f docker-compose.yml -f compose.sync.yml up -d --force-recreate netbox-sync`.

**Important:** A second Compose file must always be included in commands. `docker compose up -d` by itself won't load `compose.sync.yml`. Also, Zabbix group ownership is currently *only* recorded in the placeholder description, not mapped to NetBox tenants.
