"""Scoped immutable assessment projections and private fragment feedback.

No model/provider calls and no public fixture data. Operators bind an already
reviewed projection to exact persisted interview and vacancy content. Replacing
that source never moves an old assessment or feedback onto the new recording.
"""
import hashlib
import json
import math
import re
import secrets

from domain import iso, now, require, text
import employer_store as store


_HEX = re.compile(r'^[a-f0-9]{64}$')
_ID = re.compile(r'^[^\x00-\x20\x7f]{1,240}$')
_STATES = {'supported', 'unknown', 'conflict', 'not_applicable'}
_BASIS = {'observed', 'narrated', 'hypothetical', 'reflected'}
_VACANCY_FIELDS = ('id', 'title', 'role', 'company', 'description', 'problem', 'expectations',
                   'details', 'salary', 'format', 'city', 'remote')


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_dump(value).encode()).hexdigest()


def _identifier(value):
    require(isinstance(value, str) and _ID.fullmatch(value), 'Проверьте идентификатор оценки.', 400)
    return value


def init(db):
    """Startup only. No DDL runs while reading or saving feedback."""
    db.execute('''CREATE TABLE IF NOT EXISTS ia_assessment_revisions (
      workspace_id TEXT NOT NULL REFERENCES em_workspaces(id),
      source_key TEXT NOT NULL, revision_id TEXT NOT NULL, source_fingerprint TEXT NOT NULL,
      body TEXT NOT NULL, body_hash TEXT NOT NULL, created_at TEXT NOT NULL,
      PRIMARY KEY(workspace_id,source_key,revision_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS ia_assessment_feedback (
      workspace_id TEXT NOT NULL, source_key TEXT NOT NULL, revision_id TEXT NOT NULL,
      account_id TEXT NOT NULL REFERENCES em_accounts(id), criterion_id TEXT NOT NULL,
      evidence_id TEXT NOT NULL, answer TEXT CHECK(answer IN ('yes','no','unknown') OR answer IS NULL),
      comment TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0), updated_at TEXT NOT NULL,
      PRIMARY KEY(workspace_id,source_key,revision_id,account_id,criterion_id,evidence_id),
      FOREIGN KEY(workspace_id,source_key,revision_id)
        REFERENCES ia_assessment_revisions(workspace_id,source_key,revision_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS ia_assessment_feedback_events (
      id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, source_key TEXT NOT NULL,
      revision_id TEXT NOT NULL, account_id TEXT NOT NULL REFERENCES em_accounts(id),
      criterion_id TEXT NOT NULL, evidence_id TEXT NOT NULL,
      previous_version INTEGER NOT NULL, version INTEGER NOT NULL,
      body TEXT NOT NULL, request_id TEXT NOT NULL, created_at TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS ia_assessment_commands (
      workspace_id TEXT NOT NULL REFERENCES em_workspaces(id),
      account_id TEXT NOT NULL REFERENCES em_accounts(id), request_id TEXT NOT NULL,
      payload_hash TEXT NOT NULL, source_key TEXT NOT NULL, revision_id TEXT NOT NULL,
      created_at TEXT NOT NULL, PRIMARY KEY(workspace_id,account_id,request_id))''')


def source_key(source, body):
    if source == 'intake':
        return _dump(['intake', body['vacancyId'], body['id'], body['index']])
    return _dump([source, body['applicationId'], body['meetingId'], body['recordingId']])


def recording_fingerprint(profile, index, vacancy=None):
    """Source + transcript + substantive vacancy content, never a client identity.

    `vacancy` must be the server's canonical store.vacancy result in production.
    It is optional only for callers inspecting a source before creating its scope.
    """
    require(isinstance(profile, dict) and type(index) is int and index == 0,
            'Расшифровка не сопоставлена с этой частью записи.', 422)
    recordings = profile.get('recordings')
    require(isinstance(recordings, list) and len(recordings) == 1 and isinstance(recordings[0], dict),
            'Расшифровка не сопоставлена с этой частью записи.', 422)
    transcript = profile.get('transcriptText')
    require(isinstance(transcript, str) and bool(transcript.strip()) and len(transcript) <= 1000000,
            'Расшифровка интервью недоступна.', 422)
    recording = recordings[0]
    return _digest({
        'recording': {key: recording.get(key) for key in
                      ('id', 'publicUrl', 'mime', 'size', 'sha256', 'checksum', 'version',
                       'source', 'sourceCandidateId', 'duration', 'durationSeconds')},
        'transcript': hashlib.sha256(transcript.encode()).hexdigest(),
        'transcriptSource': profile.get('transcriptSource'), 'source': profile.get('source'),
        'vacancy': {key: vacancy.get(key) for key in _VACANCY_FIELDS} if vacancy is not None else None,
    })


def intake_binding(db, workspace_id, body, *, allow_missing_transcript=False):
    """Resolve the same authenticated source scope as interview-assistant."""
    import employer_intake as intake
    require(set(body) == {'source', 'vacancyId', 'id', 'index'} and body['source'] == 'intake',
            'Проверьте источник интервью.', 400)
    require(isinstance(body['vacancyId'], str) and bool(body['vacancyId']) and
            isinstance(body['id'], str) and re.fullmatch(r'[a-f0-9]{24}', body['id']),
            'Кандидат не найден.', 404)
    require(type(body['index']) is int and 0 <= body['index'] < 10, 'Выберите запись.', 400)
    person, _ = intake._review_person(db, workspace_id, body['vacancyId'], body)
    recording = intake._stored_recording(person, body['index'])
    vacancy = store.vacancy(db, workspace_id, body['vacancyId'])
    profile = person.get('preparedProfile') or {}
    transcript = profile.get('transcriptText')
    recordings = profile.get('recordings')
    if allow_missing_transcript and (body['index'] != 0 or not isinstance(recordings, list)
            or len(recordings) != 1 or not isinstance(transcript, str) or not transcript.strip()):
        # Existing multipart playback stays usable. Until a transcript is pinned
        # to an exact part, there is no assessment binding for any of its parts.
        return source_key('intake', body), None, recording
    fingerprint = recording_fingerprint(profile, body['index'], vacancy)
    return source_key('intake', body), fingerprint, recording


def _recheck_binding(db, workspace_id, binding_key, fingerprint):
    """Revalidate inside the write transaction, including operator-side races."""
    parts = json.loads(binding_key)
    actual_key, actual_fingerprint, recording = intake_binding(db, workspace_id,
        {'source': 'intake', 'vacancyId': parts[1], 'id': parts[2], 'index': parts[3]}, allow_missing_transcript=True)
    require(actual_key == binding_key and actual_fingerprint == fingerprint,
            'Материалы интервью или вакансия изменились.', 409)
    return recording


def _checked_assessment(assessment, duration=None):
    require(isinstance(assessment, dict) and {'revisionId', 'criteria'} <= set(assessment) <=
            {'revisionId', 'criteria', 'provenance'}, 'Проверьте данные оценки.', 400)
    _identifier(assessment['revisionId'])
    criteria = assessment['criteria']
    require(isinstance(criteria, list) and 0 < len(criteria) <= 40, 'Проверьте критерии оценки.', 400)
    ids = set()
    for criterion in criteria:
        require(isinstance(criterion, dict), 'Проверьте критерии оценки.', 400)
        cid = _identifier(criterion.get('id'))
        require(cid not in ids, 'Критерии оценки повторяются.', 400)
        ids.add(cid)
        state, level = criterion.get('state'), criterion.get('level')
        require(isinstance(state, str) and state in _STATES and (type(level) is int and 1 <= level <= 5 if state == 'supported' else level is None),
                'Проверьте состояние и уровень оценки.', 400)
        require(criterion.get('target') is None and (criterion.get('basis') is None or
                isinstance(criterion.get('basis'), str) and criterion['basis'] in _BASIS),
                'Проверьте основание оценки.', 400)
        for key in ('label', 'summary', 'nextQuestion', 'stateLabel', 'limitation'):
            text(criterion.get(key, ''), 20000, required=key == 'label')
        evidence = criterion.get('evidence')
        require(isinstance(evidence, list) and len(evidence) <= 60, 'Проверьте источники оценки.', 400)
        evidence_ids = set()
        for item in evidence:
            require(isinstance(item, dict), 'Проверьте источники оценки.', 400)
            eid = _identifier(item.get('id'))
            require(eid not in evidence_ids, 'Источники оценки повторяются.', 400)
            evidence_ids.add(eid)
            text(item.get('text'), 20000)
            seconds = item.get('seconds')
            require(seconds is None or type(seconds) in (int, float) and math.isfinite(seconds)
                    and 0 <= seconds <= 14400 and (duration is None or seconds <= duration),
                    'Таймкод выходит за пределы записи.', 400)
            end = item.get('endSeconds')
            require(end is None or type(end) in (int, float) and math.isfinite(end) and
                    seconds is not None and seconds <= end <= 14400 and (duration is None or end <= duration),
                    'Таймкод выходит за пределы записи.', 400)
            if item.get('turnIds') is not None:
                require(isinstance(item['turnIds'], list) and len(item['turnIds']) <= 1000 and
                        all(type(turn) is int and 0 <= turn <= 100000 for turn in item['turnIds']),
                        'Проверьте реплики источника.', 400)
    try:
        encoded = _dump(assessment)
    except (TypeError, ValueError):
        require(False, 'Проверьте данные оценки.', 400)
    require(len(encoded.encode()) <= 2 * 1024 * 1024, 'Оценка слишком большая.', 413)
    return encoded


def import_assessment(db, workspace_id, binding_key, fingerprint, assessment):
    """Operator-only immutable import; there is deliberately no HTTP import route.

    Re-resolves the stored person/recording/vacancy, so a prepared projection
    cannot be accidentally bound under another employer or a changed vacancy.
    Caller owns commit/rollback; this function creates no candidate/application.
    """
    require(isinstance(fingerprint, str) and _HEX.fullmatch(fingerprint), 'Проверьте источник оценки.', 400)
    try:
        parts = json.loads(binding_key)
    except (ValueError, TypeError):
        parts = None
    require(isinstance(parts, list) and len(parts) == 4 and parts[0] == 'intake', 'Проверьте источник оценки.', 400)
    body = {'source': 'intake', 'vacancyId': parts[1], 'id': parts[2], 'index': parts[3]}
    actual_key, actual_fingerprint, recording = intake_binding(db, workspace_id, body)
    require(actual_key == binding_key and actual_fingerprint == fingerprint,
            'Материалы интервью или вакансия изменились.', 409)
    duration = recording.get('duration', recording.get('durationSeconds'))
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
        duration = None
    encoded = _checked_assessment(assessment, duration)
    digest, revision = hashlib.sha256(encoded.encode()).hexdigest(), assessment['revisionId']
    with store._atomic(db):
        _recheck_binding(db, workspace_id, binding_key, fingerprint)
        old = db.execute('''SELECT source_fingerprint,body_hash FROM ia_assessment_revisions
          WHERE workspace_id=? AND source_key=? AND revision_id=?''', (workspace_id, binding_key, revision)).fetchone()
        if old:
            require(tuple(old) == (fingerprint, digest), 'Эта ревизия оценки уже использована.', 409)
            return {'revisionId': revision, 'replayed': True}
        db.execute('INSERT INTO ia_assessment_revisions VALUES (?,?,?,?,?,?,?)',
                   (workspace_id, binding_key, revision, fingerprint, encoded, digest, iso(now())))
    return {'revisionId': revision, 'replayed': False}


def _current(db, workspace_id, binding_key, fingerprint):
    row = db.execute('''SELECT source_fingerprint,body FROM ia_assessment_revisions
      WHERE workspace_id=? AND source_key=? ORDER BY rowid DESC LIMIT 1''', (workspace_id, binding_key)).fetchone()
    # No fallback to an old assessment after a source replacement or vacancy edit.
    return json.loads(row[1]) if row and row[0] == fingerprint else None


def _feedback(db, principal, binding_key, revision):
    rows = db.execute('''SELECT criterion_id,evidence_id,answer,comment,version,updated_at
      FROM ia_assessment_feedback WHERE workspace_id=? AND source_key=? AND revision_id=?
      AND account_id=? ORDER BY criterion_id,evidence_id''',
      (principal['workspaceId'], binding_key, revision, principal['accountId']))
    return [{'criterionId': row[0], 'evidenceId': row[1], **({'answer': row[2]} if row[2] is not None else {}),
             'comment': row[3], 'version': row[4], 'updatedAt': row[5]} for row in rows]


def _saved_feedback(db, principal, binding_key, body):
    return next(item for item in _feedback(db, principal, binding_key, body['revisionId'])
                if item['criterionId'] == body['criterionId'] and item['evidenceId'] == body['evidenceId'])


def read(db, principal, binding_key, fingerprint):
    value = _current(db, principal['workspaceId'], binding_key, fingerprint)
    return {'assessment': {**value, 'feedback': _feedback(db, principal, binding_key, value['revisionId'])} if value else None}


def save_feedback(db, principal, binding_key, fingerprint, body):
    required = {'revisionId', 'criterionId', 'evidenceId', 'comment', 'expectedVersion', 'requestId'}
    require(isinstance(body, dict) and required <= set(body) <= required | {'answer'},
            'Проверьте поля обратной связи.', 400)
    for key in ('revisionId', 'criterionId', 'evidenceId', 'requestId'):
        _identifier(body[key])
    answer = body.get('answer')
    require(answer is None or isinstance(answer, str) and answer in {'yes', 'no', 'unknown'}, 'Выберите ответ.', 400)
    comment = text(body['comment'], 4000, required=False)
    require(not any((ord(ch) < 32 and ch not in '\n\r\t') or ord(ch) == 127 for ch in comment),
            'Проверьте текст отзыва.', 400)
    version = body['expectedVersion']
    require(type(version) is int and 0 <= version < 2 ** 31, 'Обновите отзыв перед сохранением.', 400)
    wid, account = principal['workspaceId'], principal['accountId']
    normalized = {**body, 'answer': answer, 'comment': comment}
    digest = _digest({'source': binding_key, 'fingerprint': fingerprint, 'feedback': normalized})
    with store._atomic(db):
        _recheck_binding(db, wid, binding_key, fingerprint)
        current = _current(db, wid, binding_key, fingerprint)
        require(current and current['revisionId'] == body['revisionId'],
                'Оценка или материалы изменились. Откройте оценку ещё раз.', 409)
        criterion = next((item for item in current['criteria'] if item['id'] == body['criterionId']), None)
        require(criterion is not None and any(item['id'] == body['evidenceId'] for item in criterion['evidence']),
                'Фрагмент оценки не найден.', 404)
        previous = db.execute('''SELECT payload_hash FROM ia_assessment_commands
          WHERE workspace_id=? AND account_id=? AND request_id=?''', (wid, account, body['requestId'])).fetchone()
        if previous:
            require(previous[0] == digest, 'Этот запрос уже использован для другого отзыва.', 409)
            return {'feedback': _saved_feedback(db, principal, binding_key, body)}
        key = (wid, binding_key, body['revisionId'], account, body['criterionId'], body['evidenceId'])
        previous = db.execute('''SELECT version FROM ia_assessment_feedback
          WHERE workspace_id=? AND source_key=? AND revision_id=? AND account_id=?
          AND criterion_id=? AND evidence_id=?''', key).fetchone()
        actual = previous[0] if previous else 0
        require(actual == version, 'Отзыв уже изменён. Обновите оценку.', 409)
        stamp, next_version = iso(now()), actual + 1
        db.execute('''INSERT INTO ia_assessment_feedback
          (workspace_id,source_key,revision_id,account_id,criterion_id,evidence_id,answer,comment,version,updated_at)
          VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace_id,source_key,revision_id,account_id,criterion_id,evidence_id)
          DO UPDATE SET answer=excluded.answer,comment=excluded.comment,version=excluded.version,updated_at=excluded.updated_at''',
          (*key, answer, comment, next_version, stamp))
        event = {'answer': answer, 'comment': comment}
        db.execute('INSERT INTO ia_assessment_feedback_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
          (secrets.token_hex(16), wid, binding_key, body['revisionId'], account, body['criterionId'], body['evidenceId'],
           actual, next_version, _dump(event), body['requestId'], stamp))
        db.execute('INSERT INTO ia_assessment_commands VALUES (?,?,?,?,?,?,?)',
                   (wid, account, body['requestId'], digest, binding_key, body['revisionId'], stamp))
        return {'feedback': _saved_feedback(db, principal, binding_key, body)}
