"""Atomically install one reviewed receiver, without service or database changes."""
import argparse
import ast
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import time


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_atomic(path, data, mode):
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        Path(temporary).unlink(missing_ok=True)


def checked_file(path, *, root_owned=True):
    require(path.is_file() and not path.is_symlink(), 'Regular protected file required')
    require(not path.stat().st_mode & 0o022, 'Group/other writable file refused')
    require(not root_owned or path.stat().st_uid == 0, 'Root-owned file required')
    return path.read_bytes()


def install(source, destination, backup_directory, expected_old, expected_new, reviewed_commit, *, root_owned=True):
    require(re.fullmatch('[a-f0-9]{40}', reviewed_commit), 'Reviewed full commit required')
    require(all(re.fullmatch('[a-f0-9]{64}', value) for value in (expected_old, expected_new)), 'Exact old/new SHA256 required')
    new = checked_file(source, root_owned=root_owned)
    old = checked_file(destination, root_owned=root_owned)
    require(digest(old) == expected_old, 'Installed receiver changed; installation refused')
    require(digest(new) == expected_new, 'Reviewed source hash mismatch')
    ast.parse(new, filename='reviewed_receiver.py')  # Syntax only; never execute source.
    require(not backup_directory.is_symlink(), 'Backup directory symlink refused')
    backup_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(backup_directory.stat().st_mode & 0o077 == 0 and
            (not root_owned or backup_directory.stat().st_uid == 0), 'Private root-owned backup directory required')
    name = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + secrets.token_hex(8)
    backup = backup_directory / (name + '.py')
    metadata = backup_directory / (name + '.json')
    write_atomic(backup, old, 0o600)
    receipt = {'reviewed_commit': reviewed_commit, 'previous_sha256': expected_old,
               'installed_sha256': expected_new, 'previous_mode': oct(destination.stat().st_mode & 0o777),
               'backup': str(backup), 'servicesRestarted': False}
    write_atomic(metadata, (json.dumps(receipt, sort_keys=True) + '\n').encode(), 0o600)
    # Recheck after preparing backup, before touching the installed receiver.
    require(checked_file(destination, root_owned=root_owned) == old, 'Receiver changed during backup; installation refused')
    write_atomic(destination, new, 0o644)
    require(digest(checked_file(destination, root_owned=root_owned)) == expected_new, 'Installed receiver verification failed')
    return receipt


@contextmanager
def deployment_locks():
    handles = []
    try:
        for path in (Path('/srv/hr-vision/.deploy.lock'), Path('/run/hr-vision-api-deploy.lock')):
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            handle = os.fdopen(descriptor, 'a')
            handles.append(handle)
            require(os.fstat(descriptor).st_uid == 0, 'Deployment lock must be root owned')
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        for handle in reversed(handles):
            handle.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--expected-old-sha256', required=True)
    parser.add_argument('--expected-new-sha256', required=True)
    parser.add_argument('--reviewed-commit', required=True)
    args = parser.parse_args()
    require(os.geteuid() == 0, 'Root required on target host')
    with deployment_locks():
        receipt = install(args.source, Path('/usr/local/libexec/hr-vision-ci/receiver.py'),
                          Path('/var/backups/hr-vision-ci/receiver'), args.expected_old_sha256,
                          args.expected_new_sha256, args.reviewed_commit)
    print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, SyntaxError):
        raise SystemExit('Receiver installation refused or failed; verify reviewed hashes, permissions and deployment locks') from None
