"""Synthetic-only async SpeechKit acceptance. No provider credentials/calls."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from sqlalchemy import update, text
from md_transcription.audio import prepare_audio
from md_transcription.backends import BackendError
from md_transcription.settings import Settings
from md_transcription.repository import Repository, jobs
from md_transcription.speechkit import SpeechKitBackend, parse_recognition
from md_transcription.worker import Runtime

EVENTS=[{'result':{'audioCursors':{'finalIndex':'0'},'final':{'channelTag':'0','alternatives':[
    {'text':'Synthetic speech','words':[{'text':'Synthetic','startTimeMs':'10','endTimeMs':'200'},
      {'text':'speech','startTimeMs':'200','endTimeMs':'500'}]}]}}},
    {'result':{'finalRefinement':{'finalIndex':'0','normalizedText':{'channelTag':'0',
      'alternatives':[{'text':'Synthetic speech.','startTimeMs':'10','endTimeMs':'500'}]}}}}]
BODY=b'\n'.join(json.dumps(e).encode() for e in EVENTS)

def wav(path,seconds=.1,channels=1):
    with wave.open(str(path),'wb') as out:
        out.setparams((channels,2,16000,0,'NONE','not compressed'))
        out.writeframes(b'\0\0'*round(16000*seconds)*channels)

class Stage:
    def __init__(self): self.uploads=[]; self.deletes=[]
    def upload(self,path,key): self.uploads.append(key); return 'https://storage.yandexcloud.net/private/'+key
    def delete(self,key): self.deletes.append(key)

class SpeechKitTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.cfg=Settings(data_dir=Path(self.tmp.name),backend_kind='yandex_speechkit',
            speechkit_api_key='fixture-not-a-real-key',staging_s3_bucket='private',api_keys={'a':'a'*24,'b':'b'*24})
        self.repo=Repository(self.cfg.database_url); self.repo.init()
        self.runtime=Runtime(self.cfg,repository=self.repo,backend=SpeechKitBackend(self.cfg,Stage()))
    def job(self):
        path=Path(self.tmp.name)/'input.wav'; wav(path)
        data=path.read_bytes(); sha=hashlib.sha256(data).hexdigest()
        self.runtime.storage.put_file(path,sha)
        return self.repo.submit('a','once',sha,sha,len(data),'input.wav','ru',self.cfg.backend_identity,backend_model='general')[0]
    def expire(self,job_id):
        with self.repo.engine.begin() as conn: conn.execute(update(jobs).where(jobs.c.id==job_id).values(lease_until=time.time()-1,available_at=0))
    def available(self,job_id):
        with self.repo.engine.begin() as conn: conn.execute(update(jobs).where(jobs.c.id==job_id).values(available_at=0))
    def test_parser_refinement_channel_is_not_role(self):
        result,events,channels=parse_recognition(BODY)
        self.assertEqual(result['full_text'],'Synthetic speech.')
        self.assertEqual(result['segments'],[{'text':'Synthetic speech.','start_ms':10,'end_ms':500,'speaker':None}])
        self.assertEqual(events,EVENTS); self.assertEqual(channels,['0'])
        for bad in (b'{}', b'{"result": null}', b'[]', b'invalid', BODY+BODY):
            with self.assertRaises(BackendError): parse_recognition(bad)
    def test_stream_error_cannot_publish_partial_success_and_warning_is_bounded(self):
        for event in ({'error':{'message':'private provider message'}},
                      {'result':{'error':{'message':'private provider message'}}},
                      {'result':{'statusCode':{'codeType':'ERROR','message':'private provider message'}}}):
            with self.assertRaises(BackendError):
                parse_recognition(BODY+b'\n'+json.dumps(event).encode())
        result,_,_=parse_recognition(BODY+b'\n'+b'{"result":{"statusCode":{"codeType":"WARNING","message":"private provider message"}}}')
        self.assertIn('speechkit_provider_warning',result['warnings'])
        self.assertNotIn('private provider message',str(result))

    def test_outer_snake_channel_and_refinement(self):
        import copy
        events=copy.deepcopy(EVENTS)
        events[0]['result']['channel_tag']='voice-1'
        del events[0]['result']['final']['channelTag']
        events[1]['result']['channel_tag']='voice-1'
        del events[1]['result']['finalRefinement']['normalizedText']['channelTag']
        transcript,_,channels=parse_recognition(b'\n'.join(json.dumps(event).encode() for event in events))
        self.assertEqual(channels,['voice-1'])
        self.assertIsNone(transcript['segments'][0]['speaker'])
        self.assertEqual(transcript['full_text'],'Synthetic speech.')

    def test_nonzero_source_start_is_observation_not_assumed_alignment(self):
        from md_transcription.audio import _observed_start_ms
        self.assertEqual(_observed_start_ms('12.5'),12500)
        self.assertIsNone(_observed_start_ms(None))

    def test_config_identity_and_secret_repr(self):
        base=self.cfg.backend_identity
        from dataclasses import replace
        for key,value in [('speechkit_model','general:rc'),('speechkit_language','en-US'),
                          ('speechkit_speaker_labeling',True),('speechkit_config_revision','2')]:
            self.assertNotEqual(replace(self.cfg,**{key:value}).backend_identity,base)
        self.assertNotIn('fixture-not-a-real-key',repr(self.cfg))
        self.assertEqual(SpeechKitBackend(self.cfg).configuration('ru')['language'],'ru-RU')
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
    def test_real_decode_and_corrupt_file(self):
        source=Path(self.tmp.name)/'source.wav'; wav(source,channels=2)
        with prepare_audio(source,self.cfg) as (audio,provenance):
            self.assertEqual(provenance['channels'],1)
            self.assertEqual(provenance['input_channels'],2)
            self.assertIsNone(provenance['timing_offset_ms'])
            self.assertEqual(provenance['timing_alignment_to_original_media'],'not_verified')
            self.assertEqual(provenance['duration_ms'],100)
            converted=audio
        self.assertFalse(converted.exists())
        source.write_bytes(b'corrupt')
        with self.assertRaises(BackendError):
            with prepare_audio(source,self.cfg): pass
    def test_duration_limit_before_decode_and_safe_commands(self):
        from md_transcription.audio import _run
        source=Path(self.tmp.name)/'local'; source.write_bytes(b'x')
        probe=json.dumps({'streams':[{'codec_type':'audio','channels':1}],'format':{'duration':'14401'}}).encode()
        with patch('md_transcription.audio._run',return_value=probe) as run:
            with self.assertRaises(BackendError) as err:
                with prepare_audio(source,self.cfg): pass
            self.assertEqual(err.exception.code,'media_duration_exceeded')
            args=run.call_args.args[0]
            self.assertEqual(args[args.index('-protocol_whitelist')+1],'file')
            self.assertEqual(run.call_count,1)
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
    def test_restart_pending_and_poll_timeout_never_resubmits(self):
        backend=self.runtime.backend; job=self.job(); calls=[]
        async def submit(*args): calls.append('POST'); return 'operation-1'
        async def pending(*args): calls.append('GET'); raise BackendError('speechkit_poll_temporary',retryable=True)
        with patch.object(backend,'submit',submit),patch.object(backend,'fetch',pending):
            self.assertEqual(self.runtime.run_once(job['id'])['status'],'queued')
        stored=Repository(self.cfg.database_url); stored.init()
        self.assertEqual(stored.get(job['id'])['provider_operation_id'],'operation-1')
        self.available(job['id'])
        async def done(*args): calls.append('GET'); return BODY
        with patch.object(backend,'submit',submit),patch.object(backend,'fetch',done):
            self.assertEqual(self.runtime.run_once(job['id'])['status'],'succeeded')
        self.assertEqual(calls,['POST','GET','GET'])
        persisted=stored.get(job['id'])
        self.assertEqual(persisted['provider_raw']['events'],EVENTS)
        self.assertEqual(persisted['provider_raw']['input_sha256'],job['input_sha256'])
        self.assertEqual(persisted['provider_raw']['provenance']['duration_ms'],100)
        self.assertEqual(len(backend.staging.deletes),1)
    def test_submit_intent_crash_and_late_id_checkpoint(self):
        job=self.job(); claimed=self.repo.claim(job['id'],60,3); token=claimed['lease_token']
        self.assertTrue(self.repo.prepare_provider(job['id'],token,'speechkit-staging/key',{'configuration':{}}))
        self.assertTrue(self.repo.begin_provider_submit(job['id'],token))
        self.expire(job['id']); self.assertEqual(self.repo.due(),[])
        self.assertEqual(self.repo.get(job['id'])['status'],'uncertain')
        self.assertFalse(self.repo.retry(job['id'],'a'))
        self.assertTrue(self.repo.record_provider_operation(job['id'],token,'late-operation'))
        resumed=self.repo.claim(job['id'],60,3)
        self.assertIsNotNone(resumed)
        self.assertNotEqual(resumed['lease_token'],token)
        self.assertFalse(self.repo.succeed(job['id'],token,{}))
        self.assertEqual(resumed['provider_operation_id'],'late-operation')
    def test_known_operation_expired_lease_resumes_even_after_attempt_budget(self):
        job=self.job(); claimed=self.repo.claim(job['id'],60,1); token=claimed['lease_token']
        self.repo.prepare_provider(job['id'],token,'speechkit-staging/key',{})
        self.repo.begin_provider_submit(job['id'],token)
        self.repo.record_provider_operation(job['id'],token,'known')
        self.expire(job['id'])
        self.assertIn(job['id'],self.repo.due())
        resumed=self.repo.claim(job['id'],60,1)
        self.assertEqual(resumed['provider_operation_id'],'known')
        self.assertFalse(self.repo.succeed(job['id'],token,{}))
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
    def test_unknown_post_outcome_no_retry(self):
        backend=self.runtime.backend; job=self.job(); calls=[]
        async def timeout(*args): calls.append('POST'); raise BackendError('speechkit_submit_uncertain',uncertain=True)
        with patch.object(backend,'submit',timeout):
            self.assertEqual(self.runtime.run_once(job['id'])['status'],'uncertain')
            self.assertEqual(self.runtime.run_once(job['id'])['status'],'skipped')
            self.assertFalse(self.repo.retry(job['id'],'a'))
        self.assertEqual(calls,['POST']); self.assertEqual(backend.staging.deletes,[])
    def test_concurrent_schema_initialization(self):
        from concurrent.futures import ThreadPoolExecutor
        database='sqlite:///'+str(Path(self.tmp.name)/'simultaneous.sqlite')
        def initialize(_):
            repository=Repository(database); repository.init(); return repository.worker_readiness()
        with ThreadPoolExecutor(max_workers=5) as pool:
            results=list(pool.map(initialize,range(5)))
        self.assertEqual(len(results),5)

    def test_existing_schema_additive_migration(self):
        old=Path(self.tmp.name)/'old.sqlite'
        import sqlite3
        with sqlite3.connect(old) as conn: conn.execute('CREATE TABLE jobs (id VARCHAR PRIMARY KEY)')
        repository=Repository('sqlite:///'+str(old)); repository.init(); repository.init()
        with repository.engine.connect() as conn:
            names={row[1] for row in conn.execute(text('PRAGMA table_info(jobs)'))}
        self.assertIn('provider_operation_id',names)
    def test_raw_result_authorization_and_health(self):
        from md_transcription.api import create_app
        job=self.job(); claimed=self.repo.claim(job['id'],60,3)
        transcript,events,_=parse_recognition(BODY)
        self.repo.succeed(job['id'],claimed['lease_token'],transcript,{'provider':'yandex_speechkit','events':events},{'configuration':{}})
        import socket
        import requests
        import uvicorn
        sock=socket.socket(); sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(create_app(self.cfg,self.runtime),log_level='critical',lifespan='off',access_log=False))
        thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True); thread.start()
        deadline=time.monotonic()+5
        while not server.started and time.monotonic()<deadline: time.sleep(.01)
        self.assertTrue(server.started)
        self.addCleanup(lambda:setattr(server,'should_exit',True))
        client=requests.Session(); client.trust_env=False; self.addCleanup(client.close)
        real_get=client.get
        client.get=lambda url,**kwargs:real_get(f'http://127.0.0.1:{port}'+url,timeout=3,**kwargs)
        url=f"/v1/jobs/{job['id']}/result/raw"
        self.assertEqual(client.get(url).status_code,401)
        self.assertEqual(client.get(url,headers={'Authorization':'Bearer '+'b'*24}).status_code,404)
        headers={'Authorization':'Bearer '+'a'*24}
        self.assertEqual(client.get(url,headers=headers).json(),{'provider':'yandex_speechkit','events':EVENTS})
        self.assertEqual(client.get(f"/v1/jobs/{job['id']}/result",headers=headers).json()['provider'],'yandex_speechkit')
        health=client.get('/health/ready',headers=headers).json()
        self.assertEqual(health['worker']['status'],'not_observed')
        self.repo.touch_worker()
        self.assertEqual(client.get('/health/ready',headers=headers).json()['worker']['status'],'observed')

class TransportTests(unittest.TestCase):
    def test_actual_aiohttp_async_rest_and_redacted_errors(self):
        observed=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def do_POST(self):
                observed.append(('POST',self.path,json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.respond(b'{"id":"fixture-operation","done":false}')
            def do_GET(self):
                observed.append(('GET',self.path))
                if self.path.startswith('/operations/'):
                    self.respond(b'{"id":"fixture-operation","done":true}')
                else: self.respond(BODY)
            def respond(self,body):
                self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as temp:
            cfg=Settings(data_dir=Path(temp),backend_kind='yandex_speechkit',speechkit_api_key='fake',staging_s3_bucket='private')
            backend=SpeechKitBackend(cfg,Stage())
            base=f'http://127.0.0.1:{server.server_port}'
            with patch('md_transcription.speechkit.STT',base+'/stt/v3'),patch('md_transcription.speechkit.OPERATIONS',base+'/operations'):
                op=asyncio.run(backend.submit('https://storage.yandexcloud.net/private/audio.wav',backend.configuration('ru')))
                body=asyncio.run(backend.fetch(op))
            self.assertEqual(body,BODY)
            body=observed[0][2]
            self.assertEqual(body['speakerLabeling']['speakerLabeling'],'SPEAKER_LABELING_DISABLED')
            self.assertFalse(body['recognitionModel']['textNormalization']['literatureText'])
            self.assertEqual(observed[1][1],'/operations/fixture-operation')
            self.assertIn('operationId=fixture-operation',observed[2][1])

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
    def test_killed_worker_resumes_checkpoint_in_fresh_process(self):
        calls=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length'])); calls.append('POST')
                self.respond(b'{"id":"restart-operation","done":false}')
            def do_GET(self):
                calls.append('GET')
                self.respond(b'{"id":"restart-operation","done":true}' if self.path.startswith('/operations/') else BODY)
            def respond(self,body):
                self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as temp:
            cfg=Settings(data_dir=Path(temp),backend_kind='yandex_speechkit',speechkit_api_key='fake',staging_s3_bucket='private')
            repository=Repository(cfg.database_url); repository.init()
            source=Path(temp)/'original.wav'; wav(source)
            data=source.read_bytes(); sha=hashlib.sha256(data).hexdigest()
            runtime=Runtime(cfg,repository=repository,backend=SpeechKitBackend(cfg,Stage()))
            runtime.storage.put_file(source,sha)
            job=repository.submit('a','kill-resume',sha,sha,len(data),'input.wav','ru',cfg.backend_identity,backend_model='general')[0]
            script="""
import asyncio,sys
from pathlib import Path
from md_transcription.settings import Settings
from md_transcription.speechkit import SpeechKitBackend
import md_transcription.speechkit as protocol
from md_transcription.worker import Runtime
class Stage:
    def upload(self,path,key): return 'https://storage.yandexcloud.net/private/'+key
    def delete(self,key): pass
cfg=Settings(data_dir=Path(sys.argv[1]),backend_kind='yandex_speechkit',speechkit_api_key='fake',staging_s3_bucket='private')
protocol.STT=sys.argv[2]+'/stt/v3'; protocol.OPERATIONS=sys.argv[2]+'/operations'
backend=SpeechKitBackend(cfg,Stage())
if sys.argv[4]=='pause':
    async def wait(operation): await asyncio.sleep(60)
    backend.fetch=wait
Runtime(cfg,backend=backend).run_once(sys.argv[3])
"""
            cmd=[sys.executable,'-c',script,temp,f'http://127.0.0.1:{server.server_port}',job['id']]
            child=subprocess.Popen([*cmd,'pause'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+10
                while time.monotonic()<deadline and repository.get(job['id'])['provider_operation_id'] is None:
                    if child.poll() is not None: self.fail(child.stderr.read().decode()[-2000:])
                    time.sleep(.03)
                self.assertEqual(repository.get(job['id'])['provider_operation_id'],'restart-operation')
            finally:
                child.kill(); child.wait(timeout=3); child.stderr.close()
            with repository.engine.begin() as conn:
                conn.execute(update(jobs).where(jobs.c.id==job['id']).values(lease_until=time.time()-1))
            finished=subprocess.run([*cmd,'resume'],capture_output=True,timeout=10)
            self.assertEqual(finished.returncode,0,finished.stderr[-2000:].decode())
            self.assertEqual(repository.get(job['id'])['status'],'succeeded')
            self.assertEqual(calls.count('POST'),1)
            self.assertEqual(repository.get(job['id'])['provider_raw']['events'],EVENTS)

if __name__=='__main__': unittest.main()
