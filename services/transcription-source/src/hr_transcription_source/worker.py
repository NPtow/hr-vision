"""Source-side HTTP coordinator. Core alone decides candidate/asset ownership."""
import hashlib
import json
import os
import stat
import time
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import quote, urlsplit

import httpx

from .repository import Repository, LostLease


class Rejected(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status


class SelectionConflict(Rejected):
    def __init__(self):
        super().__init__('primary_selection_conflict',409)


class Retryable(Exception):
    pass


def part(value):
    return quote(str(value), safe='')


class Runtime:
    def __init__(self, settings, *, repository=None, core=None, asr=None, download=None, storage=None, publish=None):
        self.settings = settings
        self.repository = repository or Repository(settings.data_dir / 'source.sqlite3')
        self.core = core or httpx.Client(base_url=settings.core_url.rstrip('/'), timeout=60, follow_redirects=False)
        self.asr = asr or httpx.Client(base_url=settings.asr_url.rstrip('/'), timeout=300, follow_redirects=False)
        self.download = download or httpx.Client(timeout=300, follow_redirects=False)
        if storage is None:
            from .storage import S3Storage
            storage = S3Storage(settings.s3_bucket, prefix=settings.s3_prefix,
                endpoint_url=settings.s3_endpoint, region_name=settings.s3_region)
        self.storage, self.publish = storage, publish
        self._publisher = None
        self.stage = settings.data_dir / 'staging'
        self.stage.mkdir(parents=True, exist_ok=True, mode=0o700)

    @staticmethod
    def _headers(token):
        return {'Authorization': 'Bearer ' + token}

    def _json(self, client, method, path, token, **kwargs):
        try:
            # Stream result bodies too: a malicious/unhealthy upstream must not
            # allocate an unbounded response before the configured limit check.
            expected_status=kwargs.pop('expected_status',200)
            headers={**self._headers(token),**kwargs.pop('headers',{})}
            with client.stream(method, path, headers=headers, **kwargs) as response:
                raw = bytearray()
                for block in response.iter_bytes():
                    raw.extend(block)
                    if len(raw) > self.settings.max_result_bytes:
                        raise Rejected('upstream_response_too_large', 502)
                status = response.status_code
            if status >= 500 or status in (408,429):
                raise Retryable('upstream_unavailable')
            if status >= 400:
                raise Rejected('upstream_rejected_' + str(status), status)
            if status >= 300:
                raise Rejected('upstream_redirect_forbidden', 502)
            if status != expected_status:
                raise Rejected('unexpected_upstream_status',502)
            return json.loads(raw), bytes(raw)
        except (httpx.HTTPError, OSError, json.JSONDecodeError):
            raise Retryable('upstream_unavailable') from None

    def authorize(self, caller_name, event):
        caller = self.settings.callers[caller_name]
        path = '/v1/bindings/' + part(event['binding_id'])
        binding, _ = self._json(self.core, 'GET', path, caller.core_read_token)
        if (binding.get('owner_kind'), binding.get('owner_id'), binding.get('generation')) != (event['owner_kind'],event['owner_id'],event['generation']) or binding.get('revoked'):
            raise Rejected('binding_owner_generation_mismatch')
        pair = binding.get('provider'), binding.get('connection_id')
        write_token = self.settings.source_tokens.get(pair)
        if write_token is None:
            raise Rejected('source_credential_unconfigured', 503)
        # Source-role binding GET exercises Core's active/room/consent checks;
        # portal GET alone intentionally permits reading historical metadata.
        active, _ = self._json(self.core, 'GET', path, write_token)
        if active != binding:
            raise Rejected('binding_changed')
        materials, _ = self._json(self.core, 'GET', '/v1/owners/'+part(event['owner_kind'])+'/'+part(event['owner_id'])+'/materials', caller.core_read_token)
        if (materials.get('owner_kind'),materials.get('owner_id')) != (event['owner_kind'],event['owner_id']):
            raise Rejected('owner_response_mismatch')
        matching = [a for a in materials.get('assets',[]) if a.get('id') == event['asset_id']]
        if len(matching) != 1 or matching[0].get('source_id') != event['binding_id'] or matching[0].get('kind') not in ('video','audio') or matching[0].get('state') != 'ready':
            raise Rejected('recording_not_owned_by_binding')
        # Verify the generation again from the materials view, closing the gap
        # between the two HTTP reads. Core repeats it transactionally on receipt.
        sources = [s for s in materials.get('sources',[]) if s.get('id') == event['binding_id']]
        if len(sources)!=1 or sources[0].get('generation') != event['generation'] or sources[0].get('revoked'):
            raise Rejected('binding_changed')
        return binding, matching[0], materials, write_token

    def submit(self, caller, key, event):
        self.authorize(caller,event)
        job, created = self.repository.submit(caller,key,event,self.settings.config_identity,
            select_primary=self.settings.select_primary)
        self.enqueue(job['id'])
        return job,created

    def enqueue(self, identity):
        try:
            if self.publish:
                self.publish(identity)
            elif self.settings.broker_url:
                from .queue import build_celery
                if self._publisher is None:
                    self._publisher=build_celery(self.settings)
                self._publisher.send_task('hr_transcription_source.run', args=[identity], queue='hr_transcription_source', retry=False)
        except Exception:
            # SQL was already committed; scheduler reconciles lost delivery.
            pass

    def reconcile(self):
        due = self.repository.due()
        for identity in due:
            self.enqueue(identity)
        return {'pending':len(due)}

    def _clean_staging(self):
        cutoff=time.time()-2*self.settings.lease_seconds
        for path in self.stage.iterdir():
            if not path.name.startswith(('recording-','raw-')):
                continue
            try:
                info=path.lstat()
                if stat.S_ISREG(info.st_mode) and info.st_mtime<cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                pass

    def _input(self, job, binding, asset, token):
        caller = self.settings.callers[job['caller']]
        access, _ = self._json(self.core,'GET','/v1/assets/'+part(asset['id'])+'/access',caller.core_read_token)
        if access.get('id') != asset['id']:
            raise Rejected('asset_access_mismatch')
        if access.get('storage') == 'remote':
            resolver = self.settings.remote_resolvers.get((binding['provider'],binding['connection_id']))
            if resolver is None:
                raise Rejected('remote_source_resolver_unconfigured', 422)
            # Explicit operator-injected adapter accepts Core attested identities,
            # never a requester URL. It returns a context manager of byte stream.
            manager = resolver.open_recording(binding, asset, access)
            expected = None
        elif access.get('storage') == 'owned':
            locations = [l for l in asset.get('locations',[]) if l.get('backend')=='s3' and l.get('state')=='verified']
            if not locations or not locations[-1].get('sha256') or type(locations[-1].get('size')) is not int:
                raise Rejected('verified_owned_location_required')
            expected = locations[-1]
            url = access.get('url','')
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or parsed.hostname not in self.settings.download_hosts or parsed.username or parsed.password or parsed.fragment or parsed.port not in (None,443):
                raise Rejected('core_download_host_not_allowed', 502)
            manager = self.download.stream('GET',url)
        else:
            raise Rejected('unsupported_asset_storage')
        if expected and expected['size'] > self.settings.max_input_bytes:
            raise Rejected('recording_too_large',413)
        required = expected['size'] if expected else self.settings.max_input_bytes
        if shutil.disk_usage(self.stage).free < required + 64*1024**2:
            raise Retryable('staging_disk_unavailable')
        fd,name = tempfile.mkstemp(prefix='recording-',dir=self.stage)
        size,digest = 0,hashlib.sha256()
        try:
            with os.fdopen(fd,'wb') as target, manager as response:
                if expected:
                    if response.status_code != 200:
                        raise Retryable('recording_download_unavailable')
                    chunks = response.iter_bytes(1024*1024)
                else:
                    chunks = iter(lambda:response.read(1024*1024),b'')
                for block in chunks:
                    size += len(block)
                    if size > self.settings.max_input_bytes:
                        raise Rejected('recording_too_large',413)
                    target.write(block)
                    digest.update(block)
                    self.repository.update(job['id'],token,lease_until=self.repository.clock()+self.settings.lease_seconds)
                target.flush()
                os.fsync(target.fileno())
            sha = digest.hexdigest()
            if not size or expected and (size != expected['size'] or sha != expected['sha256']):
                raise Rejected('recording_integrity_failed')
            return Path(name),sha
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise

    def _asr_submit(self, job, binding, asset, token):
        path,sha = self._input(job,binding,asset,token)
        # The SHA and deterministic ASR idempotency key survive a lost POST ack.
        try:
            self.repository.update(job['id'],token,input_sha256=sha)
            extension = str(asset.get('metadata',{}).get('format','')).lower().lstrip('.')
            if extension not in ('mp4','m4a','mp3','wav','webm','ogg','flac','mpeg','mpga'):
                extension = 'mp4' if asset['kind']=='video' else 'm4a'
            with path.open('rb') as stream:
                data,_ = self._json(self.asr,'POST','/v1/jobs?language=ru',self.settings.asr_token,
                    content=stream,expected_status=202,headers={'Idempotency-Key':'source:'+job['stable_key'],
                        'X-Filename':'input.'+extension,'Content-Type':'application/octet-stream'})
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',data.get('id','')) or data.get('input_sha256') != sha:
                raise Rejected('asr_job_proof_mismatch')
            return self.repository.update(job['id'],token,asr_job_id=data['id'],transcription_status='running')
        finally:
            path.unlink(missing_ok=True)

    def _fetch_result(self, job, token):
        identity, auth = job['asr_job_id'], self.settings.asr_token
        status,_ = self._json(self.asr,'GET','/v1/jobs/'+part(identity),auth)
        if status.get('id') != identity or status.get('input_sha256') != job['input_sha256']:
            raise Rejected('asr_job_proof_mismatch')
        if status.get('status') == 'failed':
            return self.repository.update(job['id'],token,transcription_status='failed',error_code='asr_failed')
        if status.get('status') == 'uncertain':
            return self.repository.update(job['id'],token,transcription_status='uncertain',error_code='asr_submit_uncertain')
        if status.get('status') != 'succeeded':
            return self.repository.update(job['id'],token,next_at=self.repository.clock()+self.settings.retry_seconds,lease_token=None,lease_until=None)
        result,_ = self._json(self.asr,'GET','/v1/jobs/'+part(identity)+'/result',auth)
        raw_path = '/v1/jobs/'+part(identity)+'/result/raw'
        if result.get('job_id') != identity or result.get('input_sha256') != job['input_sha256'] or result.get('provider') != self.settings.asr_provider or result.get('raw_result_url') != raw_path:
            raise Rejected('asr_result_proof_mismatch')
        envelope, raw = self._json(self.asr,'GET',raw_path,auth)
        if envelope.get('schema_version') != 1 or envelope.get('job_id') != identity or envelope.get('input_sha256') != job['input_sha256'] or envelope.get('provider')!=self.settings.asr_provider or envelope.get('model') != result.get('model') or not isinstance(envelope.get('events'),list):
            raise Rejected('asr_raw_proof_mismatch')
        self._validate_transcript(result.get('transcript'))
        return self.repository.update(job['id'],token,result=result,raw=raw,transcription_status='succeeded')

    @staticmethod
    def _validate_transcript(value):
        # Core independently validates the complete strict wire model. Check
        # text/segments before persisting provider output or uploading to S3.
        if not isinstance(value,dict) or value.get('schema_version')!=1 or not isinstance(value.get('segments'),list) or not value['segments']:
            raise Rejected('invalid_normalized_transcript')
        try:
            text='\n'.join(s['text'] for s in value['segments'])
            if not text.strip() or value.get('text')!=text or value.get('full_text')!=text:
                raise ValueError()
        except (KeyError,TypeError,ValueError):
            raise Rejected('invalid_normalized_transcript') from None

    def _store_raw(self, job, token):
        raw=job['raw']
        fd,name=tempfile.mkstemp(prefix='raw-',dir=self.stage)
        try:
            with os.fdopen(fd,'wb') as target:
                target.write(raw)
                target.flush()
                os.fsync(target.fileno())
            obj=self.storage.put_file(name,expected_sha256=hashlib.sha256(raw).hexdigest(),expected_size=len(raw),content_type='application/json')
            ref={'backend':'s3','object_key':obj.key,'sha256':obj.sha256,'size':obj.size,'content_type':'application/json'}
            return self.repository.update(job['id'],token,raw_ref=ref,delivery_status='delivering')
        finally:
            Path(name).unlink(missing_ok=True)

    def _deliver(self,job,token):
        binding,asset,materials,write_token=self.authorize(job['caller'],job['event'])
        if not job['receipt']:
            channels=[c for c in materials['channels'] if c['binding_id']==binding['id'] and c['generation']==binding['generation']]
            if len(channels)!=1:
                raise Rejected('binding_channel_missing')
            channel=channels[0]
            if not channel['acquisition_complete'] or asset['kind']=='video' and channel['video_status']!='ready':
                raise Retryable('recording_acquisition_not_complete')
            result=job['result']
            external='asr:'+self.settings.asr_provider+':'+job['event']['asset_id']+':'+job['asr_job_id']+':'+job['revision']
            receipt={'schema_version':1,'generation':binding['generation'],'receipt_id':'asr:'+job['stable_key'],
                'assets':[{'external_id':external,'kind':'transcript','state':'ready','locations':[],
                    'transcript':{'raw_ref':job['raw_ref'],'normalized':result['transcript'],
                    'provenance':{'origin':self.settings.asr_provider,'model_version':result.get('model'),
                        'language':result.get('language'),'processing_status':'completed','coverage':'not_independently_verified',
                        'input_asset_id':job['event']['asset_id'],'input_sha256':job['input_sha256']}}}],
                'video_status':channel['video_status'],'acquisition_complete':channel['acquisition_complete'],
                'transcript_status':'ready','transcription_status':'succeeded'}
            job=self.repository.update(job['id'],token,receipt=receipt)
        ack,_=self._json(self.core,'POST','/v1/bindings/'+part(binding['id'])+'/receipts',write_token,json=job['receipt'])
        if ack.get('binding_id')!=binding['id'] or ack.get('generation')!=binding['generation'] or ack.get('receipt_id')!=job['receipt']['receipt_id'] or not ack.get('transcript_ids'):
            raise Rejected('core_receipt_proof_mismatch')
        # Read back with the same Core portal principal: presence under the
        # candidate's owner plus exact raw hash/provenance, not HTTP 200 alone.
        _,_,readback,_=self.authorize(job['caller'],job['event'])
        matching={t['id']:t for t in readback['transcripts']}
        ack_assets=set(ack.get('asset_ids',[]))
        for identity in ack['transcript_ids']:
            t=matching.get(identity)
            if not t or t.get('asset_id') not in ack_assets or t.get('raw_ref')!=job['raw_ref'] or t.get('metadata',{}).get('origin')!=self.settings.asr_provider:
                raise Retryable('core_readback_pending')
        return self.repository.update(job['id'],token,transcript_ids=ack['transcript_ids'],delivery_status='succeeded',error_code=None)

    def _selection_path(self,job):
        event=job['event']
        return '/v1/bindings/'+part(event['binding_id'])+'/recordings/'+part(event['asset_id'])+'/transcript-selection'

    def _selection_put(self,job,status,write_token,*,transcript_id=None,error_code=None):
        body={'generation':job['event']['generation'],'request_id':'source:'+job['id'],
            'expected_revision':job['selection_revision'],'status':status,
            'transcript_id':transcript_id,'error_code':error_code}
        try:
            result,_=self._json(self.core,'PUT',self._selection_path(job),write_token,json=body)
        except Rejected as error:
            if error.status==409:
                raise SelectionConflict() from None
            raise
        expected={'binding_id':job['event']['binding_id'],'generation':job['event']['generation'],
            'recording_id':job['event']['asset_id'],'request_id':body['request_id'],
            'revision':job['selection_revision']+1}
        if any(result.get(k)!=v for k,v in expected.items()):
            raise Rejected('primary_selection_proof_mismatch')
        if status!='pending' and any(result.get(k)!=body[k] for k in ('status','transcript_id','error_code')):
            raise Rejected('primary_selection_proof_mismatch')
        if result.get('status') not in ('pending','ready','failed','uncertain'):
            raise Rejected('primary_selection_proof_mismatch')
        return result

    def _selection_pending(self,job,token,materials,write_token):
        if job['selection_revision'] is None:
            def matching(rows):
                return [s for s in rows if s.get('binding_id')==job['event']['binding_id'] and
                    s.get('generation')==job['event']['generation'] and s.get('recording_id')==job['event']['asset_id']]
            # Invalid selected content stays hidden, but Core exposes a safe CAS
            # marker so a new request can replace it without guessing revision0.
            current=matching(materials.get('transcript_selection_revisions',[]))
            if not current:
                current=matching(materials.get('transcript_selections',[]))
            if len(current)>1:
                raise Rejected('ambiguous_primary_selection')
            revision=current[0]['revision'] if current else 0
            if type(revision) is not int or revision<0:
                raise Rejected('invalid_primary_selection_revision')
            # This base is committed BEFORE the first network PUT. Never replace
            # it on a retry, even when another request has advanced Core's CAS.
            job=self.repository.update(job['id'],token,selection_revision=revision)
        ack=self._selection_put(job,'pending',write_token)
        if ack['status']!='pending':
            outcome=job['transcription_status']
            expected=job['selection_outcome'] or ('ready' if outcome=='succeeded' else outcome)
            if ack['status']!=expected:
                raise SelectionConflict()
        return self.repository.update(job['id'],token,selection_status='pending')

    def _selection_finish(self,job,token,status):
        if job['selection_revision'] is None:
            # An API opt-in can arrive while an older worker already holds the
            # job. Register its durable CAS intent before any terminal PUT.
            _,_,materials,write_token=self.authorize(job['caller'],job['event'])
            job=self._selection_pending(job,token,materials,write_token)
        if job['selection_outcome'] is None:
            job=self.repository.update(job['id'],token,selection_outcome=status)
        elif job['selection_outcome']!=status:
            raise SelectionConflict()
        _,_,_,write_token=self.authorize(job['caller'],job['event'])
        transcript_id=None
        error_code=None
        if status=='ready':
            if not job['transcript_ids'] or len(job['transcript_ids'])!=1:
                raise Rejected('ambiguous_receipt_transcript')
            transcript_id=job['transcript_ids'][0]
        else:
            error_code='asr_failed' if status=='failed' else 'asr_submit_uncertain'
        ack=self._selection_put(job,status,write_token,transcript_id=transcript_id,error_code=error_code)
        caller=self.settings.callers[job['caller']]
        event=job['event']
        primary,_=self._json(self.core,'GET','/v1/owners/'+part(event['owner_kind'])+'/'+part(event['owner_id'])+'/primary-transcripts',caller.core_read_token)
        if (primary.get('owner_kind'),primary.get('owner_id'))!=(event['owner_kind'],event['owner_id']):
            raise Rejected('primary_selection_owner_mismatch')
        keys=('binding_id','generation','recording_id','request_id','revision','status','transcript_id','error_code')
        selections=[row for row in primary.get('selections',[]) if all(row.get(k)==ack.get(k) for k in keys)]
        if len(selections)!=1 or status=='ready' and transcript_id not in {t.get('id') for t in primary.get('transcripts',[])}:
            raise Retryable('primary_selection_readback_pending')
        return self.repository.update(job['id'],token,selection_status=status,error_code=error_code)

    def _reject_job(self,identity,token,error):
        job=self.repository.get(identity)
        if job['selection_requested'] and job['selection_revision'] is not None and not isinstance(error,SelectionConflict):
            # A definitive input/backend/delivery rejection must not strand a
            # registered primary request in pending. Preserve successful paid
            # ASR facts; only the selection is failed when delivery is rejected.
            outcome='uncertain' if job['transcription_status']=='uncertain' else 'failed'
            changes={'delivery_status':'rejected','error_code':error.code}
            if job['transcription_status'] not in ('succeeded','uncertain'):
                changes['transcription_status']='failed'
            job=self.repository.update(identity,token,**changes)
            try:
                job=self._selection_finish(job,token,outcome)
                return self.repository.update(identity,token,lease_token=None,lease_until=None)
            except LostLease:
                raise
            except Rejected as selection_error:
                error=selection_error
            except Exception:
                # Keep the terminal ASR checkpoint and retry only the Core
                # outcome publication, including after a process restart.
                return self.repository.update(identity,token,error_code='retryable_selection_failure',
                    next_at=self.repository.clock()+self.settings.retry_seconds,lease_token=None,lease_until=None)
        changes={'delivery_status':'rejected','error_code':error.code,'lease_token':None,'lease_until':None}
        if job['selection_requested']:
            changes['selection_status']='conflict' if isinstance(error,SelectionConflict) else 'rejected'
        return self.repository.update(identity,token,**changes)

    def run_once(self, identity=None):
        if identity is None:
            due=self.repository.due()
            if not due:
                self.repository.claim('idle',self.settings.lease_seconds)
                return {'claimed':False}
            identity=due[0]
        job,token=self.repository.claim(identity,self.settings.lease_seconds)
        if not job:
            return {'claimed':False}
        try:
            if job['revision']!=self.settings.config_identity:
                raise Rejected('configuration_revision_changed')
            binding,asset,materials,write_token=self.authorize(job['caller'],job['event'])
            if job['selection_requested']:
                job=self._selection_pending(job,token,materials,write_token)
            if job['selection_requested'] and job['selection_outcome'] in ('failed','uncertain'):
                job=self._selection_finish(job,token,job['selection_outcome'])
                job=self.repository.update(identity,token,delivery_status='rejected',lease_token=None,lease_until=None)
                return self.public(job)
            if job['transcription_status'] not in ('failed','uncertain'):
                if not job['asr_job_id']:
                    channels=[c for c in materials['channels'] if c['binding_id']==binding['id'] and c['generation']==binding['generation']]
                    if len(channels)!=1 or not channels[0]['acquisition_complete'] or asset['kind']=='video' and channels[0]['video_status']!='ready':
                        raise Retryable('recording_acquisition_not_complete')
                    job=self._asr_submit(job,binding,asset,token)
                if job['result'] is None:
                    job=self._fetch_result(job,token)
                    if job['result'] is None and job['transcription_status'] not in ('failed','uncertain'):
                        return self.public(job)
            if job['transcription_status'] in ('failed','uncertain'):
                if job['selection_requested']:
                    job=self._selection_finish(job,token,job['transcription_status'])
                job=self.repository.update(identity,token,delivery_status='rejected',lease_token=None,lease_until=None)
                return self.public(job)
            if job['raw_ref'] is None:
                job=self._store_raw(job,token)
            job=self._deliver(job,token)
            if job['selection_requested']:
                job=self._selection_finish(job,token,'ready')
            job=self.repository.update(identity,token,lease_token=None,lease_until=None)
        except LostLease:
            return {'claimed':True,'lease_lost':True}
        except Rejected as error:
            try:
                job=self._reject_job(identity,token,error)
            except LostLease:
                return {'claimed':True,'lease_lost':True}
        except Exception:
            # Delivery and selection retries reuse persisted ASR/result/receipt.
            # Never log dependency exceptions (credentials, URLs or transcript).
            try:
                job=self.repository.update(identity,token,error_code='retryable_dependency_failure',next_at=self.repository.clock()+self.settings.retry_seconds,lease_token=None,lease_until=None)
            except LostLease:
                return {'claimed':True,'lease_lost':True}
        return self.public(job)

    @staticmethod
    def public(job):
        result={k:job[k] for k in ('id','transcription_status','delivery_status','asr_job_id','transcript_ids','error_code','created_at','updated_at','selection_requested','selection_revision','selection_status')}
        result['selection_requested']=bool(result['selection_requested'])
        return result
