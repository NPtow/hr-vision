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
import secrets
import sqlite3

from domain import Problem, iso, now, require, text
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
    row = db.execute('''SELECT a.id,a.candidate_id,a.vacancy_id,a.status,v.body
      FROM cp_applications a JOIN cp_vacancies v ON v.id=a.vacancy_id WHERE a.id=?''',
      (application_id,)).fetchone()
    require(row is not None and (candidate_id is None or row[1] == candidate_id)
            and (vacancy_id is None or row[2] == vacancy_id), 'Вакансия не найдена.', 404)
    return {'applicationId': row[0], 'candidateId': row[1], 'vacancyId': row[2],
            'interest': row[3], 'vacancy': json.loads(row[4])}


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
    state = {key: info[key] for key in ('applicationId', 'candidateId', 'vacancyId', 'interest')}
    state.update(_workflow(db, application_id))
    if _review_rejected(db, application_id):
        state['decision'] = 'declined'
    state['closedBy'] = _closed(db, info)
    state['meeting'] = _meeting(db, application_id)
    if state['afterMeetingId'] and (not state['meeting'] or state['afterMeetingId'] != state['meeting']['id']):
        state['afterInterest'] = 'pending'
    record = _feedback(db, application_id, state['meeting']['id']) if state['meeting'] else None
    state['feedback'] = record['submitted'] if record else None
    if state['meeting'] and record and actor == 'manager':
        state['meeting']['questionnaire'] = record['questionnaire']
        if not record['submitted']:
            state['meeting']['feedbackDraft'] = record['draft']
    state['offer'] = _current_offer(db, application_id)
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
        identity(db, application_id, vacancy_id=vacancy_id)
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


MANAGER_ACTIONS = {'feedback.questions', 'feedback.draft', 'feedback', 'decision', 'reject', 'offer.save', 'offer.send', 'chat'}
CANDIDATE_ACTIONS = {'after', 'offer.reply', 'chat'}
ALIASES = {'feedback.submit': 'feedback', 'offer.draft': 'offer.save', 'after.interest': 'after'}


def manager_action(db, application_id, action, body, *, manager_id, vacancy_id=None):
    require(isinstance(manager_id, str) and bool(manager_id), 'Войдите в кабинет работодателя.', 401)
    return _action(db, application_id, action, body, actor='manager', actor_id=manager_id,
                   vacancy_id=vacancy_id)


def candidate_action(db, candidate_id, application_id, action, body):
    require(isinstance(candidate_id, str) and bool(candidate_id), 'Войдите в свой кабинет.', 401)
    return _action(db, application_id, action, body, actor='candidate', actor_id=candidate_id)


def reject(db, application_id, *, manager_id, vacancy_id=None, request_id=None):
    """Explicit early refusal, separate from the feedback-gated pool decision.

    The adapter cancels pending/confirmed meetings before calling this helper and
    updates employer_store visibility afterwards, all in the same transaction.
    A live meeting and an accepted offer cannot be withdrawn by this operation.
    """
    return manager_action(db, application_id, 'reject',
                          {'requestId': request_id} if request_id is not None else {},
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
    fingerprint = hashlib.sha256(_json({'action': action, 'body': {
        k: v for k, v in body.items() if k != 'requestId'}}).encode()).hexdigest()
    events = []
    with _atomic(db):
        info = identity(db, app_id, candidate_id=actor_id if actor == 'candidate' else None,
                        vacancy_id=vacancy_id)
        if rid is not None:
            old = db.execute('''SELECT fingerprint FROM cp_workflow_commands
              WHERE application_id=? AND actor_key=? AND request_id=?''', (app_id, actor_key, rid)).fetchone()
            if old:
                require(old[0] == fingerprint, 'Этот запрос уже использован для другого действия.', 409)
                return {'state': load(db, app_id, actor=actor, candidate_id=actor_id if actor == 'candidate' else None),
                        'events': [], 'replayed': True}
        state = _workflow(db, app_id)
        hidden = _review_rejected(db, app_id)
        if 'expectedRevision' in body:
            require(type(body['expectedRevision']) is int and body['expectedRevision'] == state['revision'],
                    'Данные уже изменились. Обновите кабинет.', 409)
        require(info['interest'] == 'accepted', 'Сначала кандидат должен согласовать вакансию.', 409)
        require((state['decision'] != 'declined' and not hidden) or action == 'reject'
                or (action == 'decision' and body.get('value') == 'declined'),
                'Компания завершила работу с этим кандидатом.', 409)
        changed = _apply(db, info, state, action, body, actor, actor_id, events)
        if changed:
            _touch(db, app_id)
        if rid is not None:
            db.execute('INSERT INTO cp_workflow_commands VALUES (?,?,?,?,?)',
                       (app_id, actor_key, rid, fingerprint, iso(now())))
        return {'state': load(db, app_id, actor=actor, candidate_id=actor_id if actor == 'candidate' else None),
                'events': events, 'replayed': not changed}


def _apply(db, info, state, action, body, actor, actor_id, events):
    app_id = info['applicationId']
    offer = _current_offer(db, app_id)
    if action == 'chat':
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
        _require_feedback(db, app_id)
        require(not offer or offer['status'] == 'draft', 'По кандидату уже отправлен оффер.', 409)
        _ensure(db, app_id)
        db.execute('UPDATE cp_employer_workflows SET decision=? WHERE application_id=?', (value, app_id))
        if value == 'declined':
            mid = _message(db, app_id, 'system', actor_id,
                           'Компания решила не продолжать подбор с вами по этой вакансии.', system=True)
            events.append(_event(info, 'employer.declined', mid))
    elif action == 'reject':
        if state['decision'] == 'declined':
            return False
        require(not db.execute("""SELECT 1 FROM cp_meetings WHERE application_id=?
          AND status IN ('pending','confirmed','live') LIMIT 1""", (app_id,)).fetchone(),
          'Сначала завершите или отмените назначенную встречу.', 409)
        require(not offer or offer['status'] != 'accepted', 'Кандидат уже принял оффер.', 409)
        require(_closed(db, info) != info['candidateId'], 'Кандидат уже принят по этой вакансии.', 409)
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
        require(state['decision'] == 'pool', 'Сначала добавьте кандидата в пул.', 409)
        _require_feedback(db, app_id)
        require(not offer or offer['status'] in ('draft', 'sent'), 'На оффер уже получен ответ.', 409)
        terms = {k: text(body.get(k), 1500) for k in ('role', 'compensation', 'format', 'expectations')}
        date = text(body.get('startDate'), 10)
        try:
            parsed = dt.date.fromisoformat(date)
        except ValueError:
            raise Problem('Проверьте дату выхода.', 400)
        require(parsed >= now().astimezone(dt.timezone(dt.timedelta(hours=3))).date(), 'Дата выхода уже прошла.', 400)
        terms['startDate'] = date
        if offer:
            _version(body, offer)
            if all(offer[k] == v for k, v in terms.items()):
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
        _require_feedback(db, app_id)
        require(state['afterInterest'] != 'no', 'Кандидат не хочет продолжать. Выберите другого.', 409)
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


def _require_feedback(db, app_id):
    require(not db.execute("""SELECT 1 FROM cp_meetings WHERE application_id=?
      AND status IN ('pending','confirmed','live') LIMIT 1""", (app_id,)).fetchone(),
      'Сначала завершите или отмените назначенную встречу.', 409)
    meeting = _meeting(db, app_id)
    record = _feedback(db, app_id, meeting['id']) if meeting and meeting['status'] == 'completed' else None
    require(record and record['submitted'], 'Сначала сохраните фидбек по завершённой встрече.', 409)


def _version(body, offer):
    require(type(body.get('version')) is int and body['version'] == offer['version'],
            'Условия изменились. Прочитайте новую версию.', 409)
