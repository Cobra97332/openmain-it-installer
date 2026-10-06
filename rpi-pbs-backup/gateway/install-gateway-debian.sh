#!/usr/bin/env bash
set -Eeuo pipefail

[[ $EUID -eq 0 ]] || { echo "Als root ausführen." >&2; exit 1; }

ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
[[ "$ARCH" == "amd64" || "$ARCH" == "x86_64" ]] || {
  echo "FEHLER: Gateway benötigt x86-64/amd64." >&2
  exit 1
}

if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  source /etc/os-release
fi
[[ "${ID:-}" == "debian" ]] || {
  echo "FEHLER: Unterstützt wird Debian 13 (Trixie)." >&2
  exit 1
}
[[ "${VERSION_CODENAME:-}" == "trixie" || "${VERSION_ID:-}" == "13" ]] || {
  echo "FEHLER: Debian 13 (Trixie) erforderlich. Gefunden: ${PRETTY_NAME:-unbekannt}" >&2
  exit 1
}

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates wget curl rsync openssh-server sudo

if ! command -v proxmox-backup-client >/dev/null 2>&1; then
  install -d -m 0755 /usr/share/keyrings
  wget -q https://enterprise.proxmox.com/debian/proxmox-archive-keyring-trixie.gpg \
    -O /usr/share/keyrings/proxmox-archive-keyring.gpg

  cat >/etc/apt/sources.list.d/pbs-client.sources <<'REPO'
Types: deb
URIs: http://download.proxmox.com/debian/pbs-client
Suites: trixie
Components: main
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
REPO

  apt-get update
  apt-get install -y proxmox-backup-client
fi

BASE_URL="${OPENMAIN_RPI_PBS_BASE_URL:-https://raw.githubusercontent.com/Cobra97332/openmain-it-installer/main/rpi-pbs-backup/gateway}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

for f in rpi-pbs-ingest rpi-pbs-restore; do
  curl -fsSL "$BASE_URL/$f" -o "$TMP/$f"
done

install -d -m 0700 /etc/openmain
install -m 0755 "$TMP/rpi-pbs-ingest" /usr/local/sbin/rpi-pbs-ingest
install -m 0755 "$TMP/rpi-pbs-restore" /usr/local/sbin/rpi-pbs-restore

if ! id rpi-backup >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash rpi-backup
fi
passwd -l rpi-backup >/dev/null 2>&1 || true

install -d -o rpi-backup -g rpi-backup -m 0700 /home/rpi-backup/.ssh
touch /home/rpi-backup/.ssh/authorized_keys
chown rpi-backup:rpi-backup /home/rpi-backup/.ssh/authorized_keys
chmod 0600 /home/rpi-backup/.ssh/authorized_keys

install -d -o rpi-backup -g rpi-backup -m 0700 /var/lib/openmain-rpi-pbs/staging
install -d -o root -g root -m 0700 /var/lib/openmain-rpi-pbs/restore

cat >/etc/sudoers.d/openmain-rpi-pbs <<'SUDO'
rpi-backup ALL=(root) NOPASSWD: /usr/local/sbin/rpi-pbs-ingest
SUDO
chmod 0440 /etc/sudoers.d/openmain-rpi-pbs
visudo -cf /etc/sudoers.d/openmain-rpi-pbs >/dev/null

systemctl enable --now ssh >/dev/null 2>&1 || systemctl enable --now sshd >/dev/null 2>&1 || true

echo "Gateway-Basis auf Debian 13 installiert."
echo "Konfiguration wird vom PVE-Deploy-Script nach /etc/openmain/rpi-pbs-gateway.conf übertragen."
