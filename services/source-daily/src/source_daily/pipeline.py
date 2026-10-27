"""Source-owned orchestration. Provider discovery, safe fetch, ASR HTTP, core receipts."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
import uuid
import math

from ._vendor.materials_capture import Artifact
from ._vendor.materials_http import DownloadError, download, validate_url
from ._vendor.materials_storage import StorageError
from ._vendor.support import Problem
from .errors import LeaseLost, ServiceError
from .store import encode
from botocore.exceptions import BotoCoreError, ClientError


def safe_id(value):
    return (isinstance(value,str) and 0 < len(value) <= 200 and
            not any(ord(c) < 32 for c in value) and '://' not in value and '?' not in value and '#' not in value)


def clean_artifact(artifact):
    """Explicit URL-free metadata projection before any checkpoint/evidence write."""
    source = artifact.source
    identifiers = (source.provider,source.connection_id,source.room_id,source.room_name,source.session_id,
                   artifact.provider_asset_id)
    if not all(safe_id(v) for v in identifiers) or source.capture_id is not None and not safe_id(source.capture_id):
        raise ServiceError('invalid_provider_artifact_identity')
    if artifact.kind not in ('video','audio','transcript') or artifact.status not in ('ready','processing','failed','missing','blocked'):
        raise ServiceError('invalid_provider_artifact')
    metadata = {}
    for field in ('duration','start_ts'):
        value = artifact.metadata.get(field)
        if type(value) in (int,float) and math.isfinite(value) and value>=0:
            metadata[field] = value
    return Artifact(source,artifact.provider_asset_id,artifact.kind,artifact.status,metadata)


def checked_binding(value, binding_id, generation, connection_id):
    if (not isinstance(value,dict) or value.get('binding_id') != binding_id or
            type(value.get('generation')) is not int or value['generation'] != generation or
            value.get('provider') != 'daily' or value.get('connection_id') != connection_id or
            value.get('revoked') or value.get('active') is False):
        raise ServiceError('binding_fenced',409)
    policy = value.get('storage_policy')
    ref = value.get('source_ref')
    if policy not in ('remote','owned') or not isinstance(ref,dict):
        raise ServiceError('invalid_core_binding',502)
    allowed = {'capture_instance_id','capture_id','room_id','room_name','session_id','exclusive_room_owner','recording_instance_id'}
    if set(ref) - allowed or any(not safe_id(v) for k,v in ref.items() if k != 'exclusive_room_owner'):
        raise ServiceError('invalid_core_source_ref',502)
    if not all(safe_id(ref.get(key)) for key in ('room_id','room_name')):
        raise ServiceError('invalid_core_source_ref',502)
    proof = ref.get('exclusive_room_owner')
    if proof is not None and (not isinstance(proof,dict) or set(proof) != {'kind','id'} or
            proof.get('kind') not in ('interview','meeting') or not safe_id(proof.get('id'))):
        raise ServiceError('invalid_core_source_ref',502)
    return {**value, 'source_ref': {**ref,'provider':'daily','connection_id':connection_id}}


def location(stored):
    result = stored.as_dict()
    if result.get('backend') != 's3':
        raise ServiceError('owned_s3_required',503,retryable=True)
    return {**result,'state':'verified'}


def canonical(value):
    """Validate the HTTP normalizer boundary, without implementing normalization."""
    if not isinstance(value,dict) or value.get('schema_version') != 1 or not isinstance(value.get('segments'),list):
        raise ServiceError('invalid_normalized_transcript')
    rows = value['segments']
    if not 1 <= len(rows) <= 100000:
        raise ServiceError('invalid_normalized_transcript')
    allowed = {'schema_version','format','segments','full_text','text','vtt','warnings'}
    if set(value) - allowed or value.get('format') not in ('vtt','srt','json','canonical_json'):
        raise ServiceError('invalid_normalized_transcript')
    for row in rows:
        if not isinstance(row,dict) or set(row) != {'start_ms','end_ms','text','speaker'} or not isinstance(row['text'],str) or not row['text'].strip():
            raise ServiceError('invalid_normalized_transcript')
        start,end,speaker = row['start_ms'],row['end_ms'],row['speaker']
        if ((start is None) != (end is None) or start is not None and
                (type(start) is not int or type(end) is not int or not 0 <= start <= end) or
                speaker is not None and (not isinstance(speaker,str) or len(speaker)>500)):
            raise ServiceError('invalid_normalized_transcript')
    full = '\n'.join(row['text'] for row in rows)
    if (value.get('text') != full or value.get('full_text') != full or not isinstance(value.get('vtt'),str) or
            not isinstance(value.get('warnings'),list) or len(value['warnings'])>100 or
            any(not isinstance(item,str) or len(item)>500 for item in value['warnings'])):
        raise ServiceError('invalid_normalized_transcript')
    return value


class Pipeline:
    def __init__(self, settings, store, core, capture, transcription, storage, *, downloader=download):
        self.settings,self.store,self.core,self.capture = settings,store,core,capture
        self.transcription,self.storage,self.downloader = transcription,storage,downloader

    def binding(self,binding_id,generation):
        return checked_binding(self.core.binding(binding_id),binding_id,generation,self.settings.connection_id)

    def enqueue(self,binding_id,generation):
        binding = self.binding(binding_id,generation)
        source_ref = {k:v for k,v in binding['source_ref'].items() if k!='exclusive_room_owner'}
        job = self.store.create(binding_id,generation,source_ref)
        if job['status']=='succeeded' and job['checkpoint'].get('transcription_status')=='awaiting_configuration':
            pending_text = any(a.get('kind')=='transcript' for a in job['checkpoint'].get('discovered',[]))
            if self.transcription.configured and (self.settings.enable_asr or pending_text):
                return self.resume(job['id'])
        return job

    def resume(self,job_id):
        job = self.store.get(job_id)
        if job is None:
            raise ServiceError('job_not_found',404)
        self.binding(job['binding_id'],job['generation'])
        return self.store.resume(job_id)

    def _tick(self,job,token):
        self.store.renew(job['id'],token,self.settings.lease_seconds)

    def _save(self,job,token,cp):
        self.store.save(job['id'],token,cp)

    def _deliver(self,job,token):
        for body in self.store.pending(job['id']):
            self._tick(job,token)
            # Refetch authorization before sending an immutable, durable receipt.
            self.binding(job['binding_id'],job['generation'])
            self.core.receipt(job['binding_id'],body)
            self.store.delivered(job['id'],token,body['receipt_id'])

    def _receipt(self,job,token,cp):
        assets = [value for _,value in sorted(cp.get('assets',{}).items())]
        base = {'schema_version':1,'generation':job['generation'],
                'video_status':cp.get('video_status','processing'),
                'transcript_status':cp.get('transcript_status','processing'),
                'acquisition_complete':cp.get('acquisition_complete',False),
                'transcription_status':cp.get('transcription_status','not_requested')}
        # Stable sorting is required because JSON checkpoints sort mapping keys.
        # ASR can add one transcript per video; core accepts 100 assets per receipt.
        chunks = [assets[i:i+100] for i in range(0,len(assets),100)] or [[]]
        for chunk in chunks:
            body = {**base,'assets':chunk}
            digest = hashlib.sha256(encode([job['binding_id'],body]).encode()).hexdigest()
            body['receipt_id'] = 'source-' + digest
            if len(encode(body).encode()) > 24*1024*1024:
                raise ServiceError('core_receipt_size_limit')
            self.store.receipt(job['id'],token,body)
        self._deliver(job,token)

    def _raw(self,path,info):
        if self.storage is None:
            raise ServiceError('owned_s3_not_configured',503,retryable=True)
        try:
            return location(self.storage.put_file(path, expected_sha256=info.sha256,
                            expected_size=info.size,content_type=info.content_type))
        except ClientError as error:
            denied = error.response.get('Error',{}).get('Code') in ('AccessDenied','InvalidAccessKeyId','SignatureDoesNotMatch','NoSuchBucket')
            raise ServiceError('storage_access_denied' if denied else 'storage_request_failed',retryable=not denied) from None
        except (StorageError,BotoCoreError,OSError):
            raise ServiceError('storage_unavailable',retryable=True) from None

    def _read_raw(self,key):
        try:
            return self.storage.read_bytes(key,max_bytes=self.settings.max_transcript_bytes)
        except (StorageError,BotoCoreError,ClientError,OSError):
            raise ServiceError('storage_read_failed',retryable=True) from None

    def _fetch(self,artifact,path):
        try:
            source = self.capture.resolve(artifact)
            return self.downloader(source,path,max_bytes=self.settings.max_transcript_bytes if
                    artifact.kind=='transcript' else self.settings.max_video_bytes,timeout_seconds=120)
        except Problem as error:
            status = getattr(error,'provider_status',error.status)
            raise ServiceError('daily_access_' + str(status),502,retryable=status in (401,403,408,429) or status>=500) from None
        except DownloadError as error:
            # Distinguish denied recording from a generic ASR error, never retain URL.
            code = ('daily_download_' + str(error.remote_status)) if error.remote_status else 'daily_download_' + error.code
            raise ServiceError(code,502,retryable=error.retryable) from None

    def _acquire(self,job,token,cp,artifact,policy):
        key = artifact.asset_key
        if cp['assets'].get(key,{}).get('state') == 'ready':
            return
        self._tick(job,token)
        asset = {'external_id':artifact.provider_asset_id,'kind':artifact.kind,'state':'ready',
                 'metadata':{k:v for k,v in artifact.metadata.items() if k=='duration'},
                 'locations':[]}
        asset['metadata']['session_id'] = artifact.source.session_id
        with tempfile.TemporaryDirectory(prefix='source-',dir=self.settings.temp_root) as directory:
            suffix = '.vtt' if artifact.kind=='transcript' else '.mp4'
            path = Path(directory)/('input'+suffix)
            if artifact.kind=='transcript' or policy=='owned':
                owned = cp.setdefault('acquired',{}).get(key)
                if owned is None:
                    info = self._fetch(artifact,path)
                    self._tick(job,token)
                    owned = self._raw(path,info)
                    cp['acquired'][key] = owned
                    self._save(job,token,cp)
                asset['locations'].append(owned)
                if artifact.kind=='transcript':
                    # Raw object remains immutable even when normalization is unavailable.
                    if not path.exists():
                        path.write_bytes(self._read_raw(owned['object_key']))
                    normalized = canonical(self.transcription.normalize(path.read_bytes(),'vtt'))
                    asset['transcript'] = {'raw_ref':{k:v for k,v in owned.items() if k!='state'},
                        'normalized':normalized,'provenance':{'origin':'daily_provider',
                        'processing_status':'completed','coverage':'not_independently_verified'}}
            else:
                # A finished metadata row is insufficient to promise usable remote access.
                try:
                    source = self.capture.resolve(artifact)
                    validate_url(source.url)
                except Problem as error:
                    raise ServiceError('daily_access_'+str(getattr(error,'provider_status',error.status)),retryable=True) from None
                except (ValueError,DownloadError):
                    raise ServiceError('daily_access_invalid',retryable=True) from None
            asset['locations'].append({'backend':'provider','object_key':artifact.provider_asset_id,'state':'available'})
        cp['assets'][key] = asset
        cp.get('asset_errors',{}).pop(key,None)
        # Exact source evidence is durable before access endpoint can resolve it.
        self.store.save_artifact(job['id'],token,job['binding_id'],artifact.provider_asset_id,asdict(artifact))
        self._save(job,token,cp)

    def _asr(self,job,token,cp,artifacts):
        covered_sessions = {a.get('metadata',{}).get('session_id') for a in cp['assets'].values()
            if a['kind']=='transcript' and a['state']=='ready' and
            a.get('transcript',{}).get('provenance',{}).get('origin')=='daily_provider'}
        videos = [a for a in artifacts if a.kind in ('video','audio') and a.status=='ready' and
                  a.source.session_id not in covered_sessions and
                  cp['assets'].get(a.asset_key,{}).get('state')=='ready']
        if not videos:
            cp['transcription_status'] = 'not_requested'
            return True
        if not self.settings.enable_asr or not self.transcription.configured or self.storage is None:
            cp['transcription_status'] = 'awaiting_configuration'
            return True
        # One job per media part; no model/engine selection in source code.
        pending = False
        asr = cp.setdefault('asr',{})
        for artifact in videos:
            self._tick(job,token)
            entry = asr.setdefault(artifact.asset_key,{'polls':0})
            if entry.get('status') in ('succeeded','failed'):
                continue
            if not entry.get('id') or entry.get('status')=='uploading':
                with tempfile.TemporaryDirectory(prefix='asr-',dir=self.settings.temp_root) as directory:
                    path = Path(directory)/'input.mp4'
                    info = self._fetch(artifact,path)  # Source owns input even with remote retention.
                    stable_key = 'source-' + hashlib.sha256(encode([job['binding_id'],artifact.asset_key]).encode()).hexdigest()
                    result = self.transcription.submit(path,stable_key)
                if (not safe_id(result.get('id')) or result.get('status') not in ('uploading','queued','running','succeeded','failed','uncertain') or
                        result.get('input_sha256')!=info.sha256):
                    raise ServiceError('invalid_asr_job')
                entry['id'],entry['status'] = result['id'],result['status']
                entry['input_sha256'] = info.sha256
                self._save(job,token,cp)  # Lost upload response retries exactly the same key/bytes.
            state = self.transcription.poll(entry['id'])
            entry['polls'] += 1
            if state.get('id') != entry['id'] or state.get('status') not in ('uploading','queued','running','succeeded','failed','uncertain'):
                raise ServiceError('invalid_asr_status')
            status = state['status']
            if status=='succeeded':
                result = self.transcription.result(entry['id'])
                normalized = canonical(result.get('transcript'))
                if (result.get('job_id') != entry['id'] or result.get('processing_status') != 'completed' or
                        result.get('input_sha256') != entry.get('input_sha256')):
                    raise ServiceError('invalid_asr_result')
                with tempfile.TemporaryDirectory(prefix='result-',dir=self.settings.temp_root) as directory:
                    path = Path(directory)/'transcript.json'
                    raw = encode(result).encode()
                    path.write_bytes(raw)
                    from ._vendor.materials_http import DownloadResult
                    info = DownloadResult(path,len(raw),hashlib.sha256(raw).hexdigest(),'application/json')
                    owned = self._raw(path,info)
                provenance = {'origin':'asr','processing_status':'completed','coverage':'not_independently_verified'}
                for field,target in (('model','model_version'),('language','language')):
                    if result.get(field) is not None:
                        if not isinstance(result[field],str) or len(result[field])>200 or '://' in result[field]:
                            raise ServiceError('invalid_asr_provenance')
                        provenance[target] = result[field]
                cp['assets']['asr-'+artifact.asset_key] = {'external_id':'asr:'+entry['id'],'kind':'transcript','state':'ready',
                    'metadata':{'session_id':artifact.source.session_id},'locations':[owned],
                    'transcript':{'raw_ref':{k:v for k,v in owned.items() if k!='state'},'normalized':normalized,'provenance':provenance}}
                entry['status'] = 'succeeded'
            elif status in ('failed','uncertain') or entry['polls']>=self.settings.max_asr_polls:
                # Uncertain never triggers an automatic explicit /retry paid call.
                entry['status'],entry['error_code'] = 'failed', 'asr_'+status if status in ('failed','uncertain') else 'asr_poll_limit'
            else:
                entry['status'] = status
                pending = True
            self._save(job,token,cp)
        statuses = {asr[a.asset_key].get('status') for a in videos}
        cp['transcription_status'] = ('running' if 'running' in statuses else 'queued') if pending else \
            'failed' if 'failed' in statuses else 'succeeded'
        cp['transcript_status'] = 'ready' if any(a['kind']=='transcript' and a['state']=='ready' for a in cp['assets'].values()) else \
            'processing' if pending else 'error'
        return not pending

    def run(self,job_id):
        job,token = self.store.claim(job_id,self.settings.lease_seconds)
        if not job:
            return
        cp = job['checkpoint']
        cp.setdefault('assets',{})
        cp.setdefault('asset_errors',{})
        try:
            binding = self.binding(job['binding_id'],job['generation'])
            self._deliver(job,token)
            if cp.get('terminal'):
                # A crash can land after checkpoint commit but before receipt insert.
                # Rebuild from that exact checkpoint before considering work finished.
                self._receipt(job,token,cp)
                self.store.release(job_id,token,'succeeded' if not cp.get('failed') else 'failed')
                return
            Path(self.settings.temp_root).mkdir(parents=True,exist_ok=True,mode=0o700)
            if not cp.get('acquisition_complete'):
                attempts = job['attempts'] + 1
                self.store.save(job_id,token,cp,attempts=attempts)
                self._tick(job,token)
                discovery = self.capture.discover(binding['source_ref'])
                if len(discovery.artifacts)>self.settings.max_artifacts:
                    raise ServiceError('provider_artifact_limit')
                artifacts = [clean_artifact(a) for a in discovery.artifacts]
                cp['discovered'] = [asdict(a) for a in artifacts]
                for artifact in artifacts:
                    if artifact.status=='ready':
                        try:
                            self._acquire(job,token,cp,artifact,binding['storage_policy'])
                        except (ServiceError,StorageError,Problem,ValueError,OSError) as error:
                            if isinstance(error,LeaseLost):
                                raise
                            cp['asset_errors'][artifact.asset_key] = error.code if isinstance(error,ServiceError) else 'artifact_acquisition_failed'
                settled = attempts>=self.settings.settle_attempts
                all_acquired = all(a.status=='ready' and (cp['assets'].get(a.asset_key,{}).get('state')=='ready' or
                    a.kind=='transcript' and a.asset_key in cp.get('acquired',{})) for a in artifacts)
                channels_ok = discovery.complete and all(s.get('status') not in ('blocked','error','processing','partial') for s in discovery.statuses.values())
                cp['acquisition_complete'] = bool(settled and all_acquired and channels_ok)
                exhausted = attempts>=self.settings.max_attempts
                for channel,kinds in (('video',('video','audio')),('transcript',('transcript',))):
                    ready = any(a['kind'] in kinds and a['state']=='ready' for a in cp['assets'].values())
                    state = discovery.statuses.get(channel,{}).get('status')
                    cp[channel+'_status'] = 'ready' if ready else 'not_recorded' if settled and state=='absent' else \
                        'error' if exhausted else 'processing'
                if exhausted and not cp['acquisition_complete']:
                    cp['terminal'],cp['failed'] = True,True
                    cp['transcription_status'] = 'not_requested'
            else:
                artifacts = [Artifact.from_ref(a) for a in cp.get('discovered',[])]
            if cp.get('acquisition_complete'):
                provider_text = any(a['kind']=='transcript' for a in cp['assets'].values())
                pending_provider_text = [a for a in artifacts if a.kind=='transcript' and a.status=='ready' and
                    a.asset_key not in cp['assets']]
                if pending_provider_text:
                    # Raw acquisition and normalization readiness are separate states.
                    cp['normalization_attempts'] = cp.get('normalization_attempts',0)+1
                    normalization_errors = []
                    for artifact in pending_provider_text:
                        try:
                            self._acquire(job,token,cp,artifact,binding['storage_policy'])
                        except ServiceError as error:
                            if isinstance(error,LeaseLost):
                                raise
                            cp['error_code'] = error.code
                            normalization_errors.append(error)
                    if normalization_errors:
                        unconfigured = any(e.code in ('service_not_configured','asr_not_configured') for e in normalization_errors)
                        cp['terminal'] = unconfigured or any(not e.retryable for e in normalization_errors) or cp['normalization_attempts']>=self.settings.max_attempts
                        cp['transcription_status'] = 'awaiting_configuration' if unconfigured else 'failed' if cp['terminal'] else 'queued'
                        cp['transcript_status'] = 'ready' if provider_text else 'processing' if unconfigured or not cp['terminal'] else 'error'
                    else:
                        cp['transcript_status'] = 'ready'
                if not pending_provider_text or all(a.asset_key in cp['assets'] for a in pending_provider_text):
                    try:
                        cp['terminal'] = self._asr(job,token,cp,artifacts)
                    except ServiceError as error:
                        if isinstance(error,LeaseLost):
                            raise
                        if error.code in ('service_not_configured','asr_not_configured'):
                            cp['transcription_status'] = 'awaiting_configuration'
                            cp['terminal'] = True
                        else:
                            cp['asr_errors'] = cp.get('asr_errors',0)+1
                            cp['transcription_status'] = 'queued' if error.retryable else 'failed'
                            cp['error_code'] = error.code
                            cp['terminal'] = not error.retryable or cp['asr_errors']>=self.settings.max_attempts
                            if cp['terminal']:
                                cp['transcription_status'] = 'failed'
                                cp['transcript_status'] = 'error'
            self._save(job,token,cp)
            self._receipt(job,token,cp)
            self.store.release(job_id,token,'failed' if cp.get('failed') else 'succeeded' if cp.get('terminal') else 'waiting',delay=self.settings.retry_seconds)
        except LeaseLost:
            return
        except ServiceError as error:
            try:
                # A delivery outage retains immutable receipts and never counts as delivered.
                tries = cp.get('transport_attempts',0)+1
                cp['transport_attempts'] = tries
                self._save(job,token,cp)
                fenced = error.code in ('binding_fenced','upstream_404','upstream_401','upstream_403','upstream_409')
                pending_receipts = bool(self.store.pending(job_id))
                status = 'fenced' if fenced else 'waiting' if pending_receipts and error.retryable else \
                    'failed' if not error.retryable or tries>=self.settings.max_attempts else 'waiting'
                delay = min(3600,self.settings.retry_seconds*(2**min(tries-1,7)))
                self.store.release(job_id,token,status,delay=delay,error_code=error.code)
            except LeaseLost:
                pass
        except (OSError,ValueError,Problem,StorageError):
            try:
                self.store.release(job_id,token,'failed',error_code='source_processing_failed')
            except LeaseLost:
                pass

    def access(self,binding_id,external_id):
        # No public external-id lookup and no caller-supplied provider URL.
        binding = self.core.binding(binding_id)
        generation = binding.get('generation')
        binding = checked_binding(binding,binding_id,generation,self.settings.connection_id)
        evidence = self.store.artifact(binding_id,external_id)
        if evidence is None:
            # Historical catalogue bindings have no private source job. The core
            # authorizes the exact binding; provider discovery supplies fresh proof.
            discovery = self.capture.discover(binding['source_ref'])
            exact = [a for a in discovery.artifacts if a.provider_asset_id==external_id and a.status=='ready']
            if len(exact)!=1:
                raise ServiceError('material_not_found',404)
            evidence = asdict(clean_artifact(exact[0]))
        artifact = Artifact.from_ref(evidence)
        ref = binding['source_ref']
        if (artifact.source.room_id != ref.get('room_id') or artifact.source.room_name != ref.get('room_name') or
                ref.get('session_id') and artifact.source.session_id != ref['session_id'] or
                artifact.source.capture_id != ref.get('capture_instance_id',ref.get('capture_id'))):
            raise ServiceError('material_not_found',404)
        try:
            source = self.capture.resolve(artifact)  # Re-inspects provider room+session now.
            validate_url(source.url)
        except (Problem,ValueError,DownloadError):
            raise ServiceError('daily_access_unavailable',502) from None
        self.store.remember_verified_artifact(binding_id,external_id,evidence)
        return {'url':source.url,'expires_at':source.expires_at,'expires':source.expires_at,'provider':'daily'}
