"""Internal selection workbench over canonical people, vacancies and core results.

This module does not score people. Immutable engine documents and their exact
input bindings are stored separately from the editable candidate directory.
Publication always uses employer_store's approval and notification path.
"""
import hashlib
import json
import re
import secrets
import time
from urllib.parse import parse_qs, urlsplit

from domain import Problem, iso, now, require, text
import employer_store as employers
import selection_status
import assessment_core_bridge
import assessment_core_lifecycle
import selection_materials_lifecycle
import selection_facts
import vacancy_profile_bridge
import recommendations_bridge

PREFIX = '/api/hr/selection/'
HEADERS = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}
EDITABLE = {'name': 140, 'desiredRole': 200, 'city': 120, 'about': 12000}
STATES = {'supported', 'unknown', 'not_applicable', 'conflict'}


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def init(db):
    selection_facts.init(db)
    vacancy_profile_bridge.init(db)
    recommendations_bridge.init(db)
    selection_status.init_evidence(db)
    for sql in (
        '''CREATE TABLE IF NOT EXISTS sl_core_results (
          id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES cp_candidates(id),
          interview_id TEXT NOT NULL REFERENCES cp_interviews(id),
          engine_candidate_id TEXT NOT NULL, job_id TEXT NOT NULL,
          request_hash TEXT NOT NULL, source_revision TEXT NOT NULL,
          document_hash TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL,
          UNIQUE(candidate_id,document_hash))''',
        '''CREATE INDEX IF NOT EXISTS sl_result_person ON sl_core_results(candidate_id,created_at)''',
        '''CREATE TABLE IF NOT EXISTS sl_core_matches (
          id TEXT PRIMARY KEY, result_id TEXT NOT NULL REFERENCES sl_core_results(id),
          vacancy_id TEXT NOT NULL REFERENCES cp_vacancies(id), vacancy_hash TEXT NOT NULL,
          partition TEXT NOT NULL, rank INTEGER, reason TEXT NOT NULL,
          body TEXT NOT NULL, created_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS sl_requirements (
          id TEXT PRIMARY KEY, vacancy_id TEXT NOT NULL REFERENCES cp_vacancies(id),
          vacancy_hash TEXT NOT NULL, engine_vacancy_id TEXT NOT NULL,
          body TEXT NOT NULL, created_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS sl_cohort (
          vacancy_id TEXT NOT NULL REFERENCES cp_vacancies(id),
          candidate_id TEXT NOT NULL REFERENCES cp_candidates(id), created_at TEXT NOT NULL,
          PRIMARY KEY(vacancy_id,candidate_id))''',
        '''CREATE TABLE IF NOT EXISTS sl_edits (
          id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES cp_candidates(id),
          before_body TEXT NOT NULL, after_body TEXT NOT NULL, created_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS sl_nominations (
          id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES cp_candidates(id),
          vacancy_id TEXT NOT NULL REFERENCES cp_vacancies(id),
          result_id TEXT NOT NULL REFERENCES sl_core_results(id),
          match_id TEXT NOT NULL REFERENCES sl_core_matches(id),
          state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(candidate_id,vacancy_id))''',
        '''CREATE TABLE IF NOT EXISTS sl_commands (
          request_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, result TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS sl_profile_links (
          candidate_id TEXT PRIMARY KEY REFERENCES cp_candidates(id),
          invitation_id TEXT NOT NULL REFERENCES cp_invitations(id), url TEXT NOT NULL)''',
    ):
        db.execute(sql)


def person(db, cid):
    row = db.execute('SELECT profile,updated_at,email,searching FROM cp_candidates WHERE id=?', (cid,)).fetchone()
    require(row, 'Кандидат не найден.', 404)
    profile = json.loads(row[0])
    interview = db.execute('SELECT id,status,completed_at,room FROM cp_interviews WHERE candidate_id=?', (cid,)).fetchone()
    resume = db.execute('SELECT uploaded_at FROM cp_resumes WHERE candidate_id=?', (cid,)).fetchone()
    # Stable input metadata. Directory spelling changes do not replace evidence.
    room = json.loads(interview[3]) if interview and interview[3] else None
    room_identity = ({key: room.get(key) for key in ('provider', 'id', 'name', 'meetingNumber',
                      'meetingSessionId', 'connectionId')} if isinstance(room, dict) else room)
    source = digest({'interview': list(interview[:3]) if interview else None, 'room': room_identity,
                     'resumeUploadedAt': resume[0] if resume else None})
    contact = db.execute('SELECT contact_email FROM cp_intake WHERE candidate_id=?', (cid,)).fetchone()
    # Connection means an established account or consented booking channel.
    # A scraped address is searchable, but does not itself connect the mailbox.
    email_target = invitation_target(db, cid, 'email')
    telegram_target = invitation_target(db, cid, 'telegram')
    return {'id': cid, **{key: profile.get(key, '') for key in EDITABLE},
            'email': email_target or (contact[0] if contact else '') or '', 'accountActive': bool(row[2]),
            'emailConnected': bool(email_target), 'telegramConnected': bool(telegram_target),
            'searching': bool(row[3]), 'consentReuse': profile.get('consentReuse') is True,
            'interviewId': interview[0] if interview else None,
            'interviewStatus': interview[1] if interview else None, 'resumeAvailable': bool(resume),
            'revision': digest([row[0], row[1]]), 'sourceRevision': source}


def vacancy(db, vid):
    row = db.execute('SELECT workspace_id FROM em_vacancies WHERE vacancy_id=? AND archived_at IS NULL', (vid,)).fetchone()
    require(row, 'Вакансия не найдена.', 404)
    value = employers.vacancy(db, row[0], vid)
    substantive = json.loads(db.execute('SELECT body FROM cp_vacancies WHERE id=?', (vid,)).fetchone()[0])
    return {**value, 'workspaceId': row[0], 'contentHash': digest(substantive)}


def _seal(document, expected_format):
    require(isinstance(document, dict) and document.get('engine_contract') == 'engine-v1'
            and document.get('format') == expected_format and document.get('synthetic') is False,
            'Нужен реальный результат ядра engine-v1.', 422)
    claimed = document.get('document_sha256')
    require(isinstance(claimed, str) and re.fullmatch('[a-f0-9]{64}', claimed)
            and digest({**document, 'document_sha256': ''}) == claimed,
            'Контрольная сумма результата ядра не совпадает.', 422)
    require(len(dump(document).encode()) <= 2 * 1024 * 1024, 'Результат ядра слишком большой.', 413)
    return claimed


def import_result(db, body):
    """Trusted operator/MCP boundary; identity comes from the exact saved request.

    The input request itself is retained by the assessment service, not duplicated
    here. No scalar score or positive verdict is inferred from a successful call.
    """
    require(set(body) == {'candidateId', 'interviewId', 'engineCandidateId', 'jobId',
                         'requestHash', 'sourceRevision', 'document'}, 'Проверьте привязку результата.', 400)
    current = person(db, body['candidateId'])
    require(current['interviewId'] == body['interviewId'] and current['interviewStatus'] == 'completed'
            and current['sourceRevision'] == body['sourceRevision'], 'Материалы кандидата изменились.', 409)
    doc = body['document']
    seal = _seal(doc, 'candidate_profile/1')
    require(doc.get('body', {}).get('candidate_id') == body['engineCandidateId'],
            'Результат принадлежит другому кандидату.', 422)
    model = doc.get('model', {})
    require(model.get('mode') == 'provider' and type(model.get('call_count')) is int
            and model['call_count'] > 0, 'Нет подтверждения вызова настоящей модели.', 422)
    require(isinstance(body['requestHash'], str) and re.fullmatch('[a-f0-9]{64}', body['requestHash']),
            'Проверьте контрольную сумму запроса.', 422)
    families = doc.get('body', {}).get('families')
    require(isinstance(families, list) and 1 <= len(families) <= 20, 'Некорректные критерии ядра.', 422)
    for family in families:
        criteria = family.get('criteria') if isinstance(family, dict) else None
        require(isinstance(criteria, list) and len(criteria) <= 100, 'Некорректные критерии ядра.', 422)
        for item in criteria:
            require(isinstance(item, dict) and item.get('state') in STATES and
                    (type(item.get('level')) is int and 1 <= item['level'] <= 5
                     if item['state'] == 'supported' else item.get('level') is None),
                    'Некорректный уровень оценки.', 422)
    old = db.execute('SELECT id,request_hash,engine_candidate_id FROM sl_core_results WHERE candidate_id=? AND document_hash=?',
                     (current['id'], seal)).fetchone()
    if old:
        require(old[1] == body['requestHash'] and old[2] == body['engineCandidateId'], 'Привязка уже существует.', 409)
        return {'id': old[0], 'existing': True}
    identity = 'result_' + secrets.token_hex(12)
    db.execute('''INSERT INTO sl_core_results
      (id,candidate_id,interview_id,engine_candidate_id,job_id,request_hash,source_revision,document_hash,body,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?)''',
        (identity, current['id'], current['interviewId'], text(body['engineCandidateId'], 100),
         text(body['jobId'], 100), body['requestHash'], body['sourceRevision'], seal, dump(doc), iso(now())))
    return {'id': identity, 'existing': False}


def result(db, candidate, vid=None):
    row = db.execute('''SELECT id,source_revision,body,created_at,job_id FROM sl_core_results
      WHERE candidate_id=? ORDER BY rowid DESC LIMIT 1''', (candidate['id'],)).fetchone()
    if not row:
        return None
    doc = json.loads(row[2])
    criteria = [item for family in doc['body']['families'] for item in family['criteria']]
    counts = {state: sum(item['state'] == state for item in criteria) for state in STATES}
    counts['failed'] = sum(item.get('failed_processing') is True for item in criteria)
    match = None
    if vid:
        value = db.execute('''SELECT id,vacancy_hash,partition,rank,reason FROM sl_core_matches
          WHERE result_id=? AND vacancy_id=? ORDER BY rowid DESC LIMIT 1''', (row[0], vid)).fetchone()
        if value:
            match = {'id': value[0], 'stale': value[1] != vacancy(db, vid)['contentHash'],
                     'partition': value[2], 'rank': value[3], 'reason': value[4]}
    stale = row[1] != candidate['sourceRevision']
    recommendation = ('stale' if stale or match and match['stale'] else
                      match['partition'] if match else 'not_matched')
    return {'id': row[0], 'createdAt': row[3], 'jobId': row[4], 'model': doc.get('model', {}).get('model_id'),
            'counts': counts, 'stale': stale, 'recommendation': recommendation,
            'reason': match['reason'] if match else 'Сопоставление с требованиями вакансии ещё не выполнено.',
            'match': match, 'criteria': criteria, 'limitations': doc.get('limitations', []),
            'documentHash': doc['document_sha256']}


def import_requirement(db, body):
    require(set(body) == {'vacancyId', 'vacancyHash', 'engineVacancyId', 'document'},
            'Проверьте привязку требований.', 400)
    v = vacancy(db, body['vacancyId'])
    require(v['contentHash'] == body['vacancyHash'], 'Вакансия изменилась.', 409)
    doc = body['document']
    seal = _seal(doc, 'requirement_profile/1')
    requirement = doc.get('body', {})
    require(requirement.get('vacancy_id') == body['engineVacancyId'] and requirement.get('state') == 'confirmed',
            'Нужны подтверждённые требования этой вакансии.', 422)
    old = db.execute('SELECT vacancy_id,vacancy_hash FROM sl_requirements WHERE id=?', (seal,)).fetchone()
    if old:
        require(old[0] == v['id'] and old[1] == v['contentHash'], 'Требования уже привязаны.', 409)
    else:
        db.execute('INSERT INTO sl_requirements VALUES (?,?,?,?,?,?)',
                   (seal, v['id'], v['contentHash'], text(body['engineVacancyId'], 100), dump(doc), iso(now())))
    return {'id': seal, 'existing': bool(old)}


def import_match(db, body):
    require(set(body) == {'resultId', 'requirementId', 'document', 'rank'}, 'Проверьте сопоставление.', 400)
    r = db.execute('SELECT candidate_id,engine_candidate_id,document_hash,source_revision FROM sl_core_results WHERE id=?',
                   (body['resultId'],)).fetchone()
    q = db.execute('SELECT vacancy_id,vacancy_hash,engine_vacancy_id FROM sl_requirements WHERE id=?',
                   (body['requirementId'],)).fetchone()
    require(r and q, 'Сначала сохраните оценку и требования.', 409)
    require(person(db, r[0])['sourceRevision'] == r[3] and vacancy(db, q[0])['contentHash'] == q[1],
            'Исходные материалы изменились.', 409)
    doc = body['document']
    seal = _seal(doc, 'match/1')
    match = doc.get('body', {}).get('match', {})
    require(match.get('candidate_id') == r[1] and match.get('vacancy_id') == q[2]
            and match.get('candidate_profile_revision_ref', {}).get('sha256') == r[2]
            and match.get('requirement_profile_revision_ref', {}).get('sha256') == body['requirementId']
            and match.get('requirement_state') == 'confirmed' and match.get('status') == 'current',
            'Сопоставление относится к другим исходным данным.', 422)
    partition, rank = match.get('partition'), body['rank']
    require(partition in ('ranked', 'needs_assessment', 'non_matching', 'conflicting', 'failed_processing'),
            'Неизвестный результат сопоставления.', 422)
    require((type(rank) is int and 0 <= rank <= 1000000) if partition == 'ranked' else rank is None,
            'Нужна позиция из ранжирования ядра.', 422)
    reasons = match.get('partition_reasons', [])
    require(isinstance(reasons, list) and len(reasons) <= 100 and all(isinstance(x, str) for x in reasons),
            'Проверьте причины рекомендации.', 422)
    reason = '; '.join(reasons) or ('Соответствует подтверждённым требованиям ядра.' if partition == 'ranked'
                                  else 'Нужна дополнительная оценка.')
    old = db.execute('SELECT result_id,vacancy_id,rank FROM sl_core_matches WHERE id=?', (seal,)).fetchone()
    if old:
        require(tuple(old) == (body['resultId'], q[0], rank), 'Сопоставление уже привязано.', 409)
    else:
        db.execute('INSERT INTO sl_core_matches VALUES (?,?,?,?,?,?,?,?,?)',
                   (seal, body['resultId'], q[0], q[1], partition, rank, reason, dump(doc), iso(now())))
    return {'id': seal, 'partition': partition, 'existing': bool(old)}


def directory(db, vid=None, *, offset=0, limit=200, candidate_ids=None):
    if vid:
        vacancy(db, vid)
    people = []
    cohort = {row[0] for row in db.execute('SELECT candidate_id FROM sl_cohort WHERE vacancy_id=?', (vid,))} if vid else set()
    match_map = recommendations_bridge.projections(db, vid) if vid and assessment_core_bridge.enabled() else {}
    total = db.execute('SELECT COUNT(*) FROM cp_candidates').fetchone()[0]
    if candidate_ids is None:
        candidate_rows = db.execute('SELECT id FROM cp_candidates ORDER BY created_at DESC,id LIMIT ? OFFSET ?', (limit, offset)).fetchall()
    else:
        candidate_rows = db.execute('SELECT id FROM cp_candidates WHERE id IN (' + ','.join('?' for _ in candidate_ids) + ') ORDER BY created_at DESC,id', tuple(candidate_ids)).fetchall()
        total = len(candidate_rows)
    for (cid,) in candidate_rows:
        item = person(db, cid)
        item['assessment'] = result(db, item, vid)
        item['inCohort'] = cid in cohort
        row = db.execute('SELECT id,status FROM cp_applications WHERE candidate_id=? AND vacancy_id=? AND archived_at IS NULL',
                         (cid, vid)).fetchone() if vid else None
        item['application'] = {'id': row[0], 'status': row[1]} if row else None
        nominated = db.execute('SELECT id,state FROM sl_nominations WHERE candidate_id=? AND vacancy_id=?',
                                (cid, vid)).fetchone() if vid else None
        item['nomination'] = {'id': nominated[0], 'state': nominated[1]} if nominated else None
        event_keys = (['selection:' + nominated[0]] if nominated else []) + (['application.proposed:' + row[0]] if row else [])
        item['deliveries'] = [dict(zip(('id', 'event', 'channel', 'status', 'error'), r)) for r in db.execute(
            'SELECT id,event_type,channel,status,last_error FROM cn_outbox WHERE candidate_id=? AND event_key IN ('
            + ','.join('?' for _ in event_keys) + ') ORDER BY created_at,rowid',
            (cid, *event_keys))] if event_keys else []
        item['canAdd'] = bool(item['searching'] and
            item['interviewStatus'] == 'completed' and item['assessment'] and
            item['assessment']['recommendation'] == 'ranked' and not item['application'] and not nominated)
        item['workflow'] = selection_status.overview(db, item, vid)
        item['workflow']['automation'] = assessment_core_lifecycle.public_candidate_status(db, cid)
        # A newer assessment pipeline supersedes an engine-v1 recommendation.
        # Until its matching adapter is connected, it cannot authorize sending.
        item['canAdd'] = item['canAdd'] and item['workflow']['match']['state'] == 'ranked'
        item['requiredFields'] = selection_facts.read(db, 'candidate', cid)
        item['hrInterview'] = selection_status.evidence(db, item)
        if assessment_core_bridge.enabled() and vid:
            match = match_map.get(cid, {'state': 'not_compared', 'origin': 'recommendations'})
            item['workflow']['match'] = match
            item['canAdd'] = bool(item['searching'] and item['hrInterview']['state'] == 'ready' and
                match.get('state') == 'compared' and match.get('status') == 'scored' and
                match.get('eligibility') != 'fail' and not item['application'] and not nominated)
            newnom = db.execute('SELECT id,state,error,retry_at,attempts,recommendation_id FROM sl_recommendation_nominations WHERE candidate_id=? AND vacancy_id=?', (cid,vid)).fetchone()
            if newnom:
                item['nomination'] = {'id':newnom[0], 'state':newnom[1], 'error':newnom[2],
                                      'retryAt':newnom[3], 'attempts':newnom[4]}
                item['canAdd'] = item['canAdd'] and newnom[1] == 'blocked' and newnom[5] != match.get('recommendationId')
        people.append(item)
    return {'candidates': people, 'count': total, 'offset': offset, 'limit': limit,
            'nextOffset': offset + len(people) if offset + len(people) < total else None}


def edit(db, body):
    require(set(body) == {'candidateId', 'revision', 'fields'}, 'Проверьте поля кандидата.', 400)
    fields = body['fields']
    require(isinstance(fields, dict) and 0 < len(fields) <= len(EDITABLE) and set(fields) <= set(EDITABLE),
            'Это поле редактируется в кабинете кандидата.', 400)
    values = {key: text(value, EDITABLE[key], required=key == 'name') for key, value in fields.items()}
    with employers._atomic(db):
        current = person(db, body['candidateId'])
        require(current['revision'] == body['revision'], 'Карточка изменилась. Обновите таблицу.', 409)
        before = json.loads(db.execute('SELECT profile FROM cp_candidates WHERE id=?', (current['id'],)).fetchone()[0])
        after = {**before, **values}
        db.execute('UPDATE cp_candidates SET profile=?,updated_at=? WHERE id=?', (dump(after), iso(now()), current['id']))
        db.execute('INSERT INTO sl_edits VALUES (?,?,?,?,?)',
                   (secrets.token_hex(16), current['id'], dump({k: before.get(k) for k in values}), dump(values), iso(now())))
    return {'candidate': person(db, current['id'])}


def invitation_target(db, cid, channel):
    """Only established account or consented booking contacts; never scrape a CV."""
    account = db.execute('SELECT email FROM cp_candidates WHERE id=?', (cid,)).fetchone()
    if channel == 'email' and account and account[0]:
        return account[0]
    # Core/public dossier readers need only canonical candidate tables. Optional
    # delivery adapters must not make those snapshots depend on their schema.
    tables = {row[0] for row in db.execute('''SELECT name FROM sqlite_master WHERE type='table'
              AND name IN ('cp_booking_profile_consents','cn_recipients','cn_booking_telegram')''')}
    consent = db.execute('''SELECT contact_email,invitation_id FROM cp_booking_profile_consents
                            WHERE candidate_id=?''', (cid,)).fetchone() if 'cp_booking_profile_consents' in tables else None
    if channel == 'email':
        return consent[0] if consent else None
    import candidate_notifications as notices
    recipient = (notices._dict(db.execute('SELECT * FROM cn_recipients WHERE candidate_id=?', (cid,)))
                 if 'cn_recipients' in tables else None)
    if notices._telegram_identity_active(db, recipient):
        return recipient['telegram_chat_id']
    bound = db.execute('SELECT chat_id FROM cn_booking_telegram WHERE candidate_id=? AND invitation_id=?',
                       (cid, consent[1])).fetchone() if consent and 'cn_booking_telegram' in tables else None
    return bound[0] if bound else None


def _profile_link(db, cid, origin):
    import candidate_invites as invites
    if person(db, cid)['accountActive']:
        return origin + '/iframe.html?id=hr-vision-product--candidate&viewMode=story&cp=login', None
    old = db.execute('''SELECT l.url,l.invitation_id FROM sl_profile_links l JOIN cp_invitations i
      ON i.id=l.invitation_id WHERE l.candidate_id=? AND i.revoked_at IS NULL
      AND i.consumed_at IS NULL AND i.expires>?''', (cid, int(time.time()))).fetchone()
    if old:
        return old[0], old[1]
    issued = invites.issue(db, cid, 'claim', origin)
    db.execute('INSERT INTO sl_profile_links VALUES (?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET invitation_id=excluded.invitation_id,url=excluded.url',
               (cid, issued['invitationId'], issued['url']))
    return issued['url'], issued['invitationId']


def enqueue_invitation(db, nomination_id, origin):
    import candidate_notifications as notices
    row = db.execute('SELECT candidate_id FROM sl_nominations WHERE id=?', (nomination_id,)).fetchone()
    if not row:
        row = db.execute('SELECT candidate_id FROM sl_recommendation_nominations WHERE id=?', (nomination_id,)).fetchone()
    require(row, 'Приглашение не найдено.', 404)
    cid, config, tick = row[0], notices.settings(), int(time.time())
    link, invitation = _profile_link(db, cid, origin)
    payload = dump({'nominationId': nomination_id, 'url': link, 'invitationId': invitation})
    for channel in ('email', 'telegram'):
        target = invitation_target(db, cid, channel)
        configured = config.email_configured if channel == 'email' else config.telegram_configured
        state = 'disabled' if not configured else 'pending' if target else 'unbound'
        db.execute('''INSERT OR IGNORE INTO cn_outbox
          (id,candidate_id,event_key,event_type,channel,recipient,created_at,status,next_attempt_at,payload_json)
          VALUES (?,?,?,'selection.invitation',?,?,?,?,?,?)''',
          (secrets.token_hex(16), cid, 'selection:' + nomination_id, channel, target, tick, state, tick, payload))


def prepare_invitation(db, row, config, tick):
    """Called under the normal outbox claim, before any provider request."""
    payload = json.loads(row['payload_json'])
    nomination = db.execute('SELECT candidate_id,state FROM sl_nominations WHERE id=?', (payload.get('nominationId'),)).fetchone()
    if not nomination:
        nomination = db.execute('SELECT candidate_id,state FROM sl_recommendation_nominations WHERE id=?', (payload.get('nominationId'),)).fetchone()
        if nomination and not recommendation_nomination_current(db, payload.get('nominationId')):
            return 'error', 'recommendation_stale'
    if not nomination or nomination[0] != row['candidate_id'] or nomination[1] not in ('waiting_profile', 'waiting_consent'):
        return 'error', 'invitation_no_longer_needed'
    p = person(db, row['candidate_id'])
    if not p['searching']:
        return 'error', 'search_stopped'
    if payload.get('invitationId'):
        token = db.execute('SELECT revoked_at,consumed_at,expires,candidate_id FROM cp_invitations WHERE id=?',
                            (payload['invitationId'],)).fetchone()
        if not token or token[0] or token[1] or token[2] <= tick or token[3] != row['candidate_id']:
            return 'error', 'invitation_expired'
    target = invitation_target(db, row['candidate_id'], row['channel'])
    if not target or target != row['recipient']:
        return 'error', 'recipient_changed'
    configured = config.email_configured if row['channel'] == 'email' else config.telegram_configured
    return ('pending', None) if configured else ('disabled', 'channel_disabled')


def invitation_message(row):
    link = json.loads(row['payload_json'])['url']
    # This first contact deliberately contains no CV, assessment or employer data.
    subject = 'Приглашение в МашаДаша'
    content = 'Для вас подготовлено предложение работы. Откройте свой профиль, проверьте данные и согласуйте участие.\n\n'
    return subject, content + link, {'inline_keyboard': [[{'text': 'Открыть профиль', 'url': link}]]}


def reconcile_nominations(db, portal, cid):
    """Candidate consent/claim calls this in their existing transaction.

    A withdrawn or replaced result never releases an old nomination. Only the
    standard application path can make the employer projection visible.
    """
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sl_nominations'").fetchone():
        return
    reconcile_recommendation_nominations(db, portal, cid)
    p = person(db, cid)
    if not p['accountActive'] or not p['consentReuse'] or not p['searching']:
        return
    rows = db.execute("SELECT id,vacancy_id,result_id,match_id FROM sl_nominations WHERE candidate_id=? AND state IN ('waiting_profile','waiting_consent')",
                      (cid,)).fetchall()
    for nid, vid, rid, mid in rows:
        try:
            v = vacancy(db, vid)
            employers.ensure_open(db, v['workspaceId'], vid)
            assessment = result(db, p, vid)
        except Problem as error:
            if error.status not in (404, 409):
                raise
            db.execute("UPDATE sl_nominations SET state='blocked',updated_at=? WHERE id=?", (iso(now()), nid))
            continue
        if not assessment or assessment['id'] != rid or not assessment.get('match') or assessment['match']['id'] != mid or assessment['recommendation'] != 'ranked':
            db.execute("UPDATE sl_nominations SET state='blocked',updated_at=? WHERE id=?", (iso(now()), nid))
            continue
        employers.import_batch(db, v['workspaceId'], vid, {
            'source': 'core', 'version': 'assessment-engine-v1', 'externalBatchId': nid,
            'candidates': [{'candidateId': cid, 'rank': assessment['match']['rank'], 'reason': assessment['reason']}]}, portal)
        db.execute("UPDATE sl_nominations SET state='proposed',updated_at=? WHERE id=?", (iso(now()), nid))


def nominate(db, portal, body):
    require(set(body) == {'vacancyId', 'candidateIds', 'requestId'} and isinstance(body['candidateIds'], list)
            and 1 <= len(body['candidateIds']) <= 100 and all(isinstance(x, str) for x in body['candidateIds'])
            and len(set(body['candidateIds'])) == len(body['candidateIds']), 'Выберите кандидатов.', 400)
    request_id = text(body['requestId'], 120)
    hashed = digest({'vacancyId': body['vacancyId'], 'candidateIds': sorted(body['candidateIds'])})
    with employers._atomic(db):
        old = db.execute('SELECT payload_hash,result FROM sl_commands WHERE request_id=?', (request_id,)).fetchone()
        if old:
            require(old[0] == hashed, 'Запрос уже использован для другой подборки.', 409)
            return json.loads(old[1])
        v = vacancy(db, body['vacancyId'])
        employers.ensure_open(db, v['workspaceId'], v['id'])
        rows = {p['id']: p for p in directory(db, v['id'], candidate_ids=body['candidateIds'])['candidates']}
        require(all(cid in rows and rows[cid]['canAdd'] for cid in body['candidateIds']),
                'Нужны актуальная рекомендация ядра и завершённое интервью.', 409)
        receipt = {'added': [], 'waitingProfile': [], 'proposed': []}
        for cid in body['candidateIds']:
            p = rows[cid]
            state = 'waiting_consent' if p['accountActive'] else 'waiting_profile'
            nid = 'nomination_' + secrets.token_hex(12)
            db.execute('INSERT INTO sl_nominations VALUES (?,?,?,?,?,?,?,?)',
                (nid, cid, v['id'], p['assessment']['id'], p['assessment']['match']['id'], state, iso(now()), iso(now())))
            receipt['added'].append(cid)
            reconcile_nominations(db, portal, cid)
            latest = db.execute('SELECT state FROM sl_nominations WHERE id=?', (nid,)).fetchone()[0]
            if latest == 'proposed':
                receipt['proposed'].append(cid)
            else:
                enqueue_invitation(db, nid, portal.origin)
                receipt['waitingProfile'].append(cid)
        db.execute('INSERT INTO sl_commands VALUES (?,?,?)', (request_id, hashed, dump(receipt)))
        return receipt


class SelectionRoutes:
    def __init__(self, connect, lock, portal):
        self.connect, self.lock, self.portal = connect, lock, portal
        with lock, connect() as db:
            init(db)
            assessment_core_bridge.init(db)

    @staticmethod
    def handles(path):
        return urlsplit(path).path.startswith(PREFIX)

    def handle(self, method, raw_path, headers, body, actor):
        parsed = urlsplit(raw_path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        require(set(query) <= {'vacancyId', 'offset', 'limit'} and all(len(v) == 1 for v in query.values()), 'Проверьте фильтр.', 400)
        require(next((v for k, v in headers.items() if k.lower() == 'sec-fetch-site'), '') != 'cross-site',
                'Другой источник запроса.', 403)
        action = parsed.path[len(PREFIX):].strip('/')
        if action == 'status':
            with self.connect() as db:
                require(actor(db) == 'manager', 'Доступ только для команды HR Vision.', 403)
                require(method == 'GET', 'Действие не найдено.', 404)
            status = {}
            for name, attribute in (('materials', 'selection_materials_lifecycle'),
                                    ('assessment', 'assessment_lifecycle'),
                                    ('recommendations', 'recommendation_automation_lifecycle')):
                runtime = getattr(self.portal, attribute, None)
                status[name] = runtime.status() if runtime is not None else {'enabled': False, 'running': False}
            status['ok'] = all(not value.get('enabled') or
                (value.get('running') and value.get('healthy', True) and not value.get('error'))
                for value in status.values())
            return status, dict(HEADERS)
        if action in ('review-tasks', 'review-prepare', 'review-retry'):
            import employer_review
            with self.lock, self.connect() as db:
                require(actor(db) == 'manager', 'Доступ только для команды HR Vision.', 403)
                if method == 'GET' and action == 'review-tasks':
                    return employer_review.tasks(db), dict(HEADERS)
            require(method == 'POST' and action != 'review-tasks' and isinstance(body, dict), 'Проверьте запрос.', 400)
            keys = {'applicationId', 'preferredParameterIds' if action == 'review-prepare' else 'requestId'}
            require(set(body) == keys and isinstance(body['applicationId'], str), 'Проверьте запрос.', 400)
            if action == 'review-retry':
                require(isinstance(body['requestId'], str) and 0 < len(body['requestId']) <= 100, 'Проверьте запрос.', 400)
            require(employer_review.enabled(), 'Подготовка цитат ещё не подключена.', 503)
            bridge = employer_review.Bridge(self.portal)
            result = (bridge.prepare(body['applicationId'], body['preferredParameterIds']) if action == 'review-prepare'
                      else bridge.retry(body['applicationId'], body['requestId']))
            return result, dict(HEADERS)
        def authorize_core(cid):
            with self.lock, self.connect() as db:
                require(actor(db) == 'manager', 'Доступ только для команды HR Vision.', 403)
                person(db, cid)
        def authorize_vacancy(vid):
            with self.lock, self.connect() as db:
                require(actor(db) == 'manager', 'Доступ только для команды HR Vision.', 403)
                vacancy(db, vid)
        parts = action.split('/')
        if len(parts) in (3, 5) and parts[0] == 'candidates' and parts[2] == 'interviews':
            require(method == 'GET' and (len(parts) == 3 or parts[4] == 'materials'),
                    'Действие не найдено.', 404)
            cid = parts[1]
            authorize_core(cid)
            import selection_interviews
            reader = selection_interviews.configured(self.connect, authorize_core)
            value = reader.list(cid) if len(parts) == 3 else reader.materials(cid, parts[3])
            return value, dict(HEADERS)
        if len(parts) == 3 and parts[0] in ('vacancies', 'candidates') and parts[2] == 'required-fields':
            kind = 'vacancy' if parts[0] == 'vacancies' else 'candidate'
            (authorize_vacancy if kind == 'vacancy' else authorize_core)(parts[1])
            with self.lock, self.connect() as db:
                require(actor(db) == 'manager', 'Доступ только для команды HR Vision.', 403)
                require(method in ('GET', 'POST'), 'Действие не найдено.', 404)
                value = selection_facts.read(db, kind, parts[1]) if method == 'GET' else selection_facts.update(db, kind, parts[1], body)
            return value, dict(HEADERS)
        if len(parts) in (3, 4) and parts[0] == 'vacancies' and parts[2] in ('profile', 'recommendations'):
            vid = parts[1]
            authorize_vacancy(vid)
            require(method in ('GET', 'POST'), 'Действие не найдено.', 404)
            if parts[2] == 'profile':
                bridge = vacancy_profile_bridge.configured(self.connect, self.lock, authorize_vacancy)
                if len(parts) == 4:
                    require(parts[3] == 'revisions' and method == 'POST', 'Действие не найдено.', 404)
                    value = bridge.revise(vid, body)
                elif method == 'POST':
                    require(isinstance(body, dict) and set(body) == {'revision'}, 'Проверьте запрос профиля.', 400)
                    value = bridge.start(vid, body['revision'])
                else:
                    value = bridge.read(vid)
                with self.connect() as db:
                    value['requiredFields'] = selection_facts.read(db, 'vacancy', vid)
                    value['options'] = selection_facts.OPTIONS
                value['stale'] = value['state'] == 'stale'
            else:
                require(len(parts) == 3, 'Действие не найдено.', 404)
                bridge = recommendations_bridge.configured(self.connect, self.lock, authorize_vacancy, authorize_core)
                if method == 'POST':
                    require(isinstance(body, dict) and set(body) == {'revision', 'candidateIds'}, 'Проверьте запрос расчёта.', 400)
                    value = bridge.start(vid, body['revision'], body['candidateIds'])
                else:
                    value = bridge.read(vid)
            return value, dict(HEADERS)
        if len(parts) == 3 and parts[0] == 'nominations' and parts[2] == 'retry' and method == 'POST':
            require(isinstance(body, dict) and set(body) == {'revision', 'recommendationId'}, 'Проверьте версию повтора.', 400)
            with self.connect() as db:
                require(actor(db) == 'manager', 'Доступ только для команды HR Vision.', 403)
                row = db.execute('SELECT vacancy_id FROM sl_recommendation_nominations WHERE id=?', (parts[1],)).fetchone()
                require(row, 'Приглашение не найдено.', 404)
                vid = row[0]
            bridge = recommendations_bridge.configured(self.connect, self.lock, authorize_vacancy, authorize_core)
            verified = bridge.read(vid)
            with self.lock, self.connect() as db:
                require(actor(db) == 'manager', 'Доступ только для команды HR Vision.', 403)
                value = retry_recommendation_nomination(db, parts[1], body, verified)
            return value, dict(HEADERS)
        if method == 'POST' and action == 'publish' and assessment_core_bridge.enabled():
            require(isinstance(body, dict) and 'vacancyId' in body, 'Проверьте публикацию.', 400)
            bridge = recommendations_bridge.configured(self.connect, self.lock, authorize_vacancy, authorize_core)
            verified = bridge.read(body['vacancyId'])
            with self.lock, self.connect() as db:
                require(actor(db) == 'manager', 'Доступ только для команды HR Vision.', 403)
                value = nominate_recommendation(db, self.portal, body, verified)
            return value, dict(HEADERS)
        if method == 'POST' and action.startswith('candidates/') and action.endswith('/assessment'):
            parts = action.split('/')
            require(len(parts) == 3 and isinstance(body, dict) and set(body) == {'revision'},
                    'Проверьте запрос оценки.', 400)
            cid = parts[1]
            authorize_core(cid)
            with self.connect() as db:
                require(person(db, cid)['revision'] == body['revision'], 'Карточка изменилась. Обновите её.', 409)
            bridge = assessment_core_bridge.configured(self.connect, self.lock, authorize_core)
            return {'candidateId': cid, 'assessment': bridge.start(cid)}, dict(HEADERS)
        if method == 'GET' and action.startswith('candidates/') and action.endswith('/readiness'):
            parts = action.split('/')
            require(len(parts) == 3, 'Действие не найдено.', 404)
            cid, vid = parts[1], query.get('vacancyId', [None])[0]
            with self.lock, self.connect() as db:
                require(actor(db) == 'manager', 'Доступ только для команды HR Vision.', 403)
                initial = person(db, cid)
                if vid:
                    vacancy(db, vid)
            # External reads cannot hold the shared application lock. Opening a
            # drawer may verify saved sources, but never starts ASR, a model or invitations.
            selection_materials_lifecycle.refresh_candidate(self.connect, self.lock, authorize_core, cid)
            sources = selection_status.materials(cid)
            if assessment_core_bridge.enabled():
                try:
                    assessment = assessment_core_bridge.configured(self.connect, self.lock, authorize_core).read(cid)
                except Problem as error:
                    if error.status in (401, 403, 404):
                        raise
                    assessment = {'state': 'unavailable', 'result': None, 'match': {'state': 'unavailable'}}
                except (OSError, ValueError, KeyError, TypeError):
                    assessment = {'state': 'unavailable', 'result': None, 'match': {'state': 'unavailable'}}
            else:
                with self.connect() as db:
                    assessment = selection_status.verified_assessment(db, cid, vid)
            with self.lock, self.connect() as db:
                require(actor(db) == 'manager', 'Доступ только для команды HR Vision.', 403)
                require(person(db, cid)['sourceRevision'] == initial['sourceRevision'],
                        'Материалы кандидата изменились. Обновите карточку.', 409)
                hr_interview = selection_status.evidence(db, person(db, cid))
                required_fields = selection_facts.read(db, 'candidate', cid)
                automation = assessment_core_lifecycle.public_candidate_status(db, cid)
            return {'candidateId': cid, 'materials': sources, 'assessment': assessment,
                    'hrInterview': hr_interview, 'requiredFields': required_fields,
                    'automation': automation}, dict(HEADERS)
        with self.lock, self.connect() as db:
            require(actor(db) == 'manager', 'Доступ только для команды HR Vision.', 403)
            if method == 'GET' and action == 'candidates':
                try:
                    offset, limit = int(query.get('offset', ['0'])[0]), int(query.get('limit', ['200'])[0])
                except ValueError:
                    raise Problem('Проверьте страницу каталога.', 400) from None
                require(offset >= 0 and 1 <= limit <= 500, 'Проверьте страницу каталога.', 400)
                result = directory(db, query.get('vacancyId', [None])[0], offset=offset, limit=limit)
            elif method == 'GET' and action == 'vacancies':
                result = {'vacancies': [vacancy(db, row[0]) for row in db.execute(
                    'SELECT vacancy_id FROM em_vacancies WHERE archived_at IS NULL ORDER BY created_at DESC').fetchall()]}
            elif method == 'POST' and action == 'candidates/update':
                result = edit(db, body)
            elif method == 'POST' and action == 'results':
                with employers._atomic(db):
                    result = import_result(db, body)
            elif method == 'POST' and action in ('requirements', 'matches'):
                with employers._atomic(db):
                    result = (import_requirement if action == 'requirements' else import_match)(db, body)
            elif method == 'POST' and action == 'cohort':
                require(set(body) == {'vacancyId', 'candidateIds'} and isinstance(body['candidateIds'], list)
                        and 1 <= len(body['candidateIds']) <= 100, 'Выберите кандидатов.', 400)
                vacancy(db, body['vacancyId'])
                with employers._atomic(db):
                    for cid in body['candidateIds']:
                        person(db, cid)
                        db.execute('INSERT OR IGNORE INTO sl_cohort VALUES (?,?,?)', (body['vacancyId'], cid, iso(now())))
                        if not assessment_core_bridge.enabled():
                            import assessment_jobs
                            assessment_jobs.enqueue(db, 'candidate', cid, body['vacancyId'])
                result = directory(db, body['vacancyId'])
                result['cohortCandidateIds'] = [r[0] for r in db.execute('SELECT candidate_id FROM sl_cohort WHERE vacancy_id=? ORDER BY candidate_id', (body['vacancyId'],))]
            elif method == 'POST' and action == 'publish':
                result = nominate(db, self.portal, body)
            else:
                raise Problem('Действие не найдено.', 404)
            return result, dict(HEADERS)


def recommendation_nomination_current(db, nid):
    row = db.execute('SELECT candidate_id,vacancy_id,binding_id,recommendation_id,unknown_approval FROM sl_recommendation_nominations WHERE id=?', (nid,)).fetchone()
    if not row:
        return False
    current = recommendations_bridge.latest(db, row[1])
    if not current or current['id'] != row[2] or current['recommendation_id'] != row[3]:
        return False
    if recommendations_bridge.local_versions(db, row[1], json.loads(current['candidate_ids'])) != json.loads(current['input_versions']):
        return False
    match = recommendations_bridge.projection(db, row[1], row[0])
    return bool(match.get('status') == 'scored' and match.get('eligibility') != 'fail'
                and (match.get('eligibility') != 'unknown' or row[4]))


def reconcile_recommendation_nominations(db, portal, cid):
    """Consent transaction records work; the worker verifies sources outside it."""
    if 'sl_recommendation_nominations' not in selection_status.tables(db):
        return
    p = person(db, cid)
    rows = db.execute("SELECT id FROM sl_recommendation_nominations WHERE candidate_id=? AND state IN ('waiting_profile','waiting_consent')", (cid,)).fetchall()
    for (nid,) in rows:
        if not recommendation_nomination_current(db, nid):
            state = 'blocked'
        elif p['searching'] and p['accountActive'] and p['consentReuse']:
            state = 'pending_verification'
        else:
            continue
        db.execute('UPDATE sl_recommendation_nominations SET state=?,updated_at=? WHERE id=?', (state,iso(now()),nid))


def nominate_recommendation(db, portal, body, verified):
    required = {'vacancyId', 'candidateIds', 'requestId', 'recommendationId', 'revision'}
    require(isinstance(body, dict) and required <= set(body) <= required | {'unknownApprovals'}, 'Проверьте версию рекомендации.', 400)
    ids, approvals = body['candidateIds'], body.get('unknownApprovals', [])
    require(isinstance(ids, list) and 1 <= len(ids) <= 100 and all(isinstance(x, str) for x in ids)
            and len(ids) == len(set(ids)) and isinstance(approvals, list) and all(isinstance(x, str) for x in approvals)
            and len(approvals) == len(set(approvals)) and set(approvals) <= set(ids), 'Выберите кандидатов и подтверждения.', 400)
    require(verified['state'] == 'ready' and not verified['staleReasons'] and
            verified['recommendationId'] == body['recommendationId'] and verified['revision'] == body['revision'],
            'Рекомендация изменилась. Пересчитайте подборку.', 409)
    request_id = text(body['requestId'], 120)
    hashed = digest({**body, 'candidateIds': sorted(ids), 'unknownApprovals': sorted(approvals)})
    with employers._atomic(db):
        old = db.execute('SELECT payload_hash,result FROM sl_commands WHERE request_id=?', (request_id,)).fetchone()
        if old:
            require(old[0] == hashed, 'Запрос уже использован для другой подборки.', 409)
            return json.loads(old[1])
        vid = body['vacancyId']
        v = vacancy(db, vid)
        employers.ensure_open(db, v['workspaceId'], vid)
        binding = recommendations_bridge.latest(db, vid)
        require(binding and binding['recommendation_id'] == body['recommendationId'] and
            recommendations_bridge.local_versions(db, vid, json.loads(binding['candidate_ids'])) == json.loads(binding['input_versions']),
            'Исходные данные изменились.', 409)
        items = {i['candidate_id']: i for i in verified['result']['items']}
        unknown = {cid for cid in ids if items.get(cid, {}).get('eligibility') == 'unknown'}
        require(set(approvals) == unknown, 'Явно подтвердите каждый неизвестный обязательный факт.', 409)
        for cid in ids:
            p = person(db, cid)
            item = items.get(cid)
            require(item and item['status'] == 'scored' and item['eligibility'] in ('pass', 'unknown')
                and p['searching'] and selection_status.evidence(db, p)['state'] == 'ready', 'Нужны текущий расчёт и материалы HR-интервью.', 409)
            old_nomination = db.execute('SELECT id,state,recommendation_id FROM sl_recommendation_nominations WHERE candidate_id=? AND vacancy_id=?', (cid,vid)).fetchone()
            if old_nomination and old_nomination[1] == 'blocked' and old_nomination[2] != body['recommendationId']:
                cursor = db.execute('SELECT * FROM sl_recommendation_nominations WHERE id=?', (old_nomination[0],))
                archived = dict(zip([d[0] for d in cursor.description],cursor.fetchone()))
                db.execute('INSERT INTO sl_recommendation_nomination_history VALUES (?,?,?)', (archived['id'],dump(archived),time.time()))
                db.execute('DELETE FROM sl_recommendation_nominations WHERE id=?', (old_nomination[0],))
            require(not db.execute('SELECT 1 FROM cp_applications WHERE candidate_id=? AND vacancy_id=? AND archived_at IS NULL', (cid,vid)).fetchone()
                and not db.execute('SELECT 1 FROM sl_recommendation_nominations WHERE candidate_id=? AND vacancy_id=?', (cid,vid)).fetchone()
                and not db.execute('SELECT 1 FROM sl_nominations WHERE candidate_id=? AND vacancy_id=?', (cid,vid)).fetchone(), 'Кандидат уже добавлен.', 409)
        receipt = {'added': [], 'waitingProfile': [], 'proposed': [], 'pendingVerification': []}
        for cid in ids:
            p = person(db, cid)
            nid = 'nomination_' + secrets.token_hex(12)
            approval = dump({'actor': 'manager', 'recommendationId': body['recommendationId'],
                'inputVersions': json.loads(binding['input_versions']), 'at': iso(now())}) if cid in unknown else None
            db.execute('INSERT INTO sl_recommendation_nominations (id,candidate_id,vacancy_id,recommendation_id,binding_id,unknown_approval,state,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                (nid, cid, vid, body['recommendationId'], binding['id'], approval,
                 'waiting_consent' if p['accountActive'] else 'waiting_profile', iso(now()), iso(now())))
            receipt['added'].append(cid)
            reconcile_recommendation_nominations(db, portal, cid)
            state = db.execute('SELECT state FROM sl_recommendation_nominations WHERE id=?', (nid,)).fetchone()[0]
            if state == 'proposed':
                receipt['proposed'].append(cid)
            elif state == 'pending_verification':
                receipt['pendingVerification'].append(cid)
            else:
                enqueue_invitation(db, nid, portal.origin)
                receipt['waitingProfile'].append(cid)
        db.execute('INSERT INTO sl_commands VALUES (?,?,?)', (request_id, hashed, dump(receipt)))
    return receipt


def retry_recommendation_nomination(db, nid, body, verified):
    require(verified['state'] == 'ready' and not verified['staleReasons'] and
            verified['recommendationId'] == body['recommendationId'] and verified['revision'] == body['revision'],
            'Рекомендация изменилась. Нужны новый расчёт и подтверждение.',409)
    row = db.execute('SELECT state,recommendation_id,candidate_id,vacancy_id FROM sl_recommendation_nominations WHERE id=?',(nid,)).fetchone()
    require(row and row[0] == 'verification_failed' and row[1] == body['recommendationId'] and recommendation_nomination_current(db,nid),
            'Повтор недоступен для этой версии.',409)
    p = person(db,row[2])
    v = vacancy(db,row[3])
    employers.ensure_open(db,v['workspaceId'],row[3])
    require(p['searching'] and p['accountActive'] and p['consentReuse'] and selection_status.evidence(db,p)['state'] == 'ready',
            'Согласие или материалы изменились.',409)
    db.execute("UPDATE sl_recommendation_nominations SET state='pending_verification',attempts=0,retry_at=0,error=NULL,updated_at=? WHERE id=?",(iso(now()),nid))
    return {'nominationId':nid,'state':'pending_verification'}
