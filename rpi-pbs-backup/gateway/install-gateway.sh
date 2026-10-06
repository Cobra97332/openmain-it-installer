#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "Als root ausführen." >&2; exit 1; }
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
apt-get update
apt-get install -y rsync openssh-server sudo
command -v proxmox-backup-client >/dev/null 2>&1 || {
  echo "FEHLER: proxmox-backup-client fehlt. Auf dem x86-Gateway zuerst den offiziellen Proxmox Backup Client installieren." >&2
  exit 1
}

install -d -m 0700 /etc/openmain
install -m 0755 "$BASE/rpi-pbs-ingest" /usr/local/sbin/rpi-pbs-ingest
install -m 0755 "$BASE/rpi-pbs-restore" /usr/local/sbin/rpi-pbs-restore
if [[ ! -e /etc/openmain/rpi-pbs-gateway.conf ]]; then
  install -m 0600 "$BASE/rpi-pbs-gateway.conf.example" /etc/openmain/rpi-pbs-gateway.conf
fi

if ! id rpi-backup >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash rpi-backup
fi
install -d -o rpi-backup -g rpi-backup -m 0700 /home/rpi-backup/.ssh
install -d -o rpi-backup -g rpi-backup -m 0700 /srv/rpi-pbs-staging

cat >/etc/sudoers.d/openmain-rpi-pbs <<'SUDO'
rpi-backup ALL=(root) NOPASSWD: /usr/local/sbin/rpi-pbs-ingest
SUDO
chmod 0440 /etc/sudoers.d/openmain-rpi-pbs
visudo -cf /etc/sudoers.d/openmain-rpi-pbs

cat <<'MSG'
Gateway-Grundinstallation abgeschlossen.

Nächste Schritte:
1. /etc/openmain/rpi-pbs-gateway.conf anpassen.
2. PBS Token-Secret in PBS_PASSWORD_FILE ablegen (0600).
3. Public Keys der Raspberry Pis nach /home/rpi-backup/.ssh/authorized_keys eintragen.
4. Auf jedem Pi den Client installieren und GATEWAY_HOST setzen.
MSG
