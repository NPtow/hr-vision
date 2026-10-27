"""Script approval, durable notes and interview/account boundaries; no external calls."""
import json
from unittest.mock import patch

from domain import Problem
import recruiter_coach as coach
import test_recruiter_access as fixture
import unittest


class RecruiterCoachTest(unittest.TestCase):
    setUp = fixture.RecruiterAccessTest.setUp
    tearDown = fixture.RecruiterAccessTest.tearDown
    connect = fixture.RecruiterAccessTest.connect
    payload = fixture.RecruiterAccessTest.payload
    register_candidate = fixture.RecruiterAccessTest.register_candidate
    call = fixture.RecruiterAccessTest.call
    not_manager = staticmethod(fixture.RecruiterAccessTest.not_manager)
    request = fixture.RecruiterAccessTest.request
    provision = fixture.RecruiterAccessTest.provision
    recruiter = fixture.RecruiterAccessTest.recruiter
    slot = fixture.RecruiterAccessTest.slot
    assign_fixture = fixture.RecruiterAccessTest.assign_fixture

    def prepare(self):
        self.owner = self.recruiter()
        person, _ = self.register_candidate()
        self.cid = person['id']
        self.assign_fixture(person, self.slot(self.owner))
        with self.connect() as db:
            self.iid = self.portal.interview(db, self.cid)['id']
        self.identity = {'candidateId': self.cid, 'interviewId': self.iid}

    def command(self, action, values=None, auth=None):
        return self.request('interview/coach/' + action, {**self.identity, **(values or {})}, auth or self.owner)[0]

    def draft(self, expected=0):
        return self.command('draft', {'expectedRevision': expected, 'source': 'Approved method, sample CV',
                                     'questions': [{'id': 'q1', 'topic': 'Опыт', 'question': 'Что вы сделали лично?'},
                                                   {'id': 'q2', 'question': 'Как измерили результат?'}]})

    def live(self):
        with self.connect() as db:
            db.execute("UPDATE cp_interviews SET status='live' WHERE id=?", (self.iid,))
        return self.command('pin')

    def rejects(self, status, action, values=None, auth=None):
        with self.assertRaises(Problem) as caught:
            self.command(action, values, auth)
        self.assertEqual(caught.exception.status, status)

    def test_draft_is_not_used_until_exact_latest_revision_is_approved(self):
        self.prepare()
        state = self.draft()
        self.assertIsNone(state['active'])
        self.rejects(409, 'approve', {'revision': 2})
        self.command('approve', {'revision': 1})
        state = self.draft(1)
        self.assertEqual(state['active']['revision'], 1)
        self.assertEqual(state['latest']['revision'], 2)
        self.rejects(409, 'approve', {'revision': 1})
        self.rejects(409, 'draft', {'expectedRevision': 0, 'questions': [{'question': 'stale'}]})

    def test_join_pins_approved_revision_new_draft_cannot_replace_active_script(self):
        self.prepare()
        self.draft()
        self.command('approve', {'revision': 1})
        self.request('interview/join', {'candidateId': self.cid}, self.owner)
        state = self.command('state')
        self.assertEqual(state['pinnedRevision'], 1)
        self.draft(1)
        self.rejects(409, 'approve', {'revision': 2})
        self.assertEqual(self.command('state')['active']['revision'], 1)

    def test_durable_progress_cas_and_completed_history(self):
        self.prepare()
        self.draft()
        self.command('approve', {'revision': 1})
        state = self.live()
        values = {'expectedRevision': state['revision'], 'scriptRevision': 1,
                  'progress': {'currentId': 'q2', 'answers': {'q1': {'status': 'answered', 'note': 'Пример подтверждён'}}}}
        saved = self.command('progress', values)
        self.assertEqual(saved['progress']['answers']['q1']['note'], 'Пример подтверждён')
        self.rejects(409, 'progress', values)
        self.rejects(400, 'progress', {**values, 'expectedRevision': saved['revision'], 'progress': {'currentId': 'made-up'}})
        with self.connect() as db:
            db.execute("UPDATE cp_interviews SET status='completed' WHERE id=?", (self.iid,))
        self.assertEqual(self.command('state')['progress'], saved['progress'])
        self.rejects(409, 'draft', {'expectedRevision': 1, 'questions': [{'question': 'Overwrite history'}]})
        self.command('progress', {**values, 'expectedRevision': saved['revision'],
                                 'progress': {**saved['progress'], 'general': 'Итог после разговора'}})

    def test_another_recruiter_and_mismatched_interview_cannot_read_or_write(self):
        self.prepare()
        other = self.recruiter('other')
        self.rejects(404, 'state', auth=other)
        self.rejects(404, 'context', auth=other)
        self.rejects(404, 'draft', {'questions': [{'question': 'x'}], 'expectedRevision': 0}, other)
        self.rejects(404, 'state', {'interviewId': 'another-interview'})
        with self.assertRaises(Problem) as error:
            self.request('interview/coach/state', auth=self.owner, method='GET')
        self.assertEqual(error.exception.status, 405)

    def test_backup_link_is_manual_safe_scoped_and_keeps_notes(self):
        self.prepare()
        for url in ('javascript:alert(1)', 'http://zoom.us/j/123', 'https://user:pass@zoom.us/x', 'https://[bad'):
            self.rejects(400, 'backup', {'expectedRevision': 0, 'url': url})
        state = self.command('backup', {'expectedRevision': 0, 'url': 'https://example.zoom.us/j/123'})
        self.assertEqual(state['backupUrl'], 'https://example.zoom.us/j/123')
        self.rejects(409, 'backup', {'expectedRevision': 0, 'url': ''})
        self.assertEqual(self.command('backup', {'expectedRevision': 1, 'url': ''})['backupUrl'], '')

    def test_general_notes_work_without_a_script_and_survive_later_approval(self):
        self.prepare()
        state = self.live()
        saved = self.command('progress', {'expectedRevision': state['revision'], 'scriptRevision': None,
                                         'progress': {'general': 'Начали с уточнения роли'}})
        self.assertEqual(saved['progress']['general'], 'Начали с уточнения роли')
        self.draft()
        self.command('approve', {'revision': 1})
        state = self.command('pin')
        self.assertEqual(state['progress']['currentId'], 'q1')
        self.assertEqual(state['progress']['general'], 'Начали с уточнения роли')
        self.rejects(400, 'progress', {'expectedRevision': state['revision'], 'scriptRevision': 1,
                                      'progress': {'currentId': []}})

    def test_invalid_questions_and_context_do_not_invoke_model_or_notifications(self):
        self.prepare()
        self.rejects(400, 'draft', {'expectedRevision': 0, 'questions': []})
        self.rejects(400, 'draft', {'expectedRevision': 0, 'questions': [{'id': 'x', 'question': 'a'}, {'id': 'x', 'question': 'b'}]})
        self.rejects(400, 'draft', {'expectedRevision': 0, 'questions': [{'question': 'a', 'score': 5}]})
        with self.connect() as db:
            before = db.execute('SELECT COUNT(*) FROM fake_events').fetchone()[0]
        with patch.object(self.portal.resume_model, 'generate', side_effect=AssertionError('No model call')):
            result = self.command('context')
            self.assertEqual(result['interviewId'], self.iid)
            self.assertNotIn('password', json.dumps(result))
            self.draft()
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fake_events').fetchone()[0], before)
