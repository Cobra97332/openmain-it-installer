# HOWTO - Raspberry Pi Kundenrouter

## Buero

Voraussetzungen:

- Raspberry Pi 3, 4 oder 5
- Raspberry Pi OS 64-bit oder Debian arm64
- DHCP und Internet
- NetBird Setup Key
- NetBird API Token

Installer laden:

```bash
curl -fsSL https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/netbird-router/raspberry-pi/install-router.sh -o install-router.sh
chmod +x install-router.sh
```

Primary vorbereiten:

```bash
sudo ./install-router.sh \
  --customer "Taxi Daheim" \
  --role primary \
  --hostname nb-taxi-daheim-01
```

Backup vorbereiten:

```bash
sudo ./install-router.sh \
  --customer "Taxi Daheim" \
  --role backup \
  --hostname nb-taxi-daheim-02
```

Nach der Vorbereitung pruefen:

```bash
systemctl is-enabled openmain-netbird-customer-deploy.service
systemctl is-active netbird || true
sudo cat /etc/openmain-netbird-router/office-network.env
```

Erwartung:

```text
Auto-Deploy = enabled
NetBird = inactive
```

Danach:

```bash
sudo shutdown -h now
```

## Kunde

1. Pi per Ethernet anschliessen.
2. Einschalten.
3. Der Pi wartet auf DHCP, IPv4 und Default-Route.
4. Er vergleicht Netz, Gateway und Gateway-MAC mit dem Buerostandort.
5. Erst bei erkanntem Standortwechsel wird NetBird aktiviert.
6. Kunden-Network, Resource und BINAT werden automatisch eingerichtet.

Live-Log:

```bash
journalctl -u openmain-netbird-customer-deploy -f
```

Status:

```bash
systemctl status openmain-netbird-customer-deploy --no-pager
```

Nach erfolgreichem Deployment:

```bash
netbird status
ip -4 route
cat /proc/sys/net/ipv4/ip_forward
nft list table ip mein_binat
cat /var/lib/netbird-kundenrouter/state.json
```

## Manuell aktivieren

Falls die Standorterkennung wegen identischer Netzparameter nicht ausloest:

```bash
sudo /usr/local/sbin/openmain-netbird-customer-deploy --force
```

Nur am Kundenstandort ausfuehren.

## Mapping

```text
/24        -> 10.30.x.0/24
/16-/23    -> 10.40er Bereich
```

Vorhandene Kunden-Mappings werden bei einem Retry wiederverwendet.

## Sicherheit

Vor der Kundenaktivierung liegt die lokale Aktivierungskonfiguration unter:

```text
/etc/openmain-netbird-router/customer.env
```

Dateirechte: `0600 root:root`.

Nach erfolgreichem Deployment wird die Datei entfernt.

Der Setup Key muss bis zum geplanten Kundentermin gueltig sein.

## Fehlerdiagnose

```bash
ip -4 addr
ip -4 route
ip neigh
journalctl -u openmain-netbird-customer-deploy -n 200 --no-pager
journalctl -u netbird -n 100 --no-pager
```
