#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG=/etc/openmain-router-update.conf
[[ -r "$CONFIG" ]] || { echo 'Update-Konfiguration fehlt' >&2; exit 1; }
# This root-owned file contains only validated installer values.
source "$CONFIG"

log(){ printf '[openmain-router-update] %s\n' "$*"; }
exec 9>/run/lock/openmain-router-update.lock
flock -n 9 || { log 'Ein Update läuft bereits; dieser Lauf wird übersprungen.'; exit 0; }

export DEBIAN_FRONTEND=noninteractive
apt_opts=(-o DPkg::Lock::Timeout=1800 -o Dpkg::Options::=--force-confold)
log 'Starte apt-get update.'
apt-get "${apt_opts[@]}" update || { log 'apt-get update fehlgeschlagen; kein Neustart.'; exit 1; }
log 'Starte apt-get upgrade -y.'
apt-get "${apt_opts[@]}" upgrade -y || { log 'apt-get upgrade fehlgeschlagen; kein Neustart.'; exit 1; }
[[ -e /var/run/reboot-required ]] || { log 'Kein Neustart erforderlich.'; exit 0; }

expected_hour=04
[[ "$ROLE" == backup ]] && expected_hour=05
if [[ "$(TZ=Europe/Berlin date +%u)" != 7 || "$(TZ=Europe/Berlin date +%H)" != "$expected_hour" ]]; then
  log 'Nachgeholter oder überlanger Lauf außerhalb des Berliner Wartungsfensters; Neustart ausgesetzt.'
  exit 0
fi

if [[ "$TOPOLOGY" == pair ]]; then
  if [[ -z "${PARTNER_LAN_IP:-}" ]] || [[ ! -x /usr/local/sbin/openmain-zabbix-query ]]; then
    log 'Partneradresse oder Zabbix-Pruefer fehlt; Neustart ausgesetzt.'
    exit 0
  fi
  partner_role=backup
  [[ "$ROLE" == backup ]] && partner_role=primary
  observed_role=$(/usr/local/sbin/openmain-zabbix-query "$PARTNER_LAN_IP" openmain.router.role 2>/dev/null || true)
  ready=$(/usr/local/sbin/openmain-zabbix-query "$PARTNER_LAN_IP" openmain.router.ready 2>/dev/null || true)
  if [[ "$observed_role" != "$partner_role" || "$ready" != 1 ]]; then
    log "Partner $PARTNER_LAN_IP nicht als betriebsbereiter $partner_role verifiziert; Neustart ausgesetzt."
    exit 0
  fi
  log "Partner $PARTNER_LAN_IP ist als $partner_role mit NetBird, BINAT und LAN-Probe betriebsbereit."
elif [[ "$TOPOLOGY" != single ]]; then
  log 'Topologie unbekannt; Neustart ausgesetzt.'
  exit 0
fi

log 'Erfolgreiches Update und Neustart erforderlich; starte System neu.'
systemctl reboot
