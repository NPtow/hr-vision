"""Search pause/resume through real HTTP and isolated canonical SQLite records.

The shared fixture starts the actual API with external transport forbidden.
Historical meeting/offer rows are setup fixtures; all search changes, candidate
decisions, proposals and state reads cross the HTTP adapter.
"""
import datetime as dt
import json
import unittest
import uuid
from unittest.mock import patch

from domain import Problem, iso, now
import employer_workflow
import test_candidate_bot_http as http_fixture
from test_candidate_portal import FakeMedia


class CandidateSearchHTTPTest(unittest.TestCase):
    SECRET = http_fixture.CandidateBotHTTPTest.SECRET
    setUp = http_fixture.CandidateBotHTTPTest.setUp
    stop_server = http_fixture.CandidateBotHTTPTest.stop_server
    request = http_fixture.CandidateBotHTTPTest.request
    telegram_request = http_fixture.CandidateBotHTTPTest.telegram_request
    register = http_fixture.CandidateBotHTTPTest.register
    state = http_fixture.CandidateBotHTTPTest.state
    completed_interview = http_fixture.CandidateBotHTTPTest.completed_interview
    vacancy = http_fixture.CandidateBotHTTPTest.vacancy
    application = http_fixture.CandidateBotHTTPTest.application
    application_state = http_fixture.CandidateBotHTTPTest.application_state
    proposed_meeting = http_fixture.CandidateBotHTTPTest.proposed_meeting

    def search(self, candidate, searching, *, status=200):
        code, body, _ = self.request('candidate/search', {'searching': searching}, cookie=candidate['cookie'])
        self.assertEqual(code, status, body)
        if status == 200:
            self.assertEqual(body, self.state(candidate))
        return body

    def decide(self, candidate, application, decision):
        code, body, _ = self.request('candidate/application/decision', {
            'applicationId': application['id'], 'decision': decision}, cookie=candidate['cookie'])
        self.assertEqual(code, 200, body)

    def propose(self, candidate, vacancy, *, status=200):
        code, body, _ = self.request('recruiter/applications', {
            'candidateId': candidate['id'], 'vacancyId': vacancy['id']}, token=self.manager)
        self.assertEqual(code, status, body)
        return body

    def history_fixture(self, candidate, application):
        meeting = self.proposed_meeting(candidate, application)
        offer_id, message_id = 'offer-' + uuid.uuid4().hex, 'message-' + uuid.uuid4().hex
        terms = {'role': 'Менеджер', 'compensation': '180 000 ₽', 'format': 'Москва, офис',
                 'expectations': 'Работа с клиентами', 'startDate': (now() + dt.timedelta(days=30)).date().isoformat()}
        with self.app.connect() as db:
            db.execute("UPDATE cp_meetings SET status='completed',confirmed_at=?,completed_at=? WHERE id=?",
                       (iso(now()), iso(now()), meeting['id']))
            db.execute('''INSERT INTO cp_offer_versions
                (id,application_id,vacancy_id,version,terms,status,created_by,created_at,sent_at)
                VALUES (?,?,?,1,?,'sent','fixture-manager',?,?)''',
                       (offer_id, application['id'], application['vacancy']['id'], json.dumps(terms), iso(now()), iso(now())))
            db.execute('''INSERT INTO cp_workflow_messages
                (id,application_id,actor,actor_id,text,created_at) VALUES (?,?,'candidate',?,?,?)''',
                       (message_id, application['id'], candidate['id'], 'Сохранённое сообщение кандидата', iso(now())))
        return meeting, offer_id, message_id

    def snapshot(self, candidate):
        with self.app.connect() as db:
            return {
                'candidate': db.execute('SELECT * FROM cp_candidates WHERE id=?', (candidate['id'],)).fetchone(),
                'applications': db.execute('SELECT * FROM cp_applications WHERE candidate_id=? ORDER BY id',
                                           (candidate['id'],)).fetchall(),
                'meetings': db.execute('''SELECT m.* FROM cp_meetings m JOIN cp_applications a
                    ON a.id=m.application_id WHERE a.candidate_id=? ORDER BY m.id''', (candidate['id'],)).fetchall(),
                'interviews': db.execute('SELECT * FROM cp_interviews WHERE candidate_id=?', (candidate['id'],)).fetchall(),
            }

    def test_default_searching_and_unarchived_application_survive_reload(self):
        owner = self.register()
        self.assertIs(owner['searching'], True)
        application = self.application(owner, 'SEARCH_DEFAULT')
        self.assertIsNone(application['archivedAt'])
        self.assertIs(self.state(owner)['searching'], True)
        code, session, _ = self.request('candidate/session', cookie=owner['cookie'])
        self.assertEqual(code, 200, session)
        self.assertIs(session['candidate']['searching'], True)
        self.assertIsNone(session['candidate']['applications'][0]['archivedAt'])

    def test_search_requires_candidate_owner_session_and_strict_boolean(self):
        owner, other = self.register(), self.register()
        self.assertEqual(self.request('candidate/search', {'searching': False})[0], 401)
        self.assertEqual(self.request('candidate/search', {'searching': False}, token=self.manager)[0], 401)
        self.assertEqual(self.request('candidate/search', {'searching': False}, cookie=owner['cookie'],
            headers={'Origin': 'https://untrusted.example'})[0], 403)
        for payload in ({}, {'searching': 0}, {'searching': 1}, {'searching': 'false'}, {'searching': None},
                        {'searching': False, 'candidateId': other['id']}):
            code, body, _ = self.request('candidate/search', payload, cookie=owner['cookie'])
            self.assertEqual(code, 400, (payload, body))
        self.assertTrue(self.state(owner)['searching'])
        self.assertTrue(self.state(other)['searching'])
        self.search(other, False)
        self.assertTrue(self.state(owner)['searching'])
        self.assertFalse(self.state(other)['searching'])

    def test_pause_archives_all_statuses_once_and_preserves_history_and_other_owner(self):
        owner, other = self.register(), self.register()
        pending = self.application(owner, 'SEARCH_PENDING')
        accepted = self.application(owner, 'SEARCH_ACCEPTED')
        declined = self.application(owner, 'SEARCH_DECLINED')
        foreign = self.application(other, 'SEARCH_FOREIGN')
        self.decide(owner, declined, 'declined')
        historical, offer_id, message_id = self.history_fixture(owner, accepted)
        with self.app.connect() as db:
            completed_before = db.execute('SELECT * FROM cp_meetings WHERE id=?', (historical['id'],)).fetchone()
            offer_before = db.execute('SELECT * FROM cp_offer_versions WHERE id=?', (offer_id,)).fetchone()
            message_before = db.execute('SELECT * FROM cp_workflow_messages WHERE id=?', (message_id,)).fetchone()
            interview_before = db.execute('SELECT * FROM cp_interviews WHERE candidate_id=?', (owner['id'],)).fetchone()
        paused = self.search(owner, False)
        self.assertFalse(paused['searching'])
        self.assertTrue(paused['consentReuse'])
        self.assertFalse(paused['autoApprove'])
        self.assertEqual({item['id'] for item in paused['applications']}, {pending['id'], accepted['id'], declined['id']})
        timestamps = {item['archivedAt'] for item in paused['applications']}
        self.assertEqual(len(timestamps), 1)
        self.assertNotIn(None, timestamps)
        self.assertEqual({item['id']: item['status'] for item in paused['applications']},
                         {pending['id']: 'pending', accepted['id']: 'accepted', declined['id']: 'declined'})
        self.assertTrue(self.state(other)['searching'])
        self.assertIsNone(self.application_state(other, foreign)['archivedAt'])
        archived = self.application_state(owner, accepted)
        self.assertEqual(archived['offer']['id'], offer_id)
        self.assertEqual(archived['messages'][0]['id'], message_id)
        self.assertEqual(archived['meetings'][0]['id'], historical['id'])
        self.search(owner, False)
        self.assertEqual({item['archivedAt'] for item in self.state(owner)['applications']}, timestamps)
        with self.app.connect() as db:
            self.assertEqual(db.execute('SELECT * FROM cp_meetings WHERE id=?', (historical['id'],)).fetchone(), completed_before)
            withdrawn = db.execute('SELECT * FROM cp_offer_versions WHERE id=?', (offer_id,)).fetchone()
            self.assertEqual(withdrawn[5], 'withdrawn')
            self.assertEqual(withdrawn[:5] + withdrawn[6:], offer_before[:5] + offer_before[6:])
            self.assertEqual(db.execute('SELECT * FROM cp_workflow_messages WHERE id=?', (message_id,)).fetchone(), message_before)
            self.assertEqual(db.execute('SELECT * FROM cp_interviews WHERE candidate_id=?', (owner['id'],)).fetchone(), interview_before)
        # Keeping the old offer as sent would leave its partial unique index
        # blocking an employer from offering this vacancy to another candidate.
        self.propose(other, accepted['vacancy'])
        replacement = next(item for item in self.state(other)['applications']
                           if item['vacancy']['id'] == accepted['vacancy']['id'])
        _, replacement_offer, _ = self.history_fixture(other, replacement)
        with self.app.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM cp_offer_versions WHERE id=?', (offer_id,)).fetchone()[0], 'withdrawn')
            self.assertEqual(db.execute('SELECT status FROM cp_offer_versions WHERE id=?', (replacement_offer,)).fetchone()[0], 'sent')

    def test_pause_cancels_pending_and_confirmed_meetings_preserving_exact_history(self):
        owner = self.register()
        first_app = self.application(owner, 'SEARCH_PENDING_MEETING')
        second_app = self.application(owner, 'SEARCH_CONFIRMED_MEETING')
        first = self.proposed_meeting(owner, first_app, hours=1)
        second = self.proposed_meeting(owner, second_app, hours=3)
        code, body, _ = self.request('candidate/meeting/confirm', {
            'applicationId': second_app['id'], 'meetingId': second['id']}, cookie=owner['cookie'])
        self.assertEqual(code, 200, body)
        self.search(owner, False)
        for application, original in ((first_app, first), (second_app, second)):
            current = self.application_state(owner, application)
            self.assertIsNone(current['meeting'])
            history = current['meetings'][0]
            self.assertEqual((history['id'], history['start'], history['end']),
                             (original['id'], original['start'], original['end']))
            self.assertEqual(history['status'], 'cancelled')
            self.assertEqual(history['cancellationReason'], 'search_stopped')
            self.assertIsNotNone(history['cancelledAt'])

    def test_resume_keeps_history_archived_and_allows_new_application_for_same_vacancy(self):
        owner = self.register()
        old = self.application(owner, 'SEARCH_REOFFER')
        self.search(owner, False)
        archived_at = self.application_state(owner, old)['archivedAt']
        additional_vacancy = self.vacancy('SEARCH_NEW_AFTER_RESUME')
        self.propose(owner, old['vacancy'], status=409)
        self.propose(owner, additional_vacancy, status=409)
        self.search(owner, True)
        self.assertEqual(self.application_state(owner, old)['archivedAt'], archived_at)
        self.assertEqual(len(self.state(owner)['applications']), 1)
        self.propose(owner, old['vacancy'])
        self.propose(owner, old['vacancy'])  # Repeated proposal still deduplicates the active application.
        matching = [item for item in self.state(owner)['applications'] if item['vacancy']['id'] == old['vacancy']['id']]
        self.assertEqual(len(matching), 2)
        fresh = next(item for item in matching if item['id'] != old['id'])
        self.assertIsNone(fresh['archivedAt'])
        self.assertEqual(fresh['status'], 'pending')
        self.propose(owner, additional_vacancy)
        self.assertEqual(len(self.state(owner)['applications']), 3)
        with self.app.connect() as db:
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_live_employer_meeting_rejects_pause_without_partial_changes(self):
        owner = self.register()
        self.application(owner, 'SEARCH_UNRELATED_PENDING')
        application = self.application(owner, 'SEARCH_LIVE_EMPLOYER_CALL')
        meeting = self.proposed_meeting(owner, application)
        with self.app.connect() as db:
            db.execute("UPDATE cp_meetings SET status='live' WHERE id=?", (meeting['id'],))
        before = self.snapshot(owner)
        self.search(owner, False, status=409)
        self.assertEqual(self.snapshot(owner), before)
        self.assertTrue(self.state(owner)['searching'])
        self.assertTrue(all(item['archivedAt'] is None for item in self.state(owner)['applications']))

    def test_live_recruiter_interview_rejects_pause_without_partial_changes(self):
        owner = self.register()
        self.application(owner, 'SEARCH_LIVE_RECRUITER_CALL')
        with self.app.connect() as db:
            db.execute("UPDATE cp_interviews SET status='live',completed_at=NULL WHERE candidate_id=?", (owner['id'],))
        before = self.snapshot(owner)
        self.search(owner, False, status=409)
        self.assertEqual(self.snapshot(owner), before)
        self.assertTrue(self.state(owner)['searching'])

    def test_booked_recruiter_interview_remains_booked_when_search_is_paused(self):
        owner = self.register()
        self.completed_interview(owner)
        with self.app.connect() as db:
            db.execute("UPDATE cp_interviews SET status='booked',completed_at=NULL WHERE candidate_id=?", (owner['id'],))
            before = db.execute('SELECT * FROM cp_interviews WHERE candidate_id=?', (owner['id'],)).fetchone()
        paused = self.search(owner, False)
        self.assertEqual(paused['interview']['status'], 'booked')
        self.assertEqual(paused['interview']['id'], before[0])
        with self.app.connect() as db:
            self.assertEqual(db.execute('SELECT * FROM cp_interviews WHERE candidate_id=?', (owner['id'],)).fetchone(), before)

    def test_archived_application_rejects_decision_meeting_and_workflow_mutations(self):
        owner = self.register()
        pending = self.application(owner, 'SEARCH_ARCHIVED_PENDING')
        accepted = self.application(owner, 'SEARCH_ARCHIVED_ACCEPTED')
        meeting = self.proposed_meeting(owner, accepted)
        self.search(owner, False)
        self.search(owner, True)
        before = self.snapshot(owner)
        requests = [
            ('candidate/application/decision', {'applicationId': pending['id'], 'decision': 'accepted'}),
            ('candidate/meeting/confirm', {'applicationId': accepted['id'], 'meetingId': meeting['id']}),
            ('candidate/meeting/decline', {'applicationId': accepted['id'], 'meetingId': meeting['id']}),
            ('candidate/workflow', {'applicationId': accepted['id'], 'action': 'chat', 'text': 'Must not be sent'}),
        ]
        for path, payload in requests:
            code, body, _ = self.request(path, payload, cookie=owner['cookie'])
            self.assertEqual(code, 409, (path, body))
        self.assertEqual(self.snapshot(owner), before)
        self.assertEqual(self.application_state(owner, accepted)['messages'], [])

    def test_archive_write_failure_rolls_back_searching_and_all_applications(self):
        owner = self.register()
        self.application(owner, 'SEARCH_ROLLBACK_FIRST')
        broken = self.application(owner, 'SEARCH_ROLLBACK_SECOND')
        before = self.snapshot(owner)
        with self.app.connect() as db:
            # A failure on one archive write must undo the profile change and all
            # other archive writes from the same request.
            db.execute('CREATE TABLE search_failure_target (id TEXT PRIMARY KEY)')
            db.execute('INSERT INTO search_failure_target VALUES (?)', (broken['id'],))
            db.execute('''CREATE TRIGGER fail_search_archive BEFORE UPDATE OF archived_at ON cp_applications
                WHEN NEW.id IN (SELECT id FROM search_failure_target)
                BEGIN SELECT RAISE(ABORT,'isolated test archive failure'); END''')
        self.search(owner, False, status=500)
        self.assertEqual(self.snapshot(owner), before)

    def test_accepted_offer_and_closed_vacancy_outcome_survive_pause_and_resume(self):
        owner = self.register()
        application = self.application(owner, 'SEARCH_ACCEPTED_OFFER')
        _, offer_id, _ = self.history_fixture(owner, application)
        code, body, _ = self.request('candidate/workflow', {
            'applicationId': application['id'], 'action': 'offer.reply',
            'version': 1, 'value': 'accepted'}, cookie=owner['cookie'])
        self.assertEqual(code, 200, body)
        with self.app.connect() as db:
            offer_before = db.execute('SELECT * FROM cp_offer_versions WHERE id=?', (offer_id,)).fetchone()
            outcome_before = db.execute('SELECT * FROM cp_vacancy_outcomes WHERE vacancy_id=?',
                                        (application['vacancy']['id'],)).fetchone()
            vacancy_before = db.execute('SELECT body FROM cp_vacancies WHERE id=?',
                                        (application['vacancy']['id'],)).fetchone()[0]
        self.assertIsNotNone(outcome_before)
        self.assertEqual(offer_before[5], 'accepted')
        self.assertEqual(json.loads(vacancy_before)['status'], 'closed')
        for searching in (False, True):
            self.search(owner, searching)
            archived = self.application_state(owner, application)
            self.assertIsNotNone(archived['archivedAt'])
            self.assertEqual(archived['offer']['status'], 'accepted')
            self.assertEqual(archived['workflow']['closedBy'], owner['id'])
            with self.app.connect() as db:
                self.assertEqual(db.execute('SELECT * FROM cp_offer_versions WHERE id=?', (offer_id,)).fetchone(), offer_before)
                self.assertEqual(db.execute('SELECT * FROM cp_vacancy_outcomes WHERE vacancy_id=?',
                                            (application['vacancy']['id'],)).fetchone(), outcome_before)
                self.assertEqual(db.execute('SELECT body FROM cp_vacancies WHERE id=?',
                                            (application['vacancy']['id'],)).fetchone()[0], vacancy_before)

    def test_employer_chat_and_old_idempotent_replay_are_blocked_after_archive_and_resume(self):
        owner = self.register()
        application = self.application(owner, 'SEARCH_ARCHIVED_EMPLOYER_CHAT')
        self.decide(owner, application, 'accepted')
        original = {'text': 'Сообщение работодателя до паузы', 'requestId': 'before-search-pause'}
        with self.app.connect() as db:
            first = employer_workflow.manager_action(db, application['id'], 'chat', original,
                                                     manager_id='fixture-manager')
        self.assertFalse(first['replayed'])
        with self.app.connect() as db:
            replay = employer_workflow.manager_action(db, application['id'], 'chat', original,
                                                      manager_id='fixture-manager')
        self.assertTrue(replay['replayed'])
        self.search(owner, False)
        self.search(owner, True)
        with self.app.connect() as db:
            before = {table: db.execute('SELECT * FROM ' + table + ' WHERE application_id=?',
                                        (application['id'],)).fetchall()
                      for table in ('cp_workflow_messages', 'cp_workflow_commands', 'cp_employer_workflows')}
        for payload in (original, {'text': 'Новая попытка после паузы', 'requestId': 'after-search-pause'}):
            with self.assertRaises(Problem) as caught:
                with self.app.connect() as db:
                    employer_workflow.manager_action(db, application['id'], 'chat', payload,
                                                     manager_id='fixture-manager')
            self.assertEqual(caught.exception.status, 409)
        with self.app.connect() as db:
            for table, rows in before.items():
                self.assertEqual(db.execute('SELECT * FROM ' + table + ' WHERE application_id=?',
                                            (application['id'],)).fetchall(), rows)
        messages = self.application_state(owner, application)['messages']
        self.assertEqual([message['text'] for message in messages], [original['text']])

    def test_pause_during_provider_room_creation_prevents_join_from_reactivating_meeting(self):
        self.start = now().replace(microsecond=0) + dt.timedelta(minutes=5)
        owner = self.register()
        application = self.application(owner, 'SEARCH_ROOM_CREATION_RACE')
        meeting = self.proposed_meeting(owner, application, hours=0)
        code, body, _ = self.request('candidate/meeting/confirm', {
            'applicationId': application['id'], 'meetingId': meeting['id']}, cookie=owner['cookie'])
        self.assertEqual(code, 200, body)
        media = FakeMedia()
        self.app.CANDIDATE_PORTAL.media = media
        original_room = media.room

        def stop_search_during_room_creation(mid, **kwargs):
            # A second HTTP request changes state while the first request is in
            # its fake provider I/O, before its final database revalidation.
            self.search(owner, False)
            return original_room(mid, **kwargs)

        with patch.object(media, 'room', side_effect=stop_search_during_room_creation):
            code, body, _ = self.request('recruiter/meeting/join', {
                'candidateId': owner['id'], 'applicationId': application['id'],
                'meetingId': meeting['id'], 'consent': True}, token=self.manager)
        self.assertEqual(code, 409, body)
        self.assertEqual(media.rooms, [meeting['id']])
        self.assertNotIn('token', body)
        self.assertFalse(self.state(owner)['searching'])
        self.assertIsNotNone(self.application_state(owner, application)['archivedAt'])
        with self.app.connect() as db:
            row = db.execute('SELECT status,cancellation_reason,room FROM cp_meetings WHERE id=?',
                             (meeting['id'],)).fetchone()
            self.assertEqual(row, ('cancelled', 'search_stopped', None))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_media_consents WHERE meeting_id=?',
                                        (meeting['id'],)).fetchone()[0], 0)
        self.assertEqual(self.app.CANDIDATE_PORTAL._media_locks, {})


if __name__ == '__main__':
    unittest.main()
