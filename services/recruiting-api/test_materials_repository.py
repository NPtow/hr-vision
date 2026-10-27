import concurrent.futures
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from materials_repository import MaterialsRepository, RepositoryError


class MaterialsRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'catalog.sqlite3'
        self.time = 1000.0
        with self.connect() as db:
            db.executescript('''CREATE TABLE cp_interviews(id TEXT PRIMARY KEY);
                CREATE TABLE cp_meetings(id TEXT PRIMARY KEY);
                INSERT INTO cp_interviews VALUES ('i1'),('i2');
                INSERT INTO cp_meetings VALUES ('m1');''')
        self.repo = MaterialsRepository(self.connect, clock=lambda: self.time)
        self.repo.init()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def source(self, **extra):
        args = dict(owner_kind='interview', owner_id='i1', provider='daily', connection_id='account-one',
                    source_ref={'room_id': 'room-one', 'room_name': 'hrv-i1', 'capture_instance_id': 'capture-one'},
                    storage_policy='copy')
        args.update(extra)
        return self.repo.ensure_source(**args)

    def job(self, source=None, **extra):
        return self.repo.enqueue_job((source or self.source())['id'], 'discover', 'request-one', **extra)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(RepositoryError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_owner_fk_and_distinct_captures_in_reused_room(self):
        first = self.source()
        self.assertEqual(self.source()['id'], first['id'])
        second = self.source(source_ref={**first['source_ref'], 'capture_instance_id': 'capture-two'})
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(len(self.repo.list_sources('interview', 'i1')), 2)
        self.assert_code('owner_not_found', self.source, owner_id='missing')
        self.assert_code('invalid_owner', self.source, owner_kind='candidate')

    def test_capture_identity_cannot_be_rebound_to_another_owner(self):
        self.source()
        self.assert_code('source_identity_conflict', self.source, owner_id='i2')

    def test_database_enforces_exactly_one_typed_owner(self):
        self.source()
        with self.connect() as db:
            for values in [(None, None), ('i1', 'm1')]:
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute('UPDATE media_sources SET interview_id=?,meeting_id=?', values)
                db.rollback()

    def test_reference_urls_and_credentials_rejected_recursively(self):
        for ref in [{'room_id': 'r', 'url': 'https://example.com/x'},
                    {'room_id': 'r', 'nested': {'token': 'secret'}},
                    {'room_id': 'https://example.com/?signature=sensitive'}]:
            self.assert_code('unsafe_reference', self.source, source_ref=ref)

    def test_asset_channels_independent_missing_explicit_and_no_regression(self):
        source = self.source()
        video = self.repo.upsert_asset(source['id'], 'video', 'recording-one', state='ready')
        transcript = self.repo.upsert_asset(source['id'], 'transcript', 'transcript-one', state='pending')
        self.assertEqual(self.repo.get_asset(video['id'])['state'], 'ready')
        self.assertEqual(self.repo.get_asset(transcript['id'])['state'], 'pending')
        again = self.repo.upsert_asset(source['id'], 'video', 'recording-one', state='pending')
        self.assertEqual(again['state'], 'ready')
        self.assert_code('missing_not_confirmed', self.repo.upsert_asset,
                         source['id'], 'transcript', 'transcript-one', state='missing')
        missing = self.repo.upsert_asset(source['id'], 'transcript', 'transcript-one', state='missing',
                                        metadata={'explicit_missing': True})
        self.assertEqual(missing['state'], 'missing')

    def test_transcript_revision_immutable_and_raw_reference_retained(self):
        source = self.source()
        asset = self.repo.upsert_asset(source['id'], 'transcript', 't1', state='ready')
        one = self.repo.save_transcript(asset['id'], {'backend': 'private', 'object_key': 'raw/t1.vtt'},
                                        [{'text': 'First', 'start_ms': 0, 'end_ms': 1000, 'speaker': 'A'}])
        same = self.repo.save_transcript(asset['id'], one['raw_ref'], one['segments'])
        two = self.repo.save_transcript(asset['id'], {'backend': 'private', 'object_key': 'raw/t2.vtt'},
                                        [{'text': 'Corrected', 'start_ms': 0, 'end_ms': 1000, 'speaker': 'A'}])
        self.assertEqual(one['id'], same['id'])
        self.assertEqual((one['version'], two['version']), (1, 2))
        self.assertEqual(self.repo.list_transcripts(source['id'])[0]['segments'][0]['text'], 'First')
        with self.connect() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE transcript_versions SET segments='[]' WHERE id=?", (one['id'],))

    def test_location_idempotency_and_verified_copy_needs_checksum(self):
        asset = self.repo.upsert_asset(self.source()['id'], 'video', 'r1')
        self.assert_code('invalid_location', self.repo.add_location, asset['id'], 's3', 'bucket/object', state='verified')
        location = self.repo.add_location(asset['id'], 's3', 'bucket/object', state='verified', size=5, sha256='a' * 64)
        self.assertEqual(self.repo.add_location(asset['id'], 's3', 'bucket/object', state='pending')['state'], 'verified')
        self.assertEqual(len(self.repo.list_locations(asset['id'])), 1)
        self.assert_code('unsafe_reference', self.repo.add_location, asset['id'], 's3', 'https://signed.example/x')
        self.assertEqual(location['size'], 5)

    def test_two_workers_can_only_claim_once(self):
        job = self.job()
        barrier = threading.Barrier(2)
        def claim(worker):
            repository = MaterialsRepository(self.connect, clock=lambda: self.time)
            barrier.wait()
            return repository.claim_job(worker, job_id=job['id'])
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ['one', 'two']))
        self.assertEqual(sum(value is not None for value in results), 1)
        self.assertEqual(self.repo.get_job(job['id'])['attempts'], 1)

    def test_expired_reclaimed_worker_cannot_publish_or_finish(self):
        source = self.source()
        job = self.job(source)
        first = self.repo.claim_job('one', lease_seconds=10, job_id=job['id'])
        self.time += 11
        second = self.repo.claim_job('two', lease_seconds=10, job_id=job['id'])
        self.assertNotEqual(first['lease_token'], second['lease_token'])
        self.assert_code('stale_lease', self.repo.upsert_asset, source['id'], 'video', 'r1', state='ready',
                         job_id=job['id'], lease_token=first['lease_token'])
        self.assert_code('stale_lease', self.repo.finish_job, job['id'], first['lease_token'])
        asset = self.repo.upsert_asset(source['id'], 'video', 'r1', state='ready',
                                       job_id=job['id'], lease_token=second['lease_token'])
        self.repo.finish_job(job['id'], second['lease_token'], result={'asset_id': asset['id']})
        self.assertEqual(self.repo.get_job(job['id'])['status'], 'succeeded')
        self.assertEqual([item['status'] for item in self.repo.list_attempts(job['id'])], ['expired', 'succeeded'])

    def test_generation_invalidation_fences_active_worker_and_new_jobs(self):
        source = self.source()
        job = self.job(source)
        lease = self.repo.claim_job('one', job_id=job['id'])
        self.repo.invalidate_source(source['id'])
        self.assert_code('stale_lease', self.repo.finish_job, job['id'], lease['lease_token'])
        self.assertEqual(self.repo.get_job(job['id'])['status'], 'cancelled')
        next_job = self.job(source)
        self.assertNotEqual(job['id'], next_job['id'])
        self.assertEqual(next_job['generation'], source['generation'] + 1)

    def test_retry_survives_new_repository_and_stops_at_attempt_limit(self):
        job = self.job(max_attempts=2)
        lease = self.repo.claim_job('one', lease_seconds=10)
        self.repo.fail_job(job['id'], lease['lease_token'], 'temporary_error', retry_after=5)
        self.assertIsNone(self.repo.claim_job('two'))
        self.time += 6
        new_repo = MaterialsRepository(self.connect, clock=lambda: self.time)
        second = new_repo.claim_job('two', lease_seconds=10)
        self.assertEqual(second['attempts'], 2)
        new_repo.fail_job(job['id'], second['lease_token'], 'temporary_error')
        self.assertEqual(new_repo.get_job(job['id'])['status'], 'failed')
        self.assertIsNone(new_repo.claim_job('three'))

    def test_cancel_and_reconcile_fence_old_workers(self):
        job = self.job(max_attempts=1)
        lease = self.repo.claim_job('one', lease_seconds=5)
        self.time += 6
        self.repo.reconcile_jobs()
        self.assertEqual(self.repo.get_job(job['id'])['status'], 'failed')
        self.assert_code('stale_lease', self.repo.renew_job, job['id'], lease['lease_token'])

    def test_event_deduplication_scoped_by_provider_account(self):
        first = self.repo.record_event('daily', 'one', 'event-one', {'event': 'ready', 'recording_id': 'r1'})
        duplicate = self.repo.record_event('daily', 'one', 'event-one', {'event': 'ready', 'recording_id': 'r1'})
        another = self.repo.record_event('daily', 'two', 'event-one', {'event': 'ready', 'recording_id': 'r1'})
        self.assertEqual(first['id'], duplicate['id'])
        self.assertNotEqual(first['id'], another['id'])
        self.assert_code('event_identity_conflict', self.repo.record_event, 'daily', 'one', 'event-one', {'event': 'different'})

    def test_parent_discovery_creates_child_but_cannot_publish_child_outputs(self):
        parent = self.source()
        job = self.job(parent)
        lease = self.repo.claim_job('discoverer', job_id=job['id'])
        fence = {'job_id': job['id'], 'lease_token': lease['lease_token']}
        child = self.source(source_ref={'room_id': 'room-one', 'session_id': 'actual-session'}, **fence)
        self.assertEqual(child['parent_source_id'], parent['id'])
        self.assertEqual(child['storage_policy'], 'owned')
        child_job = self.repo.enqueue_job(child['id'], 'discover', 'child', **fence)
        self.assertEqual(child_job['source_id'], child['id'])
        self.assert_code('stale_lease', self.repo.upsert_asset, child['id'], 'video', 'r1', **fence)
        self.repo.invalidate_owner('interview', 'i1')
        self.assert_code('stale_lease', self.source,
                         source_ref={'room_id': 'room-one', 'session_id': 'late-child'}, **fence)
        self.assert_code('stale_lease', self.repo.enqueue_job, child['id'], 'discover', 'late', **fence)
        self.assertEqual(self.repo.get_job(child_job['id'])['status'], 'cancelled')

    def test_durable_owner_suspension_blocks_requeue_and_preserves_other_owners(self):
        source = self.source()
        other = self.source(owner_id='i2', source_ref={'room_id': 'r2', 'capture_instance_id': 'capture-two'})
        job = self.job(source)
        lease = self.repo.claim_job('worker', job_id=job['id'])
        asset = self.repo.upsert_asset(source['id'], 'video', 'r1')
        self.repo.suspend_owner('interview', 'i1')
        self.assert_code('source_disabled', self.repo.enqueue_job, source['id'], 'discover', 'new-generation')
        self.assert_code('stale_lease', self.repo.add_location, asset['id'], 's3', 'bucket/object',
                         job_id=job['id'], lease_token=lease['lease_token'])
        self.assertEqual(self.repo.get_source(other['id'])['generation'], 1)
        self.assertEqual(self.repo.list_pending_jobs(), [])

    def test_owner_policy_change_fences_children_and_is_idempotent(self):
        parent = self.source(storage_policy='remote')
        job = self.job(parent)
        lease = self.repo.claim_job('discoverer', job_id=job['id'])
        child = self.source(source_ref={'room_id': 'room-one', 'session_id': 's1'}, storage_policy='remote',
                            job_id=job['id'], lease_token=lease['lease_token'])
        updated = self.repo.update_owner_policy('interview', 'i1', 'owned')
        self.assertEqual({row['id'] for row in updated}, {parent['id'], child['id']})
        self.assertTrue(all(row['storage_policy'] == 'owned' and row['generation'] == 2 for row in updated))
        self.assertTrue(all(row['generation'] == 2 for row in self.repo.update_owner_policy('interview', 'i1', 'copy')))

    def test_fenced_transcript_rejects_expired_lease_before_any_revision(self):
        source = self.source()
        asset = self.repo.upsert_asset(source['id'], 'transcript', 't1')
        job = self.job(source)
        lease = self.repo.claim_job('worker', lease_seconds=5, job_id=job['id'])
        self.time += 6
        self.assert_code('stale_lease', self.repo.save_transcript, asset['id'], {'object_key': 'raw/t1.vtt'},
                         [{'text': 'stale'}], job_id=job['id'], lease_token=lease['lease_token'])
        self.assertEqual(self.repo.list_transcripts(source['id']), [])

    def test_schema_init_does_not_commit_caller_transaction(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("INSERT INTO cp_interviews VALUES ('not-committed')")
            self.repo.init(db)
            db.rollback()
        self.assert_code('owner_not_found', self.source, owner_id='not-committed')

    def test_duplicate_enqueue_does_not_reset_failed_job_or_payload(self):
        job = self.job(payload={'asset_id': 'one'}, max_attempts=1)
        lease = self.repo.claim_job('worker', job_id=job['id'])
        self.repo.fail_job(job['id'], lease['lease_token'], 'permanent', retryable=False)
        duplicate = self.job(payload={'asset_id': 'one'})
        self.assertEqual(duplicate['id'], job['id'])
        self.assertEqual(duplicate['status'], 'failed')
        self.assert_code('job_identity_conflict', self.job, payload={'asset_id': 'other'})

    def test_transcript_content_can_discuss_urls_tokens_and_passwords(self):
        source = self.source()
        asset = self.repo.upsert_asset(source['id'], 'transcript', 't1')
        words = 'Visit https://jobs.example.com and discuss password/token handling.'
        version = self.repo.save_transcript(asset['id'], {'object_key': 'raw/t1.vtt'}, [{'text': words}],
            metadata={'origin': 'provider_import', 'normalized': {'full_text': words, 'segments': [{'text': words}]}})
        self.assertEqual(version['segments'][0]['text'], words)
        self.assertEqual(version['metadata']['normalized']['full_text'], words)
        self.assert_code('invalid_transcript_provenance', self.repo.save_transcript, asset['id'],
                         {'object_key': 'raw/t1.vtt'}, [], metadata={'authorization': 'secret'})

    def test_actual_session_identity_is_account_scoped_even_with_capture_hint(self):
        first = self.source(source_ref={'room_id': 'r1', 'session_id': 'actual', 'capture_instance_id': 'hint'})
        # A second bootstrap hint cannot create a second owner for a known session.
        self.assert_code('source_identity_conflict', self.source, owner_id='i2',
                         source_ref={'room_id': 'r1', 'session_id': 'actual', 'capture_instance_id': 'another-hint'})
        other_account = self.source(connection_id='another-account',
            source_ref={'room_id': 'r1', 'session_id': 'actual', 'capture_instance_id': 'hint'})
        self.assertNotEqual(first['id'], other_account['id'])


if __name__ == '__main__':
    unittest.main()
