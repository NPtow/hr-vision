import unittest
from unittest.mock import Mock, patch
from daily_media import DailyMedia, ProviderError
from domain import Problem


class DailyMediaTest(unittest.TestCase):
    def setUp(self):
        self.media = DailyMedia('test-key', True)
        self.room = {'name': 'hrv-test', 'id': 'room-id', 'url': 'https://example.daily.co/hrv-test', 'privacy': 'private'}

    def test_missing_room_created_private(self):
        self.media.request = Mock(side_effect=[ProviderError(404), self.room])
        self.assertEqual(self.media.room('test')['id'], 'room-id')
        payload = self.media.request.call_args.args[1]
        self.assertEqual(payload['privacy'], 'private')
        self.assertFalse(payload['properties']['enable_knocking'])
        self.assertEqual(payload['properties']['max_participants'], 2)

    def test_retry_reuses_room_and_renews_expiry(self):
        self.media.request = Mock(side_effect=[self.room, self.room])
        self.media.room('test')
        self.assertEqual(self.media.request.call_args.args[0], '/rooms/hrv-test')
        self.assertIn('exp', self.media.request.call_args.args[1]['properties'])

    def test_intro_room_disables_recording_and_transcription_ui(self):
        self.media.request = Mock(side_effect=[ProviderError(404), self.room])
        self.media.room('test', recordable=False)
        properties = self.media.request.call_args.args[1]['properties']
        # A synthetic live probe verified false is accepted; [] is rejected.
        self.assertIs(properties['enable_recording'], False)
        self.assertFalse(properties['permissions']['canAdmin'])
        for field in ('enable_transcription_storage', 'enable_live_captions_ui'):
            self.assertIs(properties[field], False)

    def test_recordable_room_restores_capture_capability_without_auto_start(self):
        self.media.request = Mock(side_effect=[self.room, self.room])
        self.media.room('test', recordable=True)
        properties = self.media.request.call_args.args[1]['properties']
        self.assertEqual(properties['enable_recording'], 'cloud')
        self.assertTrue(properties['enable_transcription_storage'])
        self.assertNotIn('start_cloud_recording', properties)

    def test_deleted_room_is_recreated(self):
        self.media.request = Mock(side_effect=[ProviderError(404), {**self.room, 'id': 'new-id'}])
        self.assertEqual(self.media.room('test')['id'], 'new-id')

    def test_auth_failure_not_treated_as_missing_room(self):
        self.media.request = Mock(side_effect=ProviderError(401))
        with self.assertRaises(ProviderError): self.media.room('test')
        self.assertEqual(self.media.request.call_count, 1)

    def test_public_room_is_not_used(self):
        self.media.request = Mock(return_value={**self.room, 'privacy': 'public'})
        with self.assertRaises(Problem): self.media.room('test')
        self.assertEqual(self.media.request.call_count, 1)

    def test_expired_room_can_be_finished(self):
        def provider(path, body=None):
            if '/presence?' in path:
                return {'total_count': 0, 'data': []}
            raise ProviderError(404)
        self.media.request = Mock(side_effect=provider)
        self.media.finish(self.room)
        self.assertEqual(self.media.request.call_count, 4)

    def test_transient_failure_does_not_finish_room(self):
        self.media.request = Mock(side_effect=ProviderError(503))
        with self.assertRaises(ProviderError): self.media.finish(self.room)

    def finishing_provider(self, snapshots, stop_error=None):
        snapshots = list(snapshots)
        def provider(path, body=None):
            if path.endswith('/recordings/stop') and stop_error:
                raise stop_error
            if '/presence?' in path:
                data = snapshots.pop(0) if len(snapshots) > 1 else snapshots[0]
                return {'total_count': len(data), 'data': data}
            return {'status': 'sent'}
        self.media.request = Mock(side_effect=provider)
        return self.media.request

    def test_stop_capture_eject_and_verify_before_expiring_room(self):
        participants = [{'id': 'one', 'userId': 'recruiter', 'room': self.room['name']},
                        {'id': 'two', 'userId': 'candidate', 'room': self.room['name']}]
        provider = self.finishing_provider([participants, []])
        self.media.finish({**self.room, 'recordingInstanceId': 'custom-instance'})
        calls = provider.call_args_list
        self.assertEqual(calls[0].args, ('/rooms/hrv-test/recordings/stop', {'type': 'cloud', 'instanceId': 'custom-instance'}))
        self.assertEqual(calls[1].args, ('/rooms/hrv-test/transcription/stop', {}))
        self.assertEqual(calls[2].args, ('/rooms/hrv-test/presence?limit=100',))
        self.assertEqual(calls[3].args, ('/rooms/hrv-test/eject', {'ids': ['one', 'two'], 'user_ids': ['recruiter', 'candidate'], 'ban': True}))
        self.assertEqual(calls[4].args, ('/rooms/hrv-test/presence?limit=100',))
        self.assertIn('exp', calls[5].args[1]['properties'])

    def test_accepted_eject_without_departure_is_not_completion(self):
        provider = self.finishing_provider([[{'id': 'one', 'userId': 'candidate'}]])
        with patch('daily_media.time.sleep'), self.assertRaises(Problem) as caught:
            self.media.finish(self.room)
        self.assertEqual(caught.exception.status, 502)
        self.assertFalse(any(call.args[0] == '/rooms/hrv-test' for call in provider.call_args_list))

    def test_exact_ejected_ids_override_stale_presence(self):
        def provider(path, body=None):
            if '/presence?' in path:
                return {'total_count': 2, 'data': [{'id': 'one'}, {'id': 'two'}]}
            if path.endswith('/eject'):
                return {'ejectedIds': ['two', 'one']}
            return {'status': 'sent'}
        self.media.request = Mock(side_effect=provider)
        self.media.finish(self.room)
        paths = [call.args[0] for call in self.media.request.call_args_list]
        self.assertEqual(paths.count('/rooms/hrv-test/presence?limit=100'), 1)
        self.assertEqual(paths[-1], '/rooms/hrv-test')

    def test_partial_ejected_ids_are_not_full_completion(self):
        def provider(path, body=None):
            if '/presence?' in path:
                return {'total_count': 2, 'data': [{'id': 'one'}, {'id': 'two'}]}
            return {'ejectedIds': ['one']} if path.endswith('/eject') else {'status': 'sent'}
        self.media.request = Mock(side_effect=provider)
        with patch('daily_media.time.sleep'), self.assertRaises(Problem):
            self.media.finish(self.room)

    def test_capture_failure_still_attempts_ejection_but_no_false_success(self):
        provider = self.finishing_provider([[{'id': 'one', 'userId': 'candidate'}], []], ProviderError(503))
        with self.assertRaises(ProviderError):
            self.media.finish(self.room)
        paths = [call.args[0] for call in provider.call_args_list]
        self.assertIn('/rooms/hrv-test/transcription/stop', paths)
        self.assertIn('/rooms/hrv-test/eject', paths)
        self.assertNotIn('/rooms/hrv-test', paths)

    def test_404_does_not_skip_an_active_session(self):
        provider = self.finishing_provider([[{'id': 'one', 'userId': 'candidate'}], []], ProviderError(404))
        self.media.finish(self.room)
        self.assertIn('/rooms/hrv-test/eject', [call.args[0] for call in provider.call_args_list])

    def test_repeat_finish_when_session_empty_is_safe(self):
        provider = self.finishing_provider([[]])
        self.media.finish(self.room)
        self.media.finish(self.room)
        paths = [call.args[0] for call in provider.call_args_list]
        self.assertEqual(paths.count('/rooms/hrv-test/recordings/stop'), 2)
        self.assertEqual(paths.count('/rooms/hrv-test/transcription/stop'), 2)
        self.assertNotIn('/rooms/hrv-test/eject', paths)

    def test_bad_presence_cannot_be_treated_as_empty(self):
        self.media.request = Mock(return_value={'data': []})
        with self.assertRaises(Problem):
            self.media.finish(self.room)

    def test_bad_request_from_stop_is_not_swallowed(self):
        self.finishing_provider([[]], ProviderError(400))
        with self.assertRaises(ProviderError) as caught:
            self.media.finish(self.room)
        self.assertEqual(caught.exception.provider_status, 400)

    def test_known_disabled_intro_closes_without_capture_stop(self):
        def provider(path, body=None):
            if '/presence?' in path:
                return {'total_count': 1, 'data': [{'id': 'one', 'userId': 'candidate'}]}
            if path.endswith('/eject'):
                return {'ejectedIds': ['one']}
            return {'status': 'sent'}
        self.media.request = Mock(side_effect=provider)
        self.media.finish({**self.room, 'captureNeverStarted': True})
        paths = [call.args[0] for call in self.media.request.call_args_list]
        self.assertFalse(any(path.endswith('/stop') for path in paths))
        self.assertIn('/rooms/hrv-test/eject', paths)
        self.assertEqual(paths[-1], '/rooms/hrv-test')


if __name__ == '__main__': unittest.main()
