"""Crash, ownership and independent-channel checks with fake provider bytes only."""
from dataclasses import replace
import hashlib
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from materials_capture import Artifact, Discovery, HttpSource, Source
from materials_http import DownloadError, DownloadResult
from materials_repository import MaterialsRepository
from materials_runtime import MaterialsRuntime
from materials_runtime import create_celery_app
from materials_storage import FileSystemStorage
from materials_worker import MaterialsWorker


VTT = b'WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello research\n'
VIDEO = b'\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isommp42'


class FakeCapture:
    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.resolved = []

    def discover(self, ref):
        artifacts = [a for a in self.artifacts if not ref.get('session_id') or a.source.session_id == ref['session_id']]
        return Discovery(artifacts, {k: {'status': 'ready', 'complete': True} for k in ('video', 'transcript')}, True)

    def inspect(self, artifact):
        return artifact

    def resolve(self, artifact):
        self.resolved.append(artifact.kind)
        return HttpSource('https://synthetic.invalid/' + artifact.kind)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / 'test.sqlite3'
        self.clock = [1000.0]
        self.connect = lambda: sqlite3.connect(self.path, timeout=10)
        with self.connect() as db:
            db.execute('CREATE TABLE cp_interviews (id TEXT PRIMARY KEY,status TEXT,room TEXT)')
            db.execute('CREATE TABLE cp_meetings (id TEXT PRIMARY KEY,status TEXT,room TEXT)')
            db.execute("INSERT INTO cp_interviews VALUES ('i1','completed',NULL)")
        self.repo = MaterialsRepository(self.connect, clock=lambda: self.clock[0])
        self.repo.init()
        self.source = Source('fake', 'account', 'room', 'hrv-i1', 'session')
        self.capture = FakeCapture([
            Artifact(self.source, 'recording', 'video', 'ready'),
            Artifact(self.source, 'transcript', 'transcript', 'ready', {'format': 'vtt'})])
        self.storage = FileSystemStorage(self.root / 'store')
        self.downloads = []
        self.fail_video = False

        def download(source, destination, **kwargs):
            self.downloads.append(source.url)
            if self.fail_video and source.url.endswith('video'):
                raise DownloadError('synthetic_failure', retryable=True)
            body = VTT if source.url.endswith('transcript') else VIDEO
            Path(destination).write_bytes(body)
            return DownloadResult(str(destination), len(body), hashlib.sha256(body).hexdigest(),
                                  'text/vtt' if source.url.endswith('transcript') else 'video/mp4')

        self.downloader = download
        self.worker = self.make_worker()

    def tearDown(self):
        self.tmp.cleanup()

    def make_worker(self):
        return MaterialsWorker(self.repo, {'fake': self.capture}, self.storage,
                               downloader=self.downloader, temp_root=self.root)

    def register(self, policy='remote', session='session'):
        return self.repo.ensure_source('interview', 'i1', 'fake', 'account',
            {'room_id': 'room', 'room_name': 'hrv-i1', 'session_id': session}, storage_policy=policy)

    def drain(self, maximum=15):
        results = []
        for _ in range(maximum):
            result = self.worker.run_once()
            if not result['claimed']:
                break
            results.append(result)
        return results

    def test_remote_video_never_downloaded_transcript_always_owned(self):
        source = self.register()
        job = self.worker.enqueue_discovery(source['id'])
        self.drain()
        self.assertEqual(self.capture.resolved, ['transcript'])
        transcripts = self.repo.list_transcripts(source['id'])
        self.assertEqual(transcripts[0]['segments'][0]['text'], 'Hello research')
        self.assertEqual(transcripts[0]['metadata']['origin'], 'provider_import')
        self.assertEqual(self.worker.run_once(job['id']), {'claimed': False})
        video = next(a for a in self.repo.list_assets(source['id']) if a['kind'] == 'video')
        self.assertEqual(self.repo.list_locations(video['id'])[0]['backend'], 'provider')

    def test_video_failure_does_not_hide_owned_transcript(self):
        source = self.register('owned')
        self.fail_video = True
        self.worker.enqueue_discovery(source['id'])
        results = self.drain()
        self.assertTrue(any(r.get('code') == 'download_failed' for r in results), results)
        self.assertEqual(len(self.repo.list_transcripts(source['id'])), 1)

    def test_restart_and_duplicate_delivery_publish_once(self):
        source = self.register('owned')
        discovery = self.worker.enqueue_discovery(source['id'])
        lost = self.repo.claim_job('dead', 3, job_id=discovery['id'])
        self.clock[0] += 4
        self.worker = self.make_worker()
        self.worker.reconcile()
        self.drain()
        self.assertEqual(len(self.repo.list_transcripts(source['id'])), 1)
        self.worker.enqueue_discovery(source['id'], token='repeat')
        self.drain()
        self.assertEqual(len(self.repo.list_transcripts(source['id'])), 1)
        self.assertEqual(len(self.downloads), 2)
        with self.assertRaises(Exception):
            self.repo.finish_job(lost['id'], lost['lease_token'])

    def test_upgrade_same_source_preserves_transcript_and_copies_video(self):
        source = self.register()
        self.worker.enqueue_discovery(source['id'])
        self.drain()
        self.repo.update_source_policy(source['id'], 'owned')
        self.worker.enqueue_discovery(source['id'])
        self.drain()
        self.assertEqual(len(self.repo.list_sources('interview', 'i1')), 1)
        self.assertEqual(len(self.repo.list_transcripts(source['id'])), 1)
        self.assertEqual(self.capture.resolved, ['transcript', 'video'])

    def test_provider_wait_does_not_invent_asr_output(self):
        source = self.register()
        self.capture.artifacts = [self.capture.artifacts[0]]
        self.worker.enqueue_discovery(source['id'])
        self.drain()
        state = self.repo.get_source(source['id'])
        self.assertEqual(state['metadata']['asr']['state'], 'awaiting_provider')
        self.assertEqual(self.repo.list_transcripts(source['id']), [])

    def test_child_sessions_remain_separate(self):
        parent = self.repo.ensure_source('interview', 'i1', 'fake', 'account',
            {'room_id': 'room', 'room_name': 'hrv-i1', 'capture_instance_id': 'owner-i1',
             'exclusive_room_owner': {'kind': 'interview', 'id': 'i1'}}, storage_policy='remote')
        self.capture.artifacts.append(Artifact(replace(self.source, session_id='session2'),
                                               'transcript2', 'transcript', 'ready', {'format': 'vtt'}))
        self.worker.enqueue_discovery(parent['id'])
        self.drain()
        sources = self.repo.list_sources('interview', 'i1')
        self.assertEqual(len(sources), 3)
        versions = [t for s in sources for t in self.repo.list_transcripts(s['id'])]
        self.assertEqual(len(versions), 2)

    def test_suspend_during_download_prevents_publication_and_reconcile_resurrection(self):
        source = self.register()
        original = self.worker.downloader

        def revoked(*args, **kwargs):
            result = original(*args, **kwargs)
            self.repo.suspend_source(source['id'])
            return result

        self.worker.downloader = revoked
        self.worker.enqueue_discovery(source['id'])
        self.drain()
        self.assertEqual(self.repo.list_transcripts(source['id']), [])
        runtime = MaterialsRuntime(self.connect, self.repo, {'fake': self.capture}, self.storage,
                                   clock=lambda: self.clock[0])
        runtime.reconcile()
        self.assertEqual(self.repo.list_pending_jobs(), [])

    def test_backfill_ambiguous_room_does_not_contact_provider(self):
        with self.connect() as db:
            db.execute('UPDATE cp_interviews SET room=?', ('{"id":"room","name":"reused"}',))
        runtime = MaterialsRuntime(self.connect, self.repo, {'fake': self.capture}, self.storage)
        self.assertEqual(runtime.backfill()['ambiguous'], 1)
        self.assertEqual(self.repo.list_sources('interview', 'i1'), [])
        self.assertEqual(self.capture.resolved, [])

    def test_disabled_celery_delivery_does_not_construct_runtime(self):
        app = create_celery_app({'CELERY_BROKER_URL': 'memory://', 'HR_MATERIALS_TEST_MODE': '1',
                                 'HR_MATERIALS_ENABLED': '0'})
        self.assertEqual(app.tasks['materials.run_job'].run('untrusted-id'), {'enabled': False})

    def test_reconcile_republishes_committed_job_after_broker_failure(self):
        source = self.register()
        failed = lambda identity: (_ for _ in ()).throw(RuntimeError('broker unavailable'))
        self.worker.publish = failed
        job = self.worker.enqueue_discovery(source['id'])
        self.assertEqual(self.repo.get_job(job['id'])['status'], 'pending')
        delivered = []
        self.worker.publish = delivered.append
        self.worker.reconcile()
        self.assertEqual(delivered, [job['id']])

    def test_invalid_transcript_preserves_raw_and_has_no_fake_version(self):
        source = self.register()
        original = self.worker.downloader

        def invalid(*args, **kwargs):
            value = original(*args, **kwargs)
            if args[0].url.endswith('transcript'):
                Path(value.path).write_bytes(b'not a transcript')
                value = replace(value, size=16, sha256=hashlib.sha256(b'not a transcript').hexdigest())
            return value

        self.worker.downloader = invalid
        self.worker.enqueue_discovery(source['id'])
        self.drain()
        transcript = next(a for a in self.repo.list_assets(source['id']) if a['kind'] == 'transcript')
        self.assertEqual(transcript['state'], 'unsupported')
        self.assertEqual(self.repo.list_transcripts(source['id']), [])
        self.assertTrue(any(l['state'] == 'verified' for l in self.repo.list_locations(transcript['id'])))
        self.assertFalse(list(self.root.glob('materials-*')))

    def test_http_200_html_is_not_a_ready_video(self):
        source = self.register('owned')
        original = self.worker.downloader

        def html(*args, **kwargs):
            value = original(*args, **kwargs)
            if args[0].url.endswith('video'):
                body = b'<html>temporary upstream error</html>'
                Path(value.path).write_bytes(body)
                return replace(value, size=len(body), sha256=hashlib.sha256(body).hexdigest(), content_type='text/html')
            return value

        self.worker.downloader = html
        self.worker.enqueue_discovery(source['id'])
        results = self.drain()
        self.assertTrue(any(r.get('code') == 'unexpected_video_content' for r in results))
        video = next(a for a in self.repo.list_assets(source['id']) if a['kind'] == 'video')
        self.assertEqual(video['state'], 'error')
        self.assertEqual(self.repo.list_locations(video['id']), [])
        self.assertEqual(len(self.repo.list_transcripts(source['id'])), 1)

    def test_pending_write_is_adopted_after_crash_without_redownload(self):
        source = self.register()
        original = self.storage.put_file

        def crash(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('synthetic crash after durable Put')

        self.storage.put_file = crash
        self.worker.enqueue_discovery(source['id'])
        self.drain()
        transcript = next(a for a in self.repo.list_assets(source['id']) if a['kind'] == 'transcript')
        self.assertEqual(self.repo.list_locations(transcript['id'])[0]['state'], 'pending')
        self.storage.put_file = original
        self.clock[0] += 31
        self.drain()
        self.assertEqual(self.capture.resolved, ['transcript'])
        self.assertEqual(len(self.repo.list_transcripts(source['id'])), 1)
        self.assertEqual(self.repo.list_locations(transcript['id'])[0]['state'], 'verified')

    def test_storage_namespace_change_cannot_reinterpret_owned_key(self):
        source = self.register()
        self.worker.enqueue_discovery(source['id'])
        self.drain()
        transcript = next(a for a in self.repo.list_assets(source['id']) if a['kind'] == 'transcript')
        self.worker.storage = FileSystemStorage(self.root / 'another-store')
        job = self.worker.enqueue(source['id'], 'import_transcript', {'asset_id': transcript['id']}, token='new-config')
        result = self.worker.run_once(job['id'])
        self.assertEqual(result['code'], 'storage_connection_mismatch')
        self.assertEqual(self.capture.resolved, ['transcript'])

    def test_ready_refreshes_during_settling_then_waiting_polling_stops(self):
        source = self.register()
        self.worker.enqueue_discovery(source['id'])
        self.drain()
        runtime = MaterialsRuntime(self.connect, self.repo, {'fake': self.capture}, self.storage,
                                   clock=lambda: self.clock[0], settle_seconds=120)
        self.assertEqual(self.repo.get_source(source['id'])['state'], 'ready')
        self.assertEqual(runtime.reconcile()['rediscovered'], 1)
        self.repo.set_source_state(source['id'], 'awaiting_provider')
        self.clock[0] += 121
        jobs_before = len(self.repo.list_jobs(source['id']))
        self.assertEqual(runtime.reconcile()['rediscovered'], 0)
        self.assertEqual(len(self.repo.list_jobs(source['id'])), jobs_before)
        self.assertEqual(self.repo.get_source(source['id'])['state'], 'awaiting_provider')

    def test_later_manual_discovery_recovers_exhausted_copy_with_history(self):
        source = self.register('owned')
        self.fail_video = True
        self.worker.enqueue_discovery(source['id'])
        self.drain()
        original = next(j for j in self.repo.list_jobs(source['id']) if j['operation'] == 'copy_asset')
        for _ in range(original['max_attempts'] - 1):
            self.clock[0] += 31
            self.drain()
        self.assertEqual(self.repo.get_job(original['id'])['status'], 'failed')
        self.fail_video = False
        parent = self.worker.enqueue_discovery(source['id'], token='manual:later')
        self.worker.run_once(parent['id'])
        new_copy = [j for j in self.repo.list_jobs(source['id']) if j['operation'] == 'copy_asset' and j['id'] != original['id']]
        self.assertEqual(len(new_copy), 1)
        self.assertEqual(self.worker.run_once(parent['id']), {'claimed': False})
        self.drain()
        self.assertEqual(self.repo.get_job(original['id'])['status'], 'failed')
        self.assertEqual(self.repo.get_job(new_copy[0]['id'])['status'], 'succeeded')
        video = next(a for a in self.repo.list_assets(source['id']) if a['kind'] == 'video')
        self.assertEqual(video['state'], 'ready')
        self.worker.enqueue_discovery(source['id'], token='manual:after-success')
        self.drain()
        self.assertEqual(len([j for j in self.repo.list_jobs(source['id']) if j['operation'] == 'copy_asset']), 2)

    def test_new_discovery_does_not_bypass_copy_retry_backoff(self):
        source = self.register('owned')
        self.fail_video = True
        self.worker.enqueue_discovery(source['id'])
        self.drain()
        old = next(j for j in self.repo.list_jobs(source['id']) if j['operation'] == 'copy_asset')
        self.assertEqual(old['status'], 'retry')
        parent = self.worker.enqueue_discovery(source['id'], token='manual:too-soon')
        self.worker.run_once(parent['id'])
        copies = [j for j in self.repo.list_jobs(source['id']) if j['operation'] == 'copy_asset']
        self.assertEqual(len(copies), 1)
        current = self.repo.get_job(old['id'])
        self.assertEqual((current['status'], current['attempts'], current['next_attempt']),
                         ('retry', old['attempts'], old['next_attempt']))

    def test_backfill_excludes_no_recording_before_page_limit(self):
        with self.connect() as db:
            db.execute('ALTER TABLE cp_interviews ADD COLUMN candidate_id TEXT')
            db.execute('CREATE TABLE cp_intake (candidate_id TEXT PRIMARY KEY)')
            db.execute('CREATE TABLE cp_recording_control (interview_id TEXT PRIMARY KEY,start_requested_at TEXT)')
            for identity in ('a-flag', 'b-prepared', 'z-eligible'):
                room = {'id': 'room-' + identity, 'name': 'hrv-' + identity}
                if identity == 'a-flag':
                    room['captureNeverStarted'] = True
                db.execute('INSERT INTO cp_interviews VALUES (?,?,?,?)',
                           (identity, 'completed', json.dumps(room), 'candidate-' + identity))
            db.execute("INSERT INTO cp_intake VALUES ('candidate-b-prepared')")
            db.execute("INSERT INTO cp_recording_control VALUES ('b-prepared',NULL)")
        runtime = MaterialsRuntime(self.connect, self.repo, {'fake': self.capture}, self.storage)
        result = runtime.backfill(limit=1)
        self.assertEqual(result['registered'], 1)
        self.assertEqual(result['skipped'], 2)
        self.assertEqual(self.repo.list_sources('interview', 'a-flag'), [])
        self.assertEqual(self.repo.list_sources('interview', 'b-prepared'), [])
        self.assertEqual(len(self.repo.list_sources('interview', 'z-eligible')), 1)
        self.assertEqual(self.capture.resolved, [])


if __name__ == '__main__':
    unittest.main()
