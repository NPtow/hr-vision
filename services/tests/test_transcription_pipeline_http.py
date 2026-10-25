"""Real TCP source -> ASR -> provider fixture -> S3 -> Core -> candidate dossier.

No paid API or real person data. FFmpeg is real; only Deepgram and object storage
are fixtures. Receipt acknowledgement loss exercises actual committed Core state.
"""
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import wave

import httpx
from fastapi import FastAPI, Request

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT/'interface-lab/server', ROOT/'services/data-core/src',
             ROOT/'services/transcription/src', ROOT/'services/transcription-source/src'):
    sys.path.insert(0, str(path))

from test_zoom_core_http_integration import OwnedObjects, real_http_app
from hr_materials_core.app import create_app as core_app
from hr_materials_core.auth import Principal, Tokens
from hr_materials_core.repository import CoreRepository
from hr_materials_core.service import CoreService
from materials_capture import owner_source_ref
from md_transcription.api import create_app as asr_app
from md_transcription.settings import Settings as ASRSettings
from md_transcription.worker import Runtime as ASRRuntime
from hr_transcription_source.api import create_app as source_app
from hr_transcription_source.settings import Settings as SourceSettings, Caller
from hr_transcription_source.worker import Runtime as SourceRuntime

PORTAL='portal-fixture-'+'p'*32
WRITE='source-fixture-'+'w'*32
ASR='asr-fixture-'+'a'*32
SOURCE='source-api-fixture-'+'s'*32


def headers(token):
    return {'Authorization':'Bearer '+token}


def test_full_deepgram_path_and_receipt_ack_loss(tmp_path, monkeypatch):
    import md_transcription.deepgram as deepgram
    # A genuine valid WAV input, decoded by the actual subprocess implementation.
    sink=io.BytesIO()
    with wave.open(sink, 'wb') as stream:
        stream.setparams((1,2,16000,0,'NONE','not compressed'))
        stream.writeframes(b'\0\0'*16000)
    media=sink.getvalue()
    path=tmp_path/'sample.wav'; path.write_bytes(media)
    sha=hashlib.sha256(media).hexdigest()
    store=OwnedObjects()
    obj=store.put_file(path,expected_size=len(media),expected_sha256=sha,content_type='audio/wav')
    room={'provider':'zoom','id':'12345678901','name':'hrv-interview',
          'meetingNumber':'12345678901','meetingSessionId':'fixture-session','connectionId':'zoom'}
    connect=lambda:sqlite3.connect(tmp_path/'hr.sqlite3',timeout=10)
    with connect() as db:
        db.executescript('''
          CREATE TABLE em_intake_candidates(id TEXT PRIMARY KEY,workspace_id TEXT,vacancy_id TEXT,body TEXT);
          CREATE TABLE cp_candidates(id TEXT PRIMARY KEY,email TEXT,password_hash TEXT,profile TEXT,created_at TEXT,updated_at TEXT);
          CREATE TABLE cp_interviews(id TEXT PRIMARY KEY,candidate_id TEXT,status TEXT,room TEXT,created_at TEXT,completed_at TEXT);
          CREATE TABLE cp_applications(id TEXT PRIMARY KEY,candidate_id TEXT,status TEXT);
          CREATE TABLE cp_meetings(id TEXT PRIMARY KEY,application_id TEXT,status TEXT,room TEXT,start TEXT,end TEXT,completed_at TEXT);
        ''')
        db.execute('INSERT INTO cp_candidates VALUES(?,?,?,?,?,?)',('candidate','fixture@example.org','secret','{}','2026-01-01','2026-01-01'))
        db.execute('INSERT INTO cp_interviews VALUES(?,?,?,?,?,?)',('interview','candidate','completed',json.dumps(room),'2026-01-01','2026-01-01'))
    repository=CoreRepository(connect,default_connections={'zoom':'zoom'}); repository.init()
    core=core_app(service=CoreService(repository,{('zoom','zoom'):store}),tokens=Tokens({
        PORTAL:Principal('portal','portal'), WRITE:Principal('zoom','source','zoom','zoom')}))
    provider=FastAPI()
    calls=[]
    payload={'metadata':{'request_id':'fixture-request','duration':1,'channels':1,
                         'model_info':{'fixture':{'name':'nova-3','version':'fixture'}},
                         'diarize_info':{'arch':'v2','model_uuid':'fixture'}},
             'results':{'channels':[{'alternatives':[{'transcript':'Synthetic speech.',
                 'words':[{'word':'synthetic','punctuated_word':'Synthetic','start':0.1,'end':0.4,'speaker':0},
                          {'word':'speech','punctuated_word':'speech.','start':0.4,'end':0.8,'speaker':1}]}]}]}}
    @provider.post('/v1/listen')
    async def listen(request:Request):
        assert request.headers['authorization']=='Token provider-fixture'
        assert dict(request.query_params)=={'model':'nova-3','language':'ru','diarize_model':'v2',
            'smart_format':'false','punctuate':'true','utterances':'true','numerals':'false','profanity_filter':'false'}
        audio=await request.body()
        with wave.open(io.BytesIO(audio)) as decoded:
            assert decoded.getframerate()==16000 and decoded.getnchannels()==1
        calls.append(hashlib.sha256(audio).hexdigest())
        return payload

    cfg=ASRSettings(data_dir=tmp_path/'asr',api_keys={'source':ASR},backend_kind='deepgram',deepgram_api_key='provider-fixture')
    asr=ASRRuntime(cfg,publish=lambda _:None)
    with real_http_app(provider) as provider_url, real_http_app(core) as core_url, real_http_app(asr_app(cfg,asr)) as asr_url:
        monkeypatch.setattr(deepgram,'LISTEN',provider_url+'/v1/listen')
        with httpx.Client(base_url=core_url) as api:
            binding=api.post('/v1/bindings',headers=headers(PORTAL),json={'schema_version':1,
                'owner_kind':'interview','owner_id':'interview','provider':'zoom','connection_id':'zoom',
                'storage_policy':'owned','source_ref':owner_source_ref('interview','interview',room,'zoom','zoom')})
            assert binding.status_code==200,binding.text
            bid=binding.json()['binding_id']
            # Preserve a separate, already-existing provider transcript.
            raw_path=tmp_path/'original.vtt'; raw_path.write_bytes(b'WEBVTT\n\nOriginal Zoom words')
            raw=store.put_file(raw_path,expected_size=raw_path.stat().st_size,
                expected_sha256=hashlib.sha256(raw_path.read_bytes()).hexdigest(),content_type='text/vtt')
            receipt={'schema_version':1,'generation':1,'receipt_id':'capture','video_status':'not_recorded',
                'transcript_status':'ready','acquisition_complete':True,'transcription_status':'not_requested',
                'assets':[{'external_id':'recording','kind':'audio','state':'ready','metadata':{'format':'wav'},
                  'locations':[{'backend':'s3','object_key':obj.key,'sha256':sha,'size':len(media),'state':'verified','content_type':'audio/wav'}]},
                  {'external_id':'original-zoom-text','kind':'transcript','state':'ready','locations':[],
                   'transcript':{'raw_ref':{'backend':'s3','object_key':raw.key,'sha256':raw.sha256,'size':raw.size,'content_type':raw.content_type},
                    'provenance':{'origin':'zoom_provider','processing_status':'completed','coverage':'not_independently_verified'},
                    'normalized':{'schema_version':1,'format':'json','segments':[{'text':'Original Zoom words','start_ms':None,'end_ms':None,'speaker':None}],
                                  'text':'Original Zoom words','full_text':'Original Zoom words','vtt':'','warnings':['missing_timestamps']}}}]}
            capture=api.post('/v1/bindings/'+bid+'/receipts',headers=headers(WRITE),json=receipt)
            assert capture.status_code==200,capture.text
            aid=capture.json()['asset_ids'][0]
            original_id=capture.json()['transcript_ids'][0]
        def download(request):
            assert request.url.host=='owned-fixture.example'
            return httpx.Response(200,content=media)
        settings=SourceSettings(tmp_path/'source',core_url,asr_url,ASR,
            {'collector':Caller(SOURCE,PORTAL)},{('zoom','zoom'):WRITE},revision='deepgram-v1',
            asr_provider='deepgram',download_hosts=frozenset({'owned-fixture.example'}),retry_seconds=0)
        def fresh_source():
            return SourceRuntime(settings,download=httpx.Client(transport=httpx.MockTransport(download)),
                                 storage=store,publish=lambda _:None)
        source=fresh_source()
        with real_http_app(source_app(settings,source)) as source_url, httpx.Client(base_url=source_url) as api:
            event={'owner_kind':'interview','owner_id':'interview','binding_id':bid,'generation':1,'asset_id':aid}
            auth={**headers(SOURCE),'Idempotency-Key':'fixture-recording'}
            response=api.post('/v1/recordings',headers=auth,json=event)
            assert response.status_code==202,response.text
            job=response.json()['id']
            assert api.post('/v1/recordings',headers=auth,json=event).json()['id']==job
            assert source.run_once(job)['transcription_status']=='running'
            asr_id=source.repository.get(job)['asr_job_id']
            assert ASRRuntime(cfg).run_once(asr_id)['status']=='succeeded'
            assert len(calls)==1
            # Fresh worker instance/reopened SQLite, real commit, lost acknowledgement.
            source=fresh_source()
            original=source._json
            def ack_lost(client,method,path,token,**kwargs):
                result=original(client,method,path,token,**kwargs)
                if method=='POST' and path.endswith('/receipts'):
                    raise httpx.ReadTimeout('fixture lost acknowledgement')
                return result
            source._json=ack_lost
            assert source.run_once(job)['delivery_status']=='delivering'
            source=fresh_source()
            delivered=source.run_once(job)
            assert delivered['delivery_status']=='succeeded',delivered
            assert len(calls)==1
            assert source.run_once(job)=={'claimed':False}
        with httpx.Client(base_url=core_url) as api:
            materials=api.get('/v1/owners/interview/interview/materials',headers=headers(PORTAL)).json()
            assert len(materials['transcripts'])==2
            assert any(t['id']==original_id and '\n'.join(s['text'] for s in t['segments'])=='Original Zoom words' for t in materials['transcripts'])
            new=next(t for t in materials['transcripts'] if t['metadata']['origin']=='deepgram')
            assert '\n'.join(s['text'] for s in new['segments'])=='Synthetic\nspeech.'
            assert [s['speaker'] for s in new['segments']]==['speaker_0','speaker_1']
            stored=store.objects[new['raw_ref']['object_key']]
            assert hashlib.sha256(stored).hexdigest()==new['raw_ref']['sha256']
            assert json.loads(stored)['events']==[payload]
            dossier=api.get('/v1/candidates/candidate/dossier',headers=headers(PORTAL))
            assert dossier.status_code==200 and new['id'] in dossier.text
            assert new['id'] in delivered['transcript_ids']
