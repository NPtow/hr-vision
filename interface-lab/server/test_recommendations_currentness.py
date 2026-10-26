"""Regression coverage for current matching inputs, using real HR SQLite."""
import unittest
from unittest.mock import patch

import recommendations_bridge as matching
import selection
from domain import Problem
import test_recommendations_bridge as fixtures


class CurrentnessTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.IntegrationTest()
        self.f.setUp()

    def tearDown(self):
        self.f.tearDown()

    def test_new_ready_candidate_invalidates_full_pool_without_assessment_run(self):
        self.f.start()
        with self.f.connect() as db:
            self.assertEqual(matching.automatic_candidate_ids(self.f.portal, db, self.f.vid), [self.f.cid])
            self.f.evidence(db, self.f.pending)
            self.assertEqual(matching.automatic_candidate_ids(self.f.portal, db, self.f.vid), sorted([self.f.cid, self.f.pending]))
            self.assertTrue(all(p['state'] == 'stale' for p in matching.projections(db, self.f.vid).values()))
        self.assertEqual(self.f.rb.read(self.f.vid)['state'], 'stale')

    def test_ready_material_revision_change_invalidates_without_local_interview_change(self):
        self.f.start()
        with self.f.connect() as db:
            before = selection.person(db, self.f.cid)['sourceRevision']
            db.execute('UPDATE sl_evidence SET corpus_revision=? WHERE candidate_id=?', ('b' * 64, self.f.cid))
            self.assertEqual(selection.person(db, self.f.cid)['sourceRevision'], before)
        self.assertEqual(self.f.rb.read(self.f.vid)['state'], 'stale')

    def test_verification_refresh_does_not_change_input_version(self):
        with self.f.connect() as db:
            before = matching.local_versions(db, self.f.vid, [self.f.cid])
            self.f.evidence(db, self.f.cid)
            after = matching.local_versions(db, self.f.vid, [self.f.cid])
        self.assertEqual(before, after)

    def test_revoked_part_invalidates_and_removes_candidate_from_ready_pool(self):
        self.f.start()
        with self.f.connect() as db:
            self.f.evidence(db, self.f.cid, parts=False)
            self.assertEqual(matching.automatic_candidate_ids(self.f.portal, db, self.f.vid), [])
        self.assertEqual(self.f.rb.read(self.f.vid)['state'], 'stale')

    def test_unknown_target_is_usable_without_rewriting_source_requirement(self):
        first = self.f.start()
        parameter = {'parameter_id': 'A01', 'status': 'needs_review', 'importance': 2, 'required_level_id': None}
        self.f.profile['parameters'] = [parameter]
        value = self.f.rb.start(self.f.vid, first['revision'], [self.f.cid])
        self.assertEqual(value['state'], 'ready')
        submitted = [body for path, body in self.f.posts if path == '/v1/recommendations'][-1]
        self.assertEqual(submitted['vacancy_profile']['parameters'], [parameter])
        self.assertEqual(submitted['policy']['version'], 'tanh-higher-v2')

    def test_unknown_importance_does_not_invent_weight(self):
        first = self.f.start()
        self.f.profile['parameters'] = [{'parameter_id': 'A01', 'status': 'needs_review',
            'importance': 'insufficient_data', 'required_level_id': None}]
        sent = len(self.f.posts)
        with self.assertRaises(Problem) as error:
            self.f.rb.start(self.f.vid, first['revision'], [self.f.cid])
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(len(self.f.posts), sent)

    def test_materials_changed_during_remote_calculation_cannot_publish_current_result(self):
        with self.f.connect() as db:
            revision = selection.vacancy(db, self.f.vid)['contentHash']
        self.f.vp.start(self.f.vid, revision)
        original = self.f.call

        def changed(method, path, body=None):
            result = original(method, path, body)
            if path == '/v1/recommendations':
                with self.f.connect() as db:
                    db.execute('UPDATE sl_evidence SET corpus_revision=? WHERE candidate_id=?', ('c' * 64, self.f.cid))
            return result

        with patch.object(self.f, 'call', side_effect=changed):
            with self.assertRaises(Problem) as error:
                self.f.rb.start(self.f.vid, revision, [self.f.cid])
        self.assertEqual(error.exception.status, 409)
        with self.f.connect() as db:
            self.assertIsNone(matching.latest(db, self.f.vid)['result'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cn_outbox').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_applications').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
