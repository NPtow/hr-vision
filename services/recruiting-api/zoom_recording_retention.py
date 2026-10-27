"""Trash only verified owned Zoom files after a 30-day recovery buffer.

Never deletes whole meetings, unknown files, or local objects. Only exact saved
session UUIDs are eligible. An uncertain mutation is recorded and not replayed.
Official contracts: https://developers.zoom.us/docs/api/meetings/ (Cloud Recording)
and https://developers.zoom.us/docs/api/accounts/ (Get account settings).
"""
import datetime as dt
from contextlib import contextmanager
import json
import time

from materials_capture import owner_source_ref
from media_runtime import provider_budget


RETENTION_SECONDS = 30 * 86400


class RetentionBlocked(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def require(condition, code):
    if not condition:
        raise RetentionBlocked(code)


class ZoomRecordingRetention:
    def __init__(self, connect, repository, storage, media, *, clock=None):
        self.connect, self.repository, self.storage, self.media = connect, repository, storage, media
        self.clock = clock or time.time

    @contextmanager
    def _db(self):
        db = self.connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def init(self):
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS zoom_recording_retention_checks (
                source_id TEXT PRIMARY KEY REFERENCES media_sources(id),
                next_check REAL NOT NULL, state TEXT NOT NULL, updated_at REAL NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS zoom_recording_retention_files (
                source_id TEXT NOT NULL REFERENCES media_sources(id), file_id TEXT NOT NULL,
                state TEXT NOT NULL, updated_at REAL NOT NULL,
                PRIMARY KEY(source_id,file_id))''')

    def _room(self, source):
        require(source['provider'] == 'zoom' and source['connection_id'] == self.media.connection_id,
                'connection_mismatch')
        require(source['storage_policy'] == 'owned' and not source['metadata'].get('capture_disabled'),
                'owned_capture_required')
        kind = source['owner_kind']
        require(kind in ('interview', 'meeting'), 'owner_invalid')
        with self._db() as db:
            row = db.execute(f'SELECT status,room FROM cp_{"interviews" if kind == "interview" else "meetings"} WHERE id=?',
                             (source['owner_id'],)).fetchone()
        require(row and row[0] == 'completed', 'owner_not_completed')
        room = json.loads(row[1])
        require(room.get('sessionPinned') is True, 'exact_session_not_pinned')
        ref = owner_source_ref(kind, source['owner_id'], room, 'zoom', source['connection_id'])
        require(all(source['source_ref'].get(key) == ref[key] for key in
                    ('room_id', 'room_name', 'meeting_number', 'session_id')), 'owner_binding_changed')
        return room

    def _receipts(self, source_id):
        with self._db() as db:
            return dict(db.execute('SELECT file_id,state FROM zoom_recording_retention_files WHERE source_id=?',
                                   (source_id,)))

    def _owned(self, asset, row):
        require(asset['state'] == 'ready', 'owned_copy_not_ready')
        namespace = getattr(self.storage, 'connection_id', self.storage.backend)
        locations = [loc for loc in self.repository.list_locations(asset['id'])
                     if loc['state'] == 'verified' and loc['backend'] == self.storage.backend
                     and loc['metadata'].get('storage_connection_id') == namespace]
        require(bool(locations), 'verified_owned_copy_required')
        # A verified location on another storage account never counts as our backup.
        location = min(locations, key=lambda loc: loc['updated_at'])
        require(location['size'] > 0 and type(row.get('file_size')) is int
                and row['file_size'] == location['size'], 'owned_size_mismatch')
        require(self.clock() >= location['updated_at'] + RETENTION_SECONDS, 'retention_not_elapsed')
        stored = self.storage.head(location['object_key'])
        require(stored.size == location['size'] and stored.sha256 == location['sha256'], 'owned_copy_mismatch')
        return location

    def _plan(self, source):
        room = self._room(source)
        response = self.media.recordings(room)
        account = self.media.recording_account_id
        require(account and response.get('account_id') == account, 'account_mismatch')
        require(str(response.get('id')) == room['meetingNumber'] and
                response.get('uuid') == room['meetingSessionId'], 'session_mismatch')
        rows = response.get('recording_files')
        require(isinstance(rows, list) and len(rows) <= 100 and not response.get('next_page_token') and
                response.get('recording_count', len(rows)) == len(rows), 'incomplete_listing')
        require(all(isinstance(row, dict) and isinstance(row.get('id'), str) and row['id'] and
                    row.get('meeting_id', room['meetingSessionId']) == room['meetingSessionId'] for row in rows),
                'invalid_file_binding')
        require(len({row['id'] for row in rows}) == len(rows), 'duplicate_file_ids')
        receipts = self._receipts(source['id'])
        require(not any(state in ('attempting', 'unknown') for state in receipts.values()), 'mutation_needs_review')
        assets = {(asset['kind'], asset['provider_asset_id']): asset
                  for asset in self.repository.list_assets(source['id'])}
        videos = [row for row in rows if row.get('file_type') == 'MP4']
        known_videos = {identity for kind, identity in assets if kind == 'video'}
        require(bool(known_videos), 'no_video_copy')
        require(known_videos == {row['id'] for row in videos} |
                {identity for identity, state in receipts.items() if state == 'trashed' and identity in known_videos},
                'video_inventory_changed')
        plan = []
        for row in rows:
            kind = ('video' if row.get('file_type') == 'MP4' else 'transcript'
                    if row.get('file_type') == 'TRANSCRIPT' and row.get('recording_type') == 'audio_transcript'
                    and row.get('file_extension', 'VTT') == 'VTT' else None)
            if kind is None or receipts.get(row['id']) == 'trashed':
                continue  # Uncopied M4A/chat/unknown formats stay in Zoom.
            asset = assets.get((kind, row['id']))
            if kind == 'transcript' and asset is None:
                continue
            require(asset is not None and row.get('status') == 'completed', 'video_not_fully_copied')
            try:
                ended = dt.datetime.fromisoformat(row['recording_end'].replace('Z', '+00:00'))
                require(ended.tzinfo is not None and ended.timestamp() <= self.clock() - RETENTION_SECONDS,
                        'retention_not_elapsed')
            except (KeyError, TypeError, AttributeError):
                raise RetentionBlocked('invalid_recording_end') from None
            self._owned(asset, row)
            plan.append((asset, row))
        return room, plan

    def run_source(self, source_id, *, dry_run=False):
        with provider_budget():
            return self._run_source(source_id, dry_run=dry_run)

    def _run_source(self, source_id, *, dry_run=False):
        source = self.repository.get_source(source_id)
        room, plan = self._plan(source)
        if not plan:
            return {'state': 'nothing_to_trash', 'count': 0}
        settings = self.media.recording_retention_settings()
        require(settings.get('allow_recovery_deleted_cloud_recordings') is True, 'trash_not_enabled')
        require(settings.get('auto_delete_cmr') is False, 'provider_auto_delete_enabled')
        if dry_run:
            return {'state': 'eligible', 'count': len(plan)}
        count = 0
        for asset, row in plan:
            fresh = self.repository.get_source(source_id)
            require(fresh['generation'] == source['generation'], 'source_changed')
            require(self._room(fresh) == room, 'owner_binding_changed')
            self._owned(self.repository.get_asset(asset['id']), row)
            with self._db() as db:
                claimed = db.execute('''INSERT OR IGNORE INTO zoom_recording_retention_files
                    VALUES (?,?,'attempting',?)''', (source_id, row['id'], self.clock())).rowcount
            if not claimed:
                continue
            try:
                # No retry: a timeout could mean Zoom has already moved the file.
                self.media.trash_recording_file(room, row['id'])
            except Exception:
                with self._db() as db:
                    db.execute('''UPDATE zoom_recording_retention_files SET state='unknown',updated_at=?
                        WHERE source_id=? AND file_id=?''', (self.clock(), source_id, row['id']))
                raise RetentionBlocked('mutation_needs_review') from None
            with self._db() as db:
                db.execute('''UPDATE zoom_recording_retention_files SET state='trashed',updated_at=?
                    WHERE source_id=? AND file_id=?''', (self.clock(), source_id, row['id']))
            count += 1
        return {'state': 'trashed', 'count': count}

    def tick(self, limit=5, *, dry_run=False):
        require(type(limit) is int and 1 <= limit <= 20, 'invalid_limit')
        self.init()
        with self._db() as db:
            ids = [row[0] for row in db.execute('''SELECT s.id FROM media_sources s
                LEFT JOIN zoom_recording_retention_checks c ON c.source_id=s.id
                WHERE s.provider='zoom' AND s.connection_id=? AND s.storage_policy='owned'
                AND s.created_at<=? AND COALESCE(c.next_check,0)<=?
                ORDER BY COALESCE(c.next_check,0),s.created_at,s.id LIMIT ?''',
                (self.media.connection_id, self.clock() - RETENTION_SECONDS, self.clock(), limit))]
        results = []
        for source_id in ids:
            try:
                result = self.run_source(source_id, dry_run=dry_run)
            except Exception as error:
                result = {'state': error.code if isinstance(error, RetentionBlocked) else 'retention_check_failed', 'count': 0}
            results.append({'source_id': source_id, **result})
            if not dry_run:
                with self._db() as db:
                    db.execute('''INSERT INTO zoom_recording_retention_checks VALUES (?,?,?,?)
                        ON CONFLICT(source_id) DO UPDATE SET next_check=excluded.next_check,
                        state=excluded.state,updated_at=excluded.updated_at''',
                        (source_id, self.clock() + 86400, result['state'], self.clock()))
        return {'checked': len(results), 'trashed': sum(r['count'] for r in results if r['state'] == 'trashed'),
                'results': results}
