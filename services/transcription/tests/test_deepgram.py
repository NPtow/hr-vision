"""Synthetic Deepgram prerecorded contract and restart safety, never cloud calls."""
import asyncio
from dataclasses import replace
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave
from sqlalchemy import update
from md_transcription.backends import BackendError
from md_transcription.deepgram import DeepgramBackend, parse_deepgram
from md_transcription.repository import Repository, jobs
from md_transcription.settings import Settings
from md_transcription.worker import Runtime

PAYLOAD={'metadata':{'request_id':'fixture-request','model_info':{'nova-id':{'name':'nova-3','version':'fixture'}},
    'diarize_info':{'model':'v2'}},'results':{'channels':[{'alternatives':[{'transcript':'Привет мир. Да.',
    'words':[{'word':'привет','punctuated_word':'Привет','start':0.1,'end':0.3,'speaker':0},
    {'word':'мир','punctuated_word':'мир.','start':0.3,'end':0.6,'speaker':0},
    {'word':'да','punctuated_word':'Да.','start':0.9,'end':1.2,'speaker':1}]}]}],
    'utterances':[{'transcript':'Привет мир.','start':0.1,'end':0.6,'speaker':0}]}}
BODY=json.dumps(PAYLOAD,ensure_ascii=False).encode()

def wav(path):
    with wave.open(str(path),'wb') as out:
        out.setparams((1,2,16000,0,'NONE','not compressed')); out.writeframes(b'\0\0'*3200)

class DeepgramTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.cfg=Settings(data_dir=Path(self.tmp.name),backend_kind='deepgram',deepgram_api_key='synthetic-secret')
        self.repo=Repository(self.cfg.database_url); self.repo.init()
        self.backend=DeepgramBackend(self.cfg); self.runtime=Runtime(self.cfg,repository=self.repo,backend=self.backend)
    def job(self):
        path=Path(self.tmp.name)/'source.wav'; wav(path)
        data=path.read_bytes(); sha=hashlib.sha256(data).hexdigest(); self.runtime.storage.put_file(path,sha)
        return self.repo.submit('a','once',sha,sha,len(data),'input.wav','ru',self.cfg.backend_identity,backend_model='nova-3')[0]
    def test_parser_preserves_speakers_as_labels_and_punctuation(self):
        result=parse_deepgram(PAYLOAD)
        self.assertEqual(result['full_text'],'Привет мир.\nДа.')
        self.assertEqual([s['speaker'] for s in result['segments']],['speaker_0','speaker_1'])
        self.assertEqual(result['segments'][0]['start_ms'],100)
        self.assertEqual(result['segments'][1]['end_ms'],1200)
        self.assertIn('provider_speaker_labels_are_not_roles',result['warnings'])
        import copy
        bad=copy.deepcopy(PAYLOAD); bad['results']['channels'][0]['alternatives'][0]['words'][0]['speaker']='candidate'
        with self.assertRaises(BackendError): parse_deepgram(bad)
        bad=copy.deepcopy(PAYLOAD); bad['results']['channels'][0]['alternatives'][0]['transcript']='lost speech'
        with self.assertRaises(BackendError): parse_deepgram(bad)
    def test_configuration_is_explicit_and_identity_tracks_it(self):
        self.assertEqual(self.backend.configuration('ru')['model'],'nova-3')
        self.assertEqual(self.backend.configuration('ru')['diarize_model'],'v2')
        for field,value in [('deepgram_model','nova-2'),('deepgram_language','en'),('deepgram_diarize',False),('deepgram_diarize_model','v3'),('deepgram_config_revision','2')]:
            self.assertNotEqual(replace(self.cfg,**{field:value}).backend_identity,self.cfg.backend_identity)
        self.assertNotIn('synthetic-secret',repr(self.cfg))
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
    def test_raw_saved_before_parse_failure_retry_uses_saved_response(self):
        job=self.job(); calls=[]
        async def submit(*_): calls.append('POST'); return BODY
        with patch.object(self.backend,'submit',submit),patch('md_transcription.deepgram.parse_deepgram',side_effect=BackendError('fixture_parse_failure')):
            self.assertEqual(self.runtime.run_once(job['id'])['status'],'failed')
        saved=Repository(self.cfg.database_url).get(job['id'])
        self.assertEqual(saved['provider_state'],'response_saved')
        self.assertEqual(saved['provider_raw']['events'],[PAYLOAD])
        self.assertEqual(saved['provider_metadata']['provider_model_info'],PAYLOAD['metadata']['model_info'])
        self.assertTrue(self.repo.retry(job['id'],'a'))
        with patch.object(self.backend,'submit',submit): self.assertEqual(self.runtime.run_once(job['id'])['status'],'succeeded')
        self.assertEqual(calls,['POST'])
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
    def test_unknown_post_outcome_stays_locked(self):
        job=self.job(); calls=[]
        async def submit(*_): calls.append('POST'); raise BackendError('deepgram_submit_uncertain',uncertain=True)
        with patch.object(self.backend,'submit',submit):
            self.assertEqual(self.runtime.run_once(job['id'])['status'],'uncertain')
            self.assertFalse(self.repo.retry(job['id'],'a'))
            self.assertEqual(self.runtime.run_once(job['id'])['status'],'skipped')
        self.assertEqual(calls,['POST'])
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
    def test_invalid_json_constants_are_saved_before_rejection(self):
        job=self.job()
        async def submit(*_): return b'{"metadata":{"value":NaN},"results":{}}'
        with patch.object(self.backend,'submit',submit): self.assertEqual(self.runtime.run_once(job['id'])['status'],'failed')
        raw=self.repo.get(job['id'])['provider_raw']
        self.assertEqual(raw['events'],[])
        import base64
        self.assertIn(b'NaN',base64.b64decode(raw['unparsed_response_base64']))
        self.assertTrue(self.repo.retry(job['id'],'a'))
        with patch.object(self.backend,'submit',side_effect=AssertionError('must not POST')):
            self.assertEqual(self.runtime.run_once(job['id'])['status'],'failed')

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
    def test_corrupted_stored_input_never_calls_provider(self):
        job=self.job(); self.runtime.storage.path(job['input_key']).write_bytes(b'corrupt')
        with patch.object(self.backend,'submit',side_effect=AssertionError('must not POST')):
            self.assertEqual(self.runtime.run_once(job['id'])['status'],'failed')
        self.assertEqual(self.repo.get(job['id'])['error_code'],'input_integrity_failed')

    def test_late_response_checkpoint_after_expired_lease(self):
        job=self.job(); claimed=self.repo.claim(job['id'],60,3); token=claimed['lease_token']
        self.repo.prepare_provider(job['id'],token,None,{})
        self.repo.begin_provider_submit(job['id'],token)
        with self.repo.engine.begin() as conn: conn.execute(update(jobs).where(jobs.c.id==job['id']).values(lease_until=time.time()-1))
        self.repo.due(); self.assertEqual(self.repo.get(job['id'])['status'],'uncertain')
        self.assertFalse(self.repo.retry(job['id'],'a'))
        self.assertTrue(self.repo.record_provider_response(job['id'],token,{'events':[PAYLOAD]},{}))
        self.assertEqual(self.repo.get(job['id'])['status'],'queued')
        resumed=self.repo.claim(job['id'],60,3)
        self.assertIsNotNone(resumed)
        self.assertFalse(self.repo.succeed(job['id'],token,{}))

class DeepgramTransportTests(unittest.TestCase):
    def fixture(self):
        observed=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def do_POST(self):
                observed.append({'path':self.path,'body':self.rfile.read(int(self.headers['Content-Length']))})
                self.send_response(200); self.send_header('Content-Length',str(len(BODY))); self.end_headers(); self.wfile.write(BODY)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}/v1/listen',observed
    def test_binary_request_explicit_query_and_original_response(self):
        from urllib.parse import parse_qs,urlsplit
        url,observed=self.fixture()
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'audio.wav'; wav(path)
            cfg=Settings(data_dir=Path(temp),backend_kind='deepgram',deepgram_api_key='fake')
            backend=DeepgramBackend(cfg)
            with patch('md_transcription.deepgram.LISTEN',url): body=asyncio.run(backend.submit(path,backend.configuration('ru')))
            self.assertEqual(body,BODY); self.assertEqual(observed[0]['body'],path.read_bytes())
            query=parse_qs(urlsplit(observed[0]['path']).query)
            self.assertEqual(query,{'model':['nova-3'],'language':['ru'],'diarize_model':['v2'],'smart_format':['false'],
                'punctuate':['true'],'utterances':['true'],'numerals':['false'],'profanity_filter':['false']})
    def test_transport_timeout_rate_limit_and_response_bound(self):
        from unittest.mock import MagicMock
        class Response:
            status=200
            body=BODY
            async def __aenter__(self): return self
            async def __aexit__(self,*_): pass
            @property
            def content(self): return self
            async def iter_chunked(self,_): yield self.body
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'audio.wav'; wav(path)
            cfg=Settings(data_dir=Path(temp),backend_kind='deepgram',deepgram_api_key='not-logged')
            backend=DeepgramBackend(cfg)
            for status,uncertain in ((429,False),(401,False),(503,True),(302,True)):
                response=Response(); response.status=status
                with patch('md_transcription.deepgram.aiohttp.ClientSession') as factory:
                    factory.return_value.__aenter__.return_value.post=MagicMock(return_value=response)
                    with self.assertRaises(BackendError) as err: asyncio.run(backend.submit(path,backend.configuration('ru')))
                    self.assertEqual(err.exception.uncertain,uncertain)
                    self.assertFalse(err.exception.retryable)
                    self.assertNotIn('not-logged',str(err.exception))
            with patch('md_transcription.deepgram.aiohttp.ClientSession') as factory:
                session=factory.return_value.__aenter__.return_value
                session.post=MagicMock(side_effect=asyncio.TimeoutError())
                with self.assertRaises(BackendError) as err: asyncio.run(backend.submit(path,backend.configuration('ru')))
                self.assertTrue(err.exception.uncertain)
                self.assertFalse(factory.call_args.kwargs['trust_env'])
                self.assertFalse(session.post.call_args.kwargs['allow_redirects'])
            limited=DeepgramBackend(replace(cfg,max_result_bytes=1))
            with patch('md_transcription.deepgram.aiohttp.ClientSession') as factory:
                factory.return_value.__aenter__.return_value.post=MagicMock(return_value=Response())
                with self.assertRaises(BackendError) as err: asyncio.run(limited.submit(path,limited.configuration('ru')))
                self.assertEqual(err.exception.code,'deepgram_response_too_large')
                self.assertTrue(err.exception.uncertain)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
    def test_kill_after_raw_save_fresh_worker_does_not_post(self):
        url,observed=self.fixture()
        with tempfile.TemporaryDirectory() as temp:
            cfg=Settings(data_dir=Path(temp),backend_kind='deepgram',deepgram_api_key='fake')
            runtime=Runtime(cfg); source=Path(temp)/'source.wav'; wav(source)
            data=source.read_bytes(); sha=hashlib.sha256(data).hexdigest(); runtime.storage.put_file(source,sha)
            job=runtime.repository.submit('a','kill',sha,sha,len(data),'input.wav','ru',cfg.backend_identity,backend_model='nova-3')[0]
            script="""
import sys,time
from pathlib import Path
from md_transcription.settings import Settings
import md_transcription.deepgram as protocol
from md_transcription.worker import Runtime
cfg=Settings(data_dir=Path(sys.argv[1]),backend_kind='deepgram',deepgram_api_key='fake')
protocol.LISTEN=sys.argv[2]
if sys.argv[4]=='pause':
    def wait(payload): time.sleep(60)
    protocol.parse_deepgram=wait
Runtime(cfg).run_once(sys.argv[3])
"""
            command=[sys.executable,'-c',script,temp,url,job['id']]
            child=subprocess.Popen([*command,'pause'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+10
                while time.monotonic()<deadline and runtime.repository.get(job['id'])['provider_raw'] is None:
                    if child.poll() is not None: self.fail(child.stderr.read().decode()[-2000:])
                    time.sleep(.03)
                self.assertEqual(runtime.repository.get(job['id'])['provider_raw']['events'],[PAYLOAD])
            finally: child.kill(); child.wait(timeout=3); child.stderr.close()
            with runtime.repository.engine.begin() as conn: conn.execute(update(jobs).where(jobs.c.id==job['id']).values(lease_until=time.time()-1))
            completed=subprocess.run([*command,'resume'],capture_output=True,timeout=10)
            self.assertEqual(completed.returncode,0,completed.stderr[-2000:].decode())
            self.assertEqual(runtime.repository.get(job['id'])['status'],'succeeded')
            self.assertEqual(len(observed),1)

if __name__=='__main__': unittest.main()
