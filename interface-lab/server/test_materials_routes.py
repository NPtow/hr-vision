"""Real HTTP wiring with isolated state and signed synthetic provider events."""
import base64
import hashlib
import hmac
import http.client
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


class MaterialsRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = tempfile.TemporaryDirectory(prefix='materials-http-')
        cls.secret = b'synthetic-daily-webhook-secret-only'
        cls.env = patch.dict(os.environ, {'HR_DATA_DIR': cls.data.name,
            'HR_TEAM_KEY': 'synthetic-test-team-only', 'HR_VIDEO_ENABLED': '0',
            'HR_NOTIFICATIONS_ENABLED': '0', 'HR_HH_ENABLED': '0', 'DAILY_API_KEY': '',
            'HR_MATERIALS_ENABLED': '1', 'HR_MATERIALS_STORAGE_BACKEND': 'filesystem',
            'HR_MATERIALS_VIDEO_POLICY': 'remote', 'HR_MATERIALS_CONNECTION_ID': 'daily-test',
            'HR_MATERIALS_DB_PATH': str(Path(cls.data.name) / 'journey.sqlite3'),
            'HR_MATERIALS_STORAGE_ROOT': str(Path(cls.data.name) / 'objects'),
            'HR_MATERIALS_DAILY_WEBHOOK_HMAC': base64.b64encode(cls.secret).decode(),
            'CELERY_BROKER_URL': 'amqp://unreachable.invalid', 'HR_RECRUITER_ORIGIN': 'https://test.invalid',
            'HR_ADDITIONAL_ORIGINS': 'https://test.invalid'})
        cls.env.start()
        spec = importlib.util.spec_from_file_location('materials_http_test_app', Path(__file__).with_name('app.py'))
        cls.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.app)
        cls.server = cls.app.ThreadingHTTPServer(('127.0.0.1', 0), cls.app.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.env.stop()
        cls.data.cleanup()

    def post(self, raw, signed=True):
        timestamp = str(int(time.time()))
        signature = base64.b64encode(hmac.digest(self.secret, timestamp.encode() + b'.' + raw, hashlib.sha256)).decode()
        headers = {'Content-Type': 'application/json', 'X-Webhook-Timestamp': timestamp,
                   'X-Webhook-Signature': signature if signed else 'invalid'}
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request('POST', '/api/hr/materials/webhooks/daily', body=raw, headers=headers)
            result = connection.getresponse()
            return result.status, json.loads(result.read())
        finally:
            connection.close()

    def test_enabled_application_uses_catalogue_without_network_at_boot(self):
        self.assertIsNotNone(self.app.CANDIDATE_PORTAL.materials_catalog)
        self.assertEqual(self.app.MATERIALS_RUNTIME.connection_id, 'daily-test')

    def test_handshake_verifies_original_serialization(self):
        status, body = self.post(b'{ "test" : "test" }')
        self.assertEqual(status, 200, body)
        self.assertTrue(body['test'])

    def test_invalid_signature_is_rejected_without_persistence(self):
        with self.app.connect() as db:
            before = db.execute('SELECT count(*) FROM media_events').fetchone()[0]
        status, _ = self.post(b'{"test":"test"}', signed=False)
        self.assertEqual(status, 401)
        with self.app.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM media_events').fetchone()[0], before)

    def test_repeated_event_is_durable_and_does_not_invent_an_owner(self):
        raw = json.dumps({'id': 'synthetic-event-one', 'type': 'recording.ready-to-download',
                          'payload': {'recording_id': 'synthetic-recording', 'room_name': 'hrv-no-owner',
                                      'download_link': 'https://private.invalid/must-not-persist'}}).encode()
        self.assertEqual(self.post(raw)[0], 200)
        self.assertEqual(self.post(raw)[0], 200)
        with self.app.connect() as db:
            rows = db.execute('SELECT payload FROM media_events WHERE event_id=?', ('synthetic-event-one',)).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertNotIn('must-not-persist', rows[0][0])
            self.assertEqual(db.execute('SELECT count(*) FROM media_sources').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
