"""Source integration without Daily/HR DB: real HTTP core+normalizer, private SQLite."""
import copy
from dataclasses import replace
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from fastapi.testclient import TestClient
from source_daily.api import create_app
from source_daily.clients import CoreClient,TranscriptionClient
from source_daily.config import Settings
from source_daily.errors import LeaseLost,ServiceError
from source_daily.pipeline import Pipeline
from source_daily.store import Store,encode
from source_daily._vendor.daily_media import DailyMedia,ProviderError
from source_daily._vendor.materials_capture import Artifact,Discovery,HttpSource,Source
from source_daily._vendor.materials_daily import DailyCapture
from source_daily._vendor.materials_http import DownloadError,DownloadResult
from source_daily._vendor.materials_storage import StoredObject
from source_daily._vendor.support import Problem

TOKEN='portal-token-0123456789'
CANONICAL={'schema_version':1,'format':'vtt','segments':[{'start_ms':None,'end_ms':None,'text':'Actual speech','speaker':None}],
           'full_text':'Actual speech','text':'Actual speech','vtt':'WEBVTT\n\nActual speech','warnings':['missing_speakers']}
BINDING={'binding_id':'binding-1','generation':1,'provider':'daily','connection_id':'daily',
         'source_ref':{'room_id':'room-1','room_name':'hrv-owner','session_id':'session-1','capture_instance_id':'capture-1'},
         'storage_policy':'remote','owner_kind':'interview','owner_id':'owner'}


class RemoteHTTP:
    def __init__(self):
        self.binding=copy.deepcopy(BINDING)
        self.receipts={}
        self.calls=[]
        self.fail_receipts=False
        self.normalize_status=200
        self.asr_status='queued'
        self.upload_keys=[]
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def respond(self,status,value):
                raw=json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            def do_GET(self):
                owner.calls.append(('GET',self.path))
                if self.path=='/v1/bindings/binding-1': return self.respond(200,owner.binding)
                if self.path=='/v1/jobs/asr-1': return self.respond(200,{'id':'asr-1','status':owner.asr_status})
                if self.path=='/v1/jobs/asr-1/result': return self.respond(200,{'job_id':'asr-1','input_sha256':hashlib.sha256(b'actual-video-bytes').hexdigest(),'processing_status':'completed','coverage':'not_independently_verified','model':'operator-selected','language':None,'transcript':CANONICAL})
                return self.respond(404,{'error':'not_found'})
            def do_POST(self):
                data=self.rfile.read(int(self.headers.get('Content-Length','0')))
                owner.calls.append(('POST',self.path))
                if self.path.endswith('/receipts'):
                    if owner.fail_receipts: return self.respond(503,{'error':'unavailable'})
                    body=json.loads(data)
                    previous=owner.receipts.get(body['receipt_id'])
                    if previous is not None and previous!=body: return self.respond(409,{'error':'conflict'})
                    owner.receipts[body['receipt_id']]=body
                    return self.respond(200,{'accepted':True})
                if self.path.startswith('/v1/normalize?'):
                    return self.respond(owner.normalize_status,CANONICAL if owner.normalize_status==200 else {'error':'invalid_transcript'})
                if self.path=='/v1/jobs':
                    owner.upload_keys.append((self.headers.get('Idempotency-Key'),data))
                    return self.respond(202,{'id':'asr-1','status':owner.asr_status,'input_sha256':hashlib.sha256(data).hexdigest()})
                return self.respond(404,{'error':'not_found'})
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()


class OwnedStorage:
    backend='s3'
    def __init__(self): self.blobs={}; self.puts=0
    def put_file(self,path,**kw):
        data=Path(path).read_bytes(); digest=hashlib.sha256(data).hexdigest()
        assert kw['expected_sha256']==digest and kw['expected_size']==len(data)
        key='materials/sha256/'+digest[:2]+'/'+digest
        self.blobs[key]=data; self.puts+=1
        return StoredObject(key,digest,len(data),kw['content_type'],'s3')
    def read_bytes(self,key,max_bytes):
        data=self.blobs[key]
        if len(data)>max_bytes: raise ValueError()
        return data


class Capture:
    def __init__(self,transcript=False):
        self.source=Source('daily','daily','room-1','hrv-owner','session-1','capture-1')
        self.artifacts=[Artifact(self.source,'recording-1','video','ready',{'duration':7})]
        if transcript: self.artifacts.append(Artifact(self.source,'text-1','transcript','ready'))
        self.resolved=[]; self.error=None
    def discover(self,ref):
        assert ref['provider']=='daily' and ref['connection_id']=='daily'
        return Discovery(self.artifacts,{'video':{'status':'ready','complete':True},
            'transcript':{'status':'ready' if len(self.artifacts)>1 else 'absent','complete':True}},True)
    def resolve(self,artifact):
        self.resolved.append(artifact.provider_asset_id)
        if self.error: raise self.error
        return HttpSource('https://storage.example.com/'+artifact.provider_asset_id+'?secret=NEVER-PERSIST',expires_at=4102444800)


class Media:
    configured=True
    def __init__(self): self.calls=[]; self.fail_recording=False
    def room(self,meeting_id,**options): return {'id':'room-1','name':'hrv-'+meeting_id,'url':'https://test.daily.co/hrv-'+meeting_id}
    def request(self,path,body=None):
        self.calls.append((path,body))
        if self.fail_recording and path.endswith('/recordings/start'): raise ProviderError(403)
        return {'token':'provider-token'} if path=='/meeting-tokens' else {'ok':True}
    def finish(self,room): self.calls.append(('finish',room))
    def materials(self,*a,**kw): return {'recordings':[],'transcripts':[]}


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.remote=RemoteHTTP(); self.clock=[1000.0]
        self.settings=Settings(db_path=self.tmp.name+'/private/jobs.sqlite3',temp_root=self.tmp.name+'/tmp',
            api_keys={TOKEN:'portal'},daily_key='private-provider-key',core_url=self.remote.url,core_token='core-token',
            transcription_url=self.remote.url,transcription_token='asr-token',settle_attempts=1,max_attempts=3,retry_seconds=1)
        self.store=Store(self.settings.db_path,clock=lambda:self.clock[0]); self.capture=Capture(); self.storage=OwnedStorage()
        self.downloads=[]
        def fetch(source,path,**kw):
            self.downloads.append(source.url)
            data=b'WEBVTT\n\n00:00.000 --> 00:01.000\nActual speech' if 'text-1' in source.url else b'actual-video-bytes'
            Path(path).write_bytes(data)
            return DownloadResult(path,len(data),hashlib.sha256(data).hexdigest(),'text/vtt' if 'text-1' in source.url else 'video/mp4')
        self.fetch=fetch
        self.pipeline=Pipeline(self.settings,self.store,CoreClient(self.remote.url,'core-token'),self.capture,
            TranscriptionClient(self.remote.url,'asr-token'),self.storage,downloader=fetch)
        self.media=Media()
        self.client=TestClient(create_app(self.settings,self.pipeline,self.media,publish=lambda _: (_ for _ in ()).throw(RuntimeError('broker secret'))))
        self.headers={'Authorization':'Bearer '+TOKEN}
    def tearDown(self): self.client.close(); self.remote.close(); self.tmp.cleanup()
    def run_job(self):
        job=self.pipeline.enqueue('binding-1',1); self.pipeline.run(job['id']); return self.store.get(job['id'])
    def resume(self,job):
        self.clock[0]=max(self.clock[0]+2,job['next_run']+1)
        self.pipeline.run(job['id'])
        return self.store.get(job['id'])
    def receipt(self): return list(self.remote.receipts.values())[-1]
    def test_remote_video_survives_unconfigured_asr_without_download(self):
        job=self.run_job()
        self.assertEqual(job['status'],'succeeded'); self.assertEqual(self.downloads,[])
        self.assertEqual(self.receipt()['video_status'],'ready')
        self.assertTrue(self.receipt()['acquisition_complete'])
        self.assertEqual(self.receipt()['transcription_status'],'awaiting_configuration')
        self.assertEqual(self.receipt()['assets'][0]['locations'][0]['backend'],'provider')
    def test_owned_video_verified_before_receipt(self):
        self.remote.binding['storage_policy']='owned'
        self.run_job()
        self.assertEqual(len(self.downloads),1)
        asset=self.receipt()['assets'][0]
        self.assertEqual(asset['locations'][0]['backend'],'s3')
        self.assertEqual(asset['locations'][0]['state'],'verified')
        self.assertEqual(asset['locations'][0]['sha256'],hashlib.sha256(b'actual-video-bytes').hexdigest())
    def test_provider_transcript_uses_http_normalizer_and_owned_raw(self):
        self.capture=Capture(True); self.pipeline.capture=self.capture
        self.run_job()
        transcript=next(a for a in self.receipt()['assets'] if a['kind']=='transcript')
        self.assertEqual(transcript['transcript']['normalized'],CANONICAL)
        self.assertEqual(transcript['transcript']['provenance']['origin'],'daily_provider')
        self.assertIn(transcript['transcript']['raw_ref']['object_key'],self.storage.blobs)
        self.assertIn(('POST','/v1/normalize?format=vtt'),self.remote.calls)
        self.assertEqual(self.receipt()['transcription_status'],'not_requested')
    def test_missing_normalizer_keeps_acquisition_and_video_independent(self):
        self.pipeline.capture=Capture(True)
        self.pipeline.transcription=TranscriptionClient('','')
        self.run_job()
        self.assertTrue(self.receipt()['acquisition_complete'])
        self.assertEqual(self.receipt()['video_status'],'ready')
        self.assertEqual(self.receipt()['transcription_status'],'awaiting_configuration')
        self.assertEqual(self.storage.puts,1)
    def test_invalid_normalization_does_not_create_empty_success(self):
        self.pipeline.capture=Capture(True); self.remote.normalize_status=422
        self.run_job()
        self.assertEqual(self.receipt()['transcript_status'],'error')
        self.assertEqual(self.receipt()['video_status'],'ready')
        self.assertFalse(any(a['kind']=='transcript' for a in self.receipt()['assets']))
    def test_provider_inaccessible_recording_failure_bounded(self):
        self.remote.binding['storage_policy']='owned'
        def denied(*a,**kw): raise DownloadError('https://private-secret',code='remote_status',remote_status=403,retryable=True)
        self.pipeline.downloader=denied
        job=self.run_job(); job=self.resume(job); job=self.resume(job)
        self.assertEqual(job['status'],'failed')
        self.assertEqual(job['checkpoint']['asset_errors'][self.capture.artifacts[0].asset_key],'daily_download_403')
        self.assertEqual(self.receipt()['video_status'],'error')
        self.assertFalse(self.receipt()['acquisition_complete'])
    def test_receipt_503_is_durable_then_same_body_replayed(self):
        self.remote.fail_receipts=True
        job=self.run_job(); pending=self.store.pending(job['id'])
        self.assertEqual(job['status'],'waiting'); self.assertEqual(len(pending),1)
        self.remote.fail_receipts=False; job=self.resume(job)
        self.assertEqual(job['status'],'succeeded'); self.assertEqual(self.store.pending(job['id']),[])
        self.assertEqual(self.remote.receipts[pending[0]['receipt_id']],pending[0])
    def test_crash_after_core_acceptance_replays_same_receipt(self):
        delivered=self.store.delivered
        with patch.object(self.store,'delivered',side_effect=SystemExit):
            with self.assertRaises(SystemExit): self.run_job()
        job=self.store.get(next(iter(self.store.due()),'missing'))
        with self.store.db() as db: job_id=db.execute('SELECT id FROM source_jobs').fetchone()[0]
        pending=self.store.pending(job_id); self.assertEqual(len(pending),1)
        self.clock[0]+=self.settings.lease_seconds+1
        self.pipeline.run(job_id)
        self.assertEqual(self.store.get(job_id)['status'],'succeeded')
        self.assertEqual(len(self.remote.receipts),1)
        self.assertEqual(self.remote.receipts[pending[0]['receipt_id']],pending[0])
    def test_expired_lease_cannot_mark_delivered(self):
        job=self.pipeline.enqueue('binding-1',1); claimed,token=self.store.claim(job['id'],60)
        self.clock[0]+=61
        self.assertRaises(LeaseLost,self.store.save,job['id'],token,{})
        self.assertRaises(LeaseLost,self.store.delivered,job['id'],token,'receipt')
        claimed,new=self.store.claim(job['id'],60)
        self.assertNotEqual(new,token)
    def test_generation_change_fences_old_worker_and_receipt(self):
        job=self.pipeline.enqueue('binding-1',1); claimed,token=self.store.claim(job['id'],600)
        self.remote.binding['generation']=2
        newer=self.pipeline.enqueue('binding-1',2)
        self.assertEqual(self.store.get(job['id'])['status'],'fenced')
        self.assertRaises(LeaseLost,self.store.save,job['id'],token,{})
        self.assertRaises(ServiceError,self.pipeline.enqueue,'binding-1',1)
        self.assertEqual(newer['generation'],2)
    def test_access_requires_binding_owned_evidence_and_reinspection(self):
        self.run_job()
        self.assertIn('secret=',self.pipeline.access('binding-1','recording-1')['url'])
        self.assertRaises(ServiceError,self.pipeline.access,'binding-other','recording-1')
        self.assertRaises(ServiceError,self.pipeline.access,'binding-1','not-discovered')
        self.remote.binding['source_ref']['session_id']='other-session'
        self.assertRaises(ServiceError,self.pipeline.access,'binding-1','recording-1')
    def test_no_urls_keys_or_owner_profile_in_private_db(self):
        self.run_job()
        with self.store.db() as db:
            blob=' '.join(str(tuple(row)) for table in ('source_jobs','source_artifacts','source_receipts') for row in db.execute('SELECT * FROM '+table))
        for secret in ('NEVER-PERSIST','https://','private-provider-key','core-token','asr-token','owner_kind'):
            self.assertNotIn(secret,blob)
        with self.store.db() as db:
            self.assertEqual({r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}, {'source_jobs','source_artifacts','source_receipts','source_webhooks'})
    def test_jobs_api_durable_before_failed_publish_and_idempotent(self):
        first=self.client.post('/v1/jobs',json={'binding_id':'binding-1','generation':1},headers=self.headers)
        second=self.client.post('/v1/jobs',json={'binding_id':'binding-1','generation':1},headers=self.headers)
        self.assertEqual(first.status_code,202); self.assertEqual(first.json()['id'],second.json()['id'])
        self.assertIn(first.json()['id'],self.store.due())
    def test_job_cannot_supply_url_candidate_or_policy(self):
        for extra in ({'source_url':'https://evil.test'},{'candidate_id':'owner'},{'storage_policy':'owned'}):
            response=self.client.post('/v1/jobs',json={'binding_id':'binding-1','generation':1,**extra},headers=self.headers)
            self.assertEqual(response.status_code,422)
            self.assertNotIn('evil',response.text)
    def test_typed_calls_token_whitelist_and_no_raw_forwarding(self):
        room={'name':'hrv-owner','id':'room-1'}
        properties={'user_name':'Recruiter','user_id':'r1','exp':4102444800}
        valid=self.client.post('/v1/calls/token',json={'room':room,'properties':properties},headers=self.headers)
        self.assertEqual(valid.status_code,200)
        for extra in ({'bad':'secret'},{'room_name':'hrv-other'},{'auto_start_transcription':True}):
            response=self.client.post('/v1/calls/token',json={'room':room,'properties':{**properties,**extra}},headers=self.headers)
            self.assertEqual(response.status_code,422)
        self.assertEqual(self.client.post('/v1/daily/request',json={'path':'/recordings'},headers=self.headers).status_code,404)
    def test_start_independent_ack_and_no_default_asr_or_daily_model(self):
        self.media.fail_recording=True
        result=self.client.post('/v1/calls/start',json={'room':{'name':'hrv-owner','id':'room-1'},'start_recording':True,'start_transcription':True},headers=self.headers)
        self.assertEqual(result.status_code,200)
        self.assertFalse(result.json()['recording_requested']); self.assertFalse(result.json()['transcription_requested'])
        self.assertEqual(result.json()['transcription_error'],'daily_transcription_not_configured')
        self.assertFalse(any(path.endswith('/transcription/start') for path,_ in self.media.calls))
    def test_auth_and_body_limits_and_legacy_off(self):
        self.assertEqual(self.client.get('/health/live').status_code,200)
        self.assertEqual(self.client.get('/health/ready').status_code,401)
        self.assertEqual(self.client.post('/v1/jobs',content=b'x'*(self.settings.max_body_bytes+1),headers=self.headers).status_code,413)
        self.assertEqual(self.client.post('/v1/calls/materials',json={'room':{'name':'hrv-owner','id':'room-1'}},headers=self.headers).status_code,404)
    def test_asr_upload_poll_result_and_no_model_selection(self):
        self.pipeline.settings=replace(self.settings,enable_asr=True)
        job=self.run_job()
        self.assertEqual(job['status'],'waiting'); self.assertEqual(self.receipt()['transcription_status'],'queued')
        self.remote.asr_status='succeeded'; job=self.resume(job)
        self.assertEqual(job['status'],'succeeded'); self.assertEqual(self.receipt()['transcription_status'],'succeeded')
        transcript=next(a for a in self.receipt()['assets'] if a['kind']=='transcript')
        self.assertEqual(transcript['transcript']['provenance']['model_version'],'operator-selected')
        self.assertEqual(len(self.remote.upload_keys),1)
        self.assertEqual(self.remote.upload_keys[0][1],b'actual-video-bytes')
        self.assertFalse(any('model=' in path for _,path in self.remote.calls))
    def test_asr_uncertain_never_auto_retried_and_video_stays_ready(self):
        self.pipeline.settings=replace(self.settings,enable_asr=True); self.remote.asr_status='uncertain'
        job=self.run_job()
        self.assertEqual(self.receipt()['transcription_status'],'failed'); self.assertEqual(self.receipt()['video_status'],'ready')
        self.assertFalse(any('/retry' in path for _,path in self.remote.calls))
    def test_asr_poll_limit_bounded(self):
        self.pipeline.settings=replace(self.settings,enable_asr=True,max_asr_polls=2)
        job=self.run_job(); job=self.resume(job)
        self.assertEqual(job['status'],'succeeded'); self.assertEqual(self.receipt()['transcription_status'],'failed')
    def test_raw_acquisition_not_repeated_after_normalizer_outage(self):
        self.pipeline.capture=Capture(True); self.remote.normalize_status=503
        job=self.run_job()
        self.assertEqual(self.storage.puts,1)
        self.assertEqual(self.receipt()['transcription_status'],'queued')
        self.remote.normalize_status=200; job=self.resume(job)
        self.assertEqual(job['status'],'succeeded'); self.assertEqual(self.storage.puts,1)

    def test_receipt_outage_exceeds_acquisition_budget_but_recovers(self):
        self.remote.fail_receipts=True
        job=self.run_job()
        for _ in range(6): job=self.resume(job)
        self.assertEqual(job['status'],'waiting')
        self.assertEqual(job['attempts'],1)
        self.assertEqual(len(self.store.pending(job['id'])),1)
        self.remote.fail_receipts=False; job=self.resume(job)
        self.assertEqual(job['status'],'succeeded')
        self.assertEqual(len(self.remote.receipts),1)

    def test_botocore_storage_errors_are_safe_and_bounded(self):
        from botocore.exceptions import ClientError,EndpointConnectionError
        for error in (ClientError({'Error':{'Code':'AccessDenied','Message':'https://secret-URL'}},'PutObject'),
                      EndpointConnectionError(endpoint_url='https://secret-URL')):
            with self.subTest(error=type(error).__name__):
                self.remote.binding['storage_policy']='owned'
                self.pipeline.storage.put_file=lambda *a,**kw: (_ for _ in ()).throw(error)
                job=self.pipeline.enqueue('binding-1',1)
                if job['status']=='failed': job=self.pipeline.resume(job['id'])
                self.pipeline.run(job['id']); job=self.store.get(job['id'])
                for _ in range(2): job=self.resume(job)
                self.assertEqual(job['status'],'failed')
                self.assertEqual(job['attempts'],3)
                self.assertNotIn('https://',encode(job['checkpoint']))

    def test_explicit_resume_and_reenqueue_after_asr_configuration(self):
        job=self.run_job()
        self.pipeline.settings=replace(self.settings,enable_asr=True)
        revived=self.pipeline.enqueue('binding-1',1)
        self.assertEqual(revived['id'],job['id']); self.assertEqual(revived['status'],'queued')
        self.pipeline.run(job['id'])
        self.assertEqual(len(self.remote.upload_keys),1)
        self.remote.asr_status='succeeded'
        job=self.store.get(job['id']); job=self.resume(job)
        result=self.client.post('/v1/jobs/'+job['id']+'/resume',headers=self.headers)
        self.assertEqual(result.status_code,202)
        self.pipeline.run(job['id'])
        self.assertEqual(len(self.remote.upload_keys),1)

    def test_provider_text_covers_only_its_session_not_another_recording(self):
        self.pipeline.settings=replace(self.settings,enable_asr=True)
        self.pipeline.capture=Capture(True)
        second=Artifact(replace(self.capture.source,session_id='session-2'),'recording-2','video','ready')
        self.pipeline.capture.artifacts.append(second)
        self.remote.binding['source_ref'].pop('session_id')
        self.remote.binding['source_ref']['exclusive_room_owner']={'kind':'interview','id':'owner'}
        job=self.run_job()
        self.assertEqual(len(self.remote.upload_keys),1)
        self.assertEqual(self.receipt()['video_status'],'ready')
        self.assertEqual(self.receipt()['transcript_status'],'ready')
        self.assertEqual(self.receipt()['transcription_status'],'queued')

    def test_retention_upgrade_preserves_uncertain_asr_no_second_upload(self):
        self.pipeline.settings=replace(self.settings,enable_asr=True)
        self.remote.asr_status='uncertain'
        self.run_job()
        self.remote.binding['generation']=2; self.remote.binding['storage_policy']='owned'
        upgraded=self.pipeline.enqueue('binding-1',2); self.pipeline.run(upgraded['id'])
        self.assertEqual(len(self.remote.upload_keys),1)
        self.assertEqual(self.receipt()['generation'],2)
        self.assertEqual(self.receipt()['transcription_status'],'failed')
        self.assertEqual(self.receipt()['video_status'],'ready')
        self.assertEqual(self.receipt()['assets'][0]['locations'][0]['backend'],'s3')

    def test_retention_upgrade_reuses_successful_asr_transcript(self):
        self.pipeline.settings=replace(self.settings,enable_asr=True)
        self.remote.asr_status='succeeded'; self.run_job()
        self.remote.binding['generation']=2; self.remote.binding['storage_policy']='owned'
        upgraded=self.pipeline.enqueue('binding-1',2); self.pipeline.run(upgraded['id'])
        self.assertEqual(len(self.remote.upload_keys),1)
        self.assertEqual(self.receipt()['transcription_status'],'succeeded')
        self.assertTrue(any(a['kind']=='transcript' for a in self.receipt()['assets']))

    def test_untrusted_provider_dates_and_ids_cannot_persist_signed_urls(self):
        self.capture.artifacts[0]=replace(self.capture.artifacts[0],metadata={'duration':7,
            'created_at':'https://cdn.example.com/file?X-Amz-Signature=SECRET'})
        self.run_job()
        with self.store.db() as db:
            data=' '.join(str(tuple(row)) for table in ('source_jobs','source_artifacts') for row in db.execute('SELECT * FROM '+table))
        self.assertNotIn('SECRET',data); self.assertNotIn('https://',data)

    def test_untrusted_provider_asset_id_rejected_before_persistence(self):
        self.capture.artifacts[0]=replace(self.capture.artifacts[0],provider_asset_id='https://example.com/private?signature=SECRET')
        job=self.run_job()
        self.assertEqual(job['status'],'failed')
        self.assertNotIn('SECRET',encode(job['checkpoint']))

    def test_asr_uploading_retransmits_same_idempotent_input(self):
        self.pipeline.settings=replace(self.settings,enable_asr=True)
        self.remote.asr_status='uploading'; job=self.run_job()
        self.remote.asr_status='queued'; job=self.resume(job)
        self.assertEqual(len(self.remote.upload_keys),2)
        self.assertEqual(self.remote.upload_keys[0],self.remote.upload_keys[1])

    def test_historical_core_binding_access_uses_exact_provider_proof(self):
        self.assertIsNone(self.store.artifact('binding-1','recording-1'))
        result=self.pipeline.access('binding-1','recording-1')
        self.assertIn('https://',result['url'])
        self.assertIsNotNone(self.store.artifact('binding-1','recording-1'))
        self.assertRaises(ServiceError,self.pipeline.access,'binding-1','alien-recording')

    def test_crash_after_terminal_checkpoint_before_receipt_insert_recovers(self):
        with patch.object(self.store,'receipt',side_effect=SystemExit):
            with self.assertRaises(SystemExit): self.run_job()
        with self.store.db() as db: job_id=db.execute('SELECT id FROM source_jobs').fetchone()[0]
        self.assertEqual(self.store.pending(job_id),[])
        self.clock[0]+=self.settings.lease_seconds+1; self.pipeline.run(job_id)
        self.assertEqual(self.store.get(job_id)['status'],'succeeded')
        self.assertEqual(len(self.remote.receipts),1)

    def test_webhook_hmac_deduplication_and_job_wakeup(self):
        import base64,hmac,time
        secret=b'webhook-secret-at-least-16bytes'
        self.pipeline.capture.verify_webhook=DailyCapture(self.media,webhook_hmac=base64.b64encode(secret).decode()).verify_webhook
        job=self.run_job()
        event={'id':'event-1','type':'recording.ready-to-download',
               'payload':{'room_id':'room-1','mtg_session_id':'session-1','status':'https://secret-url?token=SECRET'}}
        raw=json.dumps(event,separators=(',',':')).encode(); timestamp=str(int(time.time()))
        signature=base64.b64encode(hmac.digest(secret,timestamp.encode()+b'.'+raw,hashlib.sha256)).decode()
        headers={**self.headers,'x-webhook-timestamp':timestamp,'x-webhook-signature':signature,'Content-Type':'application/json'}
        response=self.client.post('/v1/webhooks/daily',content=raw,headers=headers)
        self.assertEqual(response.status_code,200); self.assertFalse(response.json()['duplicate'])
        self.assertEqual(self.store.get(job['id'])['status'],'queued')
        duplicate=self.client.post('/v1/webhooks/daily',content=raw,headers=headers)
        self.assertTrue(duplicate.json()['duplicate'])
        invalid=self.client.post('/v1/webhooks/daily',content=raw+b' ',headers=headers)
        self.assertEqual(invalid.status_code,401)
        with self.store.db() as db: hints=' '.join(row[0] for row in db.execute('SELECT hint FROM source_webhooks'))
        self.assertNotIn('SECRET',hints); self.assertNotIn('https://',hints)


class AdapterBoundaryTests(unittest.TestCase):
    def test_finish_room404_requires_independent_empty_presence(self):
        media=DailyMedia('key',True)
        paths=[]
        def request(path,body=None):
            paths.append(path)
            if '/presence?' in path: return {'total_count':0,'data':[]}
            raise ProviderError(404)
        media.request=request
        media.finish({'id':'room-1','name':'hrv-owner'})
        self.assertTrue(any('/presence?' in p for p in paths))
    def test_finish_presence_failure_is_not_success(self):
        media=DailyMedia('key',True)
        media.request=lambda *a,**k: (_ for _ in ()).throw(ProviderError(404))
        self.assertRaises(ProviderError,media.finish,{'id':'room-1','name':'hrv-owner'})
    def test_discovery_exact_session_not_latest_room_recording(self):
        class Daily:
            def request(self,path):
                if path.startswith('/transcript?'): return {'data':[],'total_count':0}
                return {'data':[{'id':'alien','room_name':'hrv-owner','mtgSessionId':'alien-session','status':'finished'}],'total_count':1}
        result=DailyCapture(Daily()).discover({**BINDING['source_ref'],'provider':'daily','connection_id':'daily'})
        self.assertEqual(result.artifacts,[])
    def test_no_exact_session_or_server_attestation_is_blocked(self):
        class Daily:
            def request(self,*a): raise AssertionError('provider must not be called')
        result=DailyCapture(Daily()).discover({'room_id':'room-1','room_name':'hrv-owner','provider':'daily','connection_id':'daily'})
        self.assertFalse(result.complete); self.assertEqual(result.statuses['video']['status'],'blocked')
    def test_provider_deadline_expires_before_network(self):
        import time
        from source_daily._vendor.media_runtime import provider_budget,provider_timeout
        with provider_budget(deadline=time.monotonic()-1): self.assertRaises(Problem,provider_timeout)


if __name__=='__main__': unittest.main()
