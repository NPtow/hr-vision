"""Integration uses the real candidate schema and synthetic HH responses only."""
from dataclasses import replace
import datetime as dt
import hashlib
import json
import time
import unittest
from urllib.parse import parse_qs, urlsplit

import candidate_invites as invites
from domain import Problem, iso, now
from hh_recruiting import API, HHConfig
from hh_routes import HHRecruiterRoutes
from test_hh_recruiting import Provider, TOKEN, ME, CV
import test_candidate_portal as fixture


class HHRouteTest(unittest.TestCase):
    connect = fixture.CandidatePortalTest.connect
    call = fixture.CandidatePortalTest.call
    not_manager = staticmethod(fixture.CandidatePortalTest.not_manager)
    slot = fixture.CandidatePortalTest.slot

    def setUp(self):
        fixture.CandidatePortalTest.setUp(self)
        self.time = time.time() - 2
        self.provider = Provider()
        self.config = HHConfig(enabled=True, sending_enabled=True, client_id='fixture-client', client_secret='fixture-secret',
          redirect_uri='https://portal.example/iframe.html?id=hr-vision-product--manager&viewMode=story&workspace=recruiter&hhCallback=1',
          user_agent='HR-Test/1 test@example.test', invite_origin=self.portal.origin)
        self.routes = HHRecruiterRoutes(self.connect, self.portal, self.config, transport=self.provider, clock=lambda: self.time)

    def tearDown(self):
        self.routes.stop_worker()
        self.assertEqual([], self.provider.expected, 'Unconsumed provider fixtures')
        fixture.CandidatePortalTest.tearDown(self)

    def http(self, action, body=None, actor='manager'):
        return self.routes.handle('GET' if body is None else 'POST', '/api/hr/recruiter/hh/' + action,
                                  {'Authorization': 'Bearer synthetic'}, body, lambda db: actor)[0]

    def ready(self, full_cv=True):
        url = self.http('oauth/start', {})['url']
        state = parse_qs(urlsplit(url).query)['state'][0]
        self.provider.add('POST', '/token', TOKEN)
        self.provider.add('GET', '/me', ME)
        conn = self.http('oauth/complete', {'state': state, 'code': 'synthetic-code'})['connection']['id']
        with self.connect() as db:
            db.execute('INSERT INTO cp_vacancies VALUES (?,?,?)', ('local1', json.dumps({'id': 'local1', 'title': 'Менеджер'}), iso(now())))
        self.provider.add('GET', '/employers/10/vacancies/active?all_accessible=true&page=0&per_page=50',
                          {'items': [{'id': '30', 'name': 'Менеджер'}], 'page': 0, 'pages': 1})
        self.http('vacancies/sync', {'connectionId': conn})
        self.http('vacancies/link', {'connectionId': conn, 'vacancyId': '30', 'localId': 'local1'})
        self.import_cv(conn, full_cv)
        cid = self.routes.hh.negotiations('manager', conn, '30')[0]['candidate_id']
        return conn, cid

    def import_cv(self, conn, full=True):
        self.provider.add('GET', '/negotiations?vacancy_id=30',
                          {'collections': [{'id': 'response', 'url': API + '/negotiations/response?vacancy_id=30'}]})
        self.provider.add('GET', '/negotiations/response?vacancy_id=30&page=0&per_page=50',
                          {'items': [{'id': '40', 'resume': {'id': 'resume1', 'title': 'Менеджер'},
                           'chat_id': 50, 'employer_state': {'id': 'response'}}], 'pages': 1, 'page': 0})
        self.provider.add('GET', '/resumes/resume1', CV if full else {'errors': [{'value': 'access_denied'}]}, status=200 if full else 403)
        return self.http('responses/import', {'connectionId': conn, 'vacancyId': '30', 'collectionIds': ['response']})

    def rule(self, conn, event='interview_booked', enabled=True, **extra):
        return self.http('rules', {'connectionId': conn, 'vacancyId': '30', 'event': event,
          'template': 'Встреча: {invite_url}', 'enabled': enabled, **extra})

    def book(self, cid):
        slot = self.slot()
        self.call('recruiter/interview/book', {'candidateId': cid, 'slotId': slot['id']}, manager=True)
        with self.connect() as db:
            return db.execute('SELECT id FROM cp_interviews WHERE candidate_id=?', (cid,)).fetchone()[0]

    def send_fixture(self):
        self.provider.add('GET', '/negotiations/40', {'id': '40', 'vacancy': {'id': '30'}, 'chat_id': 50, 'messaging_status': 'ok'})
        self.provider.add('POST', '/common/chats/50/messages', {'id': 'message100'}, status=201,
                          verify=lambda call: self.assertIs(call['json_body']['is_automated'], True))

    def test_no_config_and_no_worker_and_auth_required(self):
        disabled = HHRecruiterRoutes(self.connect, self.portal, HHConfig(), transport=self.provider)
        result, headers = disabled.handle('GET', '/api/hr/recruiter/hh/state', {}, None, lambda db: 'manager')
        self.assertFalse(result['configured'])
        self.assertFalse(result['sendingEnabled'])
        self.assertEqual([], result['connections'])
        self.assertEqual('no-store', headers['Cache-Control'])
        self.assertIsNone(disabled.start_worker())
        self.assertEqual({'enabled': False, 'processed': 0}, disabled.process_pending())
        with self.assertRaises(Problem) as error:
            self.http('state', actor='candidate')
        self.assertEqual(403, error.exception.status)
        self.assertEqual([], self.provider.calls)

    def test_routes_narrow_and_candidate_routes_not_swallowed(self):
        self.assertTrue(self.routes.handles('/api/hr/recruiter/hh/state?connectionId=x'))
        self.assertFalse(self.routes.handles('/api/hr/recruiter/interview/book'))
        self.assertFalse(self.routes.handles('/api/hr/candidate/state'))

    def test_import_same_person_real_text_cv_no_account_no_consent(self):
        conn, cid = self.ready()
        self.import_cv(conn)
        with self.connect() as db:
            people = db.execute('SELECT id,email,password_hash,profile FROM cp_candidates').fetchall()
            self.assertEqual(1, len(people))
            self.assertEqual((cid, None, None), people[0][:3])
            profile = json.loads(people[0][3])
            self.assertFalse(profile['consentReuse'])
            self.assertFalse(profile['autoApprove'])
            cv = db.execute('SELECT filename,mime,content,text_content FROM cp_resumes WHERE candidate_id=?', (cid,)).fetchone()
            self.assertEqual(('HH-resume1.txt', 'text/plain'), cv[:2])
            self.assertEqual(cv[2].decode(), cv[3])
            self.assertIn('Анна Тестовая', cv[3])
            self.assertIsNone(db.execute('SELECT 1 FROM cp_invitations').fetchone())
        self.book(cid)  # Actual CandidatePortal book gate accepts the actual imported CV.

    def test_later_full_cv_fills_missing_without_overwriting_person(self):
        conn, cid = self.ready(full_cv=False)
        with self.connect() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM cp_resumes').fetchone())
            profile = json.loads(db.execute('SELECT profile FROM cp_candidates WHERE id=?', (cid,)).fetchone()[0])
            profile['name'] = 'Исправленное имя'
            db.execute('UPDATE cp_candidates SET profile=? WHERE id=?', (json.dumps(profile), cid))
        self.import_cv(conn)
        with self.connect() as db:
            self.assertIsNotNone(db.execute('SELECT 1 FROM cp_resumes WHERE candidate_id=?', (cid,)).fetchone())
            self.assertEqual('Исправленное имя', json.loads(db.execute('SELECT profile FROM cp_candidates WHERE id=?', (cid,)).fetchone()[0])['name'])

    def test_link_requires_existing_local_vacancy(self):
        conn, _ = self.ready()
        with self.assertRaises(Problem):
            self.http('vacancies/link', {'connectionId': conn, 'vacancyId': '30', 'localId': 'nonexistent'})
        self.assertEqual('local1', self.routes.hh.vacancies('manager', conn)[0]['localId'])

    def test_deleted_hh_resume_is_skipped_without_fake_profile(self):
        conn, cid = self.ready()
        self.provider.add('GET', '/negotiations?vacancy_id=30',
                          {'collections': [{'id': 'response', 'url': API + '/negotiations/response?vacancy_id=30'}]})
        self.provider.add('GET', '/negotiations/response?vacancy_id=30&page=0&per_page=50',
                          {'items': [{'id': '41', 'resume': None, 'chat_id': 51}], 'pages': 1, 'page': 0})
        result = self.http('responses/import', {'connectionId': conn, 'vacancyId': '30', 'collectionIds': ['response']})
        self.assertEqual({'negotiations': 0, 'resumes': 0, 'skipped': 1}, result)
        with self.connect() as db:
            self.assertEqual([(cid,)], db.execute('SELECT id FROM cp_candidates').fetchall())

    def test_rule_needs_event_template_and_defaults_off(self):
        conn, cid = self.ready()
        with self.assertRaises(Problem):
            self.rule(conn, event='')
        with self.assertRaises(Problem):
            self.rule(conn, template='без ссылки')
        self.http('rules', {'connectionId': conn, 'vacancyId': '30', 'event': 'interview_booked', 'template': '{invite_url}'})
        self.book(cid)
        self.assertEqual(0, self.routes.process_pending()['processed'])
        with self.connect() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM cp_invitations').fetchone())

    def test_booked_event_is_atomic_one_invitation_one_outbox_one_uuid(self):
        conn, cid = self.ready()
        self.rule(conn)
        self.book(cid)
        self.send_fixture()
        self.assertEqual(1, self.routes.process_pending()['processed'])
        self.assertEqual(0, self.routes.process_pending()['processed'])
        with self.connect() as db:
            self.assertEqual(1, db.execute('SELECT COUNT(*) FROM cp_invitations').fetchone()[0])
            self.assertEqual(1, db.execute('SELECT COUNT(*) FROM hh_outbox').fetchone()[0])
            invitation = db.execute('SELECT token_hash FROM cp_invitations').fetchone()[0]
            url, status = db.execute('SELECT invitation_url,status FROM hh_outbox').fetchone()
            raw = parse_qs(urlsplit(url).fragment)['invite'][0]
            self.assertEqual(hashlib.sha256(raw.encode()).hexdigest(), invitation)
            self.assertEqual('sent', status)
        public = json.dumps(self.http('state?connectionId=' + conn + '&vacancyId=30'))
        self.assertNotIn(raw, public)
        self.assertNotIn('synthetic-access', public)
        self.assertNotIn('synthetic-refresh', public)

    def test_rule_not_retroactive(self):
        conn, cid = self.ready()
        self.book(cid)
        self.time = time.time() + 10
        self.rule(conn)
        self.assertEqual(0, self.routes.process_pending()['processed'])
        with self.connect() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM cp_invitations').fetchone())

    def test_existing_manual_link_not_revoked_or_recreated(self):
        conn, cid = self.ready()
        self.rule(conn)
        self.book(cid)
        with self.connect() as db:
            manual = invites.issue(db, cid, 'call', self.portal.origin)
        self.assertEqual(0, self.routes.process_pending()['processed'])
        with self.connect() as db:
            self.assertEqual([(manual['invitationId'], None)], db.execute('SELECT id,revoked_at FROM cp_invitations').fetchall())
            self.assertEqual('existing_invitation', db.execute('SELECT outcome FROM hh_dispatch_events').fetchone()[0])

    def test_claim_only_on_completed_and_not_after_account_activation(self):
        conn, cid = self.ready()
        self.rule(conn, event='interview_completed')
        iid = self.book(cid)
        self.assertEqual(0, self.routes.process_pending()['processed'])
        with self.connect() as db:
            db.execute("UPDATE cp_interviews SET status='completed',completed_at=? WHERE id=?", (iso(now()), iid))
        self.send_fixture()
        self.assertEqual(1, self.routes.process_pending()['processed'])
        with self.routes.hh.db(write=True) as db:
            row = db.execute('SELECT id,scope FROM cp_invitations').fetchone()
            self.assertEqual('claim', row[1])
            self.assertTrue(self.routes.invitation_current(db, row[0], cid))
            db.execute('UPDATE cp_candidates SET email=? WHERE id=?', ('activated@example.test', cid))
            self.assertFalse(self.routes.invitation_current(db, row[0], cid))

    def test_revoked_invite_not_sent_and_rule_disable_cancels_queue(self):
        conn, cid = self.ready()
        rule = self.rule(conn)
        self.book(cid)
        self.assertEqual(1, self.routes.enqueue_events())
        self.http('rules/disable', {'ruleId': rule['ruleId']})
        self.assertEqual(0, self.routes.process_pending()['processed'])
        with self.connect() as db:
            self.assertEqual('cancelled', db.execute('SELECT status FROM hh_outbox').fetchone()[0])

    def test_missing_chat_rolls_back_new_invitation(self):
        conn, cid = self.ready()
        self.rule(conn)
        self.book(cid)
        with self.connect() as db:
            db.execute('UPDATE hh_negotiations SET chat_id=NULL')
        self.assertEqual(0, self.routes.process_pending()['processed'])
        with self.connect() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM cp_invitations').fetchone())
            self.assertEqual('invitation_unavailable', db.execute('SELECT outcome FROM hh_dispatch_events').fetchone()[0])

    def test_ambiguous_audience_does_not_send_to_multiple_hh_chats(self):
        conn, cid = self.ready()
        self.rule(conn)
        self.book(cid)
        with self.connect() as db:
            db.execute("""INSERT INTO hh_negotiations SELECT connection_id,'41',vacancy_id,resume_id,'51',state,cv_status,synced FROM hh_negotiations WHERE external_id='40'""")
        self.assertEqual(0, self.routes.process_pending()['processed'])
        with self.connect() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM cp_invitations').fetchone())
            self.assertEqual([('ambiguous_audience',), ('ambiguous_audience',)], db.execute('SELECT outcome FROM hh_dispatch_events').fetchall())

    def test_worker_disabled_even_with_connected_account(self):
        conn, cid = self.ready()
        self.rule(conn)
        self.book(cid)
        self.routes.config = replace(self.config, sending_enabled=False)
        self.assertIsNone(self.routes.start_worker())
        self.assertEqual(0, self.routes.process_pending()['processed'])
        with self.connect() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM cp_invitations').fetchone())

    def test_copy_callback_reuses_sent_link_and_checks_scope_hash_and_revocation(self):
        conn, cid = self.ready()
        self.rule(conn)
        self.book(cid)
        self.send_fixture()
        self.routes.process_pending()
        with self.connect() as db:
            original = db.execute('SELECT invitation_url FROM hh_outbox').fetchone()[0]
            same = self.routes.existing_invitation(db, cid, 'call')
            self.assertEqual(original, same['url'])
            self.assertIsNone(self.routes.existing_invitation(db, cid, 'claim'))
            self.assertIsNone(self.routes.existing_invitation(db, 'other-person', 'call'))
            expires = db.execute('SELECT expires FROM cp_invitations').fetchone()[0]
            db.execute('UPDATE cp_invitations SET expires=?', (int(self.time) - 1,))
            self.assertIsNone(self.routes.existing_invitation(db, cid, 'call'))
            db.execute('UPDATE cp_invitations SET expires=?', (expires,))
            db.execute("UPDATE cp_interviews SET status='completed'")
            self.assertIsNone(self.routes.existing_invitation(db, cid, 'call'))
            db.execute("UPDATE cp_interviews SET status='booked'")
            db.execute("UPDATE cp_invitations SET token_hash='invalid-hash'")
            self.assertIsNone(self.routes.existing_invitation(db, cid, 'call'))
            token = parse_qs(urlsplit(original).fragment)['invite'][0]
            db.execute('UPDATE cp_invitations SET token_hash=?,revoked_at=?', (hashlib.sha256(token.encode()).hexdigest(), iso(now())))
            self.assertIsNone(self.routes.existing_invitation(db, cid, 'call'))


if __name__ == '__main__':
    unittest.main()
