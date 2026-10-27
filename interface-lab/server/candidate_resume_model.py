"""Durable CV extraction -> evidence -> summary -> independent grounding check.

Private SQLite stores sanitized evidence/results for retry, never application logs.
The candidate portal alone applies results after reauth and a current-run check.
Existing regex extraction is not a fallback for this pipeline.
"""
import datetime as dt
import hashlib
import json
import re
import secrets
import time

from candidate_resume import salary_value, start_date_value
import candidate_resume_prompts as prompts
from domain import Problem, iso, now, require
from structured_model import ModelConfig, complete, digest, dump, scan

FIELDS = {'name': 140, 'desiredRole': 200, 'city': 120, 'residence': 200,
          'employmentType': 40, 'salaryMin': None, 'salaryMax': None, 'latestStartDate': 10}
MAX_TEXT = 24000
RUN_SECONDS = 150
DAILY_TOKENS = 1000000  # Conservative reservations include failures/retries.
JOB_TOKENS = 350000


def obj(properties):
    return {'type': 'object', 'additionalProperties': False, 'properties': properties, 'required': list(properties)}


def string(limit, nullable=False):
    return {'type': ['string', 'null'] if nullable else 'string', 'minLength': 1, 'maxLength': limit}


PROFILE_SCHEMA = obj({key: ({'type': ['integer', 'null'], 'minimum': 1, 'maximum': 100000000}
    if limit is None else string(limit, True)) for key, limit in FIELDS.items()})
PROFILE_SCHEMA['properties']['employmentType']['enum'] = [None, 'full-time', 'part-time', 'project', 'internship']


def schemas(chunks):
    quote = obj({'chunkId': {'type': 'string', 'enum': [chunk['chunkId'] for chunk in chunks]}, 'quote': string(1000)})
    return obj({'isResume': {'type': 'boolean'}, 'profile': PROFILE_SCHEMA,
        'evidence': {'type': 'array', 'maxItems': len(FIELDS), 'items': obj({
            'field': {'type': 'string', 'enum': list(FIELDS)}, **quote['properties']})},
        'experience': {'type': 'array', 'maxItems': 6, 'items': quote}})


WRITE_SCHEMA = obj({'about': {**string(600), 'minLength': 40},
    'evidenceIds': {'type': 'array', 'maxItems': 6, 'items': string(20)}})
JUDGE_SCHEMA = obj({'grounded': {'type': 'boolean'},
    'unsupportedFields': {'type': 'array', 'maxItems': 9,
        'items': {'type': 'string', 'enum': [*FIELDS, 'about']}}})


def prepare(text):
    require(len(text) <= MAX_TEXT, 'Резюме слишком длинное для разбора. Загрузите короткую версию.', 422)
    sanitized, redactions = scan(text)
    # Preserve PDF line structure, with bounded, stable source IDs. No filename enters prompts.
    paragraphs, current = [], ''
    for line in sanitized.splitlines():
        line = re.sub(r'[\t ]+', ' ', line).strip()
        for offset in range(0, len(line) or 1, 1200):
            part = line[offset:offset + 1200]
            if len(current) + len(part) > 1600:
                paragraphs.append(current.strip())
                current = ''
            current += part + '\n'
    if current.strip():
        paragraphs.append(current.strip())
    chunks = [{'chunkId': 'cv_' + digest(str(index) + value)[:12], 'text': value}
              for index, value in enumerate(paragraphs) if value]
    require(chunks, 'В резюме недостаточно текста для заполнения профиля.', 422)
    return chunks, redactions


def verify_extraction(result, chunks):
    by_id = {item['chunkId']: item['text'] for item in chunks}
    if not result['isResume']:
        if result['evidence'] or result['experience'] or any(value is not None for value in result['profile'].values()):
            raise ValueError('unsupported_document')
        return
    seen = set()
    for fact in [*result['evidence'], *result['experience']]:
        if fact['quote'] not in by_id.get(fact['chunkId'], '') or '[[REDACTED_INJECTION]]' in fact['quote']:
            raise ValueError('unsupported_evidence')
        if 'field' in fact:
            key = fact['field']
            if key in seen or result['profile'][key] is None:
                raise ValueError('duplicate_or_empty_field')
            seen.add(key)
            if key in ('name', 'desiredRole', 'city', 'residence'):
                normalize = lambda text: re.sub(r'\s+', '', text).casefold()
                if normalize(result['profile'][key]) not in normalize(fact['quote']):
                    raise ValueError('unsupported_text')
    if seen != {key for key, value in result['profile'].items() if value is not None}:
        raise ValueError('missing_evidence')
    try:
        low, high = (salary_value(result['profile'][key]) for key in ('salaryMin', 'salaryMax'))
        if low and high and low > high:
            raise ValueError('invalid_range')
        start_date_value(result['profile']['latestStartDate'], future=True)
    except Problem:
        raise ValueError('invalid_preference') from None


def init(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS cp_resume_runs (
        id TEXT PRIMARY KEY, owner TEXT NOT NULL, status TEXT NOT NULL, model TEXT NOT NULL,
        version TEXT NOT NULL, lease TEXT NOT NULL, started REAL NOT NULL,
        tokens INTEGER NOT NULL DEFAULT 0, result TEXT, metadata TEXT);
      CREATE TABLE IF NOT EXISTS cp_resume_heads (owner TEXT PRIMARY KEY, run_id TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS cp_resume_calls (
        run_id TEXT NOT NULL, stage TEXT NOT NULL, result TEXT NOT NULL, metadata TEXT NOT NULL,
        PRIMARY KEY(run_id,stage));
      CREATE TABLE IF NOT EXISTS cp_resume_usage (
        owner TEXT NOT NULL, day TEXT NOT NULL, tokens INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(owner,day));
    ''')


class ResumeModel:
    def __init__(self, connect, lock):
        self.connect, self.lock = connect, lock

    def assert_current(self, db, owner, run_id):
        row = db.execute('SELECT run_id FROM cp_resume_heads WHERE owner=?', (owner,)).fetchone()
        require(row and row[0] == run_id, 'Выбран другой файл. Дождитесь его разбора.', 409)

    def generate(self, resume, owner, authorize):
        config = ModelConfig.from_env()
        chunks, redactions = prepare(resume['text'])
        today = now().astimezone(dt.timezone(dt.timedelta(hours=3))).date().isoformat()
        run_id = 'cv_' + digest(owner + hashlib.sha256(resume['content']).hexdigest() +
            prompts.VERSION + config.model + config.reasoning_effort + today)
        lease = secrets.token_hex(16)
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            authorize(db)
            # Drop private retry artifacts after 30 days; the original CV remains in cp_resumes.
            db.execute('DELETE FROM cp_resume_calls WHERE run_id IN (SELECT id FROM cp_resume_runs WHERE started<?)', (time.time() - 30 * 86400,))
            db.execute('DELETE FROM cp_resume_runs WHERE started<?', (time.time() - 30 * 86400,))
            db.execute('DELETE FROM cp_resume_usage WHERE day<?', ((now() - dt.timedelta(days=30)).date().isoformat(),))
            row = db.execute('SELECT status,started,result,metadata FROM cp_resume_runs WHERE id=? AND owner=?', (run_id, owner)).fetchone()
            require(not row or row[0] != 'running' or time.time() - row[1] > RUN_SECONDS + 15,
                'Резюме уже разбирается. Дождитесь результата.', 409)
            db.execute('INSERT INTO cp_resume_heads VALUES (?,?) ON CONFLICT(owner) DO UPDATE SET run_id=excluded.run_id', (owner, run_id))
            if row and row[0] == 'ready':
                return {**resume, 'fields': json.loads(row[2]), 'runId': run_id}
            db.execute('''INSERT INTO cp_resume_runs(id,owner,status,model,version,lease,started)
                VALUES (?,?,'running',?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                status='running',lease=excluded.lease,started=excluded.started,tokens=0''',
                (run_id, owner, config.model, prompts.VERSION, lease, time.time()))
        deadline = time.monotonic() + RUN_SECONDS

        def check_in(db):
            authorize(db)
            self.assert_current(db, owner, run_id)
            active = db.execute('SELECT status,lease FROM cp_resume_runs WHERE id=?', (run_id,)).fetchone()
            require(active and active == ('running', lease), 'Разбор резюме отменён. Повторите загрузку.', 409)

        def check():
            with self.connect() as db:
                check_in(db)

        def charge(tokens):
            with self.lock, self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                check_in(db)
                used = db.execute('SELECT tokens FROM cp_resume_usage WHERE owner=? AND day=?', (owner, today)).fetchone()
                job = db.execute('SELECT tokens FROM cp_resume_runs WHERE id=?', (run_id,)).fetchone()[0]
                require((used[0] if used else 0) + tokens <= DAILY_TOKENS and job + tokens <= JOB_TOKENS,
                    'Лимит разбора резюме исчерпан. Попробуйте завтра.', 429)
                db.execute('INSERT INTO cp_resume_usage VALUES (?,?,?) ON CONFLICT(owner,day) DO UPDATE SET tokens=tokens+excluded.tokens', (owner, today, tokens))
                db.execute('UPDATE cp_resume_runs SET tokens=tokens+? WHERE id=?', (tokens, run_id))

        metadata = {'promptPack': 'candidate_resume_prompts', 'version': prompts.VERSION,
                    'model': config.model, 'redactedSegments': redactions, 'chunks': chunks, 'stages': []}

        def stage(name, role, prompt, schema, source, validator=None):
            check()
            with self.connect() as db:
                prior = db.execute('SELECT result,metadata FROM cp_resume_calls WHERE run_id=? AND stage=?', (run_id, name)).fetchone()
            if prior:
                result, metrics = json.loads(prior[0]), json.loads(prior[1])
            else:
                result, metrics = complete(config, role, 'candidate_' + name, prompts.VERSION, prompt,
                    schema, source, digest(run_id + name + dump(source)), deadline=deadline,
                    check=check, charge=charge, validator=validator)
                with self.lock, self.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    check_in(db)
                    db.execute('INSERT INTO cp_resume_calls VALUES (?,?,?,?)', (run_id, name, dump(result), dump(metrics)))
            metadata['stages'].append(metrics)
            return result

        try:
            extracted = stage('cv_extract', 'cv-extractor', prompts.EXTRACT, schemas(chunks),
                {'today': today, 'chunks': chunks}, lambda value: verify_extraction(value, chunks))
            require(extracted['isResume'] and (extracted['evidence'] or extracted['experience']),
                'В файле не нашлось данных резюме. Загрузите CV с описанием опыта.', 422)
            about = None
            if extracted['experience']:
                evidence = [{'id': 'e' + str(index), **fact} for index, fact in enumerate(extracted['experience'])]
                allowed = {fact['id'] for fact in evidence}

                def validate_about(value):
                    if not value['evidenceIds'] or set(value['evidenceIds']) - allowed:
                        raise ValueError('missing_evidence')

                written = stage('cv_about', 'cv-writer', prompts.WRITE, WRITE_SCHEMA, evidence, validate_about)
                about = written['about']
                metadata['aboutEvidence'] = written['evidenceIds']
            judged = stage('cv_judge', 'cv-judge', prompts.JUDGE, JUDGE_SCHEMA,
                {'today': today, 'chunks': chunks, 'profile': extracted['profile'], 'about': about})
            require(judged['grounded'] and not judged['unsupportedFields'],
                'Не удалось подтвердить данные из резюме. Попробуйте ещё раз или загрузите другой файл.', 422)
            fields = {key: value for key, value in extracted['profile'].items() if value is not None}
            if about:
                fields['about'] = about
            metadata['evidence'] = extracted['evidence']
            with self.lock, self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                check_in(db)
                db.execute("UPDATE cp_resume_runs SET status='ready',result=?,metadata=? WHERE id=? AND lease=?",
                           (dump(fields), dump(metadata), run_id, lease))
            return {**resume, 'fields': fields, 'runId': run_id}
        except Exception:
            with self.lock, self.connect() as db:
                db.execute("UPDATE cp_resume_runs SET status='error' WHERE id=? AND lease=? AND status='running'", (run_id, lease))
            raise
