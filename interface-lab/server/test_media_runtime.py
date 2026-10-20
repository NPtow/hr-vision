import concurrent.futures
import io
import http.client
import json
import threading
import unittest
from unittest.mock import patch

from daily_media import DailyMedia
from domain import Problem
from media_runtime import current_deadline, operation_lock, provider_budget, provider_timeout


class MediaRuntimeTest(unittest.TestCase):
    def test_nested_budget_cannot_extend_outer_deadline_and_is_cleared(self):
        with patch('media_runtime.time.monotonic', return_value=100):
            with provider_budget(deadline=110):
                self.assertEqual(provider_timeout(), 10)
                with provider_budget(deadline=200):
                    self.assertEqual(provider_timeout(), 10)
                self.assertEqual(provider_timeout(), 10)
            self.assertIsNone(current_deadline())
            self.assertEqual(provider_timeout(), 18)

    def test_expired_budget_makes_no_network_request(self):
        media = DailyMedia('unit-key', True)
        with provider_budget(deadline=0), patch('daily_media.urllib.request.urlopen') as network:
            with self.assertRaises(Problem) as error:
                media.request('/meeting-tokens', {})
        self.assertEqual(error.exception.status, 502)
        network.assert_not_called()

    def test_contended_operation_fails_promptly_without_unlocking_the_owner(self):
        lock = threading.Lock()
        lock.acquire()
        try:
            with patch('media_runtime.LOCK_WAIT_SECONDS', 0.01):
                with self.assertRaises(Problem) as error, operation_lock(lock):
                    self.fail('must not enter the occupied operation')
            self.assertEqual(error.exception.status, 503)
            self.assertTrue(lock.locked())
        finally:
            lock.release()
        with operation_lock(lock):
            self.assertIsNotNone(current_deadline())
        self.assertIsNone(current_deadline())
        self.assertFalse(lock.locked())

    def test_budget_does_not_leak_between_requests_on_different_threads(self):
        entered, leave = threading.Event(), threading.Event()
        def first():
            with provider_budget(deadline=0):
                entered.set()
                self.assertTrue(leave.wait(2))
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(first)
            self.assertTrue(entered.wait(2))
            try:
                self.assertEqual(provider_timeout(), 18)
            finally:
                leave.set()
            pending.result()

    def test_material_workers_share_the_call_deadline(self):
        media = DailyMedia('unit-key', True)
        deadlines = []
        def channel(*args):
            deadlines.append(current_deadline())
            return [], {'status': 'absent', 'ids': []}
        with provider_budget(deadline=123), patch.object(media, '_material_channel', side_effect=channel):
            media.materials({'name': 'test', 'id': 'test'})
        self.assertEqual(deadlines, [123, 123])

    def test_many_slow_material_parts_stop_at_budget_but_keep_ready_files(self):
        media = DailyMedia('unit-key', True)
        tick, calls = [0], []
        def network(request, timeout):
            calls.append((request.full_url, timeout))
            tick[0] += min(10, timeout)
            if timeout < 10:
                raise TimeoutError()
            payload = ({'data': [{'id': str(i), 'room_name': 'test', 'status': 'finished'} for i in range(20)]}
                       if '/recordings?' in request.full_url else {'download_link': 'https://media.example/ready'})
            return io.BytesIO(json.dumps(payload).encode())
        with patch('media_runtime.time.monotonic', side_effect=lambda: tick[0]), \
             patch('daily_media.urllib.request.urlopen', side_effect=network):
            files, state = media._bounded_channel(25, {'name': 'test', 'id': 'test'}, 'video', [], True)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[-1][1], 5)
        self.assertEqual(len(files), 1)
        self.assertEqual(state['status'], 'ready')
        self.assertFalse(state['complete'])
        self.assertIn('error', state)

    def test_invalid_provider_payload_is_redacted_gateway_error(self):
        media = DailyMedia('unit-key', True)
        for content in (b'<html>private provider details</html>', b'null', b'[]', b'\xff', b' ' * (2*1024*1024+1)):
            with self.subTest(size=len(content)):
                with patch('daily_media.urllib.request.urlopen', return_value=io.BytesIO(content)):
                    with self.assertRaises(Problem) as error:
                        media.request('/rooms/test')
                self.assertEqual(error.exception.status, 502)
                self.assertNotIn('private', error.exception.message)

    def test_bad_stop_response_still_disconnects_users_without_false_completion(self):
        media = DailyMedia('unit-key', True)
        calls = []
        def network(request, timeout):
            path = request.full_url
            calls.append(path)
            if path.endswith('/recordings/stop'):
                return io.BytesIO(b'<html>bad gateway</html>')
            if '/presence?' in path:
                return io.BytesIO(b'{"total_count":1,"data":[{"id":"one"}]}')
            if path.endswith('/eject'):
                return io.BytesIO(b'{"ejectedIds":["one"]}')
            return io.BytesIO(b'{"status":"sent"}')
        with patch('daily_media.urllib.request.urlopen', side_effect=network):
            with self.assertRaises(Problem) as error:
                media.finish({'name': 'test', 'id': 'test'})
        self.assertEqual(error.exception.status, 502)
        self.assertTrue(any(path.endswith('/transcription/stop') for path in calls))
        self.assertTrue(any(path.endswith('/eject') for path in calls))
        self.assertFalse(any(path.endswith('/rooms/test') for path in calls))

    def test_broken_response_body_is_a_retryable_redacted_provider_error(self):
        media = DailyMedia('secret-api-key', True)
        response = io.BytesIO()
        with patch.object(response, 'read', side_effect=http.client.IncompleteRead(b'secret response')), \
             patch('daily_media.urllib.request.urlopen', return_value=response), \
             self.assertLogs('daily_media', level='WARNING') as logs:
            with self.assertRaises(Problem) as error:
                media.request('/rooms/private-room/recordings/stop', {'token': 'secret-token'})
        self.assertEqual(error.exception.status, 502)
        output = ' '.join(logs.output)
        self.assertIn('operation=rooms.stop', output)
        self.assertNotIn('secret', output)
        self.assertNotIn('private-room', output)

    def test_broken_transcript_download_does_not_hide_ready_video(self):
        media = DailyMedia('unit-key', True)
        class BrokenDownload(io.BytesIO):
            def read(self, *_):
                raise http.client.IncompleteRead(b'partial private transcript')
        def network(request, timeout):
            if isinstance(request, str):
                return BrokenDownload()
            path = request.full_url
            if '/recordings?' in path:
                payload = {'data': [{'id': 'video', 'room_name': 'room', 'status': 'finished'}]}
            elif '/transcript?' in path:
                payload = {'data': [{'transcriptId': 'text', 'roomId': 'room', 'status': 't_finished'}]}
            else:
                payload = {'download_link': 'https://media.example/file'}
            return io.BytesIO(json.dumps(payload).encode())
        with patch('daily_media.urllib.request.urlopen', side_effect=network):
            result = media.materials({'name': 'room', 'id': 'room'})
        self.assertEqual(result['video']['status'], 'ready')
        self.assertEqual(len(result['recordings']), 1)
        self.assertEqual(result['transcription']['status'], 'error')
        self.assertNotIn('private', json.dumps(result))
