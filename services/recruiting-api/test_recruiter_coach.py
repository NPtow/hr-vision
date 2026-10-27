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

    def test_rich_notes_history_persists_original_and_navigation_does_not_add_versions(self):
        self.prepare()
        self.draft()
        self.command('approve', {'revision': 1})
        state = self.live()
        # A record made by the old plain-text client must remain restorable.
        with self.connect() as db:
            db.execute('UPDATE cp_interview_coach SET progress=? WHERE interview_id=?',
                       (json.dumps({'currentId': 'q1', 'general': 'Старая заметка', 'answers': {}}), self.iid))
        document = {'type': 'doc', 'content': [
            {'type': 'heading', 'attrs': {'level': 2}, 'content': [{'type': 'text', 'text': 'Опыт'}]},
            {'type': 'bulletList', 'content': [{'type': 'listItem', 'content': [
                {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Продажи', 'marks': [{'type': 'bold'}]}]}]}]}]}
        progress = {'currentId': 'q1', 'general': 'Опыт\nПродажи', 'noteDocument': document,
                    'answers': {'q1': {'status': '', 'note': 'Личный результат'}}}
        saved = self.command('progress', {'expectedRevision': state['revision'], 'scriptRevision': 1, 'progress': progress})
        self.assertEqual(self.command('state')['progress']['noteDocument'], document)
        versions = self.command('history')['versions']
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0]['snapshot']['document'], document)
        self.assertEqual(versions[1]['snapshot']['document'], coach.plain_document('Старая заметка'))
        self.assertTrue(versions[0]['author']['id'])
        saved = self.command('progress', {'expectedRevision': saved['revision'], 'scriptRevision': 1,
                                          'progress': {**progress, 'currentId': 'q2'}})
        self.assertEqual(len(self.command('history')['versions']), 2)
        # The old client can continue navigation without flattening the rich document.
        legacy = {key: value for key, value in saved['progress'].items() if key != 'noteDocument'}
        saved = self.command('progress', {'expectedRevision': saved['revision'], 'scriptRevision': 1, 'progress': legacy})
        self.assertEqual(saved['progress']['noteDocument'], document)
        self.assertEqual(len(self.command('history')['versions']), 2)

    def test_restore_is_cas_scoped_atomic_and_keeps_navigation_and_answer_status(self):
        self.prepare()
        self.draft()
        self.command('approve', {'revision': 1})
        state = self.live()
        first = {'currentId': 'q1', 'general': 'Первое', 'answers': {'q1': {'status': '', 'note': 'Старая'}}}
        state = self.command('progress', {'expectedRevision': state['revision'], 'scriptRevision': 1, 'progress': first})
        old_id = self.command('history')['versions'][0]['id']
        state = self.command('progress', {'expectedRevision': state['revision'], 'scriptRevision': 1,
                                          'progress': {'currentId': 'q2', 'general': 'Последнее',
                                                       'answers': {'q1': {'status': 'answered', 'note': 'Новая'}}}})
        body = {'expectedRevision': state['revision'], 'scriptRevision': 1, 'versionId': old_id}
        before = self.command('history')
        self.rejects(409, 'restore', {**body, 'expectedRevision': state['revision'] - 1})
        self.rejects(409, 'restore', {**body, 'scriptRevision': 2})
        self.rejects(404, 'restore', {**body, 'versionId': 'not-this-interview'})
        self.assertEqual(self.command('history'), before)
        capture = coach._capture
        calls = []
        def fail_second_capture(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError('simulated database interruption')
            return capture(*args, **kwargs)
        with patch.object(coach, '_capture', side_effect=fail_second_capture):
            with self.assertRaises(RuntimeError):
                self.command('restore', body)
        self.assertEqual(self.command('history'), before)
        self.assertEqual(self.command('state')['revision'], state['revision'])
        restored = self.command('restore', body)
        self.assertEqual(restored['progress']['general'], 'Первое')
        self.assertEqual(restored['progress']['currentId'], 'q2')
        self.assertEqual(restored['progress']['answers']['q1'], {'status': 'answered', 'note': 'Старая'})
        versions = self.command('history')['versions']
        self.assertEqual([entry['reason'] for entry in versions[:2]], ['restored', 'before_restore'])
        self.assertEqual(versions[1]['snapshot']['document'], coach.plain_document('Последнее'))

    def test_new_endpoints_reject_other_recruiter_and_original_resume_returns_exact_bytes(self):
        import base64
        self.prepare()
        other = self.recruiter('history-other')
        for action in ('history', 'resume', 'restore'):
            self.rejects(404, action, auth=other)
        with self.connect() as db:
            db.execute('DELETE FROM cp_resumes WHERE candidate_id=?', (self.cid,))
        self.assertEqual(self.command('resume'), {'resume': None})
        original = b'%PDF-1.4\noriginal resume bytes'
        with self.connect() as db:
            db.execute('INSERT INTO cp_resumes VALUES (?,?,?,?,?,?,?)',
                       (self.cid, 'cv.pdf', 'application/pdf', original, 'Unrelated extraction', '{}', '2026-10-20'))
        resume = self.command('resume')['resume']
        self.assertEqual(base64.b64decode(resume['contentBase64']), original)
        self.assertEqual(resume['mimeType'], 'application/pdf')
        self.assertNotIn('text', resume)
        with self.connect() as db:
            db.execute("UPDATE cp_resumes SET mime='application/msword' WHERE candidate_id=?", (self.cid,))
        self.rejects(409, 'resume')

    def test_history_pagination_is_explicit_and_keeps_every_version(self):
        self.prepare()
        self.live()
        with self.connect() as db:
            for i in range(105):
                coach._capture(db, self.iid, None, {'general': str(i)}, 'fixture', 'manual')
        first = self.command('history')
        self.assertEqual(len(first['versions']), 100)
        self.assertTrue(first['hasMore'])
        second = self.command('history', {'beforeVersionId': first['nextCursor']})
        self.assertEqual(len(second['versions']), 5)
        self.assertFalse(second['hasMore'])
        self.assertEqual(len({v['id'] for v in first['versions'] + second['versions']}), 105)

    def test_rich_document_rejects_executable_unknown_or_oversized_shapes_without_changing_state(self):
        self.prepare()
        state = self.live()
        invalid = [
            {'type': 'doc', 'content': [{'type': 'image', 'attrs': {'src': 'https://external.test'}}]},
            {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': 'link', 'marks': [{'type': 'link', 'attrs': {'href': 'javascript:x'}}]}]}]},
            {'type': 'doc', 'content': [{'type': 'heading', 'attrs': {'level': 1}}]},
            {'type': 'doc', 'content': [{'type': 'paragraph', 'attrs': {'onclick': 'x'}}]},
            coach.plain_document('x' * 24001),
            {'type': 'doc', 'content': [{'type': 'paragraph'}] * 3001},
            {'type': 'doc', 'content': []},
        ]
        for document in invalid:
            self.rejects(400, 'progress', {'expectedRevision': state['revision'], 'scriptRevision': None,
                                          'progress': {'general': '', 'noteDocument': document}})
        self.assertEqual(self.command('state')['revision'], state['revision'])
        self.assertEqual(self.command('history')['versions'], [])
