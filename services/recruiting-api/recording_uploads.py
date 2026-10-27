"""Product ownership and authenticated streaming proxy for recording uploads.

The independently deployed source service owns bytes, S3 publication and upload
recovery. Core receives immutable receipts. No ASR call is made by the portal.
"""
import hashlib
import http.client
import json
import os
import re
import secrets
import time
import urllib.parse

from core_source_client import CoreClient, ServiceHTTP, ServiceError
from domain import require, Problem
import employer_access
import employer_intake

PREFIX = '/api/hr/employer/recordings/uploads'
MIME = {'video/mp4', 'video/webm', 'video/quicktime', 'audio/wav', 'audio/x-wav',
        'audio/mpeg', 'audio/mp4', 'audio/ogg', 'audio/webm', 'audio/flac'}


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS hr_recording_uploads (
        id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES em_workspaces(id),
        account_id TEXT NOT NULL REFERENCES em_accounts(id), request_id TEXT NOT NULL,
        owner_kind TEXT NOT NULL CHECK(owner_kind IN ('intake','interview','meeting')),
        owner_id TEXT NOT NULL, vacancy_id TEXT NOT NULL REFERENCES cp_vacancies(id),
        scope TEXT NOT NULL, manifest TEXT NOT NULL, payload_hash TEXT NOT NULL,
        capture_identity TEXT NOT NULL UNIQUE, connection_id TEXT NOT NULL,
        source_upload_id TEXT, binding_id TEXT, generation INTEGER,
        revoked_at REAL, created_at REAL NOT NULL,
        UNIQUE(workspace_id,account_id,request_id))''')


def _dump(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


class RecordingUploads:
    def __init__(self, routes):
        self.routes, self.connect = routes, routes.connect
        with self.connect() as db:
            init(db)

    @staticmethod
    def _clients():
        require(os.environ.get('HR_CORE_CATALOG_ENABLED') == '1', 'Каталог материалов ещё не подключён.', 503)
        core = CoreClient(ServiceHTTP(os.environ.get('HR_MATERIALS_CORE_URL', ''), os.environ.get('HR_MATERIALS_CORE_KEY', '')))
        source = ServiceHTTP(os.environ.get('HR_RECORDING_UPLOAD_URL', 'http://127.0.0.1:8107'),
                             os.environ.get('HR_RECORDING_UPLOAD_KEY', ''), timeout=300)
        return core, source

    def _owner(self, db, headers, scope):
        principal = employer_access.authenticate(db, headers)
        if scope.get('source') == 'intake':
            require(set(scope) == {'source', 'id', 'vacancyId'}, 'Проверьте источник записи.', 400)
            person, _ = employer_intake._review_person(db, principal['workspaceId'], scope['vacancyId'], scope)
            require(not person.get('archived'), 'Кандидат в архиве.', 410)
            return principal, 'intake', scope['id']
        require(scope.get('source') in ('screening', 'meeting'), 'Выберите интервью.', 400)
        principal, application, _ = self.routes._identity(db, headers, scope)
        meeting = (self.routes.portal.interview(db, application['candidateId']) if scope['source'] == 'screening'
                   else self.routes._meeting(db, application, scope))
        require(meeting and meeting['status'] == 'completed', 'Завершите встречу перед добавлением записи.', 409)
        return principal, 'interview' if scope['source'] == 'screening' else 'meeting', meeting['id']

    def _row(self, db, headers, identity):
        cursor = db.execute('SELECT * FROM hr_recording_uploads WHERE id=?', (identity,))
        found = cursor.fetchone()
        require(found is not None, 'Загрузка не найдена.', 404)
        row = dict(zip([c[0] for c in cursor.description], found))
        principal, kind, owner = self._owner(db, headers, json.loads(row['scope']))
        require(principal['workspaceId'] == row['workspace_id'] and principal['accountId'] == row['account_id']
                and (kind, owner) == (row['owner_kind'], row['owner_id']) and not row['revoked_at'],
                'Нет доступа к загрузке.', 403)
        return row

    def begin(self, headers, body):
        require(isinstance(body, dict), 'Проверьте загрузку.', 400)
        scope = {k: v for k, v in body.items() if k not in ('requestId', 'files')}
        request_id, files = body.get('requestId'), body.get('files')
        require(isinstance(request_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{16,80}', request_id), 'Проверьте запрос загрузки.', 400)
        require(isinstance(files, list) and 1 <= len(files) <= 10, 'Выберите от 1 до 10 частей.', 400)
        total = 0
        for item in files:
            require(isinstance(item, dict) and set(item) == {'name', 'size', 'content_type'} and
                    isinstance(item['name'], str) and 0 < len(item['name']) <= 240 and
                    not any(ord(c) < 32 or c in '/\\' for c in item['name']) and
                    type(item['size']) is int and 0 < item['size'] <= 1024**3 and item['content_type'] in MIME,
                    'Нужен аудио- или видеофайл до 1 ГБ.', 400)
            total += item['size']
        require(total <= 2 * 1024**3, 'В одной загрузке — до 2 ГБ.', 413)
        digest = hashlib.sha256(_dump(body).encode()).hexdigest()
        connection = os.environ.get('HR_RECORDING_UPLOAD_CONNECTION', 'upload')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            principal, kind, owner = self._owner(db, headers, scope)
            old = db.execute('SELECT id,payload_hash FROM hr_recording_uploads WHERE workspace_id=? AND account_id=? AND request_id=?',
                (principal['workspaceId'], principal['accountId'], request_id)).fetchone()
            if old:
                require(old[1] == digest, 'Состав загрузки изменился.', 409)
                identity = old[0]
            else:
                identity = secrets.token_hex(16)
                db.execute('''INSERT INTO hr_recording_uploads
                    (id,workspace_id,account_id,request_id,owner_kind,owner_id,vacancy_id,scope,manifest,payload_hash,
                     capture_identity,connection_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (identity, principal['workspaceId'], principal['accountId'], request_id, kind, owner, scope['vacancyId'],
                     _dump(scope), _dump(files), digest, 'product-upload:' + identity, connection, time.time()))
        with self.connect() as db:
            row = self._row(db, headers, identity)
        core, source = self._clients()
        binding = core.bind({'schema_version': 1, 'owner_kind': kind, 'owner_id': owner, 'provider': 'upload',
            'connection_id': connection, 'storage_policy': 'owned', 'source_ref': {'room_id': identity,
            'room_name': identity, 'capture_instance_id': row['capture_identity']}})
        result = source._send('POST', '/v1/uploads', _dump({'owner_kind': kind, 'owner_id': owner,
            'binding_id': binding['binding_id'], 'generation': binding['generation'], 'files': files}).encode(),
            {'Idempotency-Key': 'product-upload:' + identity})
        with self.connect() as db:
            self._row(db, headers, identity)
            db.execute('UPDATE hr_recording_uploads SET source_upload_id=?,binding_id=?,generation=? WHERE id=?',
                (result['id'], binding['binding_id'], binding['generation'], identity))
            # Durable business intent precedes bytes and survives a closed tab
            # or lost finalize response. The bridge waits for every Core part.
            import assessment_jobs
            if kind == 'intake':
                assessment_jobs.enqueue(db, 'intake', owner, scope['vacancyId'], principal['workspaceId'])
            elif kind == 'interview':
                candidate = db.execute('SELECT candidate_id FROM cp_interviews WHERE id=?', (owner,)).fetchone()
                assessment_jobs.enqueue(db, 'candidate', candidate[0], scope['vacancyId'], principal['workspaceId'])
        return {**result, 'id': identity}

    def command(self, headers, identity, action, body):
        require(action in ('status', 'finalize') and body == {}, 'Проверьте действие загрузки.', 400)
        with self.connect() as db:
            row = self._row(db, headers, identity)
        require(row['source_upload_id'], 'Повторите начало загрузки.', 409)
        core, source = self._clients()
        path = '/v1/uploads/' + row['source_upload_id']
        result = source.call('GET', path) if action == 'status' else source.call('POST', path + '/finalize', {})
        canonical = core.materials(row['owner_kind'], row['owner_id']) if result['status'] == 'registered' and action == 'finalize' else None
        with self.connect() as db:
            self._row(db, headers, identity)
            if canonical is not None:
                import assessment_jobs
                if row['owner_kind'] == 'intake':
                    # UI/comment identity only; text and selected versions are
                    # always read from Core. Never persist signed access URLs.
                    assets = [a for a in canonical.get('assets', []) if a['kind'] in ('video','audio') and a['state'] == 'ready']
                    indexes = [a.get('metadata', {}).get('part_index') for a in assets]
                    ordered = all(type(i) is int and i >= 0 for i in indexes) and len(set(indexes)) == len(indexes)
                    assets.sort(key=lambda a: a['metadata']['part_index'] if ordered else a['id'])
                    person = json.loads(db.execute('SELECT body FROM em_intake_candidates WHERE id=?', (row['owner_id'],)).fetchone()[0])
                    profile = person.setdefault('preparedProfile', {})
                    profile['recordings'] = [{'id': a['id'], 'name': 'Часть ' + str(i + 1), 'source': 'core',
                        'publicUrl': '', 'mime': 'audio/wav' if a['kind'] == 'audio' else 'video/mp4',
                        'duration': a.get('metadata', {}).get('duration'),
                        'sha256': next((x['sha256'] for x in a.get('locations', []) if x['backend'] == 's3' and x['state'] == 'verified'), None)}
                        for i, a in enumerate(assets)]
                    db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (_dump(person), row['owner_id']))
                    assessment_jobs.enqueue(db, 'intake', row['owner_id'], row['vacancy_id'], row['workspace_id'])
                elif row['owner_kind'] == 'interview':
                    candidate = db.execute('SELECT candidate_id FROM cp_interviews WHERE id=?', (row['owner_id'],)).fetchone()
                    assessment_jobs.enqueue(db, 'candidate', candidate[0], row['vacancy_id'], row['workspace_id'])
        return {**result, 'id': identity}

    def stream_part(self, headers, identity, index, stream, size):
        with self.connect() as db:
            row = self._row(db, headers, identity)
        files = json.loads(row['manifest'])
        require(type(index) is int and 0 <= index < len(files) and size == files[index]['size'],
                'Размер части изменился.', 400)
        require(row['source_upload_id'], 'Повторите начало загрузки.', 409)
        _, source = self._clients()
        parsed = urllib.parse.urlsplit(source.base_url)
        cls = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
        connection = cls(parsed.hostname, parsed.port, timeout=300)
        try:
            connection.putrequest('PUT', '/v1/uploads/' + row['source_upload_id'] + '/parts/' + str(index))
            connection.putheader('Authorization', 'Bearer ' + source.key)
            connection.putheader('Content-Type', 'application/octet-stream')
            connection.putheader('Content-Length', str(size))
            connection.endheaders()
            remaining = size
            while remaining:
                chunk = stream.read(min(1022 * 1024, remaining))
                require(chunk, 'Загрузка прервалась. Повторите её.', 400)
                connection.send(chunk)
                remaining -= len(chunk)
            response = connection.getresponse()
            payload = response.read(65537)
            require(len(payload) <= 65536, 'Не удалось подтвердить загрузку.', 502)
            require(response.status < 300, 'Не удалось сохранить часть. Повторите загрузку.',
                    response.status if response.status in (400,403,404,409,413,415,422,429) else 503)
            result = json.loads(payload)
        except (OSError, ValueError, http.client.HTTPException):
            raise Problem('Загрузка прервалась. Повторите её.', 503) from None
        finally:
            connection.close()
        with self.connect() as db:
            self._row(db, headers, identity)
        return {**result, 'id': identity}
