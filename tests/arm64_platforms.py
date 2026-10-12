#!/usr/bin/env python3
"""Execute real Debian ARM64 Python under QEMU-user; APIs/hardware are mocks.

The foreign bootstrap is unpacked, not booted/configured as a Raspberry Pi OS.
Run only in disposable Docker --network none with verified debootstrap cache.
"""
import os
from pathlib import Path
import socket
import subprocess

if (os.environ.get('OPENMAIN_ISOLATED_TEST')!='1' or not Path('/.dockerenv').exists()
    or {name for _,name in socket.if_nameindex()} != {'lo'}):
    raise SystemExit('Requires explicitly isolated Docker --network none')
root=Path('/lab/arm64')
if any(p.is_symlink() for p in (root,*root.parents)) or not (root/'debootstrap/debootstrap').is_file():
    raise SystemExit('Fresh foreign debootstrap lab root required')
packages=sorted((root/'var/cache/apt/archives').glob('*.deb'))
if not packages:raise SystemExit('Verified debootstrap package cache missing')
for package in packages:
    subprocess.run(['dpkg-deb','-x',str(package),str(root)],check=True)
code='''import platform,sys,unittest
assert platform.machine() == "aarch64"
print("Actual ARM64 userspace:", platform.machine(),sys.version,flush=True)
sys.path[:0]=["/src/tests","/src/netbird-router/common"]
suite=unittest.defaultTestLoader.loadTestsFromNames([
    "test_router_platforms.RuntimeTests", "test_router_platforms.NetworkTests",
    "test_router_platforms.ZabbixHostTests", "test_native_binat_snapshot.SnapshotTests"])
sys.exit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
'''
subprocess.run(['qemu-aarch64-static','-L',str(root),str(root/'usr/bin/python3.13'),'-c',code],check=True)
