#!/usr/bin/env python3
"""Real QEMU boots of a Debian 13 test VM and a SYNTHETIC remaster fixture.

NOT a Debian Installer / netinstall acceptance test. The fixture has a real
Debian kernel, initramfs and BIOS/UEFI loaders, but boots a debootstrap disk.
Requires a disposable Docker --network none container, /src read-only and
an independently bootstrapped Debian 13 root under /lab/amd64.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys


def guest_evidence(output):
    """Fixed proof flags only: never publish the guest console or test data."""
    return {
        'unit_failure': 'FAILED (' in output,
        'qa_pass': 'OPENMAIN_QA_PASS' in output,
        'qa_failure': 'OPENMAIN_QA_FAIL' in output,
        'ct_readback_pass': 'PASS: actual kernel original/reply address/port readback, stable UserData and handles' in output,
        'tun_sigkill_pass': 'SIGKILL removes interface and blocks NEW tuple; foreign rules preserved' in output,
        'tun_failstop_pass': 'unconfirmed-closure fail-stop removes interface and blocks NEW tuple; foreign rules preserved' in output,
        'legacy_p1_markers': {name: name in output for name in (
            'P1_AFTER_FINAL_READ_STALE_GRANT:45707',
            'P1_AFTER_FINAL_READ_STALE_GRANT:45709',
            'P1_REJECTED_PROOF_DESIGN_BYPASSES_NEW_NATIVE_DROP:47101',
            'P1_REJECTED_PROOF_DESIGN_BYPASSES_NEW_NATIVE_DROP:47102',
            'P1_ESTABLISHED_CONNTRACK_BYPASSES_WITHDRAWAL',
            'P1_REPLY_ALLOWED_VIA_NETBIRD_EXTERNAL_ESTABLISHED')},
    }


def run(command, log):
    with log.open('wb') as stream:
        subprocess.run(command, check=True, stdout=stream, stderr=subprocess.STDOUT)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--native-readback', help='Compiled conntrack ABI probe; mandatory when supplied')
    parser.add_argument('--native-small',help='Compiled small native driver for mandatory TUN-lifetime proof in VM')
    parser.add_argument('--rootfs',default='/lab/amd64')
    parser.add_argument('--work',default='/lab/qemu')
    parser.add_argument('--vm-only', action='store_true', help='Debian AMD64 VM security gates; no ISO support claim')
    parser.add_argument('--unit-only',action='store_true',help='Debug unit lifecycle only; not a regression-suite pass')
    args=parser.parse_args()
    if (os.environ.get('OPENMAIN_ISOLATED_TEST')!='1' or not Path('/.dockerenv').exists()
        or {name for _,name in socket.if_nameindex()} != {'lo'}):
        raise SystemExit('Requires explicitly isolated Docker --network none')
    root,work=Path(args.rootfs),Path(args.work)
    if any(p.is_symlink() for p in (root,*root.parents,work,*work.parents)):
        raise SystemExit('Symlinked lab paths refused')
    if not root.is_relative_to('/lab') or not work.is_relative_to('/lab') or work.exists():
        raise SystemExit('Root/work must be lab-only; use a NEW work directory')
    if 'VERSION_ID="13"' not in (root/'etc/os-release').read_text():
        raise SystemExit('Debian 13 root required')
    work.mkdir(mode=0o700,parents=True)
    source=Path('/src')
    names=set((source/'netbird-router/bundle.files').read_text().splitlines())
    names.add('netbird-router/experimental/native-binat/snapshot.py')
    # Lab.main's negative startup tests bind the actual public patch digests.
    # Copy the inputs too; no profiles/keys or compiled client are installed.
    names.update('netbird-router/experimental/native-binat/' + name for name in
                 ('netbird-v0.80.0-small-native.patch', 'google-nftables-v0.3.0-ct-direction.patch'))
    # Offline lab-contract tests need these public source/build fixtures too.
    # They do NOT start Docker/controller/clients inside the platform guest.
    lab_fixture='netbird-router/experimental/native-binat/docker-lab/'
    names.update(lab_fixture+n for n in ('lab.py','container.py','packets.py','traffic.py','related.py','passive.py','Dockerfile.client'))
    names.update(lab_fixture+n for n in ('faults.py','inject.py','injectors/apply.py','injectors/engine.go','injectors/netlink.go'))
    names.update(['build-iso.sh','preseed.cfg','tests/Dockerfile.router','netbird-prometheus/sync-client-targets.py'])
    names.update(str(p.relative_to(source)) for p in (source/'tests').glob('*.py'))
    for name in sorted(names):
        src=source/name;dst=root/'src'/name
        if not src.is_file() or src.is_symlink():raise SystemExit('Unsafe source fixture')
        dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
    if args.native_small:
        driver=Path(args.native_small)
        if not driver.is_absolute() or not driver.is_file() or driver.is_symlink():
            raise SystemExit('Verified regular native driver path required')
        destination=root/'native/small-native-lab'
        destination.parent.mkdir(exist_ok=True);shutil.copyfile(driver,destination);destination.chmod(0o755)
    if args.native_readback:
        probe=Path(args.native_readback)
        if not probe.is_absolute() or not probe.is_file() or probe.is_symlink():
            raise SystemExit('Verified regular native readback probe required')
        destination=root/'native/ct-readback'
        destination.parent.mkdir(exist_ok=True);shutil.copyfile(probe,destination);destination.chmod(0o755)
    kernel=next(Path('/boot').glob('vmlinuz-*-amd64'))
    initrd=kernel.with_name(kernel.name.replace('vmlinuz-','initrd.img-'))
    version=kernel.name.removeprefix('vmlinuz-')
    shutil.copytree(Path('/usr/lib/modules')/version,root/'usr/lib/modules'/version,dirs_exist_ok=True)
    unit=root/'etc/systemd/system/openmain-qa.service'
    unit.write_text('''[Unit]
Description=Isolated OpenMain VM test (no production connections)
After=basic.target
[Service]
Type=oneshot
ExecStart=/bin/bash /root/qa.sh
StandardOutput=journal+console
StandardError=journal+console
TimeoutStartSec=10min
[Install]
WantedBy=multi-user.target
''')
    wants=unit.parent/'multi-user.target.wants';wants.mkdir(exist_ok=True)
    (wants/unit.name).unlink(missing_ok=True)
    (wants/unit.name).symlink_to('../'+unit.name)
    (root/'root/firstboot-unit-fixture.sh').write_text('umask 077; touch /var/lib/openmain-router-firstboot.done\n')
    # Only ExecStart is replaced for this unit lifecycle test. No live core run.
    firstboot=(source/'openmain-router-firstboot.service').read_text().replace(
        '/opt/openmain-router/firstboot-router.sh','/root/firstboot-unit-fixture.sh')
    (unit.parent/'openmain-router-firstboot.service').write_text(firstboot)
    (root/'root/qa.sh').write_text('''set -Eeuo pipefail
trap 'echo OPENMAIN_QA_FAIL_LINE_${LINENO}; systemctl --no-block poweroff' ERR
export PYTHONDONTWRITEBYTECODE=1
cd /src
echo OPENMAIN_QA_START
python3 netbird-router/common/runtime.py platform
if grep -q openmain.lab=iso /proc/cmdline; then
  for pattern in test_router_entrypoints.py test_router_lifecycle.py test_router_platforms.py test_binat_acl.py; do
    python3 -m unittest discover -s tests -p "$pattern" -v
  done
else
  python3 -m unittest discover -s tests -v
  sysctl -w net.ipv4.ip_forward=1 >/dev/null
  packet_status=0
  OPENMAIN_ISOLATED_TEST=1 python3 tests/nft_kernel.py || packet_status=1
  test "$packet_status" = 0
fi
echo OPENMAIN_UNIT_VERIFY
systemd-analyze verify /etc/systemd/system/openmain-router-firstboot.service
echo OPENMAIN_UNIT_START
systemctl start openmain-router-firstboot.service
echo OPENMAIN_UNIT_STATE
test "$(systemctl show -p ActiveState --value openmain-router-firstboot.service)" = inactive
echo OPENMAIN_UNIT_GETTY
for attempt in {1..50}; do
  systemctl is-active --quiet getty@tty1.service && break
  sleep 0.1
done
systemctl is-active --quiet getty@tty1.service
echo OPENMAIN_QA_PASS
systemctl --no-block poweroff
''')
    if args.native_small:
        qa=root/'root/qa.sh'
        text=qa.read_text()
        text=text.replace('  packet_status=0', '  packet_status=0\n  OPENMAIN_ISOLATED_TEST=1 unshare --net python3 tests/nft_small_tun_lifetime.py || packet_status=1\n  OPENMAIN_ISOLATED_TEST=1 OPENMAIN_TUN_CLOSURE_FAILURE=1 unshare --net python3 tests/nft_small_tun_lifetime.py || packet_status=1')
        qa.write_text(text)
    if args.native_readback:
        qa=root/'root/qa.sh'
        qa.write_text(qa.read_text().replace('  packet_status=0',
            '  packet_status=0\n  OPENMAIN_ISOLATED_TEST=1 unshare --net /native/ct-readback || packet_status=1'))
    if args.unit_only:
        script=root/'root/qa.sh'
        text=script.read_text()
        script.write_text(text[:text.index('if grep -q openmain.lab=iso')]+text[text.index('echo OPENMAIN_UNIT_VERIFY'):])
    if not args.vm_only and not args.unit_only:
        iso_root=work/'fixture';(iso_root/'.disk').mkdir(parents=True)
        (iso_root/'.disk/info').write_text('Debian GNU/Linux 13.6 SYNTHETIC kernel probe; NOT Debian Installer\n')
        (iso_root/'isolinux').mkdir()
        (iso_root/'isolinux/menu.cfg').write_text('default install\n')
        (iso_root/'isolinux/txt.cfg').write_text('label install\n append initrd=/install.amd/initrd.gz --- quiet\n')
        install=iso_root/'install.amd';install.mkdir()
        shutil.copyfile(kernel,install/'vmlinuz');shutil.copyfile(initrd,install/'initrd.gz')
        grub=iso_root/'boot/grub';grub.mkdir(parents=True)
        (grub/'grub.cfg').write_text('''set timeout=1
set default=0
menuentry 'SYNTHETIC probe (not Debian Installer)' {
 linux /install.amd/vmlinuz root=/dev/vda rw console=ttyS0 panic=-1 openmain.lab=iso --- quiet
 initrd /install.amd/initrd.gz
}
''')
        base=work/'fixture-base.iso';image=work/'openmain-fixture.iso'
        # No Apple HFS+/APM layout: exercise amd64 BIOS/EFI CD boot structures.
        run(['grub-mkrescue','--arcs-boot','-o',str(base),str(iso_root)],work/'fixture-build.log')
        env=dict(os.environ,DEBIAN_ISO=str(base),DEBIAN_ISO_SHA512=hashlib.sha512(base.read_bytes()).hexdigest(),OUTPUT_ISO=str(image))
        with (work/'remaster.log').open('wb') as stream:
            subprocess.run(['bash','/src/build-iso.sh'],env=env,check=True,stdout=stream,stderr=subprocess.STDOUT)
    failures=[]
    for mode in (('vm',) if args.unit_only or args.vm_only else ('vm','iso-bios','iso-uefi')):
        disk=work/(mode+'.raw')
        with disk.open('xb') as stream:stream.truncate(4*1024**3)
        run(['mke2fs','-q','-t','ext4','-F','-d',str(root),str(disk)],work/(mode+'-disk.log'))
        command=['qemu-system-x86_64','-accel','tcg','-cpu','max','-machine','q35','-m','2048','-smp','2',
                 '-display','none','-serial','stdio','-monitor','none','-nic','none','-no-reboot',
                 '-drive','file='+str(disk)+',format=raw,if=virtio']
        if mode=='vm':command+=['-kernel',str(kernel),'-initrd',str(initrd),'-append','root=/dev/vda rw console=ttyS0 panic=-1 openmain.lab=vm']
        else:command+=['-cdrom',str(image),'-boot','once=d']
        if mode=='iso-uefi':
            firmware=Path('/usr/share/OVMF');variables=work/'OVMF_VARS.fd'
            shutil.copyfile(firmware/'OVMF_VARS_4M.fd',variables)
            command+=['-drive','if=pflash,format=raw,readonly=on,file='+str(firmware/'OVMF_CODE_4M.fd'),
                      '-drive','if=pflash,format=raw,file='+str(variables)]
        log=work/(mode+'.log')
        with log.open('wb') as stream:
            subprocess.run(command,check=True,timeout=600,stdout=stream,stderr=subprocess.STDOUT)
        output=log.read_text(errors='replace')
        print(json.dumps({'mode': mode, 'guest_evidence': guest_evidence(output)}), flush=True)
        if 'OPENMAIN_QA_PASS' not in output or 'OPENMAIN_QA_FAIL' in output:
            failures.append(mode)
            print(mode+': FAIL; inspect lab-only log',flush=True)
            continue
        print(mode+': PASS ('+('unit lifecycle ONLY' if args.unit_only else 'real Debian 13/QEMU, mocked router services/APIs')+')',flush=True)
    if not args.unit_only and not args.vm_only:print('ISO result applies ONLY to the synthetic fixture, not official netinstall.',flush=True)
    if failures:raise SystemExit('Failed QEMU release gates: '+', '.join(failures))


if __name__=='__main__':main()
