#!/usr/bin/env bash
set -Eeuo pipefail

# OpenMain-IT NetBird Router
# Basis: offizielles Debian-13-Netinst-ISO.
# Es wird kein eigenes Debian-Live-System gebaut.
# Wir remastern nur die Debian-Installer-Bootparameter und legen
# OpenMain-IT First-Boot-Dateien auf die ISO.

REPO="Cobra97332/openmain-it-installer"
REF="main"
API="https://api.github.com/repos/$REPO/contents"
CD_BASE="${DEBIAN_CD_BASE:-https://cloudfront.debian.net/cdimage/release/current/amd64/iso-cd}"
ARCH="${ARCH:-amd64}"
WORKDIR="${WORKDIR:-$PWD/debian-netinst-build-$ARCH}"
TMP="$WORKDIR/tmp"
TREE="$WORKDIR/iso-tree"
OUT="${OUT:-$PWD/netbird-router-debian13-$ARCH.iso}"

log(){ printf '\033[1;32m[+]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[FEHLER]\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Bitte als root ausführen."
[[ "$ARCH" == amd64 ]] || die "Aktuell unterstützt: ARCH=amd64."
command -v curl >/dev/null 2>&1 || die "curl fehlt."

github_download(){
  local file="$1" out="$2"
  curl -fsSL -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28" \
    "$API/$file?ref=$REF" |
    jq -er '.content' |
    tr -d '\n\r' |
    base64 -d > "$out"
}


apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates xorriso jq

rm -rf "$WORKDIR"
mkdir -p "$TMP" "$TREE"

log "Ermittle aktuelles offizielles Debian-13-Netinst-ISO ..."
ISO_NAME="${DEBIAN_ISO_NAME:-}"
if [[ -z "$ISO_NAME" ]]; then
  ISO_NAME="$(curl -fsSL "$CD_BASE/" |
    grep -oE 'debian-13\.[0-9]+\.[0-9]+-amd64-netinst\.iso' |
    sort -V | tail -n1)"
fi
[[ -n "$ISO_NAME" ]] || die "Kein Debian-13-Netinst-ISO gefunden."

ISO_URL="$CD_BASE/$ISO_NAME"
ISO="$TMP/$ISO_NAME"

log "Lade: $ISO_URL"
curl -fL --retry 3 --retry-delay 2 "$ISO_URL" -o "$ISO"

log "Prüfe Debian SHA512SUMS ..."
curl -fsSL "$CD_BASE/SHA512SUMS" -o "$TMP/SHA512SUMS"
grep -E "  $ISO_NAME$" "$TMP/SHA512SUMS" > "$TMP/SHA512SUMS.one"
(
  cd "$TMP"
  sha512sum -c SHA512SUMS.one
)

log "Extrahiere die Debian-Netinst-Dateien ..."
xorriso -osirrox on -indev "$ISO" -extract / "$TREE" >/dev/null

[[ -f "$TREE/isolinux/txt.cfg" ]] || die "Debian isolinux/txt.cfg nicht gefunden."
[[ -f "$TREE/boot/grub/grub.cfg" ]] || die "Debian boot/grub/grub.cfg nicht gefunden."

for f in preseed.cfg firstboot-router.sh openmain-router-firstboot.service; do
  log "Lade $f von GitHub ..."
  github_download "$f" "$TMP/$f"
done

mkdir -p "$TREE/openmain-installer"
cp "$TMP/preseed.cfg" "$TREE/preseed.cfg"
cp "$TMP/firstboot-router.sh" "$TREE/openmain-installer/firstboot-router.sh"
cp "$TMP/openmain-router-firstboot.service" "$TREE/openmain-installer/openmain-router-firstboot.service"

chmod 0644 "$TREE/preseed.cfg"
chmod 0755 "$TREE/openmain-installer/firstboot-router.sh"
chmod 0644 "$TREE/openmain-installer/openmain-router-firstboot.service"

# Debian Installer mit lokalem Preseed starten.
# Debian dokumentiert preseed/file=/cdrom/preseed.cfg für remasterte Installationsmedien.
sed -i 's#---#auto=true priority=critical preseed/file=/cdrom/preseed.cfg ---#g' "$TREE/isolinux/txt.cfg"
sed -i 's#---#auto=true priority=critical preseed/file=/cdrom/preseed.cfg ---#g' "$TREE/boot/grub/grub.cfg"

# Nur geänderte/zusätzliche Dateien in die bestehende ISO schreiben.
# -boot_image any replay erhält die vorhandene BIOS/UEFI-Bootausstattung.
rm -f "$OUT"

xorriso   -indev "$ISO"   -outdev "$OUT"   -overwrite on   -map "$TREE/isolinux/txt.cfg" /isolinux/txt.cfg   -map "$TREE/boot/grub/grub.cfg" /boot/grub/grub.cfg   -map "$TREE/preseed.cfg" /preseed.cfg   -map "$TREE/openmain-installer" /openmain-installer   -boot_image any replay   -commit >/dev/null

[[ -s "$OUT" ]] || die "ISO wurde nicht erzeugt."

log "Prüfe erzeugte ISO ..."
xorriso -indev "$OUT" -find /preseed.cfg -print >/dev/null
xorriso -indev "$OUT" -find /openmain-installer -print >/dev/null
xorriso -indev "$OUT" -boot_image any show_status 2>&1 | head -n 40 || true

sha256sum "$OUT" | tee "$OUT.sha256"

log "Fertig."
log "Basis: offizielles Debian 13 Netinst"
log "ISO: $OUT"
