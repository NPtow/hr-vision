"""Employer outcomes for real applications; no provider calls or parallel profiles.

The caller authenticates a manager and checks workspace ownership before entry.
Candidate actions additionally verify candidate/application ownership here. The
caller owns the SQLite commit and enqueues returned events in that transaction.
Every write takes a SQLite write lock before reading mutable state; a savepoint
makes a failed command atomic even when the caller already owns a transaction.
"""
from contextlib import contextmanager
import copy
import datetime as dt
import hashlib
import json
import math
import secrets
import sqlite3
import urllib.parse

from domain import Problem, iso, now, require, text, timestamp
from feedback import questionnaire


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _id(prefix):
    return prefix + '_' + secrets.token_hex(12)


def init(db):
    schema = '''
    CREATE TABLE IF NOT EXISTS cp_employer_workflows (
      application_id TEXT PRIMARY KEY REFERENCES cp_applications(id),
      revision INTEGER NOT NULL DEFAULT 0,
      decision TEXT NOT NULL DEFAULT 'review' CHECK(decision IN ('review','pool','declined')),
      after_interest TEXT NOT NULL DEFAULT 'pending' CHECK(after_interest IN ('pending','yes','no')),
      after_meeting_id TEXT, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS cp_meeting_feedback (
      meeting_id TEXT PRIMARY KEY REFERENCES cp_meetings(id),
      application_id TEXT NOT NULL REFERENCES cp_applications(id),
      questionnaire TEXT NOT NULL, draft TEXT NOT NULL DEFAULT '{}',
      submitted TEXT, submitted_by TEXT, updated_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS cp_feedback_application ON cp_meeting_feedback(application_id);
    CREATE TABLE IF NOT EXISTS cp_offer_versions (
      id TEXT PRIMARY KEY, application_id TEXT NOT NULL REFERENCES cp_applications(id),
      vacancy_id TEXT NOT NULL REFERENCES cp_vacancies(id), version INTEGER NOT NULL,
      terms TEXT NOT NULL, status TEXT NOT NULL
        CHECK(status IN ('draft','sent','accepted','declined','withdrawn','superseded')),
      created_by TEXT NOT NULL, created_at TEXT NOT NULL, sent_at TEXT,
      answered_at TEXT, answered_by TEXT REFERENCES cp_candidates(id),
      UNIQUE(application_id,version));
    CREATE UNIQUE INDEX IF NOT EXISTS cp_offer_one_active_vacancy
      ON cp_offer_versions(vacancy_id) WHERE status IN ('sent','accepted');
    CREATE TABLE IF NOT EXISTS cp_vacancy_outcomes (
      vacancy_id TEXT PRIMARY KEY REFERENCES cp_vacancies(id),
      application_id TEXT UNIQUE NOT NULL REFERENCES cp_applications(id),
      candidate_id TEXT NOT NULL REFERENCES cp_candidates(id),
      offer_id TEXT UNIQUE NOT NULL REFERENCES cp_offer_versions(id), accepted_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS cp_workflow_messages (
      id TEXT PRIMARY KEY, application_id TEXT NOT NULL REFERENCES cp_applications(id),
      actor TEXT NOT NULL, actor_id TEXT NOT NULL, text TEXT NOT NULL,
      created_at TEXT NOT NULL, system INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS cp_messages_application ON cp_workflow_messages(application_id,created_at);
    CREATE TABLE IF NOT EXISTS cp_workflow_commands (
      application_id TEXT NOT NULL REFERENCES cp_applications(id), actor_key TEXT NOT NULL,
      request_id TEXT NOT NULL, fingerprint TEXT NOT NULL, created_at TEXT NOT NULL,
      PRIMARY KEY(application_id,actor_key,request_id));
    CREATE TABLE IF NOT EXISTS cp_screening_comments (
      id TEXT PRIMARY KEY, application_id TEXT NOT NULL REFERENCES cp_applications(id),
      interview_id TEXT NOT NULL, recording_id TEXT, seconds REAL NOT NULL,
      chapter_title TEXT NOT NULL DEFAULT '', text TEXT NOT NULL,
      created_by TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS cp_screening_comments_application
      ON cp_screening_comments(application_id,interview_id);
    CREATE TABLE IF NOT EXISTS cp_review_rejections (
      application_id TEXT PRIMARY KEY REFERENCES cp_applications(id),
      answers TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS cp_test_assignments (
      id TEXT PRIMARY KEY, application_id TEXT NOT NULL REFERENCES cp_applications(id),
      title TEXT NOT NULL, instructions TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL DEFAULT 'assigned'
        CHECK(status IN ('assigned','in_progress','completed','cancelled')),
      result TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', due_at TEXT,
      created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS cp_tests_application ON cp_test_assignments(application_id,created_at);
    CREATE TABLE IF NOT EXISTS cp_employer_stages (
      application_id TEXT NOT NULL REFERENCES cp_applications(id), stage_id TEXT NOT NULL,
      kind TEXT NOT NULL CHECK(kind IN ('interview','meeting','test','offer')),
      entity_id TEXT NOT NULL, position INTEGER NOT NULL,
      notes TEXT NOT NULL DEFAULT '', documents TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL,
      PRIMARY KEY(application_id,stage_id), UNIQUE(application_id,position),
      UNIQUE(application_id,kind,entity_id));
    '''
    # Unlike executescript, executing the fixed DDL statements individually does
    # not commit unrelated writes owned by the startup/integration transaction.
    for statement in schema.split(';'):
        if statement.strip():
            db.execute(statement)


@contextmanager
def _atomic(db):
    started = not db.in_transaction
    if started:
        db.execute('BEGIN IMMEDIATE')
    name = 'workflow_' + secrets.token_hex(8)
    db.execute('SAVEPOINT ' + name)
    try:
        # Upgrading an existing deferred transaction before reads also serialises
        # writers in a process that does not share the HTTP server's Python lock.
        db.execute('UPDATE cp_employer_workflows SET revision=revision WHERE 0')
        yield
    except Exception:
        db.execute('ROLLBACK TO ' + name)
        db.execute('RELEASE ' + name)
        if started:
            db.rollback()
        raise
    else:
        db.execute('RELEASE ' + name)


def identity(db, application_id, *, candidate_id=None, vacancy_id=None):
    """Canonical identity, never selected by an untrusted actor/candidate field."""
    row = db.execute('''SELECT a.id,a.candidate_id,a.vacancy_id,a.status,v.body,a.archived_at
      FROM cp_applications a JOIN cp_vacancies v ON v.id=a.vacancy_id WHERE a.id=?''',
      (application_id,)).fetchone()
    require(row is not None and (candidate_id is None or row[1] == candidate_id)
            and (vacancy_id is None or row[2] == vacancy_id), 'Вакансия не найдена.', 404)
    return {'applicationId': row[0], 'candidateId': row[1], 'vacancyId': row[2],
            'interest': row[3], 'vacancy': json.loads(row[4]), 'archivedAt': row[5]}


def require_active(info):
    require(info['archivedAt'] is None, 'Этот подбор завершён после остановки поиска кандидатом.', 409)


def _workflow(db, app_id):
    row = db.execute('''SELECT revision,decision,after_interest,after_meeting_id
      FROM cp_employer_workflows WHERE application_id=?''', (app_id,)).fetchone()
    return {'revision': row[0], 'decision': row[1], 'afterInterest': row[2],
            'afterMeetingId': row[3]} if row else {
                'revision': 0, 'decision': 'review', 'afterInterest': 'pending', 'afterMeetingId': None}


def _review_rejected(db, app_id):
    # The queue is owned by employer_store. This defensive read also covers an
    # existing queue rejection made before a workflow row was ever created.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='em_reviews'").fetchone():
        return False
    row = db.execute('SELECT state FROM em_reviews WHERE application_id=?', (app_id,)).fetchone()
    return bool(row and row[0] == 'rejected')


def _meeting(db, app_id, meeting_id=None):
    if meeting_id is None:
        row = db.execute('''SELECT id,status,start,end,completed_at FROM cp_meetings
          WHERE application_id=? ORDER BY rowid DESC LIMIT 1''', (app_id,)).fetchone()
    else:
        row = db.execute('''SELECT id,status,start,end,completed_at FROM cp_meetings
          WHERE application_id=? AND id=?''', (app_id, meeting_id)).fetchone()
    if meeting_id is not None:
        require(row is not None and row[0] == meeting_id, 'Встреча изменилась. Обновите кабинет.', 409)
    return {'id': row[0], 'status': row[1], 'start': row[2], 'end': row[3],
            'completedAt': row[4]} if row else None


def _completed_meeting(db, app_id, meeting_id):
    require(isinstance(meeting_id, str) and bool(meeting_id), 'Укажите встречу.', 400)
    meeting = _meeting(db, app_id, meeting_id)
    require(meeting['status'] == 'completed', 'Сначала завершите встречу.', 409)
    return meeting


def _current_after_interest(state, meeting):
    """An answer belongs only to its exact meeting, never to later stages."""
    return state['afterInterest'] if meeting and state['afterMeetingId'] == meeting['id'] else 'pending'


def _feedback(db, app_id, meeting_id):
    row = db.execute('''SELECT questionnaire,draft,submitted FROM cp_meeting_feedback
      WHERE application_id=? AND meeting_id=?''', (app_id, meeting_id)).fetchone()
    return {'questionnaire': json.loads(row[0]), 'draft': json.loads(row[1]),
            'submitted': json.loads(row[2]) if row[2] else None} if row else None


def meeting_feedback(db, application_id, meeting_id, *, vacancy_id=None):
    """Manager-only adapter helper for an exact historical employer meeting.

    Like manager_action, workspace authorization belongs to the caller. Never
    include this private overlay in candidate or guest responses.
    """
    identity(db, application_id, vacancy_id=vacancy_id)
    _meeting(db, application_id, meeting_id)
    record = _feedback(db, application_id, meeting_id)
    return {'meetingId': meeting_id, 'questionnaire': record['questionnaire'] if record else None,
            'feedbackDraft': record['draft'] if record and not record['submitted'] else {},
            'feedback': record['submitted'] if record else None}


def _current_offer(db, app_id):
    row = db.execute('''SELECT id,version,terms,status,created_at,sent_at,answered_at,answered_by
      FROM cp_offer_versions WHERE application_id=? ORDER BY version DESC LIMIT 1''', (app_id,)).fetchone()
    return {**json.loads(row[2]), 'id': row[0], 'version': row[1], 'status': row[3],
            'createdAt': row[4], 'sentAt': row[5], 'answeredAt': row[6],
            'answeredBy': row[7]} if row else None


def _table_exists(db, name):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def screening_comments(db, application_id):
    """Private append-only annotations; caller checks the employer's workspace."""
    rows = db.execute('''SELECT id,interview_id,recording_id,seconds,chapter_title,text,created_at
      FROM cp_screening_comments WHERE application_id=? ORDER BY rowid''', (application_id,))
    keys = ('id', 'interviewId', 'recordingId', 'seconds', 'chapterTitle', 'text', 'createdAt')
    return [dict(zip(keys, row)) for row in rows]


def review_rejection(db, application_id):
    row = db.execute('SELECT answers,created_at FROM cp_review_rejections WHERE application_id=?',
                     (application_id,)).fetchone()
    return {**json.loads(row[0]), 'createdAt': row[1]} if row else None


def rejection_answers(value):
    """The same private rejection form for canonical and intake profiles."""
    require(isinstance(value, dict) and set(value) <= {'reason', 'missing', 'improve'},
            'Проверьте ответы о причине отказа.', 400)
    result = {key: text(value.get(key, ''), 4000, required=key == 'reason')
              for key in ('reason', 'missing', 'improve')}
    require(not any((ord(ch) < 32 and ch not in '\n\r\t') or ord(ch) == 127
                    for answer in result.values() for ch in answer), 'Проверьте текст отзыва.', 400)
    return result


def review_response(db, application_id):
    """Initial employer answer, independent of a later meeting/outcome.

    Imported and older canonical rows need no backfill: an existing refusal has
    its own real timestamp; a selected meeting is never an invented invitation.
    """
    invitation_at = rejected_at = meeting_id = None
    if _table_exists(db, 'em_reviews'):
        columns = {row[1] for row in db.execute('PRAGMA table_info(em_reviews)')}
        if {'invitation_requested_at', 'rejected_at'} <= columns:
            row = db.execute('SELECT invitation_requested_at,rejected_at FROM em_reviews WHERE application_id=?',
                             (application_id,)).fetchone()
            if row:
                invitation_at, rejected_at = row
            if 'invitation_meeting_id' in columns:
                row = db.execute('SELECT invitation_meeting_id FROM em_reviews WHERE application_id=?',
                                 (application_id,)).fetchone()
                meeting_id = row[0] if row else None
    rejection = review_rejection(db, application_id)
    if invitation_at:
        result = {'decision': 'invite', 'createdAt': invitation_at}
        if meeting_id:
            meeting = _meeting(db, application_id, meeting_id)
            result.update(meetingStart=meeting['start'], meetingStatus=meeting['status'])
        return result
    if rejection or rejected_at:
        return {'decision': 'reject', 'createdAt': rejection['createdAt'] if rejection else rejected_at}
    return None


def review_meeting_start(value, *, future=False, private=False):
    """Normalize the exact proposed time; availability is checked by the portal.

    Parsing happens before idempotency lookup, while time-sensitive validation
    happens only for a new write so a committed retry remains valid tomorrow.
    A private draft is an employer preference, never candidate availability.
    """
    try:
        start = timestamp(text(value, 80))
    except Problem:
        raise Problem('Выберите дату и время встречи.', 400)
    require(start.microsecond == 0, 'Выберите время из календаря.', 400)
    if private:
        require(start.minute in (0, 30) and start.second == 0,
                'Выберите время с шагом 30 минут.', 400)
    if future:
        tick = now()
        require(tick < start <= tick + dt.timedelta(days=180),
                'Выберите будущее время в пределах 180 дней.', 400)
    return iso(start)


def _screening_comment_source(db, info, body):
    interview_id = text(body.get('interviewId'), 160)
    require(_table_exists(db, 'cp_interviews'), 'Интервью не найдено.', 404)
    row = db.execute('SELECT status,room FROM cp_interviews WHERE id=? AND candidate_id=?',
                     (interview_id, info['candidateId'])).fetchone()
    require(row is not None, 'Интервью не найдено.', 404)
    require(row[0] == 'completed', 'Комментарии доступны после интервью.', 409)
    seconds = body.get('seconds')
    require(type(seconds) in (int, float) and math.isfinite(seconds) and 0 <= seconds <= 14400,
            'Проверьте таймкод комментария.', 400)
    recording_id = body.get('recordingId')
    if recording_id is not None:
        recording_id = text(recording_id, 240)
    room = json.loads(row[1]) if row[1] else None
    recordings = []
    if room and _table_exists(db, 'cp_media_state'):
        # Cached provider IDs are accepted only for this exact source room. A
        # caller-supplied URL or a different candidate's recording is never proof.
        cached = db.execute('SELECT room_id,body FROM cp_media_state WHERE meeting_id=? AND kind=?',
                            (interview_id, 'interview')).fetchone()
        if cached and json.loads(cached[0]) == {'id': room['id'], 'name': room['name']}:
            recordings = json.loads(cached[1]).get('recordings', [])
    if recording_id is not None:
        recording = next((item for item in recordings if item.get('id') == recording_id), None)
        require(recording is not None, 'Запись изменилась. Откройте интервью ещё раз.', 409)
        duration = recording.get('duration')
        if type(duration) in (int, float) and math.isfinite(duration) and duration >= 0:
            require(seconds <= duration, 'Таймкод выходит за пределы записи.', 400)
    else:
        require(not recordings, 'Выберите запись для комментария.', 400)
    return interview_id, recording_id, seconds


def _test(db, app_id, test_id):
    row = db.execute('''SELECT id,title,instructions,status,result,note,due_at,created_at,updated_at
      FROM cp_test_assignments WHERE application_id=? AND id=?''', (app_id, test_id)).fetchone()
    require(row is not None, 'Тестовое задание не найдено.', 404)
    return dict(zip(('id', 'title', 'instructions', 'status', 'result', 'note', 'dueAt', 'createdAt', 'updatedAt'), row))


def stage_history(db, application_id, *, vacancy_id=None):
    """Read-only, manager-only history over canonical entities.

    Authorization belongs to the authenticated employer adapter. The ledger owns
    only display order and private annotations; meeting/offer status and results
    always come from their original tables. Opening history never writes rows.
    """
    info = identity(db, application_id, vacancy_id=vacancy_id)
    stages = []
    if _table_exists(db, 'cp_interviews'):
        screening = db.execute('''SELECT i.id,i.status,i.created_at,i.completed_at,s.start
          FROM cp_interviews i JOIN cp_recruiter_slots s ON s.id=i.slot_id
          WHERE i.candidate_id=?''', (info['candidateId'],)).fetchone()
        if screening:
            stages.append({'id': 'interview:' + screening[0], 'kind': 'interview', 'interviewId': screening[0],
                           'title': 'Интервью с рекрутером', 'status': screening[1], 'date': screening[4],
                           'createdAt': screening[2], 'completedAt': screening[3]})
    # Older standalone workflow fixtures predate the meeting lifecycle migration.
    # Production uses created_at; legacy rows retain their stable original start.
    columns = {row[1] for row in db.execute('PRAGMA table_info(cp_meetings)')}
    created = 'COALESCE(created_at,start)' if 'created_at' in columns else 'start'
    rows = db.execute(f'''SELECT id,{created} FROM cp_meetings
      WHERE application_id=? ORDER BY rowid''', (application_id,)).fetchall()
    for mid, created_at in rows:
        meeting = _meeting(db, application_id, mid)
        record = meeting_feedback(db, application_id, mid)
        if record['questionnaire']:
            meeting['questionnaire'] = record['questionnaire']
            meeting['feedbackDraft'] = record['feedbackDraft']
        stages.append({'id': 'meeting:' + mid, 'kind': 'meeting', 'meetingId': mid,
                       'title': 'Встреча', 'status': meeting['status'], 'date': meeting['start'],
                       'createdAt': created_at, 'meeting': meeting, 'feedback': record['feedback']})
    for (tid,) in db.execute('SELECT id FROM cp_test_assignments WHERE application_id=? ORDER BY rowid', (application_id,)):
        assignment = _test(db, application_id, tid)
        stages.append({'id': 'test:' + tid, 'kind': 'test', 'testId': tid,
                       'title': assignment['title'], 'status': assignment['status'],
                       'date': assignment['dueAt'] or assignment['createdAt'],
                       'createdAt': assignment['createdAt'], 'result': assignment['result'], 'test': assignment})
    for row in db.execute('''SELECT id,version,terms,status,created_at,sent_at,answered_at,answered_by
      FROM cp_offer_versions WHERE application_id=? ORDER BY version''', (application_id,)):
        offer = {**json.loads(row[2]), 'id': row[0], 'version': row[1], 'status': row[3],
                 'createdAt': row[4], 'sentAt': row[5], 'answeredAt': row[6], 'answeredBy': row[7]}
        stages.append({'id': 'offer:' + row[0], 'kind': 'offer', 'offerId': row[0],
                       'title': 'Оффер', 'status': row[3], 'date': row[5] or row[4],
                       'createdAt': row[4], 'offer': offer})
    overlays = {row[0]: {'position': row[1], 'notes': row[2], 'documents': json.loads(row[3])}
                for row in db.execute('''SELECT stage_id,position,notes,documents
                  FROM cp_employer_stages WHERE application_id=?''', (application_id,))}
    # Existing rows are deterministically presented before any later imported
    # entity. On the next explicit write they are registered in this same order.
    stages.sort(key=lambda item: (0 if item['kind'] == 'interview' else 1,
                0 if item['id'] in overlays else 1,
                overlays[item['id']]['position'] if item['id'] in overlays else 0,
                item.get('createdAt') or '', item['id']))
    for stage in stages:
        overlay = overlays.get(stage['id'], {})
        stage['notes'] = stage['test']['note'] if stage['kind'] == 'test' else overlay.get('notes', '')
        stage['documents'] = overlay.get('documents', [])
    return stages


def register_stages(db, application_id, *, vacancy_id=None):
    """Append missing canonical entities during an already authenticated write.

    Call before and after adding a meeting, test, or offer so cross-kind order
    survives same-second timestamps and later rescheduling. No duplicate truth.
    """
    require(db.in_transaction, 'Нужна транзакция для сохранения этапа.', 409)
    stages = stage_history(db, application_id, vacancy_id=vacancy_id)
    position = db.execute('SELECT COALESCE(MAX(position),0) FROM cp_employer_stages WHERE application_id=?',
                          (application_id,)).fetchone()[0]
    known = {row[0] for row in db.execute('SELECT stage_id FROM cp_employer_stages WHERE application_id=?', (application_id,))}
    for stage in stages:
        if stage['id'] in known:
            continue
        position += 1
        kind, entity_id = stage['id'].split(':', 1)
        db.execute('''INSERT INTO cp_employer_stages
          (application_id,stage_id,kind,entity_id,position,updated_at) VALUES (?,?,?,?,?,?)''',
          (application_id, stage['id'], kind, entity_id, position, iso(now())))


def _stage(db, app_id, stage_id):
    stage_id = text(stage_id, 220)
    stage = next((row for row in stage_history(db, app_id) if row['id'] == stage_id), None)
    require(stage is not None, 'Этап не найден.', 404)
    return stage


def _documents(value):
    require(isinstance(value, list) and len(value) <= 20, 'Добавьте не больше 20 ссылок.', 400)
    documents, ids = [], set()
    for item in value:
        require(isinstance(item, dict), 'Проверьте документ.', 400)
        title, url = text(item.get('title'), 240), text(item.get('url'), 2000)
        try:
            parsed = urllib.parse.urlsplit(url)
            safe = parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password
        except ValueError:
            safe = False
        require(bool(safe) and not any(ord(char) < 32 for char in url), 'Укажите HTTP или HTTPS ссылку на документ.', 400)
        did = text(item['id'], 160) if 'id' in item else _id('document')
        require(did not in ids, 'Документ повторяется.', 400)
        ids.add(did)
        documents.append({'id': did, 'title': title, 'url': url})
    return documents


def _due_at(value):
    if value in (None, ''):
        return None
    value = text(value, 60)
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise Problem('Проверьте срок тестового задания.', 400)
    require(parsed.tzinfo is not None, 'Укажите часовой пояс срока.', 400)
    return iso(timestamp(value))


def _closed(db, info):
    row = db.execute('SELECT candidate_id FROM cp_vacancy_outcomes WHERE vacancy_id=?',
                     (info['vacancyId'],)).fetchone()
    return row[0] if row else info['vacancy'].get('closedBy')


def _open(db, info):
    require(not _closed(db, info) and info['vacancy'].get('status') not in ('closed', 'archived'),
            'Подбор по вакансии завершён.', 409)


def load(db, application_id, *, actor='manager', candidate_id=None, vacancy_id=None):
    require(actor in ('manager', 'candidate'), 'Этот аккаунт не имеет доступа.', 403)
    require(actor != 'candidate' or candidate_id is not None, 'Войдите в свой кабинет.', 401)
    info = identity(db, application_id, candidate_id=candidate_id if actor == 'candidate' else None,
                    vacancy_id=vacancy_id)
    state = {key: info[key] for key in ('applicationId', 'candidateId', 'vacancyId', 'interest', 'archivedAt')}
    state.update(_workflow(db, application_id))
    if _review_rejected(db, application_id):
        state['decision'] = 'declined'
    state['closedBy'] = _closed(db, info)
    state['meeting'] = _meeting(db, application_id)
    state['afterInterest'] = _current_after_interest(state, state['meeting'])
    record = _feedback(db, application_id, state['meeting']['id']) if state['meeting'] else None
    state['feedback'] = record['submitted'] if record else None
    if state['meeting'] and record and actor == 'manager':
        state['meeting']['questionnaire'] = record['questionnaire']
        if not record['submitted']:
            state['meeting']['feedbackDraft'] = record['draft']
    state['offer'] = _current_offer(db, application_id)
    if actor == 'manager':
        state['screeningComments'] = screening_comments(db, application_id)
        state['reviewRejection'] = review_rejection(db, application_id)
        state['stages'] = stage_history(db, application_id, vacancy_id=info['vacancyId'])
        state['meetings'] = [stage['meeting'] for stage in state['stages'] if stage['kind'] == 'meeting']
        state['offers'] = [stage['offer'] for stage in state['stages'] if stage['kind'] == 'offer']
    # Return the latest bounded page, but retain complete history in the database.
    rows = db.execute('''SELECT id,actor,actor_id,text,created_at,system FROM
      (SELECT rowid,id,actor,actor_id,text,created_at,system FROM cp_workflow_messages
       WHERE application_id=? ORDER BY rowid DESC LIMIT 500) ORDER BY rowid''', (application_id,))
    state['messages'] = [{'id': r[0], 'actor': r[1], 'actorId': r[2], 'text': r[3],
                          'at': r[4], 'system': bool(r[5])} for r in rows]
    if actor == 'candidate':
        state.pop('feedback', None)
        state.pop('afterMeetingId', None)
        if state['offer'] and state['offer']['status'] == 'draft':
            state['offer'] = None
    return state


def _touch(db, app_id):
    db.execute('''INSERT INTO cp_employer_workflows(application_id,updated_at) VALUES (?,?)
      ON CONFLICT(application_id) DO NOTHING''', (app_id, iso(now())))
    db.execute('''UPDATE cp_employer_workflows SET revision=revision+1,updated_at=?
      WHERE application_id=?''', (iso(now()), app_id))


def check_revision(db, application_id, expected_revision):
    require(type(expected_revision) is int and expected_revision == _workflow(db, application_id)['revision'],
            'Данные уже изменились. Обновите кабинет.', 409)


def meeting_changed(db, application_id):
    """The authenticated meeting adapter appends history in its own transaction."""
    register_stages(db, application_id)
    _touch(db, application_id)


def review_changed(db, application_id):
    """Advance the existing revision after an atomic, private review answer."""
    _touch(db, application_id)


def _message(db, app_id, actor, actor_id, words, *, system=False):
    mid = _id('message')
    db.execute('INSERT INTO cp_workflow_messages VALUES (?,?,?,?,?,?,?)',
               (mid, app_id, actor, actor_id, words, iso(now()), int(system)))
    return mid


def _event(info, event_type, entity_id, *, audience='candidate'):
    return {'candidateId': info['candidateId'], 'applicationId': info['applicationId'],
            'vacancyId': info['vacancyId'], 'type': event_type, 'entityId': entity_id,
            'eventKey': event_type + ':' + entity_id, 'audience': audience}


def _answers(value, questions, *, complete=False):
    ids = {q['id'] for q in questions}
    require(isinstance(value, dict) and set(value) <= ids, 'Некорректные ответы.', 400)
    result = {}
    for key, answer in value.items():
        require(isinstance(answer, dict) and answer.get('rating') in
                ('clear', 'unclear', 'unanswered', 'not_asked'), 'Оцените ответ.', 400)
        result[key] = {'rating': answer['rating'],
                       'comment': text(answer.get('comment', ''), 3000, required=False)}
    require(not complete or set(result) == ids, 'Отметьте ответ на каждый вопрос.', 400)
    return result


def prepare_questions(db, application_id, meeting_id, turns, *, vacancy_id=None):
    """Called by the authenticated adapter after provider I/O, never during it.

    Only the actual employer's turns are supplied. The first nonempty questionnaire
    freezes its questions for the exact completed meeting. Later retries return it.
    """
    with _atomic(db):
        require_active(identity(db, application_id, vacancy_id=vacancy_id))
        _completed_meeting(db, application_id, meeting_id)
        existing = _feedback(db, application_id, meeting_id)
        if existing:
            return {'status': 'ready', 'questionnaire': existing['questionnaire'], 'draft': existing['draft']}
        require(isinstance(turns, list) and len(turns) <= 1500, 'Некорректная расшифровка.', 400)
        clean_turns = []
        for turn in turns:
            require(isinstance(turn, dict), 'Некорректная реплика.', 400)
            words = text(turn.get('text'), 1500)
            seconds = turn.get('seconds', 0)
            require(type(seconds) in (int, float) and 0 <= seconds <= 14400, 'Некорректное время.', 400)
            clean_turns.append({'text': words, 'seconds': seconds, 'source': 'transcript'})
        q = questionnaire(clean_turns, meeting_id)
        if q is None:
            return {'status': 'missing', 'questionnaire': None, 'draft': {}}
        db.execute('''INSERT INTO cp_meeting_feedback
          (meeting_id,application_id,questionnaire,updated_at) VALUES (?,?,?,?)''',
          (meeting_id, application_id, _json(q), iso(now())))
        _touch(db, application_id)
        return {'status': 'ready', 'questionnaire': q, 'draft': {}}


MANAGER_ACTIONS = {'feedback.questions', 'feedback.draft', 'feedback', 'decision', 'reject', 'offer.save', 'offer.send',
                   'chat', 'test.create', 'test.update', 'stage.update', 'screening.comment.add'}
CANDIDATE_ACTIONS = {'after', 'offer.reply', 'chat'}
ALIASES = {'feedback.submit': 'feedback', 'offer.draft': 'offer.save', 'after.interest': 'after'}


def manager_action(db, application_id, action, body, *, manager_id, vacancy_id=None):
    require(isinstance(manager_id, str) and bool(manager_id), 'Войдите в кабинет работодателя.', 401)
    return _action(db, application_id, action, body, actor='manager', actor_id=manager_id,
                   vacancy_id=vacancy_id)


def candidate_action(db, candidate_id, application_id, action, body):
    require(isinstance(candidate_id, str) and bool(candidate_id), 'Войдите в свой кабинет.', 401)
    return _action(db, application_id, action, body, actor='candidate', actor_id=candidate_id)


def reject(db, application_id, *, manager_id, vacancy_id=None, request_id=None,
           rejection_answers=None, expected_revision=None):
    """Explicit early refusal, with cancellation handled by the adapter.

    The adapter cancels pending/confirmed meetings before calling this helper and
    updates employer_store visibility afterwards, all in the same transaction.
    A live meeting and an accepted offer cannot be withdrawn by this operation.
    """
    body = {'requestId': request_id} if request_id is not None else {}
    if rejection_answers is not None:
        body['rejectionAnswers'] = rejection_answers
    if expected_revision is not None:
        body['expectedRevision'] = expected_revision
    return manager_action(db, application_id, 'reject', body,
                          manager_id=manager_id, vacancy_id=vacancy_id)


def _action(db, app_id, action, body, *, actor, actor_id, vacancy_id=None):
    require(isinstance(body, dict), 'Некорректное действие.', 400)
    require(isinstance(action, str), 'Некорректное действие.', 400)
    action = ALIASES.get(action, action)
    require(action in (MANAGER_ACTIONS if actor == 'manager' else CANDIDATE_ACTIONS),
            'Этот аккаунт не может выполнить действие.', 403)
    actor_key = actor + ':' + actor_id
    rid = body.get('requestId')
    if rid is not None:
        rid = text(rid, 120)
    if action == 'screening.comment.add' or (action == 'reject' and 'rejectionAnswers' in body):
        require(rid is not None, 'Укажите идентификатор запроса.', 400)
    fingerprint = hashlib.sha256(_json({'action': action, 'body': {
        k: v for k, v in body.items() if k != 'requestId'}}).encode()).hexdigest()
    events = []
    with _atomic(db):
        info = identity(db, app_id, candidate_id=actor_id if actor == 'candidate' else None,
                        vacancy_id=vacancy_id)
        require_active(info)
        if rid is not None:
            old = db.execute('''SELECT fingerprint FROM cp_workflow_commands
              WHERE application_id=? AND actor_key=? AND request_id=?''', (app_id, actor_key, rid)).fetchone()
            if old:
                require(old[0] == fingerprint, 'Этот запрос уже использован для другого действия.', 409)
                return {'state': load(db, app_id, actor=actor, candidate_id=actor_id if actor == 'candidate' else None),
                        'events': [], 'replayed': True}
        state = _workflow(db, app_id)
        hidden = _review_rejected(db, app_id)
        if action in ('test.create', 'test.update', 'stage.update', 'screening.comment.add') or (
                action == 'reject' and 'rejectionAnswers' in body):
            require('expectedRevision' in body, 'Обновите карточку перед сохранением.', 409)
        if 'expectedRevision' in body:
            check_revision(db, app_id, body['expectedRevision'])
        require(info['interest'] == 'accepted', 'Сначала кандидат должен согласовать вакансию.', 409)
        require((state['decision'] != 'declined' and not hidden) or action == 'reject'
                or (action == 'decision' and body.get('value') == 'declined'),
                'Компания завершила работу с этим кандидатом.', 409)
        if action in ('test.create', 'test.update', 'stage.update', 'offer.save'):
            register_stages(db, app_id)
        changed = _apply(db, info, state, action, body, actor, actor_id, events)
        if changed:
            if action in ('test.create', 'offer.save'):
                register_stages(db, app_id)
            _touch(db, app_id)
        if rid is not None:
            db.execute('INSERT INTO cp_workflow_commands VALUES (?,?,?,?,?)',
                       (app_id, actor_key, rid, fingerprint, iso(now())))
        return {'state': load(db, app_id, actor=actor, candidate_id=actor_id if actor == 'candidate' else None),
                'events': events, 'replayed': not changed}


def _apply(db, info, state, action, body, actor, actor_id, events):
    app_id = info['applicationId']
    offer = _current_offer(db, app_id)
    if action == 'screening.comment.add':
        _open(db, info)
        interview_id, recording_id, seconds = _screening_comment_source(db, info, body)
        words = text(body.get('text'), 4000)
        chapter = text(body.get('chapterTitle', ''), 240, required=False)
        db.execute('''INSERT INTO cp_screening_comments
          (id,application_id,interview_id,recording_id,seconds,chapter_title,text,created_by,created_at)
          VALUES (?,?,?,?,?,?,?,?,?)''',
          (_id('screening_comment'), app_id, interview_id, recording_id, seconds, chapter, words, actor_id, iso(now())))
    elif action == 'test.create':
        _open(db, info)
        tid, stamp = _id('test'), iso(now())
        title = text(body.get('title'), 240)
        instructions = text(body.get('instructions', ''), 12000, required=False)
        note = text(body.get('note', ''), 12000, required=False)
        due_at = _due_at(body.get('dueAt'))
        documents = _documents(body.get('documents', []))
        db.execute('''INSERT INTO cp_test_assignments
          (id,application_id,title,instructions,note,due_at,created_by,created_at,updated_at)
          VALUES (?,?,?,?,?,?,?,?,?)''', (tid, app_id, title, instructions, note, due_at, actor_id, stamp, stamp))
        register_stages(db, app_id)
        db.execute('''UPDATE cp_employer_stages SET documents=? WHERE application_id=? AND stage_id=?''',
                   (_json(documents), app_id, 'test:' + tid))
    elif action == 'test.update':
        assignment = _test(db, app_id, text(body.get('testId'), 160))
        require(any(key in body for key in ('title', 'instructions', 'status', 'result', 'note', 'dueAt', 'documents')),
                'Укажите изменения тестового задания.', 400)
        values = {key: text(body.get(key, assignment[key]), 240 if key == 'title' else 12000,
                            required=key == 'title') for key in ('title', 'instructions', 'result', 'note')}
        values['status'] = body.get('status', assignment['status'])
        require(values['status'] in ('assigned', 'in_progress', 'completed', 'cancelled'), 'Проверьте статус задания.', 400)
        values['dueAt'] = _due_at(body['dueAt']) if 'dueAt' in body else assignment['dueAt']
        stage = _stage(db, app_id, 'test:' + assignment['id'])
        documents = _documents(body['documents']) if 'documents' in body else stage['documents']
        if all(assignment[key] == value for key, value in values.items()) and documents == stage['documents']:
            return False
        stamp = iso(now())
        db.execute('''UPDATE cp_test_assignments SET title=?,instructions=?,status=?,result=?,note=?,due_at=?,updated_at=?
          WHERE application_id=? AND id=?''', (values['title'], values['instructions'], values['status'], values['result'],
          values['note'], values['dueAt'], stamp, app_id, assignment['id']))
        db.execute('''UPDATE cp_employer_stages SET documents=?,updated_at=? WHERE application_id=? AND stage_id=?''',
                   (_json(documents), stamp, app_id, stage['id']))
    elif action == 'stage.update':
        stage = _stage(db, app_id, body.get('stageId'))
        require('notes' in body or 'documents' in body, 'Укажите изменения этапа.', 400)
        notes = text(body['notes'], 12000, required=False) if 'notes' in body else stage['notes']
        documents = _documents(body['documents']) if 'documents' in body else stage['documents']
        if notes == stage['notes'] and documents == stage['documents']:
            return False
        stamp = iso(now())
        if stage['kind'] == 'test':
            db.execute('UPDATE cp_test_assignments SET note=?,updated_at=? WHERE application_id=? AND id=?',
                       (notes, stamp, app_id, stage['test']['id']))
        db.execute('''UPDATE cp_employer_stages SET notes=?,documents=?,updated_at=?
          WHERE application_id=? AND stage_id=?''',
          ('' if stage['kind'] == 'test' else notes, _json(documents), stamp, app_id, stage['id']))
    elif action == 'chat':
        mid = _message(db, app_id, 'manager' if actor == 'manager' else actor_id,
                       actor_id, text(body.get('text'), 2000))
        events.append(_event(info, 'chat.message', mid,
                             audience='candidate' if actor == 'manager' else 'employer'))
    elif action == 'feedback.questions':
        meeting = _completed_meeting(db, app_id, body.get('meetingId'))
        words = text(body.get('questions'), 12000)
        turns = [{'text': line, 'seconds': 0} for line in words.splitlines() if line.strip()]
        require(0 < len(turns) <= 30, 'Укажите до 30 вопросов, каждый с новой строки.', 400)
        q = questionnaire(turns, meeting['id'], manual=True)
        require(q is not None, 'Запишите вопрос целиком.', 400)
        existing = _feedback(db, app_id, meeting['id'])
        if existing:
            require(existing['questionnaire'] == q, 'Вопросы уже подготовлены.', 409)
            return False
        db.execute('''INSERT INTO cp_meeting_feedback
          (meeting_id,application_id,questionnaire,updated_at) VALUES (?,?,?,?)''',
          (meeting['id'], app_id, _json(q), iso(now())))
    elif action in ('feedback', 'feedback.draft'):
        meeting = _completed_meeting(db, app_id, body.get('meetingId'))
        record = _feedback(db, app_id, meeting['id'])
        require(record and record['questionnaire']['id'] == body.get('questionnaireId'),
                'Вопросы изменились. Обновите кабинет.', 409)
        q = record['questionnaire']
        checked = _answers(body.get('answers'), q['questions'], complete=action == 'feedback')
        if record['submitted']:
            require(record['submitted']['answers'] == checked, 'Обратная связь уже сохранена.', 409)
            return False
        if action == 'feedback.draft':
            if record['draft'] == checked:
                return False
            db.execute('UPDATE cp_meeting_feedback SET draft=?,updated_at=? WHERE meeting_id=?',
                       (_json(checked), iso(now()), meeting['id']))
        else:
            submitted = {'meetingId': meeting['id'], 'questionnaireId': q['id'],
                         'questions': q['questions'], 'answers': checked, 'at': iso(now())}
            db.execute("UPDATE cp_meeting_feedback SET submitted=?,submitted_by=?,draft='{}',updated_at=? WHERE meeting_id=?",
                       (_json(submitted), actor_id, iso(now()), meeting['id']))
    elif action == 'after':
        meeting = _completed_meeting(db, app_id, body.get('meetingId'))
        require(_meeting(db, app_id)['id'] == meeting['id'], 'После этой встречи уже назначена новая. Обновите кабинет.', 409)
        value = body.get('value')
        require(value in ('yes', 'no'), 'Выберите ответ.', 400)
        if state['afterInterest'] == value and state['afterMeetingId'] == meeting['id']:
            return False
        require(not offer or offer['status'] not in ('sent', 'accepted'), 'Ответьте на полученный оффер.', 409)
        _ensure(db, app_id)
        db.execute('''UPDATE cp_employer_workflows SET after_interest=?,after_meeting_id=?
          WHERE application_id=?''', (value, meeting['id'], app_id))
        mid = _message(db, app_id, 'system', actor_id,
                      'Кандидат хочет продолжить.' if value == 'yes' else 'Кандидат не хочет продолжать.', system=True)
        events.append(_event(info, 'candidate.after', mid, audience='employer'))
    elif action == 'decision':
        value = body.get('value')
        require(value in ('pool', 'declined'), 'Выберите решение.', 400)
        if state['decision'] == value:
            return False
        _open(db, info)
        _require_no_active_meeting(db, app_id)
        require(not offer or offer['status'] == 'draft', 'По кандидату уже отправлен оффер.', 409)
        _ensure(db, app_id)
        db.execute('UPDATE cp_employer_workflows SET decision=? WHERE application_id=?', (value, app_id))
        if value == 'declined':
            mid = _message(db, app_id, 'system', actor_id,
                           'Компания решила не продолжать подбор с вами по этой вакансии.', system=True)
            events.append(_event(info, 'employer.declined', mid))
    elif action == 'reject':
        answers = rejection_answers(body['rejectionAnswers']) if 'rejectionAnswers' in body else None
        if state['decision'] == 'declined':
            if answers is not None:
                saved = review_rejection(db, app_id)
                require(saved and all(saved[key] == value for key, value in answers.items()),
                        'Причина отказа уже сохранена.', 409)
            return False
        if answers is not None:
            _open(db, info)
        require(not db.execute("""SELECT 1 FROM cp_meetings WHERE application_id=?
          AND status IN ('pending','confirmed','live') LIMIT 1""", (app_id,)).fetchone(),
          'Сначала завершите или отмените назначенную встречу.', 409)
        require(not offer or offer['status'] != 'accepted', 'Кандидат уже принял оффер.', 409)
        require(_closed(db, info) != info['candidateId'], 'Кандидат уже принят по этой вакансии.', 409)
        if answers is not None:
            db.execute('''INSERT INTO cp_review_rejections(application_id,answers,created_by,created_at)
              VALUES (?,?,?,?)''', (app_id, _json(answers), actor_id, iso(now())))
        withdrawn = bool(offer and offer['status'] in ('draft', 'sent'))
        if withdrawn:
            db.execute("UPDATE cp_offer_versions SET status='withdrawn' WHERE id=?", (offer['id'],))
        _ensure(db, app_id)
        db.execute("UPDATE cp_employer_workflows SET decision='declined' WHERE application_id=?", (app_id,))
        mid = _message(db, app_id, 'system', actor_id,
                       'Компания решила не продолжать подбор с вами по этой вакансии.', system=True)
        events.append(_event(info, 'offer.withdrawn' if withdrawn and offer['status'] == 'sent' else 'employer.declined',
                             offer['id'] if withdrawn and offer['status'] == 'sent' else mid))
    elif action == 'offer.save':
        _open(db, info)
        _require_no_active_meeting(db, app_id)
        require(not offer or offer['status'] in ('draft', 'sent'), 'На оффер уже получен ответ.', 409)
        previous_documents = _stage(db, app_id, 'offer:' + offer['id'])['documents'] if offer else []
        documents = _documents(body['documents']) if 'documents' in body else previous_documents
        terms = {key: text(body.get(key), limit) for key, limit in
                 (('role', 240), ('compensation', 240), ('format', 240), ('expectations', 12000))}
        date = text(body.get('startDate'), 10)
        try:
            parsed = dt.date.fromisoformat(date)
        except ValueError:
            raise Problem('Проверьте дату выхода.', 400)
        require(parsed >= now().astimezone(dt.timezone(dt.timedelta(hours=3))).date(), 'Дата выхода уже прошла.', 400)
        terms['startDate'] = date
        if offer:
            _version(body, offer)
            if all(offer[k] == v for k, v in terms.items()) and documents == previous_documents:
                return False
            db.execute("UPDATE cp_offer_versions SET status='superseded' WHERE id=?", (offer['id'],))
        else:
            require(body.get('version') is None or (type(body['version']) is int and body['version'] == 0),
                    'Условия изменились. Обновите кабинет.', 409)
        oid, status = _id('offer'), offer['status'] if offer else 'draft'
        try:
            db.execute('''INSERT INTO cp_offer_versions
              (id,application_id,vacancy_id,version,terms,status,created_by,created_at,sent_at)
              VALUES (?,?,?,?,?,?,?,?,?)''',
              (oid, app_id, info['vacancyId'], offer['version'] + 1 if offer else 1,
               _json(terms), status, actor_id, iso(now()), iso(now()) if status == 'sent' else None))
        except sqlite3.IntegrityError:
            raise Problem('Сначала дождитесь ответа на текущий оффер.', 409)
        # Explicitly saving an offer is the decision to proceed. Persist that
        # transition atomically with its validated draft, not as another UI gate.
        _ensure(db, app_id)
        db.execute("UPDATE cp_employer_workflows SET decision='pool' WHERE application_id=?", (app_id,))
        register_stages(db, app_id)
        db.execute('UPDATE cp_employer_stages SET documents=? WHERE application_id=? AND stage_id=?',
                   (_json(documents), app_id, 'offer:' + oid))
        if status == 'sent':
            _message(db, app_id, 'system', actor_id,
                     'Работодатель обновил условия оффера. Проверьте новую версию.', system=True)
            events.append(_event(info, 'offer.updated', oid))
    elif action == 'offer.send':
        require(offer is not None, 'Сначала подготовьте оффер.', 409)
        _version(body, offer)
        if offer['status'] in ('sent', 'accepted'):
            return False
        _open(db, info)
        require(state['decision'] == 'pool' and offer['status'] == 'draft', 'Сначала подготовьте оффер.', 409)
        _require_no_active_meeting(db, app_id)
        require(_current_after_interest(state, _meeting(db, app_id)) != 'no',
                'Кандидат не хочет продолжать. Выберите другого.', 409)
        try:
            db.execute("UPDATE cp_offer_versions SET status='sent',sent_at=? WHERE id=?",
                       (iso(now()), offer['id']))
        except sqlite3.IntegrityError:
            raise Problem('Сначала дождитесь ответа на текущий оффер.', 409)
        _message(db, app_id, 'system', actor_id,
                 'Вы получили оффер. Можно принять, отклонить или обсудить условия.', system=True)
        events.append(_event(info, 'offer.sent', offer['id']))
    elif action == 'offer.reply':
        require(offer is not None, 'Нет оффера для ответа.', 409)
        _version(body, offer)
        value = body.get('value')
        require(value in ('accepted', 'declined'), 'Выберите ответ.', 400)
        if offer['status'] == value and offer['answeredBy'] == actor_id:
            return False
        require(offer['status'] == 'sent', 'На оффер уже получен ответ.', 409)
        _open(db, info)
        stamp = iso(now())
        db.execute('UPDATE cp_offer_versions SET status=?,answered_at=?,answered_by=? WHERE id=?',
                   (value, stamp, actor_id, offer['id']))
        if value == 'accepted':
            db.execute('INSERT INTO cp_vacancy_outcomes VALUES (?,?,?,?,?)',
                       (info['vacancyId'], app_id, actor_id, offer['id'], stamp))
            # Merge only outcome fields; never replace the canonical vacancy body.
            vacancy = copy.deepcopy(info['vacancy'])
            vacancy.update(status='closed', closedBy=actor_id, closedByApplication=app_id)
            db.execute('UPDATE cp_vacancies SET body=? WHERE id=?', (_json(vacancy), info['vacancyId']))
        _message(db, app_id, 'system', actor_id,
                 'Оффер принят. Подбор завершён.' if value == 'accepted' else 'Кандидат отклонил оффер.', system=True)
        events.append(_event(info, 'offer.' + value, offer['id'], audience='employer'))
    return True


def _ensure(db, app_id):
    db.execute('''INSERT INTO cp_employer_workflows(application_id,updated_at) VALUES (?,?)
      ON CONFLICT(application_id) DO NOTHING''', (app_id, iso(now())))


def _require_no_active_meeting(db, app_id):
    require(not db.execute("""SELECT 1 FROM cp_meetings WHERE application_id=?
      AND status IN ('pending','confirmed','live') LIMIT 1""", (app_id,)).fetchone(),
      'Сначала завершите или отмените назначенную встречу.', 409)


def _version(body, offer):
    require(type(body.get('version')) is int and body['version'] == offer['version'],
            'Условия изменились. Прочитайте новую версию.', 409)
