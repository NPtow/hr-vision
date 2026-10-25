"""Coexistence: legacy Zoom execution never touches Daily work owned by source."""
import builtins
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from domain import Problem
from materials_capture import Discovery, owner_source_ref
from materials_repository import MaterialsRepository
from materials_runtime import MaterialsRuntime, build_runtime
from materials_service import MaterialsService
from materials_storage import FileSystemStorage
from materials_worker import WorkError


class EmptyCapture:
    def __init__(self):
        self.calls = []

    def discover(self, ref):
        self.calls.append(ref)
        return Discovery([], {kind: {'status': 'processing', 'complete': False}
                              for kind in ('video', 'transcript')}, False)


class ProviderScopeTests(unittest.TestCase):
    def test_zoom_recovery_does_not_backfill_daily_history(self):
        with self.connect() as db:
            db.execute("INSERT INTO cp_interviews VALUES ('aaa-daily','completed',?)",
                       (json.dumps({'id': 'older-daily', 'name': 'hrv-aaa-daily'}),))
            db.execute("INSERT INTO cp_interviews VALUES ('new-zoom','completed',?)",
                       (json.dumps({**self.zoom_room, 'meetingSessionId': 'new-session'}),))
        self.runtime.provider_allowlist = frozenset(('daily', 'zoom'))
        self.runtime.backfill_providers = ('zoom',)
        self.assertEqual(self.runtime.backfill(limit=1)['registered'], 1)
        self.assertEqual(self.repo.list_sources('interview', 'aaa-daily'), [])
        self.assertEqual(self.repo.list_sources('interview', 'new-zoom')[0]['provider'], 'zoom')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='materials-provider-scope-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clock = [1000.]
        self.connect = lambda: sqlite3.connect(self.root / 'state.sqlite3', timeout=1)
        self.daily_room = {'id': 'daily-room', 'name': 'hrv-daily-owner'}
        self.zoom_room = {'provider': 'zoom', 'id': '12345678901', 'name': 'hrv-zoom-owner',
            'meetingNumber': '12345678901', 'meetingSessionId': 'zoom-session', 'connectionId': 'zoom-account'}
        with self.connect() as db:
            db.execute('CREATE TABLE cp_interviews(id TEXT PRIMARY KEY,status TEXT,room TEXT)')
            db.execute('CREATE TABLE cp_meetings(id TEXT PRIMARY KEY,status TEXT,room TEXT)')
            db.executemany('INSERT INTO cp_interviews VALUES (?,\'completed\',?)',
                [('daily-owner', json.dumps(self.daily_room)), ('zoom-owner', json.dumps(self.zoom_room))])
        self.repo = MaterialsRepository(self.connect, clock=lambda: self.clock[0])
        self.repo.init()
        self.daily = self.repo.ensure_source('interview', 'daily-owner', 'daily', 'daily-account',
            owner_source_ref('interview', 'daily-owner', self.daily_room, 'daily', 'daily-account'))
        self.zoom = self.repo.ensure_source('interview', 'zoom-owner', 'zoom', 'zoom-account',
            owner_source_ref('interview', 'zoom-owner', self.zoom_room, 'zoom', 'zoom-account'))
        self.capture = EmptyCapture()
        self.daily_capture = Mock()
        self.published = []
        self.storage = FileSystemStorage(self.root / 'objects')
        self.runtime = MaterialsRuntime(self.connect, self.repo,
            {('daily', 'daily-account'): self.daily_capture, ('zoom', 'zoom-account'): self.capture},
            self.storage, provider_allowlist=('zoom',), clock=lambda: self.clock[0],
            publish=self.published.append, backfill_enabled=True)

    def daily_snapshot(self):
        sources = "SELECT id FROM media_sources WHERE provider='daily'"
        assets = 'SELECT id FROM media_assets WHERE source_id IN (' + sources + ')'
        jobs = 'SELECT id FROM media_jobs WHERE source_id IN (' + sources + ')'
        queries = {
            'sources': "SELECT * FROM media_sources WHERE provider='daily'",
            'assets': 'SELECT * FROM media_assets WHERE source_id IN (' + sources + ')',
            'locations': 'SELECT * FROM asset_locations WHERE asset_id IN (' + assets + ')',
            'transcripts': 'SELECT * FROM transcript_versions WHERE asset_id IN (' + assets + ')',
            'jobs': 'SELECT * FROM media_jobs WHERE source_id IN (' + sources + ')',
            'attempts': 'SELECT * FROM media_attempts WHERE job_id IN (' + jobs + ')',
            'events': "SELECT * FROM media_events WHERE provider='daily'"}
        with self.connect() as db:
            return {kind: sorted(db.execute(query).fetchall()) for kind, query in queries.items()}

    def seed_daily_jobs(self):
        pending = self.repo.enqueue_job(self.daily['id'], 'discover', 'pending')
        expired = self.repo.enqueue_job(self.daily['id'], 'discover', 'expired')
        held = self.repo.claim_job('old-daily-worker', 3, job_id=expired['id'])
        self.repo.record_event('daily', 'daily-account', 'daily-event',
            {'payload': {'room_id': 'daily-room'}}, source_id=self.daily['id'])
        self.clock[0] += 10
        return pending, held

    def test_zoom_run_leaves_expired_daily_lease_and_catalogue_unchanged(self):
        _, held = self.seed_daily_jobs()
        zoom_job = self.runtime.worker.enqueue_discovery(self.zoom['id'])
        before = self.daily_snapshot()
        self.assertEqual(self.runtime.worker.run_once()['job_id'], zoom_job['id'])
        self.assertEqual(self.runtime.worker.run_once(held['id']), {'claimed': False})
        self.assertEqual(self.daily_snapshot(), before)
        self.daily_capture.discover.assert_not_called()
        self.assertEqual(len(self.capture.calls), 1)

    def test_reconcile_does_not_expire_cancel_publish_or_rebind_daily(self):
        self.seed_daily_jobs()
        # Simulate an old migration's stale generation without calling invalidation.
        with self.connect() as db:
            db.execute('UPDATE media_sources SET generation=generation+1 WHERE id=?', (self.daily['id'],))
        before = self.daily_snapshot()
        self.runtime.reconcile()
        self.assertEqual(self.daily_snapshot(), before)
        for identity in self.published:
            source = self.repo.get_source(self.repo.get_job(identity)['source_id'])
            self.assertEqual(source['provider'], 'zoom')

    def test_manual_daily_register_policy_enqueue_and_heartbeat_rejected_before_write(self):
        _, held = self.seed_daily_jobs()
        before = self.daily_snapshot()
        with self.assertRaises(ValueError):
            self.runtime.register_owner('interview', 'daily-owner', 'daily', 'daily-account', self.daily['source_ref'])
        with self.assertRaises(ValueError):
            self.runtime.upgrade_policy(self.daily['id'], 'owned')
        with self.assertRaises(WorkError):
            self.runtime.enqueue_discovery(self.daily['id'])
        with self.assertRaises(WorkError):
            with self.runtime.worker._heartbeat(held):
                self.fail('Excluded Daily job must not receive lease renewal')
        with self.assertRaises(WorkError):
            self.runtime.worker._discover(self.daily, held)
        with self.assertRaises(WorkError):
            self.runtime.worker._import(self.daily, held)
        self.assertEqual(self.daily_snapshot(), before)

    def test_daily_webhook_and_pending_event_are_not_processed(self):
        self.seed_daily_jobs()
        before = self.daily_snapshot()
        with self.assertRaises(ValueError):
            self.runtime.handle_event('daily', {}, b'{"synthetic":true}')
        self.assertEqual(self.runtime._events(), 0)
        self.daily_capture.verify_webhook.assert_not_called()
        self.assertEqual(self.daily_snapshot(), before)

    def test_backfill_filters_before_limit_and_preserves_zoom_owned_policy(self):
        with self.connect() as db:
            db.executemany('INSERT INTO cp_interviews VALUES (?,\'completed\',?)', [
                ('aa-daily-no-source', json.dumps({'id': 'daily-two', 'name': 'hrv-aa-daily-no-source'})),
                ('zz-zoom-no-source', json.dumps({**self.zoom_room, 'name': 'hrv-zz-zoom-no-source',
                    'meetingSessionId': 'zoom-second-session'}))])
        before = self.daily_snapshot()
        result = self.runtime.backfill(limit=1)
        self.assertEqual(result['registered'], 1)
        self.assertEqual(self.repo.list_sources('interview', 'aa-daily-no-source'), [])
        zoom = self.repo.list_sources('interview', 'zz-zoom-no-source')[0]
        self.assertEqual((zoom['provider'], zoom['storage_policy']), ('zoom', 'owned'))
        self.assertEqual(self.daily_snapshot(), before)

    def test_pending_scope_applies_before_limit_and_default_keeps_daily_compatibility(self):
        daily_job = self.repo.enqueue_job(self.daily['id'], 'discover', 'early')
        self.clock[0] += 1
        zoom_job = self.repo.enqueue_job(self.zoom['id'], 'discover', 'later')
        self.assertEqual(self.repo.list_pending_jobs(limit=1)[0]['id'], daily_job['id'])
        self.assertEqual(self.repo.list_pending_jobs(limit=1, provider_allowlist=('zoom',))[0]['id'], zoom_job['id'])
        self.assertEqual(self.repo.claim_job('default-legacy-worker')['id'], daily_job['id'])

    def test_build_services_mode_never_imports_daily_even_with_legacy_key(self):
        original_import = builtins.__import__
        def reject_daily(name, *args, **kwargs):
            if name in ('daily_media', 'materials_daily'):
                raise AssertionError('Services mode may not import Daily adapters')
            return original_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=reject_daily), patch('zoom_media.ZoomMedia.from_env',
                return_value=Mock(capture_configured=True)):
            runtime = build_runtime(connect=self.connect, storage=self.storage, publish=self.published.append,
                environ={'HR_MATERIALS_ENABLED': '1', 'HR_MATERIALS_SERVICES_ENABLED': '1',
                    'HR_ZOOM_CONNECTION_ID': 'zoom-account', 'DAILY_API_KEY': 'synthetic-old-key'})
        self.assertEqual(set(runtime.captures), {('zoom', 'zoom-account')})
        self.assertEqual(runtime.worker.provider_allowlist, frozenset(('zoom',)))

    def test_build_filters_injected_captures_and_scopes_explicit_job_delivery(self):
        daily_job, _ = self.seed_daily_jobs()
        runtime = build_runtime(connect=self.connect, storage=self.storage,
            captures={('daily', 'daily-account'): self.daily_capture, ('zoom', 'zoom-account'): self.capture},
            publish=self.published.append, environ={'HR_MATERIALS_ENABLED': '1', 'HR_MATERIALS_SERVICES_ENABLED': '1'})
        before = self.daily_snapshot()
        self.assertEqual(set(runtime.captures), {('zoom', 'zoom-account')})
        self.assertEqual(runtime.worker.run_once(daily_job['id']), {'claimed': False})
        self.assertEqual(self.daily_snapshot(), before)

    def test_catalogue_zoom_policy_and_manual_wake_leave_mixed_owner_daily_unchanged(self):
        historical_daily = self.repo.ensure_source('interview', 'zoom-owner', 'daily', 'daily-account',
            {'room_id': 'historical-daily-room', 'room_name': 'hrv-zoom-owner',
             'capture_instance_id': 'historical-daily-capture'}, storage_policy='remote')
        self.repo.enqueue_job(historical_daily['id'], 'discover', 'historical-daily-pending')
        self.clock[0] += 61
        before = self.daily_snapshot()
        service = MaterialsService(self.runtime)
        service.register_owner('interview', {'id': 'zoom-owner', 'room': self.zoom_room}, 'owned')
        self.assertEqual(self.repo.get_source(self.zoom['id'])['storage_policy'], 'owned')
        self.assertEqual(self.daily_snapshot(), before)
        with self.assertRaises(Problem) as rejected:
            service.register_owner('interview', {'id': 'daily-owner', 'room': self.daily_room}, 'owned')
        self.assertEqual(rejected.exception.status, 409)
        self.assertEqual(self.daily_snapshot(), before)

    def test_scoped_zoom_preserves_owned_video_and_late_transcript_flow(self):
        import test_materials_zoom as zoom_fixture
        fixture = zoom_fixture.ZoomRuntimeTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.addCleanup(fixture.tearDown)
        fixture.runtime = MaterialsRuntime(fixture.connect, fixture.repository,
            {('zoom', 'zoom-account'): fixture.adapter}, fixture.storage,
            downloader=fixture.runtime.worker.downloader, normalizer=fixture.runtime.worker.normalizer,
            provider_allowlist=('zoom',))
        fixture.set_room(zoom_fixture.room())
        fixture.runtime.backfill()
        fixture.drain()
        source = fixture.repository.list_sources('interview', 'i1')[0]
        self.assertEqual(source['storage_policy'], 'owned')
        fixture.media.recordings.return_value['recording_files'].append(zoom_fixture.transcript())
        fixture.runtime.enqueue_discovery(source['id'], token='late-transcript-scoped')
        fixture.drain()
        self.assertEqual(len(fixture.downloads), 2)
        self.assertEqual(fixture.repository.list_transcripts(source['id'])[0]['segments'][0]['text'], 'Provider transcript')
        for asset in fixture.repository.list_assets(source['id']):
            locations = fixture.repository.list_locations(asset['id'])
            self.assertTrue(any(location['backend'] != 'provider' and location['state'] == 'verified'
                                for location in locations))


if __name__ == '__main__':
    unittest.main()
