"""Readiness regression tests: real SQLite and canonical synthetic source DTOs.

No providers, models, ASR, capture or production databases participate.
"""
import json
import os
from copy import deepcopy
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import selection_status
import selection
from assessment_corpus import CandidateCorpusCollector, CollectionError
from domain import Problem
from selection_materials_lifecycle import Lifecycle, configured, refresh_candidate

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'services/assessment-core'))
sys.path.insert(0, str(ROOT / 'services/assessment-core/tests'))
from test_hr_corpus import conversation


class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class CanonicalSource:
    def __init__(self, lock):
        self.lock, self.http = lock, self
        self.calls, self.primaries, self.dossiers = [], {}, {}
        self.failure, self.before_read = None, None

    def add(self, cid, parts=1):
        owner, primary = conversation('interview', 'hr-' + cid, [
            [{'text': 'PRIVATE SYNTHETIC TEXT ' + str(i), 'start_ms': 0, 'end_ms': 20}]
            for i in range(parts)])
        self.dossiers[cid] = {'candidate_id': cid, 'interviews': [owner],
                              'resume': {'invalid': 'unreadable CV'},
                              'meetings': [{'invalid': 'unready employer meeting'}]}
        self.primaries[owner['id']] = primary

    def call(self, method, path):
        assert not self.lock._is_owned(), 'network read under shared HR lock'
        assert method == 'GET'
        self.calls.append((method, path))
        if self.failure:
            raise self.failure
        if self.before_read:
            self.before_read()
        return deepcopy(self.dossiers[path.split('/')[-2]])

    def primary_transcripts(self, kind, owner_id):
        assert not self.lock._is_owned(), 'network read under shared HR lock'
        assert kind == 'interview', 'readiness must not depend on employer meetings'
        self.calls.append(('GET-primary', owner_id))
        return deepcopy(self.primaries[owner_id])


class ReadinessLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'hr.sqlite3')
        self.lock = threading.RLock()
        self.denied = set()
        self.core = CanonicalSource(self.lock)
        self.cv = Mock(side_effect=AssertionError('CV reader must not participate'))
        self.collector = CandidateCorpusCollector(self.core, self.cv, authorize=self.authorize)
        with self.connect() as db:
            db.executescript('''CREATE TABLE cp_candidates (
                id TEXT PRIMARY KEY,profile TEXT,updated_at TEXT,email TEXT,searching INTEGER);
                CREATE TABLE cp_interviews(id TEXT PRIMARY KEY,candidate_id TEXT,status TEXT,completed_at TEXT,room TEXT);
                CREATE TABLE cp_resumes(candidate_id TEXT,uploaded_at TEXT);
                CREATE TABLE cp_intake(candidate_id TEXT,contact_email TEXT);''')
        self.cid = 'new-unlisted-93'
        self.add(self.cid)

    def connect(self):
        return sqlite3.connect(self.path, factory=Connection, timeout=2)

    def add(self, cid, *, interview=True, parts=1):
        with self.connect() as db:
            db.execute('INSERT INTO cp_candidates VALUES (?,?,?,?,?)', (cid, '{}', '2026-10-26', None, 1))
            if interview:
                db.execute('INSERT INTO cp_interviews VALUES (?,?,?,?,?)', ('hr-'+cid, cid, 'completed', '2026-10-26', None))
                self.core.add(cid, parts)

    def authorize(self, cid):
        if cid in self.denied:
            raise Problem('denied', 403)
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM cp_candidates WHERE id=?', (cid,)).fetchone():
                raise Problem('missing', 404)

    def read(self, cid=None):
        with self.connect() as db:
            return selection_status.evidence(db, selection.person(db, cid or self.cid))

    def refresh(self):
        return refresh_candidate(self.connect, self.lock, self.authorize, self.cid, collector=self.collector)

    def lifecycle(self, **kwargs):
        return Lifecycle(self.connect, self.lock, self.authorize, self.collector, **kwargs)

    def test_hr_ready_is_independent_of_failed_cv_meetings_and_model_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            result = self.refresh()
        self.assertTrue(result['allPartsReady'])
        self.assertEqual(result['recordingCount'], 1)
        self.assertEqual(result['transcriptCount'], 1)
        self.assertEqual(result['contractVersion'], 'hr-interview-readiness-v1')
        self.assertEqual(len(result['materialsRevision']), 64)
        self.assertTrue(result['checkedAt'] > 0)
        self.cv.assert_not_called()
        self.assertNotIn('PRIVATE SYNTHETIC TEXT', json.dumps(result))
        with self.connect() as db:
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertFalse({'ac_bindings', 'ac_auto_work', 'as_jobs'} & names)
        self.assertEqual(self.read(), result)

    def test_bounded_scan_resumes_cursor_and_discovers_new_unlisted_candidate(self):
        self.add('aaa-new')
        self.add('zzz-denied')
        self.add('zzzz-no-interview', interview=False)
        self.denied.add('zzz-denied')
        first = self.lifecycle(batch_size=1)
        self.assertEqual(first.scan(), 1)
        self.assertEqual(first.status()['cursor'], 'aaa-new')
        second = self.lifecycle(batch_size=1)
        self.assertEqual(second.scan(), 1)
        self.assertTrue(self.read()['allPartsReady'])
        self.assertEqual(second.scan(), 1)
        self.assertEqual(second.scan(), 0)
        self.assertEqual(second.status()['cursor'], '')
        self.assertFalse(any('zzz-denied' in path for _, path in self.core.calls))
        self.assertEqual(second.status()['candidates'], {'ready': 2, 'unavailable': 1})
        # Created after the first sweep, without updating an enrollment list.
        self.add('a-newer-candidate', parts=3)
        self.assertEqual(second.scan(), 1)
        self.assertTrue(self.read('a-newer-candidate')['allPartsReady'])
        self.assertEqual(self.read('a-newer-candidate')['transcriptCount'], 3)

    def test_new_part_revocation_and_new_primary_each_change_saved_readiness(self):
        initial = self.refresh()
        self.core.add(self.cid, parts=2)
        primary = self.core.primaries['hr-' + self.cid]
        owner = self.core.dossiers[self.cid]['interviews'][0]
        primary['status'] = 'processing'
        primary['selections'][1].update(status='processing', transcript_id=None)
        primary['transcripts'].pop()
        owner['materials']['transcript_selections'] = deepcopy(primary['selections'])
        incomplete = self.refresh()
        self.assertFalse(incomplete['allPartsReady'])
        self.assertEqual((incomplete['recordingCount'], incomplete['transcriptCount']), (2, 1))
        self.assertNotEqual(incomplete['materialsRevision'], initial['materialsRevision'])
        self.core.add(self.cid, parts=2)
        restored = self.refresh()
        self.assertTrue(restored['allPartsReady'])
        owner = self.core.dossiers[self.cid]['interviews'][0]
        owner['materials']['sources'][0]['revoked'] = True
        self.assertFalse(self.refresh()['allPartsReady'])
        self.core.add(self.cid, parts=2)
        primary = self.core.primaries['hr-' + self.cid]
        owner = self.core.dossiers[self.cid]['interviews'][0]
        primary['selections'][0]['revision'] += 1
        owner['materials']['transcript_selections'] = deepcopy(primary['selections'])
        changed = self.refresh()
        self.assertTrue(changed['allPartsReady'])
        self.assertNotEqual(changed['materialsRevision'], restored['materialsRevision'])

    def test_outage_clears_ready_immediately_and_backoff_survives_restart(self):
        self.refresh()
        self.core.failure = OSError('PRIVATE PROVIDER TOKEN NEVER PERSIST')
        lifecycle = self.lifecycle()
        lifecycle.scan()
        self.assertEqual(self.read()['state'], 'unavailable')
        self.assertIsNone(self.read()['recordingCount'])
        self.assertFalse(self.read()['allPartsReady'])
        count = len(self.core.calls)
        lifecycle = self.lifecycle()
        lifecycle.scan()  # wrap
        lifecycle.scan()  # backoff prevents another source read
        self.assertEqual(len(self.core.calls), count)
        self.assertNotIn('PRIVATE PROVIDER TOKEN', json.dumps(lifecycle.status()))
        with self.connect() as db:
            saved = db.execute('SELECT body FROM sl_evidence').fetchone()[0]
            self.assertNotIn('PRIVATE PROVIDER TOKEN', saved)
            db.execute('UPDATE sl_materials_checks SET next_check=0')
        self.core.failure = None
        lifecycle.scan()
        lifecycle.scan()
        self.assertTrue(self.read()['allPartsReady'])

    def test_local_change_during_network_is_not_stamped_with_newer_revision(self):
        self.refresh()
        def changed():
            self.core.before_read = None
            with self.connect() as db:
                db.execute("UPDATE cp_interviews SET status='live'")
        self.core.before_read = changed
        result = self.refresh()
        self.assertFalse(result['allPartsReady'])
        self.assertEqual(result['errorCode'], 'materials_changed')

    def test_selected_text_integrity_and_recording_state_block_false_ready(self):
        self.refresh()
        primary = self.core.primaries['hr-' + self.cid]
        primary['transcripts'][0]['segments'][0]['text'] += 'tampered'
        self.assertEqual(self.refresh()['state'], 'unavailable')
        self.core.add(self.cid)
        owner = self.core.dossiers[self.cid]['interviews'][0]
        owner['materials']['assets'][0]['state'] = 'failed'
        self.assertFalse(self.refresh()['allPartsReady'])
        self.core.add(self.cid)
        owner = self.core.dossiers[self.cid]['interviews'][0]
        owner['materials']['channels'][0]['video_status'] = 'error'
        self.assertFalse(self.refresh()['allPartsReady'])

    def test_removed_interview_is_observed_and_does_not_remain_ready(self):
        self.refresh()
        with self.connect() as db:
            db.execute('DELETE FROM cp_interviews')
        self.core.dossiers[self.cid]['interviews'] = []
        self.assertEqual(self.lifecycle().scan(), 1)
        self.assertEqual(self.read()['state'], 'missing')
        self.assertFalse(self.read()['allPartsReady'])

    def test_primary_revision_race_between_passes_invalidates_previous_ready(self):
        self.refresh()
        reads = 0
        def changed_on_second_pass():
            nonlocal reads
            reads += 1
            if reads == 2:
                primary = self.core.primaries['hr-' + self.cid]
                primary['selections'][0]['revision'] += 1
                owner = self.core.dossiers[self.cid]['interviews'][0]
                owner['materials']['transcript_selections'] = deepcopy(primary['selections'])
        self.core.before_read = changed_on_second_pass
        self.assertEqual(self.refresh()['state'], 'unavailable')
        self.assertFalse(self.read()['allPartsReady'])

    def test_runtime_has_independent_owner_and_releases_it_on_stop(self):
        settings = {'HR_CORE_CATALOG_ENABLED': '1'}
        with patch.dict(os.environ, settings, clear=True), patch(
                'selection_materials_lifecycle.configured_collector', return_value=self.collector):
            runtime = configured(self.connect, self.lock, self.authorize)
            try:
                with self.assertRaises(Problem):
                    configured(self.connect, self.lock, self.authorize)
            finally:
                self.assertTrue(runtime.stop())
            runtime = configured(self.connect, self.lock, self.authorize)
            self.assertTrue(runtime.stop())

    def test_runtime_drains_next_batch_before_waiting_whole_scan_interval(self):
        self.add('aaa-first')
        settings = {'HR_CORE_CATALOG_ENABLED': '1', 'HR_SELECTION_MATERIALS_BATCH_SIZE': '1',
                    'HR_SELECTION_MATERIALS_INTERVAL_SECONDS': '30'}
        with patch.dict(os.environ, settings, clear=True), patch(
                'selection_materials_lifecycle.configured_collector', return_value=self.collector):
            runtime = configured(self.connect, self.lock, self.authorize)
            try:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline and not self.read()['allPartsReady']:
                    time.sleep(.01)
                self.assertTrue(self.read()['allPartsReady'])
                self.assertTrue(self.read('aaa-first')['allPartsReady'])
            finally:
                self.assertTrue(runtime.stop())


class EvidenceExpiryTests(unittest.TestCase):
    def test_old_canonical_ready_cannot_remain_ready_forever(self):
        with sqlite3.connect(':memory:', factory=Connection) as db:
            selection_status.init_evidence(db)
            ready = {'state': 'ready', 'recordingCount': 1, 'transcriptCount': 1,
                     'allPartsReady': True}
            db.execute('INSERT INTO sl_evidence VALUES (?,?,?,?,?)',
                       ('fresh-id', 'portal-v1', 'core-v1', json.dumps(ready), time.time() - 3600))
            result = selection_status.evidence(db, {'id': 'fresh-id', 'sourceRevision': 'portal-v1'})
        self.assertFalse(result['allPartsReady'])
        self.assertEqual(result['state'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
