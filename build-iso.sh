#!/usr/bin/env bash
set -Eeuo pipefail

# OpenMain-IT NetBird Router
# Basis: offizielles Debian 13 Netinst.
# Der Build verwendet bewusst den bereits getesteten V8-ISO-Weg:
# ISO mounten -> kompletten ISO-Baum kopieren -> Dateien/Bootparameter
# patchen -> ISO mit xorriso neu erzeugen.
#
# Keine Secrets werden benötigt oder in die ISO geschrieben.

REPO="Cobra97332/openmain-it-installer"
REF="main"
RAW_BASE="https://raw.githubusercontent.com/$REPO/$REF"

ARCH="${ARCH:-amd64}"
WORKDIR="${WORKDIR:-$PWD/build-netbird-router}"
BUILD_DIR="$WORKDIR/build"
ISO_DIR="$BUILD_DIR/iso"
MNT_DIR="$BUILD_DIR/mnt"
TMP_DIR="$BUILD_DIR/tmp"
OUTPUT_ISO="${OUTPUT_ISO:-$PWD/netbird-router-debian13-$ARCH.iso}"

log(){ printf '\033[1;32m[+]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[FEHLER]\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Bitte als root ausführen."
[[ "$ARCH" == amd64 ]] || die "Aktuell unterstützt: amd64."
command -v curl >/dev/null 2>&1 || die "curl fehlt."

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  curl wget rsync xorriso isolinux syslinux-utils python3 ca-certificates

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR" "$ISO_DIR" "$MNT_DIR" "$TMP_DIR"

log "Ermittle aktuelles offizielles Debian-13-Netinst-ISO ..."
DEBIAN_ISO="$(
  wget -qO- https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/ |
    grep -o 'debian-[0-9.]*-amd64-netinst.iso' |
    sort -V |
    tail -n1
)"

[[ -n "$DEBIAN_ISO" ]] || die "Konnte Debian-13-Netinst-ISO nicht ermitteln."

ISO_URL="https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/$DEBIAN_ISO"
ISO_FILE="$BUILD_DIR/$DEBIAN_ISO"

log "Nutze ISO: $DEBIAN_ISO"

if [[ ! -s "$ISO_FILE" ]]; then
  log "Lade Debian-Netinst herunter ..."
  wget -O "$ISO_FILE" "$ISO_URL"
else
  log "Debian-Netinst ist bereits vorhanden."
fi

log "Bereite kompletten Debian-ISO-Baum vor ..."
mount -o loop "$ISO_FILE" "$MNT_DIR"
rsync -a "$MNT_DIR/" "$ISO_DIR/"
umount "$MNT_DIR"

for f in preseed.cfg firstboot-router.sh openmain-router-firstboot.service authorized_keys; do
  log "Lade $f aus dem öffentlichen GitHub-Repository ..."
  curl -fsSL "$RAW_BASE/$f" -o "$ISO_DIR/$f"
done

mkdir -p "$ISO_DIR/openmain-installer"
cp "$ISO_DIR/firstboot-router.sh" "$ISO_DIR/openmain-installer/firstboot-router.sh"
cp "$ISO_DIR/openmain-router-firstboot.service" "$ISO_DIR/openmain-installer/openmain-router-firstboot.service"

chmod 0644 "$ISO_DIR/preseed.cfg" "$ISO_DIR/authorized_keys"
chmod 0755 "$ISO_DIR/firstboot-router.sh"
chmod 0644 "$ISO_DIR/openmain-router-firstboot.service"

log "Patche BIOS-Bootmenü ..."

python3 - "$ISO_DIR/isolinux" <<'PY'
import sys
from pathlib import Path

root=Path(sys.argv[1])
boot_args=(
    "auto=true priority=high file=/cdrom/preseed.cfg "
    "apt-setup/cdrom/set-first=false apt-setup/cdrom/set-next=false "
    "apt-setup/cdrom/set-failed=false apt-setup/disable-cdrom-entries=true "
    "apt-setup/use_mirror=true debian-installer/language=de "
    "debian-installer/country=DE debian-installer/locale=de_DE.UTF-8 "
    "locale=de_DE.UTF-8 keyboard-configuration/xkb-keymap=de "
    "netcfg/get_domain=local "
)
remove=[
    "auto=true","priority=critical","priority=high","file=/cdrom/preseed.cfg",
    "apt-setup/cdrom/set-first=false","apt-setup/cdrom/set-next=false",
    "apt-setup/cdrom/set-failed=false","apt-setup/disable-cdrom-entries=true",
    "apt-setup/use_mirror=true","debian-installer/language=de",
    "debian-installer/country=DE","debian-installer/locale=de_DE.UTF-8",
    "locale=de_DE.UTF-8","keyboard-configuration/xkb-keymap=de",
    "netcfg/get_hostname=debian-wg","netcfg/get_domain=local"
]
for p in root.glob("*.cfg"):
    t=p.read_text(errors="ignore")
    lines=[]
    for line in t.splitlines():
        if line.strip().startswith("append ") and "initrd=" in line:
            for x in remove:
                line=line.replace(x,"")
            line=" ".join(line.split())
            if " --- " in line:
                a,b=line.split(" --- ",1)
                line=f"{a} {boot_args}--- {b}"
            else:
                line=f"{line} {boot_args}"
        lines.append(line)
    p.write_text("\n".join(lines)+"\n")
PY

cat > "$ISO_DIR/isolinux/isolinux.cfg" <<'EOF'
default install
prompt 0
timeout 1
include menu.cfg
EOF

if [[ -f "$ISO_DIR/isolinux/menu.cfg" ]]; then
  sed -i 's/^default .*/default install/' "$ISO_DIR/isolinux/menu.cfg" || true
  if grep -q '^timeout ' "$ISO_DIR/isolinux/menu.cfg"; then
    sed -i 's/^timeout .*/timeout 1/' "$ISO_DIR/isolinux/menu.cfg"
  else
    echo 'timeout 1' >> "$ISO_DIR/isolinux/menu.cfg"
  fi
fi

log "Patche UEFI/GRUB-Bootmenü ..."

for grubcfg in "$ISO_DIR/boot/grub/grub.cfg" "$ISO_DIR/EFI/BOOT/grub.cfg"; do
  if [[ -f "$grubcfg" ]]; then
    python3 - "$grubcfg" <<'PY'
import sys
from pathlib import Path
p=Path(sys.argv[1])
t=p.read_text(errors="ignore")
boot=(
    "auto=true priority=high file=/cdrom/preseed.cfg "
    "apt-setup/cdrom/set-first=false apt-setup/cdrom/set-next=false "
    "apt-setup/cdrom/set-failed=false apt-setup/disable-cdrom-entries=true "
    "apt-setup/use_mirror=true debian-installer/language=de "
    "debian-installer/country=DE debian-installer/locale=de_DE.UTF-8 "
    "locale=de_DE.UTF-8 keyboard-configuration/xkb-keymap=de "
    "netcfg/get_domain=local "
)
remove=[
    "auto=true","priority=critical","priority=high","file=/cdrom/preseed.cfg",
    "apt-setup/cdrom/set-first=false","apt-setup/cdrom/set-next=false",
    "apt-setup/cdrom/set-failed=false","apt-setup/disable-cdrom-entries=true",
    "apt-setup/use_mirror=true","debian-installer/language=de",
    "debian-installer/country=DE","debian-installer/locale=de_DE.UTF-8",
    "locale=de_DE.UTF-8","keyboard-configuration/xkb-keymap=de",
    "netcfg/get_hostname=debian-wg","netcfg/get_domain=local"
]
lines=[]
for line in t.splitlines():
    if line.strip().startswith("linux") and "initrd=" in line:
        for x in remove: line=line.replace(x,"")
        line=" ".join(line.split())
        if " --- " in line:
            a,b=line.split(" --- ",1); line=f"{a} {boot}--- {b}"
        else: line=f"{line} {boot}"
    lines.append(line)
t="\n".join(lines)+"\n"
for n in ("30","10","5"):
    t=t.replace(f"set timeout={n}", "set timeout=1")
if not t.startswith("set timeout="): t="set timeout=1\n"+t
p.write_text(t)
PY
  fi
done

log "Aktualisiere md5sum.txt ..."
cd "$ISO_DIR"
if [[ -f md5sum.txt ]]; then
  find . -type f ! -path './isolinux/boot.cat' ! -name 'md5sum.txt' -print0 |
    xargs -0 md5sum | sed 's# ./#  ./#' > md5sum.txt
fi
cd "$WORKDIR"

log "Baue ISO mit dem bewährten Debian-V8-Verfahren ..."

rm -f "$OUTPUT_ISO"
xorriso -as mkisofs \
  -r -V "DEBIAN_NB_AUTO" \
  -o "$OUTPUT_ISO" \
  -J -joliet-long \
  -cache-inodes \
  -isohybrid-mbr /usr/lib/ISOLINUX/isohdpfx.bin \
  -b isolinux/isolinux.bin \
  -c isolinux/boot.cat \
  -boot-load-size 4 \
  -boot-info-table \
  -no-emul-boot \
  "$ISO_DIR"

[[ -s "$OUTPUT_ISO" ]] || die "ISO wurde nicht erzeugt."

sha256sum "$OUTPUT_ISO" | tee "$OUTPUT_ISO.sha256"

log "Fertig: $OUTPUT_ISO"
log "Basis: offizielles Debian 13 Netinst"
log "Methode: getesteter V8 ISO-Build"
