#!/usr/bin/env bash
# OpenMain IT: CloudPanel hinter dem bestehenden NetBird Account/BYOP Reverse Proxy
# Keine Aenderung an CloudPanel-VHosts, NetBird-Docker-Compose oder Firewall.
set -Eeuo pipefail

ADMIN_DOMAIN="${ADMIN_DOMAIN:-cloudpanel.openmain-it.de}"
NETBIRD_INTERFACE="${NETBIRD_INTERFACE:-wt0}"
ADMIN_PORT="${ADMIN_PORT:-18080}"
SITES_PORT="${SITES_PORT:-18081}"
CONF="/etc/nginx/conf.d/openmain-cloudpanel-netbird.conf"
BACKUP_DIR="/root/openmain-cloudpanel-netbird-backups"
MODE="${1:-install}"

log() { printf '[cloudpanel-netbird] %s\n' "$*"; }
die() { printf '[cloudpanel-netbird] FEHLER: %s\n' "$*" >&2; exit 1; }

[[ "$EUID" -eq 0 ]] || die "Bitte als root ausfuehren."
[[ "$MODE" == "install" || "$MODE" == "--check" ]] || die "Aufruf: $0 [--check]"
[[ "$ADMIN_DOMAIN" =~ ^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$ ]] || die "Ungueltige ADMIN_DOMAIN."
[[ "$ADMIN_PORT" =~ ^[0-9]{2,5}$ && "$SITES_PORT" =~ ^[0-9]{2,5}$ ]] || die "Ungueltige Ports."
[[ "$ADMIN_PORT" != "$SITES_PORT" ]] || die "ADMIN_PORT und SITES_PORT muessen unterschiedlich sein."

for cmd in nginx ip ss curl awk grep sed systemctl date mktemp; do
  command -v "$cmd" >/dev/null 2>&1 || die "Kommando fehlt: $cmd"
done

[[ -d /etc/nginx/conf.d ]] || die "/etc/nginx/conf.d fehlt."
command -v netbird >/dev/null 2>&1 || die "NetBird-Client fehlt auf diesem CloudPanel-Host."
NETBIRD_IP="$(ip -4 -o addr show dev "$NETBIRD_INTERFACE" 2>/dev/null | awk 'NR==1 {split($4,a,"/"); print a[1]}')"
[[ "$NETBIRD_IP" =~ ^100\. ]] || die "Keine NetBird IPv4 auf $NETBIRD_INTERFACE gefunden."

# Wichtige CloudPanel-Dienste nur pruefen; keine bestehenden VHosts anfassen.
curl -kfsS -o /dev/null --connect-timeout 4 --max-time 8 https://127.0.0.1:8443/ ||
  die "CloudPanel-Port 8443 lokal nicht erreichbar (HTTP-Status oder TLS-Verbindung)."
# CloudPanel kann TLS-Verbindungen ohne passenden SNI-Hostnamen ablehnen.
# Darum keine TLS-Probe gegen eine reine 127.0.0.1-Adresse.
ss -ltnH | awk '{print $4}' | grep -Eq '(^|[:.])443$' ||
  die "Lokaler HTTPS-Port 443 ist nicht offen."
if [[ -n "${TEST_SITE_DOMAIN:-}" ]]; then
  [[ "$TEST_SITE_DOMAIN" =~ ^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$ ]] || die "Ungueltige TEST_SITE_DOMAIN."
  curl -ksS -o /dev/null --connect-timeout 4 --max-time 8 \
    --resolve "$TEST_SITE_DOMAIN:443:127.0.0.1" \
    "https://$TEST_SITE_DOMAIN/" ||
    die "Website $TEST_SITE_DOMAIN auf lokalem Port 443 nicht erreichbar."
  log "SNI-HTTPS-Website erreichbar: $TEST_SITE_DOMAIN"
else
  log "Hinweis: Fuer einen SNI-Test TEST_SITE_DOMAIN=www.example.com setzen."
fi

if [[ ! -f "$CONF" ]]; then
  for port in "$ADMIN_PORT" "$SITES_PORT"; do
    if ss -ltnH | awk '{print $4}' | grep -Eq "[:.]$port$"; then
      die "Port $port ist bereits belegt. Bitte Alternativport per Umgebungsvariable waehlen."
    fi
  done
fi

log "NetBird: $NETBIRD_INTERFACE / $NETBIRD_IP"
log "Administration: http://$NETBIRD_IP:$ADMIN_PORT -> lokal HTTPS:8443"
log "Websites:        http://$NETBIRD_IP:$SITES_PORT -> lokal HTTPS:443 (Host/SNI erhalten)"
log "Administrator-Domain: $ADMIN_DOMAIN"
if [[ "$MODE" == "--check" ]]; then
  log "Vorpruefung abgeschlossen. Keine Aenderungen vorgenommen."
  exit 0
fi

install -d -m 0700 "$BACKUP_DIR"
TMP="$(mktemp /etc/nginx/conf.d/.openmain-cloudpanel-netbird.XXXXXXXX.conf)"
trap 'rm -f "$TMP"' EXIT
HAD_ORIGINAL=0
BACKUP=""
if [[ -f "$CONF" ]]; then
  HAD_ORIGINAL=1
  BACKUP="$BACKUP_DIR/openmain-cloudpanel-netbird.$(date +%Y%m%d-%H%M%S).conf"
  cp -a "$CONF" "$BACKUP"
fi

cat > "$TMP" <<'NGINX'
# OpenMain IT / NetBird Ingress (nur HTTP aus dem verschluesselten NetBird Tunnel).
# Die Proxy-Ziele liegen ausnahmslos auf Loopback-HTTPS.
# Ein zusaetzlicher WAN-Firewall-Block fuer TCP/18080 und TCP/18081 ist empfohlen.
map $http_upgrade $openmain_nb_connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 0.0.0.0:__ADMIN_PORT__ default_server;
    server_name __ADMIN_DOMAIN__;
    allow 100.64.0.0/10;
    deny all;

    if ($host != "__ADMIN_DOMAIN__") { return 444; }

    client_max_body_size 256m;

    location / {
        proxy_pass https://127.0.0.1:8443;
        proxy_http_version 1.1;
        proxy_ssl_verify off; # ausschliesslich Loopback-Ziel; keine unsichere WAN-Verbindung
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Port 443;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr; # keine vom Aufrufer gesetzten XFF-Werte uebernehmen
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $openmain_nb_connection_upgrade;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}

server {
    listen 0.0.0.0:__SITES_PORT__ default_server;
    server_name _;
    allow 100.64.0.0/10;
    deny all;

    client_max_body_size 10g;

    location / {
        # HTTPS auf localhost vermeidet HTTP->HTTPS-Redirect-Loops in CloudPanel-VHosts.
        proxy_pass https://127.0.0.1:443;
        proxy_http_version 1.1;
        proxy_ssl_server_name on;
        proxy_ssl_name $host;
        proxy_ssl_verify off; # ausschliesslich Loopback-Ziel
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Port 443;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr; # keine vom Aufrufer gesetzten XFF-Werte uebernehmen
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $openmain_nb_connection_upgrade;
        proxy_request_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
NGINX

sed -i \
  -e "s/__ADMIN_DOMAIN__/$ADMIN_DOMAIN/g" \
  -e "s/__ADMIN_PORT__/$ADMIN_PORT/g" \
  -e "s/__SITES_PORT__/$SITES_PORT/g" "$TMP"

restore() {
  if [[ "$HAD_ORIGINAL" -eq 1 ]]; then
    cp -a "$BACKUP" "$CONF"
  else
    rm -f "$CONF"
  fi
  nginx -t >/dev/null 2>&1 || true
}

mv -f "$TMP" "$CONF"
if ! nginx -t; then
  restore
  die "NGINX-Syntaxfehler. Aenderung zurueckgerollt; alter NGINX-Prozess unveraendert."
fi

# Nur fortfahren, wenn diese Datei in die effektive NGINX-Konfiguration geladen wird.
if ! nginx -T 2>&1 | grep -F "configuration file $CONF:" >/dev/null; then
  restore
  die "Datei wird nicht aus nginx.conf eingebunden. Rueckgaengig gemacht."
fi

if ! systemctl reload nginx; then
  restore
  systemctl reload nginx || true
  die "NGINX-Reload fehlgeschlagen. Konfiguration zurueckgerollt."
fi

log "Installiert: $CONF"
log "Admin-Test: curl -I -H 'Host: $ADMIN_DOMAIN' http://$NETBIRD_IP:$ADMIN_PORT/"
log "Site-Test:  curl -I -H 'Host: MEINE-WEBSITE.DE' http://$NETBIRD_IP:$SITES_PORT/"
log "NetBird Services: HTTP -> Peer CloudPanel -> Ports $ADMIN_PORT (privat), $SITES_PORT (oeffentliche Websites)"
log "WICHTIG: Vor dem Sperren oeffentlicher Altports NetBird-Service/DNS/SSL pruefen."
