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
bash -c 'set -euo pipefail; f=$(mktemp); trap "rm -f \"$f\"" EXIT; curl -fsSLo "$f" https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/f2c869eb6df823a856740c449a3717e06837c2a9/netbird-router/updates/retrofit.py; echo "ae83949f4883c107b46de97531fafce46a9e067ca4d01a8d3d228b80aac5ab02  $f" | sha256sum -c -; sudo python3 "$f" --install --ref f2c869eb6df823a856740c449a3717e06837c2a9'
```

Ein einzelner Primary-Router ohne Backup benötigt denselben Befehl mit
`--single` am Ende des `sudo python3`-Aufrufs. Das Flag ist nur zulässig, wenn
das Fehlen eines Backup-Routers vor Ort bestätigt wurde. Ohne dieses Flag
bricht eine unklare Topologie ohne Änderungen ab. Bei Paaren werden die zwei
bereits in `ServerActive` des Zabbix Agent 2 konfigurierten Router-LAN-Adressen
verwendet; andere Partneradressen werden nicht geraten. Bei fehlender oder
mehrdeutiger Zuordnung wird nicht installiert.

Für den bestehenden Luxon-Primary `nb-luxon-1` (derzeit ohne Backup) ist
automatischer Neustart ausdrücklich deaktiviert. Der vollständige, auf Commit
und SHA256 gepinnte Nachrüstbefehl lautet:

```bash
bash -c 'set -euo pipefail; f=$(mktemp); trap "rm -f \"$f\"" EXIT; curl -fsSLo "$f" https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/f2c869eb6df823a856740c449a3717e06837c2a9/netbird-router/updates/retrofit.py; echo "ae83949f4883c107b46de97531fafce46a9e067ca4d01a8d3d228b80aac5ab02  $f" | sha256sum -c -; sudo python3 "$f" --install --ref f2c869eb6df823a856740c449a3717e06837c2a9 --single --no-auto-reboot'
```

Die Nachrüstung akzeptiert die bereits vorhandene BINAT-Tabelle
`ip openmain_binat` sowie ältere Installationen mit `ip mein_binat`; sie legt
keine Tabelle und keine Regeln an. `AUTO_REBOOT=false` wird in
`/etc/openmain-router-update.conf` gespeichert und bei erneuter Nachrüstung
ohne Reboot-Option beibehalten. Der Sonntagstimer um 04:00 Uhr und die APT-
Updates laufen trotzdem. Bei Einzelroutern wird Zabbix Agent2 nicht wegen
dieser Update-Komponente neu gestartet.

Wenn später ein Backup-Router betriebsbereit ist, müssen zunächst beide
Router-LAN-Adressen in der vorhandenen `ServerActive`-Paarzuordnung stehen und
die Partnerprüfung einschließlich NetBird, BINAT und Kunden-LAN erfolgreich
sein. Danach die Nachrüstung ohne `--single` auf Primary und Backup erneut
ausführen. Auf dem bestehenden Primary bleibt `AUTO_REBOOT=false` ohne neue
Option erhalten; bei der ersten Nachrüstung des Backup-Routers muss
`--no-auto-reboot` ausdrücklich gesetzt werden. Erst nach isoliertem Failover-
und Neustarttest kann `--auto-reboot` auf beiden Routern bei einer erneuten
Nachrüstung bewusst gesetzt werden. Die Timer bleiben Primary 04:00 und
Backup 05:00 Uhr Europe/Berlin. Die Nachrüstung registriert keinen zweiten
NetBird-Client und verändert keine vorhandenen BINAT- oder Zabbix-Identitäten.

Der Installer legt vor Änderungen eine Root-geschützte Sicherung unter
`/var/backups/openmain-router-updates/` an. Er sichert die vorherigen
Update-Dateien samt Timerzustand und Kopien der Router-Konfiguration. Für
Rollback die im Installer-Ausgang angezeigte Sicherungs-ID verwenden:

```bash
bash -c 'set -euo pipefail; f=$(mktemp); trap "rm -f \"$f\"" EXIT; curl -fsSLo "$f" https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/f2c869eb6df823a856740c449a3717e06837c2a9/netbird-router/updates/retrofit.py; echo "ae83949f4883c107b46de97531fafce46a9e067ca4d01a8d3d228b80aac5ab02  $f" | sha256sum -c -; sudo python3 "$f" --rollback SICHERUNGS-ID'
```

Rollback stellt die Update-Dateien und den Timerzustand wieder her. Bereits
durch APT installierte Paketversionen werden nicht zurückgesetzt. Paketupdates
laufen auch bei ausgefallenem Partner; bei Paaren wird dann nur der automatische
Neustart gesperrt. Bei Einzelroutern ist keine Partnerprüfung nötig. Tests auf
echten Routern und realer NetBird-Verkehrsübernahme stehen weiterhin aus.
