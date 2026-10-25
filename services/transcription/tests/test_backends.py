import copy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import asyncio
from md_transcription.backends import BackendError, HttpAsrBackend, _provider_result
from md_transcription.contracts import validate_normalized

class Response:
    status = 200
    body = b'{"text":"hello"}'
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    @property
    def content(self): return self
    async def iter_chunked(self, chunk_size): yield self.body

class BackendTests(unittest.TestCase):
    def test_text_only_preserves_unknowns(self):
        result = _provider_result({'text':'hello'})
        self.assertEqual(result['full_text'], 'hello')
        self.assertIsNone(result['segments'][0]['start_ms'])
        self.assertIsNone(result['segments'][0]['speaker'])
        self.assertIn('asr_text_only', result['warnings'])
    def test_malformed_partial_and_nonfinite_rejected(self):
        for payload in [
            {'text':'hello', 'segments':[]},
            {'text':'hello lost', 'segments':[{'text':'hello','start':0,'end':1}]},
            {'text':'hello','segments':[{'text':'hello','start':True,'end':1}]},
            {'text':'hello','segments':[{'text':'hello','start':float('nan'),'end':1}]},
            {'text':'hello','segments':[{'text':'hello','start':0}]},
            {'text':'hello','segments':[{'text':'hello','speaker':4}]},
        ]:
            with self.subTest(payload=payload), self.assertRaises(BackendError): _provider_result(payload)
    def test_empty_is_explicit_no_speech(self):
        with self.assertRaises(BackendError) as caught: _provider_result({'text':' '})
        self.assertEqual(caught.exception.code,'asr_no_speech')
    def test_contract_rejects_bool_mixed_times_and_projection(self):
        result = _provider_result({'text':'hello'})
        for key, value in [('schema_version',True),('full_text','different')]:
            bad = copy.deepcopy(result); bad[key] = value
            with self.assertRaises(ValueError): validate_normalized(bad)
        for value in [True, 2, float('inf')]:
            bad = copy.deepcopy(result); bad['segments'][0]['start_ms'] = value
            with self.assertRaises(ValueError): validate_normalized(bad)
    def test_transport_security_size_and_ambiguous_timeout(self):
        cfg = SimpleNamespace(asr_url='http://127.0.0.1:9999/audio/transcriptions',asr_model='configured',asr_response_format='json',asr_api_key='SECRET',asr_timeout_seconds=2,max_result_bytes=1024)
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/'audio'; path.write_bytes(b'audio')
            with patch('md_transcription.backends.aiohttp.ClientSession') as factory:
                session = factory.return_value.__aenter__.return_value
                from unittest.mock import MagicMock
                session.post = MagicMock()
                session.post.side_effect = asyncio.TimeoutError('SECRET upstream body')
                with self.assertRaises(BackendError) as caught: HttpAsrBackend(cfg).transcribe(path,'audio.wav',None,'request')
                self.assertTrue(caught.exception.uncertain)
                self.assertNotIn('SECRET',str(caught.exception))
                self.assertFalse(factory.call_args.kwargs['trust_env'])
                self.assertEqual(factory.call_args.kwargs['timeout'].total,2)
                self.assertFalse(session.post.call_args.kwargs['allow_redirects'])
                session.post.side_effect = None; session.post.return_value = Response()
                result = HttpAsrBackend(cfg).transcribe(path,'audio.wav','ru','request')
                self.assertEqual(result['text'],'hello')
                cfg.max_result_bytes=1
                with self.assertRaises(BackendError) as caught: HttpAsrBackend(cfg).transcribe(path,'audio.wav',None,'request')
                self.assertEqual(caught.exception.code,'asr_response_too_large')
    def test_remote_status_semantics(self):
        cfg = SimpleNamespace(asr_url='http://127.0.0.1/audio/transcriptions',asr_model='x',asr_response_format='json',asr_api_key='',asr_timeout_seconds=2,max_result_bytes=100)
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'a'; path.write_bytes(b'x')
            for status, retryable, uncertain in [(429,True,False),(503,False,True),(302,False,False),(401,False,False)]:
                response=Response(); response.status=status
                with patch('md_transcription.backends.aiohttp.ClientSession') as factory:
                    from unittest.mock import MagicMock
                    factory.return_value.__aenter__.return_value.post = MagicMock(return_value=response)
                    with self.assertRaises(BackendError) as caught: HttpAsrBackend(cfg).transcribe(path,'a',None,'r')
                    self.assertEqual((caught.exception.retryable,caught.exception.uncertain),(retryable,uncertain))

class LiveTransportTests(unittest.TestCase):
    def test_loopback_receives_multipart_and_returns_canonical(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        observed = {}
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                observed['path'] = self.path
                observed['idempotency'] = self.headers.get('Idempotency-Key')
                observed['body'] = self.rfile.read(int(self.headers['Content-Length']))
                payload = b'{"text":"hello","segments":[{"text":"hello","start":0.5,"end":1.5}]}'
                self.send_response(200); self.send_header('Content-Length',str(len(payload))); self.end_headers(); self.wfile.write(payload)
            def log_message(self, *args): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        cfg=SimpleNamespace(asr_url=f'http://127.0.0.1:{server.server_port}/audio/transcriptions',asr_model='configured',asr_response_format='verbose_json',asr_api_key='',asr_timeout_seconds=2,max_result_bytes=1024)
        try:
            with TemporaryDirectory() as tmp:
                path=Path(tmp)/'a'; path.write_bytes(b'synthetic-audio')
                result=HttpAsrBackend(cfg).transcribe(path,'a.wav','ru','job-id')
            self.assertEqual(result['segments'][0]['start_ms'],500)
            self.assertIsNone(result['segments'][0]['speaker'])
            self.assertEqual(observed['idempotency'],'job-id')
            self.assertEqual(observed['path'],'/audio/transcriptions')
            self.assertIn(b'synthetic-audio',observed['body'])
            self.assertIn(b'configured',observed['body'])
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)
