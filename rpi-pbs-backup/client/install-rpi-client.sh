#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "Als root ausführen." >&2; exit 1; }
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
apt-get update
apt-get install -y rsync openssh-client openssh-server
install -d -m 0700 /etc/openmain /root/.ssh
SSH_PUBLIC_KEY="${OPENMAIN_SSH_PUBLIC_KEY:-ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJc8VZvZ7o/8emKoGC7UXPiOMP8PSxch6P2rUGNio8Vi Stefan}"
install -d -m 0700 /root/.ssh
touch /root/.ssh/authorized_keys
grep -qxF "$SSH_PUBLIC_KEY" /root/.ssh/authorized_keys 2>/dev/null || printf '%s\n' "$SSH_PUBLIC_KEY" >> /root/.ssh/authorized_keys
chown root:root /root/.ssh/authorized_keys
chmod 0600 /root/.ssh/authorized_keys
install -d -m 0755 /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/00-openmain-root-key.conf <<'EOF'
PermitRootLogin prohibit-password
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
sshd -t
systemctl enable --now ssh >/dev/null 2>&1 || systemctl enable --now sshd >/dev/null 2>&1 || true
systemctl restart ssh >/dev/null 2>&1 || systemctl restart sshd >/dev/null 2>&1 || true
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
