# Grafana + Zabbix Automatisierung

Dieses Paket erzeugt dynamische Grafana-Dashboards für die OpenMain-Zabbix-Umgebung.

## Dashboards

- OpenMain - OPNsense
- OpenMain - Proxmox VE
- OpenMain - Dell iDRAC
- OpenMain - NAS
- OpenMain - QNAP
- OpenMain - Synology
- OpenMain - Alle Probleme
- OpenMain - TV / NOC

Die Dashboards sind nicht pro Host fest verdrahtet. Grafana verwendet Zabbix-Gruppen und Hostvariablen. Neue Systeme erscheinen automatisch, sobald sie von `sync-zabbix-groups.py` klassifiziert wurden.

## Automatische Zabbix-Gruppen

Der Sync ordnet aktive Hosts anhand verknüpfter Zabbix-Templates und Hostnamen ein:

- `OpenMain/OPNsense`
- `OpenMain/PVE`
- `OpenMain/iDRAC`
- `OpenMain/NAS`
- `OpenMain/QNAP`
- `OpenMain/Synology`
- `OpenMain/Critical`

Zusätzlich werden Host-Tags `openmain.platform=<typ>` und für kritische Systeme `openmain.critical=true` gesetzt.

Standardmäßig gelten OPNsense, PVE, iDRAC, NAS, QNAP und Synology als kritisch. Über `OPENMAIN_CRITICAL_PLATFORMS` kann das geändert werden.

## Voraussetzungen

- Grafana
- Grafana-Zabbix Plugin `alexanderzobnin-zabbix-app`
- Zabbix 7.x
- Python 3
- Zabbix API Token für den Metadaten-Sync mit Rechten zum Lesen/Aktualisieren von Hosts und Hostgruppen
- Grafana-Zabbix-Datasource mit stabiler UID; empfohlen: `zabbix-main`

## Sicherheit

- Keine API-Tokens oder Passwörter in Git speichern.
- Für Grafana einen eigenen Zabbix-API-Token mit nur Leserechten verwenden.
- Für `sync-zabbix-groups.py` einen getrennten Management-Token verwenden.
- SNMP nach Möglichkeit als SNMPv3 `authPriv` betreiben.
- Das TV-Dashboard nur intern bzw. über NetBird bereitstellen; kein anonymer öffentlicher Zugriff.
- Kundennamen in Hostnamen können mandantenbezogene Informationen offenlegen. Grafana-Ordnerrechte und TV-Standort entsprechend einschränken.

Siehe `HOWTO.md` für Installation und Betrieb.
