"""Authenticated, optional HH capability of the existing recruiter workspace.

Mount before CandidatePortal's broad recruiter prefix. Construction never makes
network calls. start_worker is opt-in and runs independently of notifications.
"""
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit

import candidate_invites as invites
from candidate_portal import email_value, profile_value
from domain import Problem
from hh_recruiting import HHConfig, HHError, HHRecruiting, TransportUnavailable, identifier, require, short


PREFIX = '/api/hr/recruiter/hh/'
EVENTS = {'interview_booked': 'call', 'interview_completed': 'claim'}
ERRORS = {
    'hh_disabled': 'Подключение HH пока выключено на сервере.',
    'hh_not_configured': 'Для подключения нужно настроить приложение HH на сервере.',
    'sending_disabled': 'Автоматическая отправка пока выключена на сервере.',
    'oauth_invalid_state': 'Подключение истекло. Начните подключение HH ещё раз.',
    'oauth_exchange_failed': 'HH не подтвердил подключение. Повторите авторизацию.',
    'employer_authorization_required': 'Подключите аккаунт работодателя HH.',
    'reauth_required': 'HH отозвал доступ. Подключите аккаунт заново.',
    'connection_not_found': 'Подключение не найдено.',
    'linked_vacancy_required': 'Сначала свяжите вакансию HH с вакансией HR Vision.',
    'vacancy_not_found': 'Вакансия не найдена. Обновите список.',
    'local_vacancy_required': 'Выберите существующую вакансию HR Vision.',
    'collections_required': 'Выберите группы откликов для импорта.',
    'collection_unavailable': 'HH изменил группы откликов. Обновите список.',
    'event_required': 'Выберите событие для автоматической ссылки.',
    'invalid_template': 'Добавьте текст сообщения и место для ссылки: {invite_url}.',
    'provider_access_denied': 'HH не предоставил доступ к этому действию.',
    'access_required': 'Для действия нужен дополнительный доступ в HH.',
    'rule_not_found': 'Правило не найдено.',
}


def config_from_env(origin):
    return HHConfig(
        enabled=os.environ.get('HR_HH_ENABLED') == '1',
        sending_enabled=os.environ.get('HR_HH_SENDING_ENABLED') == '1',
        client_id=os.environ.get('HR_HH_CLIENT_ID', ''),
        client_secret=os.environ.get('HR_HH_CLIENT_SECRET', ''),
        redirect_uri=os.environ.get('HR_HH_REDIRECT_URI', ''),
        user_agent=os.environ.get('HR_HH_USER_AGENT', ''),
        invite_origin=origin.rstrip('/'),
    )


def event_time(value):
    try:
        return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (AttributeError, ValueError, TypeError):
        return 0


class HHRecruiterRoutes:
    def __init__(self, connect, portal, config=None, *, transport=None, clock=time.time):
        self.portal, self.clock = portal, clock
        self.config = config or config_from_env(portal.origin)
        self.hh = HHRecruiting(connect, self.config, transport=transport,
                               prepare_candidate=self.prepare_candidate, clock=clock)
        self._worker = None
        self._worker_lock = threading.Lock()
        self._stop = threading.Event()
        self.last_worker_error = None
        with self.hh.db() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS hh_rule_control (
                rule_id TEXT PRIMARY KEY REFERENCES hh_send_rules(id), enabled_since REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS hh_dispatch_events (
                connection_id TEXT NOT NULL, negotiation_id TEXT NOT NULL, event_key TEXT NOT NULL,
                candidate_id TEXT NOT NULL, rule_id TEXT NOT NULL, outbox_id TEXT,
                outcome TEXT NOT NULL, created REAL NOT NULL,
                PRIMARY KEY(connection_id,negotiation_id,event_key));
            ''')

    @staticmethod
    def handles(path):
        return urlsplit(path).path.startswith(PREFIX)

    def prepare_candidate(self, db, external_key, profile, source):
        profile = profile_value(profile)
        contact = ''
        if source.get('contactEmail'):
            try:
                contact = email_value(source['contactEmail'])
            except Problem:
                pass
        cv = None
        # A restricted/partial result is not a bookable full CV. No fake PDF.
        if source.get('cvAvailable') and source.get('cvText'):
            text = 'Резюме из HH · ' + source['resumeId'] + '\n\n' + source['cvText']
            cv = {'filename': 'HH-' + identifier(source['resumeId']) + '.txt', 'mime': 'text/plain',
                  'content': text.encode('utf-8'), 'text': text, 'fields': profile}
        cid = invites.create_prepared(db, profile, source='hh', external_key=external_key,
                                      contact_email=contact, cv=cv)
        if cv:
            # A later full import may fill a missing CV. Never overwrite an edited
            # profile or an uploaded CV from an earlier source.
            db.execute('''INSERT OR IGNORE INTO cp_resumes VALUES (?,?,?,?,?,?,?)''',
                       (cid, cv['filename'], cv['mime'], cv['content'], cv['text'],
                        json.dumps(cv['fields'], ensure_ascii=False),
                        dt.datetime.fromtimestamp(self.clock(), dt.timezone.utc).isoformat()))
        return cid

    def authorize(self, manager_actor):
        with self.hh.db() as db:
            actor = manager_actor(db)
        if actor != 'manager':
            raise Problem('Доступ только для команды HR Vision.', 403)
        return actor

    def handle(self, method, raw_path, headers, body, manager_actor):
        # actor is resolved from the server-validated owner Bearer, never JSON/query.
        actor = self.authorize(manager_actor)
        parsed = urlsplit(raw_path)
        action = parsed.path[len(PREFIX):].strip('/')
        query = parse_qs(parsed.query)
        data = body if isinstance(body, dict) else {}
        try:
            if method == 'GET' and action == 'state':
                result = self.state(actor, query.get('connectionId', [''])[0], query.get('vacancyId', [''])[0])
            elif method == 'POST' and action == 'oauth/start':
                result = {'url': self.hh.begin_oauth(actor)}
            elif method == 'POST' and action == 'oauth/complete':
                connection = self.hh.finish_oauth(actor, data.get('state'), data.get('code'))
                result = {'connection': connection}
            elif method == 'POST' and action == 'vacancies/sync':
                result = {'vacancies': self.hh.sync_vacancies(actor, identifier(data.get('connectionId')))}
            elif method == 'GET' and action == 'collections':
                result = {'collections': self.collections(actor, identifier(query.get('connectionId', [''])[0]),
                                                         identifier(query.get('vacancyId', [''])[0]))}
            elif method == 'POST' and action == 'vacancies/link':
                local = identifier(data.get('localId'))
                with self.hh.db() as db:
                    require(db.execute('SELECT id FROM cp_vacancies WHERE id=?', (local,)).fetchone(),
                            'local_vacancy_required', 404)
                self.hh.link_vacancy(actor, identifier(data.get('connectionId')), identifier(data.get('vacancyId')), local)
                result = {'linked': True}
            elif method == 'POST' and action == 'responses/import':
                result = self.hh.sync_vacancy(actor, identifier(data.get('connectionId')),
                    identifier(data.get('vacancyId')), collection_ids=data.get('collectionIds'))
            elif method == 'POST' and action == 'rules':
                result = self.save_rule(actor, data)
            elif method == 'POST' and action == 'rules/disable':
                with self.hh.db(write=True) as db:
                    rule = db.execute('SELECT * FROM hh_send_rules WHERE id=?', (identifier(data.get('ruleId')),)).fetchone()
                    require(rule, 'rule_not_found', 404)
                    self.hh.owned(db, actor, rule['connection_id'])
                    self.disable_rule(db, rule['id'])
                result = {'disabled': True}
            else:
                raise Problem('Маршрут HH не найден.', 404)
            return result, {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}
        except HHError as error:
            raise Problem(ERRORS.get(error.code, 'HH не выполнил действие. Повторите позже или проверьте доступ.'), error.status) from None
        except TransportUnavailable:
            raise Problem('HH сейчас не отвечает. Повторите позже.', 503) from None

    def state(self, actor, connection_id='', vacancy_id=''):
        try:
            self.hh.configured()
            configured = True
        except HHError:
            configured = False
        with self.hh.db() as db:
            connections = [dict(row) for row in db.execute('''SELECT id,employer_id,manager_id,status,updated
              FROM hh_connections WHERE actor_id=? ORDER BY updated DESC''', (actor,))]
            if connection_id:
                self.hh.owned(db, actor, connection_id)
            rules, deliveries, skipped = [], [], []
            if connection_id and vacancy_id:
                rules = [dict(row) for row in db.execute('''SELECT r.id,r.event_type,r.template,r.enabled,c.enabled_since
                  FROM hh_send_rules r JOIN hh_rule_control c ON c.rule_id=r.id
                  WHERE r.connection_id=? AND r.vacancy_id=? ORDER BY c.enabled_since DESC,r.rowid DESC LIMIT 20''',
                  (connection_id, vacancy_id))]
                for rule in rules:
                    rule['enabled'] = bool(rule['enabled'])
                deliveries = [dict(row) for row in db.execute('''SELECT o.id,o.candidate_id,o.status,o.attempts,
                  o.next_attempt,o.provider_message_id,o.last_error FROM hh_outbox o
                  JOIN hh_negotiations n ON n.connection_id=o.connection_id AND n.external_id=o.negotiation_id
                  WHERE o.connection_id=? AND n.vacancy_id=? ORDER BY o.created DESC LIMIT 50''',
                  (connection_id, vacancy_id))]
                skipped = [dict(row) for row in db.execute('''SELECT e.candidate_id,e.outcome FROM hh_dispatch_events e
                  JOIN hh_negotiations n ON n.connection_id=e.connection_id AND n.external_id=e.negotiation_id
                  WHERE e.connection_id=? AND n.vacancy_id=? AND e.outbox_id IS NULL ORDER BY e.created DESC LIMIT 20''',
                  (connection_id, vacancy_id))]
        return {'configured': configured, 'sendingEnabled': configured and self.config.sending_enabled,
                'connections': connections, 'vacancies': self.hh.vacancies(actor, connection_id) if connection_id else [],
                'negotiations': self.hh.negotiations(actor, connection_id, vacancy_id) if connection_id and vacancy_id else [],
                'rules': rules, 'deliveries': deliveries, 'skipped': skipped}

    def collections(self, actor, connection_id, vacancy_id):
        with self.hh.db() as db:
            self.hh.owned(db, actor, connection_id)
            require(db.execute('SELECT 1 FROM hh_vacancies WHERE connection_id=? AND external_id=? AND active=1',
                               (connection_id, vacancy_id)).fetchone(), 'vacancy_not_found', 404)
        catalog = self.hh.get(actor, connection_id, '/negotiations?vacancy_id=' + vacancy_id)
        result = []
        def visit(items, depth=0):
            require(isinstance(items, list) and depth < 5 and len(result) + len(items) <= 200,
                    'invalid_collections', 502)
            for item in items:
                require(isinstance(item, dict), 'invalid_collections', 502)
                if item.get('url'):
                    count = item.get('counters') or {}
                    result.append({'id': identifier(item.get('id')), 'name': short(item.get('name')),
                                   'count': count.get('total') if isinstance(count, dict) and isinstance(count.get('total'), int) else None})
                if item.get('sub_collections'):
                    visit(item['sub_collections'], depth + 1)
        visit(catalog.get('collections'))
        return result

    @staticmethod
    def disable_rule(db, rule_id):
        db.execute('UPDATE hh_send_rules SET enabled=0 WHERE id=?', (rule_id,))
        db.execute("""UPDATE hh_outbox SET status='cancelled',last_error='sending_rule_disabled'
          WHERE rule_id=? AND status IN ('queued','retry','unknown')""", (rule_id,))

    def save_rule(self, actor, data):
        connection_id, vacancy_id = identifier(data.get('connectionId')), identifier(data.get('vacancyId'))
        event = data.get('event')
        require(event in EVENTS, 'event_required')
        template, enabled = data.get('template'), data.get('enabled', False)
        require(isinstance(template, str) and '{invite_url}' in template and 1 <= len(template.strip()) <= 16000,
                'invalid_template')
        require(isinstance(enabled, bool), 'invalid_enabled')
        if enabled:
            self.hh.configured()
            require(self.config.sending_enabled, 'sending_disabled', 503)
        with self.hh.db(write=True) as db:
            self.hh.owned(db, actor, connection_id)
            require(db.execute('''SELECT 1 FROM hh_vacancies v JOIN cp_vacancies c ON c.id=v.local_id
              WHERE v.connection_id=? AND v.external_id=? AND v.active=1''', (connection_id, vacancy_id)).fetchone(),
              'linked_vacancy_required')
            # Only one current template/event per chosen vacancy; no repeated sends
            # after template edits. A new rule applies only to future domain events.
            for row in db.execute('SELECT id FROM hh_send_rules WHERE connection_id=? AND vacancy_id=? AND event_type=?',
                                  (connection_id, vacancy_id, event)).fetchall():
                self.disable_rule(db, row[0])
            rule_id, version = 'hhr_' + secrets.token_hex(12), 'template_' + secrets.token_hex(12)
            db.execute('INSERT INTO hh_send_rules VALUES (?,?,?,?,?,?,?)',
                       (rule_id, connection_id, vacancy_id, event, version, template.strip(), int(enabled)))
            db.execute('INSERT INTO hh_rule_control VALUES (?,?)', (rule_id, self.clock()))
        return {'ruleId': rule_id, 'enabled': enabled}

    def invitation_current(self, db, invitation_id, candidate_id):
        row = db.execute('''SELECT t.revoked_at,t.consumed_at,t.expires,t.scope,i.status,p.email FROM cp_invitations t
          JOIN cp_interviews i ON i.id=t.interview_id AND i.candidate_id=t.candidate_id
          JOIN cp_candidates p ON p.id=t.candidate_id
          WHERE t.id=? AND t.candidate_id=?''', (invitation_id, candidate_id)).fetchone()
        if not row:
            return False
        revoked, consumed, expires, scope, status, email = tuple(row)
        if revoked or consumed or expires <= self.clock():
            return False
        return (scope == 'call' and status in ('booked', 'live') or
                scope == 'claim' and status == 'completed' and email is None)

    def existing_invitation(self, db, candidate_id, scope):
        """Domain callback for ordinary copy/open, never silently revoking a sent URL.

        Caller already authorizes a recruiter. Only the exact still-valid domain
        capability can be returned; its fragment must match the stored token hash.
        Explicit replacement bypasses this callback in CandidatePortal.
        """
        if scope not in ('call', 'claim'):
            return None
        cursor = db.execute('''SELECT o.invitation_id,o.invitation_url,t.token_hash,t.expires
          FROM hh_outbox o JOIN cp_invitations t ON t.id=o.invitation_id AND t.candidate_id=o.candidate_id
          WHERE o.candidate_id=? AND t.scope=? AND t.revoked_at IS NULL AND t.consumed_at IS NULL
          AND t.expires>? ORDER BY o.created DESC LIMIT 5''', (candidate_id, scope, self.clock()))
        for raw in cursor.fetchall():
            iid, url, digest, expires = tuple(raw)
            if not self.invitation_current(db, iid, candidate_id):
                continue
            parsed = urlsplit(url)
            fragment = parse_qs(parsed.fragment)
            token = fragment.get('invite' if scope == 'call' else 'claim', [''])[0]
            if (parsed.scheme != 'https' or parsed.netloc != urlsplit(self.portal.origin).netloc or
                    parsed.username or parsed.password or not token or
                    not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), digest)):
                continue
            return {'invitationId': iid, 'url': url,
                    'expiresAt': dt.datetime.fromtimestamp(expires, dt.timezone.utc).isoformat()}
        return None

    def enqueue_events(self, limit=50):
        """Local domain scan, no provider requests; invitation/outbox/ledger commit together."""
        with self.hh.db(write=True) as db:
            rows = db.execute('''SELECT q.id AS rule_id,q.event_type,q.connection_id,n.external_id AS negotiation_id,
              q.vacancy_id,c.actor_id,r.candidate_id,i.id AS interview_id,i.status,i.created_at,i.completed_at,
              k.enabled_since FROM hh_send_rules q JOIN hh_rule_control k ON k.rule_id=q.id
              JOIN hh_connections c ON c.id=q.connection_id
              JOIN hh_vacancies v ON v.connection_id=q.connection_id AND v.external_id=q.vacancy_id
              JOIN cp_vacancies lv ON lv.id=v.local_id
              JOIN hh_negotiations n ON n.connection_id=q.connection_id AND n.vacancy_id=q.vacancy_id
              JOIN hh_resumes r ON r.connection_id=n.connection_id AND r.external_id=n.resume_id
              JOIN cp_intake intake ON intake.candidate_id=r.candidate_id AND intake.source='hh'
              JOIN cp_interviews i ON i.candidate_id=r.candidate_id
              WHERE q.enabled=1 AND v.active=1 AND c.status='connected'
              ORDER BY i.created_at,n.external_id''').fetchall()
            candidates = []
            for row in rows:
                if row['event_type'] not in EVENTS:
                    continue
                if row['event_type'] == 'interview_booked':
                    valid, occurred = row['status'] in ('booked', 'live'), event_time(row['created_at'])
                else:
                    valid, occurred = row['status'] == 'completed', event_time(row['completed_at'])
                if valid and occurred >= row['enabled_since']:
                    candidates.append(dict(row, event_key=row['event_type'] + '_' + row['interview_id']))
            counts = {}
            for item in candidates:
                key = (item['candidate_id'], item['event_key'])
                counts[key] = counts.get(key, 0) + 1
            processed = 0
            for item in candidates:
                key = (item['connection_id'], item['negotiation_id'], item['event_key'])
                if db.execute('SELECT 1 FROM hh_dispatch_events WHERE connection_id=? AND negotiation_id=? AND event_key=?', key).fetchone():
                    continue
                if processed >= limit:
                    break
                processed += 1
                scope, cid = EVENTS[item['event_type']], item['candidate_id']
                outcome, job = 'queued', None
                if counts[(cid, item['event_key'])] != 1:
                    outcome = 'ambiguous_audience'
                elif db.execute('''SELECT 1 FROM cp_invitations WHERE candidate_id=? AND scope=?
                  AND revoked_at IS NULL AND consumed_at IS NULL AND expires>?''', (cid, scope, self.clock())).fetchone():
                    outcome = 'existing_invitation'
                elif scope == 'claim' and db.execute('SELECT email FROM cp_candidates WHERE id=?', (cid,)).fetchone()[0]:
                    outcome = 'account_active'
                else:
                    # A bad/expired single event must not stop unrelated candidates.
                    db.execute('SAVEPOINT hh_invitation')
                    try:
                        invitation = invites.issue(db, cid, scope, self.portal.origin)
                        job = self.hh.enqueue_invitation(item['actor_id'], item['connection_id'], item['negotiation_id'],
                          rule_id=item['rule_id'], event_key=item['event_key'], invitation_id=invitation['invitationId'],
                          url=invitation['url'], db=db)['id']
                    except (Problem, HHError):
                        db.execute('ROLLBACK TO hh_invitation')
                        outcome = 'invitation_unavailable'
                    finally:
                        db.execute('RELEASE hh_invitation')
                db.execute('INSERT INTO hh_dispatch_events VALUES (?,?,?,?,?,?,?,?)',
                           (*key, cid, item['rule_id'], job, outcome, self.clock()))
        return processed

    def process_pending(self, limit=10):
        # No enabled config -> no local invites minted and no provider calls.
        try:
            self.hh.configured()
        except HHError:
            return {'enabled': False, 'processed': 0}
        if not self.config.sending_enabled:
            return {'enabled': False, 'processed': 0}
        limit = max(1, min(int(limit), 20))
        self.enqueue_events(limit=limit * 2)
        with self.hh.db() as db:
            jobs = db.execute('''SELECT o.id,c.actor_id FROM hh_outbox o
              JOIN hh_connections c ON c.id=o.connection_id JOIN hh_send_rules r ON r.id=o.rule_id
              WHERE r.enabled=1 AND c.status='connected' AND o.next_attempt<=? AND
              (o.status IN ('queued','retry','unknown') OR (o.status='sending' AND o.lease_until<=?))
              ORDER BY o.created LIMIT ?''', (self.clock(), self.clock(), limit)).fetchall()
        for job in jobs:
            try:
                self.hh.send_once(job['actor_id'], job['id'], is_current=self.invitation_current)
            except HHError as error:
                self.last_worker_error = error.code
        return {'enabled': True, 'processed': len(jobs)}

    def start_worker(self, interval=30):
        try:
            self.hh.configured()
        except HHError:
            return None
        if not self.config.sending_enabled:
            return None
        with self._worker_lock:
            if self._worker and self._worker.is_alive():
                return self._worker
            self._stop.clear()
            def run():
                while not self._stop.is_set():
                    try:
                        self.process_pending()
                        self.last_worker_error = None
                    except Exception:
                        # No exception body, token, URL or personal data in logs.
                        self.last_worker_error = 'worker_failed'
                    self._stop.wait(max(10, interval))
            self._worker = threading.Thread(target=run, name='hh-invitations', daemon=True)
            self._worker.start()
            return self._worker

    def stop_worker(self):
        self._stop.set()
