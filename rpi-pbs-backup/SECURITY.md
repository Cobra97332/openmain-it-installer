# Sicherheitskonzept

## Empfohlene Trennung

Das Raspberry-Pi-Backup-Gateway läuft in einem eigenen **unprivilegierten Debian-13-LXC**. Dadurch erhalten Raspberry Pis keinen SSH-Zugriff auf den PVE-Host selbst.

```text
Raspberry Pi -> SSH -> Gateway-CT -> PBS
```

Der Benutzer `rpi-backup` im CT darf per `sudo` ausschließlich `/usr/local/sbin/rpi-pbs-ingest` ausführen.

## PBS-Zugangsdaten

Der PVE-Deploy-Installer übernimmt für die Erstinstallation die bereits vorhandene Credential-Datei des ausgewählten PVE-PBS-Storages und kopiert sie als:

```text
/etc/openmain/pbs-secret
```

in den Gateway-CT. Rechte:

```text
0600 root:root
```

Für produktive Kundenumgebungen wird empfohlen, anschließend einen **eigenen PBS-API-Token nur für Raspberry-Pi-Backups** zu verwenden. Rechte nur auf den benötigten Datastore/Namespace vergeben.

Keine Tokens, Passwörter, PSKs oder privaten SSH-Schlüssel in Git committen.

## Netzwerk

Empfohlen:

- Gateway-CT nur über Management-LAN oder NetBird erreichbar machen
- SSH TCP/22 nicht öffentlich ins Internet veröffentlichen
- PBS TCP/8007 nur zwischen Gateway-CT und PBS zulassen
- optional PVE-Firewall-Regeln direkt am CT aktivieren

## Staging

`/var/lib/openmain-rpi-pbs` liegt standardmäßig auf einem separaten CT-Mountpoint mit `backup=0`.

Grund: Die dortigen Daten sind nur ein Staging-/Zwischenstand. Die eigentliche Sicherung liegt auf PBS. Dadurch wird vermieden, dass PVE dieselben Raspberry-Pi-Daten ein zweites Mal als Teil des Gateway-CT sichert.

## SSH

- eigener Backup-Benutzer `rpi-backup`
- Key-basierte Anmeldung
- Account-Passwort von `rpi-backup` gesperrt
- Backup-Client-Keys liegen in `/home/rpi-backup/.ssh/authorized_keys`
- der OpenMain-Admin-Key liegt zusätzlich in `/root/.ssh/authorized_keys`
- Root-SSH ist ausschließlich per Public Key erlaubt (`PermitRootLogin prohibit-password`)
- SSH-Passwort- und Keyboard-Interactive-Anmeldung sind deaktiviert
- private Schlüssel verbleiben auf den jeweiligen Admin-/Raspberry-Systemen

## DSGVO

Wenn Raspberry Pis personenbezogene Daten verarbeiten:

- Zugriffe auf PBS und Gateway-CT beschränken
- Retention/Löschfristen dokumentieren
- Restore-Zugriffe nachvollziehbar halten
- Offsite-Replikationen in das eigene AVV/TOM-Konzept aufnehmen
- Verschlüsselung und Schlüsselaufbewahrung getrennt dokumentieren
