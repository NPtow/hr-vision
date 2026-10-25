"""Black-box HTTP acceptance: only public CLI, HTTP, and isolated service volume."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

ROOT = Path(__file__).resolve().parents[1]
TOKENS = {'source-a': 'acceptance-source-a-secret-0123456789',
          'source-b': 'acceptance-source-b-secret-0123456789'}
VTT = b'WEBVTT\n\n00:00:00.000 --> 00:00:01.500\nHello fixture\n'
MEDIA = b'acceptance synthetic audio bytes, no private recordings'


class ApiAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='transcription-http-')
        self.addCleanup(self.directory.cleanup)
        self.data = Path(self.directory.name)
        self.session = requests.Session()
        self.session.trust_env = False
        self.addCleanup(self.session.close)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith('TRANSCRIPTION_')}
        self.env.update(TRANSCRIPTION_API_KEYS=json.dumps(TOKENS),
                        TRANSCRIPTION_DATA_DIR=str(self.data),
                        TRANSCRIPTION_BROKER_URL='', PYTHONUNBUFFERED='1')

    def start_api(self, **settings):
        self.env.update({'TRANSCRIPTION_' + k.upper(): str(v) for k, v in settings.items()})
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        # A file avoids a subprocess hanging when its stdout pipe fills.
        log = tempfile.TemporaryFile()
        self.addCleanup(log.close)
        self.api = subprocess.Popen([sys.executable, '-m', 'md_transcription', 'api',
                                     '--host', '127.0.0.1', '--port', str(port)],
                                    cwd=ROOT, env=self.env, stdout=log, stderr=log)
        self.addCleanup(self.stop_process, self.api)
        self.base = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if self.api.poll() is not None:
                log.seek(0)
                self.fail('API exited: ' + log.read(8000).decode(errors='replace'))
            try:
                if self.session.get(self.base + '/health/live', timeout=.3).status_code == 200:
                    return
            except requests.RequestException:
                pass
            threading.Event().wait(.03)
        self.fail('API did not become live within 12 seconds')

    @staticmethod
    def stop_process(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def request(self, method, path, client='source-a', **kwargs):
        headers = kwargs.pop('headers', {})
        if client is not None:
            headers['Authorization'] = 'Bearer ' + TOKENS[client]
        return self.session.request(method, self.base + path, headers=headers, timeout=4, **kwargs)

    def upload(self, body=MEDIA, key='fixture-1', filename='recording.wav', client='source-a'):
        return self.request('POST', '/v1/jobs?language=ru', client=client, data=body,
                            headers={'Idempotency-Key': key, 'X-Filename': filename,
                                     'Content-Type': 'application/octet-stream'})

    def start_asr(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                message = BytesParser(policy=default).parsebytes(
                    b'Content-Type: ' + self.headers['Content-Type'].encode() + b'\r\n\r\n' + raw)
                fields = {part.get_param('name', header='content-disposition'): part
                          for part in message.iter_parts()}
                received.append({'path': self.path,
                                 'file': fields['file'].get_payload(decode=True),
                                 'filename': fields['file'].get_filename(),
                                 'model': fields['model'].get_payload(decode=True),
                                 'language': fields['language'].get_payload(decode=True),
                                 'key': self.headers.get('Idempotency-Key')})
                result = json.dumps({'text': 'Hello fixture', 'segments': [
                    {'start': 0, 'end': 1.5, 'text': 'Hello fixture'}]}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(result)))
                self.end_headers()
                self.wfile.write(result)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        stopped = []

        def stop():
            if not stopped:
                stopped.append(True)
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
        self.addCleanup(stop)
        self.start_api(asr_url=f'http://127.0.0.1:{server.server_port}/v1/audio/transcriptions',
                       asr_model='acceptance-fixture-only', allow_insecure_asr_http='true')
        return received, stop

    def test_auth_and_exact_vtt_normalization(self):
        self.start_api()
        for path in ('/health/ready', '/v1/jobs/unknown', '/v1/jobs/unknown/result'):
            self.assertEqual(self.request('GET', path, client=None).status_code, 401)
        self.assertEqual(self.session.get(self.base + '/health/ready', headers={
            'Authorization': 'Bearer invalid'}, timeout=3).status_code, 401)
        response = self.request('POST', '/v1/normalize?format=vtt', data=VTT)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'schema_version': 1, 'format': 'vtt',
            'segments': [{'start_ms': 0, 'end_ms': 1500, 'text': 'Hello fixture', 'speaker': None}],
            'full_text': 'Hello fixture', 'text': 'Hello fixture',
            'vtt': 'WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.500\nHello fixture\n\n',
            'warnings': ['missing_speakers']})
        malformed = self.request('POST', '/v1/normalize?format=vtt', data=b'WEBVTT\n\nbroken')
        self.assertEqual((malformed.status_code, malformed.json()['detail']), (422, 'invalid_transcript'))
        disabled = self.upload()
        self.assertEqual((disabled.status_code, disabled.json()['detail']), (503, 'asr_not_configured'))
        self.assertEqual(list((self.data / 'inputs').iterdir()), [])

    def test_normalized_result_size_is_bounded(self):
        self.start_api(max_result_bytes=80)
        response = self.request('POST', '/v1/normalize?format=vtt', data=VTT)
        self.assertEqual((response.status_code, response.json()['detail']),
                         (413, 'normalized_result_too_large'))

    def test_idempotency_scope_conflicts_and_no_inline_asr(self):
        received, _ = self.start_asr()
        first = self.upload()
        self.assertEqual(first.status_code, 202, first.text)
        job = first.json()
        self.assertTrue(job['created'])
        self.assertEqual(job['status'], 'queued')
        self.assertEqual(job['input_sha256'], hashlib.sha256(MEDIA).hexdigest())
        self.assertEqual(job['input_size'], len(MEDIA))
        replay = self.upload().json()
        self.assertEqual(replay['id'], job['id'])
        self.assertFalse(replay['created'])
        before = {p.name: p.read_bytes() for p in (self.data / 'inputs').iterdir()}
        for body, name in ((b'different synthetic bytes', 'recording.wav'), (MEDIA, 'recording.mp3')):
            response = self.upload(body=body, filename=name)
            self.assertEqual((response.status_code, response.json()['detail']), (409, 'idempotency_conflict'))
        self.assertEqual(before, {p.name: p.read_bytes() for p in (self.data / 'inputs').iterdir()})
        self.assertEqual(list((self.data / 'staging').iterdir()), [])
        for suffix in ('', '/result', '/retry'):
            method = 'POST' if suffix == '/retry' else 'GET'
            self.assertEqual(self.request(method, '/v1/jobs/' + job['id'] + suffix,
                                          client='source-b').status_code, 404)
        pending = self.request('GET', '/v1/jobs/' + job['id'] + '/result')
        self.assertEqual((pending.status_code, pending.json()['detail']), (409, 'result_not_ready'))
        independent = self.upload(client='source-b').json()
        self.assertNotEqual(independent['id'], job['id'])
        self.assertEqual(received, [])
        self.assertEqual(self.request('GET', '/v1/jobs/' + job['id']).json()['attempts'], 0)

    def test_chunked_oversize_upload_cleans_staging(self):
        self.start_api(asr_url='http://127.0.0.1:1/unused', asr_model='acceptance-fixture-only',
                       allow_insecure_asr_http='true', max_input_bytes=8)
        response = self.request('POST', '/v1/jobs', data=iter([b'1234', b'56789']),
                                headers={'Idempotency-Key': 'bounded', 'X-Filename': 'fixture.wav'})
        self.assertEqual((response.status_code, response.json()['detail']), (413, 'input_too_large'))
        self.assertEqual(list((self.data / 'inputs').iterdir()), [])
        self.assertEqual(list((self.data / 'staging').iterdir()), [])

    def test_separate_worker_process_and_durable_result_after_asr_stops(self):
        received, stop_asr = self.start_asr()
        response = self.upload()
        self.assertEqual(response.status_code, 202, response.text)
        job = response.json()
        self.assertEqual(received, [])
        completed = subprocess.run([sys.executable, '-m', 'md_transcription', 'run-once',
                                    '--job-id', job['id']], cwd=ROOT, env=self.env,
                                   capture_output=True, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stderr[-8000:].decode())
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0], {'path': '/v1/audio/transcriptions', 'file': MEDIA,
                                      'filename': 'input.wav', 'model': b'acceptance-fixture-only',
                                      'language': b'ru', 'key': job['id']})
        stop_asr()
        self.assertEqual(self.request('GET', '/v1/jobs/' + job['id']).json()['status'], 'succeeded')
        result = self.request('GET', '/v1/jobs/' + job['id'] + '/result')
        self.assertEqual(result.status_code, 200, result.text)
        value = result.json()
        self.assertEqual(value['job_id'], job['id'])
        self.assertEqual(value['input_sha256'], hashlib.sha256(MEDIA).hexdigest())
        self.assertEqual(value['processing_status'], 'completed')
        self.assertEqual(value['coverage'], 'not_independently_verified')
        self.assertEqual(value['model'], 'acceptance-fixture-only')
        self.assertEqual(value['transcript']['full_text'], 'Hello fixture')
        self.assertEqual(value['transcript']['segments'], [
            {'start_ms': 0, 'end_ms': 1500, 'text': 'Hello fixture', 'speaker': None}])
        self.assertEqual(self.request('GET', '/v1/jobs/' + job['id'] + '/result',
                                      client='source-b').status_code, 404)
        # Read from a fresh API process to exclude an API-local result cache.
        self.stop_process(self.api)
        self.start_api()
        self.assertEqual(self.request('GET', '/v1/jobs/' + job['id'] + '/result').json(), value)


if __name__ == '__main__':
    unittest.main()
