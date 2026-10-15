"""Unified initial employer answers via local HTTP, temp DB and fake provider."""
import concurrent.futures
import datetime as dt
import json
import unittest
import uuid
from unittest.mock import patch

import employer_access
import employer_store
import employer_workflow
from domain import iso, now
import test_employer_acceptance as fixture


class EmployerReviewResponseTest(unittest.TestCase):
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

    def ready(self):
        employer = self.employer()
        vacancy = self.vacancy(employer)
        person, aid = self.accepted(employer, vacancy)
        return employer, vacancy, person, aid

    def command(self, employer, vacancy, aid, decision='invite', **changes):
        state = self.state(employer, vacancy)
        body = {'source': 'application', 'id': aid, 'vacancyId': vacancy['id'],
                'generation': state['generation'], 'decision': decision, 'requestId': uuid.uuid4().hex,
                'expectedRevision': state['candidates'][aid]['workflowRevision']}
        if decision == 'reject':
            body['rejectionAnswers'] = {'reason': 'Другой опыт', 'missing': 'Сложные продажи', 'improve': 'Примеры сделок'}
        return {**body, **changes}

    def counts(self):
        tables = ('cp_candidates', 'cp_applications', 'cp_interviews', 'cp_meetings', 'cp_consent_log',
                  'cp_employer_stages', 'cp_workflow_messages', 'cp_workflow_events', 'cn_outbox')
        with self.app.connect() as db:
            return {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in tables}

    def test_invite_is_durable_once_only_intent_without_onboarding_meeting_or_message(self):
        employer, vacancy, person, aid = self.ready()
        command = self.command(employer, vacancy, aid)
        counts = self.counts()
        with self.app.connect() as db:
            profile = db.execute('SELECT profile FROM cp_candidates WHERE id=?', (person['id'],)).fetchone()[0]
        result = self.ok('employer/review/respond', command, token=employer['token'])
        self.assertEqual(result['response']['decision'], 'invite')
        self.assertIsNone(result['reviewRejection'])
        stored = result['state']['candidates'][aid]
        self.assertEqual(stored['reviewState'], 'proposed')
        self.assertIsNone(stored['meeting'])
        self.assertEqual(stored['reviewResponse'], result['response'])
        self.assertEqual(stored['workflowRevision'], command['expectedRevision'] + 1)
        self.assertEqual([p['id'] for p in result['state']['invitationRequests']], [aid])
        self.assertEqual(counts, self.counts())
        repeated = self.ok('employer/review/respond', command, token=employer['token'])
        self.assertEqual(repeated, result)
        with self.app.connect() as db:
            self.assertEqual(db.execute('SELECT profile FROM cp_candidates WHERE id=?', (person['id'],)).fetchone()[0], profile)
        for decision in ('invite', 'reject'):
            retry = self.command(employer, vacancy, aid, decision)
            self.assertEqual(self.request('employer/review/respond', retry, token=employer['token'])[0], 409)
        self.assertEqual(self.request('employer/review/respond', {**command, 'decision': 'reject',
            'rejectionAnswers': {'reason': 'Changed key'}}, token=employer['token'])[0], 409)
        self.assertEqual(counts, self.counts())
        self.assertEqual(self.state(employer, vacancy)['candidates'][aid]['reviewResponse'], result['response'])
        old_endpoint = self.action_body(employer, vacancy, aid, 'review.reject')
        self.assertEqual(self.request('employer/action', old_endpoint, token=employer['token'])[0], 409)
        old_decision = self.action_body(employer, vacancy, aid, 'decision', value='declined',
            expectedRevision=stored['workflowRevision'])
        self.assertEqual(self.request('employer/action', old_decision, token=employer['token'])[0], 409)
        self.assertEqual(counts, self.counts())
        self.assertEqual(self.state(employer, vacancy)['candidates'][aid]['reviewResponse'], result['response'])

    def test_invitation_remains_exact_until_real_meeting_proposal_selects_application(self):
        employer, vacancy, person, aid = self.ready()
        another_vacancy = self.vacancy(employer, 'Другая вакансия')
        _, another_aid = self.accepted(employer, another_vacancy, person)
        result = self.ok('employer/review/respond', self.command(employer, vacancy, aid), token=employer['token'])
        self.assertIsNone(self.state(employer, another_vacancy)['candidates'][another_aid]['reviewResponse'])
        meeting = self.propose(employer, vacancy, aid)
        state = self.state(employer, vacancy)
        self.assertEqual(state['candidates'][aid]['reviewState'], 'selected')
        self.assertEqual(state['candidates'][aid]['meeting']['id'], meeting['id'])
        self.assertEqual(state['candidates'][aid]['reviewResponse'], result['response'])
        self.assertEqual(state['invitationRequests'], [])
        self.assertIsNone(self.state(employer, another_vacancy)['candidates'][another_aid]['meeting'])

    def test_rejection_receipt_survives_reload_and_revocation_without_private_profile(self):
        employer, vacancy, person, aid = self.ready()
        outsider = self.employer('Другой кабинет')
        command = self.command(employer, vacancy, aid, 'reject')
        result = self.ok('employer/review/respond', command, token=employer['token'])
        self.assertEqual(result['response']['decision'], 'reject')
        self.assertNotIn(aid, result['state']['candidates'])
        receipt = result['state']['reviewResponses']['application:' + aid]
        self.assertEqual(receipt['name'], 'Кандидат')
        self.assertEqual(receipt['reviewRejection'], result['reviewRejection'])
        self.assertEqual(set(receipt), {'source', 'id', 'applicationId', 'candidateId', 'vacancyId',
                                       'name', 'decision', 'createdAt', 'reviewRejection'})
        self.assertEqual((receipt['id'], receipt['applicationId'], receipt['candidateId'], receipt['vacancyId']),
                         (aid, aid, person['id'], vacancy['id']))
        self.assertEqual(self.ok('employer/review/respond', command, token=employer['token']), result)
        candidate = self.ok('candidate/state', cookie=person['cookie'])
        self.assertNotIn(command['rejectionAnswers']['reason'], json.dumps(candidate, ensure_ascii=False))
        self.ok('candidate/profile', {'consentReuse': False, 'autoApprove': False}, cookie=person['cookie'])
        reloaded = self.state(employer, vacancy)
        self.assertNotIn(aid, reloaded['candidates'])
        self.assertEqual(reloaded['reviewResponses']['application:' + aid], receipt)
        self.assertEqual(self.ok('employer/review/respond', command, token=employer['token'])['response'], result['response'])
        identity = {'applicationId': aid, 'vacancyId': vacancy['id'], 'generation': command['generation']}
        self.assertEqual(self.request('employer/screening/resume', identity, token=employer['token'])[0], 404)
        self.assertNotIn('application:' + aid, self.state(outsider)['reviewResponses'])

    def test_unified_identity_revision_validation_and_privacy_guards(self):
        employer, vacancy, person, aid = self.ready()
        outsider = self.employer('Без доступа')
        other_vacancy = self.vacancy(employer, 'Другая')
        command = self.command(employer, vacancy, aid)
        for changes, status in [({'id': person['id']}, 404), ({'vacancyId': other_vacancy['id']}, 404),
                                ({'generation': 'stale'}, 409), ({'expectedRevision': True}, 400),
                                ({'expectedRevision': -1}, 400), ({'requestId': ''}, 400),
                                ({'decision': 'sent'}, 400), ({'source': 'legacy'}, 400),
                                ({'rejectionAnswers': {'reason': 'not needed'}}, 400),
                                ({'unexpected': True}, 400)]:
            with self.subTest(changes=changes):
                self.assertEqual(self.request('employer/review/respond', {**command, **changes}, token=employer['token'])[0], status)
        self.assertEqual(self.request('employer/review/respond', command, token=outsider['token'])[0], 404)
        self.assertEqual(self.request('employer/review/respond', command, cookie=person['cookie'])[0], 401)
        for answers in ({'reason': ''}, {'reason': 'x' * 4001}, {'reason': 'line\x00control'}, {'reason': 'x', 'extra': 'x'}):
            self.assertEqual(self.request('employer/review/respond', {**command, 'decision': 'reject',
                'rejectionAnswers': answers}, token=employer['token'])[0], 400)
        self.act(employer, vacancy, aid, 'screening.comment.add', interviewId=person['initialId'],
                 seconds=0, text='Новая заметка', expectedRevision=command['expectedRevision'])
        self.assertEqual(self.request('employer/review/respond', command, token=employer['token'])[0], 409)
        self.assertIsNone(self.state(employer, vacancy)['candidates'][aid]['reviewResponse'])

    def test_closed_or_archived_contexts_cannot_record_new_answer(self):
        employer, vacancy, _, aid = self.ready()
        command = self.command(employer, vacancy, aid)
        with self.app.connect() as db:
            body = json.loads(db.execute('SELECT body FROM cp_vacancies WHERE id=?', (vacancy['id'],)).fetchone()[0])
            db.execute('UPDATE cp_vacancies SET body=? WHERE id=?', (json.dumps({**body, 'status': 'closed'}), vacancy['id']))
        self.assertEqual(self.request('employer/review/respond', command, token=employer['token'])[0], 409)
        with self.app.connect() as db:
            db.execute('UPDATE cp_vacancies SET body=? WHERE id=?', (json.dumps(body), vacancy['id']))
            db.execute("UPDATE cp_applications SET archived_at='2026-01-01T00:00:00Z' WHERE id=?", (aid,))
        self.assertEqual(self.request('employer/review/respond', command, token=employer['token'])[0], 409)
        with self.app.connect() as db:
            self.assertIsNone(db.execute('SELECT invitation_requested_at FROM em_reviews WHERE application_id=?', (aid,)).fetchone()[0])

    def test_request_key_is_scoped_to_exact_answer_and_state_reads_do_not_write(self):
        employer, vacancy, _, aid = self.ready()
        _, other_aid = self.accepted(employer, vacancy)
        command = self.command(employer, vacancy, aid)
        self.ok('employer/review/respond', command, token=employer['token'])
        other = self.command(employer, vacancy, other_aid, requestId=command['requestId'])
        self.assertEqual(self.request('employer/review/respond', other, token=employer['token'])[0], 409)
        with self.app.connect() as db:
            principal = employer_access.authenticate(db, {'Authorization': 'Bearer ' + employer['token']})
            before = db.total_changes
            for _ in range(3):
                state = self.app.EMPLOYER_ROUTES._state(db, principal, vacancy['id'])
            self.assertEqual(before, db.total_changes)
            self.assertIsNone(state['candidates'][other_aid]['reviewResponse'])
            employer_store.init(db)
            employer_store.init(db)
            self.assertEqual(len([row for row in db.execute('PRAGMA table_info(em_reviews)')
                                 if row[1] == 'invitation_requested_at']), 1)

    def test_dated_invite_atomically_creates_exact_pending_meeting_and_replays_without_duplicates(self):
        employer, vacancy, person, aid = self.ready()
        command = self.command(employer, vacancy, aid, meetingStart=iso(self.tick))
        result = self.ok('employer/review/respond', command, token=employer['token'])
        stored = result['state']['candidates'][aid]
        meeting = stored['meeting']
        self.assertEqual(stored['reviewState'], 'selected')
        self.assertEqual((meeting['status'], meeting['start']), ('pending', iso(self.tick)))
        self.assertEqual(result['response'], {'decision': 'invite', 'createdAt': result['response']['createdAt'],
                         'meetingStart': meeting['start'], 'meetingStatus': 'pending'})
        self.assertEqual([item['meeting']['id'] for item in result['state']['meetings']], [meeting['id']])
        self.assertIn(meeting['id'], [stage['meetingId'] for stage in stored['stages'] if stage['kind'] == 'meeting'])
        self.assertEqual(result['state']['invitationRequests'], [])
        candidate = self.ok('candidate/state', cookie=person['cookie'])
        candidate_app = next(app for app in candidate['applications'] if app['id'] == aid)
        self.assertEqual(candidate_app['meeting']['id'], meeting['id'])
        before = self.counts()
        with patch.object(employer_workflow, 'now', return_value=self.tick + dt.timedelta(days=1)):
            self.assertEqual(self.ok('employer/review/respond', command, token=employer['token']), result)
        self.assertEqual(before, self.counts())
        with self.app.connect() as db:
            self.assertEqual(db.execute('SELECT invitation_meeting_id FROM em_reviews WHERE application_id=?',
                                        (aid,)).fetchone()[0], meeting['id'])
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.ok('candidate/meeting/confirm', {'applicationId': aid, 'meetingId': meeting['id'],
                                            'revision': meeting['revision']}, cookie=person['cookie'])
        self.assertEqual(self.state(employer, vacancy)['candidates'][aid]['reviewResponse']['meetingStatus'], 'confirmed')

    def test_saved_invite_gains_time_once_and_preserves_original_answer_and_history_on_reschedule(self):
        employer, vacancy, person, aid = self.ready()
        old_command = self.command(employer, vacancy, aid)
        first = self.ok('employer/review/respond', old_command, token=employer['token'])
        command = self.command(employer, vacancy, aid, meetingStart=iso(self.tick))
        result = self.ok('employer/review/respond', command, token=employer['token'])
        self.assertEqual(result['response']['createdAt'], first['response']['createdAt'])
        self.assertEqual(self.ok('employer/review/respond', old_command, token=employer['token'])['response'], result['response'])
        changed = self.command(employer, vacancy, aid, meetingStart=iso(self.tick + dt.timedelta(minutes=30)))
        self.assertEqual(self.request('employer/review/respond', changed, token=employer['token'])[0], 409)
        first_meeting = result['state']['candidates'][aid]['meeting']
        next_meeting = self.propose(employer, vacancy, aid, minutes=30, previous=first_meeting)
        current = self.state(employer, vacancy)['candidates'][aid]
        self.assertEqual(current['reviewResponse']['meetingStart'], next_meeting['start'])
        self.assertEqual(current['reviewResponse']['meetingStatus'], 'pending')
        self.assertEqual([(stage['meetingId'], stage['status']) for stage in current['stages']
                          if stage['kind'] == 'meeting'], [(first_meeting['id'], 'cancelled'), (next_meeting['id'], 'pending')])
        self.act(employer, vacancy, aid, 'meeting.cancel', meetingId=next_meeting['id'], revision=next_meeting['revision'])
        self.assertEqual(self.state(employer, vacancy)['candidates'][aid]['reviewResponse']['meetingStatus'], 'cancelled')

    def test_busy_slot_rolls_back_initial_answer_and_dated_retry_can_choose_another_slot(self):
        employer, vacancy, _, aid = self.ready()
        _, occupied_aid = self.accepted(employer, vacancy)
        self.propose(employer, vacancy, occupied_aid)
        command = self.command(employer, vacancy, aid, meetingStart=iso(self.tick))
        before = self.counts()
        self.assertEqual(self.request('employer/review/respond', command, token=employer['token'])[0], 409)
        self.assertEqual(before, self.counts())
        current = self.state(employer, vacancy)['candidates'][aid]
        self.assertIsNone(current['reviewResponse'])
        self.assertEqual(current['workflowRevision'], command['expectedRevision'])
        with self.app.connect() as db:
            self.assertEqual(db.execute('SELECT invitation_requested_at,invitation_meeting_id FROM em_reviews WHERE application_id=?',
                                        (aid,)).fetchone(), (None, None))
            self.assertIsNone(db.execute('SELECT 1 FROM em_route_commands WHERE request_id=?', (command['requestId'],)).fetchone())
            self.assertIsNone(db.execute('SELECT 1 FROM em_store_commands WHERE request_id=?', (command['requestId'],)).fetchone())
        result = self.ok('employer/review/respond', {**command, 'meetingStart': iso(self.tick + dt.timedelta(minutes=30))},
                         token=employer['token'])
        self.assertEqual(result['response']['meetingStatus'], 'pending')

    def test_dated_invite_preserves_availability_consent_and_validation_guards(self):
        employer, vacancy, person, aid = self.ready()
        command = self.command(employer, vacancy, aid)
        for value in (None, '', 'not-time', '2026-11-10T13:00:00', iso(now() - dt.timedelta(days=1)),
                      iso(now() + dt.timedelta(days=181))):
            with self.subTest(value=value):
                self.assertEqual(self.request('employer/review/respond', {**command, 'meetingStart': value},
                                              token=employer['token'])[0], 400)
        reject = self.command(employer, vacancy, aid, 'reject', meetingStart=iso(self.tick))
        self.assertEqual(self.request('employer/review/respond', reject, token=employer['token'])[0], 400)
        outside = {**command, 'meetingStart': iso(self.tick + dt.timedelta(days=1))}
        self.assertEqual(self.request('employer/review/respond', outside, token=employer['token'])[0], 409)
        self.assertIsNone(self.state(employer, vacancy)['candidates'][aid]['reviewResponse'])
        self.ok('candidate/profile', {'consentReuse': False, 'autoApprove': False}, cookie=person['cookie'])
        self.assertEqual(self.request('employer/review/respond', {**command, 'meetingStart': iso(self.tick)},
                                      token=employer['token'])[0], 404)
        with self.app.connect() as db:
            self.assertIsNone(db.execute('SELECT invitation_requested_at FROM em_reviews WHERE application_id=?', (aid,)).fetchone()[0])

    def test_parallel_same_time_proposals_create_one_meeting_and_leave_loser_unanswered(self):
        employer, vacancy, _, aid = self.ready()
        _, other_aid = self.accepted(employer, vacancy)
        commands = [self.command(employer, vacancy, app_id, meetingStart=iso(self.tick)) for app_id in (aid, other_aid)]
        def attempt(command):
            return command['id'], self.request('employer/review/respond', command, token=employer['token'])[0]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, commands))
        self.assertEqual(sorted(status for _, status in results), [200, 409])
        state = self.state(employer, vacancy)
        for app_id, status in results:
            self.assertEqual(state['candidates'][app_id]['reviewState'], 'selected' if status == 200 else 'proposed')
            self.assertEqual(bool(state['candidates'][app_id]['reviewResponse']), status == 200)
        self.assertEqual(len(state['meetings']), 1)


if __name__ == '__main__':
    unittest.main()
