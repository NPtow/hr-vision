"""Temporary SQLite + mocked engine documents; no live model or delivery calls."""
import json
import time
from unittest.mock import patch
import unittest

import candidate_invites
import candidate_notifications as notices
import selection
import test_employer_store as fixture
import test_employer_acceptance as http_fixture


def sealed(format, body, **extra):
    # Minimal protocol doubles only. Not evidence of model quality/provenance.
    d = {'engine_contract': 'engine-v1', 'format': format, 'synthetic': False,
         'document_sha256': '', 'body': body, **extra}
    d['document_sha256'] = selection.digest(d)
    return d


class SelectionTest(fixture.EmployerStoreTest):
    def setUp(self):
        super().setUp()
        with self.connect() as db:
            __import__('employer_access').init(db)
            notices.init(db)
            selection.init(db)
            db.execute('''CREATE TABLE cp_booking_profile_consents (
              candidate_id TEXT PRIMARY KEY,contact_email TEXT,invitation_id TEXT)''')
        self.routes = selection.SelectionRoutes(self.connect, self.portal.lock, self.portal)

    def core(self, cid, *, state='supported'):
        doc = sealed('candidate_profile/1', {'candidate_id': 'engine-' + cid, 'families': [{
            'criteria': [{'criterion_id': 'A04', 'state': state,
                'level': 3 if state == 'supported' else None, 'failed_processing': False}], 'axes': []}]},
            model={'mode': 'provider', 'call_count': 1, 'model_id': 'test-only'})
        with self.connect() as db:
            p = selection.person(db, cid)
            body = {'candidateId': cid, 'interviewId': p['interviewId'], 'engineCandidateId': 'engine-' + cid,
                    'jobId': 'test-only', 'requestHash': 'a' * 64, 'sourceRevision': p['sourceRevision'], 'document': doc}
            result = selection.import_result(db, body)
        return result, body

    def matched(self, cid, *, partition='ranked'):
        result, body = self.core(cid)
        vid = self.vacancy['id']
        with self.connect() as db:
            v = selection.vacancy(db, vid)
            requirement = sealed('requirement_profile/1', {'state': 'confirmed', 'vacancy_id': 'engine-vacancy'})
            req = selection.import_requirement(db, {'vacancyId': vid, 'vacancyHash': v['contentHash'],
                'engineVacancyId': 'engine-vacancy', 'document': requirement})
            doc = sealed('match/1', {'match': {'candidate_id': 'engine-' + cid, 'vacancy_id': 'engine-vacancy',
                'candidate_profile_revision_ref': {'sha256': body['document']['document_sha256']},
                'requirement_profile_revision_ref': {'sha256': req['id']}, 'requirement_state': 'confirmed',
                'status': 'current', 'partition': partition, 'partition_reasons': []}})
            selection.import_match(db, {'resultId': result['id'], 'requirementId': req['id'], 'document': doc,
                                        'rank': 0 if partition == 'ranked' else None})
        return cid

    def prepared(self, cid):
        with self.connect() as db:
            db.execute('UPDATE cp_candidates SET email=NULL,password_hash=NULL WHERE id=?', (cid,))
            db.execute('INSERT INTO cp_intake VALUES (?,\'manual\',NULL,\'\',\'now\')', (cid,))
            db.execute('INSERT INTO cp_booking_profile_consents VALUES (?,?,?)', (cid, 'private@example.test', 'booking-' + cid))
            db.execute('INSERT INTO cn_booking_telegram VALUES (?,?,?,?)', ('booking-' + cid, cid, '123456', int(time.time())))

    def publish(self, cid, key='test-command'):
        with self.connect() as db:
            return selection.nominate(db, self.portal, {'candidateIds': [cid], 'vacancyId': self.vacancy['id'], 'requestId': key})

    def test_booking_contacts_are_connected_before_cabinet_activation(self):
        cid = self.person()
        self.prepared(cid)
        with self.connect() as db:
            row = selection.person(db, cid)
            self.assertFalse(row['accountActive'])
            self.assertTrue(row['emailConnected'])
            self.assertTrue(row['telegramConnected'])
            self.assertEqual(row['email'], 'private@example.test')
            self.assertEqual(row['interviewStatus'], 'completed')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cn_outbox').fetchone()[0], 0)

    def test_cv_contacts_and_unverified_telegram_are_not_connected(self):
        cid = self.person(completed=False)
        with self.connect() as db:
            db.execute('UPDATE cp_candidates SET email=NULL,password_hash=NULL WHERE id=?', (cid,))
            db.execute('INSERT INTO cp_intake VALUES (?,\'manual\',NULL,\'cv-only@example.test\',\'now\')', (cid,))
            notices.set_email(db, cid, 'cv-only@example.test')
            db.execute('UPDATE cn_recipients SET telegram_chat_id=\'123456\' WHERE candidate_id=?', (cid,))
            row = selection.person(db, cid)
            self.assertEqual(row['email'], 'cv-only@example.test')
            self.assertFalse(row['emailConnected'])
            self.assertFalse(row['telegramConnected'])
            self.assertIsNone(row['interviewStatus'])

    def test_connected_telegram_requires_account_or_exact_consented_booking(self):
        active, prepared = self.person(), self.person()
        self.prepared(prepared)
        with self.connect() as db:
            notices.set_email(db, active, active + '@example.test')
            db.execute('UPDATE cn_recipients SET telegram_chat_id=\'654321\',telegram_verified_at=0 WHERE candidate_id=?', (active,))
            row = selection.person(db, active)
            self.assertTrue(row['emailConnected'])
            self.assertTrue(row['telegramConnected'])
            db.execute('UPDATE cp_booking_profile_consents SET invitation_id=\'other-booking\' WHERE candidate_id=?', (prepared,))
            self.assertFalse(selection.person(db, prepared)['telegramConnected'])
            # An old generic binding cannot replace the prepared booking scope.
            db.execute('UPDATE cp_candidates SET email=NULL WHERE id=?', (active,))
            self.assertFalse(selection.person(db, active)['telegramConnected'])

    def test_optional_contact_stores_do_not_block_core_candidate_snapshots(self):
        cid = self.person()
        with self.connect() as db:
            before = selection.person(db, cid)
            notices.set_email(db, cid, cid + '@example.test')
            db.execute('UPDATE cn_recipients SET telegram_chat_id=\'654321\',telegram_verified_at=0 WHERE candidate_id=?', (cid,))
            db.execute('DROP TABLE cp_booking_profile_consents')
            self.assertTrue(selection.person(db, cid)['telegramConnected'])
            db.execute('DROP TABLE cn_recipients')
            db.execute('DROP TABLE cn_booking_telegram')
            after = selection.person(db, cid)
            self.assertTrue(after['emailConnected'])
            self.assertFalse(after['telegramConnected'])
            self.assertEqual(before['sourceRevision'], after['sourceRevision'])

    def test_prepared_booking_contact_does_not_require_generic_notification_store(self):
        cid = self.person()
        self.prepared(cid)
        with self.connect() as db:
            db.execute('DROP TABLE cn_recipients')
            self.assertTrue(selection.person(db, cid)['emailConnected'])
            self.assertTrue(selection.person(db, cid)['telegramConnected'])

    def test_unknown_never_becomes_a_score_or_a_recommendation(self):
        cid = self.person()
        self.core(cid, state='unknown')
        with self.connect() as db:
            row = selection.directory(db, self.vacancy['id'])['candidates'][0]
            self.assertEqual(row['assessment']['counts']['unknown'], 1)
            self.assertEqual(row['assessment']['counts']['supported'], 0)
            self.assertEqual(row['assessment']['recommendation'], 'not_matched')
            self.assertFalse(row['canAdd'])
        self.assert_problem(409, self.publish, cid)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sl_nominations').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cn_outbox').fetchone()[0], 0)

    def test_hash_identity_stale_source_and_duplicate_ingestion(self):
        cid = self.person()
        first, body = self.core(cid)
        with self.connect() as db:
            self.assertEqual(selection.import_result(db, body)['id'], first['id'])
            self.assertTrue(selection.import_result(db, body)['existing'])
            forged = json.loads(json.dumps(body)); forged['document']['body']['candidate_id'] = 'wrong'
            self.assert_problem(422, selection.import_result, db, forged)
            self.assert_problem(422, selection.import_result, db, {**body, 'engineCandidateId': 'wrong'})
            db.execute('UPDATE cp_interviews SET completed_at=\'changed\' WHERE candidate_id=?', (cid,))
            self.assert_problem(409, selection.import_result, db, body)
            self.assertTrue(selection.directory(db)['candidates'][0]['assessment']['stale'])

    def test_edits_use_cas_and_never_modify_credentials_or_consent(self):
        cid = self.person(consent=False)
        with self.connect() as db:
            p = selection.person(db, cid)
            original = db.execute('SELECT email,password_hash FROM cp_candidates WHERE id=?', (cid,)).fetchone()
            selection.edit(db, {'candidateId': cid, 'revision': p['revision'], 'fields': {'name': 'Новое имя'}})
            self.assert_problem(409, selection.edit, db, {'candidateId': cid, 'revision': p['revision'], 'fields': {'city': 'Пермь'}})
            p = selection.person(db, cid)
            self.assert_problem(400, selection.edit, db, {'candidateId': cid, 'revision': p['revision'], 'fields': {'consentReuse': True}})
            self.assertEqual(db.execute('SELECT email,password_hash FROM cp_candidates WHERE id=?', (cid,)).fetchone(), original)
            self.assertFalse(selection.person(db, cid)['consentReuse'])

    def test_nomination_invites_both_channels_but_hides_profile_until_candidate_approves(self):
        cid = self.matched(self.person(consent=False))
        self.prepared(cid)
        config = notices.Settings(enabled=True, smtp_host='smtp.example.test', smtp_from='candidate@example.test',
            telegram_token='test-only', telegram_username='FixtureBot', product_origin='https://example.test')
        with patch('candidate_notifications.settings', return_value=config):
            receipt = self.publish(cid)
            self.assertEqual(receipt['waitingProfile'], [cid])
            self.assertEqual(self.publish(cid), receipt)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_applications').fetchone()[0], 0)
            rows = [notices._dict(db.execute('SELECT * FROM cn_outbox WHERE channel=?', (channel,))) for channel in ('email', 'telegram')]
            self.assertEqual([r['status'] for r in rows], ['pending', 'pending'])
            self.assertEqual({json.loads(r['payload_json'])['invitationId'] for r in rows}.__len__(), 1)
            for r in rows:
                self.assertEqual(selection.prepare_invitation(db, r, config, int(time.time()))[0], 'pending')
            # Claim with the existing endpoint; preserve the person ID and let the
            # ordinary proposal require a distinct vacancy acceptance afterwards.
            raw = json.loads(rows[0]['payload_json'])['url'].split('#claim=')[1]
            capability = candidate_invites.resolve(db, raw, 'claim')
            self.portal.notifications = notices
            self.portal.claim(db, capability, {'email': 'private@example.test', 'password': 'unit-test-password', 'consentReuse': True})
            app = db.execute('SELECT id,status FROM cp_applications WHERE candidate_id=?', (cid,)).fetchone()
            self.assertEqual(app[1], 'pending')
            self.assertEqual(__import__('employer_store').applications(db, 'a'), [])
            self.portal.decide_application(db, cid, {'applicationId': app[0], 'decision': 'accepted'})
            self.assertEqual(len(__import__('employer_store').applications(db, 'a')), 1)
            self.assertEqual(selection.prepare_invitation(db, rows[0], config, int(time.time()))[0], 'error')
            selection.reconcile_nominations(db, self.portal, cid)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_applications').fetchone()[0], 1)

    def test_failed_batch_and_wrong_role_have_no_side_effects(self):
        good, bad = self.matched(self.person()), self.person()
        with self.connect() as db:
            self.assert_problem(409, selection.nominate, db, self.portal, {'candidateIds': [good, bad], 'vacancyId': self.vacancy['id'], 'requestId': 'partial'})
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sl_nominations').fetchone()[0], 0)
        for role in ('candidate', 'recruiter', 'employer'):
            self.assert_problem(403, self.routes.handle, 'GET', selection.PREFIX + 'candidates', {}, None, lambda db: role)

    def test_normal_worker_dispatches_invitation_without_disclosing_assessment(self):
        cid = self.matched(self.person(consent=False))
        self.prepared(cid)
        config = notices.Settings(enabled=True, smtp_host='smtp.example.test', smtp_from='candidate@example.test',
            telegram_token='test-only', telegram_username='FixtureBot', product_origin='https://example.test')
        class Transport:
            def __init__(self): self.calls = []
            def send_email(self, recipient, subject, content, key):
                self.calls.append(('email', content)); return 'smtp_accepted'
            def send_telegram(self, recipient, content, reply_markup=None):
                self.calls.append(('telegram', content)); return '123'
        fake = Transport()
        with patch('candidate_notifications.settings', return_value=config):
            self.publish(cid)
            counts = notices.process_pending(self.connect, settings=config, transport=fake)
            self.assertEqual(counts['delivered'], 2)
            notices.process_pending(self.connect, settings=config, transport=fake)
        self.assertEqual(sorted(k for k, _ in fake.calls), ['email', 'telegram'])
        for _, body in fake.calls:
            self.assertNotIn('test-only', body)
            self.assertNotIn('candidate_profile', body)
            self.assertNotIn('Неизменяемое досье', body)

    def test_closed_vacancy_does_not_block_candidate_consent(self):
        cid = self.matched(self.person(consent=False))
        self.prepared(cid)
        self.publish(cid)
        with self.connect() as db:
            v = json.loads(db.execute('SELECT body FROM cp_vacancies WHERE id=?', (self.vacancy['id'],)).fetchone()[0])
            v['status'] = 'closed'
            db.execute('UPDATE cp_vacancies SET body=? WHERE id=?', (json.dumps(v), v['id']))
            p = selection.person(db, cid)
            profile = json.loads(db.execute('SELECT profile FROM cp_candidates WHERE id=?', (cid,)).fetchone()[0])
            profile['consentReuse'] = True
            db.execute('UPDATE cp_candidates SET email=?,profile=? WHERE id=?', ('private@example.test', json.dumps(profile), cid))
            selection.reconcile_nominations(db, self.portal, cid)
            self.assertEqual(db.execute('SELECT state FROM sl_nominations WHERE candidate_id=?', (cid,)).fetchone()[0], 'blocked')


class SelectionHTTPTest(unittest.TestCase):
    setUpClass = classmethod(http_fixture.EmployerAcceptanceTest.setUpClass.__func__)
    tearDownClass = classmethod(http_fixture.EmployerAcceptanceTest.tearDownClass.__func__)
    setUp = http_fixture.EmployerAcceptanceTest.setUp
    request = http_fixture.EmployerAcceptanceTest.request
    ok = http_fixture.EmployerAcceptanceTest.ok
    employer = http_fixture.EmployerAcceptanceTest.employer
    vacancy = http_fixture.EmployerAcceptanceTest.vacancy

    def test_real_handler_rejects_employer_and_anonymous_and_lists_actual_vacancies(self):
        employer = self.employer()
        job = self.vacancy(employer)
        self.assertEqual(self.request('selection/vacancies')[0], 401)
        self.assertEqual(self.request('selection/vacancies', token=employer['token'])[0], 401)
        rows = self.ok('selection/vacancies', token=self.manager)['vacancies']
        self.assertIn(job['id'], [v['id'] for v in rows])
        self.assertEqual(self.request('selection/cohort', {'vacancyId': job['id'], 'candidateIds': []}, token=self.manager)[0], 400)
        self.assertEqual(self.request('selection/cohort', {'vacancyId': job['id'], 'candidateIds': []},
            token=self.manager, headers={'Origin': 'https://foreign.invalid'})[0], 403)


if __name__ == '__main__':
    unittest.main()
