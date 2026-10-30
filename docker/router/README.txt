OpenMain Docker-Kundenrouter — experimenteller Kandidat, keine Produktionsfreigabe

Eigener Branch feat/docker-standard-router, Basis f0d8738b982bdd68090ffd3c1f1cd821b03f982c.
PR #34 und sein Runtime-Patch werden weder geändert noch eingebunden.

Wiederverwendung
- common/runtime.py:nft_config unverändert: Prefix-DNAT für 100.64.0.0/10,
  hostanteilgleiche Übersetzung, ausschließlich auf DNAT-Verkehr begrenztes
  Masquerade. Standard: 10.30.1.x -> 192.168.178.x, Antworten über Conntrack.
- common/network.py: bestehende Kundenressource in Kunden- und Kundengruppe,
  ausschließlich die vorhandene einseitige Policy Admin → Kunden und
  Primary-/Backup-Router mit Metriken 100/200, masquerade=false.
- zabbix-api-register.sh, zabbix_registration.py und register_host():
  Proxy-Gruppe, Zabbix 7.4 TLS-PSK, private Schreibnachweise, Host-/Agent-Abgleich.
  Nach erfolgreicher Gruppeneinrichtung prüft ein Neustart Mitgliedschaften
  nur lesend; entzogene Rechte werden nicht automatisch wieder vergeben.
  Kundennamen dürfen keine gemeinsam genutzten Zugriffsgruppen bezeichnen.
  Nach erfolgreicher Ressourcenzuordnung gleicht der Adapter dieselbe
  Zuordnung erneut ab und vergleicht die gespeicherten IDs. Manuell
  eingeschränkte oder entfernte zentrale Policies werden nicht ersetzt.
Die systemd-/APT-Installer laufen NICHT beim Containerstart. Aus binat_acl.py
wird nur der native ACL-Parser zum lesenden Readiness-Check verwendet; der
Legacy-Reconciler wird nie gestartet. Kein eigener NetBird-Build und keine
Testinjektoren im Runtime-Image.

Entscheidende Netzwerktopologie
  VPN -> gateway (NetBird-ACL sieht 10.30.1.x)
      -> internes TRANSIT-Netz -> nat (DNAT) -> Kunden-LAN
NetBird und NAT sind getrennte Docker-Netzwerk-Namespaces. Ein DNAT direkt
im NetBird-Namespace würde die virtuelle Zieladresse vor der ACL-Prüfung
verlieren und wird deshalb nicht verwendet. NAT bekommt kein VPN-Interface;
gateway bekommt keinen direkten LAN-Anschluss. DROP-only-Grenzen verhindern
Umwege von Docker-Uplink/LAN in den Transitpfad. Keine ACL-Kopie, keine
Filter-ACCEPT-Freigabe als Ersatz für NetBird. Zusätzliche NAT-Regeln stellen
nur LAN-seitige Zabbix-TLS-Ports 10050/10051 und lokalen Monitoring-Rückweg her.
Eigene LAN-Adresse und Backup-Proxy sind auch über kontrolliertes NAT-Hairpin
erreichbar; daraus folgt keine Freigabe beliebiger Transitverbindungen.
Kein Hostnetz, keine Host-Port-Publikation, kein Docker-Socket, kein privileged.

Betriebsmodell und Grenzen
- Ein Compose-Projekt pro Router. Zwei Container: gateway enthält NetBird,
  Proxy und Agent2; nat benötigt eigenen Namespace. Keine Zabbix-Sidecars mit
  einem nach Gateway-Neustart eventuell veralteten gemeinsamen Namespace.
- Beide restart=unless-stopped; tini + Supervisor beendet alle Gateway-
  Prozesse bei Kindprozessfehler. NAT hält nach erfolgreicher Kernel-
  Einrichtung einen exklusiven flock auf seinem vorhandenen Zustandsvolume.
  Gateway liest dieses Volume nur: Start erst bei passender, lebender NAT-
  Instanz; Prozessverlust beendet den laufenden Gateway-Supervisor. Ein
  liegengebliebenes Lockfile nach SIGKILL gilt nicht als bereit (getestet).
  Keine ACL-/Benutzerliste im Signal und kein zusätzlicher Dienst.
  Ein manuell gestoppter Container bleibt
  erwartungsgemäß gestoppt. Ein bloßer Docker-healthcheck startet NICHT neu.
- Nicht persistenter Userspace-TUN, Standard-NetBird-Binary unverändert.
  Identität, PSK/Registrierungsnachweise und SQLite-Daten liegen in getrennten
  benannten Volumes. Auch die NAT-Zuordnung hat eine persistente Bindung;
  geänderte Kunden-/Netzangaben werden vor Regeländerungen abgelehnt.
  Kein automatischer Wechsel auf neue Identität/PSK.
- Zabbix läuft als zabbix-UID. Gateway-Caps: NET_ADMIN/NET_RAW für VPN,
  CHOWN/DAC_OVERRIDE/FOWNER für eigene Volumeinitialisierung,
  SETUID/SETGID für Demotion, KILL für den Supervisor. NAT nur NET_ADMIN.
  Kein SYS_ADMIN/SYS_MODULE/BPF, keine zusätzlichen privilegierten Helfer.
- Voraussetzungen: normaler Linux-Docker-Host, IPv4-forwarding im jeweiligen
  Container, nftables/conntrack im Hostkernel, vorhandenes /dev/net/tun.
  Keine Rootless-/Docker-Desktop-/LXC-Freigabe.
- NAT wie zuvor: VPN-initiiert einschließlich Antwortverkehr. Eigenständig
  vom Kunden-LAN initiierte Overlay-Verbindungen werden NICHT als geprüfte
  zusätzliche Funktion behauptet. Quell-IP im LAN wird maskiert.
- Agent2 überwacht den Container, NICHT automatisch den Debian-/Proxmox-Host.
- Standard-NetBird ist alleiniger Rechteverwalter. Es gibt KEINE Garantie
  sofortigen Entzugs bereits etablierter Sessions oder eine verteilte
  Controller-zu-Kernel-Bestätigung. Ausfall-/Startup-/Policy-Refresh-Verhalten
  braucht echte Paketnachweise, keine Annahme aus erfolgreichem Enrollment.

Architekturen: Freigabestatus
  amd64: Gesamtimage, Binary-Starts und echter Gateway-Pakettest bestanden.
  arm64: dieselben Prüfungen auf nativem ARM64-Runner bestanden.
  arm/v7: NICHT unterstützt: offizielles APT bietet hier nur Zabbix 7.0.22,
  keine geforderte 7.4-Version. Dockerfile verweigert diesen Build.
Historischer NetBird 0.80.0 Multiarch-Index:
 sha256:4976692ea44bb93871743d0b742b4a8f97f6425bfc458ca2d78e28fdb8bdf4ad
 enthält amd64, arm64 und arm/v6, keinen arm/v7-Eintrag. Ein ARMv6-Binary
 könnte auf ARMv7 laufen; Debian-armhf-, Zabbix- und Gateway-Kompatibilität
 sind damit nicht nachgewiesen. Deshalb kein bloßes Plattform-Relabeling.
Zabbix 7.4 Debian13-Pakete werden im Zielarchitektur-Build wirklich mit apt
installiert. Proxy, Agent2 und SQL-Schema müssen tatsächlich Version 7.4.x
haben; ein erfolgreicher Fallback auf Debians 7.0.x wird als Buildfehler
abgelehnt. Versions-/Architekturliste und Starttests sind CI-Nachweise.
Lokal stoppt der Build beim Zabbix-Releasepaket mit curl HTTP 403; die
Cloud-Netzpolicy erlaubt repo.zabbix.com nicht. Kein Mirror- oder TLS-Bypass.
Solange Build UND Gateway-Nachweis fehlen: keine Plattform als unterstützt
bewerben, keine Produktion/Migration vorhandener Kundenrouter.

Private Zabbix-API: eng begrenzte HTTP-Ausnahme
  NB_ZABBIX_API_URL=http://100.107.91.6:8083/api_jsonrpc.php
  NB_ZABBIX_SERVER=100.107.91.6
Nur diese exakt konfigurierte HTTP-URL ist zulässig. Andere Endpunkte bleiben
HTTPS/443 mit unveränderten TLS-, DNS- und Proxy-Zugriffsprüfungen. Es gibt
keinen allgemeinen Schalter zum Abschalten von TLS. Kein automatischer Fallback.
Vor JEDEM authentifizierten GET/Create/Update werden die aktive NetBird-Identität,
Verbindung, Router-/Server-Peer, Zabbix-Gruppenzuordnung, bestehende Policy für
TCP/10051 sowie die tatsächliche API-Zielroute/-Freigabe geprüft. Für HTTP zählt
TCP/8083 zum echten Server-Peer, nicht die frühere HTTPS-Service-Adresse/443.
Es werden keine Policies geändert. Fehlt die vorhandene 8083-Freigabe, stoppt
die Registrierung; ein erfolgreicher Enrollment allein ist keine API-Freigabe.
HTTP nutzt keine DNS-Auflösung und keinen HTTP-/ALL_PROXY. Jeder Socket ist mit
SO_BINDTODEVICE an das aktive Profilinterface und an die eigene NetBird-IP
gebunden. Nach connect werden Profil/Status, Route und Socket-Adressen vor dem
Senden von Token/PSK erneut geprüft. Fehlende Berechtigung zur Gerätebindung,
Tunnelverlust, Routenwechsel und Redirects brechen ohne Transport-Fallback ab.
Die bereits benötigte NET_RAW-Capability genügt; keine zusätzlichen Runtime-Rechte.
Die ungefilterte HTTP-Strecke endet auf dem konfigurierten Server. WireGuard
verschlüsselt den Tunnel; HTTP bietet dort keine zusätzliche TLS-Serveridentität.
Vertrauen in NetBird-Peer-Zuordnung, Server und Containerhost bleibt erforderlich.
Die lokale API-URL-Bindung darf bei einer eigenen, vor der Registrierung
abgebrochenen Installation von HTTPS auf exakt diese private URL wechseln,
wenn ALLE anderen Bindungswerte gleich sind. Vorhandene Registrierungsnachweise,
Pending-Registrierungen, fertige Konfigurationen oder Ready-Status verbieten
diesen Wechsel. PSK und NetBird-Identität werden dabei nicht ersetzt. Für bereits
registrierte Bestände gibt es keine automatische Endpunktmigration.
Der ungeauthentifizierte apiinfo.version-Probe verlangt Zabbix 7.4 mit params=[].
Kein echter API-Zugriff auf diese Infrastruktur wird in CI ausgeführt.

Nachweise für die HTTP-Ausnahme
- Offline-Regressionen: exakte URL, abweichende IP/Port/Pfad/Userinfo, Gruppen-
  und Policy-Entzug, Verbindungs-/Routenwechsel, Gerätebindungsfehler, Redirects,
  Token-freie Versionsprobe, Schutz JEDES authentifizierten API-Aufrufs und
  wiederholte Proxy-/Gruppenregistrierung ohne PSK- oder Objektduplikate.
- tests/docker_router/private_api.py prüft echte Linux-Sockets/Interfacebindung
  in zwei internen Docker-Netzen einschließlich eines Routenwechsels nach
  connect. Nur NetBird-Profil/Status werden dort synthetisch bereitgestellt;
  dies ist ausdrücklich kein echter NetBird-/Zabbix-Server-End-to-End-Nachweis.
- Der bestehende echte Standard-NetBird-Controller-/Gateway-Test bleibt Pflicht.

Vorbereitung ausschließlich in isoliertem Labor
1. Neuen Linux-Testhost/VM nehmen. Keine alten Profile/PSKs/Volumes importieren.
   Docker Engine + Compose >=2.20 + Buildx installieren. TUN im Host prüfen.
   Keine bestehenden Kunden-/PVE-Netze ändern.
2. Frische isolierte LAN-Bridge ohne physische Ports anlegen, nur wenn dieses
   CIDR auf dem Testhost sonst NICHT verwendet wird:
     docker network create --internal --subnet 192.168.178.0/24 om-demo-isolated-lan
   Für echten späteren LAN-Betrieb braucht es ein vorab geprüftes externes
   macvlan/ipvlan-Netz an einer dafür dedizierten NIC. Keine automatische
   LAN-/Hostnetz-Umkonfiguration durch dieses Projekt. Host/macvlan-Isolation
   und Switch-Zulässigkeit mehrerer MACs vorher separat prüfen.
3. Je Router eigenes privates Projektverzeichnis, .env aus .env.example,
   Compose-Datei über absoluten Pfad verwenden. .env enthält KEINE Secrets.
   CUSTOMER und ROUTER_ROLE bilden eine persistente Bindung; Änderung an
   vorhandenen Volumes wird abgelehnt. Neue transit-CIDRs pro Projekt wählen.
4. Rootgeschütztes SECRETS_DIR (0700) außerhalb git anlegen. Darin Dateien
   nb_setup_key, nb_api_token, zabbix_api_token, jeweils root:root 0600.
   Werte nur über verdeckte Eingabe/Secretmanager in die Dateien schreiben;
   kein xtrace, keine Shellhistory mit Werten und kein Token als CLI-Argument.
   Compose bind-mountet Secrets; uid/mode-Override wird hierfür NICHT als
   funktionierende Dateirechtekorrektur angenommen. Auf dem Host korrekt setzen.
   Bei Wiederaufnahme ist der Setup-Key unnötig: Datei kann leer sein, muss
   wegen Compose-Secretbindung weiter existieren. Tokens bleiben erforderlich.
5. NB_MANAGEMENT_URL, NB_ZABBIX_API_URL und NB_ZABBIX_SERVER ausdrücklich
   setzen. Zentrale NetBird-Policies für TCP10051 zum echten Zabbix-Server
   und HTTPS443 zum tatsächlichen API-Zugriffsweg (für die exakte private
   Ausnahme stattdessen TCP8083 zum Server-Peer) müssen bereits bestehen;
   sie werden geprüft, nicht pauschal erweitert. Beispiele .lab.test sind
   keine produktiven Defaults. Eigene Lab-CA
   über CA_BUNDLE_FILE als öffentlichen root-lesbaren PEM-Bundle bereitstellen;
   kein curl -k, kein Abschalten von APT-/TLS-Signaturprüfung.
6. Netz-/IPv4-Adressen, Transit-Kollisionen und Secretrechte prüfen:
     docker compose --env-file /private/demo/.env -f docker/router/compose.yaml config --quiet
7. Buildx, zunächst lokal laden (nur eine Architektur pro --load):
     docker buildx build --platform linux/amd64 -f docker/router/Dockerfile \
       --load -t openmain-router:local .
   Für ARM64 denselben Befehl mit linux/arm64, keine andere Konfiguration.
   In der Cloud optional --secret id=ca,src=/etc/ssl/certs/ca-certificates.crt;
   CA verbleibt nicht im Image. Niemals Buildlogs mit signierten URLs publizieren.
8. Nur nach erfolgreicher Paket-/Gatewayprüfung bewusst im Labor starten:
     docker compose --env-file /private/demo/.env -f docker/router/compose.yaml up -d
   Dies kann synthetische Controllerobjekte erzeugen. Keine echten Tokens!
   Primary zuerst. VIRTUAL_NETWORK muss pro Kunde eindeutig sein; Primary
   und Backup desselben Kunden teilen den Prefix. Vorhandene
   Gruppenmitglieder/Ressourcen bleiben erhalten.
   Widersprüche/fremde Registrierungsnachweise führen zum Abbruch.

Backup
Eigenes Projekt und eigene Volumes, ROLE=backup, andere freie LAN_IP,
anderes TRANSIT_SUBNET/EDGE_IP/NAT_IP, gleicher CUSTOMER und VIRTUAL_NETWORK.
Primary muss das gemeinsame Mapping bereits reserviert haben. Zugriff wird
vom selben NetBird-Policy-Modell geprüft; ein Metrikwechsel erteilt keine
Berechtigungen. SQLite/Conntrack sind routerlokal; kein verlustfreier Erhalt
laufender TCP-/UDP-Sessions beim Routerwechsel behauptet.
Ersteinrichtungen accountweit serialisieren (gemeinsame Gruppe Kunden): NetBird-Gruppen-API bietet hier kein CAS;
Read-before-write/Readback ist kein globaler Lock gegen fremde API-Schreiber.

Prüfung und Veröffentlichungsablauf
- tests/test_docker_router.py: reine Validierung/Mock-Gruppenbestand/NAT-Reuse.
- tests/docker_router/gateway.py: echter isolierter 0.80.0 Controller, sechs
  unveränderte NetBird-Clients, vier Router-/NAT-Paare, zwei überlappende LANs,
  TCP/UDP/Portregeln, Kundentrennung, Gruppenentzug neuer Flows, Backup und
  TUN-Ausfall. Konfiguration über denselben Networks-/Ressourcen-Helper wie
  Runtime; ein bloßer Nachweis des älteren /routes-Modells reicht nicht.
  Synthetische Credentials/CA nur lokal; keine externen API-Ziele.
  Das ist ein Netzwerkvertragstest. Er ersetzt NICHT den vollständigen
  Compose-Start mit echter Zabbix-7.4-Serverregistrierung/Rebootabnahme.
- Bestehende Offline-/Legacy-Sicherheitstests werden nicht verändert/entfernt.
- GitHub docker-router.yml: Buildx auf nativen AMD64-/ARM64-Runnern
  (ubuntu-24.04 / ubuntu-24.04-arm), Paketarchitektur und echte
  Binary-Starts, dann Gatewayvertrag. Jeder Fehler sperrt die Veröffentlichung.
- GHCR nach erfolgreichen Gates bei Push auf diesem Arbeitsbranch, zusätzlich
  optional workflow_dispatch publish=true (sobald Workflow verfügbar ist).
  Es werden die exakt getesteten Image-Tars gepusht, nicht neu gebaute Pakete.
  Tag candidate-<voller Commit-SHA>, kein latest/production. Beide Architekturen
  müssen bestehen; arm/v7 wird nicht in den Index geschmuggelt.
- Selbst ein grüner Netzwerkvertrag ist KEINE vollständige Produktionsfreigabe:
  echter Compose-/Zabbix-Serverstart, Backup-Passiv-Agent, Warm-/Kaltneustart,
  Controllerverlust und physische ARM64-NICs sind zusätzlich abzunehmen.

Nachgewiesener Stand / noch offene Abnahme
Historischer Netzwerk-Nachweis ef2ab99b7d9eca2712e1860604b8d02c69060918:
  https://github.com/Cobra97332/openmain-it-installer/actions/runs/39070352538
- Syntax, ShellCheck, 223 Debian-13-Offline-Tests bestanden.
- AMD64 und natives ARM64: Image-/Paketbuild, NetBird/Zabbix-Binary-Start,
  Profilformat, unveränderte Networks-/Ressourcen-Abgleiche bestanden.
- Echte Kernelpakete: TCP/UDP, Portbegrenzung, Kundentrennung zweier gleicher
  realer LANs, Gruppenentzug neuer Flows, 100/200-Backup, fehlender Tunnel,
  eigener passiver Zabbix-LAN-Hairpin und Backup-Monitoringtransport bestanden.
- Unverändertes NetBird 0.80.0, Proxy und Agent2 tatsächlich 7.4.15, beide CPUs.
- Historischer GHCR-Kandidat aus GENAU den getesteten Image-Tars veröffentlicht
  (kein Bereitstellungsziel: spätere Kontext-/Redirect-Absicherungen verwenden):
  ghcr.io/cobra97332/openmain-it-installer/customer-router:candidate-ef2ab99b7d9eca2712e1860604b8d02c69060918
  Multiarch-Index sha256:c1618425d6530f589908a7230527fa41609df0c3f5d3a47a9bc3b3b92ebdd6ff
- Nicht behauptet: vollständiger Compose-Neustart mit echtem Zabbix-Server,
  dessen TLS-PSK-Handshake, physischer Host-Neustart und Raspberry-Pi-Hardware.
  Der Pakettest emuliert lediglich Zabbix-Porttransport, NICHT dessen TLS/API.
  Vor Kundenbetrieb sind diese Abnahmen verpflichtend; PR bleibt Draft.
- Keine Sofortentzugs-/Controller-ACK-Garantie für bestehende Sessions.
- ARMHF-APT-Ergebnis ist ein eigener Paketnachweis im jeweiligen CI-Artefakt;
  Kandidaten: Agent2 und Proxy jeweils 1:7.0.22+dfsg-1~deb13u1, nicht 7.4.
  Ein ARMv7-Image-/Gatewaytest ist damit nicht erfolgt; keine ARMv7-Freigabe.
- Ein früherer emulierter ARM64-Lauf scheiterte bei der Netzwerkvorbereitung.
  Native ARM64-Tests bestehen; daraus wird keine emulierte Prüfung gemacht.
- Lokal zusätzlich 14 reine Adaptertests, Compose-Validierung und echtes
  nftables-Kernelladen bestanden. Kompletter lokaler Build: HTTP403 am
  Zabbix-Repository (Cloud-Egress); weiterer lokaler Netzwerkversuch durch
  vollen Docker-VFS-Testdatenträger blockiert. Nur eigene Testobjekte bereinigt.
- Der neue Kandidat verwendet NetBird 0.82.0 mit dem festen Multiarch-Digest
  sha256:eca5b274c635bfbb04ec6724a0712d1146190dd450eea199eeb83916fdd94965
  (AMD64 sha256:ea2982e68e6d4782e1e20adbefe1556f9c6a751c455b9be11850e809554b4d92,
  ARM64 sha256:745a46234495340d8eb2e8d8046b0fc67007e3d82183770de04cf095a6c92b98).
  Die Kompatibilität mit dem isolierten 0.80.0-Controller wird in der neuen
  AMD64-/ARM64-Gateway-CI geprüft; ein grüner früherer Lauf mit 0.81.3
  ersetzt diesen Nachweis nicht.
- Die Legacy-Kernel-CI bleibt an sechs P1-Gegenbeispielen rot. Nach Korrektur
  des isolierten QEMU-Fixtures bestehen im VM-Gast 243 Python-Tests; die VM
  erreicht dann denselben Kernelblocker. Synthetische BIOS- und UEFI-Boots
  bestehen. Diese Gates bleiben aktiv; keine Produktionsfreigabe.
- Änderungen nach diesem Runtime-Commit benötigen ihren eigenen CI-Lauf.

Rollback
Nur dieses Projekt docker compose ... down, ohne -v! Identität/SQLite bleiben.
Vor Updates Dienste stoppen und Volumes konsistent sichern, SQLite online-
Backup oder gestopptes Volume. Keine funktionierende Datenbank löschen.
Keine Übernahme von laufenden Bare-Metal-/PVE-Installationen in neue Volumes.
Bei unbekanntem/manuell geändertem Zustand STOP statt Migration. Eigene
Docker-Netze nur nach dem Stoppen dieses Laborprojekts entfernen; kein prune,
kein nft flush ruleset und keine globalen conntrack-/Host-Firewalländerungen.
