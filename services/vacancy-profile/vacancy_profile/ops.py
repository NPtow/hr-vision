"""Offline private SQLite backup; refuses while any service owns the database."""
from __future__ import annotations
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3

from .store import RunStore, RunUnavailable


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
    parser.add_argument('command', choices=['backup'])
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(backup(args.db, args.destination), sort_keys=True))
        return 0
    except (ValueError, OSError, sqlite3.Error, RunUnavailable):
        print('{"error":"backup_unavailable"}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
