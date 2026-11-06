# Nativer PVE-LXC-Kundenrouter

Neue Debian-13-Container erhalten durch `netbird-router/pve/install.sh` den
bestehenden Router-Installer und `zabbix-proxy-install.sh`. Dessen Standard ist
Zabbix Proxy **8.0** mit `zabbix-proxy-sqlite3` und `zabbix-agent2` aus dem
[offiziellen Debian-13-Repository](https://repo.zabbix.com/zabbix/8.0/release/debian/pool/main/z/zabbix-release/).
Der Zabbix-Server `100.107.91.6` muss ebenfalls Version 8.0 verwenden.

Beispiel für einen neuen Testcontainer auf einem PVE-Host:

```bash
bash netbird-router/pve/install.sh \
  --ctid 900 --hostname nb-beispiel-1 --customer Beispiel \
  --role primary --zabbix-server 100.107.91.6
```

Für einen zweiten Container `--role backup`, einen anderen CTID und Hostnamen
verwenden. Die vorhandenen Parameter und die NetBird-Metriken 100/200 bleiben
erhalten. Der Proxy-Helper akzeptiert weiterhin `--version` oder
`ZABBIX_VERSION`; ohne Angabe gilt `8.0`.

Der Helper installiert nur auf einem Container ohne vorhandene Proxy- oder
Agent-Konfiguration. Ein bereits installierter Proxy wird weder aktualisiert
noch neu registriert; Name, TLS-PSK und Konfigurationsdateien bleiben erhalten.
Ein unvollständiger Altbestand erfordert eine manuelle Prüfung. Diese Änderung
ist kein Upgradeverfahren für bestehende Kundenrouter.
