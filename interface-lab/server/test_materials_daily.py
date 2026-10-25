import base64
from dataclasses import asdict
import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from daily_media import DailyMedia, ProviderError
from domain import Problem
from materials_capture import Artifact, Source
from materials_daily import DailyCapture


class DailyCaptureTest(unittest.TestCase):
    def setUp(self):
        self.media = DailyMedia('test-api-key', True)
        self.source = Source('daily', 'daily', 'room-id', 'hrv-owner', 'session-one')
        self.adapter = DailyCapture(self.media, page_size=2, max_pages=3)

    def recording(self, rid='video-one', **kwargs):
        return {'id': rid, 'room_name': 'hrv-owner', 'mtgSessionId': 'session-one',
                'status': 'finished', 'duration': 20, **kwargs}

    def transcript(self, rid='text-one', **kwargs):
        return {'transcriptId': rid, 'roomId': 'room-id', 'mtgSessionId': 'session-one',
                'status': 't_finished', 'isVttAvailable': True, **kwargs}

    def metadata(self, recordings=None, transcripts=None):
        recordings = [] if recordings is None else recordings
        transcripts = [] if transcripts is None else transcripts
        def request(path, *args):
            self.assertNotIn('access-link', path)
            values = transcripts if path.startswith('/transcript?') else recordings
            if not path.startswith(('/transcript?', '/recordings?')):
                raise AssertionError(path)
            query = parse_qs(urlsplit(path).query)
            identifier = 'transcriptId' if path.startswith('/transcript?') else 'id'
            cursor = query.get('starting_after', [None])[0]
            start = 0 if cursor is None else next(i + 1 for i, row in enumerate(values) if row[identifier] == cursor)
            return {'total_count': len(values), 'data': values[start:start + 2]}
        self.media.request = Mock(side_effect=request)

    def test_discovery_metadata_only_and_exact_session(self):
        self.metadata([self.recording(), self.recording('other', mtgSessionId='different')], [self.transcript()])
        with patch('urllib.request.urlopen', side_effect=AssertionError('No file downloads')):
            result = self.adapter.discover(asdict(self.source))
        self.assertEqual([a.provider_asset_id for a in result.artifacts], ['video-one', 'text-one'])
        self.assertTrue(result.complete)
        self.assertEqual(set(result.statuses), {'video', 'transcript'})
        serialized = json.dumps(asdict(result))
        self.assertNotIn('http', serialized)

    def test_room_without_session_blocks_without_requests(self):
        self.media.request = Mock()
        result = self.adapter.discover({**asdict(self.source), 'session_id': ''})
        self.assertFalse(result.complete)
        self.assertEqual(result.statuses['video']['status'], 'blocked')
        self.media.request.assert_not_called()

    def test_owned_room_discovers_separate_exact_sessions(self):
        self.metadata([self.recording(), self.recording('second', mtgSessionId='session-two')], [self.transcript()])
        source = {**asdict(self.source), 'session_id': '',
                  'exclusive_room_owner': {'kind': 'interview', 'id': 'owner'}}
        result = self.adapter.discover(source)
        self.assertTrue(result.complete)
        self.assertEqual({a.source.session_id for a in result.artifacts}, {'session-one', 'session-two'})
        self.assertNotEqual(result.artifacts[0].asset_key, result.artifacts[1].asset_key)

    def test_wrong_owner_attestation_does_not_allow_room_only_discovery(self):
        self.media.request = Mock()
        result = self.adapter.discover({**asdict(self.source), 'session_id': '',
                                       'exclusive_room_owner': {'kind': 'interview', 'id': 'someone-else'}})
        self.assertFalse(result.complete)
        self.media.request.assert_not_called()

    def test_missing_provider_session_is_blocked(self):
        row = self.recording(mtgSessionId=None)
        self.media.request = Mock(side_effect=lambda path: row if '/video-one' in path else
                                  {'total_count': 1, 'data': [row]} if path.startswith('/recordings?') else
                                  {'total_count': 0, 'data': []})
        result = self.adapter.discover(asdict(self.source))
        self.assertEqual(result.artifacts, [])
        self.assertFalse(result.complete)
        self.assertEqual(result.statuses['video']['reason'], 'ambiguous_provider_binding')

    def test_channel_failure_does_not_hide_other_channel(self):
        def request(path):
            if path.startswith('/recordings?'):
                raise ProviderError(503)
            return {'total_count': 1, 'data': [self.transcript()]}
        self.media.request = Mock(side_effect=request)
        result = self.adapter.discover(asdict(self.source))
        self.assertEqual([a.kind for a in result.artifacts], ['transcript'])
        self.assertEqual(result.statuses['video']['status'], 'error')
        self.assertEqual(result.statuses['transcript']['status'], 'ready')

    def test_all_pages_not_first_twenty(self):
        self.adapter = DailyCapture(self.media, page_size=2, max_pages=20)
        self.metadata([self.recording(str(i)) for i in range(25)])
        result = self.adapter.discover(asdict(self.source))
        self.assertEqual(len(result.artifacts), 25)
        self.assertTrue(result.complete)

    def test_page_bound_is_explicit_incomplete(self):
        self.metadata([self.recording(str(i)) for i in range(7)])
        result = self.adapter.discover(asdict(self.source))
        self.assertEqual(len(result.artifacts), 6)
        self.assertFalse(result.complete)
        self.assertEqual(result.statuses['video']['reason'], 'metadata_page_limit')

    def test_duplicate_cursor_cannot_loop_or_report_complete(self):
        self.media.request = Mock(side_effect=lambda path: {'total_count': 10, 'data': [self.recording()]} if
                                  path.startswith('/recordings?') else {'total_count': 0, 'data': []})
        result = self.adapter.discover(asdict(self.source))
        self.assertFalse(result.complete)
        self.assertEqual(result.statuses['video']['reason'], 'metadata_pagination_stalled')

    def test_missing_room_in_list_requires_exact_record_inspection(self):
        row = self.recording()
        row.pop('room_name')
        self.media.request = Mock(side_effect=lambda path: self.recording() if path == '/recordings/video-one' else
                                  {'total_count': 1, 'data': [row]} if path.startswith('/recordings?') else
                                  {'total_count': 0, 'data': []})
        result = self.adapter.discover(asdict(self.source))
        self.assertTrue(result.complete)
        self.assertEqual(result.artifacts[0].provider_asset_id, 'video-one')

    def test_wrong_room_not_selected_even_if_session_matches(self):
        self.metadata([self.recording(room_name='other')], [self.transcript(roomId='other')])
        result = self.adapter.discover(asdict(self.source))
        self.assertEqual(result.artifacts, [])

    def test_pending_error_and_missing_vtt_are_not_ready(self):
        self.metadata([self.recording(status='in-progress')], [self.transcript(isVttAvailable=False)])
        result = self.adapter.discover(asdict(self.source))
        self.assertEqual([a.status for a in result.artifacts], ['processing', 'missing'])
        self.assertEqual(result.statuses['video']['status'], 'processing')

    def test_inspect_known_exact_ref_and_resolve_fresh_each_attempt(self):
        artifact = Artifact(self.source, 'video-one', 'video', 'ready')
        count = 0
        def request(path):
            nonlocal count
            if path.endswith('/access-link'):
                count += 1
                return {'download_link': f'https://store.example/asset?signature={count}',
                        'expires': int(time.time()) + 300}
            return self.recording()
        self.media.request = Mock(side_effect=request)
        first = self.adapter.resolve(asdict(artifact))
        second = self.adapter.resolve(artifact)
        self.assertNotEqual(first.url, second.url)
        self.assertEqual(first.headers, {})
        self.assertNotIn('signature', repr(first))

    def test_inspect_wrong_session_rejected_before_access_link(self):
        self.media.request = Mock(return_value=self.recording(mtgSessionId='different'))
        with self.assertRaises(ValueError):
            self.adapter.resolve(Artifact(self.source, 'video-one', 'video', 'ready'))
        self.assertEqual(self.media.request.call_count, 1)

    def test_malformed_or_expired_link_rejected(self):
        for link in ({'download_link': 'http://store.example/file'},
                     {'download_link': 'https://user:secret@store.example/file'},
                     {'download_link': 'https://store.example/file', 'expires': 1}):
            self.media.request = Mock(side_effect=[self.recording(), link])
            with self.assertRaises(ValueError):
                self.adapter.resolve(Artifact(self.source, 'video-one', 'video', 'ready'))

    def sign(self, event, *, timestamp='1800000000'):
        raw = json.dumps(event, ensure_ascii=False, separators=(',', ':')).encode()
        secret = b'test-secret-at-least-16-bytes'
        self.adapter.webhook_hmac = base64.b64encode(secret).decode()
        signature = base64.b64encode(hmac.digest(secret, timestamp.encode() + b'.' + raw, hashlib.sha256)).decode()
        return raw, {'X-Webhook-Timestamp': timestamp, 'X-Webhook-Signature': signature}

    def test_webhook_signature_and_sanitized_event_only(self):
        event = {'id': 'event-one', 'type': 'recording.ready-to-download', 'payload':
                 {'recording_id': 'video-one', 'room_name': 'hrv-owner',
                  'download_url': 'https://secret.example/token', 's3_key': 'private-key'}}
        raw, headers = self.sign(event)
        verified = self.adapter.verify_webhook(raw, headers, now=1800000000)
        self.assertEqual(verified['event_id'], 'event-one')
        self.assertEqual(verified['payload'], {'recording_id': 'video-one', 'room_name': 'hrv-owner'})
        self.assertNotIn('http', json.dumps(verified))
        with self.assertRaises(Problem):
            self.adapter.verify_webhook(raw + b' ', headers, now=1800000000)

    def test_webhook_disabled_stale_and_invalid_secret_fail_closed(self):
        with self.assertRaises(Problem):
            self.adapter.verify_webhook(b'{}', {})
        raw, headers = self.sign({'test': 'test'})
        with self.assertRaises(Problem):
            self.adapter.verify_webhook(raw, headers, now=1800000301)
        self.adapter.webhook_hmac = 'not-base64'
        with self.assertRaises(Problem):
            self.adapter.verify_webhook(raw, headers, now=1800000000)

    def test_signed_handshake_and_millisecond_timestamp(self):
        raw, headers = self.sign({'test': 'test'}, timestamp='1800000000000')
        self.assertTrue(self.adapter.verify_webhook(raw, headers, now=1800000000)['test'])


if __name__ == '__main__':
    unittest.main()
