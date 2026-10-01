#!/usr/bin/env bash
set -Eeuo pipefail

REPO="Cobra97332/openmain-it-installer"
REF="main"
BASE="https://raw.githubusercontent.com/$REPO/$REF"
TMP="/tmp/openmain-it-installer"
ARCH="${ARCH:-amd64}"

log(){ printf '[+] %s\n' "$*"; }
die(){ printf '[FEHLER] %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Bitte als root ausführen."
command -v curl >/dev/null || die "curl fehlt."

rm -rf "$TMP"
mkdir -p "$TMP"

for f in router-install.sh zabbix-proxy-install.sh zabbix-api-register.sh; do
  log "Lade $f von GitHub"
  curl -fsSL "$BASE/$f" -o "$TMP/$f"
  chmod 0755 "$TMP/$f"
done

export OPENMAIN_INSTALLER_DIR="$TMP"

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y live-build curl ca-certificates gnupg xorriso isolinux syslinux-common

WORKDIR="${WORKDIR:-$PWD/build-$ARCH}"
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
cd "$WORKDIR"

lb config --mode debian --distribution trixie --architectures "$ARCH" --binary-images iso-hybrid --debian-installer live --archive-areas "main contrib non-free-firmware" --bootappend-live "boot=live components hostname=netbird-router username=admin" --iso-application "OpenMain-IT NetBird Router" --iso-publisher "OpenMain-IT" --iso-volume "NETBIRD_ROUTER"

mkdir -p config/package-lists config/includes.chroot/usr/local/sbin config/includes.chroot/etc/systemd/system config/includes.chroot/etc/sysctl.d config/includes.chroot/etc/nftables.d config/hooks/live
cat > config/package-lists/netbird-router.list.chroot <<'EOF'
openssh-server
nftables
curl
ca-certificates
gnupg
jq
python3
iproute2
sudo
vim-tiny
less
systemd
network-manager
EOF

cp "$TMP/router-install.sh" config/includes.chroot/usr/local/sbin/netbird-router-install
cp "$TMP/zabbix-proxy-install.sh" config/includes.chroot/usr/local/sbin/zabbix-proxy-install
cp "$TMP/zabbix-api-register.sh" config/includes.chroot/usr/local/sbin/zabbix-api-register
chmod 0755 config/includes.chroot/usr/local/sbin/*

cat > config/includes.chroot/etc/sysctl.d/99-netbird-router.conf <<'EOF'
net.ipv4.ip_forward=1
EOF

cat > config/includes.chroot/etc/nftables.conf <<'EOF'
#!/usr/sbin/nft -f
flush ruleset
include "/etc/nftables.d/*.nft"
EOF

cat > config/hooks/live/010-netbird.hook.chroot <<'EOF'
#!/bin/sh
set -eu
install -d -m 0755 /usr/share/keyrings
curl -fsSL https://pkgs.netbird.io/debian/public.key | gpg --dearmor --yes -o /usr/share/keyrings/netbird-archive-keyring.gpg
echo 'deb [signed-by=/usr/share/keyrings/netbird-archive-keyring.gpg] https://pkgs.netbird.io/debian stable main' > /etc/apt/sources.list.d/netbird.list
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y netbird
systemctl enable netbird.service || true
systemctl enable nftables.service || true
systemctl enable ssh.service || true
EOF
chmod 0755 config/hooks/live/010-netbird.hook.chroot

log "Baue ISO ..."
lb build

ISO=$(find . -maxdepth 1 -type f \( -name '*.hybrid.iso' -o -name '*.iso' \) | head -n1)
[[ -n "$ISO" ]] || die "ISO wurde nicht erzeugt."

OUT="$PWD/../netbird-router-debian13-$ARCH.iso"
cp "$ISO" "$OUT"
sha256sum "$OUT" | tee "$OUT.sha256"
log "Fertig: $OUT"
