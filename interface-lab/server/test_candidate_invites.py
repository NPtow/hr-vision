"""Prepared CV -> isolated guest call -> explicit consent -> same-person account.

Provider responses are fakes; these tests prove API/state boundaries, not a saved
Daily recording. The live provider must still be checked during deployment QA.
"""
import base64
import concurrent.futures
import datetime as dt
import hashlib
import json
import sqlite3
import threading
import unittest
from urllib.parse import urlsplit, parse_qs

import candidate_invites as invites
import candidate_notifications as notifications
from candidate_portal import CandidatePortal, profile_value, password_matches
from domain import Problem, iso, now
import test_candidate_portal as fixture


CV = ('Анна Миронова\nГород: Москва\nЖелаемая должность: Менеджер по работе с клиентами\n'
      'Опыт работы: пять лет сопровождала клиентов, проводила переговоры и готовила коммерческие предложения. '
      'Развивала продажи, обучала коллег и использовала CRM. Образование: высшее экономическое. '
      'Навыки: работа с возражениями, планирование и анализ данных.')


class RecordingMedia(fixture.FakeMedia):
    def __init__(self):
        super().__init__()
        self.permissions, self.actions = [], []
        self.fail_transcription = False

    def room(self, mid, *, recordable=True):
        self.permissions.append(recordable)
        return super().room(mid, recordable=recordable)

    def request(self, path, body=None):
        self.actions.append((path, body))
        if path.endswith('/transcription/start') and self.fail_transcription:
            raise Problem('Provider unavailable', 502)
        if path.endswith('/start'):
            return {'status': 'sent'}
        return super().request(path, body)


class CandidateInvitationTest(unittest.TestCase):
    setUp = fixture.CandidatePortalTest.setUp
    tearDown = fixture.CandidatePortalTest.tearDown
    connect = fixture.CandidatePortalTest.connect
    payload = fixture.CandidatePortalTest.payload
    call = fixture.CandidatePortalTest.call
    not_manager = staticmethod(fixture.CandidatePortalTest.not_manager)
    register = fixture.CandidatePortalTest.register
    slot = fixture.CandidatePortalTest.slot
    assert_problem = fixture.CandidatePortalTest.assert_problem

    def prepared(self, **extra):
        result, _ = self.call('recruiter/candidates', {'filename': 'anna.txt',
            'contentBase64': base64.b64encode(CV.encode()).decode(), **extra}, manager=True)
        return result['candidate']

    def invitation(self, person, scope):
        result, _ = self.call('recruiter/invitations', {'candidateId': person['id'], 'scope': scope}, manager=True)
        token = parse_qs(urlsplit(result['url']).fragment)['invite' if scope == 'call' else 'claim'][0]
        self.assertNotIn(token, urlsplit(result['url']).query)
        return token, result

    def booked(self):
        person = self.prepared()
        slot = self.slot()
        self.call('recruiter/interview/book', {'candidateId': person['id'], 'slotId': slot['id']}, manager=True)
        return person

    def started(self):
        person = self.booked()
        self.call('recruiter/interview/join', {'candidateId': person['id']}, manager=True)
        self.call('recruiter/interview/start', {'candidateId': person['id'], 'oralConsent': True}, manager=True)
        return person

    def test_prepared_profile_has_cv_but_no_fake_account_or_consent(self):
        person = self.prepared(contactEmail='ANNA@example.test')
        self.assertIsNone(person['email'])
        self.assertFalse(person['accountActive'])
        self.assertFalse(person['consentReuse'])
        self.assertFalse(person['autoApprove'])
        self.assertEqual(person['intake']['contactEmail'], 'anna@example.test')
        self.assertEqual(person['resume']['filename'], 'anna.txt')
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT email,password_hash FROM cp_candidates').fetchone(), (None, None))
            self.assertEqual(db.execute('SELECT count(*) FROM cp_consent_log').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM cp_sessions').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM fake_events').fetchone()[0], 0)
        self.assertFalse(password_matches('test-passphrase-123', None))
        self.assert_problem(401, 'candidate/login', {'email': 'anna@example.test', 'password': 'test-passphrase-123'})

    def test_prepared_upload_requires_manager_and_real_readable_cv(self):
        self.assert_problem(401, 'recruiter/candidates', {'filename': 'x.txt', 'contentBase64': 'eA=='})
        self.assert_problem(422, 'recruiter/candidates', {'filename': 'x.txt', 'contentBase64': 'eA=='}, manager=True)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM cp_candidates').fetchone()[0], 0)

    def test_shared_slot_atomic_between_invited_and_selfservice(self):
        prepared = self.prepared()
        _, cookie = self.register()
        slot = self.slot()
        barrier = threading.Barrier(2)
        def book(is_recruiter):
            barrier.wait()
            try:
                self.call('recruiter/interview/book' if is_recruiter else 'candidate/book',
                          {'slotId': slot['id'], **({'candidateId': prepared['id']} if is_recruiter else {})},
                          cookie='' if is_recruiter else cookie, manager=is_recruiter)
                return 200
            except Problem as error:
                return error.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(book, (True, False)))
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(self.call('candidate/slots', cookie=cookie)[0]['slots'], [])
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM cp_interviews').fetchone()[0], 1)

    def test_guest_link_is_narrow_and_does_not_start_or_consent_recording(self):
        self.media = RecordingMedia()
        self.portal.media = self.media
        person = self.booked()
        token, _ = self.invitation(person, 'call')
        state, _ = self.call('candidate/guest/state', {'token': token})
        self.assertEqual(set(state), {'name', 'interview', 'videoConfigured'})
        self.assertNotIn('room', state['interview'])
        result, headers = self.call('candidate/guest/join', {'token': token})
        self.assertEqual(headers, {})
        self.assertIn('token', result)
        self.assertEqual(self.media.permissions, [False])
        self.assertFalse(self.media.tokens[0]['is_owner'])
        self.assertIs(self.media.tokens[0]['enable_prejoin_ui'], False)
        self.assertFalse(self.media.tokens[0]['start_cloud_recording'])
        self.assertFalse(self.media.tokens[0]['auto_start_transcription'])
        self.assertFalse(any(path.endswith('/start') for path, _ in self.media.actions))
        self.assert_problem(401, 'candidate/state')
        self.assert_problem(401, 'candidate/resume')
        self.assert_problem(410, 'candidate/claim/resume', {'token': token})
        self.assert_problem(410, 'candidate/claim', {'token': token, 'email': 'x@example.test', 'password': 'test-passphrase-123'})
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM cp_media_consents').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM cp_consent_log').fetchone()[0], 0)

    def test_call_rotation_revocation_expiry_and_no_query_tokens(self):
        person = self.booked()
        first, _ = self.invitation(person, 'call')
        second, _ = self.invitation(person, 'call')
        self.assert_problem(410, 'candidate/guest/state', {'token': first})
        self.call('recruiter/invitations/revoke', {'candidateId': person['id'], 'scope': 'call'}, manager=True)
        self.assert_problem(410, 'candidate/guest/join', {'token': second})
        third, _ = self.invitation(person, 'call')
        with self.connect() as db:
            db.execute('UPDATE cp_invitations SET expires=0 WHERE token_hash=?', (hashlib.sha256(third.encode()).hexdigest(),))
        self.assert_problem(410, 'candidate/guest/state', {'token': third})
        self.assert_problem(405, 'candidate/guest/state?token=' + third, method='GET')

    def test_guest_cannot_change_target_person(self):
        person = self.booked()
        other = self.prepared()
        token, _ = self.invitation(person, 'call')
        state, _ = self.call('candidate/guest/state', {'token': token, 'candidateId': other['id']})
        with self.connect() as db:
            self.assertEqual(state['interview']['id'], self.portal.interview(db, person['id'])['id'])

    def test_explicit_recording_preserves_oral_attestation_and_partial_retry(self):
        self.media = RecordingMedia()
        self.portal.media = self.media
        person = self.booked()
        self.assert_problem(400, 'recruiter/interview/start', {'candidateId': person['id']}, manager=True)
        self.assert_problem(409, 'recruiter/interview/start', {'candidateId': person['id'], 'oralConsent': True}, manager=True)
        self.call('recruiter/interview/join', {'candidateId': person['id']}, manager=True)
        self.assertEqual(self.media.permissions, [False])
        self.assertFalse(self.media.tokens[-1]['is_owner'])
        self.assertFalse(self.media.tokens[-1]['permissions']['canAdmin'])
        self.assertNotIn('enable_prejoin_ui', self.media.tokens[-1])
        self.assertFalse(self.media.tokens[-1]['enable_recording_ui'])
        self.assertFalse(any(path.endswith('/start') for path, _ in self.media.actions))
        self.media.fail_transcription = True
        self.assert_problem(502, 'recruiter/interview/start', {'candidateId': person['id'], 'oralConsent': True}, manager=True)
        state = self.call('recruiter/state', manager=True)[0]['candidates'][0]
        control = state['interview']['recording']
        self.assertEqual(control, {'consentReceived': True, 'consentSource': 'recruiter_oral',
            'startRequested': True, 'recordingRequested': True, 'transcriptionRequested': False})
        self.assertFalse(state['consentReuse'])
        self.media.fail_transcription = False
        self.call('recruiter/interview/start', {'candidateId': person['id'], 'oralConsent': True}, manager=True)
        self.assertEqual(sum(path.endswith('/recordings/start') for path, _ in self.media.actions), 1)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM cp_consent_log').fetchone()[0], 0)
        token, _ = self.invitation(person, 'call')
        self.call('candidate/guest/join', {'token': token})
        self.assertTrue(self.media.permissions[-1])

    def test_claim_activates_same_person_preserves_call_cv_and_session(self):
        person = self.started()
        call_token, _ = self.invitation(person, 'call')
        claim_token, _ = self.invitation(person, 'claim')
        with self.connect() as db:
            iid = self.portal.interview(db, person['id'])['id']
        preview, _ = self.call('candidate/claim/state', {'token': claim_token})
        self.assertFalse(preview['claimed'])
        self.assertNotIn('internalProfile', preview['candidate'])
        self.assertNotIn('applications', preview['candidate'])
        cv, _ = self.call('candidate/claim/resume', {'token': claim_token})
        self.assertEqual(base64.b64decode(cv['contentBase64']).decode(), CV)
        activated, headers = self.call('candidate/claim', {'token': claim_token,
            'email': 'claimed@example.test', 'password': 'test-passphrase-123', 'consentReuse': True,
            'profile': {'about': 'Уточнённый опыт'}})
        self.assertEqual(activated['id'], person['id'])
        self.assertTrue(activated['accountActive'])
        self.assertEqual(activated['interview']['id'], iid)
        self.assertEqual(activated['about'], 'Уточнённый опыт')
        cookie = headers['Set-Cookie'].split(';')[0]
        self.assertEqual(self.call('candidate/state', cookie=cookie)[0]['id'], person['id'])
        self.call('candidate/guest/join', {'token': call_token})
        self.assertEqual(self.call('candidate/claim/state', {'token': claim_token})[0], {'claimed': True, 'candidate': None})
        self.assert_problem(409, 'candidate/claim/resume', {'token': claim_token})
        self.assert_problem(409, 'candidate/claim', {'token': claim_token})
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM cp_candidates').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM cp_interviews').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT version FROM cp_consent_log').fetchone()[0], 'candidate-claim-v1')

    def test_claim_requires_own_consent_and_never_takes_existing_email(self):
        existing, _ = self.register(email='existing@example.test')
        invited = self.started()
        token, _ = self.invitation(invited, 'claim')
        self.assert_problem(400, 'candidate/claim', {'token': token, 'email': 'new@example.test', 'password': 'test-passphrase-123'})
        self.assert_problem(409, 'candidate/claim', {'token': token, 'email': existing['email'],
                            'password': 'test-passphrase-123', 'consentReuse': True})
        with self.connect() as db:
            self.assertFalse(self.portal.person(db, invited['id'])['accountActive'])
            self.assertEqual(self.portal.person(db, existing['id'])['email'], 'existing@example.test')
        self.assertFalse(self.call('candidate/claim/state', {'token': token})[0]['claimed'])
        self.assert_problem(410, 'candidate/guest/join', {'token': token})

    def test_claim_not_available_before_interview_and_call_closed_after_complete(self):
        person = self.booked()
        self.assert_problem(409, 'recruiter/invitations', {'candidateId': person['id'], 'scope': 'claim'}, manager=True)
        token, _ = self.invitation(person, 'call')
        self.call('recruiter/interview/join', {'candidateId': person['id']}, manager=True)
        self.call('recruiter/interview/start', {'candidateId': person['id'], 'oralConsent': True}, manager=True)
        self.call('recruiter/interview/complete', {'candidateId': person['id']}, manager=True)
        self.assert_problem(409, 'candidate/guest/join', {'token': token})
        self.assertEqual(self.call('candidate/guest/state', {'token': token})[0]['interview']['status'], 'completed')
        self.invitation(person, 'claim')

    def test_real_notification_module_waits_for_account_activation(self):
        with self.connect() as db:
            notifications.init(db)
        self.portal.notifications = notifications
        person = self.started()
        self.call('recruiter/interview/complete', {'candidateId': person['id']}, manager=True)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM cn_recipients').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM cn_outbox').fetchone()[0], 0)
        token, _ = self.invitation(person, 'claim')
        self.call('candidate/claim', {'token': token, 'email': 'claimed@example.test',
                                    'password': 'test-passphrase-123', 'consentReuse': True})
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT candidate_id,email_verified FROM cn_recipients').fetchone(), (person['id'], 0))
            self.assertGreater(db.execute('SELECT count(*) FROM cn_outbox').fetchone()[0], 0)

    def test_explicit_retry_after_provider_event_resends_only_failed_capture(self):
        self.media = RecordingMedia()
        self.portal.media = self.media
        person = self.started()
        self.call('recruiter/interview/start', {'candidateId': person['id'], 'oralConsent': True}, manager=True)
        self.assertEqual(sum(path.endswith('/recordings/start') for path, _ in self.media.actions), 1)
        self.call('recruiter/interview/start', {'candidateId': person['id'], 'oralConsent': True,
                                              'retryRecording': True}, manager=True)
        self.assertEqual(sum(path.endswith('/recordings/start') for path, _ in self.media.actions), 2)
        self.assertEqual(sum(path.endswith('/transcription/start') for path, _ in self.media.actions), 1)

    def test_availability_endpoint_materializes_into_same_calendar(self):
        begin = (self.tick + dt.timedelta(days=2)).date().isoformat()
        state, _ = self.call('recruiter/availability', {'recruiterName': 'Никита', 'startDate': begin,
            'weekdays': [0, 1, 2, 3, 4], 'startTime': '16:00', 'endTime': '17:00', 'timezone': 'Europe/Moscow'}, manager=True)
        self.assertEqual(len(state['availabilityBlocks']), 1)
        self.assertEqual(len(state['slots']), 40)
        self.assertTrue(all(slot['start'][11:16] in ('13:00', '13:30') for slot in state['slots']))

    def test_hh_external_identity_is_idempotent_without_account_or_consent(self):
        with self.connect() as db:
            profile = profile_value({'name': 'Из HH'})
            a = invites.create_prepared(db, profile, source='hh', external_key='hh_resume:v1:conn:one')
            b = invites.create_prepared(db, profile, source='hh', external_key='hh_resume:v1:conn:one')
            self.assertEqual(a, b)
            self.assertEqual(db.execute('SELECT count(*) FROM cp_candidates').fetchone()[0], 1)
            self.assertIsNone(db.execute('SELECT email FROM cp_candidates').fetchone()[0])


class MigrationTest(unittest.TestCase):
    def test_nullable_credentials_migration_retains_referencing_rows_and_rollback(self):
        db = sqlite3.connect(':memory:')
        try:
            db.executescript('''PRAGMA foreign_keys=ON;
              CREATE TABLE cp_candidates(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,
                profile TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
              CREATE TABLE child(cid TEXT REFERENCES cp_candidates(id), value TEXT);
              INSERT INTO cp_candidates VALUES('p','existing@example.test','hash','{}','old','old');
              INSERT INTO child VALUES('p','session-resume-interview-reference');''')
            invites.migrate_accounts(db)
            self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT * FROM child').fetchone(), ('p', 'session-resume-interview-reference'))
            self.assertEqual(db.execute('SELECT email,password_hash FROM cp_candidates').fetchone(), ('existing@example.test', 'hash'))
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            db.execute("INSERT INTO cp_candidates VALUES ('new',NULL,NULL,'{}','new','new')")
            db.commit()
            invites.migrate_accounts(db)
            self.assertEqual(db.execute('SELECT count(*) FROM cp_candidates').fetchone()[0], 2)
        finally:
            db.close()


if __name__ == '__main__':
    unittest.main()
