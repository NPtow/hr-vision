"""Media lifecycle regressions with isolated accounts and synthetic provider data.

These tests prove authorization/state handling, never a real Daily recording.
"""
import concurrent.futures
import datetime as dt
import io
import json
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from candidate_portal import CandidatePortal, MEDIA_WAIT_SECONDS
from daily_media import ProviderError
from domain import Problem, iso, now
import test_candidate_portal as fixture


class MaterialMedia(fixture.FakeMedia):
    def __init__(self):
        super().__init__()
        self.video_rows, self.text_rows = {}, {}
        self.video_error = self.text_error = False
        self.expiry = 2000000000
        self.failed_files = set()

    def request(self, path, body=None):
        parsed = urlsplit(path)
        query = parse_qs(parsed.query)
        if parsed.path == '/recordings':
            if self.video_error:
                raise ProviderError(503)
            return {'data': self.video_rows.get(query['room_name'][0], [])}
        if parsed.path == '/transcript':
            if self.text_error:
                raise ProviderError(503)
            return {'data': self.text_rows.get(query['roomId'][0], [])}
        if path.endswith('/access-link'):
            rid = path.split('/')[2]
            if rid in self.failed_files:
                raise ProviderError(503)
            return {'download_link': f'https://media.example/{rid}?expires={self.expiry}', 'expires': self.expiry}
        return super().request(path, body)

    def ready(self, room):
        self.video_rows[room['name']] = [{'id': room['id'] + '-video', 'room_name': room['name'],
                                         'status': 'finished', 'duration': 32}]
        self.text_rows[room['id']] = [{'transcriptId': room['id'] + '-text', 'roomId': room['id'], 'status': 't_finished'}]


class CandidateMediaTest(unittest.TestCase):
    tearDown = fixture.CandidatePortalTest.tearDown
    connect = fixture.CandidatePortalTest.connect
    payload = fixture.CandidatePortalTest.payload
    call = fixture.CandidatePortalTest.call
    not_manager = staticmethod(fixture.CandidatePortalTest.not_manager)
    register = fixture.CandidatePortalTest.register
    slot = fixture.CandidatePortalTest.slot
    completed_person = fixture.CandidatePortalTest.completed_person
    vacancy = fixture.CandidatePortalTest.vacancy
    propose = fixture.CandidatePortalTest.propose
    assert_problem = fixture.CandidatePortalTest.assert_problem

    def setUp(self):
        fixture.CandidatePortalTest.setUp(self)
        self.media = MaterialMedia()
        self.portal.media = self.media

    def interview(self, person):
        with self.connect() as db:
            return self.portal.interview(db, person['id'])

    def fetch(self, person):
        return self.call('recruiter/interview/materials', {'candidateId': person['id']}, manager=True)[0]

    def test_imported_interview_uses_existing_authorization_and_completion_guard(self):
        from test_dsa_panel import imported_fixture
        from unittest.mock import Mock
        person, _ = self.completed_person()
        iid = self.interview(person)['id']
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO cp_internal_profiles VALUES (?,?)',
                       (person['id'], json.dumps({'importedInterview': imported_fixture(person['id'], iid)})))
        self.portal.materials_catalog = Mock()
        result = self.fetch(person)
        self.assertEqual(result['video']['status'], 'ready')
        self.portal.materials_catalog.register_owner.assert_not_called()
        with self.assertRaises(Problem):
            self.portal.materials(person['id'], access_check=lambda db: (_ for _ in ()).throw(Problem('denied', 403)))
        with self.connect() as db:
            db.execute("UPDATE cp_interviews SET status='booked' WHERE id=?", (iid,))
        with self.assertRaises(Problem): self.fetch(person)

    def test_imported_screening_does_not_replace_later_meeting_materials(self):
        from test_dsa_panel import imported_fixture
        person, cookie = self.completed_person()
        iid = self.interview(person)['id']
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO cp_internal_profiles VALUES (?,?)',
                       (person['id'], json.dumps({'importedInterview': imported_fixture(person['id'], iid)})))
        app, meeting = self.completed_meeting(person, cookie)
        result = self.portal.materials(person['id'], app['id'], meeting['id'])
        self.assertNotEqual(result.get('source', {}).get('kind'), 'dsa-panel')

    def requested(self, person):
        with self.connect() as db:
            db.execute('UPDATE cp_recording_control SET start_requested_at=?,recording_requested=1 WHERE interview_id=?',
                       (iso(now()), self.interview(person)['id']))

    def age(self, person):
        with self.connect() as db:
            db.execute('UPDATE cp_interviews SET completed_at=? WHERE candidate_id=?',
                       (iso(now() - dt.timedelta(seconds=MEDIA_WAIT_SECONDS + 1)), person['id']))

    def completed_meeting(self, person, cookie, minutes=0):
        app = self.propose(person, self.vacancy())
        self.call('candidate/application/decision', {'applicationId': app['id'], 'decision': 'accepted'}, cookie)
        self.call('recruiter/meetings', {'applicationId': app['id'], 'start': iso(self.tick + dt.timedelta(minutes=minutes))}, manager=True)
        with self.connect() as db:
            meeting = self.portal.meeting(db, app['id'])
        body = {'applicationId': app['id'], 'meetingId': meeting['id']}
        self.call('candidate/meeting/confirm', body, cookie)
        # The test clock enters each meeting's existing allowed join interval.
        with patch('candidate_portal.now', return_value=self.tick + dt.timedelta(minutes=minutes)):
            self.call('recruiter/meeting/join', body, manager=True)
            self.call('recruiter/meeting/complete', body, manager=True)
        with self.connect() as db:
            return app, self.portal.meeting(db, app['id'])

    def test_owner_token_and_completion_do_not_invent_recording_or_attendance(self):
        person, _ = self.completed_person()
        data = self.fetch(person)
        self.assertEqual(data['status'], 'processing')
        self.assertEqual(data['video']['status'], 'processing')
        self.assertEqual(data['recordings'], [])
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM cp_media_consents').fetchone()[0], 0)
        self.assertFalse(self.interview(person)['recording']['startRequested'])

    def test_only_explicitly_unstarted_prepared_intro_is_not_recorded(self):
        import test_candidate_invites as invited
        person = invited.CandidateInvitationTest.prepared(self)
        self.call('recruiter/interview/book', {'candidateId': person['id'], 'slotId': self.slot()['id']}, manager=True)
        self.call('recruiter/interview/join', {'candidateId': person['id']}, manager=True)
        with patch.object(self.media, 'finish', wraps=self.media.finish) as finish:
            self.call('recruiter/interview/complete', {'candidateId': person['id']}, manager=True)
        self.assertTrue(finish.call_args.args[0]['captureNeverStarted'])
        data = self.fetch(person)
        self.assertEqual(data['status'], 'not_recorded')
        self.assertEqual(data['video']['status'], 'not_recorded')
        self.assertEqual(data['transcription']['status'], 'not_recorded')

    def test_requested_but_absent_is_bounded_then_retry_can_recover(self):
        person, _ = self.completed_person()
        self.requested(person)
        self.assertEqual(self.fetch(person)['status'], 'processing')
        self.age(person)
        self.assertEqual(self.fetch(person)['status'], 'error')
        room = self.interview(person)['room']
        self.media.ready(room)
        self.media.text_rows.clear()
        self.assertEqual(self.fetch(person)['video']['status'], 'ready')

    def test_legacy_unknown_capture_also_stops_waiting(self):
        person, _ = self.completed_person()
        with self.connect() as db:
            db.execute('DELETE FROM cp_recording_control')
        self.assertEqual(self.fetch(person)['status'], 'processing')
        self.age(person)
        self.assertEqual(self.fetch(person)['status'], 'error')

    def test_provider_processing_past_deadline_is_retriable_error(self):
        person, _ = self.completed_person()
        room = self.interview(person)['room']
        self.media.video_rows[room['name']] = [{'room_name': room['name'], 'id': 'pending', 'status': 'processing'}]
        self.age(person)
        data = self.fetch(person)
        self.assertEqual(data['video']['status'], 'error')
        self.assertIn('ещё раз', data['video']['error'])

    def test_transcription_failure_does_not_hide_ready_video_or_expiry(self):
        person, _ = self.completed_person()
        self.media.ready(self.interview(person)['room'])
        self.media.text_error = True
        data = self.fetch(person)
        self.assertEqual(data['status'], 'ready')
        self.assertEqual(data['transcription']['status'], 'error')
        self.assertEqual(data['recordings'][0]['expires'], self.media.expiry)
        self.assertNotIn('HTTP', data['transcription']['error'])

    def test_video_failure_does_not_hide_ready_transcript(self):
        person, _ = self.completed_person()
        self.media.ready(self.interview(person)['room'])
        self.media.video_error = True
        with patch('daily_media.urllib.request.urlopen', return_value=io.BytesIO(b'WEBVTT\nprivate transcript')):
            data = self.fetch(person)
        self.assertEqual(data['video']['status'], 'error')
        self.assertEqual(data['transcription']['status'], 'ready')
        self.assertEqual(data['transcripts'][0]['text'], 'WEBVTT\nprivate transcript')

    def test_one_failed_file_does_not_hide_other_ready_parts(self):
        person, _ = self.completed_person()
        room = self.interview(person)['room']
        self.media.video_rows[room['name']] = [{'id': rid, 'room_name': room['name'], 'status': 'finished'}
                                               for rid in ('good', 'broken')]
        self.media.failed_files.add('broken')
        data = self.fetch(person)
        self.assertEqual([r['id'] for r in data['recordings']], ['good'])
        self.assertEqual(data['video']['status'], 'ready')
        self.assertIn('error', data['video'])

    def test_metadata_survives_restart_without_signed_links_or_transcript_text(self):
        person, cookie = self.completed_person()
        room = self.interview(person)['room']
        self.media.ready(room)
        with patch('daily_media.urllib.request.urlopen', return_value=io.BytesIO(b'PRIVATE TEXT')):
            original = self.fetch(person)
        with self.connect() as db:
            saved = db.execute('SELECT body FROM cp_media_state').fetchone()[0]
        self.assertNotIn('https:', saved)
        self.assertNotIn('PRIVATE TEXT', saved)
        self.assertIn(room['id'] + '-video', saved)
        self.portal = CandidatePortal(self.connect, threading.RLock(), self.media, 'https://portal.example')
        self.media.video_error = self.media.text_error = True
        self.media.expiry += 3600
        with patch('daily_media.urllib.request.urlopen', return_value=io.BytesIO(b'PRIVATE TEXT')):
            refreshed = self.call('candidate/interview/materials', {}, cookie)[0]
        self.assertEqual(refreshed['video']['status'], 'ready')
        self.assertIn('error', refreshed['video'])
        self.assertNotEqual(refreshed['recordings'][0]['url'], original['recordings'][0]['url'])
        self.assertEqual(refreshed['recordings'][0]['expires'], self.media.expiry)

    def test_candidate_materials_use_session_identity_never_supplied_person(self):
        person, cookie = self.completed_person()
        other, other_cookie = self.register()
        self.media.ready(self.interview(person)['room'])
        self.media.text_rows.clear()
        self.assertEqual(self.call('candidate/interview/materials', {}, cookie)[0]['video']['status'], 'ready')
        self.assert_problem(409, 'candidate/interview/materials', {'candidateId': person['id']}, other_cookie)
        self.assert_problem(401, 'candidate/interview/materials', {'candidateId': person['id']})

    def test_meeting_materials_bound_to_person_application_and_exact_meeting(self):
        person, cookie = self.completed_person()
        other, other_cookie = self.register()
        app, meeting = self.completed_meeting(person, cookie)
        self.media.ready(meeting['room'])
        self.media.text_rows.clear()
        body = {'applicationId': app['id'], 'meetingId': meeting['id']}
        data = self.call('candidate/meeting/materials', body, cookie)[0]
        self.assertEqual(data['recordings'][0]['id'], meeting['id'] + '-video')
        self.assert_problem(404, 'candidate/meeting/materials', body, other_cookie)
        self.assert_problem(401, 'candidate/meeting/materials', body)
        self.assert_problem(409, 'recruiter/meeting/materials', {'applicationId': app['id']}, manager=True)
        self.assert_problem(409, 'recruiter/meeting/materials', {**body, 'meetingId': 'stale'}, manager=True)
        self.assert_problem(404, 'recruiter/meeting/materials', {**body, 'candidateId': other['id']}, manager=True)
        self.assertEqual(self.call('recruiter/meeting/materials', {**body, 'candidateId': person['id']}, manager=True)[0]['video']['status'], 'ready')

    def test_primary_interview_and_two_vacancies_never_mix_materials(self):
        person, cookie = self.completed_person()
        one, first = self.completed_meeting(person, cookie)
        two, second = self.completed_meeting(person, cookie, 40)
        primary = self.interview(person)
        for meeting in (primary, first, second):
            self.media.ready(meeting['room'])
        self.media.text_rows.clear()
        # A provider list can include foreign rows even when a filter was sent.
        self.media.video_rows[first['room']['name']].extend(self.media.video_rows[second['room']['name']])
        self.assertEqual(self.fetch(person)['recordings'][0]['id'], primary['id'] + '-video')
        for app, meeting in ((one, first), (two, second)):
            data = self.call('candidate/meeting/materials', {'applicationId': app['id'], 'meetingId': meeting['id']}, cookie)[0]
            self.assertEqual([r['id'] for r in data['recordings']], [meeting['id'] + '-video'])

    def test_closed_tab_after_join_window_can_finish_without_rejoining(self):
        person, cookie = self.register()
        self.call('candidate/book', {'slotId': self.slot()['id']}, cookie)
        self.call('recruiter/interview/join', {'candidateId': person['id']}, manager=True)
        before = self.interview(person)
        with patch('candidate_portal.now', return_value=self.tick + dt.timedelta(hours=3)):
            self.assert_problem(409, 'candidate/interview/join', {'consent': True}, cookie)
            self.assert_problem(409, 'recruiter/interview/join', {'candidateId': person['id']}, manager=True)
            self.call('recruiter/interview/complete', {'candidateId': person['id']}, manager=True)
        after = self.interview(person)
        self.assertEqual(after['status'], 'completed')
        self.assertEqual(after['id'], before['id'])
        self.assertEqual(after['room'], before['room'])
        self.assertEqual(self.media.finished, [before['id']])
        self.assertEqual(self.fetch(person)['status'], 'processing')

    def test_failed_provider_finish_keeps_live_and_retry_preserves_identity(self):
        person, cookie = self.register()
        self.call('candidate/book', {'slotId': self.slot()['id']}, cookie)
        self.call('recruiter/interview/join', {'candidateId': person['id']}, manager=True)
        before = self.interview(person)
        with patch.object(self.media, 'finish', side_effect=Problem('Завершение не подтверждено', 502)):
            self.assert_problem(502, 'recruiter/interview/complete', {'candidateId': person['id']}, manager=True)
        self.assertEqual(self.interview(person)['status'], 'live')
        self.call('recruiter/interview/complete', {'candidateId': person['id']}, manager=True)
        self.assertEqual(self.interview(person)['id'], before['id'])
        self.assertEqual(self.interview(person)['status'], 'completed')
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM fake_events WHERE type='interview.completed'").fetchone()[0], 1)

    def test_slow_materials_does_not_block_another_candidates_state(self):
        person, _ = self.completed_person()
        other, other_cookie = self.register()
        entered, release = threading.Event(), threading.Event()
        original = self.media.materials
        def slow(room, known):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(room, known)
        self.media.materials = slow
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(self.fetch, person)
            self.assertTrue(entered.wait(3))
            try:
                state = pool.submit(self.call, 'candidate/state', cookie=other_cookie).result(timeout=1)[0]
                self.assertEqual(state['id'], other['id'])
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=3)['status'], 'processing')


if __name__ == '__main__':
    unittest.main()
