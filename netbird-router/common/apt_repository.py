#!/usr/bin/env python3
"""Authenticated NetBird repository setup; public files only, never global umask."""
import argparse
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import urllib.request

KEY = '/usr/share/keyrings/netbird-archive-keyring.gpg'
SOURCE = '/etc/apt/sources.list.d/netbird.list'
URL = 'https://pkgs.netbird.io/debian/public.key'
LINE = f'deb [signed-by={KEY}] https://pkgs.netbird.io/debian stable main\n'
LIMIT = 1024 * 1024
ROOT_UID = 0
ROOT_GID = 0


class Rejected(ValueError):
    pass


def fetch():
    # Default certificate verification; never disable APT or TLS verification.
    with urllib.request.urlopen(URL, timeout=30) as response:
        if response.geturl() != URL:
            raise Rejected('Unexpected key endpoint')
        data = response.read(LIMIT + 1)
    if not data or len(data) > LIMIT:
        raise Rejected('Invalid public key size')
    return data


def public_key(data):
    with tempfile.TemporaryDirectory(prefix='openmain-public-key-') as home:
        options = ['gpg', '--batch', '--no-options', '--homedir', home]
        binary = subprocess.check_output(options + ['--dearmor'], input=data, stderr=subprocess.DEVNULL, timeout=15)
        records = subprocess.check_output(options + ['--show-keys', '--with-colons'], input=binary, stderr=subprocess.DEVNULL, timeout=15).decode()
        if not binary or not any(line.startswith('pub:') for line in records.splitlines()) or any(line.startswith(('sec:', 'ssb:')) for line in records.splitlines()):
            raise Rejected('Not a public signing certificate')
        return binary


def parents(root, name):
    target = root / name.lstrip('/')
    for directory in reversed(target.parent.relative_to(root).parents):
        p = root / directory
        if not p.is_dir() or p.is_symlink() or p.stat().st_uid != ROOT_UID or p.stat().st_mode & 0o022 or p.stat().st_mode & 0o005 != 0o005:
            raise Rejected('Unsafe repository directory')
    p = target.parent
    if p.exists() or p.is_symlink():
        if not p.is_dir() or p.is_symlink() or p.stat().st_uid != ROOT_UID or p.stat().st_mode & 0o022 or p.stat().st_mode & 0o005 != 0o005:
            raise Rejected('Unsafe repository directory')
    return target


def opened(path):
    if not path.exists() and not path.is_symlink():
        return None
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != ROOT_UID or info.st_nlink != 1 or info.st_mode & 0o022 or info.st_size > LIMIT:
            raise Rejected('Untrusted repository file')
        data = os.read(fd, LIMIT + 1)
        if len(data) != info.st_size:
            raise Rejected('Incomplete repository file')
        return fd, info, data
    except Exception:
        os.close(fd)
        raise


def reconcile(install=False, root=Path('/'), download=fetch):
    key, source = parents(root, KEY), parents(root, SOURCE)
    files = []
    try:
        for path in (key, source):
            files.append(opened(path))
        if files[1] and files[1][2] not in (LINE.encode(), LINE.rstrip('\n').encode()):
            raise Rejected('Different repository definition')
        if files[1] and not files[0]:
            raise Rejected('Existing source without its signing key')
        if not install and not any(files):
            return
        # Authenticate before chmod/chown or replacement. A rotated/different
        # existing key requires explicit review; never silently replace it.
        approved = public_key(download())
        if files[0] and files[0][2] != approved:
            raise Rejected('Existing key differs from HTTPS signing certificate')
        for path, entry in zip((key, source), files):
            if entry:
                current = path.stat(follow_symlinks=False)
                info = entry[1]
                if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                    raise Rejected('Repository file replaced during validation')
        for path, entry, data in zip((key, source), files, (approved, LINE.encode())):
            if entry:
                os.fchown(entry[0], ROOT_UID, ROOT_GID)
                os.fchmod(entry[0], 0o644)
            elif install:
                if not path.parent.exists():
                    path.parent.mkdir(mode=0o755)
                    path.parent.chmod(0o755)
                # Publish fully synced binary/text files without overwriting a target.
                fd, temporary = tempfile.mkstemp(dir=path.parent)
                try:
                    os.fchmod(fd, 0o644)
                    os.fchown(fd, ROOT_UID, ROOT_GID)
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(data); stream.flush(); os.fsync(stream.fileno())
                    if path.exists() or path.is_symlink():
                        raise Rejected('Repository target appeared during setup')
                    # No overwrite even if another installer creates the target.
                    os.link(temporary, path)
                finally:
                    os.unlink(temporary)
    finally:
        for entry in files:
            if entry:
                os.close(entry[0])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('repair', 'install'))
    args = parser.parse_args()
    reconcile(install=args.operation == 'install')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('NetBird-Paketquelle nicht eindeutig/verifiziert oder nicht sicher zugreifbar. Kein APT-Update; Quelle/Schluessel manuell pruefen.', file=sys.stderr)
        sys.exit(1)
