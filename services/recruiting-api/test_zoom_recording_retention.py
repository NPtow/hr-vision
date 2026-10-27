"""Synthetic provider only; never deletes any real Zoom recording."""
import datetime as dt
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from materials_capture import owner_source_ref
from materials_repository import MaterialsRepository
from materials_storage import FileSystemStorage
from zoom_recording_retention import RETENTION_SECONDS, RetentionBlocked, ZoomRecordingRetention


class ZoomRetentionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.connect = lambda: sqlite3.connect(root / 'state.sqlite3')
        self.stamp = dt.datetime(2026, 10, 25, tzinfo=dt.timezone.utc).timestamp()
        self.clock = lambda: self.stamp
        self.room = {'provider': 'zoom', 'id': '12345678901', 'name': '12345678901',
                     'meetingNumber': '12345678901', 'meetingSessionId': '/exact//instance==',
                     'connectionId': 'our-zoom', 'sessionPinned': True}
        with self.connect() as db:
            db.execute('CREATE TABLE cp_interviews(id TEXT PRIMARY KEY,status TEXT,room TEXT)')
            db.execute('CREATE TABLE cp_meetings(id TEXT PRIMARY KEY,status TEXT,room TEXT)')
            db.execute('INSERT INTO cp_interviews VALUES(?,?,?)', ('interview', 'completed', json.dumps(self.room)))
        self.repository = MaterialsRepository(self.connect, clock=self.clock)
        self.repository.init()
        self.source = self.repository.ensure_source('interview', 'interview', 'zoom', 'our-zoom',
            owner_source_ref('interview', 'interview', self.room, 'zoom', 'our-zoom'), storage_policy='owned')
        self.storage = FileSystemStorage(root / 'store')
        self.media = Mock(connection_id='our-zoom', recording_account_id='our-account')
        self.media.recording_retention_settings.return_value = {
            'allow_recovery_deleted_cloud_recordings': True, 'auto_delete_cmr': False}
        self.rows = []
        self.metadata = {'id': '12345678901', 'uuid': self.room['meetingSessionId'],
                         'account_id': 'our-account', 'recording_files': self.rows}
        self.media.recordings.return_value = self.metadata
        self.retention = ZoomRecordingRetention(self.connect, self.repository, self.storage, self.media, clock=self.clock)
        self.retention.init()
        self.asset, self.location = self.copy('video-one')
        self.stamp += RETENTION_SECONDS + 1

    def copy(self, identity, *, kind='video', state='ready'):
        raw = Path(self.temp.name) / (identity + '.data')
        raw.write_bytes(b'private-media-' + identity.encode())
        stored = self.storage.put_file(raw)
        asset = self.repository.upsert_asset(self.source['id'], kind, identity, state=state)
        location = self.repository.add_location(asset['id'], stored.backend, stored.key, state='verified',
            size=stored.size, sha256=stored.sha256, metadata={'storage_connection_id': self.storage.connection_id})
        self.rows.append({'id': identity, 'file_type': 'MP4' if kind == 'video' else 'TRANSCRIPT',
                          'recording_type': 'shared_screen_with_speaker_view' if kind == 'video' else 'audio_transcript',
                          'file_extension': 'MP4' if kind == 'video' else 'VTT',
                          'meeting_id': self.room['meetingSessionId'], 'status': 'completed',
                          'recording_end': '2026-10-24T12:00:00Z', 'file_size': stored.size})
        return asset, location

    def blocked(self, reason):
        with self.assertRaises(RetentionBlocked) as caught:
            self.retention.run_source(self.source['id'])
        self.assertEqual(caught.exception.code, reason)
        self.media.trash_recording_file.assert_not_called()

    def test_trashes_only_owned_files_and_preserves_local_copy(self):
        original = self.storage.head(self.location['object_key'])
        self.rows.append({'id': 'audio', 'file_type': 'M4A', 'status': 'completed'})
        result = self.retention.run_source(self.source['id'])
        self.assertEqual(result, {'state': 'trashed', 'count': 1})
        self.media.trash_recording_file.assert_called_once_with(self.room, 'video-one')
        self.assertEqual(self.storage.head(self.location['object_key']), original)
        self.assertEqual(self.repository.get_asset(self.asset['id'])['state'], 'ready')

    def test_all_video_parts_must_be_verified_before_any_trash(self):
        self.rows.append({**self.rows[0], 'id': 'second-video'})
        self.blocked('video_inventory_changed')

    def test_pending_second_video_blocks_first(self):
        self.copy('second-video', state='pending')
        self.blocked('owned_copy_not_ready')

    def test_missing_file_blocks_and_keeps_provider_copy(self):
        (self.storage.root / self.location['object_key']).unlink()
        with self.assertRaises(ValueError):
            self.retention.run_source(self.source['id'])
        self.media.trash_recording_file.assert_not_called()

    def test_replaced_file_blocks_and_keeps_provider_copy(self):
        (self.storage.root / self.location['object_key']).write_bytes(b'damaged')
        with self.assertRaises(ValueError):
            self.retention.run_source(self.source['id'])
        self.media.trash_recording_file.assert_not_called()

    def test_cloud_size_change_is_not_our_verified_copy(self):
        self.rows[0]['file_size'] += 1
        self.blocked('owned_size_mismatch')

    def test_30_days_start_after_copy_not_meeting_date(self):
        self.stamp -= 2
        self.blocked('retention_not_elapsed')

    def test_late_recording_end_also_preserves_full_buffer(self):
        self.rows[0]['recording_end'] = dt.datetime.fromtimestamp(self.stamp, dt.timezone.utc).isoformat()
        self.blocked('retention_not_elapsed')

    def test_wrong_account_or_session_never_trashes(self):
        for key, value, reason in [('account_id', 'another', 'account_mismatch'),
                                   ('uuid', 'another', 'session_mismatch')]:
            original = self.metadata[key]
            self.metadata[key] = value
            self.blocked(reason)
            self.metadata[key] = original

    def test_unpinned_or_changed_owner_room_is_rejected(self):
        for change, reason in [({'sessionPinned': False}, 'exact_session_not_pinned'),
                               ({'meetingSessionId': 'next-reuse'}, 'owner_binding_changed')]:
            with self.connect() as db:
                db.execute('UPDATE cp_interviews SET room=?', (json.dumps({**self.room, **change}),))
            self.blocked(reason)

    def test_live_owner_never_trashes(self):
        with self.connect() as db:
            db.execute("UPDATE cp_interviews SET status='live'")
        self.blocked('owner_not_completed')

    def test_remote_policy_or_revoked_source_never_trashes(self):
        self.repository.update_source_policy(self.source['id'], 'remote')
        self.blocked('owned_capture_required')
        self.repository.update_source_policy(self.source['id'], 'owned')
        self.repository.suspend_source(self.source['id'])
        self.blocked('owned_capture_required')

    def test_incomplete_or_conflicting_listing_rejected(self):
        self.metadata['next_page_token'] = 'next'
        self.blocked('incomplete_listing')
        del self.metadata['next_page_token']
        self.rows.append(dict(self.rows[0]))
        self.blocked('duplicate_file_ids')

    def test_wrong_storage_connection_cannot_authorize_cleanup(self):
        with self.connect() as db:
            db.execute('UPDATE asset_locations SET metadata=?', ('{"storage_connection_id":"other"}',))
        self.blocked('verified_owned_copy_required')

    def test_disabled_or_unknown_trash_or_auto_delete_blocks(self):
        for settings, reason in [({}, 'trash_not_enabled'),
            ({'allow_recovery_deleted_cloud_recordings': False}, 'trash_not_enabled'),
            ({'allow_recovery_deleted_cloud_recordings': True, 'auto_delete_cmr': True}, 'provider_auto_delete_enabled'),
            ({'allow_recovery_deleted_cloud_recordings': True}, 'provider_auto_delete_enabled')]:
            self.media.recording_retention_settings.return_value = settings
            self.blocked(reason)

    def test_missing_scope_never_sends_mutation(self):
        self.media.recording_retention_settings.side_effect = RuntimeError('403 private provider body')
        result = self.retention.tick()
        self.assertEqual(result['results'][0]['state'], 'retention_check_failed')
        self.assertNotIn('private', json.dumps(result))
        self.media.trash_recording_file.assert_not_called()

    def test_ambiguous_mutation_is_not_replayed(self):
        self.media.trash_recording_file.side_effect = TimeoutError('private URL')
        for _ in range(2):
            with self.assertRaises(RetentionBlocked) as caught:
                self.retention.run_source(self.source['id'])
            self.assertEqual(caught.exception.code, 'mutation_needs_review')
        self.assertEqual(self.media.trash_recording_file.call_count, 1)

    def test_successful_replay_does_not_repeat_delete(self):
        self.retention.run_source(self.source['id'])
        self.rows.clear()  # Provider omits files moved to trash.
        self.assertEqual(self.retention.run_source(self.source['id'])['state'], 'nothing_to_trash')
        self.media.trash_recording_file.assert_called_once()

    def test_only_owned_transcript_is_also_trashed(self):
        self.stamp -= RETENTION_SECONDS + 1
        self.copy('transcript', kind='transcript')
        self.stamp += RETENTION_SECONDS + 1
        self.assertEqual(self.retention.run_source(self.source['id'])['count'], 2)
        self.assertEqual([c.args[1] for c in self.media.trash_recording_file.call_args_list], ['video-one', 'transcript'])

    def test_dry_run_is_read_only_and_tick_is_bounded_with_cooldown(self):
        self.assertEqual(self.retention.tick(dry_run=True)['results'][0]['state'], 'eligible')
        self.media.trash_recording_file.assert_not_called()
        self.assertEqual(self.retention._receipts(self.source['id']), {})
        self.assertEqual(self.retention.tick()['trashed'], 1)
        self.assertEqual(self.retention.tick()['checked'], 0)
        with self.assertRaises(RetentionBlocked):
            self.retention.tick(limit=21)


if __name__ == '__main__':
    unittest.main()
