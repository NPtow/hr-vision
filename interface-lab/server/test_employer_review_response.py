"""Unified initial employer answers via local HTTP, temp DB and fake provider."""
import json
import unittest
import uuid

import employer_access
import employer_store
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


if __name__ == '__main__':
    unittest.main()
