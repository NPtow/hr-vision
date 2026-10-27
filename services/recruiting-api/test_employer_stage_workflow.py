"""Persistent stage history on temporary canonical records; no network or sends."""
import concurrent.futures
import json
import unittest
from unittest.mock import patch

from domain import Problem, iso, now
import employer_workflow as wf
import test_employer_workflow as fixture


class StageWorkflowTest(unittest.TestCase):
    setUp = fixture.WorkflowTest.setUp
    tearDown = fixture.WorkflowTest.tearDown
    connect = fixture.WorkflowTest.connect
    add_meeting = fixture.WorkflowTest.add_meeting
    manager = fixture.WorkflowTest.manager
    candidate = fixture.WorkflowTest.candidate
    state = fixture.WorkflowTest.state
    terms = fixture.WorkflowTest.terms
    assert_problem = fixture.WorkflowTest.assert_problem

    def action(self, action, *, aid='a1', **body):
        body.setdefault('expectedRevision', self.state(aid)['revision'])
        return self.manager(action, body, aid=aid)

    def assignments(self, aid='a1'):
        return [s for s in self.state(aid)['stages'] if s['kind'] == 'test']

    def test_repeated_tests_and_meetings_keep_order_and_exact_results_after_reopen(self):
        stamp = now()
        with patch.object(wf, 'now', return_value=stamp):
            first = self.action('test.create', title='Практическое задание', note='Первое задание')['state']['stages'][-1]
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                wf.register_stages(db, 'a1')
                self.add_meeting(db, 'meeting-second', 'a1')
                wf.meeting_changed(db, 'a1')
            second = self.action('test.create', title='Практическое задание')['state']['stages'][-1]
        before = [s['id'] for s in self.state()['stages']]
        self.action('test.update', testId=first['test']['id'], status='completed', result='Разобрал кейс',
                    note='Сильная аргументация', documents=[{'title': 'Ответ', 'url': 'https://example.test/result-one'}])
        state = self.state()  # A fresh connection, not the mutation's return value.
        self.assertEqual(before, [s['id'] for s in state['stages']])
        self.assertEqual(before, ['meeting:m1', first['id'], 'meeting:meeting-second', second['id']])
        saved = next(s for s in state['stages'] if s['id'] == first['id'])
        other = next(s for s in state['stages'] if s['id'] == second['id'])
        self.assertEqual((saved['result'], saved['notes'], saved['status']),
                         ('Разобрал кейс', 'Сильная аргументация', 'completed'))
        self.assertEqual(saved['documents'][0]['url'], 'https://example.test/result-one')
        self.assertEqual((other['result'], other['notes'], other['documents']), ('', '', []))
        self.assertEqual([m['id'] for m in state['meetings']], ['m1', 'meeting-second'])

    def test_history_reads_do_not_create_ledger_or_modify_application(self):
        with self.connect() as db:
            before = db.total_changes
            for _ in range(3):
                wf.load(db, 'a1')
                wf.stage_history(db, 'a1')
            self.assertEqual(db.total_changes, before)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_employer_stages').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT status FROM cp_applications WHERE id='a1'").fetchone()[0], 'accepted')

    def test_idempotent_create_retry_after_later_stage_does_not_append_or_reorder(self):
        body = {'title': 'Один', 'requestId': 'create-once', 'expectedRevision': 0}
        initial = self.manager('test.create', body)
        self.assertEqual(initial['events'], [])
        self.action('test.create', title='Два')
        before = self.state()
        result = self.manager('test.create', body)
        self.assertTrue(result['replayed'])
        self.assertEqual(before, result['state'])
        self.assertEqual(len(self.assignments()), 2)
        self.assert_problem(409, lambda: self.manager('test.create', {**body, 'title': 'Подмена'}))

    def test_stale_revision_and_foreign_entity_fail_without_side_effects(self):
        stage = self.action('test.create', title='Локальный')['state']['stages'][-1]
        self.assert_problem(409, lambda: self.manager('test.update',
            {'testId': stage['test']['id'], 'result': 'Устаревший', 'expectedRevision': 0}))
        self.assert_problem(409, lambda: self.manager('test.create', {'title': 'Нет ревизии'}))
        for aid in ('a2', 'a3'):
            self.assert_problem(404, lambda: self.action('test.update', aid=aid, testId=stage['test']['id'], result='Чужой'))
            self.assert_problem(404, lambda: self.action('stage.update', aid=aid, stageId=stage['id'], notes='Чужой'))
        self.assert_problem(404, lambda: self.manager('test.create',
            {'title': 'Чужая вакансия', 'expectedRevision': 1}, vacancy_id='v2'))
        self.assertEqual(self.assignments()[0]['result'], '')
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT DISTINCT application_id FROM cp_employer_stages').fetchall(), [('a1',)])

    def test_private_stage_notes_do_not_leak_into_candidate_workflow(self):
        stage = self.action('test.create', title='Тест', note='private assessment')['state']['stages'][-1]
        self.action('stage.update', stageId='meeting:m1', notes='Private employer note')
        candidate = self.state(actor='candidate', cid='c1')
        for key in ('stages', 'meetings', 'offers', 'tests'):
            self.assertNotIn(key, candidate)
        self.assertNotIn('private', json.dumps(candidate).lower())
        self.assert_problem(403, lambda: self.candidate('test.update', {'testId': stage['test']['id'], 'status': 'completed'}))
        self.assert_problem(403, lambda: self.candidate('stage.update', {'stageId': 'meeting:m1', 'notes': 'x'}))

    def test_stage_and_test_note_aliases_have_one_persisted_value(self):
        stage = self.action('test.create', title='Тест', note='Начало')['state']['stages'][-1]
        self.action('stage.update', stageId=stage['id'], notes='После интервью')
        self.assertEqual(self.assignments()[0]['test']['note'], 'После интервью')
        self.action('test.update', testId=stage['test']['id'], note='После теста')
        self.assertEqual(self.assignments()[0]['notes'], 'После теста')
        self.action('stage.update', stageId='meeting:m1', notes='Только первая встреча')
        with self.connect() as db:
            self.add_meeting(db, 'm-next', 'a1')
            wf.meeting_changed(db, 'a1')
        stages = {item['id']: item for item in self.state()['stages']}
        self.assertEqual(stages['meeting:m1']['notes'], 'Только первая встреча')
        self.assertEqual(stages['meeting:m-next']['notes'], '')

    def test_due_date_and_document_validation_are_atomic(self):
        stage = self.action('test.create', title='Тест', dueAt='2026-11-01T16:30:00+03:00')['state']['stages'][-1]
        self.assertEqual(stage['test']['dueAt'], '2026-11-01T13:30:00Z')
        for changes in ({'dueAt': '2026-11-01T16:30'}, {'status': 'invented'}, {'title': ''},
                        {'documents': [{'title': 'X', 'url': 'javascript:alert(1)'}]},
                        {'documents': [{'title': 'X', 'url': 'https://login:password@example.test/file'}]},
                        {'documents': [{'title': 'X', 'url': 'https://example.test'}] * 21}):
            before = self.state()
            self.assert_problem(400, lambda: self.action('test.update', testId=stage['test']['id'], **changes))
            self.assertEqual(self.state(), before)
        self.action('test.update', testId=stage['test']['id'], dueAt=None)
        self.assertIsNone(self.assignments()[0]['test']['dueAt'])

    def test_offer_from_screening_flow_needs_no_employer_meeting_or_fake_feedback(self):
        with self.connect() as db:
            db.execute("DELETE FROM cp_meetings WHERE application_id='a1'")
        self.assert_problem(400, lambda: self.manager('offer.save', self.terms(expectations='')))
        self.assertEqual(self.state()['decision'], 'review')
        first = self.manager('offer.save', self.terms())['state']
        self.assertEqual(first['decision'], 'pool')
        self.assertIsNone(first['feedback'])
        second = self.manager('offer.save', self.terms(version=1, compensation='200 000 ₽'))['state']
        self.assertEqual([s['kind'] for s in second['stages']], ['offer', 'offer'])
        self.assertEqual([o['version'] for o in second['offers']], [1, 2])
        self.assertEqual(second['offers'][0]['status'], 'superseded')
        self.assertEqual(second['offers'][0]['compensation'], '180 000 ₽')
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_meeting_feedback').fetchone()[0], 0)

    def test_same_revision_concurrent_updates_cannot_lose_result(self):
        stage = self.action('test.create', title='Тест')['state']['stages'][-1]
        revision = self.state()['revision']
        def update(result):
            try:
                return self.manager('test.update', {'testId': stage['test']['id'], 'result': result,
                                                    'expectedRevision': revision})
            except Problem as error:
                return error.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, ('Первый ответ', 'Второй ответ')))
        self.assertEqual(sum(value == 409 for value in results), 1)
        self.assertIn(self.assignments()[0]['result'], ('Первый ответ', 'Второй ответ'))
        self.assertEqual(self.state()['revision'], revision + 1)

    def test_offer_documents_are_versioned_with_terms_and_invalid_update_rolls_back(self):
        first = self.manager('offer.save', self.terms(documents=[
            {'title': 'Условия', 'url': 'https://example.test/offer-v1'}]))['state']['stages'][-1]
        saved = self.manager('offer.save', self.terms(version=1, documents=[
            {'title': 'Новые условия', 'url': 'https://example.test/offer-v2'}]))['state']
        offers = [stage for stage in saved['stages'] if stage['kind'] == 'offer']
        self.assertEqual([stage['offer']['version'] for stage in offers], [1, 2])
        self.assertEqual(offers[0]['documents'], first['documents'])
        self.assertEqual(offers[1]['documents'][0]['url'], 'https://example.test/offer-v2')
        self.assert_problem(400, lambda: self.manager('offer.save', self.terms(version=2, documents=[
            {'title': 'Unsafe', 'url': 'file:///private/report.pdf'}])))
        self.assertEqual(self.state(), saved)
        updated = self.manager('offer.save', self.terms(version=2, compensation='250 000 ₽'))['state']['stages'][-1]
        self.assertEqual(updated['documents'], offers[1]['documents'])

    def test_old_meeting_refusal_does_not_block_offer_after_new_completed_meeting(self):
        self.candidate('after', {'meetingId': 'm1', 'value': 'no'})
        self.assertEqual(self.state()['afterInterest'], 'no')
        with self.connect() as db:
            # The later canonical meeting already exists; this test does not
            # waive candidate consent required when booking through the portal.
            self.add_meeting(db, 'm-later', 'a1')
            wf.meeting_changed(db, 'a1')
        self.assertEqual(self.state()['afterInterest'], 'pending')
        offer = self.manager('offer.save', self.terms())['state']['offer']
        result = self.manager('offer.send', {'version': offer['version'], 'requestId': 'after-later-meeting'})
        self.assertEqual(result['state']['offer']['status'], 'sent')
        self.assertTrue(self.manager('offer.send', {
            'version': offer['version'], 'requestId': 'after-later-meeting'})['replayed'])
        with self.connect() as db:
            self.assertEqual(db.execute('''SELECT after_interest,after_meeting_id FROM cp_employer_workflows
                WHERE application_id='a1' ''').fetchone(), ('no', 'm1'))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM cp_meetings WHERE application_id='a1'").fetchone()[0], 2)

    def test_current_meeting_refusal_still_blocks_offer_and_preserves_draft(self):
        with self.connect() as db:
            self.add_meeting(db, 'm-latest', 'a1')
            wf.meeting_changed(db, 'a1')
        self.candidate('after', {'meetingId': 'm-latest', 'value': 'no'})
        offer = self.manager('offer.save', self.terms())['state']['offer']
        self.assert_problem(409, lambda: self.manager('offer.send', {'version': offer['version']}))
        self.assertEqual(self.state()['offer']['status'], 'draft')
        self.assertEqual(self.state()['afterInterest'], 'no')

    def test_offer_limits_match_form_and_candidate_reply_keeps_long_terms(self):
        terms = self.terms(role='Р' * 240, compensation='З' * 240, format='Ф' * 240, expectations='О' * 12000)
        offer = self.manager('offer.save', terms)['state']['offer']
        for key in ('role', 'compensation', 'format', 'expectations'):
            limit = 12000 if key == 'expectations' else 240
            self.assert_problem(400, lambda: self.manager('offer.save', {
                **terms, 'version': offer['version'], key: 'X' * (limit + 1)}))
        self.manager('offer.send', {'version': offer['version']})
        candidate = self.state(actor='candidate', cid='c1')
        self.assertEqual(candidate['offer']['expectations'], terms['expectations'])
        accepted = self.candidate('offer.reply', {'version': offer['version'], 'value': 'accepted'})
        self.assertEqual(accepted['state']['offer']['status'], 'accepted')
        self.assertEqual(accepted['state']['offer']['expectations'], terms['expectations'])

    def test_shared_screening_is_first_but_annotations_are_application_scoped(self):
        with self.connect() as db:
            db.executescript('''CREATE TABLE cp_recruiter_slots(id TEXT PRIMARY KEY,start TEXT);
                CREATE TABLE cp_interviews(id TEXT PRIMARY KEY,candidate_id TEXT,slot_id TEXT,status TEXT,
                  created_at TEXT,completed_at TEXT);''')
            db.execute('INSERT INTO cp_recruiter_slots VALUES (?,?)', ('s1', iso(now())))
            db.execute('INSERT INTO cp_interviews VALUES (?,?,?,?,?,?)',
                       ('i1', 'c1', 's1', 'completed', iso(now()), iso(now())))
        self.action('stage.update', stageId='interview:i1', notes='Вакансия первая')
        self.action('stage.update', aid='a3', stageId='interview:i1', notes='Вакансия вторая')
        self.assertEqual(self.state()['stages'][0]['id'], 'interview:i1')
        self.assertEqual(self.state()['stages'][0]['notes'], 'Вакансия первая')
        self.assertEqual(self.state('a3')['stages'][0]['notes'], 'Вакансия вторая')
        self.assert_problem(404, lambda: self.action('stage.update', aid='a2', stageId='interview:i1', notes='Чужой'))


if __name__ == '__main__':
    unittest.main()
