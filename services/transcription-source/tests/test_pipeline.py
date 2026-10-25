"""Source API -> durable worker -> ASR HTTP -> object storage -> real Core API/DB."""
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI,Request
from fastapi.testclient import TestClient

from test_core import PORTAL,SCOPED,SOURCE,auth,binding,receipt,video
from hr_transcription_source.api import create_app
from hr_transcription_source.repository import Repository,LostLease
from hr_transcription_source.settings import Settings,Caller
from hr_transcription_source.worker import Runtime

TOKEN='t'*40
ASR_TOKEN='a'*40
MEDIA=b'fixture recording bytes'


@pytest.fixture
def setup(core_env,tmp_path):
    core,repo,store,service,principals,connect=core_env
    identity=binding(core,policy='owned').json()['binding_id']
    raw=store.add(MEDIA)
    response=core.post('/v1/bindings/'+identity+'/receipts',headers=auth(SOURCE),json=receipt(video(location={**raw,'state':'verified'})))
    assert response.status_code==200,response.text
    asset=response.json()['asset_ids'][0]
    event={'owner_kind':'interview','owner_id':'i1','binding_id':identity,'generation':1,'asset_id':asset}
    settings=Settings(tmp_path/'source','http://127.0.0.1:8092','http://127.0.0.1:8091',ASR_TOKEN,
        {'collector':Caller(TOKEN,SCOPED)},{('daily','daily'):SOURCE},download_hosts=frozenset({'private.example'}),retry_seconds=0)
    state={'submits':0,'jobs':{},'s3_fail':0,'downloaded':0}
    asr_app=FastAPI()
    @asr_app.post('/v1/jobs',status_code=202)
    async def submit(request:Request):
        assert request.headers['authorization']=='Bearer '+ASR_TOKEN
        key=request.headers['idempotency-key']
        data=await request.body()
        assert data==MEDIA
        if key not in state['jobs']:
            state['submits']+=1
            state['jobs'][key]='asr-job-one'
        return {'id':state['jobs'][key],'input_sha256':hashlib.sha256(data).hexdigest()}
    @asr_app.get('/v1/jobs/{identity}')
    def status(identity):
        return {'id':identity,'status':state.get('asr_status','succeeded'),'input_sha256':hashlib.sha256(MEDIA).hexdigest()}
    normalized={'schema_version':1,'format':'canonical_json','segments':[{'text':'actual words','start_ms':0,'end_ms':1000,'speaker':'channel-0'}],
        'text':'actual words','full_text':'actual words','vtt':'WEBVTT\n','warnings':[]}
    @asr_app.get('/v1/jobs/{identity}/result')
    def result(identity):
        return {'job_id':identity,'input_sha256':hashlib.sha256(MEDIA).hexdigest(),'model':'general','language':'ru',
            'provider':state.get('provider','yandex_speechkit'),'raw_result_url':state.get('raw_url','/v1/jobs/'+identity+'/result/raw'),'transcript':normalized}
    @asr_app.get('/v1/jobs/{identity}/result/raw')
    def raw_result(identity):
        return {'schema_version':1,'provider':state.get('provider','yandex_speechkit'),'job_id':identity,'input_sha256':hashlib.sha256(MEDIA).hexdigest(),
            'model':'general','language':'ru','operation_id':'operation-one','configuration':{},'provenance':{},'warnings':[],
            'events':[{'actual':'provider event'}]}
    def download(request):
        state['downloaded']+=1
        return httpx.Response(200,content=state.get('media',MEDIA))
    def put_file(path,**kwargs):
        if state['s3_fail']:
            state['s3_fail']-=1
            raise OSError('unavailable')
        data=Path(path).read_bytes()
        assert hashlib.sha256(data).hexdigest()==kwargs['expected_sha256']
        assert len(data)==kwargs['expected_size']
        ref=store.add(data,kwargs['content_type'])
        return store.head(ref['object_key'])
    store.put_file=put_file
    asr=TestClient(asr_app)
    runtime=Runtime(settings,core=core,asr=asr,download=httpx.Client(transport=httpx.MockTransport(download)),storage=store,publish=lambda _:None)
    api=TestClient(create_app(settings,runtime))
    return api,runtime,event,state,core,connect


def submit(api,event,key='event-one'):
    return api.post('/v1/recordings',headers={'Authorization':'Bearer '+TOKEN,'Idempotency-Key':key},json=event)


def test_http_lifecycle_dedup_and_real_core_dossier(setup):
    api,runtime,event,state,core,_=setup
    assert api.post('/v1/recordings',json=event).status_code==401
    response=submit(api,event)
    assert response.status_code==202,response.text
    identity=response.json()['id']
    assert submit(api,event,'second-key').json()['id']==identity
    result=runtime.run_once(identity)
    assert result['delivery_status']=='succeeded',result
    assert result['transcription_status']=='succeeded'
    assert state['submits']==1
    assert submit(api,event).json()['id']==identity
    assert runtime.run_once(identity)=={'claimed':False}
    dossier=core.get('/v1/candidates/c1/dossier',headers=auth(SCOPED)).json()
    assert result['transcript_ids'][0] in json.dumps(dossier)
    material=core.get('/v1/owners/interview/i1/materials',headers=auth(SCOPED)).json()
    assert material['channels'][0]['video_status']=='ready'
    assert material['channels'][0]['acquisition_complete'] is True
    assert len(material['transcripts'])==1
    assert material['transcripts'][0]['metadata']['origin']=='yandex_speechkit'
    assert len(material['assets'])==2
    assert api.get('/v1/jobs/'+identity,headers={'Authorization':'Bearer '+TOKEN}).json()['transcript_ids']==result['transcript_ids']


def test_cross_candidate_and_asset_binding_failure(setup):
    api,runtime,event,state,core,_=setup
    assert submit(api,{**event,'owner_id':'i2'}).status_code==409
    other=binding(core,owner='i2',policy='owned').json()['binding_id']
    assert submit(api,{**event,'owner_id':'i2','binding_id':other}).status_code==403
    assert submit(api,{**event,'asset_id':'foreign'}).status_code==409
    assert submit(api,{**event,'generation':2}).status_code==409
    assert submit(api,{**event,'url':'https://evil.example'}).status_code==422
    assert state['submits']==0


def test_s3_retry_and_restart_never_resubmit_asr(setup):
    api,runtime,event,state,core,_=setup
    identity=submit(api,event).json()['id']
    state['s3_fail']=1
    result=runtime.run_once(identity)
    assert result['transcription_status']=='succeeded'
    assert result['delivery_status']=='pending'
    assert runtime.repository.get(identity)['raw']
    runtime.repository=Repository(runtime.repository.path)
    assert runtime.run_once(identity)['delivery_status']=='succeeded'
    assert state['submits']==1
    assert state['downloaded']==1


def test_core_receipt_ack_loss_replays_same_body(setup,monkeypatch):
    api,runtime,event,state,core,_=setup
    identity=submit(api,event).json()['id']
    original=runtime._json
    calls=[]
    def lost(client,method,path,token,**kwargs):
        result=original(client,method,path,token,**kwargs)
        if method=='POST' and path.endswith('/receipts'):
            calls.append(kwargs['json'])
            if len(calls)==1:
                raise httpx.ReadTimeout('ack lost')
        return result
    monkeypatch.setattr(runtime,'_json',lost)
    assert runtime.run_once(identity)['delivery_status']=='delivering'
    result=runtime.run_once(identity)
    assert result['delivery_status']=='succeeded',result
    assert len(calls)==2 and calls[0]==calls[1]
    material=core.get('/v1/owners/interview/i1/materials',headers=auth(SCOPED)).json()
    assert len(material['transcripts'])==1
    assert state['submits']==1


def test_asr_submit_ack_loss_and_worker_restart_stable_idempotency(setup,monkeypatch):
    api,runtime,event,state,_,_=setup
    identity=submit(api,event).json()['id']
    original=runtime._json
    once=[True]
    def lost(client,method,path,token,**kwargs):
        result=original(client,method,path,token,**kwargs)
        if client is runtime.asr and method=='POST' and once[0]:
            once[0]=False
            raise httpx.ReadTimeout('ack lost')
        return result
    monkeypatch.setattr(runtime,'_json',lost)
    assert runtime.run_once(identity)['delivery_status']=='pending'
    assert runtime.run_once(identity)['delivery_status']=='succeeded'
    assert state['submits']==1


def test_restart_after_known_asr_id_only_polls(setup):
    api,runtime,event,state,_,_=setup
    identity=submit(api,event).json()['id']
    state['asr_status']='running'
    assert runtime.run_once(identity)['transcription_status']=='running'
    assert runtime.repository.get(identity)['asr_job_id']=='asr-job-one'
    state['asr_status']='succeeded'
    runtime.repository=Repository(runtime.repository.path)
    assert runtime.run_once(identity)['delivery_status']=='succeeded'
    assert state['submits']==1 and state['downloaded']==1


def test_revocation_before_delivery_rejects_and_preserves_paid_result(setup):
    api,runtime,event,state,_,connect=setup
    identity=submit(api,event).json()['id']
    state['s3_fail']=1
    assert runtime.run_once(identity)['transcription_status']=='succeeded'
    with connect() as db:
        db.execute("UPDATE media_sources SET metadata=? WHERE id=?",(json.dumps({'capture_disabled':True}),event['binding_id']))
    assert runtime.run_once(identity)['delivery_status']=='rejected'
    assert state['submits']==1


def test_corrupt_recording_does_not_submit(setup):
    api,runtime,event,state,_,_=setup
    identity=submit(api,event).json()['id']
    state['media']=b'corrupt'
    result=runtime.run_once(identity)
    assert result['error_code']=='recording_integrity_failed'
    assert result['delivery_status']=='rejected'
    assert state['submits']==0
    assert list(runtime.stage.iterdir())==[]


def test_raw_redirect_refused_and_result_response_bounded(setup):
    api,runtime,event,state,_,_=setup
    identity=submit(api,event).json()['id']
    state['raw_url']='https://evil.example/raw'
    assert runtime.run_once(identity)['error_code']=='asr_result_proof_mismatch'
    assert state['submits']==1


def test_expired_lease_cannot_publish_checkpoint(setup):
    _,runtime,event,_,_,_=setup
    job,_=runtime.submit('collector','one',event)
    clock=[1000.0]
    runtime.repository.clock=lambda:clock[0]
    # Fixture created job before fake clock; set its due time via SQL.
    with runtime.repository.db() as db:
        db.execute('UPDATE jobs SET next_at=0')
    _,old=runtime.repository.claim(job['id'],600)
    clock[0]+=601
    _,new=runtime.repository.claim(job['id'],600)
    assert new!=old
    with pytest.raises(LostLease):
        runtime.repository.update(job['id'],old,delivery_status='succeeded')
    assert runtime.repository.get(job['id'])['delivery_status']=='pending'


def test_owned_zoom_keeps_original_provider_transcript_and_adds_asr_version(setup):
    from hr_materials_core.app import create_app as core_app
    from hr_materials_core.auth import Principal,Tokens
    from materials_capture import owner_source_ref
    from test_core import transcript
    api,runtime,event,state,old_core,connect=setup
    service=old_core.app.state.service
    store=runtime.storage
    zoom_token='z'*40
    room={'provider':'zoom','id':'12345678901','name':'hrv-i1','meetingNumber':'12345678901',
          'meetingSessionId':'exact-session-uuid','connectionId':'zoom-account'}
    with connect() as db:
        db.execute("UPDATE cp_interviews SET room=? WHERE id='i1'",(json.dumps(room),))
        # This test replaces the fixture's prior Daily call with a distinct
        # Zoom binding; revoke the old call before projecting playback access.
        db.execute('UPDATE media_sources SET metadata=? WHERE id=?',
            (json.dumps({'capture_disabled':True}),event['binding_id']))
    service.storage_registry[('zoom','zoom-account')]=store
    tokens=Tokens({PORTAL:Principal('portal','portal'),SCOPED:Principal('candidate','portal',candidate_ids=frozenset({'c1'})),
        zoom_token:Principal('zoom','source','zoom','zoom-account')})
    with TestClient(core_app(service=service,tokens=tokens)) as core:
        runtime.core=core
        runtime.settings.source_tokens[('zoom','zoom-account')]=zoom_token
        response=core.post('/v1/bindings',headers=auth(),json={'schema_version':1,'owner_kind':'interview','owner_id':'i1',
            'provider':'zoom','connection_id':'zoom-account','storage_policy':'owned',
            'source_ref':owner_source_ref('interview','i1',room,'zoom','zoom-account')})
        assert response.status_code==200,response.text
        identity=response.json()['binding_id']
        location={**store.add(MEDIA),'state':'verified'}
        provider_text=transcript(store)
        provider_text['transcript']['provenance']['origin']='zoom_provider'
        response=core.post('/v1/bindings/'+identity+'/receipts',headers=auth(zoom_token),
            json=receipt(video('zoom-recording',location),provider_text,transcript_status='ready'))
        assert response.status_code==200,response.text
        original=response.json()['transcript_ids'][0]
        event={**event,'binding_id':identity,'asset_id':response.json()['asset_ids'][0]}
        identity=submit(api,event).json()['id']
        result=runtime.run_once(identity)
        assert result['delivery_status']=='succeeded',result
        materials=core.get('/v1/owners/interview/i1/materials',headers=auth(SCOPED)).json()
        transcripts=materials['transcripts']
        assert len(transcripts)==2
        assert original in {t['id'] for t in transcripts}
        assert {t['metadata']['origin'] for t in transcripts}=={'zoom_provider','yandex_speechkit'}
        assert state['submits']==1
        assert result['transcript_ids'][0] in core.get('/v1/candidates/c1/dossier',headers=auth(SCOPED)).text
        # Canonical Core consumers require explicit selection for every recording;
        # provider text is retained as an immutable alternative. The legacy local
        # adapter keeps its compatibility projection during the independent rollout.
        from types import SimpleNamespace
        from core_source_client import CoreMaterialsCatalog
        from materials_service import MaterialsService
        class CoreRead:
            def materials(self,kind,owner):
                response=core.get('/v1/owners/'+kind+'/'+owner+'/materials',headers=auth(SCOPED))
                assert response.status_code==200
                return response.json()
            def access(self,asset):
                response=core.get('/v1/assets/'+asset+'/access',headers=auth(SCOPED))
                assert response.status_code==200
                return response.json()
        local=MaterialsService(SimpleNamespace(repository=service.repository,storage=store,
            clock=lambda:0,settle_seconds=86400))
        canonical=CoreMaterialsCatalog(CoreRead(),connect=connect).legacy_materials('interview',{'id':'i1','room':room})
        assert canonical['transcripts']==[]
        assert canonical['transcription']['status']=='processing'
        assert {t['id'] for t in canonical['transcriptVariants']}=={original,*result['transcript_ids']}
        projected=local.legacy_materials('interview',{'id':'i1','room':room})
        assert [t['id'] for t in projected['transcripts']]==[original]
        assert projected['transcripts'][0]['fullText']=='Actual speech'
        assert [t['id'] for t in projected['transcriptVariants']]==result['transcript_ids']
        assert projected['transcriptVariants'][0]['origin']=='yandex_speechkit'


def test_remote_without_explicit_resolver_is_clear_error_no_policy_change(setup):
    api,runtime,event,state,core,connect=setup
    with connect() as db:
        db.execute("UPDATE media_sources SET storage_policy='remote' WHERE id=?",(event['binding_id'],))
        db.execute('DELETE FROM asset_locations WHERE asset_id=?',(event['asset_id'],))
        row=db.execute('SELECT provider_asset_id FROM media_assets WHERE id=?',(event['asset_id'],)).fetchone()
    repo=core.app.state.service.repository
    repo.add_location(event['asset_id'],'provider',row[0],'available')
    identity=submit(api,event).json()['id']
    result=runtime.run_once(identity)
    assert result['error_code']=='remote_source_resolver_unconfigured'
    assert state['submits']==0
    with connect() as db:
        assert db.execute('SELECT storage_policy FROM media_sources WHERE id=?',(event['binding_id'],)).fetchone()[0]=='remote'


def test_generation_change_after_asr_blocks_publication(setup):
    api,runtime,event,state,core,connect=setup
    identity=submit(api,event).json()['id']
    state['s3_fail']=1
    assert runtime.run_once(identity)['transcription_status']=='succeeded'
    with connect() as db:
        db.execute('UPDATE media_sources SET generation=generation+1 WHERE id=?',(event['binding_id'],))
    result=runtime.run_once(identity)
    assert result['delivery_status']=='rejected'
    assert result['error_code']=='binding_owner_generation_mismatch'
    assert state['submits']==1
    assert not core.get('/v1/owners/interview/i1/materials',headers=auth(SCOPED)).json()['transcripts']


def test_asr_poll_429_is_retryable_without_resubmit(setup,monkeypatch):
    from hr_transcription_source.worker import Retryable
    api,runtime,event,state,_,_=setup
    identity=submit(api,event).json()['id']
    original=runtime.asr
    class Limited:
        def stream(self,method,path,**kwargs):
            if method=='GET' and path=='/v1/jobs/asr-job-one':
                return httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(429))).stream(method,'https://fixture.example'+path)
            return original.stream(method,path,**kwargs)
    runtime.asr=Limited()
    result=runtime.run_once(identity)
    assert result['error_code']=='retryable_dependency_failure'
    assert runtime.repository.get(identity)['asr_job_id']=='asr-job-one'
    runtime.asr=original
    assert runtime.run_once(identity)['delivery_status']=='succeeded'
    assert state['submits']==1


def test_lost_lease_after_download_cleans_recording_temp(setup,monkeypatch):
    api,runtime,event,state,_,_=setup
    identity=submit(api,event).json()['id']
    update=runtime.repository.update
    def lost(identity,token,**kwargs):
        if 'input_sha256' in kwargs:
            raise LostLease()
        return update(identity,token,**kwargs)
    monkeypatch.setattr(runtime.repository,'update',lost)
    assert runtime.run_once(identity)['lease_lost'] is True
    assert list(runtime.stage.iterdir())==[]
    assert state['submits']==0


def test_authenticated_response_limit_is_bounded(setup):
    from hr_transcription_source.worker import Rejected
    _,runtime,_,_,_,_=setup
    runtime.settings.max_result_bytes=10
    client=httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,content=b'x'*100)))
    with pytest.raises(Rejected):
        runtime._json(client,'GET','https://fixture.example',ASR_TOKEN)


def test_explicit_deepgram_provider_is_preserved_in_core(setup):
    api,runtime,event,state,core,_=setup
    runtime.settings.asr_provider='deepgram'
    runtime.settings.revision='deepgram-v1'
    state['provider']='deepgram'
    identity=submit(api,event).json()['id']
    result=runtime.run_once(identity)
    assert result['delivery_status']=='succeeded',result
    material=core.get('/v1/owners/interview/i1/materials',headers=auth(SCOPED)).json()
    assert material['transcripts'][0]['metadata']['origin']=='deepgram'
    assert material['assets'][-1]['external_id'].startswith('asr:deepgram:') or material['assets'][0]['external_id'].startswith('asr:deepgram:')
    assert runtime.repository.get(identity)['revision']=='deepgram:deepgram-v1'


def test_delivery_waits_for_complete_acquisition_without_rerunning_asr(setup):
    api,runtime,event,state,core,connect=setup
    with connect() as db:
        db.execute('UPDATE core_material_channels SET acquisition_complete=0 WHERE binding_id=?',(event['binding_id'],))
    identity=submit(api,event).json()['id']
    result=runtime.run_once(identity)
    assert result['transcription_status']=='queued'
    assert result['delivery_status']=='pending'
    assert state['submits']==0
    with connect() as db:
        db.execute('UPDATE core_material_channels SET acquisition_complete=1 WHERE binding_id=?',(event['binding_id'],))
    assert runtime.run_once(identity)['delivery_status']=='succeeded'
    assert state['submits']==1


def test_durable_submit_survives_broker_outage_and_fresh_runtime(setup):
    api,runtime,event,state,_,_=setup
    def broker_down(_):
        raise OSError('broker unavailable')
    runtime.publish=broker_down
    response=submit(api,event)
    assert response.status_code==202
    identity=response.json()['id']
    assert runtime.repository.due()==[identity]
    fresh=Runtime(runtime.settings,core=runtime.core,asr=runtime.asr,download=runtime.download,storage=runtime.storage,
        publish=lambda _:None)
    assert fresh.reconcile()['pending']==1
    assert fresh.run_once(identity)['delivery_status']=='succeeded'
    assert state['submits']==1
