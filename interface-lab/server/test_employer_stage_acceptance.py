"""Independent stage-card acceptance against a temporary, real HTTP backend.

Run: python3 -m unittest -v test_employer_stage_acceptance
For browser QA: python3 test_employer_stage_acceptance.py --serve-fixture /tmp/stage-fixture.json
The latter creates only synthetic accounts in a TemporaryDirectory. Its JSON
contains temporary local session tokens and must never be committed. No real
Daily calls, notification delivery, HH requests, or production database writes.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import signal
import threading
import unittest
from unittest.mock import patch

import employer_projection
import test_employer_acceptance as fixture


class EmployerStageAcceptanceTest(unittest.TestCase):
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
    feedback = fixture.EmployerAcceptanceTest.feedback
    offer_terms = fixture.EmployerAcceptanceTest.offer_terms

    def person_state(self, employer, vacancy, aid):
        return self.state(employer, vacancy)['candidates'][aid]

    def stage_action(self, employer, vacancy, aid, action, **changes):
        current = self.person_state(employer, vacancy, aid)
        return self.act(employer, vacancy, aid, action,
                        expectedRevision=current['workflowRevision'], **changes)['candidates'][aid]

    def completed_meeting(self, employer, vacancy, person, aid, *, minutes=0, previous=None):
        meeting = self.propose(employer, vacancy, aid, minutes=minutes, previous=previous)
        # Only the synthetic provider clock advances; no waiting or real call.
        with patch('candidate_portal.now', return_value=self.tick + dt.timedelta(minutes=minutes - 1)):
            fixture.EmployerAcceptanceTest.complete(self, employer, vacancy, person, aid, meeting)
        return self.person_state(employer, vacancy, aid)['meeting']

    def create_test(self, employer, vacancy, aid, title):
        before = {s['id'] for s in self.person_state(employer, vacancy, aid)['stages']}
        current = self.stage_action(employer, vacancy, aid, 'test.create',
                                   title=title, instructions='Синтетическое задание ' + title)
        added = [s for s in current['stages'] if s['id'] not in before]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]['kind'], 'test')
        return added[0]

    @staticmethod
    def stages(person, kind):
        return [stage for stage in person['stages'] if stage['kind'] == kind]

    def test_repeat_histories_keep_exact_materials_and_survive_new_database_connection(self):
        employer = self.employer('Stage history')
        vacancy = self.vacancy(employer)
        person, aid = self.accepted(employer, vacancy)
        first = self.completed_meeting(employer, vacancy, person, aid)
        self.feedback(employer, vacancy, aid, first)
        self.stage_action(employer, vacancy, aid, 'stage.update', stageId='meeting:' + first['id'],
                          notes='ONLY_FIRST_MEETING', documents=[{'title': 'Первый документ', 'url': 'https://example.test/first'}])
        first_test = self.create_test(employer, vacancy, aid, 'Первое тестовое')
        self.stage_action(employer, vacancy, aid, 'test.update', testId=first_test['test']['id'],
                          status='completed', result='ONLY_FIRST_TEST')
        second = self.completed_meeting(employer, vacancy, person, aid, minutes=60, previous=first)
        self.feedback(employer, vacancy, aid, second)
        self.stage_action(employer, vacancy, aid, 'stage.update', stageId='meeting:' + second['id'],
                          notes='ONLY_SECOND_MEETING')
        second_test = self.create_test(employer, vacancy, aid, 'Второе тестовое')
        self.stage_action(employer, vacancy, aid, 'test.update', testId=second_test['test']['id'],
                          status='completed', result='ONLY_SECOND_TEST')
        self.act(employer, vacancy, aid, 'decision', value='pool')
        first_offer = self.act(employer, vacancy, aid, 'offer.save', **self.offer_terms())['candidates'][aid]['offer']
        self.act(employer, vacancy, aid, 'offer.save', version=first_offer['version'],
                 **self.offer_terms(compensation='170 000 ₽'))

        current = self.person_state(employer, vacancy, aid)
        self.assertEqual(current['candidateId'], person['id'])
        self.assertEqual(current['applicationId'], aid)
        self.assertEqual(current['vacancyId'], vacancy['id'])
        self.assertEqual(current['reviewState'], 'selected')
        self.assertEqual(current['stages'][0]['kind'], 'interview')
        self.assertEqual(len(self.stages(current, 'interview')), 1)
        self.assertEqual([s['id'] for s in current['stages'][:5]], [
            'interview:' + person['initialId'], 'meeting:' + first['id'],
            first_test['id'], 'meeting:' + second['id'], second_test['id'],
        ])
        self.assertEqual([s['kind'] for s in current['stages'][5:]], ['offer', 'offer'])
        meetings = {s['meetingId']: s for s in self.stages(current, 'meeting')}
        self.assertEqual(set(meetings), {first['id'], second['id']})
        for meeting, sentinel in ((first, 'ONLY_FIRST_MEETING'), (second, 'ONLY_SECOND_MEETING')):
            stage = meetings[meeting['id']]
            self.assertEqual(stage['notes'], sentinel)
            self.assertEqual(stage['feedback']['meetingId'], meeting['id'])
            self.assertEqual(stage['meeting']['id'], meeting['id'])
        self.assertEqual(meetings[first['id']]['documents'][0]['url'], 'https://example.test/first')
        self.assertEqual(meetings[second['id']]['documents'], [])
        tests = {s['test']['id']: s for s in self.stages(current, 'test')}
        self.assertEqual(tests[first_test['test']['id']]['test']['result'], 'ONLY_FIRST_TEST')
        self.assertEqual(tests[second_test['test']['id']]['test']['result'], 'ONLY_SECOND_TEST')
        self.assertEqual(len(current['offers']), 2)
        self.assertEqual([o['version'] for o in sorted(current['offers'], key=lambda o: o['version'])], [1, 2])
        self.assertEqual(next(o for o in current['offers'] if o['version'] == 1)['compensation'], '150 000 ₽')
        self.assertEqual(current['offer']['compensation'], '170 000 ₽')
        # Reopen SQLite independently of the handler's response/React state.
        with self.app.connect() as db:
            restored = employer_projection.build_state(db,
                {'workspaceId': employer['workspace']['id'], 'name': 'QA', 'team': False},
                self.app.CANDIDATE_PORTAL, self.media, vacancy['id'])['candidates'][aid]
        self.assertEqual(restored['stages'], current['stages'])
        self.assertEqual(restored['offers'], current['offers'])

    def test_lost_response_retry_is_one_stage_and_foreign_identity_cannot_mutate_it(self):
        employer, outsider = self.employer('Owner'), self.employer('Foreign')
        vacancy = self.vacancy(employer)
        other_vacancy = self.vacancy(employer, 'Другая собственная вакансия')
        person, aid = self.accepted(employer, vacancy)
        other_person, other_aid = self.accepted(employer, vacancy)
        _, same_person_other_application = self.accepted(employer, other_vacancy, person)
        self.completed_meeting(employer, vacancy, person, aid)
        self.completed_meeting(employer, vacancy, other_person, other_aid, minutes=60)
        self.completed_meeting(employer, other_vacancy, person, same_person_other_application, minutes=120)
        command = self.action_body(employer, vacancy, aid, 'test.create', title='Exactly once',
            expectedRevision=self.person_state(employer, vacancy, aid)['workflowRevision'])
        first = self.ok('employer/action', command, token=employer['token'])['candidates'][aid]
        # Treat the first response as lost; send the identical command again.
        again = self.ok('employer/action', command, token=employer['token'])['candidates'][aid]
        self.assertEqual(first['stages'], again['stages'])
        self.assertEqual(len(self.stages(again, 'test')), 1)
        self.assertEqual(self.request('employer/action', {**command, 'title': 'Changed payload'},
                                     token=employer['token'])[0], 409)
        stage = self.stages(again, 'test')[0]
        bad_commands = [
            (employer, vacancy, other_aid, {'testId': stage['test']['id'], 'result': 'FOREIGN'}),
            (employer, other_vacancy, same_person_other_application, {'testId': stage['test']['id'], 'result': 'FOREIGN'}),
            (outsider, vacancy, aid, {'testId': stage['test']['id'], 'result': 'FOREIGN'}),
        ]
        for actor, target_vacancy, target_app, changes in bad_commands:
            body = {**command, 'requestId': 'mismatch-' + target_app + actor['workspace']['id'],
                    'action': 'test.update', 'applicationId': target_app, 'candidate': target_app,
                    'vacancyId': target_vacancy['id'], **changes}
            body.pop('title', None)
            if actor is employer:
                target_state = self.state(actor, target_vacancy)
                body['generation'] = target_state['generation']
                body['expectedRevision'] = target_state['candidates'][target_app]['workflowRevision']
            status, _, _ = self.request('employer/action', body, token=actor['token'])
            self.assertEqual(status, 404)
            stage_body = {**body, 'action': 'stage.update', 'stageId': stage['id'],
                          'notes': 'FOREIGN', 'requestId': body['requestId'] + '-stage'}
            stage_body.pop('testId', None)
            stage_body.pop('result', None)
            self.assertEqual(self.request('employer/action', stage_body, token=actor['token'])[0], 404)
        self.assertEqual(self.person_state(employer, vacancy, aid)['stages'], again['stages'])
        self.assertEqual(self.request('employer/state?vacancyId=' + vacancy['id'], token=outsider['token'])[0], 404)
        self.assertNotIn(stage['id'], [s['id'] for s in self.person_state(
            employer, other_vacancy, same_person_other_application)['stages']])
        self.assertNotIn(stage['id'], json.dumps(self.state(outsider)))
        self.assertNotIn(stage['id'], [s['id'] for s in self.person_state(employer, vacancy, other_aid)['stages']])

    def test_offer_does_not_require_or_fabricate_feedback_and_keeps_version_guard(self):
        employer = self.employer('Optional feedback')
        vacancy = self.vacancy(employer)
        person, aid = self.accepted(employer, vacancy)
        meeting = self.completed_meeting(employer, vacancy, person, aid)
        self.assertIsNone(self.person_state(employer, vacancy, aid)['feedback'])
        offer = self.act(employer, vacancy, aid, 'offer.save', **self.offer_terms())['candidates'][aid]['offer']
        self.act(employer, vacancy, aid, 'offer.save', version=offer['version'],
                 **self.offer_terms(compensation='180 000 ₽'))
        stale = self.action_body(employer, vacancy, aid, 'offer.save', version=offer['version'],
                                 **self.offer_terms(compensation='190 000 ₽'))
        self.assertEqual(self.request('employer/action', stale, token=employer['token'])[0], 409)
        current = self.person_state(employer, vacancy, aid)
        self.assertEqual(current['decision'], 'pool')
        self.assertIsNone(current['feedback'])
        self.assertIsNone(next(s for s in current['stages'] if s.get('meetingId') == meeting['id'])['feedback'])
        with self.app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_meeting_feedback WHERE application_id=?', (aid,)).fetchone()[0], 0)

    def seed_browser(self):
        employer = self.employer('Stage browser')
        vacancy = self.vacancy(employer, 'QA · Продажи')
        empty = self.vacancy(employer, 'QA · Без выбранных')
        other_vacancy = self.vacancy(employer, 'QA · Другая вакансия')
        person, aid = self.accepted(employer, vacancy, self.candidate('Алёна Проверочная'))
        first = self.completed_meeting(employer, vacancy, person, aid)
        self.stage_action(employer, vacancy, aid, 'stage.update', stageId='meeting:' + first['id'], notes='ONLY_FIRST_MEETING')
        test = self.create_test(employer, vacancy, aid, 'Первое тестовое')
        self.stage_action(employer, vacancy, aid, 'test.update', testId=test['test']['id'], status='completed', result='ONLY_FIRST_TEST')
        second = self.completed_meeting(employer, vacancy, person, aid, minutes=60, previous=first)
        self.stage_action(employer, vacancy, aid, 'stage.update', stageId='meeting:' + second['id'], notes='ONLY_SECOND_MEETING')
        test2 = self.create_test(employer, vacancy, aid, 'Второе тестовое')
        self.stage_action(employer, vacancy, aid, 'test.update', testId=test2['test']['id'], status='completed', result='ONLY_SECOND_TEST')
        # Distinct applications for the same person make vacancy mix-ups observable.
        _, other_aid = self.accepted(employer, other_vacancy, person)
        other_meeting = self.propose(employer, other_vacancy, other_aid, minutes=180)
        proposed, proposed_aid = self.accepted(employer, vacancy, self.candidate('Борис На рассмотрении'))
        self.accepted(employer, empty, proposed)
        return {'apiBase': self.url, 'token': employer['token'], 'workspaceId': employer['workspace']['id'],
                'vacancy': vacancy, 'emptyVacancy': empty, 'otherVacancy': other_vacancy,
                'applicationId': aid, 'candidateId': person['id'], 'candidateName': 'Алёна Проверочная',
                'otherApplicationId': other_aid, 'otherMeetingId': other_meeting['id'],
                'proposedApplicationId': proposed_aid, 'proposedName': 'Борис На рассмотрении',
                'initialInterviewId': person['initialId'], 'firstMeetingId': first['id'],
                'secondMeetingId': second['id'], 'firstTestId': test['test']['id'],
                'secondTestId': test2['test']['id'], 'startDate': self.offer_terms()['startDate'],
                'localOnly': True, 'fixture': 'real-http-temp-sqlite-fake-daily'}


def serve_fixture(destination):
    cls = EmployerStageAcceptanceTest
    cls.setUpClass()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        case = cls()
        case.setUp()
        result = case.seed_browser()
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        target.chmod(0o600)
        print('Local stage fixture ready: ' + str(target), flush=True)
        stop.wait()
    finally:
        Path(destination).unlink(missing_ok=True)
        cls.tearDownClass()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve-fixture')
    args, remaining = parser.parse_known_args()
    if args.serve_fixture:
        serve_fixture(args.serve_fixture)
    else:
        unittest.main(argv=[__file__, *remaining])
