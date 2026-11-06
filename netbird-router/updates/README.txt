# Updates für bestehende Debian-13-Kundenrouter

Dieser Nachrüstpfad installiert nur die OpenMain-Update-Komponente. Er führt
`router-install.sh` nicht erneut aus, ruft `netbird up` nicht auf und ändert
keine bestehenden BINAT-, nftables-, Proxy- oder NetBird-Dateien. PVE-LXC,
PVE-VMs und bereits aktivierte Raspberry-Pi-ARM64-Router verwenden denselben
Routerzustand unter `/var/lib/netbird-kundenrouter/state.json`. Ein nur im
Büro vorbereiteter Raspberry Pi hat diesen Zustand noch nicht und wird
abgewiesen.

Der folgende Einzeiler ist auf den Implementierungscommit und den SHA256-Wert
des Nachrüst-Installers festgelegt. Die vier nachgeladenen Hilfsdateien werden
vom Installer zusätzlich gegen ihre festgelegten SHA256-Werte geprüft:

```bash
bash -c 'set -euo pipefail; f=$(mktemp); trap "rm -f \"$f\"" EXIT; curl -fsSLo "$f" https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/f17e4669c4c8994dbb8077d4b02690b814833bb4/netbird-router/updates/retrofit.py; echo "44ae8cec37f2a56f442cc18355cd7f0103f82641af9c5b2d59f3b55447eb2ab9  $f" | sha256sum -c -; sudo python3 "$f" --install --ref f17e4669c4c8994dbb8077d4b02690b814833bb4'
```

Ein einzelner Primary-Router ohne Backup benötigt denselben Befehl mit
`--single` am Ende des `sudo python3`-Aufrufs. Das Flag ist nur zulässig, wenn
das Fehlen eines Backup-Routers vor Ort bestätigt wurde. Ohne dieses Flag
bricht eine unklare Topologie ohne Änderungen ab. Bei Paaren werden die zwei
bereits in `ServerActive` des Zabbix Agent 2 konfigurierten Router-LAN-Adressen
verwendet; andere Partneradressen werden nicht geraten. Bei fehlender oder
mehrdeutiger Zuordnung wird nicht installiert.

Der Installer legt vor Änderungen eine Root-geschützte Sicherung unter
`/var/backups/openmain-router-updates/` an. Er sichert die vorherigen
Update-Dateien samt Timerzustand und Kopien der Router-Konfiguration. Für
Rollback die im Installer-Ausgang angezeigte Sicherungs-ID verwenden:

```bash
bash -c 'set -euo pipefail; f=$(mktemp); trap "rm -f \"$f\"" EXIT; curl -fsSLo "$f" https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/f17e4669c4c8994dbb8077d4b02690b814833bb4/netbird-router/updates/retrofit.py; echo "44ae8cec37f2a56f442cc18355cd7f0103f82641af9c5b2d59f3b55447eb2ab9  $f" | sha256sum -c -; sudo python3 "$f" --rollback SICHERUNGS-ID'
```

Rollback stellt die Update-Dateien und den Timerzustand wieder her. Bereits
durch APT installierte Paketversionen werden nicht zurückgesetzt. Paketupdates
laufen auch bei ausgefallenem Partner; bei Paaren wird dann nur der automatische
Neustart gesperrt. Bei Einzelroutern ist keine Partnerprüfung nötig. Tests auf
echten Routern und realer NetBird-Verkehrsübernahme stehen weiterhin aus.
