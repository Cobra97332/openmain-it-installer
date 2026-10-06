# Raspberry Pi NetBird Kundenrouter

Oeffentliche Installationsdokumentation fuer Raspberry Pi 3/4/5 mit Raspberry Pi OS 64-bit oder Debian arm64.

## Prinzip

Der Pi wird im Buero vorbereitet, aber dort noch nicht mit NetBird verbunden.

Die Vorbereitung speichert lokal:

- Kundenname und Primary/Backup-Rolle
- die fuer die spaetere Aktivierung benoetigten NetBird-Werte
- die Router-/Zabbix-Installationsdateien
- LAN-Netz, Default-Gateway und Gateway-MAC des Buerostandorts

Beim naechsten Boot wartet der Auto-Deploy-Dienst auf ein anderes Netzwerk. Erst dann werden NetBird, Kunden-Network, Resource, BINAT und optional Zabbix eingerichtet.

## Vorbereitung

```bash
curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/netbird-router/raspberry-pi/install-router.sh -o install-router.sh
chmod +x install-router.sh
sudo ./install-router.sh --customer "KUNDENNAME" --role primary
```

Danach:

```bash
sudo shutdown -h now
```

## Beim Kunden

Pi per Ethernet anschliessen und einschalten.

Der Dienst prueft automatisch:

1. DHCP/IPv4
2. Default-Route
3. Unterschied zum Bueronetz
4. Erreichbarkeit des NetBird-Managements

Danach erfolgt die Aktivierung automatisch.

## Status

```bash
systemctl status openmain-netbird-customer-deploy --no-pager
journalctl -u openmain-netbird-customer-deploy -f
```

Nach erfolgreichem Deployment:

```bash
netbird status
cat /proc/sys/net/ipv4/ip_forward
nft list table ip mein_binat
cat /var/lib/netbird-kundenrouter/state.json
```

## Manuell erzwingen

Nur im Kunden-LAN:

```bash
sudo /usr/local/sbin/openmain-netbird-customer-deploy --force
```

## Mapping

- /24 Kunden-LAN: freies /24 aus `10.30.0.0/16`
- /16 bis /23: passendes Netz aus dem 10.40er-Bereich
- vorhandene Kunden-Mappings werden bei Retry wiederverwendet

## Sicherheit

Die fuer die Aktivierung benoetigten Werte liegen vor dem Deployment lokal unter:

```text
/etc/openmain-netbird-router/customer.env
```

Die Datei ist nur fuer root lesbar und wird nach erfolgreichem Deployment entfernt.

SSH verwendet Public-Key-Anmeldung:

```text
PermitRootLogin prohibit-password
PubkeyAuthentication yes
PasswordAuthentication no
```

Standard-Zabbix-Server:

```text
100.107.91.6:10051
```
