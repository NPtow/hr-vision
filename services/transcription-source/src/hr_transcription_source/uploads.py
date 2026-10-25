"""Private raw-byte ingress. Upload registration never submits an ASR request."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

from starlette.concurrency import run_in_threadpool

from .repository import Conflict, LostLease
from .upload_repository import UploadRepository
from .worker import Rejected, Retryable, part

# The MIME and extension select a bounded local demuxer; no requester paths/URLs.
MEDIA = {
    'video/mp4': ('mp4', {'mp4','m4v'}, 'mov'),
    'video/quicktime': ('mov', {'mov'}, 'mov'),
    'video/webm': ('webm', {'webm'}, 'matroska,webm'),
    'audio/mp4': ('m4a', {'m4a','mp4'}, 'mov'),
    'audio/mpeg': ('mp3', {'mp3','mpga','mpeg'}, 'mp3'),
    'audio/wav': ('wav', {'wav'}, 'wav'),
    'audio/x-wav': ('wav', {'wav'}, 'wav'),
    'audio/webm': ('webm', {'webm'}, 'matroska,webm'),
    'audio/ogg': ('ogg', {'ogg','oga'}, 'ogg'),
    'audio/flac': ('flac', {'flac'}, 'flac'),
    'audio/x-flac': ('flac', {'flac'}, 'flac'),
}


def clean_manifest(manifest, settings):
    files = manifest['files']
    if not 1 <= len(files) <= settings.upload_max_files:
        raise Rejected('upload_file_count_exceeded', 413)
    total = 0
    for file in files:
        if file['content_type'] not in MEDIA:
            raise Rejected('unsupported_media_type', 415)
        if not 0 < file['size'] <= settings.max_input_bytes:
            raise Rejected('upload_file_size_exceeded', 413)
        total += file['size']
        name = file.get('name')
        if name is not None:
            # Keep a display basename only. Never use this name in a path/key.
            name = name.replace('\\','/').split('/')[-1]
            name = ''.join(c for c in name if ord(c) >= 32 and ord(c) != 127).strip()
            if not name or len(name) > 255 or Path(name).suffix.lower().lstrip('.') not in MEDIA[file['content_type']][1]:
                raise Rejected('invalid_media_name', 422)
            file['name'] = name
    if total > settings.upload_max_total_bytes:
        raise Rejected('upload_total_size_exceeded', 413)
    return manifest


def probe_media(path, content_type, settings):
    extension, _, demuxer = MEDIA[content_type]
    wrapper = ('import os,resource,sys; n=int(sys.argv[1]); '
        'resource.setrlimit(resource.RLIMIT_FSIZE,(1048576,1048576)); '
        'resource.setrlimit(resource.RLIMIT_CPU,(n,n)); '
        'resource.setrlimit(resource.RLIMIT_NOFILE,(64,64)); os.execvp(sys.argv[2],sys.argv[2:])')
    command = [sys.executable,'-c',wrapper,str(settings.upload_probe_seconds),'ffprobe','-v','error',
        '-protocol_whitelist','file','-format_whitelist',demuxer,
        '-show_entries','stream=codec_type,duration:format=duration','-of','json',str(path)]
    try:
        with tempfile.TemporaryFile() as output:
            subprocess.run(command, stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.DEVNULL,
                timeout=settings.upload_probe_seconds,check=True,env={'PATH':os.environ.get('PATH','/usr/bin:/bin')})
            output.seek(0)
            raw = output.read(1024*1024+1)
        if len(raw)>1024*1024:
            raise ValueError()
        value = json.loads(raw)
        audio = [stream for stream in value['streams'] if stream.get('codec_type') == 'audio']
        video = any(stream.get('codec_type') == 'video' for stream in value['streams'])
        duration = float(value.get('format',{}).get('duration') or audio[0]['duration'])
        if not audio or not math.isfinite(duration) or not 0 < duration <= settings.upload_max_duration_seconds:
            raise ValueError()
        if content_type.startswith('video/') != video:
            raise ValueError()
        return {'duration':duration,'format':extension,'kind':'video' if video else 'audio'}
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError):
        raise Rejected('invalid_or_unsupported_media',422) from None


class Uploads:
    def __init__(self, runtime, *, probe=probe_media):
        self.runtime, self.settings = runtime, runtime.settings
        self.repository = UploadRepository(runtime.repository)
        self.root = self.settings.data_dir/'uploads'
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.probe = probe

    def authorize(self, caller, event):
        binding, token = self.runtime.authorize_binding(caller,event)
        if binding.get('provider') != 'upload' or binding.get('storage_policy') != 'owned':
            raise Rejected('owned_upload_binding_required')
        return binding, token

    def begin(self, caller, key, manifest):
        manifest = clean_manifest(manifest,self.settings)
        self.authorize(caller,manifest)
        self.cleanup()
        upload, created = self.repository.begin(caller,key,manifest,self.settings.upload_stage_budget_bytes,self.orphan_bytes())
        return {**self.public(upload),'created':created}

    def get(self, caller, identity):
        upload = self.repository.get(identity,caller)
        if upload is None:
            raise Rejected('upload_not_found',404)
        self.authorize(caller,upload['manifest'])
        return upload

    def path(self, identity, index):
        upload=self.repository.get(identity)
        local_ref=upload['parts'][index]['local_ref']
        if local_ref is None:
            # A placeholder for a missing input; never published or read as a
            # ready part. Kept independent of any current writer lease token.
            return self.root/(identity+'-'+str(index)+'.media')
        if not re.fullmatch(re.escape(identity+'-'+str(index)+'-')+r'[a-f0-9]{48}\.media',local_ref):
            raise Rejected('invalid_upload_checkpoint')
        return self.root/local_ref

    async def receive(self, caller, identity, index, request):
        upload = await run_in_threadpool(self.get,caller,identity)
        if upload['status'] in ('expired','rejected'):
            raise Rejected('upload_unavailable',410)
        if not 0 <= index < len(upload['parts']):
            raise Rejected('upload_part_not_found',404)
        if request.headers.get('content-type','').lower() != 'application/octet-stream':
            raise Rejected('raw_media_required',415)
        file, prior = upload['manifest']['files'][index], upload['parts'][index]
        if request.headers.get('content-length') is not None:
            try:
                if int(request.headers['content-length']) != file['size']:
                    raise ValueError()
            except ValueError:
                raise Rejected('upload_size_mismatch',409) from None
        if shutil.disk_usage(self.root).free < file['size'] + 64*1024**2 and prior['sha256'] is None:
            raise Retryable('staging_disk_unavailable')
        token = None
        temporary = None
        target = None
        stream_token=self.repository.claim_stream(identity,index,self.settings.lease_seconds,
            self.settings.upload_stream_concurrency,self.settings.upload_stage_budget_bytes,self.orphan_bytes())
        digest, size = hashlib.sha256(), 0
        renewed = self.runtime.repository.clock()
        try:
            # A completed part may only be read/hash-compared, including after finalization.
            if prior['sha256'] is None:
                token = self.repository.claim_part(identity,index,self.settings.lease_seconds)
                fd, temporary = tempfile.mkstemp(prefix='upload-'+identity+'-',dir=self.root)
                target = os.fdopen(fd,'wb')
            async for block in request.stream():
                now = self.runtime.repository.clock()
                if now-renewed >= 10:
                    self.repository.renew_stream(stream_token,self.settings.lease_seconds)
                    if token:
                        self.repository.update_part(identity,index,token,lease_until=now+self.settings.lease_seconds)
                    renewed = now
                size += len(block)
                if size > file['size'] or size > self.settings.max_input_bytes:
                    raise Rejected('upload_size_exceeded',413)
                digest.update(block)
                if target:
                    # A socket chunk is bounded by the ASGI server; no full-body allocation.
                    await run_in_threadpool(target.write,block)
            if size != file['size']:
                raise Rejected('upload_size_mismatch',409)
            sha = digest.hexdigest()
            if prior['sha256'] is not None:
                if sha != prior['sha256']:
                    raise Rejected('upload_input_mismatch',409)
                await run_in_threadpool(self.authorize,caller,upload['manifest'])
                return {'index':index,'status':'ready','size':size,'sha256':sha}
            target.flush()
            os.fsync(target.fileno())
            target.close()
            target = None
            probe = await run_in_threadpool(self.probe,Path(temporary),file['content_type'],self.settings)
            # Recheck revocation/generation after the potentially long stream.
            await run_in_threadpool(self.authorize,caller,upload['manifest'])
            # Each writer publishes under its unique lease token. Only a fenced
            # DB reference makes that file authoritative. An expired writer can
            # leave an orphan, never poison the next writer's immutable input.
            destination = self.root/(identity+'-'+str(index)+'-'+token+'.media')
            os.link(temporary,destination)
            self.repository.update_part(identity,index,token,status='ready',sha256=sha,probe=probe,
                local_ref=destination.name,lease_token=None,lease_until=None)
            return {'index':index,'status':'ready','size':size,'sha256':sha}
        finally:
            self.repository.release_stream(stream_token)
            if target:
                target.close()
            if temporary:
                Path(temporary).unlink(missing_ok=True)
            if token:
                try:
                    self.repository.update_part(identity,index,token,lease_token=None,lease_until=None)
                except LostLease:
                    pass

    def finalize(self, caller, identity):
        upload = self.get(caller,identity)
        if upload['status'] == 'registered':
            return self.public(upload)
        if upload['status'] in ('rejected','expired'):
            raise Rejected('upload_rejected',409)
        self.repository.start_finalize(identity)
        token = self.repository.claim_finalize(identity,self.settings.lease_seconds)
        try:
            upload = self.get(caller,identity)
            manifest = upload['manifest']
            _, write_token = self.authorize(caller,manifest)
            if upload['receipt'] is None:
                assets = []
                for index,(file,item) in enumerate(zip(manifest['files'],upload['parts'])):
                    self.repository.checkpoint(identity,token,lease_until=self.runtime.repository.clock()+self.settings.lease_seconds)
                    stored = item['stored']
                    if stored is None:
                        obj = self.runtime.storage.put_file(self.path(identity,index),expected_sha256=item['sha256'],
                            expected_size=file['size'],content_type=file['content_type'])
                        if (obj.backend,obj.sha256,obj.size,obj.content_type) != ('s3',item['sha256'],file['size'],file['content_type']):
                            raise Rejected('stored_object_proof_mismatch')
                        stored = {'backend':'s3','object_key':obj.key,'state':'verified','size':obj.size,
                            'sha256':obj.sha256,'content_type':obj.content_type}
                        self.repository.store_part(identity,index,token,stored)
                    probe = item['probe']
                    assets.append({'external_id':'upload:'+identity+':'+str(index),'kind':probe['kind'],'state':'ready',
                        'metadata':{'format':probe['format'],'duration':probe['duration'],'part_index':index},'locations':[stored]})
                receipt = {'schema_version':1,'generation':manifest['generation'],'receipt_id':'upload:'+identity,'assets':assets,
                    'video_status':'ready','transcript_status':'processing','acquisition_complete':True,'transcription_status':'not_requested'}
                self.repository.checkpoint(identity,token,receipt=receipt)
                upload = self.repository.get(identity)
            # Persisted receipt is immutable and replayed even if Core committed but ACK was lost.
            self.authorize(caller,manifest)
            ack,_ = self.runtime._json(self.runtime.core,'POST','/v1/bindings/'+part(manifest['binding_id'])+'/receipts',write_token,json=upload['receipt'])
            if (ack.get('binding_id'),ack.get('generation'),ack.get('receipt_id')) != (
                    manifest['binding_id'],manifest['generation'],upload['receipt']['receipt_id']) or len(ack.get('asset_ids',[])) != len(manifest['files']):
                raise Rejected('receipt_proof_mismatch')
            self._readback(caller,upload,ack)
            self.repository.checkpoint(identity,token,status='registered',ack=ack,lease_token=None,lease_until=None)
            # Only after durable receipt + Core readback. Raw source input is retained in S3.
            for index in range(len(upload['parts'])):
                try:
                    self.path(identity,index).unlink(missing_ok=True)
                except OSError:
                    pass  # Registered files are retried by cleanup below.
            self.cleanup()
            return self.public(self.repository.get(identity))
        except Rejected as error:
            if error.status < 500:
                self.repository.checkpoint(identity,token,status='rejected',error_code=error.code)
            raise
        finally:
            try:
                self.repository.checkpoint(identity,token,lease_token=None,lease_until=None)
            except LostLease:
                pass

    def _readback(self, caller, upload, ack):
        manifest = upload['manifest']
        material,_ = self.runtime._json(self.runtime.core,'GET','/v1/owners/'+part(manifest['owner_kind'])+'/'+part(manifest['owner_id'])+'/materials',
            self.settings.callers[caller].core_read_token)
        if (material.get('owner_kind'),material.get('owner_id')) != (manifest['owner_kind'],manifest['owner_id']):
            raise Rejected('owner_response_mismatch')
        visible = {a['id']:a for a in material.get('assets',[])}
        for index,(asset_id,expected) in enumerate(zip(ack['asset_ids'],upload['receipt']['assets'])):
            asset = visible.get(asset_id,{})
            if (asset.get('source_id'),asset.get('external_id'),asset.get('kind'),asset.get('state'),asset.get('metadata',{}).get('part_index')) != (
                    manifest['binding_id'],expected['external_id'],expected['kind'],'ready',index):
                raise Rejected('upload_readback_failed')
            wanted = expected['locations'][0]
            if not any(all(location.get(key)==wanted[key] for key in ('backend','object_key','state','sha256','size')) for location in asset.get('locations',[])):
                raise Rejected('upload_readback_failed')

    def reconcile(self):
        self.cleanup()
        complete = failed = 0
        for identity in self.repository.due():
            upload = self.repository.get(identity)
            try:
                self.finalize(upload['caller'],identity)
                complete += 1
            except Rejected as error:
                if error.status < 500:
                    self.repository.reject_unleased(identity,error.code)
                failed += 1
            except Exception:
                # Durable intent survives dependency/ACK loss; never print URLs or SDK errors.
                failed += 1
        return {'registered':complete,'deferred':failed}

    def cleanup(self):
        self.repository.expire_idle(self.settings.upload_idle_ttl_seconds)
        cutoff=self.runtime.repository.clock()-2*self.settings.lease_seconds
        # Remove only stale temporary streams or inputs whose receipt ACK is
        # already durable. Never erase an unregistered/rejected input here.
        for index,path in enumerate(self.root.iterdir()):
            if index>=100:
                break
            try:
                info=path.lstat()
                if not stat.S_ISREG(info.st_mode):
                    continue
                if path.name.startswith('upload-'):
                    match=re.match(r'upload-([a-f0-9]{32})-',path.name)
                    upload=self.repository.get(match[1]) if match else None
                    if info.st_mtime<cutoff or upload and upload['status'] in ('registered','expired'):
                        path.unlink(missing_ok=True)
                elif re.fullmatch(r'[a-f0-9]{32}-[0-9]+(?:-[a-f0-9]{48})?\.media',path.name):
                    upload=self.repository.get(path.name[:32])
                    referenced=upload and any(p['local_ref']==path.name for p in upload['parts'])
                    if upload and upload['status'] in ('registered','expired') or not referenced and info.st_mtime<cutoff:
                        path.unlink(missing_ok=True)
            except OSError:
                pass
        for identity in self.repository.terminal_reserved():
            if not any(self.root.glob(identity+'-*.media')) and not any(self.root.glob('upload-'+identity+'-*')):
                self.repository.release_reservation(identity)

    def orphan_bytes(self):
        # Registered/staged ready files already occupy their durable reservation.
        # Unreferenced files (e.g. killed writer) consume additional capacity until
        # cleanup; never allow them to disappear from the admission budget.
        with self.runtime.repository.db() as db:
            refs={row[0] for row in db.execute('''SELECT p.local_ref FROM upload_parts p JOIN uploads u ON u.id=p.upload_id
              WHERE u.reserved_bytes>0 AND p.local_ref IS NOT NULL''')}
        total=0
        for path in self.root.iterdir():
            info=path.lstat()
            if stat.S_ISREG(info.st_mode) and path.name not in refs:
                total+=info.st_size
        return total

    @staticmethod
    def public(upload):
        return {'id':upload['id'],'status':upload['status'],
            'owner_kind':upload['manifest']['owner_kind'],'owner_id':upload['manifest']['owner_id'],
            'binding_id':upload['manifest']['binding_id'],'generation':upload['manifest']['generation'],
            'parts':[{'index':p['part_index'],'size':upload['manifest']['files'][p['part_index']]['size'],
                      'status':p['status'],'sha256':p['sha256']} for p in upload['parts']],
            'asset_ids':upload['ack'].get('asset_ids',[]) if upload['ack'] else [],
            'error_code':upload['error_code'],
            'created_at':upload['created_at'],'updated_at':upload['updated_at']}
