# Sicherheitskonzept

## Grundprinzip

Der Raspberry Pi besitzt keine PBS-Anmeldedaten. Er darf nur per SSH auf ein dediziertes Staging-Verzeichnis des Backup-Gateways schreiben und den validierenden Ingest-Wrapper auslösen.

## PBS-Zugangsdaten

- Token nur auf dem x86-Gateway speichern.
- Secret-Datei: `0600 root:root`.
- API-Token auf den notwendigen Datastore/Namespace begrenzen.
- Keine Tokens, Passwörter, PSKs oder private Schlüssel in Git committen.

## SSH

- dedizierter Benutzer `rpi-backup`
- Key-basierte Anmeldung
- kein Root-SSH für den Backup-Transport erforderlich
- Gateway möglichst nur über Management-LAN oder NetBird erreichbar machen
- Public Keys dürfen ins `authorized_keys`; private Keys bleiben ausschließlich auf dem jeweiligen Raspberry Pi

## sudo

Der Benutzer `rpi-backup` darf nur `/usr/local/sbin/rpi-pbs-ingest` per sudo ausführen. Der Wrapper akzeptiert ausschließlich streng validierte Backup-IDs und baut den Staging-Pfad selbst.

## Staging

`/srv/rpi-pbs-staging` enthält eine aktuelle Kopie der gesicherten Daten und ist damit wie ein Backup zu behandeln:

```bash
chmod 700 /srv/rpi-pbs-staging
chown rpi-backup:rpi-backup /srv/rpi-pbs-staging
```

Bei besonders sensiblen Systemen das Gateway selbst verschlüsseln bzw. entsprechend absichern.

## Clientseitige PBS-Verschlüsselung

Optional kann auf dem Gateway ein PBS-Keyfile gesetzt werden:

```bash
PBS_KEYFILE="/etc/openmain/rpi-pbs.key"
```

Das Keyfile muss separat und sicher gesichert werden. Ohne Schlüssel ist ein verschlüsseltes Backup nicht wiederherstellbar.

## DSGVO

Wenn Raspberry Pis personenbezogene Daten verarbeiten:

- Zugriff auf PBS und Gateway auf erforderliche Administratoren beschränken.
- Backup-Retention dokumentieren.
- Löschfristen auch für Backups berücksichtigen.
- Restore-Zugriffe und administrative Tätigkeiten nachvollziehbar protokollieren.
- Offsite-/Cloud-Replikationen entsprechend dem eigenen AVV-/TOM-Konzept behandeln.
