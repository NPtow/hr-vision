"""Real collector/core contracts with fake HTTP; no paid calls or HR deliveries."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'services/assessment-core'))
from assessment_core.engine import prepare, interpret
from assessment_core_bridge import Bridge, init, latest
from assessment_corpus import CollectionError
from domain import Problem


RUBRIC = {'rubric_id': 'test', 'version': '1', 'parameters': [
    {'parameter_id': 'skill', 'title': 'Skill', 'definition': 'Test rubric', 'levels': [
        {'level_id': 'L1', 'label': 'First', 'description': 'First behaviour'},
        {'level_id': 'L2', 'label': 'Second', 'description': 'Second behaviour'}]}]}
CORPUS = {'candidate_id': 'c1', 'sources': [
    {'source_id': name, 'kind': 'cv' if name == 'cv' else 'interview', 'version': '1',
     'units': [{'unit_id': 'u1', 'text': text, 'role': 'unknown'}]}
    for name, text in [('cv', 'Full CV  \n'), ('interview1', 'First interview'), ('interview2', 'Second interview')]]}


class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Collector:
    def __init__(self, authorize):
        self.authorize, self.revision = authorize, 'source-v1'
        self.status = 'ready'

    def collect(self, cid):
        self.authorize(cid)
        return {'candidate_id': cid, 'status': self.status, 'revision': self.revision,
                'corpus': deepcopy(CORPUS), 'manifest': {'issues': [{'code': 'pending'}] if self.status != 'ready' else [],
                'source_count': 3, 'unit_count': 3, 'text_characters': 50}}

    def revalidate(self, old):
        result = self.collect(old['candidate_id'])
        if result['revision'] != old['revision'] or result['status'] != 'ready':
            raise CollectionError('source_changed')
        return result


class Service:
    def __init__(self):
        self.calls, self.values = [], {}

    def call(self, method, path, body=None):
        self.calls.append((method, path, deepcopy(body)))
        if method == 'POST':
            plan = prepare(body['corpus'], body['rubric'])
            response = {'model': 'jev-1.14.0', 'usage': {'input_tokens': 100, 'output_tokens': 10},
                        'answers': {'skill': {'type': 'choice', 'choice': 'insufficient_data', 'confidence': 1,
                        'probabilities': {'L1': 0, 'L2': 0, 'insufficient_data': 1}}}}
            value = {'run_id': plan['run_id'], 'status': 'completed', 'assessment': interpret(plan, response),
                     'error': None, 'corpus_revision': plan['corpus']['sha256'],
                     'rubric_version': '1', 'model': 'jev-1.14.0'}
            self.values[plan['run_id']] = value
            return deepcopy(value)
        return deepcopy(self.values[path.rsplit('/', 1)[1]])


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'hr.sqlite3')
        self.allowed = True
        self.collector, self.service = Collector(self.authorize), Service()
        self.bridge = Bridge(self.connect, threading.RLock(), self.authorize, self.collector,
                             self.service, deepcopy(RUBRIC), 'jev-1.14.0')
        with self.connect() as db:
            db.execute('CREATE TABLE cp_candidates (id TEXT PRIMARY KEY)')
            db.execute("INSERT INTO cp_candidates VALUES ('c1')")
            init(db)

    def connect(self):
        return sqlite3.connect(self.path, factory=Connection)

    def authorize(self, cid):
        if not self.allowed or cid != 'c1':
            raise Problem('denied', 403)

    def test_get_is_read_only_and_never_submits(self):
        value = self.bridge.read('c1')
        self.assertEqual(value['state'], 'not_started')
        self.assertTrue(value['canRun'])
        self.assertEqual(self.service.calls, [])

    def test_entire_cv_and_every_interview_reach_core_and_same_input_is_reused(self):
        result = self.bridge.start('c1')
        self.assertEqual(result['coreRun']['assessment']['candidate_id'], 'c1')
        self.assertEqual(result['coreRun']['assessment']['parameters'][0]['status'], 'insufficient_data')
        self.assertEqual(self.service.calls[0][2]['corpus'], CORPUS)
        self.assertEqual(result['corpus']['sources'], 3)
        again = self.bridge.start('c1')
        self.assertEqual(again['coreRun']['run_id'], result['coreRun']['run_id'])
        self.assertEqual(sum(method == 'POST' for method, _, _ in self.service.calls), 1)

    def test_access_is_checked_before_collection_and_result_publication(self):
        self.allowed = False
        for action in (self.bridge.start, self.bridge.read):
            with self.assertRaises(Problem) as error:
                action('c1')
            self.assertEqual(error.exception.status, 403)
        self.assertFalse(self.service.calls)
        self.allowed = True
        self.bridge.start('c1')
        self.allowed = False
        with self.assertRaises(Problem):
            self.bridge.read('c1')

    def test_incomplete_corpus_cannot_be_submitted(self):
        self.collector.status = 'blocked'
        with self.assertRaises(Problem) as error:
            self.bridge.start('c1')
        self.assertEqual(error.exception.status, 409)
        self.assertFalse(self.service.calls)

    def test_source_change_hides_saved_result_without_rescoring(self):
        self.bridge.start('c1')
        self.collector.revision = 'source-v2'
        result = self.bridge.read('c1')
        self.assertEqual(result['state'], 'stale')
        self.assertNotIn('coreRun', result)
        self.assertEqual(sum(method == 'POST' for method, _, _ in self.service.calls), 1)

    def test_rubric_change_cannot_show_previous_result(self):
        self.bridge.start('c1')
        self.bridge.rubric_hash = 'changed'
        self.assertEqual(self.bridge.read('c1')['state'], 'stale')

    def test_wrong_candidate_or_rubric_is_rejected(self):
        result = self.bridge.start('c1')
        for field, value in [('candidate_id', 'c2'), ('rubric_sha256', 'bad'), ('run_id', 'b'*64)]:
            run = deepcopy(result['coreRun'])
            run['assessment'][field] = value
            with self.subTest(field=field), self.assertRaises(Problem):
                self.bridge.validate_receipt(run, 'c1', run['run_id'])

    def test_unacknowledged_dispatch_is_not_replayed_by_read(self):
        self.bridge.start('c1')
        with self.connect() as db:
            db.execute("UPDATE ac_bindings SET run_id=NULL,status='dispatching',receipt=NULL")
        count = len(self.service.calls)
        result = self.bridge.read('c1')
        self.assertEqual(result['state'], 'dispatch_unknown')
        self.assertTrue(result['canRun'])
        self.assertEqual(len(self.service.calls), count)


if __name__ == '__main__':
    unittest.main()
