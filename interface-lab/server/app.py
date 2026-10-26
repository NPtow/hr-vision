"""Isolated test API. State lives outside releases; media provider keys stay here."""
import base64
import binascii
import hashlib
import re
import hmac
import ipaddress
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from domain import NAMES, Problem, accounts, actor_name, fresh, public_state, require, transition, text, iso, now, timestamp
from zoom_api_client import zoom_from_env
from media_runtime import provider_budget, provider_timeout
from feedback import questionnaire, vtt_turns
import browser_access
from dsa_panel import DsaPanel
from candidate_portal import CandidatePortal, ClosingConnection
import candidate_notifications
from candidate_bot import CandidateBot
from hh_routes import HHRecruiterRoutes
from candidate_booking import CandidateBooking
from candidate_access import CandidateAccess
from hh_outreach import HHOutreach
from hh_outreach_routes import HHOutreachRoutes
from hh_outreach_worker import OutreachBrowserService
from employer_routes import EmployerRoutes
from selection import SelectionRoutes
from meeting_lab import MeetingLab
from public_profiles import PublicProfiles
import employer_access
import observability

ROOT = Path(os.environ.get('HR_DATA_DIR', '/var/lib/hr-vision-api'))
ROOT.mkdir(parents=True, exist_ok=True)
DB = ROOT / 'journey.sqlite3'
TEAM = os.environ['HR_TEAM_KEY']
ORIGIN = os.environ.get('HR_ORIGIN', 'https://hr-vision.158-160-179-53.sslip.io')
ORIGINS = browser_access.allowed_origins(ORIGIN, [value.strip() for value in
    os.environ.get('HR_ADDITIONAL_ORIGINS', '').split(',') if value.strip()])
MATERIALS_SERVICES_ENABLED = os.environ.get('HR_MATERIALS_SERVICES_ENABLED') == '1'
VIDEO_ENABLED = os.environ.get('HR_VIDEO_ENABLED') == '1'
TRUST_CLIENT_IP = os.environ.get('HR_TRUST_CLIENT_IP') == '1'
if MATERIALS_SERVICES_ENABLED:
    from core_source_client import clients_from_env
    CORE_CLIENT, MEDIA = clients_from_env()
else:
    from daily_media import DailyMedia
    MEDIA = DailyMedia(os.environ.get('DAILY_API_KEY', ''), VIDEO_ENABLED)
DSA_PANEL = DsaPanel()
LOCK = threading.RLock()


def client_identity(peer, headers):
    """Accept only the address overwritten by our ingress, never arbitrary XFF."""
    try:
        remote = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    if TRUST_CLIENT_IP and remote.is_loopback:
        forwarded = headers.get('X-HR-Client-IP', '')
        try:
            # One IP only; no forwarding chains, port suffixes or IPv6 zones.
            require(isinstance(forwarded, str) and len(forwarded) <= 45 and '%' not in forwarded,
                    'Некорректный адрес.', 400)
            return str(ipaddress.ip_address(forwarded.strip()))
        except (ValueError, Problem):
            pass
    return str(remote)


def connect():
    db = sqlite3.connect(DB, timeout=10, factory=ClosingConnection)
    db.execute('PRAGMA busy_timeout=10000')
    db.execute('PRAGMA foreign_keys=ON')
    return db


with connect() as db:
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS scenario (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, actor TEXT NOT NULL, expires INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS session_scopes (token TEXT PRIMARY KEY, scope TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS scenario_variants (scope TEXT PRIMARY KEY, body TEXT NOT NULL)')
    db.execute('INSERT OR IGNORE INTO scenario VALUES (1, ?)', (json.dumps(fresh()),))
    db.execute('CREATE TABLE IF NOT EXISTS intake_candidates (id TEXT PRIMARY KEY, scope TEXT NOT NULL, batch TEXT NOT NULL, body TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS intake_files (id TEXT PRIMARY KEY, candidate TEXT NOT NULL, name TEXT NOT NULL, content BLOB NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS intake_batches (id TEXT PRIMARY KEY, digest TEXT NOT NULL)')
os.chmod(DB, 0o600)
CANDIDATE_PORTAL = CandidatePortal(connect, LOCK, MEDIA, ORIGIN, candidate_notifications,
                                  origins=ORIGINS, recruiter_origin=os.environ.get('HR_RECRUITER_ORIGIN', ORIGIN),
                                  zoom=zoom_from_env())
CANDIDATE_BOT = CandidateBot(CANDIDATE_PORTAL, candidate_notifications)
PUBLIC_PROFILES = PublicProfiles(CANDIDATE_PORTAL)
HH_ROUTES = HHRecruiterRoutes(connect, CANDIDATE_PORTAL)
CANDIDATE_PORTAL.invitation_lookup = HH_ROUTES.existing_invitation
BOOKING = CandidateBooking(CANDIDATE_PORTAL)
CANDIDATE_ACCESS = CandidateAccess(CANDIDATE_PORTAL)
CANDIDATE_PORTAL.candidate_access = CANDIDATE_ACCESS
OUTREACH = HHOutreach(connect, issue_booking=BOOKING.issue, booking_current=BOOKING.current,
    prepare_candidate=HH_ROUTES.prepare_candidate,
    sending_enabled=os.environ.get('HR_HH_BROWSER_SENDING_ENABLED') == '1')
BOOKING.event_sink = OUTREACH.event_sink
OUTREACH_ROUTES = HHOutreachRoutes(OUTREACH, worker_secret=os.environ.get('HR_HH_OUTREACH_WORKER_SECRET', ''))
OUTREACH_BROWSER = OutreachBrowserService.from_env(OUTREACH, CANDIDATE_PORTAL)
EMPLOYER_ROUTES = EmployerRoutes(connect, LOCK, CANDIDATE_PORTAL, MEDIA, ORIGIN)
SELECTION_ROUTES = SelectionRoutes(connect, LOCK, CANDIDATE_PORTAL)
MEETING_LAB = MeetingLab(connect, LOCK, MEDIA)
MATERIALS_RUNTIME = None
if MATERIALS_SERVICES_ENABLED:
    from core_source_client import CoreMaterialsCatalog, ProviderMaterialsCatalog
    from core_source_outbox import CompletionOutbox
    CANDIDATE_PORTAL.materials_outbox = CompletionOutbox(connect, CORE_CLIENT, MEDIA,
        connection_id=os.environ.get('HR_MATERIALS_CONNECTION_ID', 'daily'))
    daily_catalog = CoreMaterialsCatalog(CORE_CLIENT, MEDIA, CANDIDATE_PORTAL.materials_outbox)
    zoom_catalog = None
    if os.environ.get('HR_MATERIALS_ENABLED') == '1':
        from materials_runtime import build_runtime
        from materials_service import MaterialsService
        # Transitional Zoom collector only. Services mode fences legacy Daily
        # work in runtime/worker; it never constructs a Daily provider client.
        # Keep owned Zoom playback available even without provider credentials.
        MATERIALS_RUNTIME = build_runtime(connect=connect, publish=lambda _job_id: None)
        zoom_catalog = MaterialsService(MATERIALS_RUNTIME)
    CANDIDATE_PORTAL.materials_catalog = ProviderMaterialsCatalog(daily_catalog, zoom_catalog)
elif os.environ.get('HR_MATERIALS_ENABLED') == '1':
    from materials_runtime import build_runtime
    from materials_service import MaterialsService
    # HTTP requests commit intent locally; the separate scheduler dispatches it.
    MATERIALS_RUNTIME = build_runtime(connect=connect, media=MEDIA, publish=lambda _job_id: None)
    CANDIDATE_PORTAL.materials_catalog = MaterialsService(MATERIALS_RUNTIME)

if os.environ.get('HR_CORE_CATALOG_ENABLED') == '1':
    # Daily remains the explicit existing reserve. Zoom and imported owned files
    # read Core; opening uploaded materials must never schedule provider capture.
    from core_source_client import CoreClient, CoreMaterialsCatalog, ProviderMaterialsCatalog, ServiceHTTP
    CORE_CLIENT = CoreClient(ServiceHTTP(os.environ['HR_MATERIALS_CORE_URL'],
        os.environ['HR_MATERIALS_CORE_KEY'], timeout=10))
    CANDIDATE_PORTAL.materials_catalog = ProviderMaterialsCatalog(
        CANDIDATE_PORTAL.materials_catalog, CoreMaterialsCatalog(CORE_CLIENT, connect=connect),
        CoreMaterialsCatalog(CORE_CLIENT, connect=connect, collect=False))


def read(db, scope='sfera'):
    require(scope in ('sfera', 'dsa'), 'Подбор не найден.', 404)
    row = (db.execute('SELECT body FROM scenario WHERE id=1').fetchone() if scope == 'sfera'
           else db.execute('SELECT body FROM scenario_variants WHERE scope=?', (scope,)).fetchone())
    require(row, 'Откройте подбор заново.', 409)
    state = json.loads(row[0])
    state['scope'] = scope
    return state


def write(db, state):
    body = json.dumps(state, ensure_ascii=False)
    if state.get('scope', 'sfera') == 'sfera':
        db.execute('UPDATE scenario SET body=? WHERE id=1', (body,))
    else:
        db.execute('UPDATE scenario_variants SET body=? WHERE scope=?', (body, state['scope']))


def ensure_scope(scope):
    if scope == 'sfera':
        return
    with connect() as db:
        if db.execute('SELECT 1 FROM scenario_variants WHERE scope=?', (scope,)).fetchone():
            return
    # Read once, outside the shared-state lock. Later source refreshes cannot erase test progress.
    source = DSA_PANEL.load()
    state = fresh('dsa', source['candidates'])
    state['vacancy']['role'] = source.get('role') or state['vacancy']['role']
    with LOCK, connect() as db:
        db.execute('INSERT OR IGNORE INTO scenario_variants VALUES (?,?)',
                   (scope, json.dumps(state, ensure_ascii=False)))



def intake_add(db, body):
    """Store employer-owned drafts without fabricating candidate consent or availability."""
    scope = body.get('scope')
    require(scope in ('sfera', 'dsa'), 'Выберите подбор.', 400)
    batch = body.get('batch')
    require(isinstance(batch, str) and re.fullmatch(r'[a-zA-Z0-9-]{12,80}', batch), 'Некорректный запрос.', 400)
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    old = db.execute('SELECT digest FROM intake_batches WHERE id=?', (batch,)).fetchone()
    if old:
        require(old[0] == digest, 'Состав кандидатов изменился. Обновите страницу.', 409)
        return {'candidates': [json.loads(row[0]) for row in db.execute('SELECT body FROM intake_candidates WHERE batch=? ORDER BY rowid', (batch,))]}
    entries = body.get('entries')
    require(isinstance(entries, list) and 1 <= len(entries) <= 10, 'Можно добавить от 1 до 10 кандидатов.', 400)
    count = db.execute('SELECT COUNT(*) FROM intake_candidates').fetchone()[0]
    require(count + len(entries) <= 500, 'Достигнут лимит базы кандидатов.')
    stored = db.execute('SELECT COALESCE(SUM(LENGTH(content)),0) FROM intake_files').fetchone()[0]
    created, total = [], 0
    for entry in entries:
        require(isinstance(entry, dict), 'Проверьте данные кандидата.', 400)
        person = {key: text(entry.get(key, ''), limit, required=key == 'name') for key, limit in [('name', 140), ('role', 200), ('contact', 160), ('link', 2048), ('note', 4000)]}
        if person['link']:
            try:
                url = urllib.parse.urlsplit(person['link'])
                require(url.scheme == 'https' and bool(url.hostname) and not url.username and not url.password and not any(ord(ch) < 32 for ch in person['link']), 'Нужна HTTPS-ссылка на резюме.', 400)
            except ValueError:
                raise Problem('Проверьте ссылку на резюме.', 400)
        person.update(id=secrets.token_hex(12), scope=scope, createdAt=iso(now()), archived=False, files=[])
        file = entry.get('file')
        if file is not None:
            require(isinstance(file, dict), 'Проверьте резюме.', 400)
            name = text(file.get('name'), 240)
            require('/' not in name and '\\' not in name and not any(ord(c) < 32 for c in name), 'Проверьте название файла.', 400)
            extension = Path(name).suffix.lower()
            require(extension in ('.pdf', '.doc', '.docx', '.rtf'), 'Подойдут PDF, DOC, DOCX или RTF.', 400)
            raw = file.get('data')
            require(isinstance(raw, str) and len(raw) <= 7 * 1024 * 1024, 'Резюме должно быть не больше 5 МБ.', 413)
            try:
                content = base64.b64decode(raw, validate=True)
            except (binascii.Error, ValueError):
                raise Problem('Не удалось прочитать файл.', 400)
            require(0 < len(content) <= 5 * 1024 * 1024, 'Резюме должно быть не больше 5 МБ.', 413)
            signatures = {'.pdf': b'%PDF-', '.doc': bytes.fromhex('d0cf11e0a1b11ae1'), '.docx': b'PK', '.rtf': b'{\\rtf'}
            require(content.startswith(signatures[extension]), 'Содержимое файла не соответствует формату.', 400)
            total += len(content)
            require(total <= 8 * 1024 * 1024 and stored + total <= 128 * 1024 * 1024, 'Недостаточно места для резюме.', 413)
            fid = secrets.token_hex(12)
            db.execute('INSERT INTO intake_files VALUES (?,?,?,?)', (fid, person['id'], name, content))
            person['files'].append({'id': fid, 'name': name, 'size': len(content)})
        db.execute('INSERT INTO intake_candidates VALUES (?,?,?,?)', (person['id'], scope, batch, json.dumps(person, ensure_ascii=False)))
        created.append(person)
    db.execute('INSERT INTO intake_batches VALUES (?,?)', (batch, digest))
    return {'candidates': created}


def provider(path, body=None, method=None):
    require(not MATERIALS_SERVICES_ENABLED, 'Raw provider access is unavailable.', 503)
    return MEDIA.request(path, body, method)


def get_meeting(state, actor, body):
    cid = body.get('candidate')
    require(cid in state['candidates'] and (actor == 'manager' or actor == cid), 'Нет доступа.', 403)
    person = state['candidates'][cid]
    m = person['meeting']
    require(body.get('generation') == state['generation'] and m and m['id'] == body.get('meetingId'), 'Встреча изменилась. Обновите страницу.')
    require(person['interest'] == 'accepted', 'Вакансия не принята.', 403)
    return person, m


def load_transcripts(room):
    if MATERIALS_SERVICES_ENABLED:
        return MEDIA.materials(room).get('transcripts', [])
    result = []
    transcripts = provider('/transcript?' + urllib.parse.urlencode({'roomId': room['id'], 'limit': 100}))
    for transcript in transcripts.get('data', []):
        if transcript.get('roomId') != room['id'] or transcript.get('status') != 't_finished':
            continue
        link = provider('/transcript/' + urllib.parse.quote(transcript['transcriptId'], safe='') + '/access-link')
        url = link.get('download_link') or link.get('link')
        require(isinstance(url, str) and url.startswith('https://'), 'Провайдер не вернул файл транскрипта.', 502)
        with urllib.request.urlopen(url, timeout=provider_timeout()) as file:
            content = file.read(1024 * 1024 + 1)
        require(len(content) <= 1024 * 1024, 'Транскрипт слишком большой для теста.', 502)
        result.append({'id': transcript['transcriptId'], 'text': content.decode('utf-8')})
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = 'HRVision'

    def log_message(self, *_):
        pass  # No request URLs, bearer tokens or transcripts in journal.

    def send_response(self, code, message=None):
        observability.response_status(code)
        return super().send_response(code, message)

    def answer(self, code, data, headers=None):
        content = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        forwarded = {name: value for name, value in (headers or {}).items()
                     if name.lower() not in {'content-length', 'cache-control', 'x-content-type-options'}}
        self.send_response(code)
        if not any(name.lower() == 'content-type' for name in forwarded):
            self.send_header('Content-Type', 'application/octet-stream' if isinstance(data, bytes) else 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(content)))
        for name, value in forwarded.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(content)

    def actor(self, db):
        raw = self.headers.get('Authorization', '')
        require(raw.startswith('Bearer ') and len(raw) < 200, 'Выберите аккаунт на стартовой странице.', 401)
        digest = hashlib.sha256(raw[7:].encode()).hexdigest()
        item = db.execute('SELECT actor FROM sessions WHERE token=? AND expires>?', (digest, int(time.time()))).fetchone()
        require(item, 'Сессия закончилась. Войдите снова.', 401)
        scope = db.execute('SELECT scope FROM session_scopes WHERE token=?', (digest,)).fetchone()
        self.scope = scope[0] if scope else 'sfera'
        return item[0]

    def browser_access(self):
        return browser_access.valid(self.headers.get('Cookie'), TEAM)

    def invite_access(self, db, body):
        if self.browser_access() or hmac.compare_digest(str(body.get('teamKey', '')).encode(), TEAM.encode()):
            return
        if self.headers.get('Authorization'):
            self.actor(db)  # Existing tabs can migrate without reopening an invitation.
            return
        raise Problem('Откройте актуальную ссылку HR Vision из чата.', 403)

    def feedback_questions(self, body):
        # Provider processing must not block state polling, chat or another call.
        with LOCK, connect() as db:
            actor = self.actor(db)
            state = read(db, self.scope)
            _, m = get_meeting(state, actor, body)
            require(actor == 'manager' and m['status'] == 'completed', 'Вопросы доступны работодателю после встречи.', 403)
            if m.get('questionnaire'):
                return self.answer(200, {'status': 'ready', 'questionnaire': m['questionnaire'], 'draft': m.get('feedbackDraft', {})})
            room = m.get('room')
            mid = m['id']
            scope = self.scope
        result = None
        status = 'missing'
        if room:
            try:
                with provider_budget():
                    transcripts = load_transcripts(room)
                turns = [turn for item in transcripts for turn in vtt_turns(item['text'], NAMES['manager'])]
                result = questionnaire(turns, mid)
                status = 'ready' if result else 'missing' if transcripts else 'processing'
            except Exception:
                status = 'unavailable'
        # Provider work can outlive logout, scope changes or room replacement.
        # Validate again before returning or persisting private interview data.
        with LOCK, connect() as db:
            require(self.actor(db) == actor and self.scope == scope, 'Войдите снова.', 401)
            state = read(db, self.scope)
            _, current = get_meeting(state, actor, body)
            require(current['status'] == 'completed' and current.get('room') == room,
                    'Встреча изменилась. Обновите страницу.', 409)
            if result:
                db.execute('BEGIN IMMEDIATE')
                # Freeze one version. A second device cannot change an open form.
                current.setdefault('questionnaire', result)
                if not current['questionnaire']:
                    current['questionnaire'] = result
                result = current['questionnaire']
                draft = current.get('feedbackDraft', {})
                write(db, state)
        if result:
            return self.answer(200, {'status': 'ready', 'questionnaire': result, 'draft': draft})
        return self.answer(200, {'status': status, 'questionnaire': None})

    def finish_meeting(self, body):
        with LOCK, connect() as db:
            actor = self.actor(db)
            _, meeting = get_meeting(read(db, self.scope), actor, body)
            mid = meeting['id']
        with CANDIDATE_PORTAL.media_operation(mid):
            return self._finish_meeting(body)

    def _finish_meeting(self, body):
        with LOCK, connect() as db:
            actor = self.actor(db)
            require(actor == 'manager', 'Нет доступа.', 403)
            state = read(db, self.scope)
            _, meeting = get_meeting(state, actor, body)
            room = meeting.get('room')
            if meeting['status'] != 'completed':
                # Validate first without persisting a premature completion.
                transition(state, actor, body)
        if room:
            MEDIA.finish(room)
        with LOCK, connect() as db:
            db.execute('BEGIN IMMEDIATE')
            actor = self.actor(db)
            require(actor == 'manager', 'Нет доступа.', 403)
            state = read(db, self.scope)
            _, meeting = get_meeting(state, actor, body)
            require(meeting.get('room') == room, 'Встреча изменилась. Обновите страницу.')
            if meeting['status'] != 'completed':
                state = transition(state, actor, body)
                write(db, state)
            result = public_state(state, actor)
            result['videoConfigured'] = MEDIA.configured
        return self.answer(200, result)

    def meeting_room(self, body):
        with LOCK, connect() as db:
            actor = self.actor(db)
            _, meeting = get_meeting(read(db, self.scope), actor, body)
            mid = meeting['id']
        # Reopening a room and finishing it must never race. Provider I/O does
        # not hold the shared state lock used by other candidates and cabinets.
        with CANDIDATE_PORTAL.media_operation(mid):
            with LOCK, connect() as db:
                actor = self.actor(db)
                state = read(db, self.scope)
                _, meeting = get_meeting(state, actor, body)
                require(meeting['status'] in ('confirmed', 'live') and not state['closedBy'], 'Сначала подтвердите встречу.')
                require(body.get('consent') is True, 'Подтвердите запись разговора.', 400)
                name = actor_name(state, actor)
            room = MEDIA.room(mid)
            from core_source_client import meeting_token
            token = meeting_token(MEDIA, room, {'room_name': room['name'],
                'user_name': name, 'user_id': actor, 'is_owner': actor == 'manager',
                'exp': int(time.time()) + 3600, 'eject_at_token_exp': True})
            with LOCK, connect() as db:
                db.execute('BEGIN IMMEDIATE')
                actor = self.actor(db)
                state = read(db, self.scope)
                _, meeting = get_meeting(state, actor, body)
                require(meeting['status'] in ('confirmed', 'live') and not state['closedBy'], 'Встреча изменилась. Обновите страницу.')
                meeting['room'] = room
                write(db, state)
            return self.answer(200, {'url': room['url'], 'token': token['token']})

    def meeting_materials(self, body):
        # Snapshot authorization and identity, then release the global state lock
        # before any provider call. A slow transcript must not freeze all cabinets.
        with LOCK, connect() as db:
            actor = self.actor(db)
            _, meeting = get_meeting(read(db, self.scope), actor, body)
            require(meeting['status'] == 'completed', 'Материалы доступны после встречи.')
            room = meeting.get('room')
            known = meeting.get('mediaMetadata')
            ended_at = meeting.get('endedAt')
            skipped = meeting.get('testSkip', False)
        if room:
            result = MEDIA.materials(room, known=known)
        else:
            result = {'recordings': [], 'transcripts': [],
                      'video': {'status': 'absent', 'ids': []},
                      'transcription': {'status': 'absent', 'ids': []}, 'checkedAt': iso(now())}
        # "Completed" alone is not evidence of a recording. Waiting is bounded;
        # a later refresh may still discover a delayed provider file.
        recent = bool(ended_at and (now() - timestamp(ended_at)).total_seconds() < 30 * 60)
        for channel in ('video', 'transcription'):
            status = result[channel]['status']
            if room and recent and status == 'absent':
                result[channel]['status'] = 'processing'
            elif not recent and status == 'processing':
                result[channel]['status'] = 'unavailable'
        result['testSkip'] = skipped
        with LOCK, connect() as db:
            db.execute('BEGIN IMMEDIATE')
            actor = self.actor(db)
            state = read(db, self.scope)
            _, current = get_meeting(state, actor, body)
            require(current['status'] == 'completed' and current.get('room') == room,
                    'Встреча изменилась. Обновите страницу.')
            # Persist identifiers and status only, never expiring playback URLs.
            previous = current.get('mediaMetadata') or {}
            records = {item['id']: {key: item.get(key) for key in ('id', 'duration')}
                       for item in previous.get('recordings', []) + result['recordings']}
            transcripts = {item['id']: {'id': item['id']}
                           for item in previous.get('transcripts', []) + result['transcripts']}
            current['mediaMetadata'] = {
                'recordings': list(records.values()),
                'transcripts': list(transcripts.values()),
                'videoStatus': result['video']['status'],
                'transcriptionStatus': result['transcription']['status'],
                'checkedAt': result['checkedAt'],
            }
            write(db, state)
        return self.answer(200, result)

    @observability.http_request
    def do_GET(self):
        try:
            if PUBLIC_PROFILES.handles(self.path):
                result, headers = PUBLIC_PROFILES.handle('GET', self.path, self.headers, None,
                    client_identity(self.client_address[0], self.headers))
                return self.answer(200, result, headers)
            if SELECTION_ROUTES.handles(self.path):
                result, headers = SELECTION_ROUTES.handle('GET', self.path, self.headers, None, self.actor)
                return self.answer(200, result, headers)
            if MEETING_LAB.handles(self.path):
                result, headers = MEETING_LAB.handle('GET', self.path, self.headers)
                return self.answer(200, result, headers)
            if OUTREACH_BROWSER.handles(self.path):
                result, headers = OUTREACH_BROWSER.handle('GET', self.path, self.headers, None, self.actor)
                return self.answer(200, result, headers)
            if OUTREACH_ROUTES.handles(self.path):
                result, headers = OUTREACH_ROUTES.handle('GET', self.path, self.headers, None, self.actor)
                return self.answer(200, result, headers)
            if BOOKING.handles(self.path):
                result, headers = BOOKING.handle('GET', self.path, self.headers, None,
                    client_identity(self.client_address[0], self.headers))
                return self.answer(200, result, headers)
            if EMPLOYER_ROUTES.handles(self.path):
                result, headers = EMPLOYER_ROUTES.handle('GET', self.path, self.headers,
                    ip=client_identity(self.client_address[0], self.headers), team_grant=self.browser_access())
                return self.answer(200, result, headers)
            if HH_ROUTES.handles(self.path):
                result, headers = HH_ROUTES.handle('GET', self.path, self.headers, None, self.actor)
                return self.answer(200, result, headers)
            if CANDIDATE_PORTAL.handles(urllib.parse.urlsplit(self.path).path):
                result, headers = CANDIDATE_PORTAL.handle('GET', self.path, self.headers, None,
                    client_identity(self.client_address[0], self.headers), self.actor, team_grant=self.browser_access())
                return self.answer(200, result, headers)
            if self.path == '/api/hr/health':
                return self.answer(200, {'ok': True, 'videoConfigured': MEDIA.configured})
            if self.path == '/api/hr/access':
                return self.answer(200, {'ready': self.browser_access()})
            if self.path.startswith('/api/hr/intake/files/'):
                fid = self.path.removeprefix('/api/hr/intake/files/')
                require(re.fullmatch(r'[a-f0-9]{24}', fid), 'Резюме не найдено.', 404)
                with connect() as db:
                    require(self.actor(db) == 'manager', 'Резюме доступно работодателю.', 403)
                    file = db.execute('SELECT name, content FROM intake_files WHERE id=?', (fid,)).fetchone()
                require(file is not None, 'Резюме не найдено.', 404)
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + urllib.parse.quote(file[0], safe=''))
                self.send_header('Content-Length', str(len(file[1])))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(file[1])
                return
            if urllib.parse.urlsplit(self.path).path == '/api/hr/intake':
                scope = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('scope', [''])[0]
                require(scope in ('sfera', 'dsa'), 'Выберите подбор.', 400)
                with connect() as db:
                    require(self.actor(db) == 'manager', 'Кандидаты доступны работодателю.', 403)
                    people = [json.loads(row[0]) for row in db.execute('SELECT body FROM intake_candidates WHERE scope=? ORDER BY rowid DESC', (scope,))]
                return self.answer(200, {'candidates': people})
            if self.path == '/api/hr/dsa-candidates':
                with connect() as db:
                    require(self.actor(db) == 'manager', 'Материалы доступны работодателю.', 403)
                # Source I/O never holds the hiring scenario lock.
                return self.answer(200, DSA_PANEL.load())
            require(self.path == '/api/hr/state', 'Не найдено.', 404)
            with LOCK, connect() as db:
                actor = self.actor(db)
                result = public_state(read(db, self.scope), actor)
                result['videoConfigured'] = MEDIA.configured
            self.answer(200, result)
        except Problem as error:
            if error.status >= 500:
                observability.capture_exception(error)
            self.answer(error.status, {'error': error.message})
        except Exception as error:
            observability.capture_exception(error)
            self.answer(500, {'error': 'Не удалось прочитать состояние. Попробуйте ещё раз.'})

    @observability.http_request
    def do_POST(self):
        try:
            # Session bearer auth is tab-scoped; explicit JSON + origin blocks browser CSRF.
            require(self.headers.get('Origin') is None or self.headers.get('Origin') in ORIGINS,
                    'Другой источник запроса.', 403)
            path = urllib.parse.urlsplit(self.path).path
            upload_part = re.fullmatch(r'/api/hr/employer/recordings/uploads/([a-f0-9]{32})/parts/([0-9]{1,2})', path)
            if upload_part:
                require(self.headers.get('Sec-Fetch-Site') not in ('cross-site', 'none'), 'Другой источник запроса.', 403)
                require(self.headers.get('Content-Type') == 'application/octet-stream' and
                        not self.headers.get('Transfer-Encoding'), 'Нужен файл с известным размером.', 415)
                size = int(self.headers.get('Content-Length', '0'))
                require(0 < size <= 1024**3, 'Файл должен быть не больше 1 ГБ.', 413)
                self.connection.settimeout(300)
                result = EMPLOYER_ROUTES.recording_uploads.stream_part(self.headers, upload_part[1], int(upload_part[2]), self.rfile, size)
                return self.answer(200, result)
            require(self.headers.get('Content-Type', '').startswith('application/json'), 'Нужен JSON.', 415)
            if EMPLOYER_ROUTES.handles(path) or MEETING_LAB.handles(path) or SELECTION_ROUTES.handles(path):
                require(self.headers.get('Sec-Fetch-Site') not in ('cross-site', 'none') or
                        self.headers.get('Sec-Fetch-Site') == 'none' and self.headers.get('Origin') in ORIGINS,
                        'Другой источник запроса.', 403)
            if path == '/api/hr/employer/telegram/session':
                require(self.headers.get('Origin') in ORIGINS, 'Другой источник запроса.', 403)
            size = int(self.headers.get('Content-Length', '0'))
            coach_notes = path in (
                '/api/hr/recruiter/interview/coach/progress',
                '/api/hr/recruiter/interview/coach/restore',
            )
            if path in ('/api/hr/employer/intake', '/api/hr/employer/vacancies/import') or MEETING_LAB.handles(path):
                with connect() as db:
                    employer_access.authenticate(db, self.headers)
            if path == '/api/hr/recruiter/candidates' or coach_notes:
                with connect() as db:
                    CANDIDATE_PORTAL.recruiter_principal(db, self.headers, self.actor)
            if path == '/api/hr/intake':
                with connect() as db:
                    require(self.actor(db) == 'manager', 'Кандидатов добавляет работодатель.', 403)
            if path == '/api/hr/candidate/resume':
                # Authenticate before reading a potentially large private file.
                with connect() as db:
                    CANDIDATE_PORTAL.session_candidate(db, self.headers)
            if SELECTION_ROUTES.handles(path):
                with connect() as db:
                    require(self.actor(db) == 'manager', 'Доступ только для команды HR Vision.', 403)
            outreach_discovery = bool(re.fullmatch(r'/api/hr/outreach/campaigns/[A-Za-z0-9_-]+/discover', path))
            if outreach_discovery:
                OUTREACH_ROUTES.authorize(self.actor)
            limit = (256 * 1024 if path == '/api/hr/materials/webhooks/daily' else
                     12 * 1024 * 1024 if path in ('/api/hr/intake', '/api/hr/employer/intake') else
                     7 * 1024 * 1024 if path in ('/api/hr/candidate/resume', '/api/hr/recruiter/candidates', '/api/hr/employer/vacancies/import') else
                     3 * 1024 * 1024 if path in ('/api/hr/selection/results', '/api/hr/selection/requirements', '/api/hr/selection/matches') else
                     256 * 1024 if coach_notes else
                     256 * 1024 if outreach_discovery else
                     128 * 1024 if path in ('/api/hr/employer/vacancies', '/api/hr/employer/vacancies/update') or MEETING_LAB.handles(path) else 32768)
            require(0 < size <= limit, 'Слишком большой запрос.', 413)
            raw_body = self.rfile.read(size)
            require(len(raw_body) == size, 'Неполный запрос.', 400)
            if path == '/api/hr/materials/webhooks/daily':
                if MATERIALS_SERVICES_ENABLED:
                    result = MEDIA.forward_webhook(raw_body, self.headers)
                else:
                    require(MATERIALS_RUNTIME is not None, 'Каталог материалов ещё не подключён.', 503)
                    result = MATERIALS_RUNTIME.handle_event('daily', self.headers, raw_body)
                return self.answer(200, result)
            body = json.loads(raw_body)
            require(isinstance(body, dict), 'Некорректный запрос.', 400)
            if PUBLIC_PROFILES.handles(self.path):
                result, headers = PUBLIC_PROFILES.handle('POST', self.path, self.headers, body,
                    client_identity(self.client_address[0], self.headers))
                return self.answer(200, result, headers)
            if SELECTION_ROUTES.handles(self.path):
                result, headers = SELECTION_ROUTES.handle('POST', self.path, self.headers, body, self.actor)
                return self.answer(200, result, headers)
            if MEETING_LAB.handles(self.path):
                result, headers = MEETING_LAB.handle('POST', self.path, self.headers, body)
                return self.answer(200, result, headers)
            if OUTREACH_BROWSER.handles(self.path):
                result, headers = OUTREACH_BROWSER.handle('POST', self.path, self.headers, body, self.actor)
                return self.answer(200, result, headers)
            if OUTREACH_ROUTES.handles(self.path):
                result, headers = OUTREACH_ROUTES.handle('POST', self.path, self.headers, body, self.actor)
                return self.answer(200, result, headers)
            if BOOKING.handles(self.path):
                result, headers = BOOKING.handle('POST', self.path, self.headers, body,
                    client_identity(self.client_address[0], self.headers))
                return self.answer(200, result, headers)
            if EMPLOYER_ROUTES.handles(self.path):
                result, headers = EMPLOYER_ROUTES.handle('POST', self.path, self.headers, body,
                    ip=client_identity(self.client_address[0], self.headers), team_grant=self.browser_access())
                return self.answer(200, result, headers)
            if HH_ROUTES.handles(self.path):
                result, headers = HH_ROUTES.handle('POST', self.path, self.headers, body, self.actor)
                return self.answer(200, result, headers)
            if self.path == '/api/hr/candidate/telegram/webhook':
                with LOCK, connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    result = candidate_notifications.handle_telegram_update(db, body,
                        supplied_secret=self.headers.get('X-Telegram-Bot-Api-Secret-Token', ''),
                        update_handler=CANDIDATE_BOT.handle_update)
                return self.answer(200, result)
            if CANDIDATE_PORTAL.handles(urllib.parse.urlsplit(self.path).path):
                result, headers = CANDIDATE_PORTAL.handle('POST', self.path, self.headers, body,
                    client_identity(self.client_address[0], self.headers), self.actor, team_grant=self.browser_access())
                return self.answer(200, result, headers)
            if self.path == '/api/hr/feedback/questions':
                return self.feedback_questions(body)
            if self.path == '/api/hr/materials':
                return self.meeting_materials(body)
            if self.path == '/api/hr/action' and body.get('action') == 'meeting.finish':
                return self.finish_meeting(body)
            if self.path == '/api/hr/room':
                return self.meeting_room(body)
            if self.path in ('/api/hr/session', '/api/hr/accounts'):
                scope = body.get('scope', 'sfera')
                require(scope in ('sfera', 'dsa'), 'Подбор не найден.', 400)
                with connect() as db:
                    self.invite_access(db, body)
                ensure_scope(scope)
            response_headers = {}
            with LOCK, connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if self.path == '/api/hr/access':
                    self.invite_access(db, body)
                    response_headers['Set-Cookie'] = browser_access.issue(TEAM)
                    response = {'ready': True}
                elif self.path == '/api/hr/invite':
                    self.invite_access(db, {})
                    task = '&task=dsa' if body.get('scope') == 'dsa' else ''
                    response = {'url': ORIGIN + '/iframe.html?id=hr-vision-product--start&viewMode=story' + task + '#team=' + urllib.parse.quote(TEAM, safe='')}
                elif self.path == '/api/hr/accounts':
                    response = accounts(read(db, scope))
                elif self.path == '/api/hr/session':
                    self.invite_access(db, body)
                    actor = body.get('actor')
                    state = read(db, scope)
                    require(actor == 'manager' or actor in state['candidates'], 'Аккаунт не найден.', 400)
                    token = secrets.token_urlsafe(32)
                    digest = hashlib.sha256(token.encode()).hexdigest()
                    db.execute('DELETE FROM sessions WHERE expires<?', (int(time.time()),))
                    db.execute('DELETE FROM session_scopes WHERE token NOT IN (SELECT token FROM sessions)')
                    db.execute('INSERT INTO sessions VALUES (?,?,?)', (digest, actor, int(time.time()) + 86400 * 7))
                    db.execute('INSERT INTO session_scopes VALUES (?,?)', (digest, scope))
                    response = {'token': token, 'actor': actor, 'scope': scope}
                else:
                    actor = self.actor(db)
                    state = read(db, self.scope)
                    if self.path == '/api/hr/intake':
                        require(actor == 'manager', 'Кандидатов добавляет работодатель.', 403)
                        response = intake_add(db, body)
                    elif self.path == '/api/hr/intake/archive':
                        require(actor == 'manager', 'Нет доступа.', 403)
                        require(isinstance(body.get('archived'), bool), 'Выберите действие.', 400)
                        item = db.execute('SELECT body FROM intake_candidates WHERE id=?', (body.get('id'),)).fetchone()
                        require(item is not None, 'Кандидат не найден.', 404)
                        person = json.loads(item[0]); person['archived'] = body['archived']
                        db.execute('UPDATE intake_candidates SET body=? WHERE id=?', (json.dumps(person, ensure_ascii=False), person['id']))
                        response = {'candidate': person}
                    elif self.path == '/api/hr/action':
                        state = transition(state, actor, body)
                        write(db, state)
                        response = public_state(state, actor)
                        response['videoConfigured'] = MEDIA.configured
                    else:
                        raise Problem('Не найдено.', 404)
            self.answer(200, response, response_headers)
        except Problem as error:
            if error.status >= 500:
                observability.capture_exception(error)
            self.answer(error.status, {'error': error.message})
        except (ValueError, json.JSONDecodeError, TypeError):
            self.answer(400, {'error': 'Некорректный запрос.'})
        except Exception as error:
            observability.capture_exception(error)
            self.answer(500, {'error': 'Не удалось сохранить действие. Попробуйте ещё раз.'})


if __name__ == '__main__':
    observability.install_thread_hook()
    import call_cleanup
    call_cleanup.start_worker(CANDIDATE_PORTAL)
    import assessment_jobs
    assessment_jobs.start_worker(CANDIDATE_PORTAL)
    import assessment_core_lifecycle

    def authorize_assessment_candidate(candidate_id):
        # Background reconciliation has no browser session. Its configured
        # allowlist/creation cursor scopes work; ownership is the HR backend's
        # canonical candidate directory, never an arbitrary caller-supplied ID.
        with connect() as db:
            require(db.execute('SELECT 1 FROM cp_candidates WHERE id=?',
                               (candidate_id,)).fetchone(), 'Кандидат не найден.', 404)

    CANDIDATE_PORTAL.assessment_lifecycle = assessment_core_lifecycle.start_background(
        connect, LOCK, authorize_assessment_candidate)
    import recommendations_bridge
    CANDIDATE_PORTAL.recommendation_lifecycle = recommendations_bridge.start_background(
        connect, LOCK, CANDIDATE_PORTAL)
    import employer_review
    employer_review.start_worker(CANDIDATE_PORTAL)
    candidate_notifications.start_worker(connect, update_handler=CANDIDATE_BOT.handle_update,
        telegram_renderer=CANDIDATE_BOT.render_notification)
    HH_ROUTES.start_worker()
    OUTREACH_BROWSER.start_worker()
    CANDIDATE_ACCESS.start_worker()
    server = ThreadingHTTPServer(('127.0.0.1', int(os.environ.get('HR_PORT', '8391'))), Handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if CANDIDATE_PORTAL.assessment_lifecycle is not None:
            CANDIDATE_PORTAL.assessment_lifecycle.stop()
        if CANDIDATE_PORTAL.recommendation_lifecycle is not None:
            CANDIDATE_PORTAL.recommendation_lifecycle.stop()
