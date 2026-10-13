"""Isolated real-entity workflow contracts; no provider or outbound messages."""
import concurrent.futures
import datetime as dt
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from domain import Problem, iso, now
import employer_workflow as wf


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hr-employer-workflow-')
        self.path = Path(self.tmp.name) / 'state.sqlite3'
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE cp_candidates(id TEXT PRIMARY KEY, profile TEXT NOT NULL);
              CREATE TABLE cp_vacancies(id TEXT PRIMARY KEY,body TEXT NOT NULL,created_at TEXT NOT NULL);
              CREATE TABLE cp_applications(id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL REFERENCES cp_candidates(id),
                vacancy_id TEXT NOT NULL REFERENCES cp_vacancies(id),status TEXT NOT NULL,archived_at TEXT DEFAULT NULL);
              CREATE TABLE cp_meetings(id TEXT PRIMARY KEY,application_id TEXT NOT NULL REFERENCES cp_applications(id),
                start TEXT,end TEXT,status TEXT,room TEXT,confirmed_at TEXT,completed_at TEXT);
            ''')
            wf.init(db)
            for cid in ('c1', 'c2', 'c3'):
                db.execute('INSERT INTO cp_candidates VALUES (?,?)', (cid, json.dumps({'name': cid, 'secret': 'original-profile'})))
            for vid in ('v1', 'v2', 'v3'):
                db.execute('INSERT INTO cp_vacancies VALUES (?,?,?)', (vid, json.dumps({
                    'id': vid, 'role': 'Менеджер', 'status': 'open', 'custom': {'retained': True}}), iso(now())))
            for aid, cid, vid in [('a1', 'c1', 'v1'), ('a2', 'c2', 'v1'), ('a3', 'c1', 'v2'), ('a4', 'c3', 'v3')]:
                db.execute('INSERT INTO cp_applications(id,candidate_id,vacancy_id,status) VALUES (?,?,?,?)', (aid, cid, vid, 'accepted'))
                self.add_meeting(db, 'm' + aid[1:], aid)

    def tearDown(self):
        self.tmp.cleanup()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=10000')
        return db

    def add_meeting(self, db, mid, aid, status='completed'):
        start = now() - dt.timedelta(hours=1)
        db.execute('INSERT INTO cp_meetings VALUES (?,?,?,?,?,NULL,?,?)',
                   (mid, aid, iso(start), iso(start + dt.timedelta(minutes=30)), status,
                    iso(start), iso(now()) if status == 'completed' else None))

    def manager(self, action, body=None, aid='a1', manager='manager-1', vacancy_id=None):
        with self.connect() as db:
            return wf.manager_action(db, aid, action, body or {}, manager_id=manager, vacancy_id=vacancy_id)

    def candidate(self, action, body=None, aid='a1', cid='c1'):
        with self.connect() as db:
            return wf.candidate_action(db, cid, aid, action, body or {})

    def state(self, aid='a1', actor='manager', cid=None):
        with self.connect() as db:
            return wf.load(db, aid, actor=actor, candidate_id=cid)

    def questions(self, aid='a1', mid='m1'):
        return self.manager('feedback.questions', {'meetingId': mid,
            'questions': 'Как вы возвращали потерянных клиентов?\nКак оценивали результат своей работы?'}, aid=aid)['state']['meeting']['questionnaire']

    def feedback(self, aid='a1', mid='m1'):
        q = self.questions(aid, mid)
        body = {'meetingId': mid, 'questionnaireId': q['id'],
                'answers': {item['id']: {'rating': 'clear', 'comment': 'Есть конкретный пример.'} for item in q['questions']}}
        self.manager('feedback.submit', body, aid=aid)
        return body

    def terms(self, **changes):
        return {'role': 'Менеджер', 'compensation': '180 000 ₽', 'format': 'Москва, гибрид',
                'expectations': 'Восстановить повторные продажи',
                'startDate': (now() + dt.timedelta(days=5)).date().isoformat(), **changes}

    def draft(self, aid='a1', mid='m1'):
        self.feedback(aid, mid)
        self.manager('decision', {'value': 'pool'}, aid=aid)
        return self.manager('offer.draft', self.terms(), aid=aid)['state']['offer']

    def sent(self, aid='a1', mid='m1'):
        offer = self.draft(aid, mid)
        self.manager('offer.send', {'version': offer['version']}, aid=aid)
        return offer

    def assert_problem(self, status, call):
        with self.assertRaises(Problem) as ctx:
            call()
        self.assertEqual(ctx.exception.status, status)

    def test_default_overlay_does_not_modify_canonical_identity(self):
        state = self.state()
        self.assertEqual((state['applicationId'], state['candidateId'], state['vacancyId']), ('a1', 'c1', 'v1'))
        self.assertEqual((state['decision'], state['afterInterest'], state['offer']), ('review', 'pending', None))
        self.assertEqual(state['meeting']['id'], 'm1')
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_employer_workflows').fetchone()[0], 0)

    def test_candidate_cannot_read_or_act_as_another(self):
        self.assert_problem(404, lambda: self.state(actor='candidate', cid='c2'))
        self.assert_problem(404, lambda: self.candidate('chat', {'text': 'Hi'}, cid='c2'))
        self.assert_problem(404, lambda: self.manager('chat', {'text': 'Hi'}, vacancy_id='v3'))

    def test_role_action_guard_and_candidate_required(self):
        self.assert_problem(403, lambda: self.candidate('decision', {'value': 'pool'}))
        self.assert_problem(403, lambda: self.manager('offer.reply', {'version': 1, 'value': 'accepted'}))
        self.assert_problem(401, lambda: self.state(actor='candidate'))
        self.assert_problem(401, lambda: self.manager('chat', {'text': 'x'}, manager=''))

    def test_unaccepted_application_has_no_workflow_actions(self):
        with self.connect() as db:
            db.execute("UPDATE cp_applications SET status='pending' WHERE id='a1'")
        self.assert_problem(409, lambda: self.manager('chat', {'text': 'Hi'}))

    def test_questions_require_exact_completed_employer_meeting(self):
        self.assert_problem(409, lambda: self.questions(mid='m2'))
        with self.connect() as db:
            db.execute("UPDATE cp_meetings SET status='confirmed' WHERE id='m1'")
        self.assert_problem(409, lambda: self.questions())
        self.assert_problem(400, lambda: self.manager('feedback.questions', {'questions': 'Как ваш опыт?'}))

    def test_questionnaire_freezes_and_duplicate_replays(self):
        q = self.questions()
        result = self.manager('feedback.questions', {'meetingId': 'm1',
            'questions': 'Как вы возвращали потерянных клиентов?\nКак оценивали результат своей работы?'})
        self.assertTrue(result['replayed'])
        self.assertEqual(result['state']['meeting']['questionnaire'], q)
        self.assert_problem(409, lambda: self.manager('feedback.questions', {'meetingId': 'm1',
            'questions': 'Почему вы решили сменить место работы?'}))

    def test_transcript_questions_no_invented_fallback(self):
        with self.connect() as db:
            empty = wf.prepare_questions(db, 'a1', 'm1', [{'text': 'Добрый день.', 'seconds': 0}])
            self.assertEqual(empty['status'], 'missing')
            ready = wf.prepare_questions(db, 'a1', 'm1', [{'text': 'Как вы возвращали клиентов?', 'seconds': 17}])
            replay = wf.prepare_questions(db, 'a1', 'm1', [{'text': 'Зачем выбрали продажи?', 'seconds': 24}])
            self.assertEqual(ready, replay)
            self.assertEqual(ready['questionnaire']['questions'][0]['seconds'], 17)

    def test_draft_submission_full_answers_and_immutable_result(self):
        q = self.questions()
        first = {q['questions'][0]['id']: {'rating': 'clear', 'comment': ''}}
        draft = {'meetingId': 'm1', 'questionnaireId': q['id'], 'answers': first}
        saved = self.manager('feedback.draft', draft)
        self.assertEqual(saved['state']['meeting']['feedbackDraft'], first)
        self.assert_problem(400, lambda: self.manager('feedback', draft))
        complete = {**draft, 'answers': {item['id']: {'rating': 'not_asked', 'comment': ''} for item in q['questions']}}
        self.manager('feedback', complete)
        self.assertTrue(self.manager('feedback', complete)['replayed'])
        self.assert_problem(409, lambda: self.manager('feedback.draft', draft))
        self.assertNotIn('feedbackDraft', self.state()['meeting'])

    def test_feedback_unknown_question_and_stale_questionnaire_rejected(self):
        q = self.questions()
        self.assert_problem(409, lambda: self.manager('feedback.draft', {'meetingId': 'm1', 'questionnaireId': 'stale', 'answers': {}}))
        self.assert_problem(400, lambda: self.manager('feedback.draft', {'meetingId': 'm1', 'questionnaireId': q['id'], 'answers': {'unknown': {'rating': 'clear'}}}))

    def test_candidate_projection_hides_private_feedback_and_draft_offer(self):
        self.draft()
        state = self.state(actor='candidate', cid='c1')
        self.assertNotIn('feedback', state)
        self.assertNotIn('questionnaire', state['meeting'])
        self.assertNotIn('feedbackDraft', state['meeting'])
        self.assertIsNone(state['offer'])

    def test_historical_feedback_uses_exact_meeting_not_latest(self):
        q = self.questions()
        with self.connect() as db:
            self.add_meeting(db, 'm-new', 'a1', status='confirmed')
        result = self.manager('feedback', {'meetingId': 'm1', 'questionnaireId': q['id'],
            'answers': {item['id']: {'rating': 'clear'} for item in q['questions']}})
        self.assertEqual(result['state']['meeting']['id'], 'm-new')
        self.assertIsNone(result['state']['feedback'])
        self.assert_problem(409, lambda: self.manager('decision', {'value': 'pool'}))
        with self.connect() as db:
            self.assertIsNotNone(db.execute("SELECT submitted FROM cp_meeting_feedback WHERE meeting_id='m1'").fetchone()[0])
            private = wf.meeting_feedback(db, 'a1', 'm1', vacancy_id='v1')
            self.assertEqual(private['feedback']['meetingId'], 'm1')
            self.assertEqual(private['questionnaire']['id'], q['id'])
            self.assert_problem(409, lambda: wf.meeting_feedback(db, 'a1', 'm2'))

    def test_open_older_meeting_blocks_decision_even_after_newer_feedback(self):
        with self.connect() as db:
            db.execute("UPDATE cp_meetings SET status='confirmed' WHERE id='m1'")
            self.add_meeting(db, 'm-new', 'a1')
        self.feedback(mid='m-new')
        self.assert_problem(409, lambda: self.manager('decision', {'value': 'declined'}))

    def test_after_interest_tied_to_latest_completed_meeting(self):
        result = self.candidate('after.interest', {'meetingId': 'm1', 'value': 'yes'})
        self.assertEqual(result['state']['afterInterest'], 'yes')
        self.assertTrue(self.candidate('after', {'meetingId': 'm1', 'value': 'yes'})['replayed'])
        with self.connect() as db:
            self.add_meeting(db, 'm-new', 'a1')
        self.assertEqual(self.state()['afterInterest'], 'pending')
        self.assert_problem(409, lambda: self.candidate('after', {'meetingId': 'm1', 'value': 'no'}))

    def test_after_interest_cannot_override_sent_offer(self):
        self.sent()
        self.assert_problem(409, lambda: self.candidate('after', {'meetingId': 'm1', 'value': 'no'}))

    def test_pool_does_not_require_feedback_and_decline_is_terminal(self):
        self.assertEqual(self.manager('decision', {'value': 'pool'})['state']['decision'], 'pool')
        self.assertIsNone(self.state()['feedback'])
        result = self.manager('decision', {'value': 'declined'})
        self.assertEqual(result['events'][0]['type'], 'employer.declined')
        self.assertTrue(self.manager('decision', {'value': 'declined'})['replayed'])
        self.assert_problem(409, lambda: self.manager('chat', {'text': 'New'}))
        self.assert_problem(409, lambda: self.manager('decision', {'value': 'pool'}))
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT profile FROM cp_candidates WHERE id='c1'").fetchone()[0],
                             json.dumps({'name': 'c1', 'secret': 'original-profile'}))
            self.assertEqual(db.execute("SELECT status FROM cp_applications WHERE id='a3'").fetchone()[0], 'accepted')

    def test_draft_needs_all_terms_and_nonpast_start(self):
        self.feedback()
        self.manager('decision', {'value': 'pool'})
        self.assert_problem(400, lambda: self.manager('offer.save', self.terms(expectations='')))
        self.assert_problem(400, lambda: self.manager('offer.save', self.terms(startDate='2020-01-01')))
        self.assert_problem(400, lambda: self.manager('offer.save', self.terms(startDate='invalid')))

    def test_edit_creates_immutable_version_and_rejects_stale_update(self):
        initial = self.draft()
        updated = self.manager('offer.save', self.terms(version=1, compensation='200 000 ₽'))['state']['offer']
        self.assertEqual(updated['version'], 2)
        self.assertNotEqual(initial['id'], updated['id'])
        self.assert_problem(409, lambda: self.manager('offer.save', self.terms(version=1, format='Удалённо')))
        self.assert_problem(409, lambda: self.manager('offer.save', self.terms()))
        with self.connect() as db:
            old = db.execute('SELECT terms,status FROM cp_offer_versions WHERE id=?', (initial['id'],)).fetchone()
            self.assertEqual(json.loads(old[0])['compensation'], '180 000 ₽')
            self.assertEqual(old[1], 'superseded')

    def test_same_terms_do_not_generate_a_new_version(self):
        self.draft()
        result = self.manager('offer.save', self.terms(version=1))
        self.assertTrue(result['replayed'])
        self.assertEqual(result['state']['offer']['version'], 1)

    def test_sent_edit_remains_sent_and_old_reply_rejected(self):
        self.sent()
        result = self.manager('offer.save', self.terms(version=1, compensation='200 000 ₽'))
        self.assertEqual(result['state']['offer']['status'], 'sent')
        self.assertEqual(result['events'][0]['type'], 'offer.updated')
        self.assert_problem(409, lambda: self.candidate('offer.reply', {'version': 1, 'value': 'accepted'}))
        self.candidate('offer.reply', {'version': 2, 'value': 'accepted'})

    def test_send_requires_version_and_candidate_willingness(self):
        self.draft()
        self.assert_problem(409, lambda: self.manager('offer.send'))
        self.candidate('after', {'meetingId': 'm1', 'value': 'no'})
        self.assert_problem(409, lambda: self.manager('offer.send', {'version': 1}))

    def test_send_retry_and_reply_retry_are_idempotent(self):
        self.sent()
        retry = self.manager('offer.send', {'version': 1})
        self.assertTrue(retry['replayed'])
        self.assertEqual(retry['events'], [])
        result = self.candidate('offer.reply', {'version': 1, 'value': 'accepted'})
        again = self.candidate('offer.reply', {'version': 1, 'value': 'accepted'})
        self.assertTrue(again['replayed'])
        self.assertEqual(again['events'], [])
        self.assertEqual(result['events'][0]['type'], 'offer.accepted')
        self.assert_problem(409, lambda: self.candidate('offer.reply', {'version': 1, 'value': 'declined'}))

    def test_accept_closes_only_correct_vacancy_preserving_fields(self):
        self.sent()
        self.candidate('offer.reply', {'version': 1, 'value': 'accepted'})
        with self.connect() as db:
            one = json.loads(db.execute("SELECT body FROM cp_vacancies WHERE id='v1'").fetchone()[0])
            two = json.loads(db.execute("SELECT body FROM cp_vacancies WHERE id='v2'").fetchone()[0])
            self.assertEqual((one['status'], one['closedBy'], one['closedByApplication']), ('closed', 'c1', 'a1'))
            self.assertEqual(one['custom'], {'retained': True})
            self.assertEqual(two['status'], 'open')
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def test_only_one_active_offer_per_vacancy_decline_allows_next(self):
        self.sent()
        self.draft('a2', 'm2')
        self.assert_problem(409, lambda: self.manager('offer.send', {'version': 1}, aid='a2'))
        self.candidate('offer.reply', {'version': 1, 'value': 'declined'})
        result = self.manager('offer.send', {'version': 1}, aid='a2')
        self.assertEqual(result['state']['offer']['status'], 'sent')
        self.assertIsNone(result['state']['closedBy'])

    def test_other_vacancy_can_have_an_active_offer_same_candidate(self):
        self.sent()
        self.sent('a3', 'm3')
        self.candidate('offer.reply', {'version': 1, 'value': 'accepted'}, aid='a3')
        self.assertEqual(self.state()['offer']['status'], 'sent')
        self.assertIsNone(self.state()['closedBy'])

    def test_chat_scopes_messages_and_binds_sender_to_authentication(self):
        m = self.manager('chat', {'text': 'Здравствуйте', 'actor': 'c2', 'candidateId': 'c2'})
        c = self.candidate('chat', {'text': 'Спасибо', 'actor': 'manager', 'candidateId': 'c2'})
        self.assertEqual(m['state']['messages'][0]['actor'], 'manager')
        self.assertEqual(c['state']['messages'][1]['actor'], 'c1')
        self.assertEqual(c['events'][0]['audience'], 'employer')
        self.assertEqual(self.state('a2')['messages'], [])
        self.assert_problem(400, lambda: self.manager('chat', {'text': 'x' * 2001}))

    def test_request_id_reuses_command_but_rejects_new_payload(self):
        body = {'text': 'Первое сообщение', 'requestId': 'stable-request'}
        self.manager('chat', body)
        second = self.manager('chat', body)
        self.assertTrue(second['replayed'])
        self.assertEqual(second['events'], [])
        self.assertEqual(len(second['state']['messages']), 1)
        self.assert_problem(409, lambda: self.manager('chat', {**body, 'text': 'Другое'}))
        self.manager('chat', body, manager='manager-2')
        self.assertEqual(len(self.state()['messages']), 2)

    def test_expected_revision_rejects_stale_draft_or_chat(self):
        self.manager('chat', {'text': 'Первое', 'expectedRevision': 0})
        self.assert_problem(409, lambda: self.manager('chat', {'text': 'Второе', 'expectedRevision': 0}))
        self.assertEqual(len(self.state()['messages']), 1)

    def test_caller_rollback_rolls_back_entire_workflow_and_dedupe(self):
        db = self.connect()
        try:
            wf.manager_action(db, 'a1', 'chat', {'text': 'Will roll back', 'requestId': 'rollback'}, manager_id='m')
            self.assertTrue(db.in_transaction)
            db.rollback()
        finally:
            db.close()
        self.assertEqual(self.state()['messages'], [])
        self.assertFalse(self.manager('chat', {'text': 'Will roll back', 'requestId': 'rollback'}, manager='m')['replayed'])

    def test_init_never_commits_unrelated_caller_writes(self):
        db = self.connect()
        try:
            db.execute("UPDATE cp_candidates SET profile='rollback-init' WHERE id='c3'")
            wf.init(db)
            self.assertTrue(db.in_transaction)
            db.rollback()
            self.assertNotEqual(db.execute("SELECT profile FROM cp_candidates WHERE id='c3'").fetchone()[0], 'rollback-init')
        finally:
            db.close()

    def test_failure_rolls_back_own_savepoint_not_unrelated_caller_write(self):
        self.draft()
        with self.connect() as db:
            db.execute("UPDATE cp_candidates SET profile='kept' WHERE id='c3'")
            with self.assertRaises(Problem):
                wf.manager_action(db, 'a1', 'offer.save', self.terms(version=7), manager_id='m')
            self.assertEqual(db.execute("SELECT profile FROM cp_candidates WHERE id='c3'").fetchone()[0], 'kept')
        self.assertEqual(self.state()['offer']['version'], 1)

    def race(self, functions):
        gate = threading.Barrier(len(functions))
        def run(fn):
            gate.wait()
            try:
                return 200, fn()
            except Problem as exc:
                return exc.status, None
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(functions)) as pool:
            return list(pool.map(run, functions))

    def test_concurrent_send_two_applications_has_one_winner(self):
        self.draft()
        self.draft('a2', 'm2')
        result = self.race([
            lambda: self.manager('offer.send', {'version': 1}),
            lambda: self.manager('offer.send', {'version': 1}, aid='a2')])
        self.assertEqual(sorted(r[0] for r in result), [200, 409])
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM cp_offer_versions WHERE status='sent'").fetchone()[0], 1)

    def test_concurrent_edit_and_answer_cannot_accept_stale_terms(self):
        self.sent()
        result = self.race([
            lambda: self.manager('offer.save', self.terms(version=1, compensation='250 000 ₽')),
            lambda: self.candidate('offer.reply', {'version': 1, 'value': 'accepted'})])
        self.assertEqual(sorted(r[0] for r in result), [200, 409])
        state = self.state()
        if state['closedBy']:
            self.assertEqual((state['offer']['version'], state['offer']['compensation']), (1, '180 000 ₽'))
        else:
            self.assertEqual((state['offer']['version'], state['offer']['status']), (2, 'sent'))

    def test_concurrent_opposite_replies_only_one_is_applied(self):
        self.sent()
        result = self.race([
            lambda: self.candidate('offer.reply', {'version': 1, 'value': 'accepted'}),
            lambda: self.candidate('offer.reply', {'version': 1, 'value': 'declined'})])
        self.assertEqual(sorted(r[0] for r in result), [200, 409])
        state = self.state()
        self.assertEqual(state['closedBy'] == 'c1', state['offer']['status'] == 'accepted')

    def test_concurrent_chat_retries_have_one_message(self):
        body = {'text': 'Один раз', 'requestId': 'one-message'}
        result = self.race([lambda: self.manager('chat', body), lambda: self.manager('chat', body)])
        self.assertEqual([r[0] for r in result], [200, 200])
        self.assertEqual(len(self.state()['messages']), 1)
        self.assertEqual(sum(len(r[1]['events']) for r in result), 1)

    def test_closed_vacancy_rejects_new_offers_and_decisions(self):
        self.draft('a2', 'm2')
        self.sent()
        self.candidate('offer.reply', {'version': 1, 'value': 'accepted'})
        self.assert_problem(409, lambda: self.manager('offer.send', {'version': 1}, aid='a2'))
        self.assert_problem(409, lambda: self.manager('decision', {'value': 'declined'}, aid='a2'))

    def test_early_reject_without_feedback_preserves_identity_and_history(self):
        with self.connect() as db:
            result = wf.reject(db, 'a1', manager_id='manager-1', vacancy_id='v1', request_id='refusal')
            self.assertEqual(result['state']['decision'], 'declined')
            self.assertEqual(result['state']['meeting']['id'], 'm1')
            self.assertEqual(result['events'][0]['type'], 'employer.declined')
        self.assertTrue(self.manager('reject')['replayed'])
        self.assert_problem(409, lambda: self.candidate('chat', {'text': 'Hello'}))
        self.assertEqual(self.state('a3')['decision'], 'review')

    def test_reject_withdraws_sent_offer_and_allows_next_candidate(self):
        original = self.sent()
        self.draft('a2', 'm2')
        result = self.manager('reject')
        self.assertEqual(result['state']['offer']['status'], 'withdrawn')
        self.assertEqual(result['events'][0]['type'], 'offer.withdrawn')
        self.assert_problem(409, lambda: self.candidate('offer.reply', {'version': 1, 'value': 'accepted'}))
        self.manager('offer.send', {'version': 1}, aid='a2')
        with self.connect() as db:
            row = db.execute('SELECT terms,status FROM cp_offer_versions WHERE id=?', (original['id'],)).fetchone()
            self.assertEqual(json.loads(row[0])['compensation'], '180 000 ₽')
            self.assertEqual(row[1], 'withdrawn')

    def test_reject_requires_adapter_to_cancel_active_meeting_first(self):
        for status in ('pending', 'confirmed', 'live'):
            with self.connect() as db:
                db.execute('UPDATE cp_meetings SET status=? WHERE id=?', (status, 'm1'))
            self.assert_problem(409, lambda: self.manager('reject'))
            self.assertEqual(self.state()['meeting']['status'], status)
        with self.connect() as db:
            db.execute("UPDATE cp_meetings SET status='cancelled' WHERE id='m1'")
        self.manager('reject')

    def test_reject_cannot_withdraw_accepted_offer(self):
        self.sent()
        self.candidate('offer.reply', {'version': 1, 'value': 'accepted'})
        self.assert_problem(409, lambda: self.manager('reject'))
        self.assertEqual(self.state()['offer']['status'], 'accepted')

    def test_queue_rejection_blocks_candidate_even_without_workflow_row(self):
        with self.connect() as db:
            db.execute('CREATE TABLE em_reviews(application_id TEXT PRIMARY KEY,state TEXT)')
            db.execute("INSERT INTO em_reviews VALUES ('a1','rejected')")
        self.assertEqual(self.state(actor='candidate', cid='c1')['decision'], 'declined')
        self.assert_problem(409, lambda: self.candidate('chat', {'text': 'Hello'}))
        self.assert_problem(409, lambda: self.candidate('after', {'meetingId': 'm1', 'value': 'yes'}))
        self.manager('reject')

    def test_invalid_action_and_boolean_version_are_rejected(self):
        self.assert_problem(400, lambda: self.manager(['chat'], {'text': 'bad'}))
        self.feedback()
        self.manager('decision', {'value': 'pool'})
        self.assert_problem(409, lambda: self.manager('offer.save', self.terms(version=False)))
        self.sent()
        self.assert_problem(409, lambda: self.candidate('offer.reply', {'version': True, 'value': 'accepted'}))


if __name__ == '__main__':
    unittest.main()
