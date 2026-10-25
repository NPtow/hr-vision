"""Native Zoom completion through real employer HTTP/auth/SQLite boundaries.

Only provider I/O and the materials catalog are faked. These tests do not
create Zoom meetings, use credentials, send mail, or inherit another suite.
"""
import hashlib
import json
import time
import unittest
from unittest.mock import Mock, patch

from call_cleanup import process_native_zoom_ends
from materials_repository import MaterialsRepository
from materials_runtime import MaterialsRuntime
from materials_service import MaterialsService
import test_employer_call_providers as fixture


class EmployerNativeZoomEndTest(unittest.TestCase):
    # Reuse setup/helpers explicitly; unittest must not execute the provider
    # switch suite again merely to exercise this new endpoint.
    request = fixture.EmployerCallProvidersTest.request
    ok = fixture.EmployerCallProvidersTest.ok
    employer = fixture.EmployerCallProvidersTest.employer
    vacancy = fixture.EmployerCallProvidersTest.vacancy
    state = fixture.EmployerCallProvidersTest.state
    candidate = fixture.EmployerCallProvidersTest.candidate
    batch = fixture.EmployerCallProvidersTest.batch
    accepted = fixture.EmployerCallProvidersTest.accepted
    action_body = fixture.EmployerCallProvidersTest.action_body
    act = fixture.EmployerCallProvidersTest.act
    propose = fixture.EmployerCallProvidersTest.propose
    exact = staticmethod(fixture.EmployerCallProvidersTest.exact)
    body = fixture.EmployerCallProvidersTest.body
    map_host = fixture.EmployerCallProvidersTest.map_host
    select = fixture.EmployerCallProvidersTest.select
    join_host = fixture.EmployerCallProvidersTest.join_host
    join_candidate = fixture.EmployerCallProvidersTest.join_candidate
    public_meeting = fixture.EmployerCallProvidersTest.public_meeting
    saved_room = fixture.EmployerCallProvidersTest.saved_room

    @classmethod
    def setUpClass(cls):
        fixture.EmployerCallProvidersTest.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        fixture.EmployerCallProvidersTest.tearDownClass.__func__(cls)

    def setUp(self):
        fixture.EmployerCallProvidersTest.setUp(self)
        self.portal = self.app.CANDIDATE_PORTAL
        self.map_host()
        self.select()
        self.join_host()
        self.catalog = Mock(spec=['register_owner'])
        catalog_patch = patch.object(self.portal, 'materials_catalog', self.catalog)
        catalog_patch.start()
        self.addCleanup(catalog_patch.stop)

    def reconcile(self, **auth):
        return self.ok('employer/call-reconcile', self.body(),
                       **(auth or {'token': self.owner['token']}))

    def saved_meeting(self):
        with self.app.connect() as db:
            return self.portal.meeting(db, self.aid, self.meeting['id'])

    def assert_no_completion(self):
        self.assertEqual(self.saved_meeting()['status'], 'live')
        self.assertFalse(self.zoom.finished, 'observing a native end must never issue an end command')
        self.assertFalse(self.media.finished)
        self.catalog.register_owner.assert_not_called()

    def real_catalog(self):
        repository = MaterialsRepository(self.app.connect)
        repository.init()
        # Registration persists a discovery job only. No worker is run, and no
        # provider/storage adapter or broker is connected in these HTTP tests.
        runtime = MaterialsRuntime(self.app.connect, repository, {}, None)
        service = MaterialsService(runtime)
        self.catalog.register_owner.side_effect = service.register_owner
        return repository, service

    def assert_one_source_and_job(self, repository, session):
        sources = repository.list_sources('meeting', self.meeting['id'])
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]['source_ref']['session_id'], session)
        jobs = repository.list_jobs(sources[0]['id'])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['operation'], 'discover')

    def test_native_end_and_replay_register_one_exact_materials_source(self):
        session = 'provider-confirmed-employer-native-session'
        repository, _ = self.real_catalog()
        self.assertFalse(self.saved_room().get('sessionPinned'))
        with patch.object(self.zoom, 'get_state', return_value={
                'status': 'ended', 'providerSessionId': session}) as read:
            result = self.reconcile()
            self.assertEqual(result, {'completed': True, 'status': 'completed'})
            saved = self.saved_meeting()
            self.assertEqual(saved['status'], 'completed')
            self.assertTrue(saved['completedAt'])
            self.assertEqual(saved['room']['meetingSessionId'], session)
            self.assertTrue(saved['room']['sessionPinned'])
            with self.app.connect() as db:
                choice = self.portal.calls.choice(db, self.meeting['id'])
            self.assertEqual(json.loads(choice['room']), saved['room'])
            self.catalog.register_owner.assert_called_once()
            kind, owner = self.catalog.register_owner.call_args.args
            self.assertEqual((kind, owner['id'], owner['status']),
                             ('meeting', self.meeting['id'], 'completed'))
            self.assertEqual(owner['room'], saved['room'])
            self.assertEqual(self.reconcile(), result)
            read.assert_called_once()
            # Replays must retry registration to repair a crash between the
            # business commit and catalog write. The durable result is unique.
            self.assertEqual(self.catalog.register_owner.call_count, 2)
            for call in self.catalog.register_owner.call_args_list:
                self.assertEqual(call.args, (kind, owner))
            self.assert_one_source_and_job(repository, session)
        self.assertFalse(self.zoom.finished)
        self.assertFalse(self.media.finished)

    def test_completed_replay_repairs_failed_catalog_registration(self):
        repository, service = self.real_catalog()
        session = 'ended-before-catalog-write'
        attempts = []

        def interrupted_registration(*args):
            attempts.append(args)
            if len(attempts) == 1:
                raise RuntimeError('synthetic catalog outage after business completion')
            return service.register_owner(*args)

        self.catalog.register_owner.side_effect = interrupted_registration
        with patch.object(self.zoom, 'get_state', return_value={
                'status': 'ended', 'providerSessionId': session}) as read, \
                patch('observability.capture_exception'):
            result = self.reconcile()
            self.assertEqual(result, {'completed': True, 'status': 'completed'})
            self.assertEqual(self.saved_meeting()['status'], 'completed')
            self.assertEqual(repository.list_sources('meeting', self.meeting['id']), [])
            self.assertEqual(self.reconcile(), result)
            self.assertEqual(self.reconcile(), result)
            read.assert_called_once()
        self.assertEqual(len(attempts), 3)
        self.assert_one_source_and_job(repository, session)
        self.assertFalse(self.zoom.finished)
        self.assertFalse(self.media.finished)

    def test_host_leave_while_zoom_is_started_or_waiting_keeps_meeting_live(self):
        for status in ('started', 'waiting'):
            with self.subTest(status=status), patch.object(self.zoom, 'get_state', return_value={
                    'status': status, 'providerSessionId': 'still-active-session'}):
                self.assertEqual(self.reconcile(), {'completed': False, 'status': 'live'})
                self.assert_no_completion()
                self.assertFalse(self.saved_room().get('sessionPinned'))

    def test_foreign_workspace_and_candidate_are_denied_before_provider_read(self):
        foreign = self.employer('Foreign native-end caller')
        body = self.body()
        for auth, expected in (({'token': foreign['token']}, 404),
                               ({'cookie': self.person['cookie']}, 401)):
            with self.subTest(auth_kind=next(iter(auth))), patch.object(self.zoom, 'get_state') as read:
                status, result, _ = self.request('employer/call-reconcile', body, **auth)
                self.assertEqual(status, expected, result)
                read.assert_not_called()
                self.assert_no_completion()

    def test_wrong_meeting_or_revision_never_reads_or_completes_another_meeting(self):
        for change, expected in (({'meetingId': 'unknown-meeting'}, 404),
                                 ({'revision': self.meeting['revision'] + 1}, 409)):
            with self.subTest(change=change), patch.object(self.zoom, 'get_state') as read:
                status, result, _ = self.request('employer/call-reconcile',
                    {**self.body(), **change}, token=self.owner['token'])
                self.assertEqual(status, expected, result)
                read.assert_not_called()
                self.assert_no_completion()

    def test_revocation_during_provider_read_cannot_commit_completion(self):
        body = self.body()

        def revoked(_room):
            with self.app.connect() as db:
                db.execute('DELETE FROM em_sessions WHERE token_hash=?',
                           (hashlib.sha256(self.owner['token'].encode()).hexdigest(),))
            return {'status': 'ended', 'providerSessionId': 'ended-after-revocation'}

        with patch.object(self.zoom, 'get_state', side_effect=revoked):
            status, result, _ = self.request('employer/call-reconcile', body, token=self.owner['token'])
        self.assertEqual(status, 401, result)
        self.assert_no_completion()
        self.assertFalse(self.saved_room().get('sessionPinned'))

    def test_room_replaced_during_provider_read_cannot_complete_stale_room(self):
        before = self.saved_room()
        replacement = {**before, 'meetingNumber': '99999999999', 'id': '99999999999'}

        def replaced(_room):
            with self.app.connect() as db:
                db.execute('UPDATE cp_meetings SET room=? WHERE id=?',
                           (json.dumps(replacement), self.meeting['id']))
            return {'status': 'ended', 'providerSessionId': 'old-room-ended-session'}

        with patch.object(self.zoom, 'get_state', side_effect=replaced):
            status, result, _ = self.request('employer/call-reconcile', self.body(), token=self.owner['token'])
        self.assertEqual(status, 409, result)
        self.assert_no_completion()
        self.assertEqual(self.saved_room(), replacement)

    def test_end_for_different_pinned_instance_does_not_complete_or_replace_uuid(self):
        pinned = 'original-live-instance'
        self.zoom.session = pinned
        self.ok('employer/call-connected', self.body(), token=self.owner['token'])
        with patch.object(self.zoom, 'get_state', return_value={
                'status': 'ended', 'providerSessionId': 'other-ended-instance'}):
            status, result, _ = self.request('employer/call-reconcile', self.body(), token=self.owner['token'])
        self.assertEqual(status, 409, result)
        self.assert_no_completion()
        self.assertEqual(self.saved_room()['meetingSessionId'], pinned)

    def test_ended_without_provider_instance_proof_is_not_completion(self):
        with patch.object(self.zoom, 'get_state', return_value={'status': 'ended'}):
            status, result, _ = self.request('employer/call-reconcile', self.body(), token=self.owner['token'])
        self.assertEqual(status, 409, result)
        self.assert_no_completion()

    def test_background_poll_completes_employer_meeting_after_browser_closes(self):
        # Other fixture cases share the isolated database, so postpone their
        # checks and prove this poll processes only this case's cp_meetings row.
        with self.app.connect() as db:
            db.execute('''INSERT OR REPLACE INTO cp_zoom_lifecycle_checks
              SELECT id,? FROM cp_meetings WHERE id<>?''',
              (time.time() + 3600, self.meeting['id']))
        session = 'ended-after-browser-closed'
        with patch.object(self.zoom, 'get_state', return_value={
                'status': 'ended', 'providerSessionId': session}) as read:
            results = process_native_zoom_ends(self.portal)
        self.assertEqual(results, [{'meetingId': self.meeting['id'], 'completed': True}])
        read.assert_called_once()
        saved = self.saved_meeting()
        self.assertEqual((saved['status'], saved['room']['meetingSessionId']), ('completed', session))
        self.assertTrue(saved['room']['sessionPinned'])
        self.catalog.register_owner.assert_called_once()
        self.assertFalse(self.zoom.finished)


if __name__ == '__main__':
    unittest.main()
