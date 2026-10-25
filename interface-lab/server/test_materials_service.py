"""Assembled portal/repository/worker/service tests; synthetic bytes, no network."""
import hashlib
import io
import json
from pathlib import Path
import unittest

import test_candidate_portal as portal_fixture
import test_candidate_invites as invitation_fixture
from domain import Problem
from materials_capture import Artifact, Discovery, HttpSource, Source
from materials_http import DownloadResult
from materials_repository import MaterialsRepository
from materials_runtime import MaterialsRuntime
from materials_service import MaterialsService
from materials_storage import FileSystemStorage, S3Storage


VTT = 'WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nСобственный проверенный текст\n'.encode()
VIDEO = b'\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isommp42'  # Synthetic container header, not a real recording.


class CaptureFixture:
    def __init__(self):
        self.discover_calls, self.resolve_calls = [], []
        self.unavailable = False
        self.on_resolve = None

    def discover(self, ref):
        self.discover_calls.append(dict(ref))
        if self.unavailable:
            raise OSError('synthetic provider outage')
        source = Source(ref['provider'], ref['connection_id'], ref['room_id'], ref['room_name'],
                        ref.get('session_id') or 'session-' + ref['room_id'])
        artifacts = [Artifact(source, source.room_id + '-video', 'video', 'ready', {'duration': 1}),
                     Artifact(source, source.room_id + '-text', 'transcript', 'ready', {'format': 'vtt'})]
        return Discovery(artifacts, {kind: {'status': 'ready', 'complete': True}
                                     for kind in ('video', 'transcript')}, True)

    def inspect(self, artifact):
        if self.unavailable:
            raise OSError('synthetic provider outage')
        return artifact

    def resolve(self, artifact):
        self.resolve_calls.append(artifact)
        if self.unavailable:
            raise OSError('synthetic provider outage')
        if self.on_resolve:
            self.on_resolve(artifact)
        return HttpSource('https://synthetic.invalid/' + artifact.kind + '?temporary=not-persisted')


class MemoryS3Client:
    """Exercise actual S3Storage without an SDK network transport."""
    def __init__(self):
        self.objects, self.signatures = {}, []

    def put_object(self, **kw):
        body = kw['Body'].read()
        self.objects[kw['Key']] = {'Body': body, 'ContentLength': len(body),
                                  'Metadata': kw['Metadata'], 'ContentType': kw['ContentType']}

    def head_object(self, **kw):
        return {key: value for key, value in self.objects[kw['Key']].items() if key != 'Body'}

    def get_object(self, **kw):
        return {'Body': io.BytesIO(self.objects[kw['Key']]['Body'])}

    def generate_presigned_url(self, operation, **kw):
        self.signatures.append((operation, kw))
        return 'https://private-storage.example/signed-capability'


class MaterialsServiceIntegrationTests(unittest.TestCase):
    def setUp(self):
        # Compose the established full portal fixture without inheriting its tests.
        self.fixture = portal_fixture.CandidatePortalTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.portal, self.connect = self.fixture.portal, self.fixture.connect
        self.clock = [1_800_000_000.0]
        self.repository = MaterialsRepository(self.connect, clock=lambda: self.clock[0])
        self.repository.init()
        self.capture = CaptureFixture()
        self.storage = FileSystemStorage(Path(self.fixture.tmp.name) / 'owned-materials')
        self.downloads = []

        def downloader(source, destination, **kwargs):
            self.downloads.append(source.url)
            transcript = '/transcript?' in source.url
            body = VTT if transcript else VIDEO
            Path(destination).write_bytes(body)
            return DownloadResult(str(destination), len(body), hashlib.sha256(body).hexdigest(),
                                  'text/vtt' if transcript else 'video/mp4')

        self.runtime = MaterialsRuntime(self.connect, self.repository,
            {('daily', 'daily'): self.capture}, self.storage, downloader=downloader,
            temp_root=self.fixture.tmp.name, clock=lambda: self.clock[0], settle_seconds=120)
        self.service = MaterialsService(self.runtime)
        self.portal.materials_catalog = self.service

    def complete(self):
        person, cookie = self.fixture.completed_person()
        with self.connect() as db:
            meeting = self.portal.interview(db, person['id'])
        return person, cookie, meeting

    def drain(self):
        results = []
        for _ in range(30):
            result = self.runtime.worker.run_once()
            if not result['claimed']:
                return results
            results.append(result)
        self.fail('Synthetic material jobs did not settle')

    def catalogue(self, meeting):
        return self.service.list_materials('interview', meeting['id'])

    def video(self, meeting):
        return next(asset for asset in self.catalogue(meeting)['assets'] if asset['kind'] == 'video')

    def complete_without_start(self):
        person = invitation_fixture.CandidateInvitationTest.prepared(self.fixture)
        slot = self.fixture.slot()
        self.fixture.call('recruiter/interview/book',
                          {'candidateId': person['id'], 'slotId': slot['id']}, manager=True)
        self.fixture.call('recruiter/interview/join', {'candidateId': person['id']}, manager=True)
        self.fixture.call('recruiter/interview/complete', {'candidateId': person['id']}, manager=True)
        with self.connect() as db:
            meeting = self.portal.interview(db, person['id'])
        return person, meeting

    def assert_no_material_work(self, meeting):
        self.assertEqual(self.repository.list_sources('interview', meeting['id']), [])
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM media_sources').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM media_jobs').fetchone()[0], 0)
        self.assertEqual(self.capture.discover_calls, [])
        self.assertEqual(self.capture.resolve_calls, [])
        self.assertEqual(self.downloads, [])

    def test_prepared_join_complete_without_start_persists_never_recorded_and_queues_nothing(self):
        person, meeting = self.complete_without_start()
        self.assertIs(meeting['room']['captureNeverStarted'], True)
        with self.connect() as db:
            stored = json.loads(db.execute('SELECT room FROM cp_interviews WHERE id=?',
                                           (meeting['id'],)).fetchone()[0])
        self.assertIs(stored['captureNeverStarted'], True)
        result, _ = self.fixture.call('recruiter/interview/materials',
                                      {'candidateId': person['id']}, manager=True)
        self.assertEqual(result['status'], 'not_recorded')
        self.assertEqual(result['video']['status'], 'not_recorded')
        self.assertEqual(result['transcription']['status'], 'not_recorded')
        self.assert_no_material_work(meeting)

    def test_legacy_prepared_completed_without_room_flag_still_skips_capture(self):
        person, meeting = self.complete_without_start()
        room = dict(meeting['room'])
        room.pop('captureNeverStarted')
        with self.connect() as db:
            db.execute('UPDATE cp_interviews SET room=? WHERE id=?', (json.dumps(room), meeting['id']))
        result, _ = self.fixture.call('recruiter/interview/materials',
                                      {'candidateId': person['id']}, manager=True)
        self.assertEqual(result['status'], 'not_recorded')
        self.assertEqual(result['recordings'], [])
        self.assertEqual(result['transcripts'], [])
        self.assert_no_material_work(meeting)

    def test_manual_discovery_retry_is_durable_minute_coalesced_and_overrides_failed_history(self):
        person, _, meeting = self.complete()
        source = self.repository.list_sources('interview', meeting['id'])[0]
        original = self.repository.list_jobs(source['id'])[0]
        claimed = self.repository.claim_job('synthetic-failure', 60, job_id=original['id'])
        self.repository.fail_job(claimed['id'], claimed['lease_token'], 'provider_unavailable', retryable=False)
        self.repository.set_source_state(source['id'], 'error',
            {'discovery': {kind: {'status': 'error'} for kind in ('video', 'transcript')}})
        self.clock[0] += self.runtime.settle_seconds + 61
        self.assertEqual(self.service.legacy_materials('interview', meeting)['status'], 'error')
        result, _ = self.fixture.call('recruiter/interview/materials',
                                      {'candidateId': person['id']}, manager=True)
        jobs = self.repository.list_jobs(source['id'])
        self.assertEqual(len(jobs), 2)
        fresh = next(job for job in jobs if job['id'] != original['id'])
        self.assertEqual(fresh['status'], 'pending')
        self.assertEqual(result['video']['status'], 'processing')
        self.assertEqual(result['transcription']['status'], 'processing')
        self.fixture.call('recruiter/interview/materials', {'candidateId': person['id']}, manager=True)
        self.assertEqual(len(self.repository.list_jobs(source['id'])), 2)
        # A backoff retry remains active even though the historical job failed.
        claimed = self.repository.claim_job('synthetic-retry', 60, job_id=fresh['id'])
        self.repository.fail_job(claimed['id'], claimed['lease_token'], 'provider_unavailable',
                                 retryable=True, retry_after=30)
        self.assertEqual(self.service.legacy_materials('interview', meeting)['status'], 'processing')
        self.assertEqual(self.capture.discover_calls, [])
        self.assertEqual(self.capture.resolve_calls, [])
        self.assertEqual(self.downloads, [])

    def test_settled_absent_or_missing_materials_report_error_without_active_jobs(self):
        for missing in (False, True):
            with self.subTest(explicit_missing=missing):
                _, _, meeting = self.complete()

                def discover(ref):
                    self.capture.discover_calls.append(dict(ref))
                    source = Source(ref['provider'], ref['connection_id'], ref['room_id'], ref['room_name'],
                                    ref.get('session_id') or 'session-' + ref['room_id'])
                    artifacts = ([Artifact(source, source.room_id + '-' + kind, kind, 'missing')
                                  for kind in ('video', 'transcript')] if missing else [])
                    return Discovery(artifacts, {kind: {'status': 'absent', 'complete': True}
                                                 for kind in ('video', 'transcript')}, True)

                self.capture.discover = discover
                self.drain()
                self.clock[0] += self.runtime.settle_seconds + 1
                data = self.catalogue(meeting)
                self.assertFalse(any(job['status'] in ('pending', 'retry', 'running') for job in data['jobs']))
                result = self.service.legacy_materials('interview', meeting)
                self.assertEqual(result['status'], 'error')
                self.assertEqual(result['video']['status'], 'error')
                self.assertEqual(result['transcription']['status'], 'error')
                self.assertEqual(result['recordings'], [])
                self.assertEqual(result['transcripts'], [])

    def test_completed_hook_persists_job_without_provider_discovery(self):
        _, _, meeting = self.complete()
        sources = self.repository.list_sources('interview', meeting['id'])
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]['source_ref']['exclusive_room_owner'],
                         {'kind': 'interview', 'id': meeting['id']})
        self.assertEqual(self.capture.discover_calls, [])
        restarted = MaterialsRepository(self.connect)
        jobs = restarted.list_jobs(sources[0]['id'])
        self.assertEqual([(job['operation'], job['status']) for job in jobs], [('discover', 'pending')])

    def test_legacy_completed_read_registers_durable_job_without_discovery(self):
        self.portal.materials_catalog = None
        person, cookie, meeting = self.complete()
        self.assertEqual(self.repository.list_sources('interview', meeting['id']), [])
        self.portal.materials_catalog = self.service
        result, _ = self.fixture.call('candidate/interview/materials', {}, cookie)
        sources = self.repository.list_sources('interview', meeting['id'])
        self.assertEqual(len(sources), 1)
        self.assertTrue(self.repository.list_jobs(sources[0]['id']))
        self.assertEqual(result['status'], 'processing')
        self.assertEqual(self.capture.discover_calls, [])
        self.assertEqual(self.downloads, [])

    def test_catalogue_read_never_discovers_or_resolves_provider(self):
        _, _, meeting = self.complete()
        self.drain()
        before = (len(self.capture.discover_calls), len(self.capture.resolve_calls), len(self.downloads))
        self.capture.unavailable = True
        catalogue = self.catalogue(meeting)
        self.assertEqual(len(catalogue['assets']), 2)
        self.assertEqual(len(catalogue['transcripts']), 1)
        self.assertEqual(before, (len(self.capture.discover_calls), len(self.capture.resolve_calls), len(self.downloads)))

    def test_backfill_completed_owner_persists_job_before_any_provider_work(self):
        self.portal.materials_catalog = None
        _, _, meeting = self.complete()
        result = self.runtime.backfill()
        self.assertEqual(result['registered'], 1)
        sources = self.repository.list_sources('interview', meeting['id'])
        self.assertEqual(len(sources), 1)
        self.assertEqual(self.repository.list_jobs(sources[0]['id'])[0]['status'], 'pending')
        self.assertEqual(self.capture.discover_calls, [])
        self.assertEqual(self.downloads, [])

    def test_reconcile_requires_explicit_automatic_backfill_opt_in(self):
        self.portal.materials_catalog = None
        _, _, meeting = self.complete()
        self.assertEqual(self.runtime.reconcile()['backfill'], {'enabled': False})
        self.assertEqual(self.repository.list_sources('interview', meeting['id']), [])
        self.runtime.backfill_enabled = True
        self.assertEqual(self.runtime.reconcile()['backfill']['registered'], 1)
        self.assertEqual(len(self.repository.list_sources('interview', meeting['id'])), 1)

    def test_owned_transcript_survives_provider_outage_on_candidate_read(self):
        _, cookie, meeting = self.complete()
        self.drain()
        version = self.catalogue(meeting)['transcripts'][0]
        raw_location = next(location for location in self.repository.list_locations(version['asset_id'])
                            if location['state'] == 'verified' and location['backend'] == 'filesystem')
        self.assertEqual(self.storage.read_bytes(raw_location['object_key']), VTT)
        self.capture.unavailable = True
        result, _ = self.fixture.call('candidate/interview/materials', {}, cookie)
        self.assertEqual(result['transcription']['status'], 'ready')
        self.assertEqual(result['transcripts'][0]['fullText'], 'Собственный проверенный текст')
        self.assertEqual(result['transcripts'][0]['storage'], 'owned')
        self.assertEqual(result['video']['status'], 'error')
        own = self.service.get_transcript('interview', meeting['id'], version['id'])
        self.assertEqual(own['text'], 'Собственный проверенный текст')
        self.assertEqual(len(self.downloads), 1)

    def test_remote_video_has_no_owned_copy_and_resolves_only_at_access(self):
        _, _, meeting = self.complete()
        self.drain()
        self.assertEqual([artifact.kind for artifact in self.capture.resolve_calls], ['transcript'])
        video = self.video(meeting)
        locations = self.repository.list_locations(video['id'])
        self.assertEqual([location['backend'] for location in locations], ['provider'])
        self.assertEqual(len(self.downloads), 1)
        access = self.service.get_asset_access('interview', meeting['id'], video['id'])
        self.assertEqual(access['storage'], 'remote')
        self.assertEqual([artifact.kind for artifact in self.capture.resolve_calls], ['transcript', 'video'])
        # The URL is returned transiently; catalogue persistence contains no signed URL.
        self.assertNotIn('temporary=', str(self.catalogue(meeting)))

    def test_owned_policy_copies_video_and_serves_private_s3_capability(self):
        client = MemoryS3Client()
        storage = S3Storage('synthetic-private-bucket', client=client)
        self.runtime.storage = self.runtime.worker.storage = storage
        person, _, meeting = self.complete()
        self.fixture.call('recruiter/interview/materials',
                          {'candidateId': person['id'], 'storagePolicy': 'owned'}, manager=True)
        self.drain()
        self.assertEqual(len(self.downloads), 2)
        video = self.video(meeting)
        self.assertTrue(any(location['backend'] == 's3' and location['state'] == 'verified'
                            for location in self.repository.list_locations(video['id'])))
        self.capture.unavailable = True
        resolved_before = len(self.capture.resolve_calls)
        access = self.service.get_asset_access('interview', meeting['id'], video['id'])
        self.assertEqual(access['storage'], 'owned')
        self.assertEqual(access['url'], 'https://private-storage.example/signed-capability')
        self.assertEqual(len(self.capture.resolve_calls), resolved_before)
        self.assertEqual(client.signatures[-1][1]['ExpiresIn'], 300)
        self.assertEqual(client.signatures[-1][1]['HttpMethod'], 'GET')

    def test_asset_and_transcript_cannot_be_read_through_another_owner(self):
        _, _, first = self.complete()
        self.drain()
        _, _, second = self.complete()
        data = self.catalogue(first)
        video = next(asset for asset in data['assets'] if asset['kind'] == 'video')
        count = len(self.capture.resolve_calls)
        for access in (lambda: self.service.get_asset_access('interview', second['id'], video['id']),
                       lambda: self.service.get_transcript('interview', second['id'], data['transcripts'][0]['id'])):
            with self.assertRaises(Problem) as caught:
                access()
            self.assertEqual(caught.exception.status, 404)
        self.assertEqual(len(self.capture.resolve_calls), count)

    def test_candidate_logout_during_resolve_is_rechecked_before_return(self):
        _, cookie, _ = self.complete()
        self.drain()
        self.capture.on_resolve = lambda _: self.fixture.call('candidate/logout', {}, cookie)
        with self.assertRaises(Problem) as caught:
            self.fixture.call('candidate/interview/materials', {}, cookie)
        self.assertEqual(caught.exception.status, 401)

    def test_candidate_cannot_change_storage_policy_but_recruiter_can(self):
        person, cookie, meeting = self.complete()
        source_ids = [source['id'] for source in self.repository.list_sources('interview', meeting['id'])]
        with self.assertRaises(Problem) as caught:
            self.fixture.call('candidate/interview/materials', {'storagePolicy': 'owned'}, cookie)
        self.assertEqual(caught.exception.status, 403)
        self.assertTrue(all(self.repository.get_source(sid)['storage_policy'] == 'remote' for sid in source_ids))
        self.fixture.call('recruiter/interview/materials',
                          {'candidateId': person['id'], 'storagePolicy': 'owned'}, manager=True)
        self.assertTrue(all(self.repository.get_source(sid)['storage_policy'] == 'owned' for sid in source_ids))
        self.assertEqual(self.capture.discover_calls, [])

    def test_source_invalidation_during_access_resolve_rejects_capability(self):
        _, _, meeting = self.complete()
        self.drain()
        video = self.video(meeting)
        self.capture.on_resolve = lambda _: self.repository.invalidate_source(video['source_id'])
        with self.assertRaises(Problem) as caught:
            self.service.get_asset_access('interview', meeting['id'], video['id'])
        self.assertEqual(caught.exception.status, 409)

    def test_source_invalidated_during_worker_resolve_cannot_publish_material(self):
        _, _, meeting = self.complete()
        # Drain root and exact-session discoveries only; imports stay queued.
        self.assertEqual(self.runtime.worker.run_once()['state'], 'completed')
        self.assertEqual(self.runtime.worker.run_once()['state'], 'completed')
        transcript = next(asset for asset in self.catalogue(meeting)['assets'] if asset['kind'] == 'transcript')
        self.capture.on_resolve = lambda _: self.repository.invalidate_source(transcript['source_id'])
        results = self.drain()
        self.assertTrue(any(result['state'] == 'stale' for result in results), results)
        self.assertEqual(self.repository.list_locations(transcript['id']), [])
        self.assertEqual(self.catalogue(meeting)['transcripts'], [])


if __name__ == '__main__':
    unittest.main()
