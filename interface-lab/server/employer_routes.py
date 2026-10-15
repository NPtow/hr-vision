"""Authenticated employer HTTP orchestration over the canonical candidate model.

The outer HTTP adapter validates Origin/Fetch-Metadata, bounds JSON, and supplies
team_grant only after checking the existing private browser invitation. Provider
and CV parsing calls never run under the shared state lock or SQLite transaction.
"""
import hashlib
import json
import math
import urllib.parse

from domain import Problem, iso, now, require, text
from feedback import vtt_turns
import employer_access as access
import employer_store as store
import employer_workflow as workflow
import vacancy_import


PREFIX = '/api/hr/employer'


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class EmployerRoutes:
    def __init__(self, connect, lock, portal, media, origin):
        self.connect, self.lock, self.portal, self.media, self.origin = connect, lock, portal, media, origin
        import employer_intake as intake
        self.intake = intake
        with self.lock, self.connect() as db:
            # Startup only; workflow's schema migration owns its boot-time DDL.
            workflow.init(db)
            access.init(db)
            store.init(db)
            intake.init(db)
            vacancy_import.init(db)
            db.execute('''CREATE TABLE IF NOT EXISTS em_meeting_transcripts (
              meeting_id TEXT PRIMARY KEY REFERENCES cp_meetings(id),
              application_id TEXT NOT NULL REFERENCES cp_applications(id),
              workspace_id TEXT NOT NULL REFERENCES em_workspaces(id),
              owner_name TEXT NOT NULL, turns TEXT NOT NULL DEFAULT '[]',
              joined_at TEXT, updated_at TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS em_route_commands (
              workspace_id TEXT NOT NULL REFERENCES em_workspaces(id),
              account_id TEXT NOT NULL REFERENCES em_accounts(id),
              request_id TEXT NOT NULL, action TEXT NOT NULL, payload_hash TEXT NOT NULL,
              application_id TEXT NOT NULL REFERENCES cp_applications(id), created_at TEXT NOT NULL,
              PRIMARY KEY(workspace_id,account_id,request_id))''')

    @staticmethod
    def handles(path):
        path = urllib.parse.urlsplit(path).path
        return path == PREFIX or path.startswith(PREFIX + '/')

    def _state(self, db, principal, vacancy_id=None):
        from employer_projection import build_state
        return build_state(db, principal, self.portal, self.media, vacancy_id=vacancy_id)

    def _identity(self, db, headers, body, *, include_rejected=False, include_unapproved=False, allow_archived=False):
        principal = access.authenticate(db, headers)
        aid = body.get('applicationId', body.get('candidate'))
        require(isinstance(aid, str) and 0 < len(aid) <= 160, 'Укажите кандидата.', 400)
        if body.get('candidate') is not None:
            require(body['candidate'] == aid, 'Кандидат не найден.', 404)
        app = store.application(db, principal['workspaceId'], aid, include_rejected=include_rejected,
                                include_unapproved=include_unapproved)
        if not allow_archived:
            store.require_active(app)
        require(body.get('vacancyId') == app['vacancyId'], 'Вакансия не найдена.', 404)
        vacancy = store.vacancy(db, principal['workspaceId'], app['vacancyId'])
        require(body.get('generation') == vacancy['generation'], 'Вакансия изменилась. Обновите кабинет.', 409)
        return principal, app, vacancy

    def _vacancy_import(self, headers, body):
        import vacancy_sources
        with self.lock, self.connect() as db:
            principal = access.authenticate(db, headers)
            config = vacancy_import.ModelConfig.from_env()
            iid, cached, lease = vacancy_import.reserve(db, principal, body, config)
        if cached is not None:
            return cached
        try:
            prepared = vacancy_sources.prepare(body)
            selected, redacted = vacancy_import.chunks(prepared['text'])
            with self.lock, self.connect() as db:
                latest = access.authenticate(db, headers)
                require(latest['workspaceId'] == principal['workspaceId'] and latest['accountId'] == principal['accountId'], 'Нет доступа.', 403)
                vacancy_import.reserve_tokens(db, principal, iid, selected, lease)
            fields, evidence, metadata = vacancy_import.extract(selected, config, iid)
            with self.lock, self.connect() as db:
                latest = access.authenticate(db, headers)
                require(latest['workspaceId'] == principal['workspaceId'] and latest['accountId'] == principal['accountId'], 'Нет доступа.', 403)
                return vacancy_import.finish(db, principal, iid, prepared, selected, fields, evidence, metadata, redacted, lease)
        except BaseException as error:
            with self.lock, self.connect() as db:
                vacancy_import.fail(db, iid, lease, transient=isinstance(error, Problem) and error.status in (401, 403, 429, 502, 503, 504))
            raise

    def _meeting(self, db, app, body, *, current=False):
        mid = text(body.get('meetingId'), 160)
        meeting = self.portal.meeting(db, app['applicationId'], mid)
        require(meeting is not None, 'Встреча не найдена.', 404)
        if current:
            latest = self.portal.meeting(db, app['applicationId'])
            require(latest and latest['id'] == mid, 'Встреча изменилась. Обновите кабинет.', 409)
        if 'revision' in body:
            require(type(body['revision']) is int and meeting['revision'] == body['revision'],
                    'Время встречи изменилось. Обновите кабинет.', 409)
        return meeting

    @staticmethod
    def _same(principal, app, before_principal, before_app):
        require(principal['accountId'] == before_principal['accountId'] and
                principal['workspaceId'] == before_principal['workspaceId'] and
                app['applicationId'] == before_app['applicationId'] and
                app['candidateId'] == before_app['candidateId'], 'Нет доступа.', 403)

    def _access_check(self, headers, body, principal, app, meeting_id, *, screening=False, finish=False, allow_archived=False):
        def check(db):
            latest, current, _ = self._identity(db, headers, body, include_unapproved=finish, allow_archived=allow_archived)
            self._same(latest, current, principal, app)
            meeting = self.portal.interview(db, current['candidateId']) if screening else self._meeting(db, current, body)
            require(meeting and meeting['id'] == meeting_id, 'Встреча изменилась. Обновите кабинет.', 409)
        return check

    def _session(self, headers, body, team_grant, *, personal_only=False):
        require(not body, 'Проверьте поля входа.', 400)
        with self.lock, self.connect() as db:
            principal = access.authenticate(db, headers, required=False)
            if personal_only:
                require(principal and not principal['team'], 'Войдите в кабинет работодателя.', 401)
            auth = None
            if principal:
                token = access._token(headers)
            else:
                require(team_grant is True, 'Войдите в кабинет работодателя.', 401)
                auth = access.team_session(db)
                token = auth['token']
                principal = access.authenticate(db, {'Authorization': 'Bearer ' + token})
            result = {'token': token, 'state': self._state(db, principal)}
            return result, {'Set-Cookie': access.session_cookie(token)} if auth else {}

    def _accounts(self, headers, team_grant):
        result, response_headers = self._session(headers, {}, team_grant)
        state = result['state']
        return {'scope': 'sfera', 'company': state['vacancy'].get('company') or 'HR Vision',
                'accounts': [{'id': 'manager', 'name': state['actorName'], 'role': 'Нанимающий'}]}, response_headers

    def _auth(self, action, headers, body, ip):
        # This committed transaction is separate from credential failures.
        with self.lock, self.connect() as db:
            access.consume_auth_limits(db, action, body, ip)
        with self.lock, self.connect() as db:
            result = getattr(access, action)(db, body)
            principal = access.authenticate(db, {'Authorization': 'Bearer ' + result['token']})
            result['state'] = self._state(db, principal)
            return result, {'Set-Cookie': access.session_cookie(result['token'])}

    def handle(self, method, path, headers, body=None, ip='', team_grant=False):
        parsed = urllib.parse.urlsplit(path)
        route = parsed.path[len(PREFIX):] if self.handles(path) else ''
        require(method in ('GET', 'POST'), 'Недоступный метод.', 405)
        require(body is None or isinstance(body, dict), 'Ожидается объект запроса.', 400)
        body = body or {}
        query = urllib.parse.parse_qs(parsed.query)
        require(len(query.get('vacancyId', [])) <= 1, 'Укажите одну вакансию.', 400)
        vacancy_id = query.get('vacancyId', [None])[0]
        if route in ('/register', '/login') and method == 'POST':
            return self._auth(route[1:], headers, body, ip)
        if route == '/session':
            # Public entry restores only a registered employer, never an internal
            # team account merely because this browser has a recruiter grant.
            return self._session(headers, body, team_grant, personal_only=query.get('personal') == ['1'])
        if route == '/accounts' and method == 'GET':
            return self._accounts(headers, team_grant)
        if route == '/logout' and method == 'POST':
            with self.lock, self.connect() as db:
                return {'ok': True}, {'Set-Cookie': access.logout(db, headers)}
        if route == '/room' and method == 'POST':
            return self._room(headers, body), {}
        if route == '/materials' and method == 'POST':
            return self._materials(headers, body), {}
        if route == '/screening/materials' and method == 'POST':
            return self._materials(headers, body, screening=True), {}
        if route == '/feedback/questions' and method == 'POST':
            return self._questions(headers, body), {}
        if route == '/action' and method == 'POST' and body.get('action') == 'meeting.finish':
            return self._finish(headers, body), {}
        if route == '/intake' and method == 'POST':
            return self._intake(headers, body), {}
        if route == '/intake/recording' and method == 'POST':
            return self._intake_recording(headers, body), {}
        if route == '/vacancies/import' and method == 'POST':
            return self._vacancy_import(headers, body), {}
        with self.lock, self.connect() as db:
            principal = access.authenticate(db, headers)
            wid = principal['workspaceId']
            if route == '/state' and method == 'GET':
                return self._state(db, principal, vacancy_id), {}
            if route == '/vacancies' and method == 'GET':
                if principal['team']:
                    store.adopt_existing(db, wid)
                return {'vacancies': store.list_vacancies(db, wid)}, {}
            if route == '/vacancies' and method == 'POST':
                fields = {key: value for key, value in body.items() if key != 'requestId'}
                return {'vacancy': store.create_vacancy(db, wid, fields, request_id=body.get('requestId'))}, {}
            if route == '/vacancies/update' and method == 'POST':
                fields = {key: value for key, value in body.items() if key not in ('vacancyId', 'revision', 'requestId')}
                return {'vacancy': store.update_vacancy(db, wid, body.get('vacancyId'), fields,
                  expected_revision=body.get('revision'), request_id=body.get('requestId'))}, {}
            if route == '/vacancies/archive' and method == 'POST':
                return {'vacancy': store.archive_vacancy(db, wid, body.get('vacancyId'),
                  expected_revision=body.get('revision'))}, {}
            if route == '/batches' and method == 'POST':
                require(principal['team'], 'Только внутренняя интеграция может передать подборку.', 403)
                vid = text(body.get('vacancyId'), 160)
                owner = db.execute('SELECT workspace_id FROM em_vacancies WHERE vacancy_id=?', (vid,)).fetchone()
                if not owner:
                    store.adopt_vacancy(db, wid, vid)
                    owner = (wid,)
                require('workspaceId' not in body or body['workspaceId'] == owner[0], 'Вакансия не найдена.', 404)
                fields = {key: value for key, value in body.items() if key not in ('vacancyId', 'workspaceId')}
                return {'batch': store.import_batch(db, owner[0], vid, fields, portal=self.portal)}, {}
            if route == '/intake' and method == 'GET':
                store.vacancy(db, wid, vacancy_id)
                return self.intake.list_candidates(db, wid, vacancy_id), {}
            if route == '/intake/archive' and method == 'POST':
                store.vacancy(db, wid, body.get('vacancyId'))
                return self.intake.archive(db, wid, body['vacancyId'], body), {}
            if route == '/intake/comment' and method == 'POST':
                store.vacancy(db, wid, body.get('vacancyId'))
                return self.intake.screening_comment(db, wid, body['vacancyId'], body), {}
            if route == '/intake/review' and method == 'POST':
                store.vacancy(db, wid, body.get('vacancyId'))
                return self.intake.review_reject(db, wid, body['vacancyId'], body), {}
            if route.startswith('/intake/files/') and method == 'GET':
                fid = urllib.parse.unquote(route.removeprefix('/intake/files/'))
                return self.intake.file(db, wid, fid)
            if route == '/screening/resume' and method == 'POST':
                _, app, _ = self._identity(db, headers, body)
                return self.portal.download_resume(db, app['candidateId']), {}
            if route == '/review/respond' and method == 'POST':
                return self._respond(db, headers, body), {}
            if route == '/action' and method == 'POST':
                return self._action(db, headers, body), {}
            raise Problem('Действие не найдено.', 404)

    def _intake_recording(self, headers, body):
        with self.lock, self.connect() as db:
            principal = access.authenticate(db, headers)
            source = self.intake.recording_source(db, principal['workspaceId'], body)
        # Remote calls must not hold the shared lock or an open DB transaction.
        result = self.intake.resolve_recording(source)
        with self.lock, self.connect() as db:
            latest = access.authenticate(db, headers)
            require(latest['accountId'] == principal['accountId'] and
                    latest['workspaceId'] == principal['workspaceId'], 'Нет доступа.', 403)
            require(self.intake.recording_source(db, latest['workspaceId'], body) == source,
                    'Запись изменилась. Откройте её ещё раз.', 409)
        return result

    def _intake(self, headers, body):
        with self.lock, self.connect() as db:
            principal = access.authenticate(db, headers)
            vid = store.ensure_open(db, principal['workspaceId'], body.get('vacancyId'))['id']
            previous = self.intake.existing(db, principal['workspaceId'], vid, body)
            if previous is not None:
                return previous
        prepared = self.intake.prepare(body)
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            latest = access.authenticate(db, headers)
            require(latest['accountId'] == principal['accountId'] and latest['workspaceId'] == principal['workspaceId'],
                    'Нет доступа.', 403)
            store.ensure_open(db, latest['workspaceId'], vid)
            return self.intake.add(db, latest['workspaceId'], vid, body, prepared=prepared)

    def _command(self, db, principal, app, body):
        rid = body.get('requestId')
        if rid is None:
            return None, None, False
        rid = text(rid, 120)
        fingerprint = hashlib.sha256(_dump({key: value for key, value in body.items() if key != 'requestId'}).encode()).hexdigest()
        row = db.execute('''SELECT action,payload_hash,application_id FROM em_route_commands
          WHERE workspace_id=? AND account_id=? AND request_id=?''',
          (principal['workspaceId'], principal['accountId'], rid)).fetchone()
        if row:
            require(tuple(row) == (body['action'], fingerprint, app['applicationId']),
                    'Этот запрос уже использован для другого действия.', 409)
        return rid, fingerprint, bool(row)

    def _respond(self, db, headers, body):
        """One initial review answer over either real application or intake draft."""
        fields = {'source', 'id', 'vacancyId', 'decision', 'expectedRevision', 'requestId'}
        require(fields <= set(body) and set(body) <= fields | {'generation', 'rejectionAnswers'},
                'Проверьте ответ по кандидату.', 400)
        require(body['source'] in ('application', 'intake'), 'Выберите источник кандидата.', 400)
        require(body['decision'] in ('invite', 'reject'), 'Выберите ответ.', 400)
        cid, vid, rid = text(body['id'], 160), text(body['vacancyId'], 160), text(body['requestId'], 120)
        require(not any(ord(ch) < 32 or ord(ch) == 127 for ch in rid), 'Некорректный запрос.', 400)
        require(type(body['expectedRevision']) is int and 0 <= body['expectedRevision'] < 2 ** 53,
                'Обновите карточку кандидата.', 400)
        normalized = {**body, 'id': cid, 'vacancyId': vid, 'requestId': rid}
        if body['decision'] == 'reject':
            normalized['rejectionAnswers'] = workflow.rejection_answers(body.get('rejectionAnswers'))
        else:
            require('rejectionAnswers' not in body, 'Причина отказа не нужна для приглашения.', 400)
        if body['source'] == 'intake':
            require('generation' not in body, 'Проверьте источник кандидата.', 400)
            principal = access.authenticate(db, headers)
            return self.intake.review_respond(db, principal['workspaceId'], vid,
                                             {k: v for k, v in normalized.items() if k != 'source'})
        require('generation' in body, 'Обновите вакансию перед ответом.', 409)
        db.execute('BEGIN IMMEDIATE')
        command = {**normalized, 'action': 'review.respond', 'applicationId': cid}
        # A committed retry can read its minimal receipt after consent changes.
        # New writes below still require the normal active/approved identity.
        principal, app, _ = self._identity(db, headers, command, include_rejected=True,
                                          include_unapproved=True, allow_archived=True)
        rid, fingerprint, replay = self._command(db, principal, app, command)
        wid = principal['workspaceId']
        if not replay:
            app = store.application(db, wid, cid, include_rejected=True)
            store.require_active(app)
            store.ensure_open(db, wid, vid)
            workflow.check_revision(db, cid, body['expectedRevision'])
            require(app['reviewState'] == 'proposed' and workflow.review_response(db, cid) is None and
                    workflow.load(db, cid)['decision'] != 'declined',
                    'Ответ по кандидату уже сохранён.', 409)
            if body['decision'] == 'invite':
                store.request_invitation(db, wid, cid, request_id=rid, expected_revision=body['expectedRevision'])
            else:
                self._reject(db, principal, app, command)
            db.execute('''INSERT INTO em_route_commands VALUES (?,?,?,?,?,?,?)''',
                       (wid, principal['accountId'], rid, command['action'], fingerprint, cid, iso(now())))
        return {'response': workflow.review_response(db, cid),
                'reviewRejection': workflow.review_rejection(db, cid),
                'state': self._state(db, principal, vid)}

    def _action(self, db, headers, body):
        require(isinstance(body.get('action'), str), 'Укажите действие.', 400)
        action = {'candidate.skip': 'review.skip', 'candidate.reject': 'review.reject', 'reject': 'review.reject'}.get(body['action'], body['action'])
        allowed = workflow.MANAGER_ACTIONS | {'review.skip', 'review.reject', 'meeting.propose',
                    'meeting.reschedule', 'meeting.cancel', 'meeting.joined', 'meeting.transcript'}
        require(action in allowed, 'Это действие недоступно работодателю.', 403)
        db.execute('BEGIN IMMEDIATE')
        principal, app, vacancy = self._identity(db, headers, body, include_rejected=action == 'review.reject')
        rid, fingerprint, replay = self._command(db, principal, app, body)
        if replay:
            return self._state(db, principal, app['vacancyId'])
        wid, aid, manager_id = principal['workspaceId'], app['applicationId'], 'workspace:' + principal['workspaceId']
        events = []
        if action == 'review.skip':
            store.skip(db, wid, aid, request_id=rid, expected_revision=body.get('vacancyRevision'))
        elif action == 'review.reject':
            self._reject(db, principal, app, body)
        elif action in ('meeting.propose', 'meeting.reschedule', 'meeting.cancel'):
            store.ensure_open(db, wid, app['vacancyId'])
            if 'expectedRevision' in body:
                workflow.check_revision(db, aid, body['expectedRevision'])
            workflow.register_stages(db, aid, vacancy_id=app['vacancyId'])
            command = {**body, 'applicationId': aid}
            if body.get('previousMeetingId') is not None:
                require(body.get('meetingId') in (None, body['previousMeetingId']),
                        'Встреча изменилась. Обновите кабинет.', 409)
                command['meetingId'] = body['previousMeetingId']
            current = self.portal.meeting(db, aid)
            if action == 'meeting.cancel':
                self._meeting(db, app, command, current=True)
                self.portal.cancel_meeting(db, command, manager_id=manager_id)
            elif action == 'meeting.reschedule' or current and current['status'] in ('pending', 'confirmed'):
                self._meeting(db, app, command, current=True)
                meeting = self.portal.reschedule_meeting(db, command, manager_id=manager_id)
                store.mark_selected(db, wid, aid, meeting['id'])
            else:
                if command.get('meetingId') is not None:
                    self._meeting(db, app, command, current=True)
                meeting = self.portal.propose_meeting(db, command, manager_id=manager_id)
                store.mark_selected(db, wid, aid, meeting['id'])
            if self.portal.meeting(db, aid) != current:
                workflow.meeting_changed(db, aid)
            store.bump_revision(db, wid, app['vacancyId'])
        elif action in ('meeting.joined', 'meeting.transcript'):
            meeting = self._meeting(db, app, body, current=True)
            require(meeting['status'] == 'live' and meeting['room'], 'Сначала войдите во встречу.', 409)
            saved = db.execute('''SELECT owner_name,turns,joined_at FROM em_meeting_transcripts
              WHERE meeting_id=? AND workspace_id=? AND application_id=?''', (meeting['id'], wid, aid)).fetchone()
            turns, joined_at = (json.loads(saved[1]), saved[2]) if saved else ([], None)
            if action == 'meeting.joined':
                joined_at = joined_at or iso(now())
            else:
                require(joined_at is not None, 'Сначала подтвердите вход во встречу.', 409)
                batch = body.get('turns')
                require(isinstance(batch, list) and len(batch) <= 25, 'Некорректная расшифровка.', 400)
                existing = {turn['id']: turn for turn in turns}
                for item in batch:
                    require(isinstance(item, dict), 'Некорректная реплика.', 400)
                    tid, words = text(item.get('id'), 300), text(item.get('text'), 1500)
                    seconds = item.get('seconds')
                    require(type(seconds) in (int, float) and math.isfinite(seconds) and 0 <= seconds <= 14400,
                            'Некорректное время реплики.', 400)
                    turn = {'id': tid, 'text': words, 'seconds': seconds, 'source': 'live'}
                    require(tid not in existing or existing[tid] == turn, 'Реплика уже сохранена с другим текстом.', 409)
                    if tid not in existing:
                        turns.append(turn)
                        existing[tid] = turn
                require(len(turns) <= 1500, 'Достигнут предел расшифровки встречи.', 413)
            db.execute('''INSERT INTO em_meeting_transcripts VALUES (?,?,?,?,?,?,?) ON CONFLICT(meeting_id)
              DO UPDATE SET turns=excluded.turns,joined_at=excluded.joined_at,updated_at=excluded.updated_at''',
              (meeting['id'], aid, wid, saved[0] if saved else principal['name'], _dump(turns), joined_at, iso(now())))
            store.bump_revision(db, wid, app['vacancyId'])
        else:
            if action == 'decision' and body.get('value') == 'declined':
                require(app['reviewState'] != 'proposed' or not app.get('invitationRequestedAt'),
                        'Ответ по кандидату уже сохранён.', 409)
            result = workflow.manager_action(db, aid, action, body, manager_id=manager_id, vacancy_id=app['vacancyId'])
            events = result['events']
            if action == 'decision' and body.get('value') == 'declined':
                store.reject(db, wid, aid, request_id=rid)
            if not result.get('replayed'):
                store.bump_revision(db, wid, app['vacancyId'])
        self.portal.workflow_events(db, events)
        if rid is not None:
            db.execute('INSERT INTO em_route_commands VALUES (?,?,?,?,?,?,?)',
              (wid, principal['accountId'], rid, body['action'], fingerprint, aid, iso(now())))
        return self._state(db, principal, app['vacancyId'])

    def _reject(self, db, principal, app, body):
        aid, wid = app['applicationId'], principal['workspaceId']
        # The older initial-review endpoint obeys the same one-answer rule.
        # A later selected-stage outcome remains a separate workflow action.
        require(app['reviewState'] != 'proposed' or not app.get('invitationRequestedAt'),
                'Ответ по кандидату уже сохранён.', 409)
        if 'expectedRevision' in body:
            workflow.check_revision(db, aid, body['expectedRevision'])
        if 'rejectionAnswers' in body:
            require(isinstance(body['rejectionAnswers'], dict), 'Проверьте ответы о причине отказа.', 400)
        meeting = self.portal.meeting(db, aid)
        require(not meeting or meeting['status'] != 'live', 'Сначала завершите активную встречу.', 409)
        if meeting and meeting['status'] in ('pending', 'confirmed'):
            self.portal.cancel_meeting(db, {'applicationId': aid, 'meetingId': meeting['id'], 'revision': meeting['revision']},
                                       manager_id='workspace:' + wid)
        result = workflow.reject(db, aid, manager_id='workspace:' + wid,
                                 vacancy_id=app['vacancyId'], request_id=body.get('requestId'),
                                 rejection_answers=body.get('rejectionAnswers'),
                                 expected_revision=body.get('expectedRevision'))
        self.portal.workflow_events(db, result['events'])
        store.reject(db, wid, aid, request_id=body.get('requestId'), expected_revision=body.get('vacancyRevision'))

    def _room(self, headers, body):
        require(body.get('consent') is True, 'Подтвердите запись разговора.', 400)
        with self.lock, self.connect() as db:
            principal, app, _ = self._identity(db, headers, body)
            meeting = self._meeting(db, app, body, current=True)
            require(meeting['status'] in ('confirmed', 'live'), 'Кандидат должен подтвердить время встречи.', 409)
        result = self.portal.join(app['candidateId'], app['applicationId'], True, owner=True,
              owner_name=principal['name'], owner_id='manager', meeting_id=meeting['id'],
              access_check=self._access_check(headers, body, principal, app, meeting['id']))
        with self.lock, self.connect() as db:
            latest, current, _ = self._identity(db, headers, body)
            self._same(latest, current, principal, app)
            current_meeting = self._meeting(db, current, body, current=True)
            require(current_meeting['status'] == 'live', 'Встреча уже завершена.', 409)
            db.execute('''INSERT INTO em_meeting_transcripts VALUES (?,?,?,?,?,NULL,?)
              ON CONFLICT(meeting_id) DO NOTHING''',
              (meeting['id'], app['applicationId'], principal['workspaceId'], principal['name'], '[]', iso(now())))
        return result

    def _finish(self, headers, body):
        with self.lock, self.connect() as db:
            principal, app, _ = self._identity(db, headers, body, include_unapproved=True)
            meeting = self._meeting(db, app, body)
            require(meeting['status'] in ('live', 'completed'), 'Сначала проведите встречу.', 409)
        self.portal.complete(app['candidateId'], app['applicationId'], meeting_id=meeting['id'],
            access_check=self._access_check(headers, body, principal, app, meeting['id'], finish=True))
        with self.lock, self.connect() as db:
            latest, current, _ = self._identity(db, headers, body, include_unapproved=True)
            self._same(latest, current, principal, app)
            finished = self._meeting(db, current, body)
            require(finished['status'] == 'completed', 'Не удалось завершить встречу.', 409)
            if meeting['status'] != 'completed':
                store.bump_revision(db, latest['workspaceId'], app['vacancyId'])
            return self._state(db, latest, app['vacancyId'])

    def _materials(self, headers, body, *, screening=False):
        with self.lock, self.connect() as db:
            principal, app, _ = self._identity(db, headers, body, allow_archived=True)
            meeting = self.portal.interview(db, app['candidateId']) if screening else self._meeting(db, app, body)
            require(meeting and meeting['status'] == 'completed', 'Материалы доступны после встречи.', 409)
        result = self.portal.materials(app['candidateId'], None if screening else app['applicationId'], meeting_id=meeting['id'],
            access_check=self._access_check(headers, body, principal, app, meeting['id'], screening=screening, allow_archived=True))
        with self.lock, self.connect() as db:
            latest, current, _ = self._identity(db, headers, body, allow_archived=True)
            self._same(latest, current, principal, app)
            checked = self.portal.interview(db, current['candidateId']) if screening else self._meeting(db, current, body)
            require(checked and checked['id'] == meeting['id'] and checked['status'] == 'completed'
                    and checked['room'] == meeting['room'], 'Встреча изменилась. Обновите кабинет.', 409)
        return result

    def _questions(self, headers, body):
        with self.lock, self.connect() as db:
            principal, app, _ = self._identity(db, headers, body)
            meeting = self._meeting(db, app, body)
            require(meeting['status'] == 'completed', 'Вопросы доступны после встречи.', 409)
            existing = workflow.meeting_feedback(db, app['applicationId'], meeting['id'], vacancy_id=app['vacancyId'])
            if existing['questionnaire']:
                return {'status': 'ready', 'questionnaire': existing['questionnaire'], 'draft': existing['feedbackDraft']}
            saved = db.execute('''SELECT owner_name,turns FROM em_meeting_transcripts
              WHERE meeting_id=? AND application_id=? AND workspace_id=?''',
              (meeting['id'], app['applicationId'], principal['workspaceId'])).fetchone()
            owner_name, turns = (saved[0], json.loads(saved[1])) if saved else (principal['name'], [])
        status = 'missing'
        try:
            result = self.portal.materials(app['candidateId'], app['applicationId'], meeting_id=meeting['id'],
                access_check=self._access_check(headers, body, principal, app, meeting['id']))
            transcripts = result.get('transcripts', [])
            provided = [turn for transcript in transcripts for turn in vtt_turns(transcript.get('text', ''), owner_name)]
            turns = provided or turns
            channel = result.get('transcription', {}).get('status')
            status = 'processing' if channel in ('processing', 'absent') else 'unavailable' if channel in ('error', 'unavailable') else 'missing'
        except Problem:
            status = 'unavailable'
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            latest, current, _ = self._identity(db, headers, body)
            self._same(latest, current, principal, app)
            checked = self._meeting(db, current, body)
            require(checked['status'] == 'completed' and checked['room'] == meeting['room'],
                    'Встреча изменилась. Обновите кабинет.', 409)
            prepared = workflow.prepare_questions(db, app['applicationId'], meeting['id'],
                turns[:1500], vacancy_id=app['vacancyId'])
            if prepared['status'] != 'ready':
                prepared['status'] = status
            return prepared
