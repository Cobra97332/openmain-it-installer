#!/usr/bin/env bash
set -Eeuo pipefail
# Build a Debian 13 amd64 NETINSTALL image; never copy runtime config/secrets.
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
OUTPUT_ISO="${OUTPUT_ISO:-$PWD/openmain-debian13-amd64-netinst.iso}"
[[ ! -e "$OUTPUT_ISO" ]] || { echo 'Ausgabe existiert; neuen Dateinamen verwenden.' >&2; exit 1; }
for tool in curl xorriso python3 sha512sum; do
  command -v "$tool" >/dev/null || { printf 'Build-Voraussetzung fehlt: %s\n' "$tool" >&2; exit 1; }
done
BUILD_DIR=$(mktemp -d)
trap 'rm -rf -- "$BUILD_DIR"' EXIT
ISO_DIR="$BUILD_DIR/iso"
mkdir "$ISO_DIR"
log(){ printf '%s\n' "$*"; }
die(){ printf '%s\n' "$*" >&2; exit 1; }
BASE=https://cdimage.debian.org/debian-cd/current/amd64/iso-cd
if [[ -n "${DEBIAN_ISO:-}" ]]; then
  [[ -n "${DEBIAN_ISO_SHA512:-}" ]] || die 'Lokales ISO benoetigt vertrauenswuerdige DEBIAN_ISO_SHA512.'
  ISO_FILE=$(realpath "$DEBIAN_ISO")
  [[ "$(sha512sum "$ISO_FILE" | cut -d ' ' -f1)" == "$DEBIAN_ISO_SHA512" ]] || die 'ISO-Pruefsumme falsch.'
else
  command -v gpgv >/dev/null || die 'Build-Voraussetzung fehlt: gpgv (Signaturpruefung erforderlich).'
  KEYRING="${DEBIAN_CD_KEYRING:-/usr/share/keyrings/debian-role-keys.gpg}"
  [[ -r "$KEYRING" ]] || die 'Debian-CD-Signaturschluesselring fehlt (debian-keyring installieren / DEBIAN_CD_KEYRING setzen).'
  curl -fsSL --max-time 60 "$BASE/SHA512SUMS" -o "$BUILD_DIR/SHA512SUMS"
  curl -fsSL --max-time 60 "$BASE/SHA512SUMS.sign" -o "$BUILD_DIR/SHA512SUMS.sign"
  gpgv --keyring "$KEYRING" "$BUILD_DIR/SHA512SUMS.sign" "$BUILD_DIR/SHA512SUMS"
  DEBIAN_NAME=$(awk '$2 ~ /^debian-13[.][0-9.]+-amd64-netinst.iso$/ {print $2}' "$BUILD_DIR/SHA512SUMS")
  [[ -n "$DEBIAN_NAME" && "$DEBIAN_NAME" != *$'\n'* ]] || die 'Kein eindeutiges Debian-13-Netinst in signiertem Manifest.'
  ISO_FILE="$BUILD_DIR/$DEBIAN_NAME"
  curl -fSL --retry 3 "$BASE/$DEBIAN_NAME" -o "$ISO_FILE"
  (cd "$BUILD_DIR"; awk -v n="$DEBIAN_NAME" '$2==n' SHA512SUMS | sha512sum -c -)
fi
xorriso -osirrox on -indev "$ISO_FILE" -extract / "$ISO_DIR" >/dev/null 2>&1
chmod -R u+w "$ISO_DIR"
grep -Eq 'Debian GNU/Linux 13[.]' "$ISO_DIR/.disk/info" || die 'ISO ist kein Debian 13.'
cp "$ROOT/preseed.cfg" "$ISO_DIR/preseed.cfg"
mkdir -p "$ISO_DIR/openmain-installer"
while IFS= read -r file; do
  [[ -n "$file" && "$file" != /* && "$file" != *..* ]] || die 'Ungueltiges Bundle-Manifest.'
  [[ -f "$ROOT/$file" && ! -L "$ROOT/$file" ]] || die 'Bundle-Datei fehlt/ist Symlink.'
  mkdir -p "$ISO_DIR/openmain-installer/$(dirname "$file")"
  cp "$ROOT/$file" "$ISO_DIR/openmain-installer/$file"
done < "$ROOT/netbird-router/bundle.files"
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
    if line.strip().startswith("linux"):
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


(cd "$ISO_DIR"; find . -type f ! -path './isolinux/boot.cat' ! -name md5sum.txt -print0 | xargs -0 md5sum > "$BUILD_DIR/md5sum.txt")
mv "$BUILD_DIR/md5sum.txt" "$ISO_DIR/md5sum.txt"
xorriso -indev "$ISO_FILE" -outdev "$OUTPUT_ISO" -overwrite on \
  -update_r "$ISO_DIR" / -volid OPENMAIN_DEBIAN13 \
  -boot_image any replay -commit
[[ -s "$OUTPUT_ISO" ]] || die 'ISO-Build fehlgeschlagen.'
xorriso -indev "$OUTPUT_ISO" -report_el_torito plain
sha512sum "$OUTPUT_ISO" > "$OUTPUT_ISO.sha512"
log "Netinstall-ISO erstellt: $OUTPUT_ISO (keine Kundendaten/Zugangsdaten enthalten)."
