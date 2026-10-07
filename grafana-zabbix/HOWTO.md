# HOWTO - Grafana mit Zabbix automatisch aufbauen

## 1. Zabbix-Templates

Empfohlene Basis:

| System | Zabbix-Template |
| --- | --- |
| Proxmox VE | offizielles Proxmox-VE-Template |
| Dell iDRAC | offizielles Dell-iDRAC-SNMP-Template |
| OPNsense | OPNsense-Community-Template plus Zabbix Agent |
| QNAP | QNAP-SNMP-Template, möglichst SNMPv3 |
| Synology | Synology/SNMP-Template, möglichst SNMPv3 |
| generisches NAS | passendes Vendor-Template oder Linux/SNMP-Basis |

Die Dashboards arbeiten mit Regex-Filtern auf Zabbix-Item-Namen. Je sauberer die Templates benannt sind, desto besser funktioniert die automatische Darstellung.

## 2. Zabbix-Metadaten automatisch synchronisieren

Management-Token setzen:

~~~bash
export ZABBIX_ADMIN_API_TOKEN='...'
export ZABBIX_API_URL='https://zabbix.openmain-it.de/api_jsonrpc.php'
~~~

Testlauf:

~~~bash
python3 sync-zabbix-groups.py --dry-run --verbose
~~~

Produktiver Lauf:

~~~bash
python3 sync-zabbix-groups.py
~~~

Danach sollten die Gruppen `OpenMain/...` in Zabbix existieren.

## 3. Regelmäßigen Sync per systemd installieren

~~~bash
install -d -m 0755 /opt/openmain-grafana-zabbix
install -m 0755 sync-zabbix-groups.py /opt/openmain-grafana-zabbix/
install -m 0644 systemd/openmain-zabbix-groups.service /etc/systemd/system/
install -m 0644 systemd/openmain-zabbix-groups.timer /etc/systemd/system/
~~~

Umgebungsdatei anlegen:

~~~bash
install -m 0600 /dev/null /etc/openmain-zabbix-metadata.env
nano /etc/openmain-zabbix-metadata.env
~~~

Inhalt:

~~~ini
ZABBIX_API_URL=https://zabbix.openmain-it.de/api_jsonrpc.php
ZABBIX_ADMIN_API_TOKEN=HIER_TOKEN_EINTRAGEN
OPENMAIN_CRITICAL_PLATFORMS=opnsense,pve,idrac,nas,qnap,synology
~~~

Aktivieren:

~~~bash
systemctl daemon-reload
systemctl enable --now openmain-zabbix-groups.timer
systemctl start openmain-zabbix-groups.service
journalctl -u openmain-zabbix-groups.service -n 100 --no-pager
~~~

## 4. Grafana-Datasource-UID

Empfohlen:

~~~text
zabbix-main
~~~

Falls deine vorhandene Zabbix-Datasource eine andere UID hat:

~~~bash
python3 generate-dashboards.py \
  --datasource-uid DEINE_UID \
  --output /var/lib/grafana/dashboards/openmain
~~~

## 5. Dashboard-Provisioning

Provider-Datei unter `/etc/grafana/provisioning/dashboards/openmain.yaml`:

~~~yaml
apiVersion: 1

providers:
  - name: OpenMain Zabbix
    orgId: 1
    folder: OpenMain Monitoring
    folderUid: openmain-monitoring
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    allowUiUpdates: false
    options:
      path: /var/lib/grafana/dashboards/openmain
      foldersFromFilesStructure: false
~~~

Dashboard-Dateien erzeugen:

~~~bash
install -d -o grafana -g grafana -m 0755 /var/lib/grafana/dashboards/openmain

python3 generate-dashboards.py \
  --datasource-uid zabbix-main \
  --output /var/lib/grafana/dashboards/openmain

chown -R grafana:grafana /var/lib/grafana/dashboards/openmain
systemctl restart grafana-server
~~~

Bei Docker müssen Provisioning- und Dashboard-Verzeichnisse persistent in den Grafana-Container gemountet sein.

## 6. TV-Dashboard

Dashboard: `OpenMain - TV / NOC`

Empfohlene Darstellung:

- Browser im Kiosk-Modus
- 1920x1080
- 30 Sekunden Refresh
- dedizierter Grafana-Viewer oder Authentik-Session
- Zugriff ausschließlich intern/NetBird
- kein öffentlicher anonymer Zugriff

Beispiel:

~~~text
https://GRAFANA-DOMAIN/d/openmain-tv/openmain-tv-noc?kiosk
~~~

## 7. Problem-Dashboard

`OpenMain - Alle Probleme` zeigt:

- Disaster
- High+
- Average+
- Warning+
- alle aktiven Probleme in einer Tabelle

Das Dashboard arbeitet direkt mit Zabbix-Problems und benötigt keine manuelle Hostpflege.

## 8. Erweiterung

Für weitere Plattformen:

1. neue Erkennungsregel in `sync-zabbix-groups.py`
2. neues Profil in `generate-dashboards.py`
3. Generator erneut ausführen
