"""Revocable, read-only capabilities for a single completed candidate interview.

Tokens travel in a URL fragment and POST bodies, never URL/access logs. A share
does not grant an account, change hiring consent or expose recruiter notes.
"""
import hashlib
import json
import re
import secrets
from urllib.parse import urlsplit

from domain import iso, now, require
from interview_assistant import InterviewAssistant
import selection


PUBLIC = '/api/hr/public-profile'
OWNER = '/api/hr/candidate/public-profile'
HEADERS = {'X-Robots-Tag': 'noindex, nofollow, noarchive', 'Referrer-Policy': 'no-referrer'}


class PublicProfiles:
    def __init__(self, portal):
        self.portal = portal
        self.connect, self.lock = portal.connect, portal.lock
        self.assistant = InterviewAssistant(self.connect, self.lock)
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS cp_public_profiles (
                id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
                candidate_id TEXT NOT NULL REFERENCES cp_candidates(id),
                interview_id TEXT NOT NULL REFERENCES cp_interviews(id),
                room_fingerprint TEXT NOT NULL, issuer TEXT NOT NULL,
                created_at TEXT NOT NULL, revoked_at TEXT);
              CREATE INDEX IF NOT EXISTS cp_public_profile_owner
                ON cp_public_profiles(candidate_id, revoked_at);
            ''')

    @staticmethod
    def handles(path):
        return urlsplit(path).path in (PUBLIC, PUBLIC + '/materials', PUBLIC + '/resume',
                                      PUBLIC + '/assessment', PUBLIC + '/assistant', OWNER)

    @staticmethod
    def _fingerprint(room):
        return hashlib.sha256(json.dumps(room, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def issue(self, db, candidate_id, *, issuer, origin):
        """Caller must authenticate this owner or record an explicit operator grant."""
        require(isinstance(issuer, str) and 1 <= len(issuer) <= 160, 'Укажите владельца публикации.', 400)
        require(origin in self.portal.origins, 'Адрес публикации не разрешён.', 400)
        self.portal.person(db, candidate_id)
        meeting = self.portal.interview(db, candidate_id)
        require(meeting and meeting['status'] == 'completed', 'Сначала завершите интервью.', 409)
        token = secrets.token_urlsafe(32)
        share_id = 'profile_' + secrets.token_hex(12)
        self.revoke(db, candidate_id)
        db.execute('INSERT INTO cp_public_profiles VALUES (?,?,?,?,?,?,?,NULL)',
                   (share_id, hashlib.sha256(token.encode()).hexdigest(), candidate_id, meeting['id'],
                    self._fingerprint(meeting.get('room')), issuer, iso(now())))
        return {'id': share_id, 'url': origin + '/iframe.html?id=hr-vision-product--candidate&viewMode=story&cp=public#profile=' + token}

    @staticmethod
    def revoke(db, candidate_id):
        db.execute('UPDATE cp_public_profiles SET revoked_at=? WHERE candidate_id=? AND revoked_at IS NULL',
                   (iso(now()), candidate_id))

    def resolve(self, db, token):
        require(isinstance(token, str) and re.fullmatch(r'[A-Za-z0-9_-]{43}', token), 'Ссылка недействительна.', 404)
        row = db.execute('''SELECT id,candidate_id,interview_id,room_fingerprint FROM cp_public_profiles
          WHERE token_hash=? AND revoked_at IS NULL''', (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        require(row, 'Ссылка закрыта.', 410)
        meeting = self.portal.interview(db, row[1])
        require(meeting and meeting['id'] == row[2] and meeting['status'] == 'completed'
                and self._fingerprint(meeting.get('room')) == row[3], 'Ссылка закрыта.', 410)
        return {'id': row[0], 'candidateId': row[1], 'interviewId': row[2]}

    def profile(self, db, share):
        person = self.portal.person(db, share['candidateId'])
        resume = db.execute('SELECT filename,mime,length(content) FROM cp_resumes WHERE candidate_id=?',
                            (share['candidateId'],)).fetchone()
        # Deliberately no email, scheduling, applications, notes, account flags,
        # provider room IDs or mutable employer assessments in this projection.
        return {'id': share['id'], 'name': person['name'], 'role': person.get('desiredRole', ''),
                'resume': {'name': resume[0], 'mime': resume[1], 'size': resume[2]} if resume else None}

    def assessment(self, db, share):
        """Project the exact saved core result; never synthesize missing scores."""
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sl_core_results'").fetchone():
            return {'assessment': None}
        row = db.execute('''SELECT id,interview_id,source_revision,body,engine_candidate_id FROM sl_core_results
          WHERE candidate_id=? ORDER BY rowid DESC LIMIT 1''', (share['candidateId'],)).fetchone()
        if not row or row[1] != share['interviewId'] or row[2] != selection.person(db, share['candidateId'])['sourceRevision']:
            return {'assessment': None}
        doc = json.loads(row[3])
        selection._seal(doc, 'candidate_profile/1')
        require(doc['body']['candidate_id'] == row[4], 'Материалы аналитики изменились.', 409)
        labels = {'A01': 'Диалог и слушание', 'A03': 'Квалификация', 'A04': 'Понимание задачи',
                  'A06.1': 'Работа с возражениями', 'A07': 'Следующий шаг', 'A08': 'CRM и сопровождение',
                  'A10': 'Продукт и обещания', 'A11.1': 'Гибкость в разговоре', 'NEW-01': 'Передача лида'}
        reasons = {'task_not_given': 'На интервью не было задания, по которому можно оценить этот навык.',
                   'not_observed': 'В записи не найдено подтверждения этого навыка.',
                   'insufficient_evidence': 'В интервью недостаточно данных для оценки этого навыка.'}
        criteria = []
        for family in doc['body']['families']:
            for item in family['criteria']:
                state, cid = item['state'], item['criterion_id']
                if state == 'not_applicable':
                    continue
                reason = reasons.get(item.get('missing_reason'), 'В интервью недостаточно данных для оценки этого навыка.')
                criteria.append({'id': cid, 'label': labels.get(cid, cid), 'state': state,
                    'level': item['level'] if state == 'supported' else None, 'target': None,
                    'basis': item.get('basis') if item.get('basis') in ('observed', 'narrated', 'hypothetical', 'reflected') else None,
                    'summary': reason if state != 'supported' else 'Уровень из сохранённого разбора интервью.',
                    'missingReason': reason if state != 'supported' else None,
                    'stateLabel': 'Недостаточно данных' if state == 'unknown' else 'Противоречивые данные' if state == 'conflict' else 'Подтверждено в интервью',
                    'evidence': []})
        return {'assessment': {'revisionId': row[0], 'criteria': criteria, 'feedback': [],
                'provenance': {'method': 'saved-core-result', 'productionCore': True}}}

    def handle(self, method, raw_path, headers, body, ip):
        path = urlsplit(raw_path).path
        require(self.handles(path), 'Не найдено.', 404)
        require(not urlsplit(raw_path).query, 'Параметры ссылки передаются отдельно.', 400)
        if method == 'POST':
            require((headers.get('Origin') is None or headers.get('Origin') in self.portal.origins)
                    and headers.get('Sec-Fetch-Site') != 'cross-site', 'Другой источник запроса.', 403)
            require(headers.get('Content-Type', '').split(';', 1)[0].strip() == 'application/json', 'Нужен JSON.', 415)
            require(isinstance(body, dict), 'Некорректный запрос.', 400)
        if path == OWNER:
            require(method in ('GET', 'POST'), 'Метод не поддерживается.', 405)
            with self.lock, self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                cid = self.portal.session_candidate(db, headers)
                if method == 'GET':
                    row = db.execute('SELECT id,created_at FROM cp_public_profiles WHERE candidate_id=? AND revoked_at IS NULL', (cid,)).fetchone()
                    return {'active': bool(row), 'id': row[0] if row else None}, HEADERS
                require(body.get('action') in ('create', 'revoke'), 'Выберите действие.', 400)
                if body['action'] == 'revoke':
                    require(set(body) == {'action'}, 'Проверьте поля запроса.', 400)
                    self.revoke(db, cid)
                    return {'active': False}, HEADERS
                require(set(body) == {'action', 'consent'} and body['consent'] is True,
                        'Разрешите просмотр интервью и резюме по ссылке.', 400)
                origin = headers.get('Origin') or self.portal.origin
                return self.issue(db, cid, issuer='candidate:' + cid, origin=origin), HEADERS
        require(method == 'POST', 'Откройте персональную ссылку.', 405)
        allowed = {'token', 'action', 'query', 'requestId'} if path.endswith('/assistant') else {'token'}
        require(set(body) == allowed, 'Проверьте поля запроса.', 400)
        self.portal.rate_limit('public-profile-ip:' + ip, 120, 60)
        token = body['token']
        with self.connect() as db:
            share = self.resolve(db, token)
            if path == PUBLIC:
                return self.profile(db, share), HEADERS
            if path.endswith('/assessment'):
                return self.assessment(db, share), HEADERS
            if path.endswith('/resume'):
                return self.portal.download_resume(db, share['candidateId']), HEADERS
        # Slow provider calls run outside the shared DB lock. The capability is
        # rechecked by the catalogue and again after all upstream I/O.
        def check(db):
            require(self.resolve(db, token) == share, 'Ссылка закрыта.', 410)
        result = self.portal.materials(share['candidateId'], meeting_id=share['interviewId'], access_check=check)
        with self.connect() as db:
            check(db)
        if path.endswith('/assistant'):
            recordings, transcripts = result.get('recordings', []), result.get('transcripts', [])
            require(len(recordings) == 1 and len(transcripts) == 1, 'Расшифровка пока не сопоставлена с записью.', 422)
            transcript = transcripts[0].get('text', '')
            require(isinstance(transcript, str) and transcript.strip(), 'Расшифровка ещё не готова.', 422)
            self.portal.rate_limit('public-profile-ai:' + share['id'], 30, 86400)
            principal = {'workspaceId': 'public-profile:' + share['id'], 'accountId': 'public-profile:' + share['id']}
            source = json.dumps(['public', share['id'], share['interviewId'], recordings[0]['id'],
                                 hashlib.sha256(transcript.encode()).hexdigest()])
            return self.assistant.run(principal, source, transcript,
                {key: body[key] for key in ('action', 'query', 'requestId')}, check), HEADERS
        return {
            'recordings': [{key: row[key] for key in ('id', 'url', 'expires', 'duration') if key in row}
                           for row in result.get('recordings', [])],
            'transcripts': [{key: row[key] for key in ('id', 'assetId', 'text') if key in row}
                            for row in result.get('transcripts', [])],
            'videoStatus': result.get('video', {}).get('status', 'processing'),
            'transcriptStatus': result.get('transcription', {}).get('status', 'processing'),
        }, HEADERS
