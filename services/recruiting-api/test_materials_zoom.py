"""Exact-instance Zoom catalog integration. Synthetic metadata/files only."""
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from domain import Problem
from materials_capture import Artifact, Source, owner_source_ref
from materials_http import DownloadResult
from materials_repository import MaterialsRepository
from materials_runtime import MaterialsRuntime, build_runtime, create_celery_app
from materials_service import MaterialsService
from materials_storage import FileSystemStorage, S3Storage
from materials_zoom import ZoomCapture


NUMBER = '12345678901'
SESSION = 'exact-session/one=='
VTT = b'WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nProvider transcript\n'
VIDEO = b'\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isommp42' + b'\x00' * 8


class MemoryS3Client:
    """Exercise private storage policy/lookup with synthetic bytes, no network."""
    def __init__(self):
        self.objects = {}

    def put_object(self, **kwargs):
        body = kwargs['Body'].read()
        self.objects[kwargs['Key']] = {'Body': body, 'ContentLength': len(body),
                                      'Metadata': kwargs['Metadata'], 'ContentType': kwargs['ContentType']}

    def head_object(self, **kwargs):
        return {key: value for key, value in self.objects[kwargs['Key']].items() if key != 'Body'}

    def get_object(self, **kwargs):
        return {'Body': io.BytesIO(self.objects[kwargs['Key']]['Body'])}

    def generate_presigned_url(self, operation, **kwargs):
        return 'https://private-storage.example/temporary-owned-recording'


def room(**changes):
    return {'provider': 'zoom', 'id': NUMBER, 'name': 'hrv-i1', 'meetingNumber': NUMBER,
            'meetingSessionId': SESSION, 'connectionId': 'zoom-account', **changes}


def recording(rid='video-one', **changes):
    return {'id': rid, 'meeting_id': SESSION, 'file_type': 'MP4', 'status': 'completed',
            'file_size': 32, 'recording_type': 'shared_screen_with_speaker_view',
            'recording_start': '2026-10-25T10:00:00Z', 'recording_end': '2026-10-25T10:00:30Z',
            'download_url': 'https://us02web.zoom.us/rec/download/private',
            'play_url': 'https://us02web.zoom.us/rec/play/private', **changes}


def transcript(**changes):
    return recording('text-one', file_type='TRANSCRIPT', file_extension='VTT',
                     recording_type='audio_transcript', file_size=len(VTT), **changes)


class ZoomCaptureTest(unittest.TestCase):
    def setUp(self):
        self.media = Mock()
        self.source = Source('zoom', 'zoom-account', NUMBER, 'hrv-i1', SESSION)
        self.adapter = ZoomCapture(self.media, 'zoom-account')
        self.media.resolve_download.return_value = {
            'url': 'https://us02web.zoom.us/rec/download/private?temporary=secret',
            'headers': {'Authorization': 'Bearer secret-token'}}

    def metadata(self, files=None, **changes):
        self.media.recordings.return_value = {'id': int(NUMBER), 'uuid': SESSION,
            'recording_files': files if files is not None else [recording(), transcript()], **changes}

    def test_metadata_only_exact_identity_and_private_fields_stripped(self):
        self.metadata()
        with patch('urllib.request.urlopen', side_effect=AssertionError('No downloads')):
            result = self.adapter.discover(asdict(self.source))
        self.assertTrue(result.complete)
        self.assertEqual([a.kind for a in result.artifacts], ['video', 'transcript'])
        self.assertEqual(result.artifacts[0].metadata['duration'], 30)
        self.media.resolve_download.assert_not_called()
        encoded = json.dumps(asdict(result))
        for value in ('download_url', 'play_url', 'secret', 'https://'):
            self.assertNotIn(value, encoded)
        called = self.media.recordings.call_args.args[0]
        self.assertEqual(called['meetingSessionId'], SESSION)
        self.assertEqual(called['connectionId'], 'zoom-account')

    def test_wrong_connection_or_missing_session_never_contacts_provider(self):
        for changes in ({'connection_id': 'other'}, {'provider': 'daily'}, {'session_id': ''},
                        {'room_id': 'not-numeric'}, {'meeting_number': '98765432101'}):
            result = self.adapter.discover({**asdict(self.source), **changes})
            self.assertFalse(result.complete)
            self.assertEqual(result.statuses['video']['reason'], 'exact_session_required')
        self.media.recordings.assert_not_called()

    def test_same_number_different_instance_does_not_attach_files(self):
        self.metadata(uuid='another-session')
        result = self.adapter.discover(asdict(self.source))
        self.assertEqual(result.artifacts, [])
        self.assertEqual(result.statuses['video']['status'], 'error')
        self.media.resolve_download.assert_not_called()

    def test_same_instance_wrong_number_does_not_attach_files(self):
        self.metadata(id='98765432101')
        self.assertEqual(self.adapter.discover(asdict(self.source)).artifacts, [])

    def test_file_from_other_session_does_not_hide_valid_transcript(self):
        self.metadata([recording(meeting_id='different-session'), transcript()])
        result = self.adapter.discover(asdict(self.source))
        self.assertEqual([a.kind for a in result.artifacts], ['transcript'])
        self.assertEqual(result.statuses['video']['status'], 'blocked')
        self.assertEqual(result.statuses['transcript']['status'], 'ready')

    def test_numeric_file_meeting_id_does_not_replace_session_uuid(self):
        self.metadata([recording(meeting_id=int(NUMBER))])
        self.assertEqual(self.adapter.discover(asdict(self.source)).artifacts, [])
        self.metadata([recording(meeting_id=int(NUMBER))], uuid='other-session')
        self.assertEqual(self.adapter.discover(asdict(self.source)).artifacts, [])

    def test_processing_failed_missing_unknown_are_distinct_and_no_raw_error(self):
        for raw, expected in (('processing', 'processing'), ('failed', 'failed'),
                              ('deleted', 'missing'), ('https://secret/error', 'blocked')):
            self.metadata([recording(status=raw)])
            result = self.adapter.discover(asdict(self.source))
            self.assertEqual(result.artifacts[0].status, expected)
            self.assertNotIn('https://secret', json.dumps(asdict(result)))
            if raw == 'processing':
                self.assertFalse(result.complete)
        self.metadata([recording(file_size=0)])
        self.assertEqual(self.adapter.discover(asdict(self.source)).artifacts[0].status, 'missing')

    def test_absence_does_not_invent_transcript_or_claim_not_recorded(self):
        self.metadata([])
        result = self.adapter.discover(asdict(self.source))
        self.assertEqual(result.artifacts, [])
        self.assertEqual(result.statuses['video']['status'], 'absent')
        self.assertEqual(result.statuses['transcript']['status'], 'absent')

    def test_only_mp4_and_real_provider_vtt_are_imported(self):
        self.metadata([recording('audio', file_type='M4A'),
                       recording('chat', file_type='CHAT'),
                       recording('summary', file_type='TRANSCRIPT', recording_type='summary'),
                       recording('cc', file_type='CC'), transcript()])
        self.assertEqual([a.provider_asset_id for a in self.adapter.discover(asdict(self.source)).artifacts], ['text-one'])

    def test_limits_incomplete_metadata_not_false_complete(self):
        self.adapter.max_files = 1
        self.metadata()
        self.assertFalse(self.adapter.discover(asdict(self.source)).complete)
        self.metadata([recording()], recording_count=2)
        result = self.adapter.discover(asdict(self.source))
        self.assertFalse(result.complete)
        self.assertEqual(result.statuses['video']['reason'], 'metadata_listing_incomplete')

    def test_conflicting_duplicate_file_is_not_published(self):
        self.metadata([recording(), recording(file_size=100), transcript()])
        result = self.adapter.discover(asdict(self.source))
        self.assertEqual([a.kind for a in result.artifacts], ['transcript'])
        self.assertEqual(result.statuses['video']['status'], 'blocked')

    def test_provider_error_does_not_persist_response_or_credentials(self):
        self.media.recordings.side_effect = Problem('https://secret/?access_token=secret', 503)
        result = self.adapter.discover(asdict(self.source))
        self.assertEqual(result.statuses['video']['status'], 'error')
        self.assertNotIn('secret', json.dumps(asdict(result)))

    def test_resolve_reinspects_exact_session_and_never_returns_credentials_in_repr(self):
        self.metadata()
        asset = Artifact(self.source, 'video-one', 'video', 'ready')
        resolved = self.adapter.resolve(asset)
        self.assertEqual(resolved.headers, {'Authorization': 'Bearer secret-token'})
        self.assertNotIn('secret', repr(resolved))
        self.metadata(uuid='different')
        with self.assertRaises(ValueError):
            self.adapter.resolve(asset)
        self.assertEqual(self.media.resolve_download.call_count, 1)

    def test_resolve_rejects_changed_kind_and_unready_file(self):
        asset = Artifact(self.source, 'video-one', 'video', 'ready')
        for row in (recording(status='processing'), transcript() | {'id': 'video-one'}):
            self.metadata([row])
            with self.assertRaises(ValueError):
                self.adapter.resolve(asset)
        self.media.resolve_download.assert_not_called()

    def test_resolve_rejects_unsafe_url_or_header(self):
        self.metadata()
        asset = Artifact(self.source, 'video-one', 'video', 'ready')
        for changes in ({'url': 'https://attacker.example/file'},
                        {'url': 'http://zoom.us/file'},
                        {'url': 'https://user:secret@zoom.us/file'},
                        {'headers': {}}, {'headers': {'Authorization': 'Bearer token\nUnsafe: 1'}}):
            self.media.resolve_download.return_value = {
                'url': 'https://zoom.us/rec/download/file', 'headers': {'Authorization': 'Bearer secret'}, **changes}
            with self.assertRaises(ValueError):
                self.adapter.resolve(asset)

    def test_webhooks_fail_closed_until_subscription_is_implemented(self):
        with self.assertRaises(Problem) as error:
            self.adapter.verify_webhook(b'{}', {})
        self.assertEqual(error.exception.status, 503)


class ZoomRuntimeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.connect = lambda: sqlite3.connect(self.root / 'test.sqlite3')
        with self.connect() as db:
            db.execute('CREATE TABLE cp_interviews (id TEXT PRIMARY KEY,status TEXT,room TEXT,candidate_id TEXT)')
            db.execute('CREATE TABLE cp_meetings (id TEXT PRIMARY KEY,status TEXT,room TEXT)')
            db.execute("INSERT INTO cp_interviews VALUES ('i1','completed',NULL,'candidate')")
        self.repository = MaterialsRepository(self.connect)
        self.repository.init()
        self.media = Mock()
        self.media.recordings.return_value = {'id': NUMBER, 'uuid': SESSION, 'recording_files': [recording()]}
        self.media.resolve_download.side_effect = lambda file: {
            'url': 'https://zoom.us/rec/download/' + file['id'],
            'headers': {'Authorization': 'Bearer private-token'}}
        self.adapter = ZoomCapture(self.media, 'zoom-account')
        self.storage = FileSystemStorage(self.root / 'store')
        self.downloads = []

        def download(source, destination, **kwargs):
            self.downloads.append(source)
            body = VTT if source.url.endswith('text-one') else VIDEO
            self.assertEqual(kwargs['expected_size'], len(body))
            Path(destination).write_bytes(body)
            return DownloadResult(str(destination), len(body), hashlib.sha256(body).hexdigest(),
                                  'text/vtt' if body == VTT else 'video/mp4')

        from test_materials_worker import _parser
        self.runtime = MaterialsRuntime(self.connect, self.repository,
            {('zoom', 'zoom-account'): self.adapter}, self.storage, downloader=download,
            normalizer=_parser.normalize_transcript)

    def set_room(self, data):
        with self.connect() as db:
            db.execute('UPDATE cp_interviews SET room=? WHERE id=?', (json.dumps(data), 'i1'))

    def drain(self):
        for _ in range(10):
            if not self.runtime.worker.run_once()['claimed']:
                return
        self.fail('Worker did not settle')

    def test_owner_ref_is_allowlisted_and_instance_bound(self):
        first = owner_source_ref('interview', 'i1', room(token='secret', download_url='https://secret'),
                                 'zoom', 'zoom-account')
        other = owner_source_ref('interview', 'i1', room(meetingSessionId='another'), 'zoom', 'zoom-account')
        self.assertEqual(first['meeting_number'], NUMBER)
        self.assertEqual(first['session_id'], SESSION)
        self.assertNotEqual(first['capture_instance_id'], other['capture_instance_id'])
        self.assertNotIn('secret', json.dumps(first))
        for changes in ({'meetingSessionId': None}, {'meetingNumber': None}, {'connectionId': None},
                        {'id': '98765432101'}):
            with self.assertRaises(ValueError):
                owner_source_ref('interview', 'i1', room(**changes), 'zoom', 'zoom-account')

    def test_legacy_daily_owner_ref_unchanged(self):
        ref = owner_source_ref('interview', 'i1', {'id': 'daily-id', 'name': 'hrv-i1'})
        self.assertEqual(ref['exclusive_room_owner'], {'kind': 'interview', 'id': 'i1'})
        self.assertNotIn('meeting_number', ref)

    def test_backfill_does_not_guess_session_or_connection(self):
        for changes in ({'meetingSessionId': None}, {'connectionId': None}, {'meetingNumber': None}):
            self.set_room(room(**changes))
            self.assertEqual(self.runtime.backfill()['ambiguous'], 1)
            self.assertEqual(self.repository.list_sources('interview', 'i1'), [])
        self.media.recordings.assert_not_called()

    def test_backfill_zoom_not_skipped_by_daily_recording_control(self):
        self.set_room(room())
        with self.connect() as db:
            db.execute('CREATE TABLE cp_intake (candidate_id TEXT PRIMARY KEY)')
            db.execute('CREATE TABLE cp_recording_control (interview_id TEXT PRIMARY KEY,start_requested_at TEXT)')
            db.execute("INSERT INTO cp_intake VALUES ('candidate')")
            db.execute("INSERT INTO cp_recording_control VALUES ('i1',NULL)")
        self.assertEqual(self.runtime.backfill()['registered'], 1)
        source = self.repository.list_sources('interview', 'i1')[0]
        self.assertEqual(source['provider'], 'zoom')
        self.assertEqual(source['connection_id'], 'zoom-account')
        self.assertEqual(source['source_ref']['session_id'], SESSION)
        self.assertEqual(source['storage_policy'], 'owned')
        self.media.recordings.assert_not_called()

    def test_zoom_default_copies_video_and_late_vtt_to_private_storage(self):
        self.storage = S3Storage('private-materials', client=MemoryS3Client())
        self.runtime.storage = self.runtime.worker.storage = self.storage
        self.set_room(room())
        self.runtime.backfill()
        self.drain()
        source = self.repository.list_sources('interview', 'i1')[0]
        self.assertEqual(source['storage_policy'], 'owned')
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(self.repository.list_transcripts(source['id']), [])
        self.media.recordings.return_value['recording_files'].append(transcript())
        self.runtime.enqueue_discovery(source['id'], token='late-transcript')
        self.drain()
        self.assertEqual(len(self.downloads), 2)
        versions = self.repository.list_transcripts(source['id'])
        self.assertEqual(versions[0]['segments'][0]['text'], 'Provider transcript')
        self.assertEqual(versions[0]['metadata']['origin'], 'provider_import')
        catalog = MaterialsService(self.runtime).list_materials('interview', 'i1')
        serialized = json.dumps(catalog)
        for secret in ('private-token', 'download_url', 'play_url', 'https://zoom.us', 'https://us02web'):
            self.assertNotIn(secret, serialized)
        video = next(asset for asset in catalog['assets'] if asset['kind'] == 'video')
        self.media.resolve_download.reset_mock()
        access = MaterialsService(self.runtime).get_asset_access('interview', 'i1', video['id'])
        self.assertEqual(access['storage'], 'owned')
        self.assertEqual(access['url'], 'https://private-storage.example/temporary-owned-recording')
        self.media.resolve_download.assert_not_called()

    def test_zoom_service_rejects_remote_before_any_catalog_mutation(self):
        service = MaterialsService(self.runtime)
        with self.assertRaises(Problem) as error:
            service.register_owner('interview', {'id': 'i1', 'room': room()}, 'remote')
        self.assertEqual(error.exception.status, 400)
        self.assertEqual(self.repository.list_sources('interview', 'i1'), [])
        self.assertEqual(self.repository.list_pending_jobs(), [])
        self.media.recordings.assert_not_called()

    def test_zoom_runtime_rejects_explicit_remote(self):
        ref = owner_source_ref('interview', 'i1', room(), 'zoom', 'zoom-account')
        with self.assertRaises(ValueError):
            self.runtime.register_owner('interview', 'i1', 'zoom', 'zoom-account', ref, 'remote')
        self.assertEqual(self.repository.list_sources('interview', 'i1'), [])

    def test_default_and_explicit_daily_policy_are_preserved(self):
        service = MaterialsService(self.runtime)
        meeting = {'id': 'i1', 'room': {'id': 'daily-id', 'name': 'hrv-i1'}}
        source = service.register_owner('interview', meeting)
        self.assertEqual(source['storage_policy'], 'remote')
        service.register_owner('interview', meeting, 'owned')
        self.assertEqual(self.repository.get_source(source['id'])['storage_policy'], 'owned')
        result = service.register_owner('interview', meeting, 'remote')
        self.assertEqual(result['id'], source['id'])
        self.assertEqual(result['storage_policy'], 'remote')

    def legacy_source(self):
        ref = owner_source_ref('interview', 'i1', room(), 'zoom', 'zoom-account')
        return self.repository.ensure_source('interview', 'i1', 'zoom', 'zoom-account', ref,
                                             storage_policy='remote')

    def test_register_upgrades_legacy_zoom_without_replacing_identity(self):
        old = self.legacy_source()
        job = self.runtime.enqueue_discovery(old['id'])
        claimed = self.repository.claim_job('legacy-worker', 60, job_id=job['id'])
        service = MaterialsService(self.runtime)
        upgraded = service.register_owner('interview', {'id': 'i1', 'room': room()})
        self.assertEqual(upgraded['id'], old['id'])
        self.assertEqual(upgraded['source_ref'], old['source_ref'])
        self.assertEqual(upgraded['storage_policy'], 'owned')
        self.assertEqual(upgraded['generation'], old['generation'] + 1)
        self.assertEqual(self.repository.get_job(job['id'])['status'], 'cancelled')
        with self.assertRaises(Problem):
            self.repository.renew_job(job['id'], claimed['lease_token'])
        replay = service.register_owner('interview', {'id': 'i1', 'room': room()})
        self.assertEqual(replay['generation'], upgraded['generation'])
        self.assertEqual(len(self.repository.list_sources('interview', 'i1')), 1)
        with self.assertRaises(ValueError):
            self.runtime.upgrade_policy(old['id'], 'remote')

    def test_reconcile_upgrades_expired_legacy_zoom_once(self):
        old = self.legacy_source()
        self.repository.set_source_state(old['id'], 'ready')
        self.runtime.clock = lambda: old['created_at'] + self.runtime.settle_seconds + 100
        self.assertEqual(self.runtime.reconcile()['rediscovered'], 1)
        upgraded = self.repository.get_source(old['id'])
        self.assertEqual(upgraded['storage_policy'], 'owned')
        self.assertEqual(upgraded['generation'], old['generation'] + 1)
        jobs = self.repository.list_jobs(old['id'])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['status'], 'pending')
        self.assertEqual(jobs[0]['generation'], upgraded['generation'])
        self.assertEqual(self.runtime.reconcile()['rediscovered'], 0)
        self.assertEqual(len(self.repository.list_jobs(old['id'])), 1)
        self.media.recordings.assert_not_called()

    def test_reconcile_recovers_crash_after_owned_policy_commit(self):
        old = self.legacy_source()
        self.repository.set_source_state(old['id'], 'ready')
        self.runtime.clock = lambda: old['created_at'] + self.runtime.settle_seconds + 100
        with patch.object(self.runtime, 'enqueue_discovery', side_effect=RuntimeError('simulated process exit')):
            with self.assertRaises(RuntimeError):
                self.runtime.reconcile()
        committed = self.repository.get_source(old['id'])
        self.assertEqual(committed['storage_policy'], 'owned')
        self.assertEqual(self.repository.list_jobs(old['id']), [])
        self.assertEqual(self.runtime.reconcile()['rediscovered'], 1)
        current = self.repository.get_source(old['id'])
        self.assertEqual(current['generation'], committed['generation'])
        jobs = self.repository.list_jobs(old['id'])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['generation'], current['generation'])
        self.assertEqual(self.runtime.reconcile()['rediscovered'], 0)

    def test_disabled_legacy_zoom_is_not_revived_or_modified(self):
        old = self.legacy_source()
        suspended = self.repository.suspend_source(old['id'])
        self.runtime.reconcile()
        result = MaterialsService(self.runtime).register_owner('interview', {'id': 'i1', 'room': room()})
        self.assertEqual(result['generation'], suspended['generation'])
        self.assertEqual(result['storage_policy'], 'remote')
        self.assertTrue(result['metadata']['capture_disabled'])
        self.assertEqual(self.repository.list_pending_jobs(), [])

    def test_runtime_keeps_daily_and_registers_zoom_when_capture_keys_configured(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('No provider I/O during composition')):
            runtime = build_runtime(connect=self.connect, media=Mock(), storage=self.storage,
                environ={'HR_MATERIALS_ENABLED': '1', 'HR_MATERIALS_CONNECTION_ID': 'daily-account',
                         'HR_ZOOM_CONNECTION_ID': 'zoom-account', 'HR_ZOOM_ENABLED': '0',
                         'ZOOM_S2S_ACCOUNT_ID': 'account', 'ZOOM_S2S_CLIENT_ID': 'client',
                         'ZOOM_S2S_CLIENT_SECRET': 'server-secret'}, publish=lambda _: None)
        self.assertEqual(set(runtime.captures), {('daily', 'daily-account'), ('zoom', 'zoom-account')})

    def test_runtime_without_zoom_keys_is_still_daily_only(self):
        with patch('zoom_media.ZoomMedia.from_env', return_value=Mock(capture_configured=False)):
            runtime = build_runtime(connect=self.connect, media=Mock(), storage=self.storage,
                environ={'HR_MATERIALS_ENABLED': '1'}, publish=lambda _: None)
        self.assertEqual(set(runtime.captures), {('daily', 'daily')})


class ZoomRetentionScheduleTest(unittest.TestCase):
    def env(self, **changes):
        return {'CELERY_BROKER_URL': 'memory://', 'HR_MATERIALS_TEST_MODE': '1',
                'HR_MATERIALS_ENABLED': '1', **changes}

    def test_retention_is_separate_hourly_opt_in(self):
        app = create_celery_app(self.env(HR_ZOOM_RECORDING_RETENTION_ENABLED='1'))
        self.assertEqual(app.conf.beat_schedule['zoom-recording-retention'],
            {'task': 'materials.zoom_recording_retention', 'schedule': 3600})
        self.assertEqual(app.conf.beat_schedule['materials-reconcile']['schedule'], 10)
        with patch('materials_runtime.build_runtime') as build:
            for env in (self.env(), self.env(HR_MATERIALS_ENABLED='0', HR_ZOOM_RECORDING_RETENTION_ENABLED='1')):
                disabled = create_celery_app(env)
                self.assertNotIn('zoom-recording-retention', disabled.conf.beat_schedule)
                self.assertEqual(disabled.tasks['materials.zoom_recording_retention'].run(), {'enabled': False})
            build.assert_not_called()

    def test_retention_task_uses_only_exact_zoom_capture_binding(self):
        app = create_celery_app(self.env(HR_ZOOM_RECORDING_RETENTION_ENABLED='1'))
        media = Mock(connection_id='zoom-account')
        runtime = Mock(enabled=True, captures={
            ('daily', 'daily'): Mock(), ('zoom', 'wrong-account'): Mock(media=media),
            ('zoom', 'zoom-account'): Mock(media=media)})
        with patch('materials_runtime.build_runtime', return_value=runtime), \
                patch('zoom_recording_retention.ZoomRecordingRetention') as retention:
            retention.return_value.tick.return_value = {'checked': 1, 'trashed': 0, 'results': []}
            result = app.tasks['materials.zoom_recording_retention'].run()
        retention.assert_called_once_with(runtime.connect, runtime.repository, runtime.storage,
                                           media, clock=runtime.clock)
        retention.return_value.tick.assert_called_once_with(limit=5)
        self.assertEqual(result, {'enabled': True, 'connections': [
            {'connection_id': 'zoom-account', 'checked': 1, 'trashed': 0, 'results': []}]})


if __name__ == '__main__':
    unittest.main()
