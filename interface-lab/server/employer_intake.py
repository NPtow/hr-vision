"""Private employer-owned intake drafts for the existing Add Candidates screen.

An uploaded CV is not consent, an interview or a vacancy approval. Drafts therefore
remain separate from cp_candidates/cp_applications until the recruiter completes
the real onboarding flow. Uploaded intake links are never fetched during import;
playback only resolves a stored, ownership-checked Yandex Disk recording.
"""
import base64
import binascii
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import PurePath
import re
import secrets
import urllib.parse
import urllib.request
from urllib.parse import quote, urlsplit

from candidate_resume import MAX_BYTES, MAX_ENCODED, parse_upload
from domain import Problem, iso, now, require, text
from dsa_panel import SOURCE as DSA_PANEL_SOURCE
import employer_store as store
import employer_workflow as workflow

MAX_BATCH_BYTES = 8 * 1024 * 1024
MAX_STORAGE_BYTES = 128 * 1024 * 1024
MAX_WORKSPACE_CANDIDATES = 500
EXTENSIONS = {'.pdf': ('application/pdf', b'%PDF-'),
              '.docx': ('application/vnd.openxmlformats-officedocument.wordprocessingml.document', b'PK'),
              '.doc': ('application/msword', bytes.fromhex('d0cf11e0a1b11ae1')),
              '.rtf': ('application/rtf', b'{\\rtf')}
FIELDS = {'name': 140, 'role': 200, 'contact': 160, 'link': 2048, 'note': 4000}


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


@dataclass(frozen=True)
class PreparedBatch:
    digest: str
    batch: str
    scope: str
    entries: tuple
    total_bytes: int


def init(db):
    for statement in (
        '''CREATE TABLE IF NOT EXISTS em_intake_batches (
          workspace_id TEXT NOT NULL REFERENCES em_workspaces(id),
          vacancy_id TEXT NOT NULL REFERENCES cp_vacancies(id), batch TEXT NOT NULL,
          digest TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(workspace_id,vacancy_id,batch))''',
        '''CREATE TABLE IF NOT EXISTS em_intake_candidates (
          id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES em_workspaces(id),
          vacancy_id TEXT NOT NULL REFERENCES cp_vacancies(id), batch TEXT NOT NULL,
          body TEXT NOT NULL,
          FOREIGN KEY(workspace_id,vacancy_id,batch)
            REFERENCES em_intake_batches(workspace_id,vacancy_id,batch))''',
        '''CREATE INDEX IF NOT EXISTS em_intake_scope
          ON em_intake_candidates(workspace_id,vacancy_id)''',
        '''CREATE TABLE IF NOT EXISTS em_intake_files (
          id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES em_intake_candidates(id),
          name TEXT NOT NULL, mime TEXT NOT NULL, content BLOB NOT NULL,
          text_content TEXT NOT NULL, extracted TEXT NOT NULL, parse_status TEXT NOT NULL
          CHECK(parse_status IN ('parsed','unsupported')))''',
        '''CREATE TABLE IF NOT EXISTS em_intake_review_events (
          id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES em_workspaces(id),
          vacancy_id TEXT NOT NULL REFERENCES cp_vacancies(id),
          candidate_id TEXT NOT NULL REFERENCES em_intake_candidates(id),
          kind TEXT NOT NULL CHECK(kind IN ('comment','rejection')),
          body TEXT NOT NULL, created_at TEXT NOT NULL)''',
        '''CREATE INDEX IF NOT EXISTS em_intake_review_scope
          ON em_intake_review_events(workspace_id,vacancy_id,candidate_id)''',
    ):
        db.execute(statement)


def _body(body):
    require(isinstance(body, dict) and set(body) <= {'batch', 'scope', 'entries', 'vacancyId'} and
            {'batch', 'scope', 'entries'} <= set(body), 'Проверьте данные кандидатов.', 400)
    require(isinstance(body['batch'], str) and re.fullmatch(r'[a-zA-Z0-9-]{12,80}', body['batch']),
            'Некорректный запрос.', 400)
    require(body['scope'] in ('sfera', 'dsa'), 'Выберите подбор.', 400)
    require(isinstance(body['entries'], list) and 1 <= len(body['entries']) <= 10,
            'Можно добавить от 1 до 10 кандидатов.', 400)
    return hashlib.sha256(_dump(body).encode()).hexdigest()


def _entry(value):
    require(isinstance(value, dict) and set(value) <= set(FIELDS) | {'file'},
            'Проверьте данные кандидата.', 400)
    entry = {key: text(value.get(key, ''), limit, required=key == 'name') for key, limit in FIELDS.items()}
    if entry['link']:
        try:
            url = urlsplit(entry['link'])
            require(url.scheme == 'https' and bool(url.hostname) and not url.username and not url.password
                    and not any(ord(ch) < 32 or ord(ch) == 127 for ch in entry['link']),
                    'Нужна HTTPS-ссылка на резюме.', 400)
            _ = url.port  # Reject malformed/non-numeric ports without fetching.
        except ValueError:
            raise Problem('Проверьте ссылку на резюме.', 400)
    file = value.get('file')
    if file is None:
        return entry, None
    require(isinstance(file, dict) and set(file) == {'name', 'data'}, 'Проверьте резюме.', 400)
    filename = text(file['name'], 240)
    require('/' not in filename and '\\' not in filename and
            not any(ord(char) < 32 or ord(char) == 127 for char in filename), 'Проверьте название файла.', 400)
    extension = PurePath(filename).suffix.lower()
    require(extension in EXTENSIONS, 'Подойдут PDF, DOC, DOCX или RTF.', 415)
    raw = file['data']
    require(isinstance(raw, str) and 0 < len(raw) <= MAX_ENCODED, 'Резюме должно быть не больше 5 МБ.', 413)
    try:
        content = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise Problem('Не удалось прочитать файл.', 400)
    require(0 < len(content) <= MAX_BYTES, 'Резюме должно быть не больше 5 МБ.', 413)
    mime, signature = EXTENSIONS[extension]
    require(content.startswith(signature), 'Содержимое файла не соответствует формату.', 400)
    return entry, {'name': filename, 'mime': mime, 'content': content,
                   'extension': extension, 'encoded': raw}


def prepare(body):
    """Authenticate and check vacancy first, then call outside the HTTP global lock.

    PDF/DOCX use the existing bounded local extractor. DOC/RTF are safely retained
    as attachments, with explicitly unsupported extraction. This does not create
    a candidate account or infer any consent from the document.
    """
    digest = _body(body)
    entries, total = [], 0
    # Validate every size/signature before launching even the first PDF process.
    for raw in body['entries']:
        entry, file = _entry(raw)
        if file:
            total += len(file['content'])
            require(total <= MAX_BATCH_BYTES, 'За один раз можно добавить не больше 8 МБ файлов.', 413)
        entries.append((entry, file))
    for entry, file in entries:
        if file is None:
            continue
        file.update(text='', fields={}, parse_status='unsupported')
        if file['extension'] in ('.pdf', '.docx'):
            parsed = parse_upload({'filename': 'resume' + file['extension'], 'contentBase64': file['encoded']})
            file.update(text=parsed['text'], fields=parsed['fields'], parse_status='parsed')
            if not entry['role']:
                entry['role'] = parsed['fields'].get('desiredRole', '')
        del file['encoded']
    return PreparedBatch(digest, body['batch'], body['scope'], tuple(entries), total)


def _check_scope(db, workspace_id, vacancy_id, body=None, *, include_archived=False):
    vacancy = store.vacancy(db, workspace_id, vacancy_id, include_archived=include_archived)
    if body is not None:
        require(isinstance(body, dict), 'Проверьте данные кандидатов.', 400)
        if body.get('vacancyId') is not None:
            require(body['vacancyId'] == vacancy_id, 'Вакансия изменилась. Обновите страницу.', 409)
    return vacancy


def list_candidates(db, workspace_id, vacancy_id):
    _check_scope(db, workspace_id, vacancy_id)
    rows = db.execute('''SELECT body FROM em_intake_candidates WHERE workspace_id=? AND vacancy_id=?
      ORDER BY rowid DESC''', (workspace_id, vacancy_id)).fetchall()
    events = {}
    for cid, kind, body in db.execute('''SELECT candidate_id,kind,body FROM em_intake_review_events
      WHERE workspace_id=? AND vacancy_id=? ORDER BY rowid''', (workspace_id, vacancy_id)):
        events.setdefault(cid, []).append((kind, json.loads(body)))
    people = [json.loads(row[0]) for row in rows]
    return {'candidates': [_review_projection(person, events.get(person['id'], [])) for person in people]}


def _recordings(person):
    profile = person.get('preparedProfile')
    recordings = profile.get('recordings') if isinstance(profile, dict) else None
    return recordings if isinstance(recordings, list) else []


def _recording_fingerprint(recording):
    # The identity is derived only from persisted source metadata. A filename or
    # a chapter title can be edited without moving comments to another source.
    source = {key: recording.get(key) for key in
              ('publicUrl', 'mime', 'size', 'id', 'sha256', 'checksum', 'version')}
    if recording.get('source') == 'dsa-panel':
        source.update(source='dsa-panel', sourceCandidateId=recording.get('sourceCandidateId'))
    return 'intake-recording:' + hashlib.sha256(_dump(source).encode()).hexdigest()


def _stored_recording(person, index):
    require(type(index) is int and 0 <= index < 10, 'Выберите запись.', 400)
    recordings = _recordings(person)
    require(index < len(recordings) and isinstance(recordings[index], dict), 'Запись не найдена.', 404)
    recording = recordings[index]
    url, mime = recording.get('publicUrl', ''), recording.get('mime', '')
    if recording.get('source') == 'dsa-panel':
        source_id = recording.get('sourceCandidateId')
        source = (person.get('preparedProfile') or {}).get('source') or {}
        require(isinstance(source, dict) and source.get('kind') == 'dsa-panel' and
                source.get('example') is True and source.get('candidateId') == source_id and
                isinstance(source_id, str) and re.fullmatch(r'\d{1,12}', source_id) and
                len(recordings) == 1 and url == DSA_PANEL_SOURCE + '/api/video/' + source_id,
                'Не удалось открыть исходную запись.', 422)
    else:
        require(isinstance(url, str) and re.fullmatch(r'https://disk\.yandex\.ru/[id]/[A-Za-z0-9_-]{8,120}', url),
                'Не удалось открыть исходную запись.', 422)
    require(isinstance(mime, str) and mime.startswith(('video/', 'audio/')), 'Этот файл не является записью.', 422)
    return recording


def _review_projection(person, events):
    sources = {}
    for index in range(min(len(_recordings(person)), 10)):
        try:
            sources[index] = _recording_fingerprint(_stored_recording(person, index))
        except Problem:
            # An unavailable/replaced source does not make old comments belong
            # to the current video. The append-only rows remain in history.
            continue
    comments = [value for kind, value in events if kind == 'comment' and
                sources.get(value['recordingIndex']) == value['recordingId']]
    rejections = [value for kind, value in events if kind == 'rejection']
    response = ({'decision': 'reject', 'createdAt': rejections[-1]['createdAt']} if rejections else
                {'decision': 'invite', 'createdAt': person['invitationRequestedAt']}
                if person.get('invitationRequestedAt') else None)
    if response and response['decision'] == 'invite' and person.get('invitationMeeting'):
        response.update(meetingStart=person['invitationMeeting']['start'], meetingStatus='draft')
    # This opaque 52-bit token is exactly representable in JavaScript. Binding
    # it to the current sources prevents a stale index from targeting a newly
    # replaced recording, even before the first comment exists. GET stays read-only.
    version = {'id': person['id'], 'archived': bool(person.get('archived')),
               'sources': sources, 'events': len(events)}
    if person.get('invitationRequestedAt'):
        version['invitationRequestedAt'] = person['invitationRequestedAt']
    if person.get('invitationMeeting'):
        version['invitationMeeting'] = person['invitationMeeting']
    revision = int(hashlib.sha256(_dump(version).encode()).hexdigest()[:13], 16)
    return {**person, 'screeningComments': comments,
            'reviewRejection': rejections[-1] if rejections else None,
            'reviewResponse': response, 'reviewRevision': revision}


def _review_person(db, workspace_id, vacancy_id, body):
    _check_scope(db, workspace_id, vacancy_id, body)
    row = db.execute('''SELECT body FROM em_intake_candidates
      WHERE id=? AND workspace_id=? AND vacancy_id=?''', (body['id'], workspace_id, vacancy_id)).fetchone()
    require(row is not None, 'Кандидат не найден.', 404)
    events = [(kind, json.loads(value)) for kind, value in db.execute('''SELECT kind,body
      FROM em_intake_review_events WHERE workspace_id=? AND vacancy_id=? AND candidate_id=? ORDER BY rowid''',
        (workspace_id, vacancy_id, body['id']))]
    person = json.loads(row[0])
    return person, _review_projection(person, events)


def _review_body(body, *, comment):
    common = {'vacancyId', 'id', 'requestId', 'expectedRevision'}
    fields = {'recordingIndex', 'seconds', 'chapterTitle', 'text'} if comment else {'rejectionAnswers'}
    required = common | (fields - {'chapterTitle'} if comment else fields)
    require(isinstance(body, dict) and required <= set(body) <= common | fields,
            'Проверьте данные отзыва.', 400)
    return _review_identity(body)


def _review_identity(body):
    require(isinstance(body['id'], str) and re.fullmatch(r'[a-f0-9]{24}', body['id']),
            'Кандидат не найден.', 404)
    require(isinstance(body['vacancyId'], str) and bool(body['vacancyId']), 'Выберите вакансию.', 400)
    require(type(body['expectedRevision']) is int and 0 <= body['expectedRevision'] < 2 ** 53,
            'Обновите карточку кандидата.', 400)
    request_id = text(body['requestId'], 160)
    require(not any(ord(ch) < 32 or ord(ch) == 127 for ch in request_id), 'Некорректный запрос.', 400)
    return request_id


def _review_text(value, limit=4000, *, required=True):
    result = text(value, limit, required=required)
    require(not any((ord(ch) < 32 and ch not in '\n\r\t') or ord(ch) == 127 for ch in result),
            'Проверьте текст отзыва.', 400)
    return result


def _require_review_writable(db, workspace_id, vacancy_id, person, projected, expected_revision):
    require(projected['reviewRevision'] == expected_revision,
            'Карточка изменилась. Обновите её перед сохранением.', 409)
    store.ensure_open(db, workspace_id, vacancy_id)
    require(not person.get('archived'), 'Кандидат уже в архиве.', 409)


def _require_unanswered(projected):
    require(projected['reviewResponse'] is None, 'Ответ по этому кандидату уже сохранён.', 409)


def screening_comment(db, workspace_id, vacancy_id, body):
    """Append one private note to the exact persisted intake recording."""
    request_id = _review_body(body, comment=True)
    index, seconds = body['recordingIndex'], body['seconds']
    require(type(index) is int and 0 <= index < 10, 'Выберите запись.', 400)
    require(type(seconds) in (int, float) and 0 <= seconds <= 14400 and math.isfinite(seconds),
            'Проверьте время комментария.', 400)
    note = _review_text(body['text'])
    title = _review_text(body.get('chapterTitle', ''), 240, required=False)
    payload = {**body, 'requestId': request_id, 'text': note, 'chapterTitle': title}
    with store._atomic(db):
        person, projected = _review_person(db, workspace_id, vacancy_id, body)
        previous, digest = store._command(db, workspace_id, request_id, 'intake.comment', payload)
        if previous is not None:
            # Recompute, rather than return a saved profile snapshot: old retry
            # responses must not reattach a comment after its source changed.
            return list_candidates(db, workspace_id, vacancy_id)
        _require_review_writable(db, workspace_id, vacancy_id, person, projected, body['expectedRevision'])
        recording = _stored_recording(person, index)
        for field in ('duration', 'durationSeconds'):
            duration = recording.get(field)
            if type(duration) in (int, float) and 0 <= duration <= 14400 and math.isfinite(duration):
                require(seconds <= duration, 'Комментарий находится за пределами записи.', 400)
        created_at = iso(now())
        event = {'id': secrets.token_hex(12), 'recordingIndex': index,
                 'recordingId': _recording_fingerprint(recording), 'seconds': seconds,
                 'chapterTitle': title, 'text': note, 'createdAt': created_at}
        db.execute('INSERT INTO em_intake_review_events VALUES (?,?,?,?,?,?,?)',
                   (event['id'], workspace_id, vacancy_id, person['id'], 'comment', _dump(event), created_at))
        store._remember(db, workspace_id, request_id, 'intake.comment', digest, {'eventId': event['id']})
        return list_candidates(db, workspace_id, vacancy_id)


def review_reject(db, workspace_id, vacancy_id, body):
    """Keep the rejection answers and archive the draft in one transaction."""
    request_id = _review_body(body, comment=False)
    answers = workflow.rejection_answers(body['rejectionAnswers'])
    payload = {**body, 'requestId': request_id, 'rejectionAnswers': answers}
    with store._atomic(db):
        person, projected = _review_person(db, workspace_id, vacancy_id, body)
        previous, digest = store._command(db, workspace_id, request_id, 'intake.reject', payload)
        if previous is not None:
            return list_candidates(db, workspace_id, vacancy_id)
        _require_review_writable(db, workspace_id, vacancy_id, person, projected, body['expectedRevision'])
        _require_unanswered(projected)
        created_at = iso(now())
        event = {**answers, 'id': secrets.token_hex(12), 'createdAt': created_at}
        db.execute('INSERT INTO em_intake_review_events VALUES (?,?,?,?,?,?,?)',
                   (event['id'], workspace_id, vacancy_id, person['id'], 'rejection', _dump(event), created_at))
        person['archived'] = True
        db.execute('UPDATE em_intake_candidates SET body=? WHERE id=? AND workspace_id=? AND vacancy_id=?',
                   (_dump(person), person['id'], workspace_id, vacancy_id))
        store._remember(db, workspace_id, request_id, 'intake.reject', digest, {'eventId': event['id']})
        return list_candidates(db, workspace_id, vacancy_id)


def review_respond(db, workspace_id, vacancy_id, body):
    """Persist private employer intent; this never creates a candidate or meeting."""
    require(isinstance(body, dict) and body.get('decision') in ('invite', 'reject'),
            'Выберите ответ по кандидату.', 400)
    fields = {'vacancyId', 'id', 'decision', 'requestId', 'expectedRevision'}
    if body['decision'] == 'reject':
        fields.add('rejectionAnswers')
    optional = {'meetingStart'} if body['decision'] == 'invite' else set()
    require(fields <= set(body) <= fields | optional, 'Проверьте данные ответа.', 400)
    request_id = _review_identity(body)
    if body['decision'] == 'reject':
        result = review_reject(db, workspace_id, vacancy_id, {key: value for key, value in body.items() if key != 'decision'})
    else:
        payload = {**body, 'requestId': request_id}
        if 'meetingStart' in body:
            payload['meetingStart'] = workflow.review_meeting_start(body['meetingStart'], private=True)
        with store._atomic(db):
            person, projected = _review_person(db, workspace_id, vacancy_id, body)
            previous, digest = store._command(db, workspace_id, request_id, 'intake.invite', payload)
            if previous is None:
                _require_review_writable(db, workspace_id, vacancy_id, person, projected, body['expectedRevision'])
                response = projected['reviewResponse']
                adding_time = ('meetingStart' in payload and response and response['decision'] == 'invite' and
                               'meetingStart' not in response)
                require(response is None or adding_time, 'Ответ по этому кандидату уже сохранён.', 409)
                stamp = iso(now())
                if 'meetingStart' in payload:
                    start = workflow.review_meeting_start(payload['meetingStart'], future=True, private=True)
                    person['invitationMeeting'] = {'start': start, 'createdAt': stamp}
                person['invitationRequestedAt'] = person.get('invitationRequestedAt') or stamp
                db.execute('UPDATE em_intake_candidates SET body=? WHERE id=? AND workspace_id=? AND vacancy_id=?',
                           (_dump(person), person['id'], workspace_id, vacancy_id))
                store._remember(db, workspace_id, request_id, 'intake.invite', digest,
                                {'decision': 'invite', 'createdAt': person['invitationRequestedAt'],
                                 **({'meetingStart': payload['meetingStart'], 'meetingStatus': 'draft'}
                                    if 'meetingStart' in payload else {})})
            result = list_candidates(db, workspace_id, vacancy_id)
    saved = next(person for person in result['candidates'] if person['id'] == body['id'])
    return {**result, 'response': saved['reviewResponse'], 'reviewRejection': saved['reviewRejection']}


def existing(db, workspace_id, vacancy_id, body):
    """Cheap authenticated retry lookup; permits reuse without reparsing the CV."""
    _check_scope(db, workspace_id, vacancy_id, body)
    digest = _body(body)
    row = db.execute('''SELECT digest FROM em_intake_batches WHERE workspace_id=? AND vacancy_id=? AND batch=?''',
                     (workspace_id, vacancy_id, body['batch'])).fetchone()
    if row is None:
        return None
    require(row[0] == digest, 'Состав кандидатов изменился. Обновите страницу.', 409)
    rows = db.execute('''SELECT body FROM em_intake_candidates
      WHERE workspace_id=? AND vacancy_id=? AND batch=? ORDER BY rowid''',
                      (workspace_id, vacancy_id, body['batch'])).fetchall()
    return {'candidates': [json.loads(row[0]) for row in rows]}


def add(db, workspace_id, vacancy_id, body, prepared=None):
    _check_scope(db, workspace_id, vacancy_id, body)
    previous = existing(db, workspace_id, vacancy_id, body)
    if previous is not None:
        return previous
    store.ensure_open(db, workspace_id, vacancy_id)
    prepared = prepared if prepared is not None else prepare(body)
    require(isinstance(prepared, PreparedBatch) and prepared.digest == _body(body),
            'Файлы изменились. Повторите загрузку.', 409)
    with store._atomic(db):
        # Recheck after extraction and after acquiring the write lock. No stale
        # authorization/closed-vacancy decision is accepted by the HTTP adapter.
        _check_scope(db, workspace_id, vacancy_id, body)
        previous = existing(db, workspace_id, vacancy_id, body)
        if previous is not None:
            return previous
        store.ensure_open(db, workspace_id, vacancy_id)
        count = db.execute('SELECT COUNT(*) FROM em_intake_candidates WHERE workspace_id=?', (workspace_id,)).fetchone()[0]
        require(count + len(prepared.entries) <= MAX_WORKSPACE_CANDIDATES, 'Достигнут лимит базы кандидатов.', 409)
        stored = db.execute('SELECT COALESCE(SUM(LENGTH(content)),0) FROM em_intake_files').fetchone()[0]
        require(stored + prepared.total_bytes <= MAX_STORAGE_BYTES, 'Недостаточно места для резюме.', 413)
        created_at = iso(now())
        db.execute('INSERT INTO em_intake_batches VALUES (?,?,?,?,?)',
                   (workspace_id, vacancy_id, prepared.batch, prepared.digest, created_at))
        result = []
        for entry, file in prepared.entries:
            person = {**entry, 'id': secrets.token_hex(12), 'scope': prepared.scope,
                      'files': [], 'createdAt': created_at, 'archived': False}
            if file:
                person['files'].append({'id': secrets.token_hex(12), 'name': file['name'], 'size': len(file['content'])})
            db.execute('INSERT INTO em_intake_candidates VALUES (?,?,?,?,?)',
                       (person['id'], workspace_id, vacancy_id, prepared.batch, _dump(person)))
            if file:
                db.execute('INSERT INTO em_intake_files VALUES (?,?,?,?,?,?,?,?)',
                           (person['files'][0]['id'], person['id'], file['name'], file['mime'], file['content'],
                            file['text'], _dump(file['fields']), file['parse_status']))
            result.append(person)
        return {'candidates': result}


def archive(db, workspace_id, vacancy_id, body):
    require(isinstance(body, dict) and set(body) <= {'id', 'archived', 'vacancyId'} and
            isinstance(body.get('archived'), bool) and isinstance(body.get('id'), str), 'Выберите действие.', 400)
    with store._atomic(db):
        _check_scope(db, workspace_id, vacancy_id, body)
        row = db.execute('''SELECT body FROM em_intake_candidates
          WHERE id=? AND workspace_id=? AND vacancy_id=?''', (body.get('id'), workspace_id, vacancy_id)).fetchone()
        require(row is not None, 'Кандидат не найден.', 404)
        person = json.loads(row[0])
        person['archived'] = body['archived']
        db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (_dump(person), person['id']))
        return {'candidate': person}


def file(db, workspace_id, file_id):
    require(isinstance(file_id, str) and re.fullmatch(r'[a-f0-9]{24}', file_id), 'Резюме не найдено.', 404)
    # ID alone never authorizes a download, including archived drafts/vacancies.
    row = db.execute('''SELECT f.name,f.content,c.vacancy_id FROM em_intake_files f
      JOIN em_intake_candidates c ON c.id=f.candidate_id JOIN em_vacancies v ON v.vacancy_id=c.vacancy_id
      WHERE f.id=? AND c.workspace_id=? AND v.workspace_id=?''',
                     (file_id, workspace_id, workspace_id)).fetchone()
    require(row is not None, 'Резюме не найдено.', 404)
    _check_scope(db, workspace_id, row[2], include_archived=True)
    content = row[1]
    return content, {'Content-Type': 'application/octet-stream',
                     'Content-Disposition': "attachment; filename*=UTF-8''" + quote(row[0], safe=''),
                     'Content-Length': str(len(content)), 'Cache-Control': 'no-store',
                     'X-Content-Type-Options': 'nosniff'}


def recording_source(db, workspace_id, body):
    require(isinstance(body, dict) and {'id', 'index'} <= set(body) <= {'id', 'index', 'vacancyId'},
            'Выберите запись.', 400)
    require('vacancyId' not in body or (isinstance(body['vacancyId'], str) and bool(body['vacancyId'])),
            'Выберите вакансию.', 400)
    cid, index = body.get('id'), body.get('index')
    require(isinstance(cid, str) and re.fullmatch(r'[a-f0-9]{24}', cid)
            and type(index) is int and 0 <= index < 10, 'Выберите запись.', 400)
    row = db.execute('SELECT vacancy_id,body FROM em_intake_candidates WHERE id=? AND workspace_id=?',
                     (cid, workspace_id)).fetchone()
    require(row is not None, 'Запись не найдена.', 404)
    _check_scope(db, workspace_id, row[0], body)
    recording = _stored_recording(json.loads(row[1]), index)
    source = {'publicUrl': recording['publicUrl'], 'mime': recording['mime']}
    if recording.get('source') == 'dsa-panel':
        source.update(source='dsa-panel', sourceCandidateId=recording['sourceCandidateId'])
    return source


class _NoRecordingRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        return None


def resolve_recording(recording):
    """Use the fixed public API, never fetch a caller-supplied host or a video file."""
    if recording.get('source') == 'dsa-panel':
        source_id = recording.get('sourceCandidateId')
        require(isinstance(source_id, str) and re.fullmatch(r'\d{1,12}', source_id) and
                recording.get('publicUrl') == DSA_PANEL_SOURCE + '/api/video/' + source_id and
                isinstance(recording.get('mime'), str) and recording['mime'].startswith(('video/', 'audio/')),
                'Не удалось открыть исходную запись.', 422)
        # DSA's existing Range-capable proxy handles its private storage URL.
        # Do not resolve it as a Yandex public key or copy/download the media.
        return {'url': recording['publicUrl'], 'mime': recording['mime']}
    url = 'https://cloud-api.yandex.net/v1/disk/public/resources/download?' + urllib.parse.urlencode({
        'public_key': recording['publicUrl'],
    })
    try:
        request = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.build_opener(_NoRecordingRedirect).open(request, timeout=12) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError('Response too large')
        href = json.loads(raw)['href']
        target = urllib.parse.urlsplit(href)
        if (target.scheme != 'https' or target.username or target.password or target.port not in (None, 443)
                or not target.hostname or not (target.hostname == 'downloader.disk.yandex.ru'
                    or target.hostname.endswith('.storage.yandex.net'))):
            raise ValueError('Unexpected media origin')
        return {'url': href, 'mime': recording['mime']}
    except Exception:
        raise Problem('Не удалось открыть запись. Попробуйте ещё раз или откройте исходный файл.', 502) from None
