"""Private employer-owned intake drafts for the existing Add Candidates screen.

An uploaded CV is not consent, an interview or a vacancy approval. Drafts therefore
remain separate from cp_candidates/cp_applications until the recruiter completes
the real onboarding flow. No URL in an intake entry is fetched by this module.
"""
import base64
import binascii
from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePath
import re
import secrets
from urllib.parse import quote, urlsplit

from candidate_resume import MAX_BYTES, MAX_ENCODED, parse_upload
from domain import Problem, iso, now, require, text
import employer_store as store

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
    return {'candidates': [json.loads(row[0]) for row in rows]}


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
