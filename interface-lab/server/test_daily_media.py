import unittest
from unittest.mock import Mock
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
        self.media.request = Mock(side_effect=ProviderError(404))
        self.media.finish(self.room)

    def test_transient_failure_does_not_finish_room(self):
        self.media.request = Mock(side_effect=ProviderError(503))
        with self.assertRaises(ProviderError): self.media.finish(self.room)


if __name__ == '__main__': unittest.main()
