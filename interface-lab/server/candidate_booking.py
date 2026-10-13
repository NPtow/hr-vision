"""Personal HH booking capabilities, separate from account and call access.

Integration: construct CandidateBooking(portal) after CandidatePortal, mount
handles() before generic routes, and optionally set event_sink to the campaign
engine's transactional event writer. Public routes accept POST JSON only:
  /api/hr/booking/state {token}
  /api/hr/booking/book  {token, slotId, requestId}
  /api/hr/booking/call  {token} -> a separate existing-domain guest URL

The trusted campaign engine calls issue(db, candidate_id, campaign_id,
recipient_id) inside its own transaction; it commits invitation + send outbox
together. Repeating issuance returns the same live capability. Expired/revoked
links are not silently renewed. current()/is_sendable() are read-only preflight
checks; the engine separately checks campaign/audience/browser state.

Invitation rows contain a digest only. A private per-database HMAC key allows
the trusted issuer to recover the same URL; keep the database/backups private,
as with the existing HH outbox. Public DTOs never expose CV, contacts, internal
profile, campaign identifiers or media tokens. Public booking does not create
an account or record consent. Existing call consent/time rules remain in force.
"""
import base64
import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
from urllib.parse import parse_qs, quote, urlsplit

import candidate_invites as invites
from domain import Problem, iso, now, require, timestamp


PREFIX = '/api/hr/booking/'
TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{43}$')
ID_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,160}$')
TTL_SECONDS = 7 * 86400


def _id(value):
    require(isinstance(value, str) and ID_RE.fullmatch(value), 'Некорректный идентификатор.', 400)
    return value


def init(db):
    # Individual statements: never commit a caller's open transaction.
    for statement in (
        '''CREATE TABLE IF NOT EXISTS cp_booking_config (
          id INTEGER PRIMARY KEY CHECK(id=1), issuance_key BLOB NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS cp_booking_invitations (
          id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
          candidate_id TEXT NOT NULL REFERENCES cp_candidates(id),
          campaign_id TEXT NOT NULL, recipient_id TEXT NOT NULL,
          expires INTEGER NOT NULL, revoked_at TEXT, created_at TEXT NOT NULL,
          UNIQUE(campaign_id,recipient_id))''',
        '''CREATE INDEX IF NOT EXISTS cp_booking_candidate
          ON cp_booking_invitations(candidate_id)''',
        '''CREATE TABLE IF NOT EXISTS cp_booking_attribution (
          interview_id TEXT PRIMARY KEY REFERENCES cp_interviews(id),
          invitation_id TEXT UNIQUE NOT NULL REFERENCES cp_booking_invitations(id),
          campaign_id TEXT NOT NULL, recipient_id TEXT NOT NULL, created_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS cp_booking_requests (
          invitation_id TEXT NOT NULL REFERENCES cp_booking_invitations(id),
          request_id TEXT NOT NULL, slot_id TEXT NOT NULL REFERENCES cp_recruiter_slots(id),
          interview_id TEXT NOT NULL REFERENCES cp_interviews(id), created_at TEXT NOT NULL,
          PRIMARY KEY(invitation_id,request_id))''',
        '''CREATE TABLE IF NOT EXISTS cp_booking_call_links (
          interview_id TEXT PRIMARY KEY REFERENCES cp_interviews(id),
          call_invitation_id TEXT NOT NULL REFERENCES cp_invitations(id),
          url TEXT NOT NULL, expires_at TEXT NOT NULL)''',
    ):
        db.execute(statement)
    db.execute('INSERT OR IGNORE INTO cp_booking_config VALUES (1,?)', (secrets.token_bytes(32),))


class CandidateBooking:
    def __init__(self, portal, event_sink=None):
        self.portal, self.event_sink = portal, event_sink
        with portal.connect() as db:
            init(db)
        portal.booking_invitation_lookup = self.existing_invitation
        portal.booking_invitation_store = self.remember_invitation

    @staticmethod
    def handles(path):
        return urlsplit(path).path.startswith(PREFIX)

    @staticmethod
    def _token(db, invitation_id):
        key = db.execute('SELECT issuance_key FROM cp_booking_config WHERE id=1').fetchone()[0]
        digest = hmac.new(key, ('hr-booking-v1:' + invitation_id).encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip('=')

    @staticmethod
    def _prepared(db, candidate_id):
        return db.execute('''SELECT 1 FROM cp_candidates c JOIN cp_intake t ON t.candidate_id=c.id
          JOIN cp_resumes r ON r.candidate_id=c.id
          WHERE c.id=? AND t.source='hh' AND length(r.content)>0 AND length(trim(r.text_content))>0''',
                          (candidate_id,)).fetchone() is not None

    def issue(self, db, candidate_id, campaign_id, recipient_id, *, ttl_seconds=TTL_SECONDS):
        """Trusted only; caller owns authorization and the SQLite transaction."""
        for value in (candidate_id, campaign_id, recipient_id):
            _id(value)
        require(type(ttl_seconds) is int and 60 <= ttl_seconds <= 30 * 86400,
                'Некорректный срок приглашения.', 400)
        origin = urlsplit(self.portal.origin)
        require(origin.scheme == 'https' and origin.netloc and not origin.username and not origin.password
                and not origin.query and not origin.fragment and origin.path in ('', '/'),
                'Не настроен адрес бронирования.', 503)
        old = db.execute('''SELECT id,candidate_id,expires,revoked_at FROM cp_booking_invitations
          WHERE campaign_id=? AND recipient_id=?''', (campaign_id, recipient_id)).fetchone()
        if old:
            require(old[1] == candidate_id, 'Получатель уже связан с другим профилем.', 409)
            require(not old[3] and old[2] > int(now().timestamp()), 'Приглашение истекло или отозвано.', 410)
            identifier, expires = old[0], old[2]
        else:
            require(self._prepared(db, candidate_id), 'Сначала подготовьте профиль и резюме из HH.', 409)
            require(not db.execute('SELECT 1 FROM cp_interviews WHERE candidate_id=?', (candidate_id,)).fetchone(),
                    'Кандидат уже записан на интервью.', 409)
            identifier, expires = 'booking_' + secrets.token_hex(16), int(now().timestamp()) + ttl_seconds
            token = self._token(db, identifier)
            db.execute('INSERT INTO cp_booking_invitations VALUES (?,?,?,?,?,?,NULL,?)',
                       (identifier, hashlib.sha256(token.encode()).hexdigest(), candidate_id,
                        campaign_id, recipient_id, expires, iso(now())))
        return {'invitationId': identifier,
                'url': self.portal.origin.rstrip('/') + '/iframe.html?id=hr-vision-product--candidate'
                       '&viewMode=story&cp=invite-booking#booking=' + quote(self._token(db, identifier)),
                'expiresAt': iso(dt.datetime.fromtimestamp(expires, dt.timezone.utc))}

    @staticmethod
    def revoke(db, invitation_id):
        """Trusted explicit revocation; never called from a public token route."""
        require(db.execute('SELECT 1 FROM cp_booking_invitations WHERE id=?', (_id(invitation_id),)).fetchone(),
                'Приглашение не найдено.', 404)
        db.execute('UPDATE cp_booking_invitations SET revoked_at=COALESCE(revoked_at,?) WHERE id=?',
                   (iso(now()), invitation_id))

    def current(self, db, invitation_id, candidate_id=None):
        row = db.execute('''SELECT candidate_id,expires,revoked_at FROM cp_booking_invitations WHERE id=?''',
                         (invitation_id,)).fetchone()
        return bool(row and (candidate_id is None or candidate_id == row[0]) and not row[2]
                    and row[1] > int(now().timestamp()) and self._prepared(db, row[0])
                    and not db.execute('SELECT 1 FROM cp_interviews WHERE candidate_id=?', (row[0],)).fetchone())

    def is_sendable(self, db, invitation_id):
        return self.current(db, invitation_id)

    def _validate_call(self, db, cid, interview_id, result):
        parsed = urlsplit(result['url'])
        require(parsed.scheme == 'https' and parsed.netloc == urlsplit(self.portal.origin).netloc
                and not parsed.username and not parsed.password,
                'Ссылка на встречу недействительна.', 410)
        token = parse_qs(parsed.fragment).get('invite', [''])[0]
        capability = invites.resolve(db, token, 'call')
        require(capability['id'] == result['invitationId'] and capability['candidateId'] == cid
                and capability['interviewId'] == interview_id, 'Ссылка на встречу недействительна.', 410)

    def existing_invitation(self, db, cid, scope):
        """Read-only ordinary recruiter copy; explicit replacement bypasses it."""
        if scope != 'call':
            return None
        row = db.execute('''SELECT l.call_invitation_id,l.url,l.expires_at,l.interview_id
          FROM cp_booking_call_links l JOIN cp_interviews i ON i.id=l.interview_id
          WHERE i.candidate_id=? AND i.status IN ('booked','live')''', (cid,)).fetchone()
        if not row:
            return None
        result = {'invitationId': row[0], 'url': row[1], 'expiresAt': row[2]}
        try:
            self._validate_call(db, cid, row[3], result)
        except Problem:
            # An expired/revoked capability is not offered as a copy. The trusted
            # recruiter may explicitly request a new domain invitation.
            return None
        return result

    def remember_invitation(self, db, cid, scope, result):
        """Capture a trusted recruiter-created link without changing its domain."""
        if scope != 'call' or not db.execute('SELECT 1 FROM cp_booking_invitations WHERE candidate_id=?', (cid,)).fetchone():
            return
        interview = self._interview(db, cid)
        require(interview, 'Интервью не найдено.', 404)
        self._validate_call(db, cid, interview['id'], result)
        db.execute('''INSERT INTO cp_booking_call_links VALUES (?,?,?,?) ON CONFLICT(interview_id)
          DO UPDATE SET call_invitation_id=excluded.call_invitation_id,url=excluded.url,expires_at=excluded.expires_at''',
                   (interview['id'], result['invitationId'], result['url'], result['expiresAt']))

    @staticmethod
    def _resolve(db, token):
        require(isinstance(token, str) and TOKEN_RE.fullmatch(token), 'Ссылка недействительна.', 404)
        row = db.execute('''SELECT id,candidate_id,campaign_id,recipient_id,expires,revoked_at
          FROM cp_booking_invitations WHERE token_hash=?''', (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        require(row is not None, 'Ссылка недействительна.', 404)
        require(not row[5] and row[4] > int(now().timestamp()),
                'Ссылка истекла или отозвана. Попросите прислать новую.', 410)
        return dict(zip(('id', 'candidateId', 'campaignId', 'recipientId', 'expires', 'revokedAt'), row))

    def _event(self, db, event, invitation, *, interview_id=None, key):
        if self.event_sink:
            self.event_sink(db, event, invitation['recipientId'], campaign_id=invitation['campaignId'],
                            invitation_id=invitation['id'], interview_id=interview_id, event_key=key)

    @staticmethod
    def _interview(db, candidate_id):
        row = db.execute('''SELECT i.id,i.slot_id,i.status,s.start,s.end,COALESCE(a.name,s.recruiter_name),a.active
          FROM cp_interviews i JOIN cp_recruiter_slots s ON s.id=i.slot_id
          LEFT JOIN cp_recruiter_slot_owners o ON o.slot_id=s.id
          LEFT JOIN rc_accounts a ON a.id=o.recruiter_id WHERE i.candidate_id=?''', (candidate_id,)).fetchone()
        if not row:
            return None
        return {'id': row[0], 'slotId': row[1], 'status': row[2], 'start': row[3], 'end': row[4],
                'recruiterName': row[5], 'canOpenCall': bool(row[6] == 1 and row[2] in ('booked', 'live')
                  and timestamp(row[4]) + dt.timedelta(minutes=90) > now())}

    def _state(self, db, invitation):
        cid = invitation['candidateId']
        booking = self._interview(db, cid)
        slots = []
        if not booking:
            require(self._prepared(db, cid), 'Резюме пока недоступно. Свяжитесь с командой HR Vision.', 409)
            slots = [slot for slot in self.portal.slots(db)
                     if timestamp(slot['end']) - timestamp(slot['start']) == dt.timedelta(minutes=30)]
        # The capability needs no profile projection; only the person's name.
        row = db.execute('SELECT profile FROM cp_candidates WHERE id=?', (cid,)).fetchone()
        require(row, 'Приглашение не найдено.', 404)
        name = json.loads(row[0]).get('name', '')
        return {'status': 'booked' if booking else 'available', 'candidateName': name,
                'timezone': 'Europe/Moscow', 'slots': slots,
                'booking': {key: value for key, value in booking.items() if key != 'slotId'} if booking else None}

    def _book(self, db, invitation, body):
        slot_id, request_id = _id(body.get('slotId')), _id(body.get('requestId'))
        previous = db.execute('''SELECT slot_id FROM cp_booking_requests WHERE invitation_id=? AND request_id=?''',
                              (invitation['id'], request_id)).fetchone()
        if previous:
            require(previous[0] == slot_id, 'Этот запрос уже использован для другого времени.', 409)
            return self._state(db, invitation)
        existing = self._interview(db, invitation['candidateId'])
        if existing:
            require(existing['slotId'] == slot_id, 'Вы уже записаны. Для переноса свяжитесь с командой.', 409)
            interview_id = existing['id']
        else:
            # Unlike legacy prepared booking, a public link cannot choose an unowned slot.
            available = next((slot for slot in self.portal.slots(db) if slot['id'] == slot_id), None)
            require(available and timestamp(available['end']) - timestamp(available['start']) == dt.timedelta(minutes=30),
                    'Это время уже недоступно. Выберите другое.', 409)
            require(self._prepared(db, invitation['candidateId']), 'Сначала подготовьте резюме.', 409)
            self.portal.book(db, invitation['candidateId'], {'slotId': slot_id}, prepared=True)
            interview_id = self._interview(db, invitation['candidateId'])['id']
            db.execute('INSERT INTO cp_booking_attribution VALUES (?,?,?,?,?)',
                       (interview_id, invitation['id'], invitation['campaignId'], invitation['recipientId'], iso(now())))
            self._event(db, 'booking.created', invitation, interview_id=interview_id, key='booking:' + interview_id)
        db.execute('INSERT INTO cp_booking_requests VALUES (?,?,?,?,?)',
                   (invitation['id'], request_id, slot_id, interview_id, iso(now())))
        # A booking can lie beyond the initial invitation window. Keep this same
        # personal page available through the meeting without rotating its URL.
        booking = self._interview(db, invitation['candidateId'])
        retain_until = int(timestamp(booking['end']).timestamp()) + 90 * 60
        db.execute('UPDATE cp_booking_invitations SET expires=MAX(expires,?) WHERE id=?',
                   (retain_until, invitation['id']))
        return self._state(db, invitation)

    def _call(self, db, invitation):
        cid = invitation['candidateId']
        booking = self._interview(db, cid)
        require(booking and booking['canOpenCall'], 'Сначала выберите время действующего интервью.', 409)
        cached = db.execute('''SELECT call_invitation_id,url,expires_at FROM cp_booking_call_links WHERE interview_id=?''',
                            (booking['id'],)).fetchone()
        if cached:
            self._validate_call(db, cid, booking['id'], {'invitationId': cached[0], 'url': cached[1]})
            return {'url': cached[1], 'expiresAt': cached[2]}
        # Preserve a URL already delivered by the existing HH domain if recoverable.
        existing = self.portal.invitation_lookup(db, cid, 'call') if self.portal.invitation_lookup else None
        if existing:
            created = existing
        else:
            # Never silently replace an existing/revoked manual invitation.
            require(not db.execute("SELECT 1 FROM cp_invitations WHERE candidate_id=? AND interview_id=? AND scope='call'",
                                   (cid, booking['id'])).fetchone(),
                    'Ссылка на встречу уже создана. Используйте ссылку от рекрутера.', 409)
            created = invites.issue(db, cid, 'call', self.portal.origin)
        self.remember_invitation(db, cid, 'call', created)
        return {'url': created['url'], 'expiresAt': created['expiresAt']}

    def handle(self, method, raw_path, headers, body, ip):
        parsed = urlsplit(raw_path)
        require(parsed.path in (PREFIX + 'state', PREFIX + 'book', PREFIX + 'call'), 'Не найдено.', 404)
        require(method == 'POST', 'Откройте персональную ссылку.', 405)
        require(not parsed.query and not parsed.fragment, 'Передайте ссылку в теле запроса.', 400)
        require(headers.get('Origin') in (None, self.portal.origin)
                and headers.get('Sec-Fetch-Site') != 'cross-site', 'Другой источник запроса.', 403)
        require(headers.get('Content-Type', '').split(';', 1)[0].strip() == 'application/json', 'Нужен JSON.', 415)
        expected = {'token', 'slotId', 'requestId'} if parsed.path.endswith('/book') else {'token'}
        require(isinstance(body, dict) and set(body) == expected, 'Проверьте поля запроса.', 400)
        self.portal.rate_limit('booking-ip:' + str(ip), 120, 60)
        with self.portal.lock, self.portal.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            invitation = self._resolve(db, body['token'])
            if parsed.path.endswith('/state'):
                result = self._state(db, invitation)
                self._event(db, 'booking.page_loaded', invitation, key='booking.page_loaded:' + invitation['id'])
            elif parsed.path.endswith('/book'):
                result = self._book(db, invitation, body)
            else:
                result = self._call(db, invitation)
            return result, {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}
