"""At-least-once delivery with conservative uncertain-outcome handling."""
import json
import threading
from .repository import Repository
from .storage import Storage
from .contracts import validate_normalized

class Runtime:
    def __init__(self, settings, repository=None, backend=None, publish=None):
        self.settings = settings
        self.repository = repository or Repository(settings.database_url)
        self.repository.init()
        self.storage = Storage(settings.data_dir)
        if backend is None:
            from .backends import HttpAsrBackend
            backend = HttpAsrBackend(settings)
        self.backend = backend
        self.publish = publish

    def enqueue(self, job_id):
        try:
            if self.publish is not None: self.publish(job_id)
            elif self.settings.broker_url:
                from .queue import build_celery
                build_celery(self.settings).send_task('md_transcription.run', args=[job_id])
            else: return False
            return True
        except Exception:
            return False

    def reconcile(self):
        activated = published = due_count = 0
        cursor = None
        while batch := self.repository.uploads(after_id=cursor):
            for job in batch:
                if self.storage.verify(job['input_key'],job['input_size'],job['input_sha256']):
                    activated += bool(self.repository.activate(job['id'],job['client_id']))
            # Keyset pagination survives activation removing rows from this result set.
            cursor = batch[-1]['id']
        cursor = None
        while ids := self.repository.due(after_id=cursor):
            due_count += len(ids)
            published += sum(bool(self.enqueue(job_id)) for job_id in ids)
            cursor = ids[-1]
        return {'due':due_count, 'published':published, 'activated':activated}

    def run_once(self, job_id=None):
        if job_id is None:
            due = self.repository.due(limit=1)
            if not due: return {'status':'idle'}
            job_id = due[0]
        job = self.repository.claim(job_id,self.settings.lease_seconds,self.settings.max_attempts)
        if job is None: return {'job_id':job_id, 'status':'skipped'}
        token = job['lease_token']
        stop = threading.Event()
        def heartbeat():
            while not stop.wait(max(.05,self.settings.lease_seconds/3)):
                try:
                    if not self.repository.renew(job_id,token,self.settings.lease_seconds): return
                except Exception: return
        thread = threading.Thread(target=heartbeat,daemon=True)
        thread.start()
        try:
            if job['backend_identity'] != self.settings.backend_identity:
                self.repository.fail(job_id,token,'backend_configuration_changed')
            else:
                result = self.backend.transcribe(self.storage.path(job['input_key']), filename=job['filename'], language=job['language'], request_id=job_id)
                try:
                    result = validate_normalized(result)
                except (ValueError, TypeError):
                    self.repository.fail(job_id,token,'invalid_result')
                    return {'job_id':job_id,'status':self.repository.get(job_id)['status']}
                if len(json.dumps(result,allow_nan=False).encode())>self.settings.max_result_bytes:
                    self.repository.fail(job_id,token,'result_too_large')
                else: self.repository.succeed(job_id,token,result)
        except Exception as exc:
            # Unknown exceptions after entering the backend may conceal a completed request.
            self.repository.fail(job_id,token,getattr(exc,'code','backend_error'),retryable=getattr(exc,'retryable',False),uncertain=getattr(exc,'uncertain',True),max_attempts=self.settings.max_attempts)
        finally:
            stop.set()
            thread.join(timeout=1)
        current = self.repository.get(job_id)
        return {'job_id':job_id,'status':current['status']}
