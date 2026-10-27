"""Small, provider-neutral material jobs; the repository owns all durable state.

The queue is a wake-up mechanism. No worker result is current without a live
repository lease and source generation. Provider transcripts are imported; this
module never starts an ASR service or fabricates transcript content.
"""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import tempfile
import threading
import time

from materials_capture import Artifact


def _download(*args, **kwargs):
    from materials_http import download
    return download(*args, **kwargs)


def _normalize(*args, **kwargs):
    from materials_transcripts import normalize_transcript
    return normalize_transcript(*args, **kwargs)


class WorkError(Exception):
    def __init__(self, code, retryable=False):
        self.code, self.retryable = code, retryable
        super().__init__(code)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class MaterialsWorker:
    def __init__(self, repository, captures, storage, *, downloader=None, normalizer=None,
                 publish=None, lease_seconds=60, max_bytes=2 * 1024**3,
                 transcript_max_bytes=20 * 1024**2, timeout_seconds=300, temp_root=None):
        self.repository, self.captures, self.storage = repository, captures, storage
        self.downloader = downloader or _download
        self.normalizer = normalizer or _normalize
        self.publish = publish
        self.lease_seconds = max(3, int(lease_seconds))
        self.max_bytes, self.transcript_max_bytes = max_bytes, transcript_max_bytes
        self.timeout_seconds, self.temp_root = timeout_seconds, temp_root

    def adapter(self, source):
        adapter = self.captures.get((source['provider'], source['connection_id']))
        adapter = adapter or self.captures.get(source['provider'])
        if adapter is None:
            raise WorkError('capture_provider_unconfigured')
        return adapter

    def enqueue(self, source_id, operation, payload=None, *, token=None, parent_job=None):
        source = self.repository.get_source(source_id)
        job = self.repository.enqueue_job(source_id, operation,
            input_fingerprint=fingerprint({'generation': source['generation'], 'payload': payload or {},
                                           'nonce': token}), payload=payload or {},
            **(self.fence(parent_job) if parent_job else {}))
        if self.publish:
            try:
                self.publish(job['id'])
            except Exception:
                # The committed job remains visible to the next reconciliation.
                pass
        return job

    def enqueue_discovery(self, source_id, *, token=None, parent_job=None):
        return self.enqueue(source_id, 'discover', token=token, parent_job=parent_job)

    @contextmanager
    def _heartbeat(self, job):
        stop = threading.Event()
        lost = []

        def beat():
            while not stop.wait(self.lease_seconds / 3):
                try:
                    self.repository.renew_job(job['id'], job['lease_token'], self.lease_seconds)
                except Exception:
                    lost.append(True)
                    return

        thread = threading.Thread(target=beat, daemon=True, name='materials-lease')
        thread.start()
        try:
            yield lost
        finally:
            stop.set()
            thread.join(timeout=2)

    @staticmethod
    def fence(job):
        return {'job_id': job['id'], 'lease_token': job['lease_token']}

    def run_once(self, job_id=None, worker_id='materials-local'):
        job = self.repository.claim_job(worker_id, self.lease_seconds, job_id=job_id)
        if not job:
            return {'claimed': False}
        source = self.repository.get_source(job['source_id'])
        try:
            with self._heartbeat(job) as lost:
                if job['operation'] == 'discover':
                    result = self._discover(source, job)
                elif job['operation'] in ('copy_asset', 'import_transcript'):
                    result = self._import(source, job)
                else:
                    raise WorkError('unsupported_operation')
                if lost:
                    raise WorkError('stale_lease')
                self.repository.finish_job(job['id'], job['lease_token'], result)
            return {'claimed': True, 'job_id': job['id'], 'state': 'completed', 'result': result}
        except Exception as error:
            code = getattr(error, 'code', 'material_operation_failed')
            if code == 'stale_lease':
                return {'claimed': True, 'job_id': job['id'], 'state': 'stale'}
            retryable = getattr(error, 'retryable', not isinstance(error, (ValueError, TypeError)))
            if job['operation'] in ('copy_asset', 'import_transcript'):
                try:
                    failed_asset = self.repository.get_asset(job['payload']['asset_id'])
                    if failed_asset['state'] != 'unsupported':
                        self.repository.upsert_asset(source['id'], failed_asset['kind'],
                            failed_asset['provider_asset_id'], state='error',
                            metadata={**failed_asset['metadata'], 'error_code': code}, **self.fence(job))
                except Exception:
                    pass  # a stale/revoked holder cannot publish an error either
            try:
                self.repository.fail_job(job['id'], job['lease_token'], code,
                                         retryable=retryable, retry_after=30)
            except Exception as stale:
                if getattr(stale, 'code', None) != 'stale_lease':
                    raise
            return {'claimed': True, 'job_id': job['id'], 'state': 'error', 'code': code}

    def _discover(self, source, job):
        ref = {**source['source_ref'], 'provider': source['provider'],
               'connection_id': source['connection_id']}
        discovery = self.adapter(source).discover(ref)
        queued, sessions, failed_followup, pending_child = [], set(), False, False
        for artifact in discovery.artifacts:
            if artifact.kind not in ('video', 'transcript'):
                continue
            target = source
            # A server-attested exclusive room may discover several exact sessions.
            # Keep separate sources for each, never pair files by title or room alone.
            if artifact.source.session_id and artifact.source.session_id != ref.get('session_id'):
                child_ref = asdict(artifact.source)
                target = self.repository.ensure_source(source['owner_kind'], source['owner_id'],
                    source['provider'], source['connection_id'], child_ref,
                    storage_policy=source['storage_policy'], **self.fence(job))
                if target['id'] not in sessions:
                    child_job = self.enqueue_discovery(target['id'], parent_job=job)
                    if child_job['status'] in ('pending', 'retry', 'running'):
                        queued.append(child_job['id'])
                    failed_followup |= child_job['status'] == 'failed' or target['state'] in ('partial', 'error')
                    pending_child |= target['state'] in ('pending', 'processing', 'awaiting_provider')
                sessions.add(target['id'])
                continue
            sessions.add(target['id'])
            state = {'ready': 'pending', 'processing': 'pending', 'failed': 'error',
                     'missing': 'missing', 'blocked': 'unsupported'}.get(artifact.status, 'pending')
            previous = [a for a in self.repository.list_assets(target['id'])
                        if a['kind'] == artifact.kind and a['provider_asset_id'] == artifact.provider_asset_id]
            # A rediscovery must not reset already verified owned content.
            metadata = {'capture': asdict(artifact)}
            if state == 'missing':
                metadata['explicit_missing'] = True
            asset = self.repository.upsert_asset(target['id'], artifact.kind,
                artifact.provider_asset_id, state=(previous[0]['state'] if previous else state),
                metadata=metadata, **self.fence(job))
            if artifact.status != 'ready':
                continue
            if artifact.kind == 'video' and target['storage_policy'] == 'remote':
                self.repository.add_location(asset['id'], 'provider', artifact.provider_asset_id,
                    state='available', metadata={'provider': source['provider'],
                    'connection_id': source['connection_id']}, **self.fence(job))
                self.repository.upsert_asset(target['id'], artifact.kind, artifact.provider_asset_id,
                    state='ready', metadata=metadata, **self.fence(job))
            else:
                operation = 'import_transcript' if artifact.kind == 'transcript' else 'copy_asset'
                payload = {'asset_id': asset['id']}
                followup = self.enqueue(target['id'], operation, payload, parent_job=job)
                if followup['status'] == 'failed':
                    # A later discovery can recover a formerly exhausted GET/copy
                    # without deleting its history. Never jump over an active
                    # retry's backoff or duplicate work on replay of this parent.
                    related = [candidate for candidate in self.repository.list_jobs(target['id'])
                               if candidate['operation'] == operation and candidate['payload'] == payload
                               and candidate['generation'] == target['generation']]
                    active = [candidate for candidate in related
                              if candidate['status'] in ('pending', 'retry', 'running')]
                    completed = [candidate for candidate in related if candidate['status'] == 'succeeded']
                    if active:
                        followup = active[-1]
                    elif completed:
                        followup = completed[-1]
                    else:
                        followup = self.enqueue(target['id'], operation, payload,
                            token='rediscovery:' + job['id'], parent_job=job)
                if followup['status'] in ('pending', 'retry', 'running'):
                    queued.append(followup['id'])
                failed_followup |= followup['status'] == 'failed'
        states = {kind: dict(value) for kind, value in discovery.statuses.items()}
        awaiting = not any(a.kind == 'transcript' for a in discovery.artifacts)
        source_state = 'awaiting_provider' if awaiting else ('pending' if queued or pending_child else 'ready')
        if not discovery.complete and source_state == 'ready':
            source_state = 'pending'
        if failed_followup:
            source_state = 'partial'
        if any(v.get('status') in ('blocked', 'error', 'failed') for v in states.values()):
            source_state = 'partial' if discovery.artifacts else 'error'
        self.repository.set_source_state(source['id'], source_state,
            {'discovery': states, 'discovery_complete': discovery.complete,
             'asr': {'state': 'awaiting_provider', 'reason': 'no_asr_provider_selected'} if awaiting else
                    {'state': 'not_requested', 'reason': 'provider_transcript_import'},
             'session_source_ids': sorted(sessions)}, **self.fence(job))
        return {'enqueued': queued, 'complete': discovery.complete, 'sources': sorted(sessions)}

    def _import(self, source, job):
        asset = self.repository.get_asset(job['payload']['asset_id'])
        if asset['source_id'] != source['id']:
            raise WorkError('asset_source_mismatch')
        existing = self.repository.list_locations(asset['id'])
        own = [loc for loc in existing if loc['backend'] != 'provider' and loc['state'] in ('verified', 'pending')]
        namespace = getattr(self.storage, 'connection_id', self.storage.backend)
        for loc in own:
            if loc['metadata'].get('storage_connection_id') != namespace:
                raise WorkError('storage_connection_mismatch')
        transcripts = [t for t in self.repository.list_transcripts(source['id']) if t['asset_id'] == asset['id']]
        artifact = Artifact.from_ref(asset['metadata']['capture'])
        # Adopt a tracked pending Put after a crash, or normalize an already-owned
        # raw transcript without asking the provider to serve its bytes again.
        for loc in own:
            try:
                stored = self.storage.head(loc['object_key'])
            except (FileNotFoundError, OSError):
                continue
            except Exception:
                if loc['state'] == 'verified':
                    raise WorkError('owned_object_unavailable', True) from None
                continue
            if stored.sha256 != loc['sha256'] or stored.size != loc['size']:
                raise WorkError('owned_object_mismatch')
            if asset['kind'] == 'video':
                with self.storage.open(stored.key) as stream:
                    self._check_video(stream.read(64), stored.size, loc['metadata'].get('content_type'))
            location = self.repository.add_location(asset['id'], stored.backend, stored.key,
                state='verified', size=stored.size, sha256=stored.sha256, metadata=loc['metadata'], **self.fence(job))
            if asset['kind'] == 'transcript' and not transcripts:
                raw = self.storage.read_bytes(stored.key, max_bytes=self.transcript_max_bytes)
                self._save_transcript(source, job, asset, artifact, raw, stored,
                                      loc['metadata'].get('content_type'))
            self._mark_imported(source, job, asset)
            return {'asset_id': asset['id'], 'location_id': location['id'], 'already_owned': True}
        adapter = self.adapter(source)
        artifact = adapter.inspect(Artifact.from_ref(asset['metadata']['capture']))
        if artifact.status != 'ready':
            raise WorkError('provider_material_not_ready', artifact.status == 'processing')
        if (artifact.source.provider != source['provider'] or
                artifact.source.connection_id != source['connection_id'] or
                artifact.source.session_id != source['source_ref'].get('session_id') or
                artifact.provider_asset_id != asset['provider_asset_id']):
            raise WorkError('provider_identity_mismatch')
        resolved = adapter.resolve(artifact)  # URL/credentials live only in this attempt.
        maximum = self.transcript_max_bytes if asset['kind'] == 'transcript' else self.max_bytes
        with tempfile.TemporaryDirectory(prefix='materials-', dir=self.temp_root) as temporary:
            result = self.downloader(resolved, temporary + '/payload.part', max_bytes=maximum,
                timeout_seconds=self.timeout_seconds,
                expected_size=artifact.metadata.get('size'),
                expected_sha256=artifact.metadata.get('sha256'))
            if asset['kind'] == 'video':
                with open(result.path, 'rb') as head:
                    self._check_video(head.read(64), result.size, result.content_type)
            # Commit a fenced write intent before touching durable storage. If the
            # process dies after Put, cleanup/erasure can still enumerate the key.
            self.repository.add_location(asset['id'], self.storage.backend,
                self.storage.key_for_sha256(result.sha256), state='pending', size=result.size,
                sha256=result.sha256, metadata={'role': 'raw', 'content_type': result.content_type,
                                               'storage_connection_id': namespace},
                **self.fence(job))
            stored = self.storage.put_file(result.path, expected_size=result.size,
                expected_sha256=result.sha256, content_type=result.content_type)
            location = self.repository.add_location(asset['id'], stored.backend, stored.key,
                state='verified', size=stored.size, sha256=stored.sha256,
                metadata={'content_type': stored.content_type, 'role': 'raw',
                          'storage_connection_id': namespace}, **self.fence(job))
            if asset['kind'] == 'transcript':
                with open(result.path, 'rb') as raw_file:
                    raw = raw_file.read(maximum + 1)
                if len(raw) > maximum:
                    raise WorkError('transcript_size_limit')
                self._save_transcript(source, job, asset, artifact, raw, stored, result.content_type)
        self._mark_imported(source, job, asset)
        return {'asset_id': asset['id'], 'location_id': location['id']}

    @staticmethod
    def _check_video(head, size, content_type):
        mime = (content_type or '').split(';')[0].lower()
        mp4 = len(head) >= 12 and head[4:8] == b'ftyp' and 12 <= int.from_bytes(head[:4], 'big') <= size
        webm = head.startswith(b'\x1a\x45\xdf\xa3') and size >= 8
        allowed = ({'video/mp4', 'application/mp4', 'video/quicktime', 'application/octet-stream'} if mp4 else
                   {'video/webm', 'application/octet-stream'} if webm else set())
        if not size or mime not in allowed:
            raise WorkError('unexpected_video_content')

    def _save_transcript(self, source, job, asset, artifact, raw, stored, content_type):
        fmt = artifact.metadata.get('format') or self._transcript_format(content_type)
        try:
            normalized = self.normalizer(raw, fmt)
        except (ValueError, TypeError):
            self.repository.upsert_asset(source['id'], asset['kind'], asset['provider_asset_id'],
                state='unsupported', metadata={**asset['metadata'], 'normalization': 'unsupported'},
                **self.fence(job))
            raise WorkError('transcript_format_unsupported') from None
        self.repository.save_transcript(asset['id'],
            {'backend': stored.backend, 'object_key': stored.key, 'key': stored.key, 'sha256': stored.sha256,
             'size': stored.size}, normalized['segments'],
            metadata={'origin': 'provider_import', 'normalized': normalized,
                      'source_provider': source['provider']}, **self.fence(job))

    def _mark_imported(self, source, job, asset):
        self.repository.upsert_asset(source['id'], asset['kind'], asset['provider_asset_id'],
            state='ready', metadata=asset['metadata'], **self.fence(job))
        assets = self.repository.list_assets(source['id'])
        transcript_ready = any(a['kind'] == 'transcript' and a['state'] == 'ready' for a in assets)
        all_ready = all(a['state'] in ('ready', 'not_recorded') for a in assets)
        state = 'ready' if transcript_ready and all_ready else ('partial' if transcript_ready else 'awaiting_provider')
        self.repository.set_source_state(source['id'], state, **self.fence(job))

    @staticmethod
    def _transcript_format(content_type):
        return {'text/vtt': 'vtt', 'application/x-subrip': 'srt',
                'application/json': 'json'}.get((content_type or '').split(';')[0], 'unsupported')

    def reconcile(self):
        result = self.repository.reconcile_jobs()
        pending = self.repository.list_pending_jobs(limit=100)
        published = 0
        if self.publish:
            for job in pending:
                try:
                    self.publish(job['id'])
                    published += 1
                except Exception:
                    pass
        return {'reconciled': result, 'published': published}
