"""Real SQLite tests for vacancy ownership, atomic batches and review persistence."""
import concurrent.futures
import datetime as dt
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from candidate_portal import CandidatePortal, ClosingConnection, profile_value
from domain import Problem, iso, now
import employer_store as store


class NoMedia:
    configured = False


class Outbox:
    @staticmethod
    def init(db):
        db.execute('CREATE TABLE IF NOT EXISTS test_events (cid TEXT,key TEXT UNIQUE,type TEXT)')

    @staticmethod
    def enqueue(db, cid, key, event_type):
        db.execute('INSERT OR IGNORE INTO test_events VALUES (?,?,?)', (cid, key, event_type))


class EmployerStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='employer-store-')
        self.path = Path(self.tmp.name) / 'state.sqlite3'
        self.portal = CandidatePortal(self.connect, threading.RLock(), NoMedia(),
                                      'https://example.test', Outbox)
        self.counter = 0
        with self.connect() as db:
            self.portal.init(db)
            db.execute('CREATE TABLE em_workspaces(id TEXT PRIMARY KEY,name TEXT,kind TEXT,created_at TEXT)')
            for wid in ('a', 'b', store.TEAM_WORKSPACE):
                db.execute('INSERT INTO em_workspaces VALUES (?,?,?,?)',
                           (wid, 'Компания ' + wid, 'employer', iso(now())))
            store.init(db)
        with self.connect() as db:
            self.vacancy = store.create_vacancy(db, 'a', {'title': 'Менеджер', 'company': 'Компания',
                'city': 'Москва', 'remote': False, 'description': 'Работа с клиентами'})

    def tearDown(self):
        self.tmp.cleanup()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=10000')
        return db

    def person(self, *, consent=True, completed=True, auto=False):
        self.counter += 1
        cid = 'person-' + str(self.counter)
        tick = now().replace(microsecond=0) + dt.timedelta(days=2)
        profile = profile_value({'name': 'Тест ' + str(self.counter), 'city': 'Москва',
            'desiredRole': 'Менеджер', 'geographies': ['Москва'], 'consentReuse': consent,
            'autoApprove': auto, 'workMode': 'office',
            'availability': [{'start': iso(tick), 'end': iso(tick + dt.timedelta(hours=4))}]})
        # Include an untrusted internal-looking field; DTO must still whitelist.
        profile['secretToken'] = 'must-not-leak'
        with self.connect() as db:
            db.execute('''INSERT INTO cp_candidates
                (id,email,password_hash,profile,created_at,updated_at) VALUES (?,?,?,?,?,?)''',
                (cid, cid + '@example.test', 'not-a-real-password', json.dumps(profile), iso(now()), iso(now())))
            if completed:
                sid, iid = 'slot-' + cid, 'interview-' + cid
                db.execute('INSERT INTO cp_recruiter_slots VALUES (?,?,?,?,?)',
                           (sid, iso(tick), iso(tick + dt.timedelta(minutes=30)), 'Тест', cid))
                db.execute('INSERT INTO cp_interviews VALUES (?,?,?,?,?,?,?)',
                           (iid, cid, sid, 'completed', None, iso(now()), iso(now())))
            db.execute('INSERT INTO cp_internal_profiles VALUES (?,?)',
                       (cid, json.dumps({'summary': 'Неизменяемое досье'})))
        return cid

    @staticmethod
    def batch(cids, external='batch-1', source='core', **changes):
        return {'source': source, 'version': 'core-v1', 'externalBatchId': external,
                'candidates': [{'candidateId': cid, 'rank': n, 'reason': 'Объяснение ' + cid}
                               for n, cid in enumerate(cids)], **changes}

    def import_people(self, cids, **changes):
        with self.connect() as db:
            return store.import_batch(db, 'a', self.vacancy['id'], self.batch(cids, **changes), self.portal)

    def accepted(self, cids, **changes):
        result = self.import_people(cids, **changes)
        with self.connect() as db:
            for aid, cid in zip(result['applicationIds'], cids):
                self.portal.decide_application(db, cid, {'applicationId': aid, 'decision': 'accepted'})
        return result['applicationIds']

    def assert_problem(self, status, fn, *args, **kwargs):
        with self.assertRaises(Problem) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.status, status, caught.exception.message)

    def test_create_uses_canonical_vacancy_and_request_id_survives_restart(self):
        body = {'role': 'Инженер', 'problem': 'Развитие', 'salary': '100–150 тыс.', 'format': 'Москва'}
        with self.connect() as db:
            first = store.create_vacancy(db, 'a', body, 'request-1')
        with self.connect() as db:
            second = store.create_vacancy(db, 'a', body, 'request-1')
            self.assertEqual(first, second)
            self.assertEqual(second['title'], 'Инженер')
            self.assertEqual(second['role'], 'Инженер')
            canonical = json.loads(db.execute('SELECT body FROM cp_vacancies WHERE id=?', (first['id'],)).fetchone()[0])
            self.assertEqual(canonical['salary'], body['salary'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_vacancies').fetchone()[0], 2)
            self.assert_problem(409, store.create_vacancy, db, 'a', {'title': 'Другой'}, 'request-1')

    def test_vacancy_alias_validation_and_unknown_fields(self):
        with self.connect() as db:
            for body in ({'title': 'A', 'role': 'B'}, {'title': 'A', 'workspaceId': 'b'},
                         {'title': 'A', 'remote': 'yes'}, {'title': ''}, {'title': 'A', 'closedBy': 'x'}):
                self.assert_problem(400, store.create_vacancy, db, 'a', body)

    def test_known_ids_cannot_cross_workspace_read_or_write(self):
        with self.connect() as db:
            self.assertEqual(store.list_vacancies(db, 'b'), [])
            for operation in (store.vacancy, store.update_vacancy, store.archive_vacancy):
                args = (db, 'b', self.vacancy['id']) + ({'title': 'Чужое'},) if operation is store.update_vacancy else (db, 'b', self.vacancy['id'])
                self.assert_problem(404, operation, *args)
            self.assert_problem(404, store.vacancy, db, 'missing', self.vacancy['id'])

    def test_update_preserves_workflow_outcome_and_generation_and_checks_revision(self):
        vid = self.vacancy['id']
        with self.connect() as db:
            canonical = json.loads(db.execute('SELECT body FROM cp_vacancies WHERE id=?', (vid,)).fetchone()[0])
            canonical.update(status='closed', closedBy='same-person', closedByApplication='same-application')
            db.execute('UPDATE cp_vacancies SET body=? WHERE id=?', (json.dumps(canonical), vid))
            updated = store.update_vacancy(db, 'a', vid, {'role': 'Руководитель'}, 0, 'edit')
            self.assertEqual(updated['closedBy'], 'same-person')
            self.assertEqual(updated['status'], 'closed')
            self.assertEqual(updated['generation'], self.vacancy['generation'])
            self.assertEqual(updated['revision'], 1)
            self.assertEqual(updated['title'], 'Руководитель')
            self.assertEqual(store.update_vacancy(db, 'a', vid, {'role': 'Руководитель'}, 0, 'edit')['revision'], 1)
            self.assert_problem(409, store.update_vacancy, db, 'a', vid, {'title': 'Ещё'}, 0)
            self.assert_problem(409, store.ensure_open, db, 'a', vid)

    def test_create_does_not_commit_callers_transaction(self):
        db = self.connect()
        try:
            created = store.create_vacancy(db, 'a', {'title': 'Rollback'}, 'rolled-back')
            self.assertTrue(db.in_transaction)
            db.rollback()
        finally:
            db.close()
        with self.connect() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM cp_vacancies WHERE id=?', (created['id'],)).fetchone())
            self.assertIsNone(db.execute("SELECT 1 FROM em_store_commands WHERE request_id='rolled-back'").fetchone())

    def test_batch_waits_for_candidate_approval_without_generating_profiles(self):
        cid = self.person()
        result = self.import_people([cid])
        aid = result['applicationIds'][0]
        with self.connect() as db:
            self.assertEqual(store.applications(db, 'a'), [])
            self.assert_problem(404, store.application, db, 'a', aid)
            candidate_app = self.portal.application(db, aid, cid)
            self.assertEqual(candidate_app['status'], 'pending')
            self.assertEqual(candidate_app['vacancy']['id'], self.vacancy['id'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_candidates').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM test_events').fetchone()[0], 1)
            self.portal.decide_application(db, cid, {'applicationId': aid, 'decision': 'accepted'})
            visible = store.application(db, 'a', aid)
            self.assertEqual(visible['candidateId'], cid)
            self.assertEqual(visible['source'], 'core')
            self.assertEqual(visible['reason'], 'Объяснение ' + cid)
            self.assertNotIn('email', visible['profile'])
            self.assertNotIn('secretToken', visible['profile'])
            self.assertNotIn('password', json.dumps(visible))

    def test_explicit_opt_in_reuses_existing_auto_approval_only(self):
        automatic, manual = self.person(auto=True), self.person()
        result = self.import_people([automatic, manual])
        with self.connect() as db:
            self.assertEqual([p['candidateId'] for p in store.applications(db, 'a')], [automatic])
            self.assertTrue(self.portal.application(db, result['applicationIds'][0])['approvedAutomatically'])
            self.assertFalse(self.portal.application(db, result['applicationIds'][1])['approvedAutomatically'])

    def test_whole_batch_rejects_missing_consent_or_interview_without_partial_rows(self):
        good, no_consent, no_interview = self.person(), self.person(consent=False), self.person(completed=False)
        with self.connect() as db:
            for invalid in (no_consent, no_interview, 'absent'):
                self.assert_problem(404 if invalid == 'absent' else 409, store.import_batch,
                    db, 'a', self.vacancy['id'], self.batch([good, invalid]), self.portal)
            for table in ('cp_applications', 'em_reviews', 'em_batches', 'em_batch_items', 'test_events'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)

    def test_batch_invalid_shapes_or_duplicate_ranks_are_rejected(self):
        c1, c2 = self.person(), self.person()
        wrong = [self.batch([c1] * 101), self.batch([c1, c1]), self.batch([c1], source='made-up'),
                 self.batch([c1], candidates=[{'candidateId': c1, 'rank': True, 'reason': 'R'}]),
                 self.batch([c1], candidates=[{'candidateId': c1, 'rank': 0, 'reason': ''}]),
                 self.batch([c1, c2], candidates=[{'candidateId': c, 'rank': 1, 'reason': 'R'} for c in (c1, c2)])]
        with self.connect() as db:
            for payload in wrong:
                self.assert_problem(400, store.import_batch, db, 'a', self.vacancy['id'], payload, self.portal)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_batches').fetchone()[0], 0)

    def test_explicit_empty_core_result_is_persisted_without_fabricating_candidates(self):
        result = self.import_people([])
        self.assertEqual(result['applicationIds'], [])
        self.assertEqual(self.import_people([]), result)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_batches').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_candidates').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM test_events').fetchone()[0], 0)

    def test_batch_retry_and_different_batch_never_resurface_rejected_or_selected(self):
        cids = [self.person(), self.person(), self.person()]
        original = self.import_people(cids)
        aids = original['applicationIds']
        with self.connect() as db:
            for cid, aid in zip(cids, aids):
                self.portal.decide_application(db, cid, {'applicationId': aid, 'decision': 'accepted'})
            store.reject(db, 'a', aids[0])
            self.meeting(db, aids[1], 'm-1')
            store.mark_selected(db, 'a', aids[1], 'm-1')
        retried = self.import_people(cids)
        new_batch = self.import_people(cids, external='next-batch')
        self.assertEqual(retried, original)
        self.assertEqual(new_batch['addedApplicationIds'], [])
        with self.connect() as db:
            self.assertEqual([p['applicationId'] for p in store.review_candidates(db, 'a', self.vacancy['id'])], [aids[2]])
            self.assertEqual([p['applicationId'] for p in store.applications(db, 'a', 'selected')], [aids[1]])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_applications').fetchone()[0], 3)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM test_events').fetchone()[0], 3)
            self.assert_problem(409, store.import_batch, db, 'a', self.vacancy['id'], self.batch(cids, version='changed'), self.portal)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_batch_failure_during_portal_write_rolls_back_even_if_caller_catches(self):
        cids = [self.person(), self.person()]
        original = self.portal.propose_application
        calls = []
        def fail_second(db, body):
            calls.append(body)
            original(db, body)
            if len(calls) == 2:
                raise Problem('Synthetic failure', 503)
        self.portal.propose_application = fail_second
        with self.connect() as db:
            self.assert_problem(503, store.import_batch, db, 'a', self.vacancy['id'], self.batch(cids), self.portal)
            for table in ('cp_applications', 'em_reviews', 'em_batches', 'em_batch_items', 'test_events'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)

    def test_concurrent_batch_retry_has_one_batch_application_and_event(self):
        cid = self.person()
        def invoke(_):
            return self.import_people([cid])
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(invoke, range(4)))
        self.assertTrue(all(result == results[0] for result in results))
        with self.connect() as db:
            for table in ('cp_applications', 'em_reviews', 'em_batches', 'em_batch_items', 'test_events'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 1)

    def test_skip_defers_to_end_repeated_request_is_noop_and_returns_after_cycle(self):
        aids = self.accepted([self.person(), self.person(), self.person()])
        with self.connect() as db:
            first = store.skip(db, 'a', aids[0], 'skip-first')
            revision = store.vacancy(db, 'a', self.vacancy['id'])['revision']
            self.assertEqual(store.skip(db, 'a', aids[0], 'skip-first'), first)
            self.assertEqual(store.vacancy(db, 'a', self.vacancy['id'])['revision'], revision)
            self.assertEqual([a['applicationId'] for a in store.applications(db, 'a')], aids[1:] + aids[:1])
            store.skip(db, 'a', aids[1], 'skip-second')
            store.skip(db, 'a', aids[2], 'skip-third')
        with self.connect() as db:
            self.assertEqual([a['applicationId'] for a in store.applications(db, 'a')], aids)
            self.assertEqual(store.application(db, 'a', aids[0])['skipCount'], 1)
            self.assert_problem(409, store.skip, db, 'a', aids[1], 'skip-first')

    def test_reject_hides_every_employer_projection_but_preserves_same_profile(self):
        cid = self.person()
        aid = self.accepted([cid])[0]
        with self.connect() as db:
            self.meeting(db, aid, 'm-1')
            store.mark_selected(db, 'a', aid, 'm-1')
            first = store.reject(db, 'a', aid, 'reject-once')
            revision = store.vacancy(db, 'a', self.vacancy['id'])['revision']
            self.assertEqual(store.reject(db, 'a', aid), first)
            self.assertEqual(store.reject(db, 'a', aid, 'reject-once'), first)
            self.assertEqual(store.vacancy(db, 'a', self.vacancy['id'])['revision'], revision)
            for view in ('all', 'proposed', 'selected'):
                self.assertEqual(store.applications(db, 'a', view), [])
            self.assert_problem(404, store.application, db, 'a', aid)
            self.assert_problem(404, store.mark_selected, db, 'a', aid, 'm-1')
            self.assertEqual(self.portal.application(db, aid, cid)['candidateId'], cid)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_candidates').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_internal_profiles').fetchone()[0], 1)

    def test_rejection_is_participation_specific_for_multiple_vacancies(self):
        cid = self.person()
        aid = self.accepted([cid])[0]
        with self.connect() as db:
            other = store.create_vacancy(db, 'a', {'title': 'Менеджер 2'})
            second = store.import_batch(db, 'a', other['id'], self.batch([cid]), self.portal)['applicationIds'][0]
            self.portal.decide_application(db, cid, {'applicationId': second, 'decision': 'accepted'})
            store.reject(db, 'a', aid)
            self.assertEqual([a['applicationId'] for a in store.applications(db, 'a')], [second])

    def test_acceptance_decline_and_consent_withdrawal_remove_visibility_and_block_actions(self):
        cids = [self.person(), self.person()]
        aids = self.accepted(cids)
        with self.connect() as db:
            db.execute("UPDATE cp_applications SET status='declined' WHERE id=?", (aids[0],))
            row = db.execute('SELECT profile FROM cp_candidates WHERE id=?', (cids[1],)).fetchone()
            profile = json.loads(row[0]); profile['consentReuse'] = False
            db.execute('UPDATE cp_candidates SET profile=? WHERE id=?', (json.dumps(profile), cids[1]))
            self.assertEqual(store.applications(db, 'a'), [])
            for aid in aids:
                self.assert_problem(404, store.skip, db, 'a', aid)
                self.assert_problem(404, store.reject, db, 'a', aid)
                self.assert_problem(404, store.mark_selected, db, 'a', aid, 'not-a-meeting')

    def test_cross_workspace_application_skip_reject_select_and_batch_are_denied(self):
        cid = self.person()
        aid = self.accepted([cid])[0]
        with self.connect() as db:
            for operation in (store.application, store.skip, store.reject):
                self.assert_problem(404, operation, db, 'b', aid)
            self.assert_problem(404, store.mark_selected, db, 'b', aid, 'm')
            self.assert_problem(404, store.import_batch, db, 'b', self.vacancy['id'], self.batch([cid]), self.portal)
            self.assertEqual(store.applications(db, 'b'), [])

    def meeting(self, db, aid, mid, state='pending'):
        tick = now() + dt.timedelta(days=2)
        db.execute('''INSERT INTO cp_meetings
                   (id,application_id,start,end,status,room,confirmed_at,completed_at,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)''',
                   (mid, aid, iso(tick), iso(tick + dt.timedelta(minutes=30)), state, None, None, None, iso(now())))

    def test_selection_requires_matching_persisted_meeting_and_outer_commit(self):
        aid = self.accepted([self.person()])[0]
        with self.connect() as db:
            self.assert_problem(409, store.mark_selected, db, 'a', aid, 'missing')
            self.assertEqual(store.application(db, 'a', aid)['reviewState'], 'proposed')
        db = self.connect()
        try:
            self.meeting(db, aid, 'm-rollback')
            store.mark_selected(db, 'a', aid, 'm-rollback')
            db.rollback()
        finally:
            db.close()
        with self.connect() as db:
            self.assertEqual(store.application(db, 'a', aid)['reviewState'], 'proposed')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_meetings').fetchone()[0], 0)
            self.meeting(db, aid, 'm-1')
            self.assert_problem(409, store.mark_selected, db, 'a', aid, 'wrong-id')
            first = store.mark_selected(db, 'a', aid, 'm-1')
            revision = store.vacancy(db, 'a', self.vacancy['id'])['revision']
            self.assertEqual(store.mark_selected(db, 'a', aid, 'm-1'), first)
            self.assertEqual(store.vacancy(db, 'a', self.vacancy['id'])['revision'], revision)
            self.assert_problem(409, store.skip, db, 'a', aid)
        with self.connect() as db:
            self.assertEqual(store.application(db, 'a', aid)['reviewState'], 'selected')

    def test_stale_revision_cannot_skip_or_reject(self):
        aid = self.accepted([self.person()])[0]
        with self.connect() as db:
            revision = store.vacancy(db, 'a', self.vacancy['id'])['revision']
            store.skip(db, 'a', aid, expected_revision=revision)
            self.assert_problem(409, store.reject, db, 'a', aid, expected_revision=revision)
            self.assertEqual(store.application(db, 'a', aid)['reviewState'], 'proposed')

    def test_archive_preserves_records_and_hides_own_vacancy(self):
        aid = self.accepted([self.person()])[0]
        with self.connect() as db:
            self.meeting(db, aid, 'm-1')
            self.assert_problem(409, store.archive_vacancy, db, 'a', self.vacancy['id'])
            db.execute("UPDATE cp_meetings SET status='completed'")
            archived = store.archive_vacancy(db, 'a', self.vacancy['id'])
            self.assertIsNotNone(archived['archivedAt'])
            self.assertEqual(store.archive_vacancy(db, 'a', self.vacancy['id']), archived)
            self.assertEqual(store.list_vacancies(db, 'a'), [])
            self.assertEqual(store.applications(db, 'a'), [])
            self.assertEqual(len(store.list_vacancies(db, 'a', include_archived=True)), 1)
            self.assert_problem(404, store.application, db, 'a', aid)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_applications').fetchone()[0], 1)

    def test_internal_adoption_is_explicit_idempotent_and_does_not_steal_other_workspace(self):
        cid = self.person()
        with self.connect() as db:
            self.portal.add_vacancy(db, {'title': 'Старая', 'company': 'HR', 'city': 'Москва',
                                       'remote': False, 'description': ''})
            vid = db.execute('SELECT id FROM cp_vacancies WHERE id!=?', (self.vacancy['id'],)).fetchone()[0]
            self.portal.propose_application(db, {'candidateId': cid, 'vacancyId': vid})
            aid = db.execute('SELECT id FROM cp_applications WHERE vacancy_id=?', (vid,)).fetchone()[0]
            self.portal.decide_application(db, cid, {'applicationId': aid, 'decision': 'accepted'})
            self.assert_problem(404, store.adopt_vacancy, db, 'b', vid)
            self.assert_problem(404, store.adopt_existing, db, 'b')
            self.assert_problem(404, store.adopt_vacancy, db, store.TEAM_WORKSPACE, self.vacancy['id'])
            self.assertEqual(store.applications(db, store.TEAM_WORKSPACE), [])
            self.assertEqual(store.adopt_existing(db, store.TEAM_WORKSPACE),
                             {'vacanciesAdopted': 1, 'applicationsAdopted': 1})
            adopted = store.application(db, store.TEAM_WORKSPACE, aid)
            self.assertEqual(adopted['source'], 'recruiter')
            self.assertEqual(adopted['reason'], '')
            self.assertEqual(adopted['candidateId'], cid)
            self.assertEqual(store.adopt_existing(db, store.TEAM_WORKSPACE),
                             {'vacanciesAdopted': 0, 'applicationsAdopted': 0})
            self.assertEqual(store.vacancy(db, 'a', self.vacancy['id'])['workspaceId'], 'a')

    def test_legacy_application_with_meeting_is_adopted_as_selected(self):
        cid = self.person()
        with self.connect() as db:
            self.portal.add_vacancy(db, {'title': 'Старая', 'company': 'HR', 'city': 'Москва',
                                       'remote': False, 'description': ''})
            vid = db.execute('SELECT id FROM cp_vacancies WHERE id!=?', (self.vacancy['id'],)).fetchone()[0]
            self.portal.propose_application(db, {'candidateId': cid, 'vacancyId': vid})
            aid = db.execute('SELECT id FROM cp_applications WHERE vacancy_id=?', (vid,)).fetchone()[0]
            self.portal.decide_application(db, cid, {'applicationId': aid, 'decision': 'accepted'})
            self.meeting(db, aid, 'legacy-meeting', 'completed')
            store.adopt_existing(db, store.TEAM_WORKSPACE)
            self.assertEqual(store.application(db, store.TEAM_WORKSPACE, aid)['reviewState'], 'selected')
            self.assertEqual(db.execute('SELECT manager_id FROM cp_meetings WHERE id=?',
                                        ('legacy-meeting',)).fetchone()[0], 'workspace:' + store.TEAM_WORKSPACE)


if __name__ == '__main__':
    unittest.main()
