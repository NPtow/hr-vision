"""HTTP collector contract; parser tests live in the standalone service."""
import json
import os
import unittest
from unittest.mock import patch
import requests
import materials_transcripts as client


class TranscriptClientTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'HR_TRANSCRIPTION_URL': 'http://transcription:8080',
            'HR_TRANSCRIPTION_TOKEN': 'synthetic-token'}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        mock = patch.object(client.requests, 'Session')
        factory = mock.start()
        self.addCleanup(mock.stop)
        self.session = factory.return_value.__enter__.return_value
        self.response = self.session.post.return_value.__enter__.return_value
        self.response.status_code = 200
        self.value = {'schema_version': 1, 'format': 'vtt', 'segments': [
            {'start_ms': 0, 'end_ms': 1000, 'text': 'Hello', 'speaker': None}],
            'text': 'Hello', 'full_text': 'Hello', 'vtt': 'WEBVTT\n\n', 'warnings': ['missing_speakers']}
        self.body(self.value)

    def body(self, value):
        self.response.iter_content.return_value = [json.dumps(value).encode()]

    def test_request_and_canonical_result(self):
        self.assertEqual(client.normalize_transcript(b'raw', 'vtt'), self.value)
        self.assertFalse(self.session.trust_env)
        args, kw = self.session.post.call_args
        self.assertEqual(args, ('http://transcription:8080/v1/normalize',))
        self.assertEqual(kw['data'], b'raw')
        self.assertEqual(kw['params'], {'format': 'vtt'})
        self.assertEqual(kw['headers'], {'Authorization': 'Bearer synthetic-token', 'Content-Type': 'application/octet-stream'})
        self.assertFalse(kw['allow_redirects'])
        self.assertEqual(kw['timeout'], (5, 30))
        self.assertTrue(kw['stream'])

    def test_unconfigured_without_network(self):
        for key in ('HR_TRANSCRIPTION_URL', 'HR_TRANSCRIPTION_TOKEN'):
            with self.subTest(key=key), patch.dict(os.environ, {key: ''}):
                with self.assertRaises(client.TranscriptionUnavailable):
                    client.normalize_transcript(b'x', 'vtt')
        self.session.post.assert_not_called()

    def test_retryable_statuses(self):
        for status in (301, 401, 403, 404, 429, 500, 503):
            self.response.status_code = status
            with self.assertRaises(client.TranscriptionUnavailable):
                client.normalize_transcript(b'x', 'vtt')

    def test_semantic_errors(self):
        for status in (400, 413, 415, 422):
            self.response.status_code = status
            with self.assertRaises(client.TranscriptError):
                client.normalize_transcript(b'x', 'vtt')

    def test_transport_errors_are_retryable_and_redacted(self):
        for error in (requests.ConnectionError('secret-url'), requests.Timeout('secret-token')):
            self.session.post.side_effect = error
            with self.assertRaises(client.TranscriptionUnavailable) as caught:
                client.normalize_transcript(b'x', 'vtt')
            self.assertNotIn(str(error), str(caught.exception))
        self.session.post.side_effect = None
        self.response.iter_content.side_effect = requests.ConnectionError('stream')
        with self.assertRaises(client.TranscriptionUnavailable):
            client.normalize_transcript(b'x', 'vtt')

    def test_malformed_protocol(self):
        for value in ([], {}, {**self.value, 'schema_version': True},
                      {**self.value, 'warnings': [1]}, {**self.value, 'text': 9},
                      {**self.value, 'segments': [{'text': 'Hello'}]},
                      {**self.value, 'segments': [{'text': 'Hello', 'start_ms': True, 'end_ms': 1, 'speaker': None}]}):
            self.body(value)
            with self.assertRaises(client.TranscriptionUnavailable):
                client.normalize_transcript(b'x', 'vtt')
        self.response.iter_content.return_value = [b'not json']
        with self.assertRaises(client.TranscriptionUnavailable):
            client.normalize_transcript(b'x', 'vtt')

    def test_response_bound(self):
        with patch.object(client, 'MAX_RESPONSE_BYTES', 3):
            self.response.iter_content.return_value = [b'ab', b'cd']
            with self.assertRaises(client.TranscriptionUnavailable):
                client.normalize_transcript(b'x', 'vtt')
