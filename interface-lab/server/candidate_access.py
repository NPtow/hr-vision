"""Explicit recruiter-issued candidate access, using the existing canonical profile.

Only a button on the candidate's own recruiter's interview may enqueue this mail.
Booking consent fixes the destination for a prepared HH profile; an existing
account receives a sign-in reminder and its password is never reset. Nothing in
init, state or enqueue sends mail. The worker uses the same TLS SMTP settings and
transport as candidate_notifications, but its own explicit-purpose outbox: an
access email is not an assertion that the recipient's email is verified.

No plaintext password or email body is stored. A private random signing key and
an opaque per-issuance identifier reproduce one high-entropy password only in
memory for hashing/delivery. Repeated clicks and known-failure retries preserve
the same credentials. Before SMTP we durably mark the operation as sending; a
crash, timeout or ambiguous provider outcome becomes unknown, never auto-retry.
"""
import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
import urllib.parse

import candidate_notifications as delivery
from candidate_portal import password_hash, password_matches
from domain import Problem, iso, now, require


LEASE_SECONDS = 180
REQUEST_RE = re.compile(r'[A-Za-z0-9_-]{8,128}')
# The shared transport maps SMTPResponseException to smtp_rejected without its
# protocol phase. QUIT can fail after DATA was accepted, so treat that generic
# code as uncertain instead of allowing an unsafe resend of account credentials.
KNOWN_FAILURES = frozenset(('smtp_authentication', 'smtp_recipient_refused', 'smtp_tls_required'))
ERROR_MESSAGES = {
    'email_not_configured': 'Отправка писем пока не настроена.',
    'booking_consent_required': 'Кандидат ещё не подтвердил создание профиля при бронировании.',
    'resume_required': 'Сначала загрузите резюме кандидата.',
    'email_conflict': 'Эта почта уже используется другим аккаунтом. Доступ не отправлен.',
    'email_invalid': 'Проверьте почту кандидата.',
    'credentials_changed': 'Данные входа изменились. Проверьте аккаунт кандидата.',
    'interview_unavailable': 'Интервью недоступно.',
    'unknown_delivery': 'Результат отправки неизвестен. Проверьте письмо перед повторной отправкой.',
    'already_sent': 'Письмо уже принято почтовым сервером.',
    'delivery_in_progress': 'Письмо уже отправляется.',
}


def init(db):
    """Preserve the caller's transaction, including schema initialization."""
    for statement in (
        '''CREATE TABLE IF NOT EXISTS cp_access_config (
            name TEXT PRIMARY KEY, value TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS cp_access_issuances (
            id TEXT PRIMARY KEY, candidate_id TEXT UNIQUE NOT NULL REFERENCES cp_candidates(id),
            email TEXT NOT NULL, created_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS cp_access_outbox (
            id TEXT PRIMARY KEY, candidate_id TEXT UNIQUE NOT NULL REFERENCES cp_candidates(id),
            interview_id TEXT NOT NULL REFERENCES cp_interviews(id),
            recruiter_id TEXT NOT NULL REFERENCES rc_accounts(id),
            recipient TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('credentials','signin')),
            issuance_id TEXT REFERENCES cp_access_issuances(id), request_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('queued','sending','sent','failed','unknown')),
            attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            lease_id TEXT, lease_until INTEGER, sent_at TEXT, last_error TEXT, provider_ref TEXT)''',
        '''CREATE TABLE IF NOT EXISTS cp_access_requests (
            recruiter_id TEXT NOT NULL REFERENCES rc_accounts(id), request_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL REFERENCES cp_candidates(id),
            interview_id TEXT NOT NULL REFERENCES cp_interviews(id),
            delivery_id TEXT NOT NULL REFERENCES cp_access_outbox(id), created_at TEXT NOT NULL,
            PRIMARY KEY(recruiter_id,request_id))''',
        'CREATE INDEX IF NOT EXISTS cp_access_ready ON cp_access_outbox(status,created_at)',
    ):
        db.execute(statement)
    db.execute("INSERT OR IGNORE INTO cp_access_config VALUES ('password_key',?)",
               (secrets.token_hex(32),))


def _row(cursor):
    result = cursor.fetchone()
    return dict(zip([column[0] for column in cursor.description], result)) if result else None


def _password(db, issuance_id, cid):
    key = db.execute("SELECT value FROM cp_access_config WHERE name='password_key'").fetchone()
    require(key and re.fullmatch(r'[0-9a-f]{64}', key[0]), 'Выдача доступа временно недоступна.', 503)
    data = ('candidate-access-password-v1\0' + issuance_id + '\0' + cid).encode()
    value = hmac.new(bytes.fromhex(key[0]), data, hashlib.sha256).digest()[:24]
    return 'Hrv-' + base64.urlsafe_b64encode(value).decode().rstrip('=') + '!'


def _portal_url(config):
    require(delivery._url(config.public_url), 'Адрес кабинета не настроен.', 503)
    parsed = urllib.parse.urlsplit(config.public_url)
    # Never include campaign capabilities, credentials or contact data in mail URLs.
    allowed = {key: value for key, value in urllib.parse.parse_qsl(parsed.query)
               if key in ('id', 'viewMode')}
    allowed['cp'] = 'login'
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path,
                                   urllib.parse.urlencode(allowed), ''))


class CandidateAccess:
    def __init__(self, portal, config=None, transport=None):
        self.portal = portal
        self.connect, self.lock = portal.connect, portal.lock
        self._config = config
        self._transport = transport
        self._worker_guard = threading.Lock()
        self._worker = None
        with self.connect() as db:
            init(db)

    def config(self):
        return self._config if self._config is not None else delivery.settings()

    @staticmethod
    def _owned_interview(db, cid, principal):
        require(isinstance(principal, dict) and not principal.get('legacy')
                and principal.get('id'), 'Войдите в свой кабинет рекрутера.', 401)
        row = _row(db.execute('''SELECT i.id,i.status FROM cp_interviews i
            JOIN cp_recruiter_slot_owners o ON o.slot_id=i.slot_id
            JOIN rc_accounts r ON r.id=o.recruiter_id AND r.active=1
            WHERE i.candidate_id=? AND o.recruiter_id=?''', (cid, principal['id'])))
        require(row, 'Интервью не найдено.', 404)
        return row

    @staticmethod
    def _consent(db, cid, iid):
        # Imported lazily: candidate_booking depends on CandidatePortal and has
        # its own optional schema. Old manual invitations remain unchanged.
        from candidate_booking import profile_consent
        return profile_consent(db, cid, iid)

    def _eligibility(self, db, cid, interview):
        person = _row(db.execute('SELECT email,password_hash FROM cp_candidates WHERE id=?', (cid,)))
        require(person, 'Кандидат не найден.', 404)
        if interview['status'] not in ('booked', 'live', 'completed'):
            return person, None, None, 'interview_unavailable'
        if person['email']:
            mode, target = 'signin', person['email']
        else:
            consent = self._consent(db, cid, interview['id'])
            intake = db.execute('SELECT source FROM cp_intake WHERE candidate_id=?', (cid,)).fetchone()
            if not consent or not intake or intake[0] not in ('hh', 'manual'):
                return person, 'credentials', None, 'booking_consent_required'
            if not db.execute('SELECT 1 FROM cp_resumes WHERE candidate_id=? AND length(content)>0', (cid,)).fetchone():
                return person, 'credentials', None, 'resume_required'
            mode, target = 'credentials', consent['email']
        try:
            target = delivery._email(target)
        except Problem:
            return person, mode, None, 'email_invalid'
        if db.execute('SELECT 1 FROM cp_candidates WHERE lower(email)=? AND id<>?', (target, cid)).fetchone():
            return person, mode, target, 'email_conflict'
        return person, mode, target, None

    def state(self, db, cid, *, principal):
        interview = self._owned_interview(db, cid, principal)
        _, mode, target, reason = self._eligibility(db, cid, interview)
        row = _row(db.execute('SELECT * FROM cp_access_outbox WHERE candidate_id=?', (cid,)))
        status = row['status'] if row else 'not_requested'
        if not reason and not self.config().email_configured:
            reason = 'email_not_configured'
        if row:
            mode, target = row['mode'], row['recipient']
            if status in ('queued', 'sending'):
                reason = 'delivery_in_progress'
            elif status == 'sent':
                reason = 'already_sent'
            elif status == 'unknown':
                reason = 'unknown_delivery'
            elif row['last_error'] == 'credentials_changed' or not self._credentials_current(db, row):
                reason = 'credentials_changed'
        return {'status': status, 'mode': mode, 'recipientEmail': target,
                'emailConfigured': bool(self.config().email_configured),
                'canSend': reason is None and status in ('not_requested', 'failed'),
                'blockedReason': reason, 'consentRequired': False,
                'deliveryId': row['id'] if row else None,
                'requestId': row['request_id'] if row else None,
                'interviewId': interview['id'],
                'lastError': row['last_error'] if row else None,
                'sentAt': row['sent_at'] if row else None,
                'deliveryMeaning': 'smtp_acceptance'}

    def request(self, db, principal, cid, body):
        require(isinstance(body, dict) and set(body) <= {'candidateId', 'requestId'},
                'Проверьте поля отправки доступа.', 400)
        require(body.get('candidateId', cid) == cid, 'Кандидат не совпадает.', 400)
        interview = self._owned_interview(db, cid, principal)
        return self.enqueue(db, cid, interview['id'], body.get('requestId'), principal=principal)

    def enqueue(self, db, cid, iid, request_id, *, principal):
        """Call in the HTTP transaction; no provider I/O and no implicit commit."""
        require(db.in_transaction, 'Выдача доступа требует транзакции.', 500)
        require(isinstance(request_id, str) and REQUEST_RE.fullmatch(request_id),
                'Нужен идентификатор отправки.', 400)
        interview = self._owned_interview(db, cid, principal)
        require(interview['id'] == iid, 'Интервью не найдено.', 404)
        previous = _row(db.execute('''SELECT candidate_id,interview_id FROM cp_access_requests
            WHERE recruiter_id=? AND request_id=?''', (principal['id'], request_id)))
        if previous:
            require(previous['candidate_id'] == cid and previous['interview_id'] == iid,
                    'Этот запрос уже использован для другого интервью.', 409)
            return self.state(db, cid, principal=principal)

        existing = _row(db.execute('SELECT * FROM cp_access_outbox WHERE candidate_id=?', (cid,)))
        if existing and existing['status'] in ('queued', 'sending', 'sent', 'unknown'):
            # A fresh browser/request ID is not consent to resend uncertain mail.
            db.execute('INSERT INTO cp_access_requests VALUES (?,?,?,?,?,?)',
                       (principal['id'], request_id, cid, iid, existing['id'], iso(now())))
            return self.state(db, cid, principal=principal)
        current = self.state(db, cid, principal=principal)
        reason = current['blockedReason']
        require(current['canSend'], ERROR_MESSAGES.get(reason, 'Доступ пока нельзя отправить.'),
                503 if reason == 'email_not_configured' else 409)
        person, mode, target, _ = self._eligibility(db, cid, interview)
        created = iso(now())
        if existing:
            # Retry only a known failure, at an explicit new button request.
            require(self._credentials_current(db, existing), ERROR_MESSAGES['credentials_changed'], 409)
            outbox_id = existing['id']
            db.execute('''UPDATE cp_access_outbox SET status='queued',request_id=?,recruiter_id=?,
                updated_at=?,lease_id=NULL,lease_until=NULL,last_error=NULL WHERE id=?''',
                       (request_id, principal['id'], created, outbox_id))
        else:
            issuance_id = None
            if mode == 'credentials':
                issuance_id = 'access_' + secrets.token_hex(16)
                password = _password(db, issuance_id, cid)
                hashed = password_hash(password)
                db.execute('INSERT INTO cp_access_issuances VALUES (?,?,?,?)',
                           (issuance_id, cid, target, created))
                changed = db.execute('''UPDATE cp_candidates SET email=?,password_hash=?,updated_at=?
                    WHERE id=? AND email IS NULL AND password_hash IS NULL''', (target, hashed, created, cid))
                require(changed.rowcount == 1, 'Аккаунт кандидата уже изменился.', 409)
                if self.portal.notifications:
                    self.portal.notifications.set_email(db, cid, target, verified=False)
                # Claim links may not replace the freshly issued credential. Call
                # links, profile, CV, consent settings and all foreign keys survive.
                db.execute("UPDATE cp_invitations SET revoked_at=? WHERE candidate_id=? AND scope='claim' AND revoked_at IS NULL",
                           (created, cid))
            outbox_id = 'accessmail_' + secrets.token_hex(16)
            db.execute('''INSERT INTO cp_access_outbox
                (id,candidate_id,interview_id,recruiter_id,recipient,mode,issuance_id,
                 request_id,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,'queued',?,?)''',
                       (outbox_id, cid, iid, principal['id'], target, mode, issuance_id,
                        request_id, created, created))
        db.execute('INSERT INTO cp_access_requests VALUES (?,?,?,?,?,?)',
                   (principal['id'], request_id, cid, iid, outbox_id, created))
        return self.state(db, cid, principal=principal)

    @staticmethod
    def _credentials_current(db, row):
        person = db.execute('SELECT email,password_hash FROM cp_candidates WHERE id=?',
                            (row['candidate_id'],)).fetchone()
        if not person or person[0] != row['recipient']:
            return False
        if row['mode'] == 'credentials':
            return bool(row['issuance_id'] and password_matches(
                _password(db, row['issuance_id'], row['candidate_id']), person[1]))
        return bool(person[1])

    def recover(self, db, timestamp=None):
        """Quarantine expired sends. Do not assume process failure meant no mail."""
        timestamp = int(time.time()) if timestamp is None else int(timestamp)
        return db.execute('''UPDATE cp_access_outbox SET status='unknown',last_error='delivery_interrupted',
            updated_at=?,lease_id=NULL,lease_until=NULL WHERE status='sending' AND lease_until<=?''',
                          (iso(now()), timestamp)).rowcount

    def dispatch_once(self):
        """Send at most one explicitly queued email, outside every SQLite lock."""
        config = self.config()
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.recover(db)
            if not config.email_configured:
                return None
            row = _row(db.execute("SELECT * FROM cp_access_outbox WHERE status='queued' ORDER BY created_at,id LIMIT 1"))
            if not row:
                return None
            if not self._credentials_current(db, row):
                db.execute("UPDATE cp_access_outbox SET status='failed',last_error='credentials_changed',updated_at=? WHERE id=?",
                           (iso(now()), row['id']))
                return {'status': 'failed', 'deliveryId': row['id']}
            lease = secrets.token_hex(16)
            db.execute('''UPDATE cp_access_outbox SET status='sending',attempts=attempts+1,
                lease_id=?,lease_until=?,updated_at=? WHERE id=? AND status='queued' ''',
                       (lease, int(time.time()) + LEASE_SECONDS, iso(now()), row['id']))
            password = _password(db, row['issuance_id'], row['candidate_id']) if row['mode'] == 'credentials' else None
        # The committed sending marker is the boundary before a provider side
        # effect. An abrupt exit after this point must not trigger another send.
        content = ('Ваш кабинет HR Vision:\n' + _portal_url(config) + '\n\nЛогин: ' + row['recipient'])
        if password:
            content += '\nПароль: ' + password + '\n\nВаше резюме и запись на интервью уже в кабинете.'
        else:
            content += '\n\nИспользуйте свой текущий пароль. Данные входа не изменились.'
        content += '\n\nНе пересылайте это письмо другим людям.'
        outcome, error = 'sent', None
        try:
            provider = (self._transport or delivery.Transport(config, email_role='candidate')).send_email(
                row['recipient'], 'Доступ к кабинету HR Vision', content, row['id'])
            if provider != 'smtp_accepted':
                outcome, error = 'unknown', 'smtp_delivery_uncertain'
        except delivery.DeliveryFailure as failure:
            # Never persist arbitrary exception codes or provider exception text.
            if failure.code in KNOWN_FAILURES:
                outcome, error = 'failed', failure.code
            else:
                outcome, error = 'unknown', 'smtp_delivery_uncertain'
        except Exception:
            outcome, error = 'unknown', 'smtp_delivery_uncertain'
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''UPDATE cp_access_outbox SET status=?,last_error=?,updated_at=?,
                sent_at=?,provider_ref=?,lease_id=NULL,lease_until=NULL
                WHERE id=? AND status='sending' AND lease_id=?''',
                       (outcome, error, iso(now()), iso(now()) if outcome == 'sent' else None,
                        'smtp_accepted' if outcome == 'sent' else None, row['id'], lease))
            actual = db.execute('SELECT status FROM cp_access_outbox WHERE id=?', (row['id'],)).fetchone()
        return {'status': actual[0] if actual else 'unknown', 'deliveryId': row['id']}

    def start_worker(self, interval=5, stop_event=None):
        """Start only from app main. Configuration never creates access requests."""
        if not self.config().enabled:
            return None
        with self._worker_guard:
            if self._worker is not None and self._worker.is_alive():
                return self._worker
            stop = stop_event if stop_event is not None else threading.Event()
            delay = max(1, min(60, float(interval)))

            def run():
                while not stop.is_set():
                    if not self.config().enabled:
                        return
                    try:
                        self.dispatch_once()
                    except Exception:
                        # Exceptions can contain addresses or credentials. HTTP
                        # status and durable outbox expose outcomes without logs.
                        pass
                    if stop.wait(delay):
                        return

            self._worker = threading.Thread(target=run, name='HRCandidateAccess', daemon=True)
            self._worker.start()
            return self._worker
