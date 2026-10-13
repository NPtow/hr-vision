"""Focused route regressions on an isolated HTTP server and fake provider."""
import concurrent.futures
import json
import threading
import unittest
import uuid
from unittest.mock import patch

import test_employer_acceptance as fixture


class EmployerRoutesTest(unittest.TestCase):
    setUpClass = classmethod(fixture.EmployerAcceptanceTest.setUpClass.__func__)
    tearDownClass = classmethod(fixture.EmployerAcceptanceTest.tearDownClass.__func__)
    setUp = fixture.EmployerAcceptanceTest.setUp
    request = fixture.EmployerAcceptanceTest.request
    ok = fixture.EmployerAcceptanceTest.ok
    employer = fixture.EmployerAcceptanceTest.employer
    vacancy = fixture.EmployerAcceptanceTest.vacancy
    state = fixture.EmployerAcceptanceTest.state
    candidate = fixture.EmployerAcceptanceTest.candidate
    batch = fixture.EmployerAcceptanceTest.batch
    accepted = fixture.EmployerAcceptanceTest.accepted
    action_body = fixture.EmployerAcceptanceTest.action_body
    act = fixture.EmployerAcceptanceTest.act
    propose = fixture.EmployerAcceptanceTest.propose
    exact = staticmethod(fixture.EmployerAcceptanceTest.exact)
    complete = fixture.EmployerAcceptanceTest.complete
    feedback = fixture.EmployerAcceptanceTest.feedback
    offer_terms = fixture.EmployerAcceptanceTest.offer_terms

    def ready(self):
        employer = self.employer()
        vacancy = self.vacancy(employer)
        person, aid = self.accepted(employer, vacancy)
        meeting = self.propose(employer, vacancy, aid)
        return employer, vacancy, person, aid, meeting

    def join(self, employer, vacancy, person, aid, meeting):
        self.ok('candidate/meeting/confirm', self.exact(aid, meeting), cookie=person['cookie'])
        self.ok('employer/room', self.action_body(employer, vacancy, aid, 'meeting.room',
            meetingId=meeting['id'], revision=meeting['revision'], consent=True), token=employer['token'])

    def test_cookie_only_existing_account_keeps_workspace_without_private_team_grant(self):
        employer = self.employer()
        accounts = self.ok('employer/accounts', cookie=employer['cookie'])
        self.assertEqual([row['id'] for row in accounts['accounts']], ['manager'])
        session = self.ok('employer/session', {}, cookie=employer['cookie'])
        self.assertEqual(session['token'], employer['token'])
        self.assertEqual(session['state']['workspaceId'], employer['workspace']['id'])
        self.assertNotEqual(session['state']['workspaceId'], 'hr-vision-team')

    def test_pending_rejection_cancels_same_meeting_and_retry_does_not_duplicate_events(self):
        employer, vacancy, person, aid, meeting = self.ready()
        command = self.action_body(employer, vacancy, aid, 'review.reject')
        self.ok('employer/action', command, token=employer['token'])
        self.ok('employer/action', command, token=employer['token'])
        self.assertNotIn(aid, self.state(employer, vacancy)['candidates'])
        with self.app.connect() as db:
            status = db.execute('SELECT status FROM cp_meetings WHERE id=?', (meeting['id'],)).fetchone()[0]
            self.assertEqual(status, 'cancelled')
            notices = [json.loads(row[0]) for row in db.execute('SELECT body FROM cp_workflow_events WHERE application_id=?', (aid,))]
            self.assertEqual(len([item for item in notices if item['type'] == 'employer.declined']), 1)
        self.assertEqual(self.request('candidate/meeting/confirm', self.exact(aid, meeting), cookie=person['cookie'])[0], 409)

    def test_sent_offer_rejection_withdraws_offer_and_keeps_immutable_terms(self):
        employer, vacancy, person, aid, meeting = self.ready()
        self.complete(employer, vacancy, person, aid, meeting)
        self.feedback(employer, vacancy, aid, meeting)
        self.act(employer, vacancy, aid, 'decision', value='pool')
        offer = self.act(employer, vacancy, aid, 'offer.save', **self.offer_terms())['candidates'][aid]['offer']
        self.act(employer, vacancy, aid, 'offer.send', version=offer['version'])
        self.act(employer, vacancy, aid, 'review.reject')
        candidate = self.ok('candidate/state', cookie=person['cookie'])
        app = next(row for row in candidate['applications'] if row['id'] == aid)
        self.assertEqual(app['workflow']['offer']['status'], 'withdrawn')
        self.assertEqual(app['workflow']['offer']['compensation'], offer['compensation'])
        self.assertEqual(self.request('candidate/workflow', {'applicationId': aid, 'action': 'offer.reply',
            'value': 'accepted', 'version': offer['version']}, cookie=person['cookie'])[0], 409)

    def test_live_rejection_is_blocked_but_owner_can_finish_after_consent_withdrawal(self):
        employer, vacancy, person, aid, meeting = self.ready()
        self.join(employer, vacancy, person, aid, meeting)
        self.assertEqual(self.request('employer/action', self.action_body(employer, vacancy, aid, 'review.reject'),
                                     token=employer['token'])[0], 409)
        self.ok('candidate/profile', {'consentReuse': False, 'autoApprove': False}, cookie=person['cookie'])
        self.assertNotIn(aid, self.state(employer, vacancy)['candidates'])
        result = self.act(employer, vacancy, aid, 'meeting.finish', meetingId=meeting['id'])
        self.assertNotIn(aid, result['candidates'])
        with self.app.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM cp_meetings WHERE id=?', (meeting['id'],)).fetchone()[0], 'completed')

    def test_live_turns_bind_to_exact_meeting_and_form_questions_when_provider_pending(self):
        employer, vacancy, person, aid, meeting = self.ready()
        self.join(employer, vacancy, person, aid, meeting)
        self.act(employer, vacancy, aid, 'meeting.joined', meetingId=meeting['id'])
        turns = [{'id': 'live-probe-turn', 'text': 'Как вы организовали работу с возражениями клиента?', 'seconds': 14}]
        command = self.action_body(employer, vacancy, aid, 'meeting.transcript', meetingId=meeting['id'], turns=turns)
        self.ok('employer/action', command, token=employer['token'])
        self.ok('employer/action', command, token=employer['token'])
        self.assertEqual(self.request('employer/action', {**command, 'requestId': uuid.uuid4().hex,
            'turns': [{**turns[0], 'text': 'Другой текст с тем же идентификатором.'}]}, token=employer['token'])[0], 409)
        with self.app.connect() as db:
            record = db.execute('SELECT turns,owner_name,joined_at FROM em_meeting_transcripts WHERE meeting_id=?',
                                (meeting['id'],)).fetchone()
            self.assertEqual(len(json.loads(record[0])), 1)
            self.assertEqual(record[1], 'Нанимающий A')
            self.assertIsNotNone(record[2])
        self.assertEqual(self.media.tokens[-1]['user_id'], 'manager')
        self.act(employer, vacancy, aid, 'meeting.finish', meetingId=meeting['id'])
        questions = self.ok('employer/feedback/questions', self.action_body(employer, vacancy, aid, 'questions',
            meetingId=meeting['id']), token=employer['token'])
        self.assertEqual(questions['status'], 'ready')
        self.assertEqual(questions['questionnaire']['questions'][0]['question'], turns[0]['text'])

    def test_revoked_session_cannot_persist_or_receive_materials_after_provider_io(self):
        employer, vacancy, person, aid, meeting = self.ready()
        self.complete(employer, vacancy, person, aid, meeting)
        command = self.action_body(employer, vacancy, aid, 'materials', meetingId=meeting['id'])
        entered, release = threading.Event(), threading.Event()
        def delayed(room, known=None):
            entered.set()
            self.assertTrue(release.wait(5))
            return {'recordings': [{'id': 'private-video', 'url': 'https://media.example/private', 'duration': 30}],
                    'transcripts': [], 'video': {'status': 'ready', 'ids': ['private-video']},
                    'transcription': {'status': 'processing', 'ids': []}, 'checkedAt': '2026-10-12T00:00:00Z'}
        with patch.object(self.media, 'materials', side_effect=delayed), concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.request, 'employer/materials', command, token=employer['token'])
            try:
                self.assertTrue(entered.wait(3))
                self.ok('employer/logout', {}, cookie=employer['cookie'])
            finally:
                release.set()
            status, result, _ = future.result()
        self.assertEqual(status, 401, result)
        self.assertNotIn('private', json.dumps(result))
        with self.app.connect() as db:
            self.assertIsNone(db.execute('SELECT body FROM cp_media_state WHERE meeting_id=?', (meeting['id'],)).fetchone())

    def test_intake_parser_does_not_hold_global_lock_and_revocation_prevents_save(self):
        employer = self.employer()
        vacancy = self.vacancy(employer)
        body = {'vacancyId': vacancy['id'], 'scope': 'sfera', 'batch': uuid.uuid4().hex,
                'entries': [{'name': 'Черновик до согласия', 'role': 'Менеджер'}]}
        entered, release = threading.Event(), threading.Event()
        module = self.app.EMPLOYER_ROUTES.intake
        original = module.prepare
        def delayed(value):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(value)
        with patch.object(module, 'prepare', side_effect=delayed), concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.request, 'employer/intake', body, token=employer['token'])
            try:
                self.assertTrue(entered.wait(3))
                self.ok('employer/logout', {}, cookie=employer['cookie'])
            finally:
                release.set()
            status, _, _ = future.result()
        self.assertEqual(status, 401)
        with self.app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_intake_candidates WHERE workspace_id=?',
                                       (employer['workspace']['id'],)).fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
