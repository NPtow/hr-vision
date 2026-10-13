"""Durable HH browser campaign queue. No HH API, network calls or live scheduler.

The SQLite file/WAL is private: only a worker claim contains message capability
URLs. Public projections deliberately omit message bodies, links and lease tokens.
A send_started job is never reclaimed for sending after a crash.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import re
import secrets
import sqlite3
import string
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from domain import Problem


EVENTS = {'recipient.imported', 'message.queued', 'message.sent_observed', 'message.unknown',
          'booking.page_loaded', 'booking.created', 'call.access_issued', 'interview.completed',
          'recording.ready', 'transcript.ready'}
REASONS = {'logged_out', 'challenge', 'identity_mismatch', 'ui_changed', 'cv_missing',
           'booking_unavailable', 'already_booked', 'campaign_paused', 'lease_expired',
           'transport_error', 'unconfirmed', 'operator_pause', 'duplicate', 'invalid_evidence'}
LEASE_SECONDS = 300


def require(ok, code, status=400):
    if not ok:
        raise Problem(code, status)


def identifier(value):
    value = str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) else ''
    require(bool(re.fullmatch(r'[A-Za-z0-9_-]{1,160}', value)), 'invalid_identifier')
    return value


def uid(prefix):
    return prefix + '_' + secrets.token_hex(12)


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def hh_url(value):
    require(isinstance(value, str) and len(value) <= 2000, 'invalid_hh_url')
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        require(parsed.scheme == 'https' and (host == 'hh.ru' or host.endswith('.hh.ru'))
                and not parsed.username and not parsed.password and parsed.port in (None, 443)
                and not parsed.fragment and '\\' not in value and '\n' not in value and '\r' not in value,
                'invalid_hh_url')
        allowed = {'chatId', 'chat_id', 'resumeId', 'vacancyId', 'topicId', 'responseId'}
        query = [(k, identifier(v)) for k, v in parse_qsl(parsed.query) if k in allowed]
        return urlunsplit(('https', host, parsed.path, urlencode(query), ''))
    except ValueError:
        raise Problem('invalid_hh_url', 400) from None


def template_value(value):
    require(isinstance(value, str) and 1 <= len(value.strip()) <= 8000, 'invalid_template')
    try:
        fields = [key for _, key, fmt, conversion in string.Formatter().parse(value) if key is not None]
        require(fields.count('booking_url') == 1 and set(fields) <= {'booking_url', 'name'}, 'invalid_template')
        require(all(not fmt and not conversion for _, key, fmt, conversion in string.Formatter().parse(value)
                    if key is not None), 'invalid_template')
    except ValueError:
        raise Problem('invalid_template', 400) from None
    return value.strip()


class HHOutreach:
    def __init__(self, connect, *, issue_booking, booking_current, prepare_candidate,
                 clock=time.time, sending_enabled=False):
        self.connect, self.clock = connect, clock
        self.issue_booking, self.booking_current = issue_booking, booking_current
        self.prepare_candidate, self.sending_enabled = prepare_candidate, sending_enabled
        with self.db() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS ho_campaigns (
                id TEXT PRIMARY KEY,name TEXT NOT NULL,account_id TEXT NOT NULL,mode TEXT NOT NULL,
                vacancy_id TEXT,status TEXT NOT NULL,template TEXT NOT NULL,send_limit INTEGER NOT NULL,
                pace_seconds INTEGER NOT NULL,cursor TEXT,created REAL NOT NULL,updated REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS ho_identities (
                account_id TEXT NOT NULL,resume_id TEXT NOT NULL,candidate_id TEXT NOT NULL REFERENCES cp_candidates(id),
                PRIMARY KEY(account_id,resume_id));
              CREATE TABLE IF NOT EXISTS ho_recipients (
                id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL REFERENCES ho_campaigns(id),
                account_id TEXT NOT NULL,resume_id TEXT NOT NULL,chat_id TEXT NOT NULL,
                response_id TEXT,vacancy_id TEXT,chat_url TEXT NOT NULL,resume_url TEXT NOT NULL,
                candidate_id TEXT NOT NULL REFERENCES cp_candidates(id),name TEXT NOT NULL,created REAL NOT NULL,
                UNIQUE(campaign_id,resume_id),UNIQUE(campaign_id,chat_id));
              CREATE TABLE IF NOT EXISTS ho_outbox (
                id TEXT PRIMARY KEY,recipient_id TEXT UNIQUE NOT NULL REFERENCES ho_recipients(id),
                campaign_id TEXT NOT NULL REFERENCES ho_campaigns(id),account_id TEXT NOT NULL,
                invitation_id TEXT NOT NULL,message TEXT NOT NULL,message_hash TEXT NOT NULL,
                booking_url TEXT NOT NULL,state TEXT NOT NULL,attempt INTEGER NOT NULL DEFAULT 0,
                claim_hash TEXT,lease_until REAL NOT NULL DEFAULT 0,attempt_id TEXT,
                reason TEXT,provider_message_id TEXT,created REAL NOT NULL,updated REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS ho_delivery_guards (
                account_id TEXT NOT NULL,resume_id TEXT NOT NULL,chat_id TEXT NOT NULL,
                job_id TEXT UNIQUE NOT NULL REFERENCES ho_outbox(id),
                PRIMARY KEY(account_id,resume_id),UNIQUE(account_id,chat_id));
              CREATE TABLE IF NOT EXISTS ho_account_leases (
                account_id TEXT PRIMARY KEY,worker_id TEXT,job_id TEXT,claim_hash TEXT,
                expires REAL NOT NULL DEFAULT 0,next_send REAL NOT NULL DEFAULT 0,blocked_reason TEXT);
              CREATE TABLE IF NOT EXISTS ho_attempts (
                id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES ho_outbox(id),
                state TEXT NOT NULL,created REAL NOT NULL,started REAL,finished REAL,
                evidence TEXT);
              CREATE TABLE IF NOT EXISTS ho_events (
                id TEXT PRIMARY KEY,recipient_id TEXT NOT NULL REFERENCES ho_recipients(id),
                kind TEXT NOT NULL,interview_id TEXT,event_key TEXT UNIQUE NOT NULL,created REAL NOT NULL);
              CREATE UNIQUE INDEX IF NOT EXISTS ho_one_booking ON ho_events(interview_id)
                WHERE kind='booking.created';
              CREATE INDEX IF NOT EXISTS ho_claimable ON ho_outbox(account_id,state,created);
              CREATE INDEX IF NOT EXISTS ho_campaign_events ON ho_events(recipient_id,kind);
            ''')

    @contextmanager
    def db(self, write=False):
        db = self.connect()
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA busy_timeout=10000')
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            if write:
                db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def event_sink(self, db, event_type, recipient_id=None, *, campaign_id=None,
                   invitation_id=None, interview_id=None, event_key=None):
        require(event_type in EVENTS, 'invalid_event')
        recipient = db.execute('SELECT campaign_id,candidate_id FROM ho_recipients WHERE id=?', (recipient_id,)).fetchone()
        require(recipient and (campaign_id is None or recipient[0] == campaign_id), 'recipient_not_found', 404)
        if event_type in ('booking.created', 'call.access_issued', 'interview.completed', 'recording.ready', 'transcript.ready'):
            require(interview_id and db.execute('SELECT 1 FROM cp_interviews WHERE id=? AND candidate_id=?', (interview_id, recipient[1])).fetchone(), 'interview_identity_conflict', 409)
        if invitation_id is not None:
            require(db.execute('SELECT 1 FROM ho_outbox WHERE recipient_id=? AND invitation_id=?',
                               (recipient_id, invitation_id)).fetchone(), 'invitation_mismatch', 409)
        require(isinstance(event_key, str) and 1 <= len(event_key) <= 300, 'invalid_event_key')
        existing = db.execute('SELECT recipient_id,kind,interview_id FROM ho_events WHERE event_key=?', (event_key,)).fetchone()
        if existing:
            require(tuple(existing) == (recipient_id, event_type, interview_id), 'event_identity_conflict', 409)
            return False
        db.execute('INSERT INTO ho_events VALUES (?,?,?,?,?,?)',
                   (uid('evt'), recipient_id, event_type, interview_id, event_key, self.clock()))
        return True

    @staticmethod
    def _campaign(db, campaign_id):
        row = db.execute('SELECT * FROM ho_campaigns WHERE id=?', (identifier(campaign_id),)).fetchone()
        require(row, 'campaign_not_found', 404)
        return row

    @staticmethod
    def _public_campaign(row):
        return {'id': row['id'], 'name': row['name'], 'accountId': row['account_id'], 'mode': row['mode'],
                'vacancyId': row['vacancy_id'], 'status': row['status'], 'template': row['template'],
                'limit': row['send_limit'], 'paceSeconds': row['pace_seconds'],
                'createdAt': row['created'], 'updatedAt': row['updated']}

    def create_campaign(self, data):
        require(isinstance(data, dict), 'invalid_campaign')
        name = data.get('name')
        require(isinstance(name, str) and 1 <= len(name.strip()) <= 200, 'invalid_name')
        account = identifier(data.get('accountId'))
        mode = data.get('mode')
        require(mode in ('direct', 'applicants'), 'invalid_source_mode')
        vacancy = identifier(data.get('vacancyId')) if mode == 'applicants' else None
        template = template_value(data.get('template'))
        limit, pace = data.get('limit'), data.get('paceSeconds')
        require(type(limit) is int and 1 <= limit <= 1000, 'invalid_limit')
        require(type(pace) is int and 30 <= pace <= 86400, 'invalid_pace')
        cid, stamp = uid('campaign'), self.clock()
        with self.db(write=True) as db:
            db.execute('INSERT INTO ho_campaigns VALUES (?,?,?,?,?,\'draft\',?,?,?,NULL,?,?)',
                       (cid, name.strip(), account, mode, vacancy, template, limit, pace, stamp, stamp))
            return self._public_campaign(self._campaign(db, cid))

    def campaign(self, campaign_id):
        with self.db() as db:
            return self._public_campaign(self._campaign(db, campaign_id))

    def campaigns(self):
        with self.db() as db:
            return [self._public_campaign(row) for row in db.execute('SELECT * FROM ho_campaigns ORDER BY created DESC LIMIT 200')]

    def control(self, campaign_id, action):
        require(action in ('pause', 'resume', 'cancel'), 'invalid_action')
        with self.db(write=True) as db:
            campaign = self._campaign(db, campaign_id)
            require(campaign['status'] != 'cancelled', 'campaign_cancelled', 409)
            status = {'pause': 'paused', 'resume': 'running', 'cancel': 'cancelled'}[action]
            db.execute('UPDATE ho_campaigns SET status=?,updated=? WHERE id=?', (status, self.clock(), campaign_id))
            if action == 'cancel':
                # An in-flight external click cannot be undone or assumed unsent.
                jobs = db.execute("SELECT id FROM ho_outbox WHERE campaign_id=? AND state IN ('queued','claimed','blocked')", (campaign_id,)).fetchall()
                for job in jobs:
                    if not db.execute('SELECT 1 FROM ho_attempts WHERE job_id=? AND started IS NOT NULL', (job['id'],)).fetchone():
                        db.execute('DELETE FROM ho_delivery_guards WHERE job_id=?', (job['id'],))
                    db.execute("UPDATE ho_outbox SET state='cancelled',claim_hash=NULL,lease_until=0,updated=? WHERE id=?", (self.clock(), job['id']))
                    db.execute('UPDATE ho_account_leases SET expires=0,job_id=NULL,claim_hash=NULL WHERE job_id=?', (job['id'],))
            return self._public_campaign(self._campaign(db, campaign_id))

    def import_recipients(self, campaign_id, records, *, cursor=None):
        require(isinstance(records, list) and 0 <= len(records) <= 500, 'invalid_recipients')
        require(cursor is None or isinstance(cursor, str) and len(cursor) <= 1000, 'invalid_cursor')
        imported, repeated = 0, 0
        with self.db(write=True) as db:
            campaign = self._campaign(db, campaign_id)
            require(campaign['status'] != 'cancelled', 'campaign_cancelled', 409)
            for raw in records:
                require(isinstance(raw, dict), 'invalid_recipient')
                account, resume, chat = (identifier(raw.get(key)) for key in ('accountId', 'resumeId', 'chatId'))
                require(account == campaign['account_id'], 'account_mismatch', 409)
                response = identifier(raw['responseId']) if raw.get('responseId') is not None else None
                vacancy = identifier(raw['vacancyId']) if raw.get('vacancyId') is not None else None
                require(campaign['mode'] != 'applicants' or response and vacancy == campaign['vacancy_id'], 'source_mismatch', 409)
                chat_url, resume_url = hh_url(raw.get('chatUrl')), hh_url(raw.get('resumeUrl'))
                old = db.execute('SELECT id,chat_id,response_id,vacancy_id FROM ho_recipients WHERE campaign_id=? AND resume_id=?', (campaign_id, resume)).fetchone()
                require(not old or (old['chat_id'], old['response_id'], old['vacancy_id']) == (chat, response, vacancy), 'source_identity_conflict', 409)
                require(not db.execute('SELECT 1 FROM ho_recipients WHERE campaign_id=? AND chat_id=? AND resume_id!=?', (campaign_id, chat, resume)).fetchone(), 'chat_identity_conflict', 409)
                profile = raw.get('profile') or {'name': raw.get('name', '')}
                require(isinstance(profile, dict), 'invalid_profile')
                require(not profile.get('autoApprove') and not profile.get('consentReuse'), 'imported_consent_forbidden')
                cv_text = raw.get('cvText', '')
                require(isinstance(cv_text, str) and len(cv_text) <= 200000, 'invalid_cv')
                require(type(raw.get('cvAvailable', False)) is bool, 'invalid_cv_availability')
                source = {'resumeId': resume, 'cvAvailable': bool(raw.get('cvAvailable') and cv_text.strip()),
                          'cvText': cv_text, 'contactEmail': raw.get('contactEmail', '')}
                external_key = 'hh-browser:' + digest(account + ':' + resume)
                candidate_id = self.prepare_candidate(db, external_key, profile, source)
                identity = db.execute('SELECT candidate_id FROM ho_identities WHERE account_id=? AND resume_id=?', (account, resume)).fetchone()
                require(not identity or identity[0] == candidate_id, 'candidate_identity_conflict', 409)
                db.execute('INSERT OR IGNORE INTO ho_identities VALUES (?,?,?)', (account, resume, candidate_id))
                if old:
                    repeated += 1
                    continue
                require(db.execute('SELECT COUNT(*) FROM ho_recipients WHERE campaign_id=?', (campaign_id,)).fetchone()[0] < 1000, 'recipient_limit_reached', 409)
                rid = uid('recipient')
                name = raw.get('name') or profile.get('name') or ''
                require(isinstance(name, str) and len(name) <= 300, 'invalid_name')
                db.execute('INSERT INTO ho_recipients VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                           (rid, campaign_id, account, resume, chat, response, vacancy, chat_url, resume_url, candidate_id, name, self.clock()))
                self.event_sink(db, 'recipient.imported', rid, event_key='import:' + rid)
                imported += 1
            db.execute('UPDATE ho_campaigns SET cursor=?,updated=? WHERE id=?', (cursor, self.clock(), campaign_id))
        return {'imported': imported, 'repeated': repeated}

    def queue(self, campaign_id, recipient_ids=None):
        require(recipient_ids is None or isinstance(recipient_ids, list) and len(recipient_ids) <= 1000, 'invalid_recipients')
        requested = set(identifier(value) for value in recipient_ids) if recipient_ids is not None else None
        queued, skipped = [], []
        with self.db(write=True) as db:
            campaign = self._campaign(db, campaign_id)
            require(campaign['status'] != 'cancelled', 'campaign_cancelled', 409)
            rows = db.execute('SELECT * FROM ho_recipients WHERE campaign_id=? ORDER BY created,rowid', (campaign_id,)).fetchall()
            require(requested is None or requested <= {r['id'] for r in rows}, 'recipient_not_found', 404)
            count = db.execute("SELECT COUNT(*) FROM ho_outbox WHERE campaign_id=? AND state!='cancelled'", (campaign_id,)).fetchone()[0]
            for row in rows:
                rid = row['id']
                if requested is not None and rid not in requested:
                    continue
                existing = db.execute('SELECT id FROM ho_outbox WHERE recipient_id=?', (rid,)).fetchone()
                reason = None
                if existing:
                    reason = 'already_queued'
                elif count >= campaign['send_limit']:
                    reason = 'limit_reached'
                elif db.execute('SELECT 1 FROM cp_interviews WHERE candidate_id=?', (row['candidate_id'],)).fetchone():
                    reason = 'already_booked'
                elif db.execute('SELECT 1 FROM ho_delivery_guards WHERE account_id=? AND (resume_id=? OR chat_id=?)', (row['account_id'], row['resume_id'], row['chat_id'])).fetchone():
                    reason = 'duplicate'
                elif not db.execute("SELECT 1 FROM cp_resumes WHERE candidate_id=? AND length(trim(text_content))>0", (row['candidate_id'],)).fetchone():
                    reason = 'cv_missing'
                if reason:
                    skipped.append({'recipientId': rid, 'reason': reason})
                    continue
                issued = self.issue_booking(db, row['candidate_id'], campaign_id, rid)
                message = campaign['template'].format(booking_url=issued['url'], name=row['name'])
                job = uid('send')
                db.execute('''INSERT INTO ho_outbox(id,recipient_id,campaign_id,account_id,invitation_id,message,message_hash,
                  booking_url,state,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                           (job, rid, campaign_id, row['account_id'], issued['invitationId'], message, digest(message), issued['url'], 'queued', self.clock(), self.clock()))
                db.execute('INSERT INTO ho_delivery_guards VALUES (?,?,?,?)', (row['account_id'], row['resume_id'], row['chat_id'], job))
                self.event_sink(db, 'message.queued', rid, event_key='queued:' + job)
                queued.append(job)
                count += 1
        return {'queued': queued, 'skipped': skipped}

    def _recover(self, db, account):
        expired = db.execute("SELECT * FROM ho_outbox WHERE account_id=? AND state IN ('claimed','send_started') AND lease_until<=?", (account, self.clock())).fetchall()
        for row in expired:
            state = 'unknown' if row['state'] == 'send_started' else 'queued'
            db.execute('UPDATE ho_outbox SET state=?,reason=\'lease_expired\',claim_hash=NULL,lease_until=0,updated=? WHERE id=?', (state, self.clock(), row['id']))
            db.execute('UPDATE ho_attempts SET state=?,finished=? WHERE id=?', (state, self.clock(), row['attempt_id']))
            if state == 'unknown':
                self.event_sink(db, 'message.unknown', row['recipient_id'], event_key='unknown:' + row['attempt_id'])
                db.execute('UPDATE ho_account_leases SET blocked_reason=\'unconfirmed\' WHERE account_id=?', (account,))
        db.execute('UPDATE ho_account_leases SET job_id=NULL,claim_hash=NULL,worker_id=NULL WHERE account_id=? AND expires<=?', (account, self.clock()))

    def recover_account(self, account_id):
        """Persist expired intent as unknown even while the browser/sender is paused."""
        account = identifier(account_id)
        with self.db(write=True) as db:
            self._recover(db, account)
            return {'unknown': db.execute("SELECT COUNT(*) FROM ho_outbox WHERE account_id=? AND state='unknown'", (account,)).fetchone()[0]}

    def claim(self, account_id, worker_id):
        account, worker = identifier(account_id), identifier(worker_id)
        if not self.sending_enabled:
            return None
        with self.db(write=True) as db:
            db.execute('INSERT OR IGNORE INTO ho_account_leases(account_id) VALUES (?)', (account,))
            self._recover(db, account)
            lease = db.execute('SELECT * FROM ho_account_leases WHERE account_id=?', (account,)).fetchone()
            if lease['expires'] > self.clock() or lease['next_send'] > self.clock() or lease['blocked_reason']:
                return None
            job = db.execute('''SELECT o.*,r.resume_id,r.chat_id,r.chat_url,r.resume_url,r.response_id,r.vacancy_id,r.candidate_id
              FROM ho_outbox o JOIN ho_recipients r ON r.id=o.recipient_id JOIN ho_campaigns c ON c.id=o.campaign_id
              WHERE o.account_id=? AND o.state='queued' AND c.status='running' ORDER BY o.created,o.rowid LIMIT 1''', (account,)).fetchone()
            if not job:
                return None
            token, until = secrets.token_urlsafe(32), self.clock() + LEASE_SECONDS
            attempt = job['attempt'] + 1
            attempt_id = job['id'] + '_' + str(attempt)
            db.execute("UPDATE ho_outbox SET state='claimed',attempt=?,attempt_id=?,claim_hash=?,lease_until=?,reason=NULL,updated=? WHERE id=?", (attempt, attempt_id, digest(token), until, self.clock(), job['id']))
            db.execute('INSERT INTO ho_attempts(id,job_id,state,created) VALUES (?,?,\'claimed\',?)', (attempt_id, job['id'], self.clock()))
            db.execute('UPDATE ho_account_leases SET worker_id=?,job_id=?,claim_hash=?,expires=? WHERE account_id=?', (worker, job['id'], digest(token), until, account))
            return {'jobId': job['id'], 'attemptId': attempt_id, 'claimToken': token, 'leaseUntil': until,
                    'campaignId': job['campaign_id'], 'recipientId': job['recipient_id'], 'accountId': account,
                    'resumeId': job['resume_id'], 'chatId': job['chat_id'], 'chatUrl': job['chat_url'],
                    'resumeUrl': job['resume_url'], 'responseId': job['response_id'], 'vacancyId': job['vacancy_id'],
                    'message': job['message'], 'messageHash': job['message_hash'], 'bookingUrl': job['booking_url']}

    def _claimed(self, db, job_id, token):
        require(isinstance(token, str) and 20 <= len(token) <= 100, 'invalid_claim', 403)
        row = db.execute('SELECT * FROM ho_outbox WHERE id=?', (identifier(job_id),)).fetchone()
        require(row and row['claim_hash'] and secrets.compare_digest(row['claim_hash'], digest(token)), 'invalid_claim', 403)
        require(row['lease_until'] > self.clock(), 'lease_expired', 409)
        lease = db.execute('SELECT * FROM ho_account_leases WHERE account_id=?', (row['account_id'],)).fetchone()
        require(lease and lease['job_id'] == row['id'] and lease['claim_hash'] == row['claim_hash'] and lease['expires'] > self.clock(), 'lease_expired', 409)
        return row

    def start(self, job_id, claim_token):
        require(self.sending_enabled, 'sending_disabled', 503)
        with self.db(write=True) as db:
            job = self._claimed(db, job_id, claim_token)
            require(job['state'] == 'claimed', 'send_already_started', 409)
            campaign = self._campaign(db, job['campaign_id'])
            require(campaign['status'] == 'running', 'campaign_paused', 409)
            require(self.booking_current(db, job['invitation_id']), 'booking_unavailable', 409)
            db.execute("UPDATE ho_outbox SET state='send_started',updated=? WHERE id=?", (self.clock(), job_id))
            db.execute("UPDATE ho_attempts SET state='send_started',started=? WHERE id=?", (self.clock(), job['attempt_id']))
            # Pace counts uncertain attempts too: no rapid attempts after restart.
            db.execute('UPDATE ho_account_leases SET next_send=? WHERE account_id=?', (self.clock() + campaign['pace_seconds'], job['account_id']))
            return {'started': True, 'attemptId': job['attempt_id']}

    @staticmethod
    def _evidence(job, recipient, evidence):
        require(isinstance(evidence, dict), 'invalid_evidence', 409)
        require(all(evidence.get(key) == expected for key, expected in (
            ('accountId', job['account_id']), ('resumeId', recipient['resume_id']),
            ('chatId', recipient['chat_id']), ('messageHash', job['message_hash']))), 'invalid_evidence', 409)
        require(evidence.get('persisted') is True, 'invalid_evidence', 409)
        observed = evidence.get('observedAt')
        require(type(observed) in (int, float) and math.isfinite(observed) and observed > 0, 'invalid_evidence', 409)
        message_id = identifier(evidence.get('messageId'))
        return {'accountId': job['account_id'], 'resumeId': recipient['resume_id'], 'chatId': recipient['chat_id'],
                'messageHash': job['message_hash'], 'messageId': message_id, 'persisted': True, 'observedAt': observed}

    def result(self, job_id, claim_token, outcome, *, reason=None, evidence=None):
        require(outcome in ('sent_observed', 'unknown', 'blocked'), 'invalid_outcome')
        safe_reason = reason if reason in REASONS else 'unconfirmed'
        with self.db(write=True) as db:
            job = self._claimed(db, job_id, claim_token)
            require(job['state'] in ('claimed', 'send_started'), 'invalid_job_state', 409)
            if outcome == 'sent_observed':
                require(job['state'] == 'send_started', 'send_not_started', 409)
                recipient = db.execute('SELECT * FROM ho_recipients WHERE id=?', (job['recipient_id'],)).fetchone()
                proof = self._evidence(job, recipient, evidence)
            else:
                proof = None
                # After click intent, neither a provider error nor logout proves absence.
                if job['state'] == 'send_started':
                    outcome = 'unknown'
            db.execute('UPDATE ho_outbox SET state=?,reason=?,provider_message_id=?,claim_hash=NULL,lease_until=0,updated=? WHERE id=?',
                       (outcome, None if proof else safe_reason, proof['messageId'] if proof else None, self.clock(), job_id))
            db.execute('UPDATE ho_attempts SET state=?,finished=?,evidence=? WHERE id=?', (outcome, self.clock(), json.dumps(proof) if proof else None, job['attempt_id']))
            blocked = None if proof else safe_reason
            db.execute('UPDATE ho_account_leases SET worker_id=NULL,job_id=NULL,claim_hash=NULL,expires=0,blocked_reason=? WHERE account_id=?', (blocked, job['account_id']))
            if outcome in ('sent_observed', 'unknown'):
                self.event_sink(db, 'message.' + outcome, job['recipient_id'], event_key=outcome + ':' + job['attempt_id'])
            return {'jobId': job_id, 'state': outcome}

    def reconcile(self, job_id, evidence):
        """Read-only browser evidence can settle unknown; absence never authorizes a click."""
        with self.db(write=True) as db:
            job = db.execute('SELECT * FROM ho_outbox WHERE id=?', (identifier(job_id),)).fetchone()
            require(job and job['state'] == 'unknown', 'job_not_unknown', 409)
            recipient = db.execute('SELECT * FROM ho_recipients WHERE id=?', (job['recipient_id'],)).fetchone()
            proof = self._evidence(job, recipient, evidence)
            db.execute("UPDATE ho_outbox SET state='sent_observed',reason=NULL,provider_message_id=?,updated=? WHERE id=?", (proof['messageId'], self.clock(), job_id))
            db.execute("UPDATE ho_attempts SET state='sent_observed',evidence=?,finished=? WHERE id=?", (json.dumps(proof), self.clock(), job['attempt_id']))
            self.event_sink(db, 'message.sent_observed', job['recipient_id'], event_key='sent_observed:' + job['attempt_id'])
            return {'jobId': job_id, 'state': 'sent_observed'}

    def unblock_account(self, account_id):
        """Operator explicitly re-enables an account; unknown jobs stay unknown."""
        with self.db(write=True) as db:
            account = identifier(account_id)
            self._recover(db, account)
            require(not db.execute('SELECT 1 FROM ho_account_leases WHERE account_id=? AND expires>?', (account, self.clock())).fetchone(), 'account_busy', 409)
            db.execute('UPDATE ho_account_leases SET blocked_reason=NULL WHERE account_id=?', (account,))
        return {'unblocked': True}

    def retry_blocked(self, job_id):
        with self.db(write=True) as db:
            job = db.execute('SELECT * FROM ho_outbox WHERE id=?', (identifier(job_id),)).fetchone()
            require(job and job['state'] == 'blocked', 'job_not_blocked', 409)
            require(not db.execute('SELECT 1 FROM ho_attempts WHERE job_id=? AND started IS NOT NULL', (job_id,)).fetchone(), 'send_may_have_happened', 409)
            require(self._campaign(db, job['campaign_id'])['status'] != 'cancelled', 'campaign_cancelled', 409)
            db.execute("UPDATE ho_outbox SET state='queued',reason=NULL,updated=? WHERE id=?", (self.clock(), job_id))
        return {'queued': True}

    def detail(self, campaign_id):
        with self.db() as db:
            campaign = self._campaign(db, campaign_id)
            rows = db.execute('''SELECT r.*,o.id job_id,o.state,o.reason,o.attempt,o.invitation_id,o.provider_message_id,
              o.updated job_updated FROM ho_recipients r LEFT JOIN ho_outbox o ON o.recipient_id=r.id
              WHERE r.campaign_id=? ORDER BY r.created,r.id LIMIT 1000''', (campaign_id,)).fetchall()
            events = db.execute('''SELECT e.* FROM ho_events e JOIN ho_recipients r ON r.id=e.recipient_id
              WHERE r.campaign_id=? ORDER BY e.created DESC LIMIT 10000''', (campaign_id,)).fetchall()
            event_map = {}
            bookings = {}
            for event in events:
                event_map.setdefault(event['recipient_id'], set()).add(event['kind'])
                if event['kind'] == 'booking.created':
                    bookings[event['recipient_id']] = event['interview_id']
            stats = {key: 0 for key in ('recipients', 'queued', 'sentObserved', 'unknown', 'blocked', 'pageLoaded', 'booked', 'completed', 'videoReady', 'transcriptReady')}
            recipients = []
            for row in rows:
                kinds = event_map.get(row['id'], set())
                iid = bookings.get(row['id'])
                interview = db.execute('SELECT status,room FROM cp_interviews WHERE id=? AND candidate_id=?', (iid, row['candidate_id'])).fetchone() if iid else None
                media = db.execute("SELECT body,room_id FROM cp_media_state WHERE meeting_id=? AND kind='interview'", (iid,)).fetchone() if iid else None
                try:
                    room = json.loads(interview['room']) if interview and interview['room'] else {}
                    media_room = json.loads(media['room_id']) if media else {}
                except (ValueError, TypeError):
                    room, media_room = {}, {}
                if not isinstance(room, dict) or not room.get('id') or not room.get('name') or media_room != {'id': room.get('id'), 'name': room.get('name')}:
                    media = None
                try:
                    material = json.loads(media[0]) if media else {}
                except (ValueError, TypeError):
                    material = {}
                if not isinstance(material, dict):
                    material = {}
                def ready(channel):
                    state = material.get(channel)
                    return isinstance(state, dict) and state.get('status') == 'ready' and bool(state.get('ids'))
                flags = {'recipients': True, 'queued': 'message.queued' in kinds,
                         'sentObserved': row['state'] == 'sent_observed', 'unknown': row['state'] == 'unknown',
                         'blocked': row['state'] == 'blocked',
                         'pageLoaded': 'booking.page_loaded' in kinds, 'booked': bool(iid),
                         'completed': bool(interview and interview['status'] == 'completed'),
                         'videoReady': ready('video'), 'transcriptReady': ready('transcription')}
                for key, value in flags.items():
                    stats[key] += int(value)
                recipients.append({'id': row['id'], 'candidateId': row['candidate_id'], 'name': row['name'],
                    'mode': campaign['mode'], 'resumeId': row['resume_id'], 'chatId': row['chat_id'],
                    'responseId': row['response_id'], 'vacancyId': row['vacancy_id'],
                    'jobId': row['job_id'], 'state': row['state'] or 'imported', 'reason': row['reason'],
                    'attempts': row['attempt'] or 0, 'interviewId': iid, 'progress': flags})
            lease = db.execute('SELECT blocked_reason,next_send FROM ho_account_leases WHERE account_id=?', (campaign['account_id'],)).fetchone()
            return {'campaign': self._public_campaign(campaign), 'recipients': recipients, 'stats': stats,
                    'sendingEnabled': bool(self.sending_enabled),
                    'account': {'blockedReason': lease['blocked_reason'] if lease else None,
                                'nextSendAt': lease['next_send'] if lease else None},
                    'events': [{'recipientId': e['recipient_id'], 'kind': e['kind'], 'interviewId': e['interview_id'], 'at': e['created']} for e in events[:100]]}
