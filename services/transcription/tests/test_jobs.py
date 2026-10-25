import hashlib
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from sqlalchemy import update
from md_transcription.settings import Settings
from md_transcription.repository import Repository, RepositoryConflict, jobs
from md_transcription.storage import Storage
from md_transcription.worker import Runtime
from md_transcription.normalization import normalize_transcript

def canonical(text):
    import json
    return normalize_transcript(json.dumps({'schema_version':1,'segments':[{'text':text}]}).encode(),'json')

class JobsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(data_dir=Path(self.temp.name), asr_url='https://asr.invalid/transcribe', asr_model='test-model')
        self.repo = Repository(self.settings.database_url)
        self.repo.init()
    def submit(self, client='alice', key='one', sha='a'*64):
        return self.repo.submit(client,key,sha,sha,3,'test.wav',None,self.settings.backend_identity)
    def test_upload_intent_crash_and_reconcile(self):
        digest = hashlib.sha256(b'abc').hexdigest()
        job,_ = self.repo.submit('alice','upload',digest,digest,3,'input.wav',None,self.settings.backend_identity,initial_status='uploading')
        runtime = Runtime(self.settings,repository=self.repo,backend=object(),publish=lambda _: None)
        self.assertEqual(runtime.reconcile()['activated'],0)
        self.assertIsNone(self.repo.claim(job['id'],60,3))
        source = Path(self.temp.name)/'source'
        source.write_bytes(b'abc')
        runtime.storage.put_file(source,digest)
        self.assertEqual(runtime.reconcile()['activated'],1)
        self.assertEqual(self.repo.get(job['id'])['status'],'queued')

    def test_reconcile_uploads_beyond_first_hundred_broken_intents(self):
        digest = hashlib.sha256(b'abc').hexdigest()
        ready = None
        # Assign ordered IDs so the complete blob is deterministically on page two.
        for index in range(101):
            sha = digest if index == 100 else hashlib.sha256(str(index).encode()).hexdigest()
            job,_ = self.repo.submit('alice',f'upload-{index}',sha,sha,3,'input.wav',None,self.settings.backend_identity,initial_status='uploading')
            with self.repo.engine.begin() as conn:
                conn.execute(update(jobs).where(jobs.c.id==job['id']).values(id=f'{index:04d}'))
            if index == 100: ready = f'{index:04d}'
        runtime = Runtime(self.settings,repository=self.repo,backend=object(),publish=lambda _: None)
        source = Path(self.temp.name)/'ready'
        source.write_bytes(b'abc')
        runtime.storage.put_file(source,digest)
        result = runtime.reconcile()
        self.assertEqual(result['activated'],1)
        self.assertEqual(result['published'],1)
        self.assertEqual(self.repo.get(ready)['status'],'queued')
        self.assertEqual(len(self.repo.uploads()),100)

    def test_reconcile_publishes_all_due_pages_without_claims(self):
        for index in range(101): self.submit(key=f'pending-{index}')
        published = []
        runtime = Runtime(self.settings,repository=self.repo,backend=object(),publish=published.append)
        result = runtime.reconcile()
        self.assertEqual(result['due'],101)
        self.assertEqual(result['published'],101)
        self.assertEqual(len(set(published)),101)

    def test_partial_blob_is_not_processed_and_filename_conflicts(self):
        digest = hashlib.sha256(b'abc').hexdigest()
        job,_ = self.repo.submit('alice','upload',digest,digest,3,'input.wav',None,self.settings.backend_identity,initial_status='uploading')
        runtime = Runtime(self.settings,repository=self.repo,backend=object())
        runtime.storage.path(digest).write_bytes(b'ab')
        self.assertEqual(runtime.reconcile()['activated'],0)
        self.assertEqual(self.repo.get(job['id'])['status'],'uploading')
        with self.assertRaises(RepositoryConflict):
            self.repo.submit('alice','upload',digest,digest,3,'input.mp3',None,self.settings.backend_identity,initial_status='uploading')

    def test_invalid_backend_result_fails_without_retry(self):
        class Backend:
            def transcribe(self,*args,**kwargs): return {'text':'not canonical'}
        job,_ = self.submit()
        runtime = Runtime(self.settings,repository=self.repo,backend=Backend())
        self.assertEqual(runtime.run_once(job['id'])['status'],'failed')
        self.assertEqual(self.repo.get(job['id'])['error_code'],'invalid_result')

    def test_configuration_validation_and_secret_repr(self):
        secret = 'private-secret-value-123456789'
        settings = Settings(data_dir=Path(self.temp.name),api_keys={'alice':secret},asr_api_key=secret,broker_url='amqp://secret:secret@broker')
        self.assertNotIn(secret,repr(settings))
        self.assertNotIn('amqp://',repr(settings))
        for values in ({'max_attempts':True},{'api_keys':{'alice':'short'}},{'asr_url':'https://user:pass@example.com/asr','asr_model':'m'},{'asr_url':'https://example.com/asr'},{'asr_response_format':'text'},{'asr_url':'http://example.com/asr','asr_model':'m','allow_insecure_asr_http':True}):
            with self.assertRaises(ValueError): Settings(**values)
        Settings(asr_url='http://backend:8000/asr',asr_model='m',allow_insecure_asr_http=True)

    def test_idempotency_and_tenant_isolation(self):
        first, created = self.submit()
        second, duplicate = self.submit()
        self.assertTrue(created)
        self.assertFalse(duplicate)
        self.assertEqual(first['id'],second['id'])
        with self.assertRaises(RepositoryConflict): self.submit(sha='b'*64)
        other,_ = self.submit(client='bob')
        self.assertNotEqual(first['id'],other['id'])
        self.assertIsNone(self.repo.get(first['id'],'bob'))
    def test_restart_publish_loss_and_replay(self):
        job,_ = self.submit()
        restarted = Repository(self.settings.database_url)
        restarted.init()
        self.assertIn(job['id'],restarted.due())
        runtime = Runtime(self.settings,repository=restarted,backend=object(),publish=lambda _: (_ for _ in ()).throw(OSError()))
        self.assertFalse(runtime.enqueue(job['id']))
        self.assertIn(job['id'],restarted.due())
        claimed = restarted.claim(job['id'],60,3)
        self.assertIsNone(restarted.claim(job['id'],60,3))
        self.assertTrue(restarted.succeed(job['id'],claimed['lease_token'],{'text':'ok'}))
        self.assertIsNone(restarted.claim(job['id'],60,3))
    def test_lease_fencing_and_crash_are_uncertain(self):
        job,_ = self.submit()
        claimed = self.repo.claim(job['id'],60,3)
        self.assertFalse(self.repo.succeed(job['id'],'wrong',{}))
        with self.repo.engine.begin() as conn:
            conn.execute(update(jobs).where(jobs.c.id==job['id']).values(lease_until=time.time()-1))
        self.assertFalse(self.repo.renew(job['id'],claimed['lease_token'],60))
        self.assertFalse(self.repo.succeed(job['id'],claimed['lease_token'],{}))
        self.assertEqual(self.repo.due(),[])
        self.assertEqual(self.repo.get(job['id'])['status'],'uncertain')
        self.assertFalse(self.repo.retry(job['id'],'bob'))
        self.assertTrue(self.repo.retry(job['id'],'alice'))
        new = self.repo.claim(job['id'],60,3)
        self.assertNotEqual(new['lease_token'],claimed['lease_token'])
        self.assertFalse(self.repo.fail(job['id'],claimed['lease_token'],'late'))
    def test_ambiguous_backend_does_not_auto_retry(self):
        class Backend:
            calls = 0
            def transcribe(self,*args,**kwargs):
                self.calls += 1
                raise TimeoutError('secret URL and credentials must not persist')
        backend = Backend()
        job,_ = self.submit()
        runtime = Runtime(self.settings,repository=self.repo,backend=backend)
        self.assertEqual(runtime.run_once(job['id'])['status'],'uncertain')
        self.assertEqual(runtime.run_once(job['id'])['status'],'skipped')
        self.assertEqual(backend.calls,1)
        self.assertEqual(self.repo.get(job['id'])['error_code'],'backend_error')
    def test_retry_budget(self):
        job,_ = self.submit()
        for attempt in range(3):
            with self.repo.engine.begin() as conn:
                conn.execute(update(jobs).where(jobs.c.id==job['id']).values(available_at=0))
            claimed = self.repo.claim(job['id'],60,3)
            self.repo.fail(job['id'],claimed['lease_token'],'rate_limited',retryable=True,max_attempts=3)
        self.assertEqual(self.repo.get(job['id'])['status'],'failed')
    def test_storage_hash_validation_and_persistence(self):
        storage = Storage(self.settings.data_dir)
        source = Path(self.temp.name)/'input.wav'
        source.write_bytes(b'abc')
        digest = hashlib.sha256(b'abc').hexdigest()
        self.assertEqual(storage.put_file(source,digest),digest)
        self.assertEqual(Storage(self.settings.data_dir).path(digest).read_bytes(),b'abc')
        with self.assertRaises(ValueError): storage.put_file(source,'0'*64)
        with self.assertRaises(ValueError): storage.path('../escape')
    def test_concurrent_claim_has_one_winner(self):
        job,_ = self.submit()
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda _: self.repo.claim(job['id'],60,3),range(8)))
        self.assertEqual(sum(value is not None for value in claims),1)

    def test_heartbeat_keeps_long_backend_lease_alive(self):
        settings = Settings(data_dir=Path(self.temp.name),asr_url=self.settings.asr_url,asr_model='test-model',lease_seconds=1)
        repository = self.repo
        class Backend:
            def transcribe(self,*args,**kwargs):
                time.sleep(1.3)
                repository.due()
                return canonical('long operation')
        job,_ = self.submit()
        runtime = Runtime(settings,repository=self.repo,backend=Backend())
        self.assertEqual(runtime.run_once(job['id'])['status'],'succeeded')

    def test_success_heartbeat_and_duplicate_delivery(self):
        class Backend:
            calls = 0
            def transcribe(self,*args,**kwargs):
                self.calls += 1
                return canonical('hello')
        backend = Backend()
        job,_ = self.submit()
        runtime = Runtime(self.settings,repository=self.repo,backend=backend)
        self.assertEqual(runtime.run_once(job['id'])['status'],'succeeded')
        self.assertEqual(runtime.run_once(job['id'])['status'],'skipped')
        self.assertEqual(backend.calls,1)
        self.assertEqual(self.repo.get(job['id'])['result']['text'],'hello')

if __name__ == '__main__': unittest.main()
