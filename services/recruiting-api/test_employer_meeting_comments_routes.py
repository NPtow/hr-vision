"""Meeting comments through authenticated HTTP and the employer person projection."""
import json
import unittest
import uuid

import test_employer_acceptance as fixture


class MeetingCommentRoutesTest(unittest.TestCase):
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

    def test_exact_material_comment_projects_replays_and_stays_private_to_workspace(self):
        employer, outsider = self.employer(), self.employer('Other')
        vacancy = self.vacancy(employer)
        person, aid = self.accepted(employer, vacancy)
        meeting = self.propose(employer, vacancy, aid)
        self.complete(employer, vacancy, person, aid, meeting)
        with self.app.connect() as db:
            room = self.app.CANDIDATE_PORTAL.meeting(db, aid, meeting['id'])['room']
        self.media.ready(room)
        materials = self.ok('employer/materials', self.action_body(employer, vacancy, aid, 'materials',
            meetingId=meeting['id']), token=employer['token'])
        recording_id = materials['recordings'][0]['id']
        revision = self.state(employer, vacancy)['candidates'][aid]['workflowRevision']
        command = self.action_body(employer, vacancy, aid, 'meeting.comment.add', meetingId=meeting['id'],
            recordingId=recording_id, seconds=1, text='Только для команды работодателя', expectedRevision=revision)
        self.assertEqual(self.request('employer/action', command, token=outsider['token'])[0], 404)
        self.assertEqual(self.request('candidate/workflow', {**command, 'applicationId': aid},
                                      cookie=person['cookie'])[0], 403)
        state = self.ok('employer/action', command, token=employer['token'])
        comment = state['candidates'][aid]['meetingComments'][0]
        self.assertEqual((comment['meetingId'], comment['recordingId'], comment['seconds']),
                         (meeting['id'], recording_id, 1))
        self.assertEqual(state['candidates'][aid]['screeningComments'], [])
        replay = self.ok('employer/action', command, token=employer['token'])
        self.assertEqual(replay['candidates'][aid]['meetingComments'], [comment])
        self.assertEqual(replay['candidates'][aid]['workflowRevision'], revision + 1)
        self.assertEqual(self.request('employer/action', {**command, 'requestId': uuid.uuid4().hex},
                                      token=employer['token'])[0], 409)
        candidate = self.ok('candidate/state', cookie=person['cookie'])
        self.assertNotIn('meetingComments', json.dumps(candidate))
        self.assertNotIn(comment['text'], json.dumps(candidate, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
