"""Isolated test API. State lives outside releases; media provider keys stay here."""
import hashlib
import hmac
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
from domain import IDS, NAMES, Problem, fresh, public_state, require, transition
from daily_media import DailyMedia
from feedback import questionnaire, vtt_turns
import browser_access
from dsa_panel import DsaPanel

ROOT = Path(os.environ.get('HR_DATA_DIR', '/var/lib/hr-vision-api'))
ROOT.mkdir(parents=True, exist_ok=True)
DB = ROOT / 'journey.sqlite3'
TEAM = os.environ['HR_TEAM_KEY']
ORIGIN = os.environ.get('HR_ORIGIN', 'https://hr-vision.158-160-179-53.sslip.io')
DAILY = os.environ.get('DAILY_API_KEY', '')
VIDEO_ENABLED = os.environ.get('HR_VIDEO_ENABLED') == '1'
MEDIA = DailyMedia(DAILY, VIDEO_ENABLED)
DSA_PANEL = DsaPanel()
LOCK = threading.RLock()


def connect():
    db = sqlite3.connect(DB, timeout=10)
    db.execute('PRAGMA busy_timeout=10000')
    return db


with connect() as db:
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS scenario (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, actor TEXT NOT NULL, expires INTEGER NOT NULL)')
    db.execute('INSERT OR IGNORE INTO scenario VALUES (1, ?)', (json.dumps(fresh()),))
os.chmod(DB, 0o600)


def read(db):
    return json.loads(db.execute('SELECT body FROM scenario WHERE id=1').fetchone()[0])


def write(db, state):
    db.execute('UPDATE scenario SET body=? WHERE id=1', (json.dumps(state, ensure_ascii=False),))


def provider(path, body=None, method=None):
    return MEDIA.request(path, body, method)


def get_meeting(state, actor, body):
    cid = body.get('candidate')
    require(cid in IDS and (actor == 'manager' or actor == cid), 'Нет доступа.', 403)
    person = state['candidates'][cid]
    m = person['meeting']
    require(body.get('generation') == state['generation'] and m and m['id'] == body.get('meetingId'), 'Встреча изменилась. Обновите страницу.')
    require(person['interest'] == 'accepted', 'Вакансия не принята.', 403)
    return person, m


def load_transcripts(room):
    result = []
    transcripts = provider('/transcript?' + urllib.parse.urlencode({'roomId': room['id'], 'limit': 100}))
    for transcript in transcripts.get('data', []):
        if transcript.get('roomId') != room['id'] or transcript.get('status') != 't_finished':
            continue
        link = provider('/transcript/' + urllib.parse.quote(transcript['transcriptId'], safe='') + '/access-link')
        url = link.get('download_link') or link.get('link')
        require(isinstance(url, str) and url.startswith('https://'), 'Провайдер не вернул файл транскрипта.', 502)
        with urllib.request.urlopen(url, timeout=18) as file:
            content = file.read(1024 * 1024 + 1)
        require(len(content) <= 1024 * 1024, 'Транскрипт слишком большой для теста.', 502)
        result.append({'id': transcript['transcriptId'], 'text': content.decode('utf-8')})
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = 'HRVision'

    def log_message(self, *_):
        pass  # No request URLs, bearer tokens or transcripts in journal.

    def answer(self, code, data, headers=None):
        content = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(content)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(content)

    def actor(self, db):
        raw = self.headers.get('Authorization', '')
        require(raw.startswith('Bearer ') and len(raw) < 200, 'Выберите аккаунт на стартовой странице.', 401)
        digest = hashlib.sha256(raw[7:].encode()).hexdigest()
        item = db.execute('SELECT actor FROM sessions WHERE token=? AND expires>?', (digest, int(time.time()))).fetchone()
        require(item, 'Сессия закончилась. Войдите снова.', 401)
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
            state = read(db)
            _, m = get_meeting(state, actor, body)
            require(actor == 'manager' and m['status'] == 'completed', 'Вопросы доступны работодателю после встречи.', 403)
            if m.get('questionnaire'):
                return self.answer(200, {'status': 'ready', 'questionnaire': m['questionnaire'], 'draft': m.get('feedbackDraft', {})})
            room = m.get('room')
            mid = m['id']
        result = None
        status = 'missing'
        if room:
            try:
                transcripts = load_transcripts(room)
                turns = [turn for item in transcripts for turn in vtt_turns(item['text'], NAMES['manager'])]
                result = questionnaire(turns, mid)
                status = 'ready' if result else 'missing' if transcripts else 'processing'
            except Exception:
                status = 'unavailable'
        if result:
            with LOCK, connect() as db:
                db.execute('BEGIN IMMEDIATE')
                state = read(db)
                _, current = get_meeting(state, actor, body)
                # Freeze one version. A second device cannot change an open form.
                current.setdefault('questionnaire', result)
                if not current['questionnaire']:
                    current['questionnaire'] = result
                result = current['questionnaire']
                draft = current.get('feedbackDraft', {})
                write(db, state)
            return self.answer(200, {'status': 'ready', 'questionnaire': result, 'draft': draft})
        return self.answer(200, {'status': status, 'questionnaire': None})

    def do_GET(self):
        try:
            if self.path == '/api/hr/health':
                return self.answer(200, {'ok': True, 'videoConfigured': bool(DAILY and VIDEO_ENABLED)})
            if self.path == '/api/hr/access':
                return self.answer(200, {'ready': self.browser_access()})
            if self.path == '/api/hr/dsa-candidates':
                with connect() as db:
                    require(self.actor(db) == 'manager', 'Материалы доступны работодателю.', 403)
                # Source I/O never holds the hiring scenario lock.
                return self.answer(200, DSA_PANEL.load())
            require(self.path == '/api/hr/state', 'Не найдено.', 404)
            with LOCK, connect() as db:
                actor = self.actor(db)
                result = public_state(read(db), actor)
                result['videoConfigured'] = bool(DAILY and VIDEO_ENABLED)
            self.answer(200, result)
        except Problem as error:
            self.answer(error.status, {'error': error.message})
        except Exception:
            self.answer(500, {'error': 'Не удалось прочитать состояние. Попробуйте ещё раз.'})

    def do_POST(self):
        try:
            # Session bearer auth is tab-scoped; explicit JSON + origin blocks browser CSRF.
            require(self.headers.get('Origin') in (None, ORIGIN), 'Другой источник запроса.', 403)
            require(self.headers.get('Content-Type', '').startswith('application/json'), 'Нужен JSON.', 415)
            size = int(self.headers.get('Content-Length', '0'))
            require(0 < size <= 32768, 'Слишком большой запрос.', 413)
            body = json.loads(self.rfile.read(size))
            require(isinstance(body, dict), 'Некорректный запрос.', 400)
            if self.path == '/api/hr/feedback/questions':
                return self.feedback_questions(body)
            response_headers = {}
            with LOCK, connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if self.path == '/api/hr/access':
                    self.invite_access(db, body)
                    response_headers['Set-Cookie'] = browser_access.issue(TEAM)
                    response = {'ready': True}
                elif self.path == '/api/hr/invite':
                    self.invite_access(db, {})
                    response = {'url': ORIGIN + '/iframe.html?id=hr-vision-product--start&viewMode=story#team=' + urllib.parse.quote(TEAM, safe='')}
                elif self.path == '/api/hr/session':
                    self.invite_access(db, body)
                    actor = body.get('actor')
                    require(actor in (*IDS, 'manager'), 'Аккаунт не найден.', 400)
                    token = secrets.token_urlsafe(32)
                    db.execute('DELETE FROM sessions WHERE expires<?', (int(time.time()),))
                    db.execute('INSERT INTO sessions VALUES (?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), actor, int(time.time()) + 86400 * 7))
                    response = {'token': token, 'actor': actor}
                else:
                    actor = self.actor(db)
                    state = read(db)
                    if self.path == '/api/hr/action':
                        if body.get('action') == 'meeting.finish':
                            _, meeting = get_meeting(state, actor, body)
                            require(actor == 'manager', 'Нет доступа.', 403)
                            if meeting.get('room'):
                                # Finishing is explicit. A network disconnect is never completion.
                                MEDIA.finish(meeting['room'])
                        state = transition(state, actor, body)
                        write(db, state)
                        response = public_state(state, actor)
                        response['videoConfigured'] = bool(DAILY and VIDEO_ENABLED)
                    elif self.path == '/api/hr/room':
                        _, m = get_meeting(state, actor, body)
                        require(m['status'] in ('confirmed', 'live') and not state['closedBy'], 'Сначала подтвердите встречу.')
                        require(body.get('consent') is True, 'Подтвердите запись разговора.', 400)
                        m['room'] = MEDIA.room(m['id'])
                        write(db, state)
                        token = provider('/meeting-tokens', {'properties': {'room_name': m['room']['name'],
                            'user_name': NAMES[actor], 'user_id': actor, 'is_owner': actor == 'manager',
                            'exp': int(time.time()) + 3600, 'eject_at_token_exp': True}})
                        response = {'url': m['room']['url'], 'token': token['token']}
                    elif self.path == '/api/hr/materials':
                        _, m = get_meeting(state, actor, body)
                        require(m['status'] == 'completed', 'Материалы доступны после встречи.')
                        response = {'recordings': [], 'transcripts': [], 'testSkip': m['testSkip']}
                        if m.get('room'):
                            records = provider('/recordings?' + urllib.parse.urlencode({'room_name': m['room']['name'], 'limit': 100}))
                            for recording in records.get('data', []):
                                if recording.get('room_name') == m['room']['name'] and recording.get('status') == 'finished':
                                    link = provider('/recordings/' + urllib.parse.quote(recording['id'], safe='') + '/access-link')
                                    response['recordings'].append({'id': recording['id'], 'url': link['download_link'], 'duration': recording.get('duration')})
                            response['transcripts'] = load_transcripts(m['room'])
                    else:
                        raise Problem('Не найдено.', 404)
            self.answer(200, response, response_headers)
        except Problem as error:
            self.answer(error.status, {'error': error.message})
        except (ValueError, json.JSONDecodeError, TypeError):
            self.answer(400, {'error': 'Некорректный запрос.'})
        except Exception:
            self.answer(500, {'error': 'Не удалось сохранить действие. Попробуйте ещё раз.'})


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', int(os.environ.get('HR_PORT', '8391'))), Handler).serve_forever()
