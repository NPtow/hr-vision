"""Manager-authorized adapter to the independent full-corpus assessment service.

Reuse the canonical corpus collector and ServiceHTTP. GET only reads; POST is
the explicit evaluation command. No vacancy scoring, invitations or ASR here.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import time

from assessment_corpus import CandidateCorpusCollector, CollectionError, SQLiteResumeReader
from core_source_client import CoreClient, ServiceHTTP
from domain import Problem, require


STATES = {'queued', 'sent', 'response_saved', 'completed', 'failed', 'outcome_unknown'}
LABELS = {'queued': 'queued', 'sent': 'processing', 'response_saved': 'processing',
          'completed': 'ready', 'failed': 'error', 'outcome_unknown': 'outcome_unknown',
          'dispatching': 'queued', 'stale': 'stale'}


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def enabled():
    return os.environ.get('HR_ASSESSMENT_CORE_ENABLED') == '1'


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS ac_bindings (
      id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES cp_candidates(id),
      source_revision TEXT NOT NULL, rubric_hash TEXT NOT NULL, model TEXT NOT NULL,
      manifest TEXT NOT NULL, run_id TEXT, status TEXT NOT NULL, receipt TEXT,
      created REAL NOT NULL, updated REAL NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS ac_candidate ON ac_bindings(candidate_id,created DESC)')


def latest(db, cid):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ac_bindings'").fetchone():
        return None
    cursor = db.execute('SELECT * FROM ac_bindings WHERE candidate_id=? ORDER BY created DESC,id DESC LIMIT 1', (cid,))
    row = cursor.fetchone()
    return dict(zip([d[0] for d in cursor.description], row)) if row else None


def overview(db, cid):
    row = latest(db, cid)
    return {'state': LABELS.get(row['status'], 'unavailable') if row else 'not_started',
            'origin': 'assessment-core', 'revisionId': row['run_id'] if row else None}


def configured(connect, lock, authorize):
    require(enabled(), 'Новое ядро ещё не подключено.', 503)
    try:
        with connect() as db:
            database = db.execute('PRAGMA database_list').fetchone()[2]
        rubric = json.loads(Path(os.environ['HR_ASSESSMENT_CORE_RUBRIC']).read_text())
        return Bridge(connect, lock, authorize,
            CandidateCorpusCollector(CoreClient(ServiceHTTP(os.environ['HR_MATERIALS_CORE_URL'],
                os.environ['HR_MATERIALS_CORE_KEY'], timeout=10)), SQLiteResumeReader(database), authorize=authorize),
            ServiceHTTP(os.environ.get('HR_ASSESSMENT_CORE_URL', 'http://127.0.0.1:8097'),
                        os.environ['HR_ASSESSMENT_CORE_TOKEN'], timeout=15), rubric,
            os.environ.get('HR_ASSESSMENT_CORE_MODEL', 'jev-1.14.0'))
    except (KeyError, OSError, ValueError):
        raise Problem('Настройка нового ядра недоступна.', 503) from None


class Bridge:
    def __init__(self, connect, lock, authorize, collector, service, rubric, model):
        self.connect, self.lock, self.authorize = connect, lock, authorize
        self.collector, self.service, self.rubric, self.model = collector, service, rubric, model
        require(isinstance(rubric, dict) and set(rubric) == {'rubric_id', 'version', 'parameters'}
                and isinstance(rubric['parameters'], list) and rubric['parameters']
                and re.fullmatch(r'jev-\d+\.\d+\.\d+', model), 'Некорректная версия шкал.', 503)
        self.rubric_hash = digest(rubric)

    def options(self):
        return {'canRun': True, 'rubric': {'id': self.rubric['rubric_id'], 'version': self.rubric['version'],
                'parameterCount': len(self.rubric['parameters'])}}

    def validate_receipt(self, receipt, cid, run_id=None):
        require(isinstance(receipt, dict) and receipt.get('status') in STATES
                and isinstance(receipt.get('run_id'), str) and re.fullmatch('[a-f0-9]{64}', receipt['run_id'])
                and (run_id is None or run_id == receipt['run_id'])
                and receipt.get('model') == self.model and receipt.get('rubric_version') == self.rubric['version']
                and isinstance(receipt.get('corpus_revision'), str)
                and re.fullmatch('[a-f0-9]{64}', receipt['corpus_revision']),
                'Не удалось подтвердить ответ нового ядра.', 502)
        if receipt['status'] == 'completed':
            result = receipt.get('assessment')
            require(isinstance(result, dict) and result.get('candidate_id') == cid
                    and result.get('run_id') == receipt['run_id']
                    and result.get('contract_version') == 'full-corpus-level-choice-v1'
                    and result.get('model') == self.model and result.get('rubric_sha256') == self.rubric_hash
                    and result.get('rubric_id') == self.rubric['rubric_id']
                    and result.get('rubric_version') == self.rubric['version']
                    and result.get('corpus_sha256') == receipt['corpus_revision']
                    and [p.get('parameter_id') for p in result.get('parameters', [])]
                        == [p['parameter_id'] for p in self.rubric['parameters']],
                    'Результат не соответствует кандидату или версии шкал.', 502)
        else:
            require(receipt.get('assessment') is None, 'Незавершённый результат ядра.', 502)
        return receipt

    def start(self, cid):
        self.authorize(cid)
        try:
            snapshot = self.collector.collect(cid)
            require(snapshot['status'] == 'ready',
                    'Не все материалы готовы: ' + ', '.join(i['code'] for i in snapshot['manifest']['issues']), 409)
            snapshot = self.collector.revalidate(snapshot)
        except CollectionError:
            raise Problem('Материалы изменились или недоступны. Обновите карточку.', 409) from None
        identity = digest([cid, snapshot['revision'], self.rubric_hash, self.model])
        with self.lock, self.connect() as db:
            self.authorize(cid)
            init(db)
            db.execute('''INSERT OR IGNORE INTO ac_bindings
              (id,candidate_id,source_revision,rubric_hash,model,manifest,status,created,updated)
              VALUES (?,?,?,?,?,?,'dispatching',?,?)''',
              (identity, cid, snapshot['revision'], self.rubric_hash, self.model,
               dump(snapshot['manifest']), time.time(), time.time()))
            old = db.execute('SELECT run_id FROM ac_bindings WHERE id=?', (identity,)).fetchone()[0]
        # This POST is safe to repeat after an uncertain HTTP result: the core
        # derives the same run ID from the exact full input and never re-bills it.
        receipt = self.service.call('GET', '/v1/assessments/' + old) if old else self.service.call(
            'POST', '/v1/assessments', {'corpus': snapshot['corpus'], 'rubric': self.rubric})
        self.validate_receipt(receipt, cid, old)
        self._save(identity, cid, receipt)
        return self.read(cid)

    def _save(self, identity, cid, receipt):
        with self.lock, self.connect() as db:
            self.authorize(cid)
            db.execute('UPDATE ac_bindings SET run_id=?,status=?,receipt=?,updated=? WHERE id=?',
                       (receipt['run_id'], receipt['status'], dump(receipt), time.time(), identity))

    def read(self, cid):
        self.authorize(cid)
        with self.connect() as db:
            row = latest(db, cid)
        base = {'state': 'not_started', 'result': None, 'match': {'state': 'not_compared'}, **self.options()}
        if row is None:
            return base
        if row['rubric_hash'] != self.rubric_hash or row['model'] != self.model:
            return {**base, 'state': 'stale'}
        try:
            current = self.collector.revalidate({'candidate_id': cid, 'revision': row['source_revision']})
        except CollectionError:
            with self.lock, self.connect() as db:
                self.authorize(cid)
                db.execute("UPDATE ac_bindings SET status='stale' WHERE id=?", (row['id'],))
            return {**base, 'state': 'stale'}
        if not row['run_id']:
            # GET never retries an unacknowledged POST. An explicit press of the
            # start button can recover the same idempotent input.
            return {**base, 'state': 'dispatch_unknown'}
        receipt = self.validate_receipt(self.service.call('GET', '/v1/assessments/' + row['run_id']), cid, row['run_id'])
        self.collector.revalidate(current)
        self._save(row['id'], cid, receipt)
        return {**base, 'state': LABELS[receipt['status']], 'coreRun': receipt,
                'canRun': receipt['status'] == 'completed',
                'corpus': {'sources': current['manifest']['source_count'], 'units': current['manifest']['unit_count'],
                           'characters': current['manifest']['text_characters']}}
