"""Exact meeting/recording annotations on temporary SQLite data; no provider calls."""
import concurrent.futures
import json
import unittest
import uuid

from domain import Problem, iso, now
from materials_repository import MaterialsRepository
import employer_workflow as wf
import test_employer_workflow as fixture


class MeetingCommentsTest(unittest.TestCase):
    tearDown = fixture.WorkflowTest.tearDown
    connect = fixture.WorkflowTest.connect
    add_meeting = fixture.WorkflowTest.add_meeting
    manager = fixture.WorkflowTest.manager
    candidate = fixture.WorkflowTest.candidate
    state = fixture.WorkflowTest.state
    assert_problem = fixture.WorkflowTest.assert_problem

    def setUp(self):
        fixture.WorkflowTest.setUp(self)
        self.room = {'id': 'meeting-room-one', 'name': 'hrv-m1'}
        with self.connect() as db:
            db.execute('''CREATE TABLE cp_media_state (
              meeting_id TEXT PRIMARY KEY,kind TEXT,room_id TEXT,body TEXT,updated_at TEXT)''')
            db.execute('UPDATE cp_meetings SET room=? WHERE id=?', (json.dumps(self.room), 'm1'))
            db.execute('INSERT INTO cp_media_state VALUES (?,?,?,?,?)',
                       ('m1', 'meeting', json.dumps(self.room),
                        json.dumps({'recordings': [{'id': 'recording-one', 'duration': 30}]}), iso(now())))

    def body(self, **changes):
        return {'meetingId': 'm1', 'recordingId': 'recording-one', 'seconds': 12.5,
                'text': 'Конкретный пример работы с клиентом', 'chapterTitle': 'Опыт',
                'requestId': uuid.uuid4().hex, 'expectedRevision': self.state()['revision'], **changes}

    def comment(self, body=None, **kwargs):
        return self.manager('meeting.comment.add', self.body() if body is None else body, **kwargs)

    def catalog_asset(self, asset_id='catalog-video', *, meeting_id='m1', provider='zoom',
                      kind='video', state='ready', disabled=False):
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS cp_interviews(
              id TEXT PRIMARY KEY,candidate_id TEXT,slot_id TEXT,status TEXT,created_at TEXT,completed_at TEXT)''')
            db.execute('CREATE TABLE IF NOT EXISTS cp_recruiter_slots(id TEXT PRIMARY KEY,start TEXT)')
            MaterialsRepository(self.connect).init(db)
            sid = 'source-' + asset_id
            db.execute('''INSERT INTO media_sources
              (id,meeting_id,provider,connection_id,capture_identity,source_ref,storage_policy,
               state,metadata,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
              (sid, meeting_id, provider, 'synthetic-connection', sid, '{}', 'owned', 'ready',
               json.dumps({'capture_disabled': disabled}), 1, 1))
            db.execute('INSERT INTO media_assets VALUES (?,?,?,?,?,?,?,?)',
                       (asset_id, sid, kind, 'provider-' + asset_id, state,
                        json.dumps({'capture': {'metadata': {'duration': 30}}}), 1, 1))
        return asset_id

    def test_append_history_survives_reopen_and_idempotent_retry(self):
        first_body = self.body()
        first = self.comment(first_body)
        saved = first['state']['meetingComments'][0]
        self.assertEqual(first['events'], [])
        self.assertEqual(saved['meetingId'], 'm1')
        self.assertEqual(saved['recordingId'], 'recording-one')
        self.assertEqual(saved['seconds'], 12.5)
        self.assertEqual(first['state']['screeningComments'], [])
        self.comment(self.body(text='Ещё один комментарий'))
        before = self.state()
        retry = self.comment(first_body)
        self.assertTrue(retry['replayed'])
        self.assertEqual(retry['state'], before)
        self.assertEqual(before['meetingComments'][0], saved)
        self.assertEqual(len(before['meetingComments']), 2)
        self.assert_problem(409, lambda: self.comment({**first_body, 'text': 'Подмена'}))
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_screening_comments').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_meeting_feedback').fetchone()[0], 0)

    def test_meeting_application_vacancy_isolation_and_candidate_secrecy(self):
        self.comment()
        private = self.state()['meetingComments'][0]['text']
        for aid in ('a2', 'a3'):
            self.assertEqual(self.state(aid)['meetingComments'], [])
            self.assert_problem(404, lambda: self.comment(self.body(expectedRevision=0), aid=aid))
        for meeting_id in ('m2', 'm3', 'invented'):
            self.assert_problem(404, lambda: self.comment(self.body(meetingId=meeting_id)))
        self.assert_problem(404, lambda: self.comment(vacancy_id='v2'))
        self.assert_problem(403, lambda: self.candidate('meeting.comment.add', self.body()))
        candidate = self.state(actor='candidate', cid='c1')
        self.assertNotIn('meetingComments', candidate)
        self.assertNotIn(private, json.dumps(candidate, ensure_ascii=False))
        self.assertEqual(len(self.state()['meetingComments']), 1)

    def test_old_completed_meeting_keeps_its_own_comments_after_next_is_scheduled(self):
        with self.connect() as db:
            self.add_meeting(db, 'm-next', 'a1', status='confirmed')
            wf.meeting_changed(db, 'a1')
        self.assertEqual(self.state()['meeting']['id'], 'm-next')
        self.comment()
        self.assertEqual(self.state()['meetingComments'][0]['meetingId'], 'm1')
        self.assert_problem(409, lambda: self.comment(self.body(meetingId='m-next', recordingId=None)))
        with self.connect() as db:
            db.execute("UPDATE cp_meetings SET status='completed' WHERE id='m-next'")
        self.comment(self.body(meetingId='m-next', recordingId=None, text='Вторая встреча'))
        self.assertEqual([item['meetingId'] for item in self.state()['meetingComments']], ['m1', 'm-next'])

    def test_field_and_revision_validation_are_atomic(self):
        cases = [(400, {'meetingId': None}), (409, {'recordingId': 'foreign-recording'}),
                 (400, {'recordingId': None}), (400, {'seconds': -1}), (400, {'seconds': 30.01}),
                 (400, {'seconds': 14401}), (400, {'seconds': True}), (400, {'seconds': '12'}),
                 (400, {'seconds': float('nan')}), (400, {'seconds': float('inf')}),
                 (400, {'text': ''}), (400, {'text': 'x' * 4001}),
                 (400, {'chapterTitle': 'x' * 241}), (400, {'requestId': None}),
                 (409, {'expectedRevision': None}), (409, {'expectedRevision': True})]
        for status, changes in cases:
            with self.subTest(changes=changes):
                self.assert_problem(status, lambda: self.comment(self.body(**changes)))
        missing = self.body()
        del missing['expectedRevision']
        self.assert_problem(409, lambda: self.comment(missing))
        self.assertEqual(self.state()['revision'], 0)
        self.assertEqual(self.state()['meetingComments'], [])
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_workflow_commands').fetchone()[0], 0)

    def test_legacy_cache_requires_exact_room_kind_and_completed_status(self):
        for sql, args in [
                ('UPDATE cp_media_state SET kind=? WHERE meeting_id=?', ('interview', 'm1')),
                ('UPDATE cp_media_state SET kind=?,room_id=? WHERE meeting_id=?',
                 ('meeting', json.dumps({'id': 'other', 'name': 'other'}), 'm1')),
                ('UPDATE cp_meetings SET status=? WHERE id=?', ('live', 'm1'))]:
            with self.connect() as db:
                db.execute(sql, args)
            self.assert_problem(409, lambda: self.comment())
        self.assertEqual(self.state()['meetingComments'], [])

    def test_transcript_only_comment_never_invents_recording(self):
        with self.connect() as db:
            db.execute("DELETE FROM cp_media_state WHERE meeting_id='m1'")
        self.comment(self.body(recordingId=None, seconds=14))
        saved = self.state()['meetingComments'][0]
        self.assertEqual(saved['meetingId'], 'm1')
        self.assertIsNone(saved['recordingId'])

    def test_provider_neutral_catalogue_ids_and_duration_work_without_daily_cache(self):
        for provider in ('zoom', 'daily'):
            asset_id = self.catalog_asset('catalog-' + provider, provider=provider)
            with self.connect() as db:
                db.execute('DELETE FROM cp_media_state')
            self.comment(self.body(recordingId=asset_id))
            self.assert_problem(400, lambda: self.comment(self.body(recordingId=asset_id, seconds=31)))
        self.assertEqual([item['recordingId'] for item in self.state()['meetingComments']],
                         ['catalog-zoom', 'catalog-daily'])

    def test_catalogue_blocks_foreign_disabled_nonvideo_unready_and_legacy_bypass(self):
        self.catalog_asset()
        cases = [self.catalog_asset('foreign', meeting_id='m2'),
                 self.catalog_asset('same-person-other-application', meeting_id='m3'),
                 self.catalog_asset('disabled', disabled=True),
                 self.catalog_asset('transcript', kind='transcript'),
                 self.catalog_asset('processing', state='processing'), 'recording-one']
        for asset_id in cases:
            with self.subTest(asset_id=asset_id):
                self.assert_problem(409, lambda: self.comment(self.body(recordingId=asset_id)))
        self.assert_problem(400, lambda: self.comment(self.body(recordingId=None)))
        self.assertEqual(self.state()['meetingComments'], [])
        with self.connect() as db:
            db.execute("UPDATE media_sources SET metadata=? WHERE meeting_id='m1'",
                       (json.dumps({'capture_disabled': True}),))
        self.assert_problem(409, lambda: self.comment(self.body(recordingId='recording-one')))

    def test_concurrent_retry_and_stale_revision_do_not_duplicate_or_lose_history(self):
        def run(body):
            try:
                return self.comment(body)
            except Problem as error:
                return error.status
        same = self.body()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, [same, same]))
        self.assertEqual(sum(result['replayed'] for result in results), 1)
        self.assertEqual(len(self.state()['meetingComments']), 1)
        self.assert_problem(409, lambda: self.comment({**same, 'requestId': uuid.uuid4().hex}))
        commands = [self.body(text=words) for words in ('Первый', 'Второй')]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, commands))
        self.assertEqual(results.count(409), 1)
        self.assertEqual(len(self.state()['meetingComments']), 2)

    def test_closed_archived_unaccepted_and_declined_workflows_reject_writes(self):
        with self.connect() as db:
            db.execute("UPDATE cp_applications SET status='pending' WHERE id='a1'")
        self.assert_problem(409, lambda: self.comment())
        with self.connect() as db:
            db.execute("UPDATE cp_applications SET status='accepted',archived_at=? WHERE id='a1'", (iso(now()),))
        self.assert_problem(409, lambda: self.comment())
        with self.connect() as db:
            db.execute("UPDATE cp_applications SET archived_at=NULL WHERE id='a1'")
            vacancy = json.loads(db.execute("SELECT body FROM cp_vacancies WHERE id='v1'").fetchone()[0])
            db.execute("UPDATE cp_vacancies SET body=? WHERE id='v1'", (json.dumps({**vacancy, 'status': 'closed'}),))
        self.assert_problem(409, lambda: self.comment())
        with self.connect() as db:
            db.execute("UPDATE cp_vacancies SET body=? WHERE id='v1'", (json.dumps(vacancy),))
        self.manager('reject')
        self.assert_problem(409, lambda: self.comment())
        self.assertEqual(self.state()['meetingComments'], [])


if __name__ == '__main__':
    unittest.main()
