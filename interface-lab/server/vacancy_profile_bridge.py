"""Authorized immutable vacancy profiles, owned by the vacancy service API."""
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import quote
from domain import Problem, require
from core_source_client import ServiceHTTP
from assessment_core_bridge import digest, dump, STATES, LABELS


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS vp_bindings (
        id TEXT PRIMARY KEY,vacancy_id TEXT NOT NULL,source_revision TEXT NOT NULL,rubric_hash TEXT NOT NULL,
        model TEXT NOT NULL,snapshot TEXT NOT NULL,run_id TEXT,status TEXT NOT NULL,receipt TEXT,
        profile_id TEXT,profile TEXT,created REAL NOT NULL,updated REAL NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS vp_vacancy ON vp_bindings(vacancy_id,created DESC)')


def latest(db, vid):
    cursor = db.execute('SELECT * FROM vp_bindings WHERE vacancy_id=? ORDER BY created DESC,rowid DESC LIMIT 1', (vid,))
    row = cursor.fetchone()
    return dict(zip([d[0] for d in cursor.description], row)) if row else None


def configured(connect, lock, authorize):
    import assessment_core_bridge
    require(assessment_core_bridge.enabled(), 'Новое ядро ещё не подключено.', 503)
    try:
        rubric = json.loads(Path(os.environ['HR_ASSESSMENT_CORE_RUBRIC']).read_text())
        return Bridge(connect, lock, authorize, ServiceHTTP(os.environ.get('HR_VACANCY_PROFILE_URL', 'http://127.0.0.1:8108'),
            os.environ['HR_VACANCY_PROFILE_TOKEN'], timeout=15), rubric,
            os.environ.get('HR_ASSESSMENT_CORE_MODEL', 'jev-1.14.0'))
    except (KeyError, OSError, ValueError):
        raise Problem('Настройка профиля вакансии недоступна.', 503) from None


class Bridge:
    def __init__(self, connect, lock, authorize, service, rubric, model):
        self.connect, self.lock, self.authorize, self.service = connect, lock, authorize, service
        self.rubric, self.model, self.rubric_hash = rubric, model, digest(rubric)

    def snapshot(self, vid):
        import selection
        self.authorize(vid)
        with self.connect() as db:
            value = selection.vacancy(db, vid)
            data = json.loads(db.execute('SELECT body FROM cp_vacancies WHERE id=?', (vid,)).fetchone()[0])
        return {'vacancy_id': vid, 'revision': value['contentHash'], 'data': data}

    def validate_profile(self, profile, vid, row):
        require(isinstance(profile, dict) and profile.get('contract_version') == 'vacancy-level-importance-v1'
            and profile.get('vacancy_id') == vid and profile.get('source_revision') == row['source_revision']
            and profile.get('run_id') == row['run_id'] and profile.get('vacancy_sha256') == digest(json.loads(row['snapshot'])['data'])
            and profile.get('importance_version') == 'importance-0-3-v1' and profile.get('rubric_sha256') == self.rubric_hash
            and profile.get('rubric_id') == self.rubric['rubric_id'] and profile.get('rubric_version') == self.rubric['version']
            and profile.get('model') == self.model and isinstance(profile.get('profile_id'), str)
            and re.fullmatch('[a-f0-9]{64}', profile['profile_id'])
            and [p.get('parameter_id') for p in profile.get('parameters', [])] == [p['parameter_id'] for p in self.rubric['parameters']],
            'Профиль относится к другой вакансии или шкале.', 502)
        return profile

    def validate(self, receipt, vid, row, run_id=None):
        require(isinstance(receipt, dict) and receipt.get('status') in STATES
            and isinstance(receipt.get('run_id'), str) and re.fullmatch('[a-f0-9]{64}', receipt['run_id'])
            and (run_id is None or receipt['run_id'] == run_id) and receipt.get('vacancy_revision') == row['source_revision']
            and receipt.get('rubric_version') == self.rubric['version'] and receipt.get('model') == self.model,
            'Не удалось подтвердить профиль вакансии.', 502)
        if receipt['status'] == 'completed':
            self.validate_profile(receipt.get('profile'), vid, {**row, 'run_id': receipt['run_id']})
        else:
            require(receipt.get('profile') is None, 'Незавершённый профиль вакансии.', 502)
        return receipt

    def start(self, vid, revision):
        snapshot = self.snapshot(vid)
        require(snapshot['revision'] == revision, 'Вакансия изменилась.', 409)
        identity = digest([snapshot, self.rubric_hash, self.model])
        with self.lock, self.connect() as db:
            self.authorize(vid)
            init(db)
            db.execute('''INSERT OR IGNORE INTO vp_bindings
                (id,vacancy_id,source_revision,rubric_hash,model,snapshot,status,created,updated)
                VALUES (?,?,?,?,?,?,'dispatching',?,?)''',
                (identity, vid, revision, self.rubric_hash, self.model, dump(snapshot), time.time(), time.time()))
            db.execute('UPDATE vp_bindings SET created=? WHERE id=?', (time.time(), identity))
            cursor = db.execute('SELECT * FROM vp_bindings WHERE id=?', (identity,))
            row = dict(zip([d[0] for d in cursor.description], cursor.fetchone()))
        require(self.snapshot(vid) == snapshot, 'Вакансия изменилась.', 409)
        receipt = self.service.call('GET', '/v1/vacancy-profiles/' + row['run_id']) if row['run_id'] else self.service.call(
            'POST', '/v1/vacancy-profiles', {'vacancy': snapshot, 'rubric': self.rubric})
        self.validate(receipt, vid, row, row['run_id'])
        self.save(row, receipt)
        return self.read(vid)

    def save(self, row, receipt):
        with self.lock, self.connect() as db:
            self.authorize(row['vacancy_id'])
            profile = receipt.get('profile')
            db.execute('UPDATE vp_bindings SET run_id=?,status=?,receipt=?,profile_id=COALESCE(profile_id,?),profile=COALESCE(profile,?),updated=? WHERE id=?',
                (receipt['run_id'], receipt['status'], dump(receipt), profile['profile_id'] if profile else None,
                 dump(profile) if profile else None, time.time(), row['id']))

    def read(self, vid):
        snapshot = self.snapshot(vid)
        base = {'vacancyId': vid, 'revision': snapshot['revision'], 'state': 'not_started', 'profile': None,
                'runId': None, 'staleReasons': [], 'canRun': True}
        with self.connect() as db:
            row = latest(db, vid)
        if not row:
            return base
        reasons = [key for key, bad in [('vacancy', row['source_revision'] != snapshot['revision']),
            ('rubric', row['rubric_hash'] != self.rubric_hash), ('model', row['model'] != self.model)] if bad]
        if reasons:
            return {**base, 'state': 'stale', 'staleReasons': reasons, 'runId': row['run_id']}
        if not row['run_id']:
            return {**base, 'state': 'dispatch_unknown'}
        receipt = self.validate(self.service.call('GET', '/v1/vacancy-profiles/' + row['run_id']), vid, row, row['run_id'])
        profile = receipt.get('profile')
        if row['profile_id'] and profile and row['profile_id'] != profile['profile_id']:
            profile = self.validate_profile(self.service.call('GET', '/v1/vacancy-profiles/profiles/' + quote(row['profile_id'], safe='')).get('profile'), vid, row)
            require(profile['profile_id'] == row['profile_id'], 'Другая ревизия требований.', 502)
        require(self.snapshot(vid)['revision'] == snapshot['revision'], 'Вакансия изменилась.', 409)
        self.save(row, receipt)
        state = 'needs_review' if profile and any(p.get('status') == 'needs_review' for p in profile['parameters']) else LABELS[receipt['status']]
        return {**base, 'state': state, 'profile': profile, 'runId': receipt['run_id'],
                'error': receipt.get('error'), 'canRun': receipt['status'] in ('completed', 'failed', 'outcome_unknown')}

    def revise(self, vid, body, author='manager'):
        require(isinstance(body, dict) and set(body) == {'revision', 'profileId', 'changes'}, 'Проверьте уточнение требований.', 400)
        current = self.read(vid)
        require(current['revision'] == body['revision'] and current['profile'] and current['profile']['profile_id'] == body['profileId']
                and current['state'] in ('ready', 'needs_review'), 'Требования изменились.', 409)
        profile = self.service.call('POST', '/v1/vacancy-profiles/' + current['runId'] + '/revisions',
            {'expected_parent_id': body['profileId'], 'author': author, 'changes': body['changes']}).get('profile')
        with self.connect() as db:
            row = latest(db, vid)
        self.validate_profile(profile, vid, row)
        require(self.snapshot(vid)['revision'] == body['revision'], 'Вакансия изменилась.', 409)
        with self.lock, self.connect() as db:
            self.authorize(vid)
            import selection
            require(selection.vacancy(db, vid)['contentHash'] == body['revision'], 'Вакансия изменилась.', 409)
            require(latest(db, vid)['profile_id'] == body['profileId'], 'Требования изменились.', 409)
            db.execute('UPDATE vp_bindings SET profile_id=?,profile=?,updated=? WHERE id=?',
                       (profile['profile_id'], dump(profile), time.time(), row['id']))
        return self.read(vid)
