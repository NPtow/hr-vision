"""Employer provider switch through real HTTP/auth/SQLite with fake media only.

The fixtures are reused as helpers, not inherited tests. No Zoom API, mail,
Telegram, account credentials, or production database is used by this suite.
"""
import hashlib
import json
import unittest
from unittest.mock import patch

import test_employer_acceptance as fixture
import test_call_providers as provider_fixture


class WorkspaceZoom(provider_fixture.FakeZoom):
    def __init__(self):
        super().__init__()
        self.hosts, self.host_lookups, self.state_reads = {}, [], []

    def host_for(self, actor):
        self.host_lookups.append(actor)
        return self.hosts.get(actor)

    def get_state(self, room):
        self.state_reads.append(dict(room))
        return super().get_state(room)


class EmployerCallProvidersTest(unittest.TestCase):
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

    @classmethod
    def setUpClass(cls):
        cls.safety_env = patch.dict('os.environ', {
            'HR_ZOOM_ENABLED': '0', 'HR_MATERIALS_ENABLED': '0',
            'ZOOM_MEETING_SDK_CLIENT_ID': '', 'ZOOM_MEETING_SDK_CLIENT_SECRET': '',
            'ZOOM_S2S_ACCOUNT_ID': '', 'ZOOM_S2S_CLIENT_ID': '', 'ZOOM_S2S_CLIENT_SECRET': '',
        })
        cls.safety_env.start()
        cls.addClassCleanup(cls.safety_env.stop)
        fixture.EmployerAcceptanceTest.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        fixture.EmployerAcceptanceTest.tearDownClass.__func__(cls)

    def setUp(self):
        fixture.EmployerAcceptanceTest.setUp(self)
        self.zoom = WorkspaceZoom()
        self.app.CANDIDATE_PORTAL.calls.zoom = self.zoom
        self.media.tokens.clear()
        self.media.rooms.clear()
        self.media.finished.clear()
        self.owner = self.employer()
        self.job = self.vacancy(self.owner)
        self.person, self.aid = self.accepted(self.owner, self.job)
        self.meeting = self.propose(self.owner, self.job, self.aid)
        self.ok('candidate/meeting/confirm', self.exact(self.aid, self.meeting), cookie=self.person['cookie'])
        self.actor = 'workspace:' + self.owner['workspace']['id']

    def body(self, **changes):
        return self.action_body(self.owner, self.job, self.aid, 'meeting.room',
            meetingId=self.meeting['id'], revision=self.meeting['revision'], **changes)

    def map_host(self):
        self.zoom.hosts[self.actor] = 'workspace-host@example.test'

    def select(self, provider='zoom', **changes):
        return self.ok('employer/call-provider', self.body(provider=provider, **changes), token=self.owner['token'])

    def join_host(self):
        return self.ok('employer/room', self.body(consent=True), token=self.owner['token'])

    def join_candidate(self):
        return self.ok('candidate/meeting/join', {**self.exact(self.aid, self.meeting), 'consent': True},
                       cookie=self.person['cookie'])

    def public_meeting(self):
        return self.state(self.owner, self.job)['candidates'][self.aid]['meeting']

    def saved_room(self):
        with self.app.connect() as db:
            return self.app.CANDIDATE_PORTAL.meeting(db, self.aid, self.meeting['id'])['room']

    def test_projection_and_selection_require_exact_workspace_host_mapping(self):
        # Mapping a generic manager/recruiter must not accidentally grant a
        # personal employer workspace use of somebody else's Zoom host.
        self.zoom.hosts.update({'manager': 'fallback@example.test', 'recruiter': 'fallback@example.test'})
        meeting = self.public_meeting()
        self.assertEqual(meeting['callProvider'], 'daily')
        self.assertEqual({p['id']: p['available'] for p in meeting['callProviderOptions']},
                         {'daily': True, 'zoom': False})
        status, _, _ = self.request('employer/call-provider', self.body(provider='zoom'), token=self.owner['token'])
        self.assertEqual(status, 409)
        self.assertFalse(self.zoom.created)
        self.map_host()
        state = self.select()
        meeting = state['candidates'][self.aid]['meeting']
        self.assertEqual(meeting['callProvider'], 'zoom')
        self.assertFalse(meeting['callProviderLocked'])
        self.assertTrue(next(p['available'] for p in meeting['callProviderOptions'] if p['id'] == 'zoom'))
        with self.app.connect() as db:
            choice = self.app.CANDIDATE_PORTAL.calls.choice(db, self.meeting['id'])
        self.assertEqual((choice['actor_id'], choice['host_id']), (self.actor, 'workspace-host@example.test'))
        self.assertIn(self.actor, self.zoom.host_lookups)

    def test_provider_persists_and_host_guest_share_room_without_leaking_host_secret(self):
        self.map_host()
        self.select()
        guest = self.join_candidate()
        host = self.join_host()
        self.assertEqual((guest['provider'], host['provider']), ('zoom', 'zoom'))
        self.assertEqual(guest['meetingNumber'], host['meetingNumber'])
        self.assertEqual((guest['role'], host['role']), (0, 1))
        self.assertNotIn('zak', guest)
        self.assertEqual(host['zak'], 'host-only-test')
        self.assertEqual(len(self.zoom.created), 1)
        self.assertEqual(self.zoom.created[0][1], 'workspace-host@example.test')
        self.assertFalse(self.media.tokens)
        self.assertFalse(self.media.rooms)
        meeting = self.public_meeting()
        self.assertEqual(meeting['callProvider'], 'zoom')
        self.assertTrue(meeting['callProviderLocked'])
        employer_state = self.state(self.owner, self.job)
        candidate_state = self.ok('candidate/state', cookie=self.person['cookie'])
        application = next(a for a in candidate_state['applications'] if a['id'] == self.aid)
        self.assertEqual(application['meeting']['callProvider'], 'zoom')
        for value in (employer_state, candidate_state):
            serialized = json.dumps(value)
            for secret in ('private-pass', 'host-only-test', 'synthetic-sdk-signature', 'workspace-host@example.test'):
                self.assertNotIn(secret, serialized)
        self.assertNotIn('room', meeting)
        self.assertEqual(self.saved_room()['provider'], 'zoom')

    def test_foreign_workspace_denied_before_any_provider_operation(self):
        self.map_host()
        self.select()
        self.join_host()
        foreign = self.employer('Чужой')
        for path, body in (
            ('employer/call-provider', self.body(provider='daily')),
            ('employer/call-connected', self.body()),
            ('employer/room', self.body(consent=True)),
        ):
            with self.subTest(path=path):
                snapshot = (len(self.zoom.created), len(self.zoom.state_reads), len(self.media.tokens))
                status, result, _ = self.request(path, body, token=foreign['token'])
                # Existing employer API intentionally uses 404 to avoid exposing
                # whether another workspace's candidate/meeting exists.
                self.assertEqual(status, 404, result)
                self.assertEqual(snapshot, (len(self.zoom.created), len(self.zoom.state_reads), len(self.media.tokens)))
                self.assertNotIn('zak', result)
        self.assertEqual(self.public_meeting()['callProvider'], 'zoom')

    def test_candidate_cannot_select_provider_or_send_host_connected_signal(self):
        self.map_host()
        self.select()
        self.join_candidate()
        for path, body in (
            ('employer/call-provider', self.body(provider='daily')),
            ('employer/call-connected', self.body()),
            ('employer/room', self.body(consent=True)),
        ):
            with self.subTest(path=path):
                status, result, _ = self.request(path, body, cookie=self.person['cookie'])
                self.assertEqual(status, 401, result)
                self.assertNotIn('zak', result)
        self.assertFalse(self.zoom.state_reads)
        self.assertFalse(self.saved_room().get('sessionPinned'))

    def test_connected_requires_live_host_and_pins_observed_instance_once(self):
        self.map_host()
        self.select()
        status, _, _ = self.request('employer/call-connected', self.body(), token=self.owner['token'])
        self.assertEqual(status, 409)
        self.assertFalse(self.zoom.state_reads)
        self.join_candidate()
        status, _, _ = self.request('employer/call-connected', self.body(), token=self.owner['token'])
        self.assertEqual(status, 409)
        self.join_host()
        self.zoom.session = 'actual-started-employer-session'
        result = self.ok('employer/call-connected', self.body(), token=self.owner['token'])
        self.assertTrue(result['ok'])
        room = self.saved_room()
        self.assertTrue(room['sessionPinned'])
        self.assertEqual(room['meetingSessionId'], self.zoom.session)
        self.zoom.session = 'unexpected-restarted-session'
        status, _, _ = self.request('employer/call-connected', self.body(), token=self.owner['token'])
        self.assertEqual(status, 409)
        self.assertEqual(self.saved_room()['meetingSessionId'], 'actual-started-employer-session')

    def test_daily_default_uses_existing_tokens_and_connected_is_provider_noop(self):
        host = self.join_host()
        guest = self.join_candidate()
        self.assertEqual((host['provider'], guest['provider']), ('daily', 'daily'))
        self.assertIn('token', host)
        self.assertIn('token', guest)
        self.assertNotIn('signature', host)
        self.assertNotIn('zak', guest)
        self.assertEqual(len(self.media.tokens), 2)
        self.assertTrue(self.media.tokens[0]['is_owner'])
        self.assertFalse(self.media.tokens[1]['is_owner'])
        self.assertTrue(self.ok('employer/call-connected', self.body(), token=self.owner['token'])['ok'])
        self.assertFalse(self.zoom.created)
        self.assertFalse(self.zoom.state_reads)
        self.map_host()
        status, _, _ = self.request('employer/call-provider', self.body(provider='zoom'), token=self.owner['token'])
        self.assertEqual(status, 409)
        self.assertEqual(self.public_meeting()['callProvider'], 'daily')

    def test_new_choice_flag_off_keeps_created_zoom_room_and_hides_new_option(self):
        self.map_host()
        self.select()
        self.join_host()
        self.zoom.selection_enabled = False
        self.assertEqual(self.join_candidate()['provider'], 'zoom')
        self.assertEqual(self.join_host()['provider'], 'zoom')
        self.assertEqual(len(self.zoom.created), 1)
        meeting = self.public_meeting()
        self.assertEqual(meeting['callProvider'], 'zoom')
        self.assertTrue(meeting['videoConfigured'])
        self.assertFalse(next(p['available'] for p in meeting['callProviderOptions'] if p['id'] == 'zoom'))

    def test_supplied_workspace_and_host_do_not_override_authenticated_workspace_mapping(self):
        self.map_host()
        self.zoom.hosts['workspace:forged'] = 'foreign-host@example.test'
        self.select(workspaceId='forged', hostId='foreign-host@example.test', actorId='workspace:forged')
        self.join_host()
        self.assertEqual(self.zoom.created[0][1], 'workspace-host@example.test')
        with self.app.connect() as db:
            choice = self.app.CANDIDATE_PORTAL.calls.choice(db, self.meeting['id'])
        self.assertEqual(choice['actor_id'], self.actor)

    def test_session_revoked_during_provider_work_never_returns_host_credentials(self):
        self.map_host()
        self.select()
        credentials = self.zoom.credentials

        def revoke_then_return(room, user_name, owner):
            result = credentials(room, user_name, owner)
            if owner:
                with self.app.connect() as db:
                    db.execute('DELETE FROM em_sessions WHERE token_hash=?',
                               (hashlib.sha256(self.owner['token'].encode()).hexdigest(),))
            return result

        with patch.object(self.zoom, 'credentials', side_effect=revoke_then_return):
            status, result, _ = self.request('employer/room', self.body(consent=True), token=self.owner['token'])
        self.assertEqual(status, 401, result)
        self.assertNotIn('host-only-test', json.dumps(result))
        self.assertNotIn('synthetic-sdk-signature', json.dumps(result))
        with self.app.connect() as db:
            saved = self.app.CANDIDATE_PORTAL.meeting(db, self.aid, self.meeting['id'])
        self.assertEqual(saved['status'], 'confirmed')


if __name__ == '__main__':
    unittest.main()
