"""Portal-facing catalogue. Authentication stays with the existing owner routes.

Listing metadata does not contact the capture provider. The legacy projection
resolves playback access separately, after checking the owner again.
"""
import datetime as dt
import time

from domain import Problem, require
from materials_capture import Artifact, owner_source_ref


class MaterialsService:
    def __init__(self, runtime):
        self.runtime = runtime
        self.repository = runtime.repository

    def register_owner(self, owner_kind, meeting, storage_policy=None):
        room = meeting.get('room') or {}
        provider = room.get('provider', 'daily')
        if provider == 'zoom' and storage_policy is not None:
            require(storage_policy == 'owned',
                    'Для записей Zoom требуется сохранение в нашем хранилище.', 400)
        if room.get('captureNeverStarted'):
            return None
        require(owner_kind in ('interview', 'meeting') and room.get('id') and room.get('name'),
                'Источник материалов не определён.', 409)
        connection = room.get('connectionId', self.runtime.connection_id)
        ref = owner_source_ref(owner_kind, meeting['id'], room, provider, connection)
        if storage_policy is not None:
            require(storage_policy in ('remote', 'owned'), 'Выберите способ хранения записи.', 400)
            sources = self.repository.list_sources(owner_kind, meeting['id'])
            if any(source['storage_policy'] != storage_policy for source in sources):
                self.repository.update_owner_policy(owner_kind, meeting['id'], storage_policy)
                for source in sources:
                    self.runtime.enqueue_discovery(source['id'])
        source = self.runtime.register_owner(owner_kind, meeting['id'], provider,
                                              connection, ref, storage_policy)
        # A reader can wake a settled or exhausted capture without doing provider
        # I/O in the request. Minute keys coalesce repeated portal refreshes.
        stamp = self.runtime.clock()
        for current in self.repository.list_sources(owner_kind, meeting['id']):
            if (not current['metadata'].get('capture_disabled') and current['state'] != 'ready'
                    and stamp - current['updated_at'] >= 60):
                self.runtime.enqueue_discovery(current['id'], token='manual:' + str(int(stamp // 60)))
        return source

    def _check(self, access_check):
        if access_check:
            with self.repository.connect() as db:
                access_check(db)

    def list_materials(self, owner_kind, owner_id, *, access_check=None):
        self._check(access_check)
        sources = self.repository.list_sources(owner_kind, owner_id)
        active = [source for source in sources if not source.get('metadata', {}).get('capture_disabled')]
        assets, versions, jobs = [], [], []
        for source in active:
            for asset in self.repository.list_assets(source['id']):
                assets.append({**asset, 'locations': self.repository.list_locations(asset['id'])})
            versions.extend(self.repository.list_transcripts(source['id']))
            jobs.extend(self.repository.list_jobs(source['id']))
        self._check(access_check)
        # Internal service result. Routes must use the allowlisted projection
        # below rather than serializing provider metadata or object keys.
        return {'sources': active, 'assets': assets, 'transcripts': versions, 'jobs': jobs}

    def get_transcript(self, owner_kind, owner_id, transcript_id, *, access_check=None):
        self._check(access_check)
        version = self.repository.get_transcript(transcript_id)
        asset = self.repository.get_asset(version['asset_id'])
        source = self.repository.get_source(asset['source_id'])
        self._owned(source, owner_kind, owner_id)
        self._check(access_check)
        current = self.repository.get_source(source['id'])
        self._owned(current, owner_kind, owner_id)
        require(current['generation'] == source['generation'], 'Доступ к материалу изменился.', 409)
        normalized = version['metadata'].get('normalized', {})
        return {'id': version['id'], 'revision': version['version'],
                'text': normalized.get('full_text', ''), 'segments': version['segments'],
                'warnings': normalized.get('warnings', [])}

    @staticmethod
    def _owned(source, owner_kind, owner_id):
        require(source['owner_kind'] == owner_kind and source['owner_id'] == owner_id,
                'Материал не найден.', 404)
        require(not source.get('metadata', {}).get('capture_disabled'), 'Материал недоступен.', 410)

    def get_asset_access(self, owner_kind, owner_id, asset_id, *, access_check=None):
        self._check(access_check)
        asset = self.repository.get_asset(asset_id)
        source = self.repository.get_source(asset['source_id'])
        self._owned(source, owner_kind, owner_id)
        require(asset['kind'] in ('video', 'audio'), 'Этот материал не является записью.', 400)
        generation = source['generation']
        owned = [location for location in self.repository.list_locations(asset_id)
                 if location['backend'] != 'provider' and location['state'] == 'verified']
        if owned:
            require(owned[-1]['metadata'].get('storage_connection_id') == self.runtime.storage.connection_id,
                    'Хранилище материала изменилось. Нужна проверка настройки.', 503)
            url = self.runtime.storage.signed_url(owned[-1]['object_key'], expires_seconds=300)
            result = {'url': url, 'expires': int(time.time()) + 300, 'storage': 'owned'}
        else:
            adapter = self.runtime.worker.adapter(source)
            resolved = adapter.resolve(Artifact.from_ref(asset['metadata']['capture']))
            # Browser playback cannot safely inherit a provider's bearer header.
            require(not resolved.headers, 'Для этой записи требуется серверная копия.', 409)
            result = {'url': resolved.url, 'expires': resolved.expires_at, 'storage': 'remote'}
        self._check(access_check)
        latest = self.repository.get_source(source['id'])
        self._owned(latest, owner_kind, owner_id)
        require(latest['generation'] == generation, 'Доступ к материалу изменился.', 409)
        return {'id': asset_id, **result}

    def legacy_materials(self, owner_kind, meeting, *, access_check=None):
        """Keep existing candidate/recruiter/employer players working on rollout."""
        data = self.list_materials(owner_kind, meeting['id'], access_check=access_check)
        recordings, transcripts, video_errors = [], [], []
        assets = {asset['id']: asset for asset in data['assets']}
        for asset in data['assets']:
            if asset['kind'] != 'video' or asset['state'] not in ('ready', 'available'):
                continue
            try:
                access = self.get_asset_access(owner_kind, meeting['id'], asset['id'], access_check=access_check)
                capture = asset['metadata'].get('capture', {}).get('metadata', {})
                recordings.append({**access, 'duration': capture.get('duration'),
                                   'copyStatus': 'ready' if access['storage'] == 'owned' else 'pending'
                                   if any(source['id'] == asset['source_id'] and source['storage_policy'] == 'owned'
                                          for source in data['sources']) else 'not_requested'})
            except Problem as error:
                # Never turn an authorization failure into a successful response.
                if error.status in (401, 403, 404, 410):
                    raise
                video_errors.append('Не удалось получить доступ к записи. Повторите попытку.')
            except Exception:
                video_errors.append('Не удалось получить доступ к записи. Повторите попытку.')
        latest = {}
        for version in data['transcripts']:
            if version['version'] > latest.get(version['asset_id'], {}).get('version', 0):
                latest[version['asset_id']] = version
        for version in latest.values():
            normalized = version['metadata'].get('normalized', {})
            transcripts.append({'id': version['id'], 'assetId': version['asset_id'],
                                'revision': version['version'],
                                'text': normalized.get('vtt') or normalized.get('full_text', ''),
                                'fullText': normalized.get('full_text', ''),
                                'segments': version['segments'], 'warnings': normalized.get('warnings', []),
                                'storage': 'owned'})
        def channel(kind, values):
            generations = {source['id']: source['generation'] for source in data['sources']}
            relevant = [job for job in data['jobs'] if job['operation'] == 'discover' or
                        assets.get(job.get('payload', {}).get('asset_id'), {}).get('kind') == kind]
            latest_jobs = {}
            for job in sorted(relevant, key=lambda item: (item['created_at'], item['id'])):
                if job['generation'] == generations.get(job['source_id']):
                    latest_jobs[(job['source_id'], job['operation'], job['payload'].get('asset_id'))] = job
            pending = any(job['status'] in ('pending', 'retry', 'running') for job in latest_jobs.values())
            failed = any(job['status'] == 'failed' for job in latest_jobs.values())
            provider_error = any(source['metadata'].get('discovery', {}).get(kind, {}).get('status')
                                 in ('blocked', 'error', 'failed') for source in data['sources'])
            expired = bool(data['sources']) and all(
                self.runtime.clock() > source['created_at'] + self.runtime.settle_seconds
                for source in data['sources'])
            disabled = bool(meeting.get('room', {}).get('captureNeverStarted'))
            status = ('ready' if values else 'not_recorded' if disabled else 'processing' if pending
                      else 'error' if failed or provider_error or expired else 'processing')
            state = {'status': status, 'ids': [item['id'] for item in values]}
            if status == 'error':
                state['error'] = 'Не удалось обработать материалы. Повторите проверку позже.'
            return state
        video, transcription = channel('video', recordings), channel('transcript', transcripts)
        if video_errors:
            video['status'] = 'ready' if recordings else 'error'
            video['error'] = video_errors[0]
        states = (video['status'], transcription['status'])
        result = {'status': 'ready' if 'ready' in states else 'processing' if 'processing' in states
                  else 'error' if 'error' in states else 'not_recorded',
                  'recordings': recordings, 'transcripts': transcripts, 'video': video,
                  'transcription': transcription, 'checkedAt': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'catalogVersion': 1, 'speechProvider': 'not_selected'}
        self._check(access_check)
        for source in data['sources']:
            current = self.repository.get_source(source['id'])
            self._owned(current, owner_kind, meeting['id'])
            require(current['generation'] == source['generation'], 'Доступ к материалам изменился.', 409)
        return result
