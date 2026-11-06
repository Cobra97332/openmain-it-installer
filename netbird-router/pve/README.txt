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

## Wöchentliche Paketupdates

Der Router-Installer richtet `openmain-router-update.service` und genau einen
`openmain-router-update.timer` ein. Primary läuft sonntags 04:00, Backup 05:00
Uhr in `Europe/Berlin`. Die Systemzeitzone wird nur geprüft und nicht geändert.
Das Skript führt `apt-get update` und `apt-get upgrade -y` mit Beibehaltung
lokaler Konfigurationsdateien aus. Paketentfernung und Distributionsupgrade
sind nicht vorgesehen. `Persistent=true` holt verpasste Updates nach, aber
ein Lauf außerhalb der jeweiligen Berliner Stunde löst keinen Neustart aus.

Für Paare müssen die bereits verwendeten beiden LAN-Adressen mit
`--zabbix-agent-group-ips IP_PRIMARY,IP_BACKUP` beziehungsweise
`NB_ZABBIX_AGENT_GROUP_IPS` übergeben werden. Der Update-Installer speichert
daraus die Partner-IP. Vor dem Neustart fragt er den Zabbix Agent 2 des
Partners nach Rolle und Bereitschaft. Dessen Prüfung verlangt NetBird-
Management- und Signalverbindung, IP-Forwarding, die BINAT-Tabelle und einen
erfolgreichen ICMP-Test zum konfigurierten LAN-Standardgateway. Fehlt eine
Angabe oder blockiert das LAN ICMP, wird der Neustart ausgesetzt. Beim Backup
muss der Partner als Primary wieder betriebsbereit sein. Einzelrouter ohne
Gruppenadressen benötigen keine Partnerprüfung. Die Prüfung ist eine lokale
Bereitschaftskontrolle; die Übernahme eines echten NetBird-Datenstroms muss
vor der Nutzung automatischer Neustarts am jeweiligen Teststandort geprüft
werden.

Nachträgliche Installation im bestehenden Router-LXC (nicht auf dem PVE-Host):

```bash
# Die drei Skripte aus netbird-router/pve/ gemeinsam in ein Verzeichnis laden.
bash install-router-updates.sh --role primary \
  --agent-group-ips 192.0.2.10,192.0.2.11
systemctl list-timers openmain-router-update.timer
journalctl -u openmain-router-update.service -n 100 --no-pager
```

Für einen Einzelrouter `--agent-group-ips` weglassen. Bei vorhandenen
Routerpaaren niemals ohne die zwei bekannten LAN-Adressen installieren. Der
Installer kann mit denselben Parametern wiederholt ausgeführt werden. Er
berührt weder NetBird-Policies, BINAT, Zabbix-PSK, SSH noch Routing-Metriken.
Er ergänzt lediglich zwei Zabbix-Agent-Schlüssel für die Partnerprüfung.
