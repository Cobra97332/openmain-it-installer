#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "Als root ausführen." >&2; exit 1; }
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
apt-get update
apt-get install -y rsync openssh-client
install -d -m 0700 /etc/openmain /root/.ssh
install -m 0755 "$BASE/rpi-pbs-backup.sh" /usr/local/sbin/rpi-pbs-backup
install -m 0644 "$BASE/rpi-pbs-backup.service" /etc/systemd/system/rpi-pbs-backup.service
install -m 0644 "$BASE/rpi-pbs-backup.timer" /etc/systemd/system/rpi-pbs-backup.timer
if [[ ! -e /etc/openmain/rpi-pbs-backup.conf ]]; then
  install -m 0600 "$BASE/rpi-pbs-backup.conf.example" /etc/openmain/rpi-pbs-backup.conf
fi
if [[ ! -e /root/.ssh/openmain-rpi-pbs ]]; then
  ssh-keygen -t ed25519 -N '' -f /root/.ssh/openmain-rpi-pbs -C "openmain-rpi-pbs@$(hostname -s)"
fi
systemctl daemon-reload
systemctl enable rpi-pbs-backup.timer
cat <<MSG

Installiert.
1. /etc/openmain/rpi-pbs-backup.conf anpassen.
2. Diesen Public Key auf dem Gateway-Benutzer rpi-backup hinterlegen:

$(cat /root/.ssh/openmain-rpi-pbs.pub)

3. Test: systemctl start rpi-pbs-backup.service
4. Timer: systemctl enable --now rpi-pbs-backup.timer
MSG
