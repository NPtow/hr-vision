"""Connect durable campaigns to the existing, exclusively owned HH browser.

No browser is launched here. With no bridge configuration the API still serves
campaign drafts and booking; no network or sender thread starts at import time.
Only an operator's explicit discovery request installs a recurring applicant
source. A campaign must also be running before its source or sender is polled.
"""
import observability
import json
import os
import re
import threading
import time
from urllib.parse import urlsplit

from domain import Problem, require
from hh_browser import BrowserBridgeClient, BrowserError
from hh_browser.adapter import hh_url

PREFIX = '/api/hr/outreach/'
HEADERS = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}
ERRORS = {
    'login_required': 'Войдите в HH в виртуальном браузере.',
    'captcha': 'В HH появилась проверка. Пройдите её в виртуальном браузере.',
    'selectors_not_calibrated': 'Нужно проверить элементы HH в авторизованном браузере.',
    'bridge_unavailable': 'Виртуальный браузер пока недоступен.',
    'bridge_unauthorized': 'Не настроен доступ к виртуальному браузеру.',
    'account_mismatch': 'В браузере открыт другой аккаунт HH.',
    'manual_browser_owner': 'Виртуальный браузер занят вручную.',
    'browser_paused': 'Работа виртуального браузера приостановлена.',
    'page_structure_changed': 'Страница HH изменилась. Отправка приостановлена.',
}


def safe_error(code):
    return ERRORS.get(code, 'Не удалось проверить HH. Отправка приостановлена.')


def queue_reason(code):
    return {'login_required': 'logged_out', 'captcha': 'challenge',
            'account_mismatch': 'identity_mismatch', 'recipient_mismatch': 'identity_mismatch',
            'selectors_not_calibrated': 'ui_changed', 'page_structure_changed': 'ui_changed',
            'bridge_unavailable': 'transport_error', 'campaign_paused': 'campaign_paused',
            'booking_unavailable': 'booking_unavailable'}.get(code, 'unconfirmed')


def source_url(value):
    try:
        return hh_url(value)
    except (BrowserError, ValueError):
        raise Problem('Нужна ссылка на страницу HH.', 400) from None


class OutreachBrowserService:
    def __init__(self, engine, portal, *, bridge=None, account_id='', clock=time.time,
                 worker_enabled=False):
        self.engine, self.portal, self.bridge = engine, portal, bridge
        self.account_id, self.clock = account_id, clock
        self.worker_enabled = worker_enabled
        self.operation = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.last_error = None
        self.last_media_check = 0
        with engine.db(write=True) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS ho_browser_sources (
              campaign_id TEXT PRIMARY KEY REFERENCES ho_campaigns(id), source TEXT NOT NULL,
              checked REAL NOT NULL, next_check REAL NOT NULL, error TEXT, complete INTEGER NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS ho_media_checks (
              interview_id TEXT PRIMARY KEY, checked REAL NOT NULL, next_check REAL NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS ho_browser_operations (
              campaign_id TEXT PRIMARY KEY REFERENCES ho_campaigns(id), operation_id TEXT NOT NULL,
              source TEXT NOT NULL,state TEXT NOT NULL,result TEXT NOT NULL,error TEXT,updated REAL NOT NULL)''')

    @classmethod
    def from_env(cls, engine, portal):
        token = os.environ.get('HR_HH_BROWSER_TOKEN', '')
        account = os.environ.get('HR_HH_BROWSER_ACCOUNT', '')
        url = os.environ.get('HR_HH_BROWSER_URL', '')
        bridge = BrowserBridgeClient(url, token, timeout=120) if url and len(token) >= 32 and account else None
        return cls(engine, portal, bridge=bridge, account_id=account,
                   worker_enabled=os.environ.get('HR_HH_BROWSER_WORKER_ENABLED') == '1')

    @staticmethod
    def handles(raw_path):
        path = urlsplit(raw_path).path
        return path in (PREFIX + 'browser', PREFIX + 'browser/resume', PREFIX + 'browser/manual') or bool(
            re.fullmatch(re.escape(PREFIX) + r'campaigns/[A-Za-z0-9_-]+/(discover|discovery)', path)) or bool(
            re.fullmatch(re.escape(PREFIX) + r'jobs/[A-Za-z0-9_-]+/acknowledge', path))

    def browser_state(self):
        state = {'configured': bool(self.bridge), 'loggedIn': False, 'accountId': self.account_id,
                 'busy': False, 'captcha': False, 'calibrationReady': False, 'paused': False,
                 'manual': False, 'lastCheckedAt': None, 'error': None,
                 'sendingEnabled': bool(self.engine.sending_enabled and self.worker_enabled)}
        if not self.bridge:
            state['error'] = 'Виртуальный браузер ещё не подключён к рассылке.'
            return state
        try:
            observed = self.bridge.status()
            for key in ('loggedIn', 'busy', 'captcha', 'paused', 'manual'):
                state[key] = observed.get(key) is True
            state['calibrationReady'] = observed.get('selectorsCalibrated') is True
            state['lastCheckedAt'] = observed.get('lastCheckedAt')
            code = observed.get('error')
            if state['calibrationReady'] and state['loggedIn'] and observed.get('accountId') != self.account_id:
                state['loggedIn'], code = False, 'account_mismatch'
            if code:
                state['error'] = safe_error(code)
            elif not state['calibrationReady']:
                state['error'] = safe_error('selectors_not_calibrated')
        except BrowserError as error:
            state['error'] = safe_error(error.code)
        return state

    def handle(self, method, raw_path, headers, body, manager_actor):
        with self.engine.db() as db:
            require(manager_actor(db) == 'manager', 'Доступ только для команды HR Vision.', 403)
        parsed = urlsplit(raw_path)
        require(not parsed.query and headers.get('Sec-Fetch-Site') != 'cross-site', 'Некорректный запрос.', 403)
        path = parsed.path
        if method == 'GET' and path == PREFIX + 'browser':
            return self.browser_state(), dict(HEADERS)
        poll = re.fullmatch(re.escape(PREFIX) + r'campaigns/([A-Za-z0-9_-]+)/discovery', path)
        if method == 'GET' and poll:
            self.engine.campaign(poll[1])
            if self.operation.acquire(blocking=False):
                try:
                    self.poll_discovery(poll[1])
                finally:
                    self.operation.release()
            return self.discovery_state(poll[1]), dict(HEADERS)
        require(method == 'POST' and isinstance(body, dict), 'Нужен JSON-запрос.', 400)
        require(self.bridge is not None, 'Виртуальный браузер ещё не подключён.', 503)
        acknowledgment = re.fullmatch(re.escape(PREFIX) + r'jobs/([A-Za-z0-9_-]+)/acknowledge', path)
        if acknowledgment:
            require(body.get('leaveUnknown') is True, 'Подтвердите продолжение без повторной отправки.', 400)
            with self.engine.db() as db:
                job = db.execute("SELECT attempt_id,account_id FROM ho_outbox WHERE id=? AND state='unknown'",
                                 (acknowledgment[1],)).fetchone()
            require(job and job['account_id'] == self.account_id, 'Попытка не найдена.', 404)
            try:
                self.bridge.acknowledge(job['attempt_id'])
            except BrowserError as error:
                raise Problem(safe_error(error.code), 409) from None
            # Keep the unknown campaign state and never release its dedupe guard.
            return {'acknowledged': True, 'retryAllowed': False}, dict(HEADERS)
        if path == PREFIX + 'browser/resume':
            try:
                self.bridge.resume()
                self.engine.unblock_account(self.account_id)
            except BrowserError as error:
                raise Problem(safe_error(error.code), 409) from None
            return self.browser_state(), dict(HEADERS)
        if path == PREFIX + 'browser/manual':
            require(type(body.get('enabled')) is bool, 'Укажите режим браузера.', 400)
            try:
                self.bridge.manual(body['enabled'])
            except BrowserError as error:
                raise Problem(safe_error(error.code), 409) from None
            return self.browser_state(), dict(HEADERS)
        match = re.fullmatch(re.escape(PREFIX) + r'campaigns/([A-Za-z0-9_-]+)/discover', path)
        require(match, 'Не найдено.', 404)
        campaign = self.engine.campaign(match[1])
        require(campaign['accountId'] == self.account_id, 'Выберите подключённый аккаунт HH.', 409)
        limit = campaign['limit']
        if campaign['mode'] == 'direct':
            urls = body.get('resumeUrls')
            require(isinstance(urls, list) and 1 <= len(urls) <= limit,
                    'Добавьте ссылки на резюме в пределах лимита кампании.', 400)
            source = {'kind': 'direct', 'accountId': self.account_id,
                      'resumeUrls': [source_url(url) for url in urls], 'limit': limit}
        else:
            source = {'kind': 'vacancy_applicants', 'accountId': self.account_id,
                      'vacancyId': campaign['vacancyId'], 'listUrl': source_url(body.get('vacancyUrl')),
                      'limit': limit}
        require(self.operation.acquire(blocking=False), 'Браузер занят. Повторите после завершения.', 409)
        try:
            return self.begin_discovery(campaign['id'], source), dict(HEADERS)
        except BrowserError as error:
            raise Problem(safe_error(error.code), 409) from None
        finally:
            self.operation.release()

    def discovery_state(self, campaign_id):
        with self.engine.db() as db:
            row = db.execute('SELECT * FROM ho_browser_operations WHERE campaign_id=?', (campaign_id,)).fetchone()
        if not row:
            return {'status': 'idle'}
        return {'operationId': row['operation_id'], 'status': 'running' if row['state'] == 'continuing' else row['state'],
                'result': json.loads(row['result']), 'error': safe_error(row['error']) if row['error'] else None}

    def begin_discovery(self, campaign_id, source):
        # An explicit discovery request survives tab closure and API restart.
        with self.engine.db() as db:
            active = db.execute("SELECT campaign_id FROM ho_browser_operations WHERE state IN ('queued','running','continuing')").fetchone()
        require(not active, 'Уже загружаем кандидатов. Дождитесь завершения.', 409)
        operation = self.bridge.begin_discovery(source)
        result = {'imported': 0, 'repeated': 0, 'skipped': 0, 'complete': False, 'errors': []}
        with self.engine.db(write=True) as db:
            db.execute('INSERT OR REPLACE INTO ho_browser_operations VALUES (?,?,?,?,?,NULL,?)',
                       (campaign_id, operation['operationId'], json.dumps(source), operation['status'],
                        json.dumps(result), self.clock()))
        return self.discovery_state(campaign_id)

    def poll_discovery(self, campaign_id=None):
        with self.engine.db() as db:
            if campaign_id:
                row = db.execute("SELECT * FROM ho_browser_operations WHERE campaign_id=? AND state IN ('queued','running','continuing')", (campaign_id,)).fetchone()
            else:
                row = db.execute("SELECT * FROM ho_browser_operations WHERE state IN ('queued','running','continuing') ORDER BY updated LIMIT 1").fetchone()
            row = dict(row) if row else None
        if not row or not self.bridge:
            return False
        if row['state'] == 'continuing':
            try:
                self._next_discovery(row['campaign_id'], json.loads(row['source']))
            except BrowserError:
                self.last_error = 'transport_error'
            return True
        try:
            operation = self.bridge.discovery_status(row['operation_id'])
        except BrowserError as error:
            if error.code in ('operation_not_found', 'invalid_identifier'):
                with self.engine.db(write=True) as db:
                    db.execute("UPDATE ho_browser_operations SET state='interrupted',error=?,updated=? WHERE campaign_id=?",
                               (error.code, self.clock(), row['campaign_id']))
                return False
            self.last_error = queue_reason(error.code)
            return True
        state = operation['status']
        if state in ('queued', 'running'):
            return True
        campaign_id, source = row['campaign_id'], json.loads(row['source'])
        if state != 'completed':
            with self.engine.db(write=True) as db:
                db.execute("UPDATE ho_browser_operations SET state='failed',error=?,updated=? WHERE campaign_id=?",
                           (operation.get('error') or (operation.get('result') or {}).get('error') or 'bridge_unavailable', self.clock(), campaign_id))
            self._source_error(campaign_id, 'bridge_unavailable')
            return False
        observed = operation['result']
        result = self.import_page(campaign_id, source, observed)
        cumulative = json.loads(row['result'])
        for key in ('imported', 'repeated', 'skipped'):
            cumulative[key] += result[key]
        cumulative['errors'] = list(set(cumulative['errors'] + result['errors']))
        campaign = self.engine.campaign(campaign_id)
        with self.engine.db() as db:
            count = db.execute('SELECT COUNT(*) FROM ho_recipients WHERE campaign_id=?', (campaign_id,)).fetchone()[0]
        cursor = observed.get('cursor')
        more = not observed.get('complete') and bool(cursor) and count < campaign['limit'] and campaign['status'] != 'cancelled'
        require(not more or cursor != source.get('cursor'), 'HH не продвинул список откликов.', 409)
        cumulative['complete'] = not more
        # Persist page import checkpoint before requesting the next browser page.
        # Re-import on crash is idempotent; no send is performed during discovery.
        if more:
            source['cursor'] = cursor
            source['limit'] = min(campaign['limit'] - count, 25)
            with self.engine.db(write=True) as db:
                db.execute("UPDATE ho_browser_operations SET state='continuing',source=?,result=?,updated=? WHERE campaign_id=?",
                           (json.dumps(source), json.dumps(cumulative), self.clock(), campaign_id))
            self._next_discovery(campaign_id, source)
            return True
        with self.engine.db(write=True) as db:
            db.execute("UPDATE ho_browser_operations SET state='completed',result=?,updated=? WHERE campaign_id=?",
                       (json.dumps(cumulative), self.clock(), campaign_id))
        if campaign['status'] == 'running':
            self.engine.queue(campaign_id)
        return False

    def _next_discovery(self, campaign_id, source):
        operation = self.bridge.begin_discovery(source)
        with self.engine.db(write=True) as db:
            db.execute('UPDATE ho_browser_operations SET operation_id=?,state=?,updated=? WHERE campaign_id=?',
                       (operation['operationId'], operation['status'], self.clock(), campaign_id))

    def import_page(self, campaign_id, source, observed):
        require(isinstance(observed, dict) and isinstance(observed.get('candidates'), list),
                'Браузер не вернул список кандидатов.', 502)
        imported = repeated = skipped = 0
        errors = []
        for item in observed['candidates'][:source['limit']]:
            if not isinstance(item, dict) or item.get('discoveryStatus') != 'ready' or not item.get('cvAvailable'):
                skipped += 1
                continue
            record = {**item, 'responseId': item.get('responseId') or None,
                      'vacancyId': item.get('vacancyId') or None}
            try:
                result = self.engine.import_recipients(campaign_id, [record])
                imported += result['imported']
                repeated += result['repeated']
            except Problem:
                # Bad source identities never become guessed/merged candidates.
                skipped += 1
                errors.append('Не удалось однозначно связать один из откликов с кандидатом.')
        with self.engine.db(write=True) as db:
            next_source = {**source}
            # A complete pass starts at the first page next time for new replies.
            if observed.get('complete'):
                next_source.pop('cursor', None)
            else:
                next_source['cursor'] = observed.get('cursor')
            db.execute('''INSERT INTO ho_browser_sources VALUES (?,?,?,?,NULL,?)
              ON CONFLICT(campaign_id) DO UPDATE SET source=excluded.source,checked=excluded.checked,
              next_check=excluded.next_check,error=NULL,complete=excluded.complete''',
                       (campaign_id, json.dumps(next_source), self.clock(), self.clock() + 600,
                        int(observed.get('complete') is True)))
        return {'imported': imported, 'repeated': repeated, 'skipped': skipped,
                'complete': observed.get('complete') is True, 'errors': list(dict.fromkeys(errors))}

    def _source_error(self, campaign_id, code):
        with self.engine.db(write=True) as db:
            db.execute('UPDATE ho_browser_sources SET error=?,next_check=? WHERE campaign_id=?',
                       (queue_reason(code), self.clock() + 600, campaign_id))

    def _send_one(self):
        job = self.engine.claim(self.account_id, 'api-browser-worker')
        if not job:
            return False
        started = False
        def before_send(attempt_id):
            nonlocal started
            require(attempt_id == job['attemptId'], 'Попытка отправки изменилась.', 409)
            self.engine.start(job['jobId'], job['claimToken'])
            started = True
            return True
        try:
            prepared = self.bridge.prepare(job, job['message'])
            require(prepared.get('messageHash') == job['messageHash'], 'Текст сообщения изменился.', 409)
            outcome = self.bridge.send(prepared['preparedId'], job['attemptId'], before_send=before_send)
            result = self.engine.result(job['jobId'], job['claimToken'], outcome.get('outcome', 'unknown'),
                reason=queue_reason(outcome.get('reason')), evidence=outcome.get('evidence'))
            if result['state'] == 'unknown':
                self.bridge.pause('send_outcome_uncertain')
        except (BrowserError, Problem, KeyError, TypeError, ValueError) as error:
            code = error.code if isinstance(error, BrowserError) else error.message if isinstance(error, Problem) else 'unconfirmed'
            try:
                self.engine.result(job['jobId'], job['claimToken'], 'unknown' if started else 'blocked',
                                   reason=queue_reason(code))
            except Problem:
                # Expired leases recover to unknown on next claim; never resend.
                pass
            try:
                self.bridge.pause('send_outcome_uncertain' if started else 'browser_check_required')
            except BrowserError:
                pass
            self.last_error = queue_reason(code)
        return True

    def _reconcile_unknown(self):
        with self.engine.db() as db:
            jobs = [dict(row) for row in db.execute('''SELECT id,attempt_id FROM ho_outbox
              WHERE account_id=? AND state='unknown' ORDER BY updated LIMIT 3''', (self.account_id,))]
        for job in jobs:
            try:
                result = self.bridge.observe(job['attempt_id'])
                if result.get('outcome') == 'sent_observed':
                    self.engine.reconcile(job['id'], result.get('evidence'))
            except (BrowserError, Problem):
                pass

    def _poll_sources(self):
        with self.engine.db() as db:
            row = db.execute('''SELECT s.campaign_id,s.source FROM ho_browser_sources s
              JOIN ho_campaigns c ON c.id=s.campaign_id
              WHERE c.status='running' AND c.mode='applicants' AND c.account_id=? AND s.next_check<=?
                AND (SELECT COUNT(*) FROM ho_outbox o WHERE o.campaign_id=c.id)<c.send_limit
              ORDER BY s.next_check LIMIT 1''', (self.account_id, self.clock())).fetchone()
        if not row:
            return
        try:
            source = json.loads(row['source'])
            source['limit'] = self.engine.campaign(row['campaign_id'])['limit']
            self.begin_discovery(row['campaign_id'], source)
        except (Problem, ValueError):
            self.engine.control(row['campaign_id'], 'pause')

    def refresh_materials(self):
        """Bounded refresh only for campaign-attributed completed interviews."""
        with self.engine.db() as db:
            rows = [dict(row) for row in db.execute('''SELECT a.interview_id,a.recipient_id,a.campaign_id,
              a.invitation_id,i.candidate_id,i.room,m.body FROM cp_booking_attribution a
              JOIN cp_interviews i ON i.id=a.interview_id
              LEFT JOIN ho_media_checks k ON k.interview_id=i.id
              LEFT JOIN cp_media_state m ON m.meeting_id=i.id
              WHERE i.status='completed' AND COALESCE(k.next_check,0)<=?
              ORDER BY COALESCE(k.checked,0) LIMIT 3''', (self.clock(),))]
        for row in rows:
            with self.engine.db(write=True) as db:
                self.engine.event_sink(db, 'interview.completed', row['recipient_id'],
                    interview_id=row['interview_id'], event_key='completed:' + row['interview_id'])
                # Commit next poll before provider I/O; a crash cannot hot-loop.
                db.execute('INSERT OR REPLACE INTO ho_media_checks VALUES (?,?,?)',
                           (row['interview_id'], self.clock(), self.clock() + 900))
            if row['room'] and self.portal.media.configured:
                try:
                    result = self.portal.materials(row['candidate_id'], meeting_id=row['interview_id'])
                except Exception as telemetry_error:
                    observability.capture_exception(telemetry_error, "worker.hh_outreach_worker")
                    continue
            else:
                result = json.loads(row['body']) if row['body'] else {}
            with self.engine.db(write=True) as db:
                for channel, event in (('video', 'recording.ready'), ('transcription', 'transcript.ready')):
                    state = result.get(channel, {})
                    if state.get('status') == 'ready' and state.get('ids'):
                        self.engine.event_sink(db, event, row['recipient_id'], interview_id=row['interview_id'],
                                               event_key=event + ':' + row['interview_id'])
                if all(result.get(channel, {}).get('status') == 'ready' for channel in ('video', 'transcription')):
                    db.execute('UPDATE ho_media_checks SET next_check=? WHERE interview_id=?',
                               (self.clock() + 86400, row['interview_id']))

    def tick(self):
        if self.clock() - self.last_media_check >= 60:
            self.last_media_check = self.clock()
            self.refresh_materials()
        if not self.bridge or not self.operation.acquire(blocking=False):
            return
        try:
            if self.poll_discovery():
                return
            self.engine.recover_account(self.account_id)
            if not self.engine.sending_enabled:
                return
            self._reconcile_unknown()
            state = self.browser_state()
            if not state['loggedIn'] or state['paused'] or state['manual'] or state['busy'] or not state['calibrationReady']:
                return
            if not self._send_one():
                self._poll_sources()
        finally:
            self.operation.release()

    def start_worker(self):
        if not self.worker_enabled or self.thread:
            return
        def run():
            while not self.stop.wait(5):
                try:
                    self.tick()
                except Exception as telemetry_error:
                    observability.capture_exception(telemetry_error, "worker.hh_outreach_worker")
                    # Error details may carry private message text. Never log them.
                    self.last_error = 'worker_check_required'
        self.thread = threading.Thread(target=run, name='hh-browser-outreach', daemon=True)
        self.thread.start()
