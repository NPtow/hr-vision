"""Offline provider boundary tests. These do not prove a two-way Zoom call."""
import base64
import hashlib
import hmac
import io
import json
import sqlite3
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from call_providers import CallProviders
from zoom_media import MAX_JSON, ZoomMedia, ZoomMediaError, sdk_signature


ENV = {'ZOOM_MEETING_SDK_CLIENT_ID': 'sdk-id', 'ZOOM_MEETING_SDK_CLIENT_SECRET': 'sdk-secret',
       'ZOOM_S2S_ACCOUNT_ID': 'account-id', 'ZOOM_S2S_CLIENT_ID': 'rest-id',
       'ZOOM_S2S_CLIENT_SECRET': 'rest-secret', 'HR_ZOOM_HOST_MAP_JSON': '{"actor-a":"host@example.test"}'}
NUMBER = '12345678901'
SESSION = 'abc/def+g=='
ROOM = {'provider': 'zoom', 'id': NUMBER, 'name': NUMBER, 'meetingNumber': NUMBER,
        'meetingSessionId': SESSION, 'connectionId': 'zoom', 'zoomHostId': 'host@example.test',
        'password': 'room-pass', 'url': 'https://us02web.zoom.us/j/' + NUMBER}


class Response(io.BytesIO):
    def __init__(self, body, status=200):
        super().__init__(body if isinstance(body, bytes) else json.dumps(body).encode())
        self.status = status


def missing(code=3001):
    return ZoomMediaError('missing', provider_status=404, zoom_code=code)


class ZoomMediaTest(unittest.TestCase):
    def setUp(self):
        self.zoom = ZoomMedia.from_env(ENV)

    def test_new_selection_off_by_default_existing_configuration_remains_available(self):
        self.assertTrue(self.zoom.configured)
        self.assertTrue(self.zoom.capture_configured)
        self.assertFalse(self.zoom.selection_enabled)
        self.assertEqual(self.zoom.host_for('actor-a'), 'host@example.test')
        self.assertIsNone(self.zoom.host_for('Actor-A'))

    def test_flag_requires_valid_keys_and_explicit_mapping(self):
        self.assertTrue(ZoomMedia.from_env({**ENV, 'HR_ZOOM_ENABLED': '1'}).selection_enabled)
        for mapping in ('null', '[]', '{', '{"actor":""}', '{"actor":123}', '{"actor":"host\nname"}'):
            media = ZoomMedia.from_env({**ENV, 'HR_ZOOM_ENABLED': '1', 'HR_ZOOM_HOST_MAP_JSON': mapping})
            self.assertFalse(media.selection_enabled)
        self.assertFalse(ZoomMedia.from_env({'HR_ZOOM_ENABLED': '1'}).configured)

    def test_capture_does_not_require_sdk_or_selection(self):
        media = ZoomMedia.from_env({k: v for k, v in ENV.items() if not k.startswith('ZOOM_MEETING')})
        self.assertTrue(media.capture_configured)
        self.assertFalse(media.configured)
        self.assertFalse(media.selection_enabled)

    def test_no_implicit_default_host_from_poc_environment(self):
        media = ZoomMedia.from_env({**ENV, 'ZOOM_HOST_USER_ID': 'fallback', 'HR_ZOOM_HOST_MAP_JSON': '{}'})
        self.assertIsNone(media.host_for('actor-a'))
        media.request = Mock()
        with self.assertRaises(ZoomMediaError):
            media.room('meeting-a', 'fallback', 'request-a')
        media.request.assert_not_called()

    def test_explicit_default_host_makes_zoom_available_without_actor_cohort(self):
        media = ZoomMedia.from_env({**ENV, 'HR_ZOOM_ENABLED': '1', 'HR_ZOOM_HOST_MAP_JSON': '{}',
                                   'HR_ZOOM_DEFAULT_HOST_ID': 'shared@example.test'})
        self.assertTrue(media.selection_enabled)
        for actor in ('recruiter-a', 'recruiter-b', 'employer-a'):
            self.assertEqual(media.host_for(actor), 'shared@example.test')
        for actor in (None, '', 123, True, '\n'):
            self.assertIsNone(media.host_for(actor))
        media.request = Mock(return_value=self.create_response())
        self.assertEqual(media.room('meeting', media.host_for('employer-a'), 'request')['zoomHostId'],
                         'shared@example.test')
        self.assertEqual(media.request.call_args.args[0], '/users/shared%40example.test/meetings')

    def test_explicit_actor_mapping_precedes_shared_host_and_flag_still_applies(self):
        media = ZoomMedia.from_env({**ENV, 'HR_ZOOM_DEFAULT_HOST_ID': 'shared@example.test'})
        self.assertEqual(media.host_for('actor-a'), 'host@example.test')
        self.assertEqual(media.host_for('actor-b'), 'shared@example.test')
        self.assertFalse(media.selection_enabled)

    def test_invalid_default_host_cannot_enable_selection_or_create_room(self):
        for host in ('', ' ', 'bad host', 'bad\nhost', 'host@example.test ', None, 123):
            with self.subTest(host=host):
                media = ZoomMedia.from_env({**ENV, 'HR_ZOOM_ENABLED': '1', 'HR_ZOOM_HOST_MAP_JSON': '{}',
                                           'HR_ZOOM_DEFAULT_HOST_ID': host})
                self.assertFalse(media.selection_enabled)
                self.assertIsNone(media.host_for('actor-a'))
                media.request = Mock()
                with self.assertRaises(ZoomMediaError):
                    media.room('meeting', 'unconfigured@example.test', 'request')
                media.request.assert_not_called()

    def test_shared_host_is_available_to_each_actor_and_persisted_room_survives_config_change(self):
        media = ZoomMedia.from_env({**ENV, 'HR_ZOOM_ENABLED': '1', 'HR_ZOOM_HOST_MAP_JSON': '{}',
                                   'HR_ZOOM_DEFAULT_HOST_ID': 'shared@example.test'})
        media.request = Mock(return_value=self.create_response())
        with tempfile.TemporaryDirectory() as directory:
            def connect():
                return sqlite3.connect(Path(directory) / 'providers.sqlite3')
            providers = CallProviders(connect, SimpleNamespace(configured=True), media)
            with connect() as db:
                providers.init(db)
                for actor in ('recruiter-a', 'recruiter-b', 'employer-a'):
                    self.assertTrue(providers.describe(db, actor, actor_id=actor)['callProviderOptions'][1]['available'])
                    providers.select(db, actor, 'zoom', actor)
                    self.assertEqual(providers.choice(db, actor)['host_id'], 'shared@example.test')
            room = providers.zoom_room('recruiter-a')
            self.assertEqual(room['recordingPolicy'], 'automatic-cloud')
            # Disabling new choices/changing host configuration does not mutate
            # an existing meeting or replace it with another provider's room.
            providers.zoom = ZoomMedia.from_env({**ENV, 'HR_ZOOM_DEFAULT_HOST_ID': 'new@example.test'})
            providers.zoom.request = Mock(side_effect=AssertionError('existing room was recreated'))
            self.assertEqual(providers.zoom_room('recruiter-a'), room)
            with connect() as db:
                current = providers.describe(db, 'recruiter-a', room, actor_id='recruiter-a')
                self.assertTrue(current['callProviderLocked'])
                self.assertEqual(current['callProvider'], 'zoom')
                self.assertFalse(current['callProviderOptions'][1]['available'])
            self.assertEqual(media.request.call_count, 1)

    def create_response(self):
        return {'id': int(NUMBER), 'uuid': SESSION, 'password': 'room-pass',
                'join_url': ROOM['url'] + '?pwd=signed-secret',
                'settings': {'auto_recording': 'cloud', 'waiting_room': False, 'join_before_host': False}}

    def test_create_has_native_cloud_autorecording_direct_entry_no_pii_and_safe_link(self):
        self.zoom.request = Mock(return_value=self.create_response())
        room = self.zoom.room('product-meeting', 'host@example.test', 'request-a')
        self.assertEqual(room['url'], ROOM['url'])
        self.assertEqual(room['meetingSessionId'], SESSION)
        self.assertEqual(room['zoomHostId'], 'host@example.test')
        self.assertEqual(room['recordingPolicy'], 'automatic-cloud')
        path, payload = self.zoom.request.call_args.args
        self.assertEqual(path, '/users/host%40example.test/meetings')
        self.assertFalse(payload['settings']['use_pmi'])
        self.assertEqual(payload['settings']['auto_recording'], 'cloud')
        self.assertFalse(payload['settings']['waiting_room'])
        self.assertFalse(payload['settings']['join_before_host'])
        self.assertNotIn('product-meeting', payload['topic'])
        self.assertNotIn('signed-secret', json.dumps(room))

    def test_uncertain_create_does_not_retry(self):
        self.zoom.request = Mock(side_effect=ZoomMediaError('unknown', code='zoom_unavailable'))
        with self.assertRaises(ZoomMediaError):
            self.zoom.room('meeting', 'host@example.test', 'request')
        self.assertEqual(self.zoom.request.call_count, 1)

    def test_create_rejects_recording_policy_or_foreign_join_url(self):
        for update in ({'settings': {'auto_recording': 'none', 'waiting_room': False, 'join_before_host': False}},
                       {'settings': {'auto_recording': 'cloud', 'waiting_room': True, 'join_before_host': False}},
                       {'settings': {'auto_recording': 'cloud', 'waiting_room': False, 'join_before_host': True}},
                       {'join_url': 'https://evil.test/j/' + NUMBER},
                       {'join_url': 'https://zoom.us/j/99999999999'}, {'uuid': ''}):
            self.zoom.request = Mock(return_value={**self.create_response(), **update})
            with self.assertRaises(ZoomMediaError):
                self.zoom.room('meeting', 'host@example.test', 'request')

    def test_signature_has_exact_meeting_role_lifetime_and_hmac(self):
        token = sdk_signature('client', 'secret', NUMBER, 0, now_seconds=1800000000)
        header, payload, signature = token.split('.')
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        self.assertEqual(claims, {'appKey': 'client', 'mn': NUMBER, 'role': 0,
                                 'iat': 1799999970, 'exp': 1800007170, 'tokenExp': 1800007170})
        expected = base64.urlsafe_b64encode(hmac.new(b'secret', (header + '.' + payload).encode(), hashlib.sha256).digest()).rstrip(b'=').decode()
        self.assertEqual(signature, expected)
        with self.assertRaises(ZoomMediaError):
            sdk_signature('client', 'secret', NUMBER, True)

    def test_guest_credentials_never_request_or_include_host_zak(self):
        self.zoom.request = Mock()
        result = self.zoom.credentials(ROOM, 'Кандидат', False)
        self.assertEqual(result['role'], 0)
        self.assertNotIn('zak', result)
        self.assertNotIn('zoomHostId', result)
        self.assertNotIn('sdk-secret', json.dumps(result))
        self.zoom.request.assert_not_called()

    def test_host_zak_uses_persisted_host_even_after_mapping_changes(self):
        self.zoom = ZoomMedia.from_env({**ENV, 'HR_ZOOM_HOST_MAP_JSON': '{}',
                                       'HR_ZOOM_DEFAULT_HOST_ID': 'new-shared@example.test'})
        self.zoom.request = Mock(return_value={'token': 'host-zak'})
        result = self.zoom.credentials(ROOM, 'Организатор', True)
        self.assertEqual((result['role'], result['zak']), (1, 'host-zak'))
        self.zoom.request.assert_called_once_with('/users/host%40example.test/token?type=zak')

    def test_existing_manual_room_keeps_settings_and_does_not_gain_autorecord_policy(self):
        room = dict(ROOM)
        self.zoom.request = Mock()
        self.zoom.credentials(room, 'Candidate', False)
        self.zoom.request.assert_not_called()
        self.assertEqual(room, ROOM)
        self.assertNotIn('recordingPolicy', room)

    def test_host_flag_must_be_server_boolean_and_connection_must_match(self):
        for owner, room in ((1, ROOM), ('true', ROOM), (False, {**ROOM, 'connectionId': 'another'})):
            with self.assertRaises(ZoomMediaError):
                self.zoom.credentials(room, 'Candidate', owner)

    def test_started_lifecycle_exposes_observed_uuid_for_parent_first_start_pin(self):
        self.zoom.request = Mock(return_value={'id': NUMBER, 'uuid': 'actual-session', 'status': 'started'})
        result = self.zoom.get_state(ROOM)
        self.assertEqual(result['providerSessionId'], 'actual-session')
        self.assertEqual(result['status'], 'started')
        self.assertEqual(self.zoom.request.call_count, 1)

    def test_native_ended_unpinned_session_uses_exact_current_uuid_proof(self):
        actual = '/actual//ended=='
        self.zoom.request = Mock(side_effect=[
            {'id': NUMBER, 'uuid': actual, 'status': 'waiting'},
            {'id': NUMBER, 'uuid': actual, 'end_time': '2026-10-25T12:00:00Z'},
        ])
        original = dict(ROOM)
        result = self.zoom.get_state(ROOM)
        self.assertEqual(result['status'], 'ended')
        self.assertEqual(result['meetingSessionId'], actual)
        self.assertEqual(result['providerSessionId'], actual)
        self.assertEqual(ROOM, original)  # Only the caller can commit the first pin.
        self.assertEqual([call.args[0] for call in self.zoom.request.call_args_list], [
            '/meetings/' + NUMBER, '/past_meetings/%252Factual%252F%252Fended%253D%253D',
        ])

    def test_unpinned_waiting_uuid_requires_exact_ended_proof(self):
        actual = 'actual-session'
        for response in (missing(),
                         {'id': NUMBER, 'uuid': actual},
                         {'id': '99999999999', 'uuid': actual, 'end_time': '2026-10-25T12:00:00Z'},
                         {'id': NUMBER, 'uuid': SESSION, 'end_time': '2026-10-25T12:00:00Z'},
                         ZoomMediaError('not authorized', provider_status=403)):
            with self.subTest(response=response):
                self.zoom.request = Mock(side_effect=[
                    {'id': NUMBER, 'uuid': actual, 'status': 'waiting'}, response,
                ])
                if isinstance(response, ZoomMediaError) and response.provider_status == 404:
                    self.assertEqual(self.zoom.get_state(ROOM)['status'], 'waiting')
                else:
                    with self.assertRaises(ZoomMediaError):
                        self.zoom.get_state(ROOM)
                self.assertEqual(self.zoom.request.call_args.args[0], '/past_meetings/' + actual)

    def test_pinned_started_other_uuid_is_rejected_without_rebinding(self):
        pinned = {**ROOM, 'sessionPinned': True}
        self.zoom.request = Mock(return_value={'id': NUMBER, 'uuid': 'another', 'status': 'started'})
        with self.assertRaises(ZoomMediaError) as error:
            self.zoom.get_state(pinned)
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(pinned['meetingSessionId'], SESSION)
        self.assertEqual(self.zoom.request.call_count, 1)

    def test_pinned_waiting_other_uuid_only_checks_original_instance(self):
        pinned = {**ROOM, 'sessionPinned': True}
        self.zoom.request = Mock(side_effect=[
            {'id': NUMBER, 'uuid': 'another', 'status': 'waiting'},
            {'id': NUMBER, 'uuid': SESSION, 'end_time': '2026-10-25T12:00:00Z'},
        ])
        result = self.zoom.get_state(pinned)
        self.assertEqual((result['status'], result['providerSessionId']), ('ended', SESSION))
        self.assertEqual(self.zoom.request.call_args.args[0], '/past_meetings/abc%2Fdef%2Bg%3D%3D')
        self.zoom.request = Mock(side_effect=[
            {'id': NUMBER, 'uuid': 'another', 'status': 'waiting'}, missing(),
        ])
        with self.assertRaises(ZoomMediaError) as error:
            self.zoom.finish(pinned)
        self.assertEqual(error.exception.status, 409)
        self.assertTrue(all(call.args[0].startswith(('/meetings/', '/past_meetings/'))
                            and 'method' not in call.kwargs for call in self.zoom.request.call_args_list))

    def test_missing_current_meeting_never_guesses_unobserved_actual_uuid(self):
        self.zoom.request = Mock(side_effect=[missing(), missing()])
        result = self.zoom.get_state(ROOM)
        self.assertEqual((result['status'], result['providerSessionId']), ('unknown', SESSION))
        self.assertEqual(self.zoom.request.call_args.args[0], '/past_meetings/abc%2Fdef%2Bg%3D%3D')

    def test_waiting_is_not_completed_and_bare_missing_is_error(self):
        self.zoom.request = Mock(side_effect=[{'id': NUMBER, 'uuid': SESSION, 'status': 'waiting'}, missing()])
        self.assertEqual(self.zoom.get_state(ROOM)['status'], 'waiting')
        self.zoom.request = Mock(side_effect=ZoomMediaError('missing', provider_status=404))
        with self.assertRaises(ZoomMediaError):
            self.zoom.get_state(ROOM)

    def test_past_requires_exact_uuid_and_end_time(self):
        ended = {'id': NUMBER, 'uuid': SESSION, 'end_time': '2026-10-25T12:00:00Z'}
        self.zoom.request = Mock(side_effect=[missing(), ended])
        self.assertEqual(self.zoom.get_state(ROOM)['status'], 'ended')
        self.zoom.request = Mock(side_effect=[missing(), {**ended, 'uuid': 'different'}])
        with self.assertRaises(ZoomMediaError):
            self.zoom.get_state(ROOM)
        for value in ('not-a-date', '2026-10-25T12:00:00', '', None, 123):
            self.zoom.request = Mock(side_effect=[missing(), {**ended, 'end_time': value}])
            with self.assertRaises(ZoomMediaError):
                self.zoom.get_state(ROOM)

    def test_finish_ends_same_instance_and_repeated_finish_does_not_mutate(self):
        self.zoom.get_state = Mock(return_value={'status': 'started', 'providerSessionId': SESSION})
        self.zoom.request = Mock(return_value={})
        self.assertEqual(self.zoom.finish(ROOM), {'status': 'ended'})
        self.zoom.request.assert_called_once_with('/meetings/' + NUMBER + '/status', {'action': 'end'}, method='PUT')
        self.zoom.get_state = Mock(return_value={'status': 'ended', 'providerSessionId': SESSION})
        self.zoom.request.reset_mock()
        self.zoom.finish(ROOM)
        self.zoom.request.assert_not_called()

    def test_finish_never_ends_different_instance_or_unknown_room(self):
        self.zoom.request = Mock()
        for state in ({'status': 'started', 'providerSessionId': 'another'}, {'status': 'unknown'}):
            self.zoom.get_state = Mock(return_value=state)
            with self.assertRaises(ZoomMediaError):
                self.zoom.finish(ROOM)
        self.zoom.request.assert_not_called()

    def test_finish_missing_requires_exact_ended_proof_auth_never_swallowed(self):
        self.zoom.get_state = Mock(side_effect=[{'status': 'started', 'providerSessionId': SESSION}, {'status': 'ended'}])
        self.zoom.request = Mock(side_effect=missing())
        self.assertEqual(self.zoom.finish(ROOM), {'status': 'ended'})
        self.zoom.get_state = Mock(return_value={'status': 'started', 'providerSessionId': SESSION})
        self.zoom.request = Mock(side_effect=ZoomMediaError('auth', provider_status=403))
        with self.assertRaises(ZoomMediaError):
            self.zoom.finish(ROOM)
        self.assertEqual(self.zoom.get_state.call_count, 1)

    def test_recordings_use_exact_double_encoded_uuid_not_latest_id(self):
        session = '/a//b=='
        self.zoom.request = Mock(return_value={'id': NUMBER, 'uuid': session, 'recording_files': []})
        result = self.zoom.recordings({**ROOM, 'meetingSessionId': session})
        self.assertEqual(result['uuid'], session)
        self.zoom.request.assert_called_once_with('/meetings/%252Fa%252F%252Fb%253D%253D/recordings')

    def test_recordings_missing_is_empty_but_auth_and_cross_instance_are_errors(self):
        self.zoom.request = Mock(side_effect=missing(3301))
        self.assertEqual(self.zoom.recordings(ROOM), {'id': NUMBER, 'uuid': SESSION, 'recording_files': [], 'status': 'absent'})
        for response in ({'id': NUMBER, 'uuid': 'another', 'recording_files': []},
                         {'id': NUMBER, 'uuid': SESSION, 'recording_files': [{'meeting_id': 'another'}]}):
            self.zoom.request = Mock(return_value=response)
            with self.assertRaises(ZoomMediaError):
                self.zoom.recordings(ROOM)
        self.zoom.request = Mock(side_effect=ZoomMediaError('auth', provider_status=403))
        with self.assertRaises(ZoomMediaError):
            self.zoom.recordings(ROOM)

    def test_download_authorization_only_for_trusted_completed_file(self):
        self.zoom.token = Mock(return_value='access-token')
        result = self.zoom.resolve_download({'status': 'completed', 'download_url': 'https://us02web.zoom.us/rec/download/file'})
        self.assertEqual(result['headers'], {'Authorization': 'Bearer access-token'})
        self.assertNotIn('access-token', result['url'])
        for url in ('https://zoom.us.evil.test/f', 'https://zoom.us@evil.test/f', 'http://zoom.us/f',
                    'https://zoom.us:1234/f', 'https://zoom.us/f#fragment', None, 123, True, 1.5,
                    'https://zoom.us/\\file', 'https://zoom.us/space file', 'https://zoom.us/' + 'x' * 16384):
            with self.assertRaises(ZoomMediaError):
                self.zoom.resolve_download({'status': 'completed', 'download_url': url})
        with self.assertRaises(ZoomMediaError):
            self.zoom.resolve_download({'status': 'processing', 'download_url': 'https://zoom.us/file'})
        self.assertEqual(self.zoom.token.call_count, 1)

    def test_legacy_materials_never_exposes_private_transport_or_false_readiness(self):
        self.zoom.recordings = Mock()
        result = self.zoom.materials(ROOM, {'recordings': [{'url': 'secret'}]})
        self.assertEqual(result['recordings'], [])
        self.assertEqual(result['video']['status'], 'error')
        self.assertEqual(result['video']['code'], 'zoom_catalog_required')
        self.assertNotIn('secret', json.dumps(result))
        self.zoom.recordings.assert_not_called()

    def test_retention_settings_are_read_only_account_scoped_and_filtered(self):
        self.zoom.request = Mock(return_value={'recording': {
            'allow_recovery_deleted_cloud_recordings': True, 'auto_delete_cmr': False,
            'other_private_setting': 'not-returned'}, 'profile': {'email': 'private'}})
        self.assertEqual(self.zoom.recording_account_id, 'account-id')
        self.assertEqual(self.zoom.recording_retention_settings(), {
            'allow_recovery_deleted_cloud_recordings': True, 'auto_delete_cmr': False})
        self.zoom.request.assert_called_once_with('/accounts/account-id/settings')
        self.zoom.request.return_value = {'recording': {}}
        self.assertIsNone(self.zoom.recording_retention_settings()['allow_recovery_deleted_cloud_recordings'])

    def test_retention_trashes_only_exact_pinned_session_file_without_retry(self):
        self.zoom.request = Mock(return_value={})
        room = {**ROOM, 'meetingSessionId': '/a//b==', 'sessionPinned': True}
        self.assertEqual(self.zoom.trash_recording_file(room, 'file-123'), {'status': 'trashed'})
        self.zoom.request.assert_called_once_with(
            '/meetings/%252Fa%252F%252Fb%253D%253D/recordings/file-123?action=trash', method='DELETE')
        self.zoom.request = Mock(side_effect=ZoomMediaError('uncertain'))
        with self.assertRaises(ZoomMediaError):
            self.zoom.trash_recording_file(room, 'file-123')
        self.assertEqual(self.zoom.request.call_count, 1)

    def test_retention_refuses_unpinned_foreign_room_or_malformed_file(self):
        self.zoom.request = Mock()
        for room, file_id in ((ROOM, 'file-1'), ({**ROOM, 'connectionId': 'another', 'sessionPinned': True}, 'file-1'),
                              ({**ROOM, 'sessionPinned': True}, '../settings'),
                              ({**ROOM, 'sessionPinned': True}, 'file?action=delete')):
            with self.assertRaises(ZoomMediaError):
                self.zoom.trash_recording_file(room, file_id)
        self.zoom.request.assert_not_called()

    def test_request_does_not_support_permanent_or_bulk_delete(self):
        self.zoom._json = Mock(return_value={})
        self.zoom.token = Mock(return_value='private-token')
        for path in ('/meetings/' + NUMBER, '/meetings/session/recordings',
                     '/meetings/session/recordings/file?action=delete',
                     '/meetings/session/recordings/file', '/accounts/account-id'):
            with self.assertRaises(ZoomMediaError):
                self.zoom.request(path, method='DELETE')
        self.zoom._json.assert_not_called()
        self.zoom.token.assert_not_called()
        self.zoom.request('/meetings/session/recordings/file?action=trash', method='DELETE')
        self.assertEqual(self.zoom._json.call_args.args[0].get_method(), 'DELETE')

    def test_trash_requires_204_response(self):
        req = urllib.request.Request('https://api.zoom.us/v2/meetings/session/recordings/file?action=trash', method='DELETE')
        self.zoom.opener = Mock()
        self.zoom.opener.open.return_value = Response({}, 200)
        with self.assertRaises(ZoomMediaError):
            self.zoom._json(req)
        self.zoom.opener.open.return_value = Response(b'', 204)
        self.assertEqual(self.zoom._json(req), {})

    def test_http_empty_204_is_supported_non204_finish_is_rejected(self):
        req = urllib.request.Request('https://api.zoom.us/v2/meetings/' + NUMBER + '/status', method='PUT')
        self.zoom.opener = Mock()
        self.zoom.opener.open.return_value = Response(b'', 204)
        self.assertEqual(self.zoom._json(req), {})
        self.zoom.opener.open.return_value = Response({}, 200)
        with self.assertRaises(ZoomMediaError):
            self.zoom._json(req)

    def test_http_provider_body_and_url_not_copied_into_error(self):
        provider_error = urllib.error.HTTPError('https://zoom.us/private?token=secret', 403,
            'private message', {}, io.BytesIO(b'{"code":4711,"message":"secret user@example.com"}'))
        self.zoom.opener = Mock()
        self.zoom.opener.open.side_effect = provider_error
        with self.assertRaises(ZoomMediaError) as caught:
            self.zoom._json(urllib.request.Request('https://zoom.us/test'))
        self.assertEqual((caught.exception.provider_status, caught.exception.zoom_code), (403, 4711))
        self.assertNotIn('secret', str(caught.exception))
        self.assertNotIn('user@example.com', str(caught.exception))

    def test_bounded_response_and_transport_errors(self):
        self.zoom.opener = Mock()
        self.zoom.opener.open.return_value = Response(b'x' * (MAX_JSON + 1))
        with self.assertRaises(ZoomMediaError):
            self.zoom._json(urllib.request.Request('https://zoom.us/test'))
        self.zoom.opener.open.side_effect = TimeoutError('private network address')
        with self.assertRaises(ZoomMediaError) as caught:
            self.zoom._json(urllib.request.Request('https://zoom.us/test'))
        self.assertNotIn('private network address', str(caught.exception))

    def test_token_cached_and_request_method_is_explicit(self):
        self.zoom._json = Mock(return_value={'access_token': 'cached', 'expires_in': 3600})
        self.assertEqual(self.zoom.token(), 'cached')
        self.assertEqual(self.zoom.token(), 'cached')
        self.assertEqual(self.zoom._json.call_count, 1)
        self.zoom._json.return_value = {}
        self.zoom.request('/meetings/' + NUMBER + '/status', {'action': 'end'}, method='PUT')
        request = self.zoom._json.call_args.args[0]
        self.assertEqual(request.get_method(), 'PUT')
        self.assertEqual(request.headers['Authorization'], 'Bearer cached')

    def test_unauthorized_invalidates_cached_token_without_replaying_mutation(self):
        self.zoom._token = 'revoked-token'
        self.zoom._token_expiry = float('inf')
        self.zoom._json = Mock(side_effect=ZoomMediaError('auth', provider_status=401))
        with self.assertRaises(ZoomMediaError):
            self.zoom.request('/users/host/meetings', {'topic': 'Test'})
        self.assertEqual(self.zoom._json.call_count, 1)
        self.assertEqual(self.zoom._token_expiry, 0)


if __name__ == '__main__':
    unittest.main()
