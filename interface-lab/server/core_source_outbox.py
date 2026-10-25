"""Portal-owned completion intent. HTTP happens strictly outside SQLite transactions."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time

from materials_capture import owner_source_ref


class CompletionOutbox:
    def __init__(self, connect, core, source, *, connection_id='daily', clock=None):
        self.connect, self.core, self.source = connect, core, source
        self.connection_id, self.clock = connection_id, clock or time.time
        with connect() as db:
            self.init(db)

    @staticmethod
    def init(db):
        db.execute('''CREATE TABLE IF NOT EXISTS portal_materials_outbox (
          id TEXT PRIMARY KEY, owner_kind TEXT NOT NULL, owner_id TEXT NOT NULL,
          payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
          next_attempt REAL NOT NULL, lease_token TEXT, lease_until REAL, last_error TEXT,
          created_at REAL NOT NULL, updated_at REAL NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS portal_materials_outbox_due ON portal_materials_outbox(status,next_attempt,lease_until)')

    def record(self, db, owner_kind, owner_id, room, storage_policy=None):
        """Use the caller's completion transaction, never connect/commit here."""
        if room.get('captureNeverStarted') or not room:
            return None
        # Only the Daily source exists behind this outbox. Zoom keeps its exact
        # session identity and owned-copy pipeline in the explicit legacy path.
        provider = room.get('provider', 'daily')
        if provider == 'zoom':
            return None
        if provider != 'daily':
            raise ValueError('Unsupported completion provider')
        if owner_kind not in ('interview', 'meeting') or storage_policy not in (None, 'remote', 'owned'):
            raise ValueError('Invalid material completion intent')
        if owner_kind == 'interview':
            # Recovery of old prepared introductions must retain the same explicit
            # no-capture evidence as the portal's materials projection/backfill.
            never_started = db.execute('''SELECT 1 FROM cp_interviews i
              JOIN cp_intake intake ON intake.candidate_id=i.candidate_id
              JOIN cp_recording_control control ON control.interview_id=i.id
              WHERE i.id=? AND control.start_requested_at IS NULL''', (owner_id,)).fetchone()
            if never_started:
                return None
        connection = room.get('connectionId', self.connection_id)
        ref = owner_source_ref(owner_kind, owner_id, room, provider, connection)
        payload = {'schema_version': 1, 'owner_kind': owner_kind, 'owner_id': owner_id,
            'provider': provider, 'connection_id': connection, 'source_ref': ref, 'storage_policy': storage_policy}
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        identity = hashlib.sha256(encoded.encode()).hexdigest()
        stamp = self.clock()
        db.execute('''INSERT OR IGNORE INTO portal_materials_outbox
          (id,owner_kind,owner_id,payload,next_attempt,created_at,updated_at) VALUES (?,?,?,?,?,?,?)''',
          (identity, owner_kind, owner_id, encoded, stamp, stamp, stamp))
        return identity

    def pending(self, owner_kind, owner_id):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM portal_materials_outbox WHERE owner_kind=? AND owner_id=? AND status<>'delivered'",
                              (owner_kind, owner_id)).fetchone() is not None

    def delivery_failed(self, owner_kind, owner_id):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM portal_materials_outbox WHERE owner_kind=? AND owner_id=? AND status<>'delivered' AND last_error IS NOT NULL",
                              (owner_kind, owner_id)).fetchone() is not None

    def dispatch_once(self):
        stamp, lease = self.clock(), secrets.token_hex(24)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('''SELECT id,payload FROM portal_materials_outbox
              WHERE (status='pending' AND next_attempt<=?) OR (status='running' AND lease_until<=?)
              ORDER BY created_at,id LIMIT 1''', (stamp, stamp)).fetchone()
            if row is None:
                return {'claimed': False}
            identity, payload = row[0], json.loads(row[1])
            db.execute("UPDATE portal_materials_outbox SET status='running',attempts=attempts+1,lease_token=?,lease_until=?,updated_at=? WHERE id=?",
                       (lease, stamp + 120, stamp, identity))
        try:
            if payload.get('provider') != 'daily':
                raise ValueError('Unsupported completion provider')
            if payload['storage_policy'] is None:
                capabilities = self.source.capabilities()
                policy = capabilities.get('default_storage_policy')
                if policy not in ('remote', 'owned') or (capabilities.get('connection_id') and capabilities['connection_id'] != payload['connection_id']):
                    raise ValueError('Invalid source capabilities')
                payload['storage_policy'] = policy
                # Pin the source default before a possibly successful/lost bind reply.
                with self.connect() as db:
                    changed = db.execute('UPDATE portal_materials_outbox SET payload=? WHERE id=? AND lease_token=? AND status=\'running\'',
                        (json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False), identity, lease)).rowcount
                    if not changed:
                        return {'claimed': True, 'state': 'stale'}
            binding = self.core.bind(payload)
            self.source.enqueue(binding['binding_id'], binding['generation'])
            with self.connect() as db:
                changed = db.execute("UPDATE portal_materials_outbox SET status='delivered',lease_token=NULL,lease_until=NULL,last_error=NULL,updated_at=? WHERE id=? AND lease_token=? AND status='running'",
                                     (self.clock(), identity, lease)).rowcount
            return {'claimed': True, 'state': 'delivered' if changed else 'stale'}
        except Exception:
            # Bounded safe error code only: never persist remote exception/body/URL.
            with self.connect() as db:
                db.execute("UPDATE portal_materials_outbox SET status='pending',next_attempt=?,lease_token=NULL,lease_until=NULL,last_error='service_delivery_failed',updated_at=? WHERE id=? AND lease_token=? AND status='running'",
                           (self.clock() + 30, self.clock(), identity, lease))
            return {'claimed': True, 'state': 'retry'}


def main():
    parser = argparse.ArgumentParser(description='Dispatch durable portal materials completion intents')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if os.environ.get('HR_MATERIALS_SERVICES_ENABLED') != '1':
        raise SystemExit('Materials services mode is disabled')
    from core_source_client import clients_from_env
    core, source = clients_from_env()
    path = Path(os.environ.get('HR_DATA_DIR', '/var/lib/hr-vision-api')) / 'journey.sqlite3'
    def connect():
        from candidate_portal import ClosingConnection
        db = sqlite3.connect(path, timeout=10, factory=ClosingConnection)
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=10000')
        return db
    outbox = CompletionOutbox(connect, core, source,
        connection_id=os.environ.get('HR_MATERIALS_CONNECTION_ID', 'daily'))
    while True:
        result = outbox.dispatch_once()
        if args.once:
            return
        if not result['claimed'] or result.get('state') == 'retry':
            time.sleep(1)


if __name__ == '__main__':
    main()
