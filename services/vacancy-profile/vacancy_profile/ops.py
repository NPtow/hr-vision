"""Explicit offline SQLite operations; refuses while any service owns the database."""
from __future__ import annotations
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3

from .store import RunStore, RunUnavailable
from jev_client import JevError


def backup(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file() or destination.exists() or destination.is_symlink():
        raise ValueError('existing source and new destination required')
    store = RunStore(source)
    with store.runtime_lock():
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(destination)) as dest:
                src.backup(dest)
                if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('backup integrity failed')
                counts = {table: dest.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                          for table in ('runs', 'profiles', 'provider_rejections')}
        except Exception:
            destination.unlink(missing_ok=True)
            raise
    return {'status': 'backed_up', 'counts': counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['backup', 'recover-rejection'])
    parser.add_argument('run_id', nargs='?')
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--expected-body-sha256')
    args = parser.parse_args()
    if args.command == 'backup' and (args.destination is None or args.run_id is not None
                                     or args.expected_body_sha256 is not None):
        parser.error('backup requires --destination only')
    if args.command == 'recover-rejection' and (args.run_id is None or not args.expected_body_sha256
                                               or args.destination is not None):
        parser.error('recover-rejection requires RUN_ID and --expected-body-sha256')
    try:
        if args.command == 'backup':
            result = backup(args.db, args.destination)
        else:
            if not args.db.is_file():
                raise ValueError('existing database required')
            profile = RunStore(args.db).recover_rejection(args.run_id, args.expected_body_sha256)
            result = {'status': 'completed', 'run_id': profile['run_id'],
                      'profile_id': profile['profile_id'], 'body_sha256': args.expected_body_sha256,
                      'provider_posts': 0}
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, RunUnavailable, JevError):
        print(json.dumps({'error': 'backup_unavailable' if args.command == 'backup' else 'rejection_recovery_unavailable'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
