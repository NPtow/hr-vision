"""Workspace ownership and employer review metadata over the canonical cp_* rows.

No matching happens here. A batch carries candidate IDs, order and reasons supplied
by its explicitly identified producer. Callers authenticate the workspace and own
the surrounding SQLite transaction; every multi-row mutation also has a savepoint.
"""
from contextlib import contextmanager
import hashlib
import json
import secrets

from domain import Problem, iso, now, require, text

TEAM_WORKSPACE = 'hr-vision-team'
VACANCY_FIELDS = {'title', 'role', 'company', 'city', 'remote', 'description',
                  'problem', 'salary', 'format', 'expectations'}
PROFILE_FIELDS = {'name', 'city', 'desiredRole', 'about', 'geographies', 'availability',
                  'desiredSalary', 'workMode', 'salaryMin', 'salaryMax', 'latestStartDate',
                  'residence', 'employmentType', 'consentReuse', 'autoApprove'}


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def _uid(prefix):
    return prefix + '_' + secrets.token_hex(12)


@contextmanager
def _atomic(db):
    # Releasing a top-level SQLite savepoint commits. Keep a caller-owned outer
    # transaction even when this is the first mutation on the connection.
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    name = 'em_' + secrets.token_hex(8)
    db.execute('SAVEPOINT ' + name)
    try:
        yield
    except BaseException:
        db.execute('ROLLBACK TO ' + name)
        db.execute('RELEASE ' + name)
        raise
    else:
        db.execute('RELEASE ' + name)


def init(db):
    """Run after CandidatePortal.init and employer_access.init; no implicit commit."""
    statements = [
        '''CREATE TABLE IF NOT EXISTS em_vacancies (
          vacancy_id TEXT PRIMARY KEY REFERENCES cp_vacancies(id),
          workspace_id TEXT NOT NULL REFERENCES em_workspaces(id),
          generation TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
          archived_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''',
        '''CREATE INDEX IF NOT EXISTS em_vacancies_workspace
          ON em_vacancies(workspace_id,created_at)''',
        '''CREATE TABLE IF NOT EXISTS em_batches (
          id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES em_workspaces(id),
          vacancy_id TEXT NOT NULL REFERENCES cp_vacancies(id),
          source TEXT NOT NULL CHECK(source IN ('core','recruiter')),
          source_version TEXT NOT NULL, external_batch_id TEXT NOT NULL,
          payload_hash TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL,
          UNIQUE(workspace_id,vacancy_id,source,external_batch_id))''',
        '''CREATE TABLE IF NOT EXISTS em_reviews (
          application_id TEXT PRIMARY KEY REFERENCES cp_applications(id),
          workspace_id TEXT NOT NULL REFERENCES em_workspaces(id),
          state TEXT NOT NULL CHECK(state IN ('proposed','selected','rejected')),
          queue_order INTEGER NOT NULL, skip_count INTEGER NOT NULL DEFAULT 0,
          source TEXT NOT NULL CHECK(source IN ('core','recruiter')),
          source_version TEXT NOT NULL, batch_id TEXT REFERENCES em_batches(id),
          rank INTEGER, reason TEXT NOT NULL, selected_at TEXT, rejected_at TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''',
        '''CREATE INDEX IF NOT EXISTS em_reviews_workspace_order
          ON em_reviews(workspace_id,state,queue_order)''',
        '''CREATE TABLE IF NOT EXISTS em_batch_items (
          batch_id TEXT NOT NULL REFERENCES em_batches(id),
          candidate_id TEXT NOT NULL REFERENCES cp_candidates(id),
          application_id TEXT NOT NULL REFERENCES cp_applications(id),
          rank INTEGER NOT NULL, reason TEXT NOT NULL,
          outcome TEXT NOT NULL CHECK(outcome IN ('added','existing')),
          PRIMARY KEY(batch_id,candidate_id), UNIQUE(batch_id,rank))''',
        '''CREATE TABLE IF NOT EXISTS em_store_commands (
          workspace_id TEXT NOT NULL REFERENCES em_workspaces(id), request_id TEXT NOT NULL,
          kind TEXT NOT NULL, payload_hash TEXT NOT NULL, result TEXT NOT NULL,
          created_at TEXT NOT NULL, PRIMARY KEY(workspace_id,request_id))''',
    ]
    for statement in statements:
        db.execute(statement)


def _workspace(db, workspace_id):
    require(isinstance(workspace_id, str) and bool(workspace_id), 'Кабинет не найден.', 404)
    row = db.execute('SELECT name FROM em_workspaces WHERE id=?', (workspace_id,)).fetchone()
    require(row is not None, 'Кабинет не найден.', 404)
    return row[0]


def _vacancy_row(db, workspace_id, vacancy_id, include_archived=False):
    _workspace(db, workspace_id)
    row = db.execute('''SELECT v.body,e.generation,e.revision,e.archived_at,e.created_at,e.updated_at
      FROM cp_vacancies v JOIN em_vacancies e ON e.vacancy_id=v.id
      WHERE v.id=? AND e.workspace_id=?''', (vacancy_id, workspace_id)).fetchone()
    require(row is not None and (include_archived or row[3] is None), 'Вакансия не найдена.', 404)
    return row


def vacancy(db, workspace_id, vacancy_id, include_archived=False):
    row = _vacancy_row(db, workspace_id, vacancy_id, include_archived)
    return {**json.loads(row[0]), 'id': vacancy_id, 'workspaceId': workspace_id,
            'generation': row[1], 'revision': row[2], 'archivedAt': row[3],
            'createdAt': row[4], 'updatedAt': row[5]}


def ensure_open(db, workspace_id, vacancy_id):
    result = vacancy(db, workspace_id, vacancy_id)
    require(not result.get('closedBy') and result.get('status') != 'closed',
            'Вакансия уже закрыта.', 409)
    return result


def list_vacancies(db, workspace_id, include_archived=False):
    _workspace(db, workspace_id)
    ids = db.execute('''SELECT vacancy_id FROM em_vacancies WHERE workspace_id=?
      AND (? OR archived_at IS NULL) ORDER BY created_at DESC,rowid DESC''',
                     (workspace_id, int(include_archived))).fetchall()
    return [vacancy(db, workspace_id, row[0], include_archived) for row in ids]


def _vacancy_body(body, previous=None, company=''):
    require(isinstance(body, dict) and not (set(body) - VACANCY_FIELDS),
            'Проверьте поля вакансии.', 400)
    previous = previous or {}
    result = dict(previous)  # Preserve outcome/status fields owned by the workflow.
    if 'title' in body and 'role' in body:
        require(body['title'] == body['role'], 'Укажите одно название вакансии.', 400)
    if not previous or 'title' in body or 'role' in body:
        title = text(body.get('title', body.get('role', '')), 200)
        result.update(title=title, role=title)
    defaults = {'company': company, 'city': '', 'description': '', 'problem': '',
                'salary': '', 'format': '', 'expectations': ''}
    limits = {'company': 200, 'city': 120, 'description': 10000, 'problem': 4000,
              'salary': 300, 'format': 300, 'expectations': 10000}
    for key, limit in limits.items():
        if key in body or key not in result:
            result[key] = text(body.get(key, defaults[key]), limit, required=key == 'company')
    if 'remote' in body or 'remote' not in result:
        remote = body.get('remote', False)
        require(isinstance(remote, bool), 'Укажите формат вакансии.', 400)
        result['remote'] = remote
    return result


def _command(db, workspace_id, request_id, kind, payload):
    if request_id is None:
        return None, None
    request_id = text(request_id, 160)
    digest = hashlib.sha256(_dump(payload).encode()).hexdigest()
    row = db.execute('''SELECT kind,payload_hash,result FROM em_store_commands
      WHERE workspace_id=? AND request_id=?''', (workspace_id, request_id)).fetchone()
    if row:
        require(row[0] == kind and row[1] == digest,
                'Этот идентификатор запроса уже использован для другого действия.', 409)
        return json.loads(row[2]), digest
    return None, digest


def _remember(db, workspace_id, request_id, kind, digest, result):
    if request_id is not None:
        db.execute('INSERT INTO em_store_commands VALUES (?,?,?,?,?,?)',
                   (workspace_id, request_id.strip(), kind, digest, _dump(result), iso(now())))


def create_vacancy(db, workspace_id, body, request_id=None):
    company = _workspace(db, workspace_id)
    prepared = _vacancy_body(body, company=company)
    with _atomic(db):
        saved, digest = _command(db, workspace_id, request_id, 'vacancy.create', prepared)
        if saved is not None:
            return vacancy(db, workspace_id, saved['vacancyId'], include_archived=True)
        vid, created = _uid('vacancy'), iso(now())
        prepared['id'] = vid
        db.execute('INSERT INTO cp_vacancies VALUES (?,?,?)', (vid, _dump(prepared), created))
        db.execute('INSERT INTO em_vacancies VALUES (?,?,?,?,?,?,?)',
                   (vid, workspace_id, secrets.token_hex(12), 0, None, created, created))
        _remember(db, workspace_id, request_id, 'vacancy.create', digest, {'vacancyId': vid})
        return vacancy(db, workspace_id, vid)


def _check_revision(current, expected_revision):
    if expected_revision is not None:
        require(type(expected_revision) is int and expected_revision >= 0,
                'Некорректная версия вакансии.', 400)
        require(current['revision'] == expected_revision, 'Вакансия изменилась. Обновите страницу.', 409)


def bump_revision(db, workspace_id, vacancy_id):
    _vacancy_row(db, workspace_id, vacancy_id, include_archived=True)
    db.execute('''UPDATE em_vacancies SET revision=revision+1,updated_at=?
      WHERE vacancy_id=? AND workspace_id=?''', (iso(now()), vacancy_id, workspace_id))


def update_vacancy(db, workspace_id, vacancy_id, body, expected_revision=None, request_id=None):
    with _atomic(db):
        current = vacancy(db, workspace_id, vacancy_id)
        payload = {'vacancyId': vacancy_id, 'body': body, 'revision': expected_revision}
        saved, digest = _command(db, workspace_id, request_id, 'vacancy.update', payload)
        if saved is not None:
            return current
        _check_revision(current, expected_revision)
        row = _vacancy_row(db, workspace_id, vacancy_id)
        updated = _vacancy_body(body, json.loads(row[0]))
        if updated != json.loads(row[0]):
            db.execute('UPDATE cp_vacancies SET body=? WHERE id=?', (_dump(updated), vacancy_id))
            bump_revision(db, workspace_id, vacancy_id)
        _remember(db, workspace_id, request_id, 'vacancy.update', digest, {'vacancyId': vacancy_id})
        return vacancy(db, workspace_id, vacancy_id)


def archive_vacancy(db, workspace_id, vacancy_id, expected_revision=None):
    with _atomic(db):
        current = vacancy(db, workspace_id, vacancy_id, include_archived=True)
        if current['archivedAt'] is not None:
            return current
        _check_revision(current, expected_revision)
        active = db.execute('''SELECT 1 FROM cp_meetings m JOIN cp_applications a
          ON a.id=m.application_id WHERE a.vacancy_id=? AND m.status IN ('pending','confirmed','live') LIMIT 1''',
                            (vacancy_id,)).fetchone()
        require(active is None, 'Сначала завершите или отмените назначенные встречи.', 409)
        db.execute('UPDATE em_vacancies SET archived_at=? WHERE vacancy_id=?', (iso(now()), vacancy_id))
        bump_revision(db, workspace_id, vacancy_id)
        return vacancy(db, workspace_id, vacancy_id, include_archived=True)


def adopt_vacancy(db, workspace_id, vacancy_id):
    """Only the existing internal team can own previously unscoped recruiter data."""
    _workspace(db, workspace_id)
    require(workspace_id == TEAM_WORKSPACE, 'Вакансия не найдена.', 404)
    with _atomic(db):
        owner = db.execute('SELECT workspace_id FROM em_vacancies WHERE vacancy_id=?', (vacancy_id,)).fetchone()
        if owner:
            require(owner[0] == workspace_id, 'Вакансия не найдена.', 404)
            return vacancy(db, workspace_id, vacancy_id, include_archived=True)
        row = db.execute('SELECT created_at FROM cp_vacancies WHERE id=?', (vacancy_id,)).fetchone()
        require(row is not None, 'Вакансия не найдена.', 404)
        db.execute('INSERT INTO em_vacancies VALUES (?,?,?,?,?,?,?)',
                   (vacancy_id, workspace_id, secrets.token_hex(12), 0, None, row[0], iso(now())))
        return vacancy(db, workspace_id, vacancy_id)


def _next_order(db, workspace_id, vacancy_id):
    row = db.execute('''SELECT COALESCE(MAX(r.queue_order),0)+1 FROM em_reviews r
      JOIN cp_applications a ON a.id=r.application_id WHERE r.workspace_id=? AND a.vacancy_id=?''',
                     (workspace_id, vacancy_id)).fetchone()
    return row[0]


def _insert_review(db, workspace_id, application_id, *, source, version, batch_id=None, rank=None, reason=''):
    app = db.execute('SELECT vacancy_id FROM cp_applications WHERE id=?', (application_id,)).fetchone()
    require(app is not None, 'Кандидат не найден.', 404)
    _vacancy_row(db, workspace_id, app[0], include_archived=True)
    meeting = db.execute('SELECT id FROM cp_meetings WHERE application_id=?', (application_id,)).fetchone()
    created = iso(now())
    state = 'selected' if meeting else 'proposed'
    db.execute('''INSERT INTO em_reviews (application_id,workspace_id,state,queue_order,source,
      source_version,batch_id,rank,reason,selected_at,created_at,updated_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
               (application_id, workspace_id, state, _next_order(db, workspace_id, app[0]), source,
                version, batch_id, rank, reason, created if meeting else None, created, created))


def adopt_existing(db, workspace_id):
    """Explicit internal-team read migration; never claims another employer's rows."""
    _workspace(db, workspace_id)
    require(workspace_id == TEAM_WORKSPACE, 'Кабинет не найден.', 404)
    with _atomic(db):
        unowned = db.execute('''SELECT v.id FROM cp_vacancies v LEFT JOIN em_vacancies e
          ON e.vacancy_id=v.id WHERE e.vacancy_id IS NULL ORDER BY v.created_at,v.rowid''').fetchall()
        for row in unowned:
            adopt_vacancy(db, workspace_id, row[0])
        applications = db.execute('''SELECT a.id,a.vacancy_id FROM cp_applications a
          JOIN em_vacancies v ON v.vacancy_id=a.vacancy_id LEFT JOIN em_reviews r ON r.application_id=a.id
          WHERE v.workspace_id=? AND r.application_id IS NULL ORDER BY a.created_at,a.rowid''',
                                  (workspace_id,)).fetchall()
        changed = set()
        for aid, vid in applications:
            _insert_review(db, workspace_id, aid, source='recruiter', version='existing-recruiter-v1')
            changed.add(vid)
        for vid in changed:
            bump_revision(db, workspace_id, vid)
        if 'manager_id' in {row[1] for row in db.execute('PRAGMA table_info(cp_meetings)')}:
            db.execute('''UPDATE cp_meetings SET manager_id=? WHERE manager_id='recruiter:team'
              AND application_id IN (SELECT a.id FROM cp_applications a JOIN em_vacancies v
                ON v.vacancy_id=a.vacancy_id WHERE v.workspace_id=?)''',
                       ('workspace:' + workspace_id, workspace_id))
        return {'vacanciesAdopted': len(unowned), 'applicationsAdopted': len(applications)}


def _eligible(db, candidate_id):
    row = db.execute('SELECT profile FROM cp_candidates WHERE id=?', (candidate_id,)).fetchone()
    require(row is not None, 'Кандидат не найден.', 404)
    profile = json.loads(row[0])
    require(profile.get('consentReuse') is True,
            'Кандидат не разрешил использовать интервью для новых вакансий.', 409)
    completed = db.execute("SELECT 1 FROM cp_interviews WHERE candidate_id=? AND status='completed'",
                           (candidate_id,)).fetchone()
    require(completed is not None, 'Сначала завершите первичное интервью.', 409)
    return profile


def _batch_payload(body):
    require(isinstance(body, dict) and set(body) == {'source', 'version', 'externalBatchId', 'candidates'},
            'Проверьте контракт подборки.', 400)
    require(body['source'] in ('core', 'recruiter'), 'Укажите источник подборки.', 400)
    result = {'source': body['source'], 'version': text(body['version'], 120),
              'externalBatchId': text(body['externalBatchId'], 200), 'candidates': []}
    require(isinstance(body['candidates'], list) and len(body['candidates']) <= 100,
            'Подборка должна содержать не больше 100 кандидатов.', 400)
    ids, ranks = set(), set()
    for item in body['candidates']:
        require(isinstance(item, dict) and set(item) == {'candidateId', 'rank', 'reason'},
                'Проверьте кандидата в подборке.', 400)
        cid = text(item['candidateId'], 160)
        rank = item['rank']
        require(type(rank) is int and 0 <= rank <= 1000000, 'Проверьте порядок подборки.', 400)
        require(cid not in ids and rank not in ranks, 'В подборке повторяется кандидат или позиция.', 400)
        ids.add(cid)
        ranks.add(rank)
        result['candidates'].append({'candidateId': cid, 'rank': rank, 'reason': text(item['reason'], 4000)})
    result['candidates'].sort(key=lambda item: item['rank'])
    return result


def import_batch(db, workspace_id, vacancy_id, body, portal=None):
    """Internal integration only: HTTP callers must separately authorize the source.

    The portal supplies its existing candidate-approval rules and transactional
    notification outbox. Already reviewed candidates retain their original state.
    """
    payload = _batch_payload(body)
    digest = hashlib.sha256(_dump(payload).encode()).hexdigest()
    with _atomic(db):
        vacancy(db, workspace_id, vacancy_id, include_archived=True)
        existing = db.execute('''SELECT payload_hash,result FROM em_batches
          WHERE workspace_id=? AND vacancy_id=? AND source=? AND external_batch_id=?''',
                              (workspace_id, vacancy_id, payload['source'], payload['externalBatchId'])).fetchone()
        if existing:
            require(existing[0] == digest, 'Подборка с этим идентификатором уже имеет другой состав.', 409)
            return json.loads(existing[1])
        ensure_open(db, workspace_id, vacancy_id)
        # Validate the entire batch before creating applications or notifications.
        for item in payload['candidates']:
            _eligible(db, item['candidateId'])
        bid, created = _uid('batch'), iso(now())
        result = {'batchId': bid, 'vacancyId': vacancy_id, 'source': payload['source'],
                  'version': payload['version'], 'externalBatchId': payload['externalBatchId'],
                  'applicationIds': [], 'addedApplicationIds': [], 'existingApplicationIds': []}
        db.execute('INSERT INTO em_batches VALUES (?,?,?,?,?,?,?,?,?)',
                   (bid, workspace_id, vacancy_id, payload['source'], payload['version'],
                    payload['externalBatchId'], digest, '{}', created))
        for item in payload['candidates']:
            cid = item['candidateId']
            app = db.execute('SELECT id FROM cp_applications WHERE vacancy_id=? AND candidate_id=?',
                             (vacancy_id, cid)).fetchone()
            if app is None:
                require(portal is not None, 'Не подключён обработчик согласования вакансии.', 503)
                portal.propose_application(db, {'vacancyId': vacancy_id, 'candidateId': cid})
                app = db.execute('SELECT id FROM cp_applications WHERE vacancy_id=? AND candidate_id=?',
                                 (vacancy_id, cid)).fetchone()
                require(app is not None, 'Не удалось сохранить участие кандидата.', 500)
            aid = app[0]
            review = db.execute('SELECT workspace_id FROM em_reviews WHERE application_id=?', (aid,)).fetchone()
            if review is None:
                _insert_review(db, workspace_id, aid, source=payload['source'], version=payload['version'],
                               batch_id=bid, rank=item['rank'], reason=item['reason'])
                outcome = 'added'
                result['addedApplicationIds'].append(aid)
            else:
                require(review[0] == workspace_id, 'Кандидат не найден.', 404)
                outcome = 'existing'
                result['existingApplicationIds'].append(aid)
            result['applicationIds'].append(aid)
            db.execute('INSERT INTO em_batch_items VALUES (?,?,?,?,?,?)',
                       (bid, cid, aid, item['rank'], item['reason'], outcome))
        db.execute('UPDATE em_batches SET result=? WHERE id=?', (_dump(result), bid))
        if result['addedApplicationIds']:
            bump_revision(db, workspace_id, vacancy_id)
        return result


def _application_row(db, workspace_id, application_id):
    _workspace(db, workspace_id)
    row = db.execute('''SELECT a.id,a.candidate_id,a.vacancy_id,a.status,a.approved_automatically,
      r.state,r.queue_order,r.skip_count,r.source,r.source_version,r.batch_id,r.rank,r.reason,
      r.selected_at,r.rejected_at,p.profile,r.created_at,r.updated_at
      FROM cp_applications a JOIN em_reviews r ON r.application_id=a.id
      JOIN em_vacancies v ON v.vacancy_id=a.vacancy_id JOIN cp_candidates p ON p.id=a.candidate_id
      WHERE a.id=? AND r.workspace_id=? AND v.workspace_id=? AND v.archived_at IS NULL''',
                     (application_id, workspace_id, workspace_id)).fetchone()
    require(row is not None, 'Кандидат не найден.', 404)
    return row


def application(db, workspace_id, application_id, include_rejected=False, include_unapproved=False):
    row = _application_row(db, workspace_id, application_id)
    profile = json.loads(row[15])
    require(include_rejected or row[5] != 'rejected', 'Кандидат не найден.', 404)
    require(include_unapproved or (row[3] == 'accepted' and profile.get('consentReuse') is True),
            'Кандидат не найден.', 404)
    return {'applicationId': row[0], 'candidateId': row[1], 'vacancyId': row[2],
            'status': row[3], 'approvedAutomatically': bool(row[4]), 'reviewState': row[5],
            'queueOrder': row[6], 'skipCount': row[7], 'source': row[8], 'sourceVersion': row[9],
            'batchId': row[10], 'rank': row[11], 'reason': row[12], 'selectedAt': row[13],
            'rejectedAt': row[14], 'profile': {key: profile[key] for key in PROFILE_FIELDS if key in profile},
            'createdAt': row[16], 'updatedAt': row[17]}


def applications(db, workspace_id, view='all', vacancy_id=None):
    _workspace(db, workspace_id)
    require(view in ('all', 'proposed', 'selected'), 'Неизвестный список кандидатов.', 400)
    if vacancy_id is not None:
        vacancy(db, workspace_id, vacancy_id)
    rows = db.execute('''SELECT a.id,a.status,p.profile,r.state FROM cp_applications a
      JOIN em_reviews r ON r.application_id=a.id JOIN em_vacancies v ON v.vacancy_id=a.vacancy_id
      JOIN cp_candidates p ON p.id=a.candidate_id
      WHERE v.workspace_id=? AND r.workspace_id=? AND v.archived_at IS NULL
      AND (? IS NULL OR a.vacancy_id=?) AND r.state!='rejected'
      ORDER BY r.queue_order,r.created_at,a.id''',
                      (workspace_id, workspace_id, vacancy_id, vacancy_id)).fetchall()
    return [application(db, workspace_id, row[0]) for row in rows
            if row[1] == 'accepted' and json.loads(row[2]).get('consentReuse') is True
            and (view == 'all' or row[3] == view)]


def review_candidates(db, workspace_id, vacancy_id, view='proposed'):
    return applications(db, workspace_id, view=view, vacancy_id=vacancy_id)


def skip(db, workspace_id, application_id, request_id=None, expected_revision=None):
    with _atomic(db):
        app = application(db, workspace_id, application_id)
        current = ensure_open(db, workspace_id, app['vacancyId'])
        payload = {'applicationId': application_id, 'revision': expected_revision}
        saved, digest = _command(db, workspace_id, request_id, 'review.skip', payload)
        if saved is not None:
            return saved
        _check_revision(current, expected_revision)
        require(app['reviewState'] == 'proposed', 'Кандидат уже выбран.', 409)
        db.execute('''UPDATE em_reviews SET queue_order=?,skip_count=skip_count+1,updated_at=?
          WHERE application_id=? AND workspace_id=?''',
                   (_next_order(db, workspace_id, app['vacancyId']), iso(now()), application_id, workspace_id))
        bump_revision(db, workspace_id, app['vacancyId'])
        result = {'applicationId': application_id, 'reviewState': 'proposed', 'skipped': True}
        _remember(db, workspace_id, request_id, 'review.skip', digest, result)
        return result


def reject(db, workspace_id, application_id, request_id=None, expected_revision=None):
    with _atomic(db):
        app = application(db, workspace_id, application_id, include_rejected=True)
        payload = {'applicationId': application_id, 'revision': expected_revision}
        saved, digest = _command(db, workspace_id, request_id, 'review.reject', payload)
        if saved is not None:
            return saved
        current = vacancy(db, workspace_id, app['vacancyId'])
        if app['reviewState'] != 'rejected':
            _check_revision(current, expected_revision)
            db.execute("UPDATE em_reviews SET state='rejected',rejected_at=?,updated_at=? WHERE application_id=?",
                       (iso(now()), iso(now()), application_id))
            bump_revision(db, workspace_id, app['vacancyId'])
        result = {'applicationId': application_id, 'reviewState': 'rejected'}
        _remember(db, workspace_id, request_id, 'review.reject', digest, result)
        return result


def mark_selected(db, workspace_id, application_id, meeting_id):
    """Called in the transaction which successfully persists the meeting proposal."""
    with _atomic(db):
        app = application(db, workspace_id, application_id)
        ensure_open(db, workspace_id, app['vacancyId'])
        meeting = db.execute('SELECT id,status FROM cp_meetings WHERE id=? AND application_id=?',
                             (meeting_id, application_id)).fetchone()
        require(meeting is not None and meeting[0] == meeting_id and
                meeting[1] in ('pending', 'confirmed', 'live', 'completed'),
                'Встреча ещё не назначена или изменилась.', 409)
        if app['reviewState'] != 'selected':
            changed = iso(now())
            db.execute("UPDATE em_reviews SET state='selected',selected_at=?,updated_at=? WHERE application_id=?",
                       (changed, changed, application_id))
            bump_revision(db, workspace_id, app['vacancyId'])
        return {'applicationId': application_id, 'meetingId': meeting_id, 'reviewState': 'selected'}
