"""Actual private ingress HTTP actions; all storage/Core effects are local fakes."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import wave
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from hr_transcription_source.api import create_app
from hr_transcription_source.settings import Settings, Caller
from hr_transcription_source.uploads import Uploads, probe_media
from hr_transcription_source.worker import Runtime

TOKEN='u'*40
CORE='c'*40
SOURCE='s'*40
EVENT={'owner_kind':'intake','owner_id':'draft1','binding_id':'binding1','generation':1}


def wav():
    raw=io.BytesIO()
    with wave.open(raw,'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b'\x01\x00'*1600)
    return raw.getvalue()


@pytest.fixture
def upload_env(tmp_path):
    settings=Settings(tmp_path/'source','http://localhost:8093','http://localhost:8106','a'*40,
        {'portal':Caller(TOKEN,CORE)},{('upload','upload'):SOURCE},max_input_bytes=100_000,
        upload_max_total_bytes=200_000,upload_max_files=3)
    state={'revoked':False,'posts':[],'assets':[],'puts':[],'put_fail':None,'ack_lost':0}
    binding={**EVENT,'id':'binding1','provider':'upload','connection_id':'upload','storage_policy':'owned','revoked':False}
    def handler(request):
        assert request.headers['authorization'] in ('Bearer '+CORE,'Bearer '+SOURCE)
        if state['revoked']:
            return httpx.Response(410,json={'detail':'revoked'})
        if request.url.path=='/v1/bindings/binding1':
            return httpx.Response(200,json=binding)
        if request.method=='POST':
            body=json.loads(request.content)
            state['posts'].append(body)
            state['assets']=[{**asset,'id':'asset'+str(index),'source_id':'binding1'} for index,asset in enumerate(body['assets'])]
            if state['ack_lost']:
                state['ack_lost']-=1
                raise httpx.ReadTimeout('lost receipt acknowledgment')
            return httpx.Response(200,json={'binding_id':'binding1','generation':1,'receipt_id':body['receipt_id'],
                'asset_ids':[a['id'] for a in state['assets']]})
        assert request.url.path=='/v1/owners/intake/draft1/materials'
        return httpx.Response(200,json={'owner_kind':'intake','owner_id':'draft1','assets':state['assets']})
    def put_file(path,**kwargs):
        sha=hashlib.sha256(Path(path).read_bytes()).hexdigest()
        assert sha==kwargs['expected_sha256'] and Path(path).stat().st_size==kwargs['expected_size']
        state['puts'].append(sha)
        if state['put_fail']==len(state['puts']):
            raise OSError('storage down')
        return SimpleNamespace(backend='s3',key='materials/sha256/'+sha[:2]+'/'+sha,sha256=sha,
            size=kwargs['expected_size'],content_type=kwargs['content_type'])
    def no_asr(request):
        pytest.fail('upload must never call ASR')
    runtime=Runtime(settings,core=httpx.Client(base_url='http://core.test',transport=httpx.MockTransport(handler)),
        asr=httpx.Client(transport=httpx.MockTransport(no_asr)),storage=SimpleNamespace(put_file=put_file),publish=lambda _:None)
    app=create_app(settings,runtime)
    app.state.uploads.probe=lambda path,mime,settings:{'kind':'audio','format':'wav','duration':0.1}
    return TestClient(app),runtime,app.state.uploads,state


def begin(api, files=None, key='upload-one', **changes):
    body={**EVENT,'files':files or [{'size':4,'content_type':'audio/wav','name':'recording.wav'}],**changes}
    return api.post('/v1/uploads',headers={'Authorization':'Bearer '+TOKEN,'Idempotency-Key':key},json=body)


def send(api,identity,data=b'data',index=0,**kwargs):
    return api.put('/v1/uploads/'+identity+'/parts/'+str(index),headers={'Authorization':'Bearer '+TOKEN,
        'Content-Type':'application/octet-stream',**kwargs},content=data)


def finalize(api,identity):
    return api.post('/v1/uploads/'+identity+'/finalize',headers={'Authorization':'Bearer '+TOKEN},json={})


def test_ordered_batch_registration_and_ack_replay_without_asr(upload_env):
    api,runtime,uploads,state=upload_env
    files=[{'size':4,'content_type':'audio/wav','name':'one.wav'},{'size':4,'content_type':'audio/wav','name':'two.wav'}]
    result=begin(api,files)
    assert result.status_code==202,result.text
    identity=result.json()['id']
    assert begin(api,files).json()['id']==identity
    assert send(api,identity,b'two!',1).status_code==200
    assert finalize(api,identity).status_code==409
    assert state['posts']==[] and state['puts']==[]
    assert send(api,identity).status_code==200
    state['ack_lost']=1
    assert finalize(api,identity).status_code==503
    durable=uploads.repository.get(identity)
    assert durable['receipt']['acquisition_complete'] is True
    assert [a['metadata']['part_index'] for a in durable['receipt']['assets']]==[0,1]
    assert uploads.path(identity,0).exists()
    # Construct another runtime/process view over the same source DB.
    restart=Uploads(runtime,probe=uploads.probe)
    result=restart.finalize('portal',identity)
    assert result['status']=='registered' and result['asset_ids']==['asset0','asset1']
    assert state['posts'][0]==state['posts'][1]
    assert len(state['puts'])==2
    assert not uploads.path(identity,0).exists()
    assert finalize(api,identity).json()['status']=='registered'
    # Immutable duplicate is validated even after raw spool cleanup.
    assert send(api,identity).status_code==200
    assert send(api,identity,b'evil').status_code==409
    assert len(state['posts'])==2


def test_stored_part_checkpoint_survives_s3_failure_and_scheduler_resume(upload_env):
    api,runtime,uploads,state=upload_env
    files=[{'size':4,'content_type':'audio/wav'},{'size':4,'content_type':'audio/wav'}]
    identity=begin(api,files).json()['id']
    send(api,identity)
    send(api,identity,b'two!',1)
    state['put_fail']=2
    assert finalize(api,identity).status_code==503
    assert uploads.repository.get(identity)['parts'][0]['stored']
    assert state['posts']==[]
    state['put_fail']=None
    assert runtime.reconcile()['uploads']=={'registered':1,'deferred':0}
    assert len(state['puts'])==3
    assert uploads.repository.get(identity)['status']=='registered'


def test_manifest_and_bytes_limits_auth_and_wrong_owner(upload_env):
    api,runtime,uploads,state=upload_env
    assert api.post('/v1/uploads',json={**EVENT,'files':[]}).status_code==401
    assert begin(api,files=[{'size':100_001,'content_type':'audio/wav'}]).status_code==413
    assert begin(api,files=[{'size':4,'content_type':'text/plain'}]).status_code==415
    assert begin(api,files=[{'size':4,'content_type':'audio/wav','name':'playlist.m3u8'}]).status_code==422
    assert begin(api,files=[{'size':4,'content_type':'audio/wav'}]*4).status_code==413
    identity=begin(api).json()['id']
    assert begin(api,files=[{'size':5,'content_type':'audio/wav'}]).status_code==409
    assert send(api,identity,b'longer').status_code==409
    # No Content-Length: streamed hard limit cannot be bypassed.
    assert send(api,identity,iter([b'ab',b'cde'])).status_code==413
    assert send(api,identity,iter([b'a'])).status_code==409
    assert send(api,identity,b'data',index=5).status_code==404
    assert uploads.repository.get(identity)['parts'][0]['status']=='pending'
    assert state['puts']==[]
    assert api.put('/v1/uploads/'+identity+'/parts/0',content=b'data').status_code==401
    assert api.get('/v1/uploads/'+identity,headers={'Authorization':'Bearer '+'x'*40}).status_code==401
    assert begin(api,key='wrongowner',owner_id='other').status_code==409
    state['revoked']=True
    assert send(api,identity).status_code==410
    assert finalize(api,identity).status_code==410
    assert api.get('/v1/uploads/'+identity,headers={'Authorization':'Bearer '+TOKEN}).status_code==410


def test_invalid_media_does_not_commit_part_or_call_storage(upload_env):
    api,runtime,uploads,state=upload_env
    uploads.probe=probe_media
    identity=begin(api).json()['id']
    assert send(api,identity).status_code==422
    assert uploads.repository.get(identity)['parts'][0]['sha256'] is None
    assert not list(uploads.root.glob('upload-*'))
    assert state['puts']==[]


@pytest.mark.skipif(shutil.which('ffprobe') is None,reason='requires local ffprobe')
def test_real_audio_probe_and_manifest_sanitization(upload_env):
    api,runtime,uploads,state=upload_env
    uploads.probe=probe_media
    data=wav()
    identity=begin(api,[{'size':len(data),'content_type':'audio/wav','name':'../../private/voice.wav'}]).json()['id']
    assert uploads.repository.get(identity)['manifest']['files'][0]['name']=='voice.wav'
    assert send(api,identity,data).status_code==200
    probe=uploads.repository.get(identity)['parts'][0]['probe']
    assert probe['duration']==pytest.approx(0.1) and probe['kind']=='audio'
    assert finalize(api,identity).status_code==200


def test_expired_part_writer_and_orphan_publish_recovery(upload_env):
    api,runtime,uploads,state=upload_env
    now=[1000.0]
    runtime.repository.clock=lambda:now[0]
    identity=begin(api).json()['id']
    token=uploads.repository.claim_part(identity,0,1)
    assert send(api,identity).status_code==409
    now[0]+=2
    # Simulate crash after atomic file publication but before its DB checkpoint.
    uploads.path(identity,0).write_bytes(b'data')
    assert send(api,identity).status_code==200
    from hr_transcription_source.repository import LostLease
    with pytest.raises(LostLease):
        uploads.repository.update_part(identity,0,token,sha256='0'*64)
    assert send(api,identity,b'evil').status_code==409


def test_caller_isolation_revocation_during_stream_and_disconnect_cleanup(upload_env):
    import asyncio
    from hr_transcription_source.settings import Caller
    from hr_transcription_source.worker import Rejected
    from starlette.requests import Request, ClientDisconnect
    api,runtime,uploads,state=upload_env
    runtime.settings.callers['other']=Caller('o'*40,CORE)
    identity=begin(api).json()['id']
    assert api.get('/v1/uploads/'+identity,headers={'Authorization':'Bearer '+'o'*40}).status_code==404
    calls=[{'type':'http.request','body':b'da','more_body':True},{'type':'http.disconnect'}]
    async def receive():
        return calls.pop(0)
    request=Request({'type':'http','headers':[(b'content-type',b'application/octet-stream')]},receive)
    with pytest.raises(ClientDisconnect):
        asyncio.run(uploads.receive('portal',identity,0,request))
    assert not list(uploads.root.glob('upload-*'))
    assert uploads.repository.get(identity)['parts'][0]['lease_token'] is None
    # Revoke the owner after initial authorization, before publishing the part.
    calls=[{'type':'http.request','body':b'data','more_body':False}]
    async def revoke_stream():
        state['revoked']=True
        return calls.pop(0)
    request=Request({'type':'http','headers':[(b'content-type',b'application/octet-stream')]},revoke_stream)
    with pytest.raises(Rejected):
        asyncio.run(uploads.receive('portal',identity,0,request))
    assert uploads.repository.get(identity)['parts'][0]['status']=='pending'
    assert not uploads.path(identity,0).exists()


def test_durable_finalization_revoked_intent_stops_retries(upload_env):
    api,runtime,uploads,state=upload_env
    identity=begin(api).json()['id']
    send(api,identity)
    state['put_fail']=1
    assert finalize(api,identity).status_code==503
    state['revoked']=True
    assert uploads.reconcile()=={'registered':0,'deferred':1}
    assert uploads.repository.get(identity)['status']=='rejected'
    assert uploads.repository.get(identity)['error_code']=='upstream_rejected_410'
    assert uploads.repository.due()==[]
    assert state['posts']==[]


def test_lost_ack_keeps_raw_until_readback_then_cleanup_is_recoverable(upload_env,monkeypatch):
    api,runtime,uploads,state=upload_env
    identity=begin(api).json()['id']
    send(api,identity)
    path=uploads.path(identity,0)
    original=Path.unlink
    def interrupted(path,*args,**kwargs):
        if path.name.startswith(identity+'-0-') and path.suffix=='.media':
            raise OSError('temporary filesystem failure')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',interrupted)
    assert finalize(api,identity).json()['status']=='registered'
    assert path.exists()
    monkeypatch.setattr(Path,'unlink',original)
    uploads.cleanup()
    assert not path.exists()


def test_expired_writer_cannot_publish_bytes_for_later_writer(upload_env):
    import asyncio
    from starlette.requests import Request
    from hr_transcription_source.repository import LostLease
    api,runtime,uploads,state=upload_env
    now=[1000.0]
    runtime.repository.clock=lambda:now[0]
    identity=begin(api).json()['id']
    original=uploads.probe
    def expired_probe(path,mime,settings):
        now[0]+=settings.lease_seconds+1
        state['second_lease']=uploads.repository.claim_part(identity,0,1)
        return original(path,mime,settings)
    uploads.probe=expired_probe
    calls=[{'type':'http.request','body':b'data','more_body':False}]
    async def receive():
        return calls.pop(0)
    request=Request({'type':'http','headers':[(b'content-type',b'application/octet-stream')]},receive)
    with pytest.raises(LostLease):
        asyncio.run(uploads.receive('portal',identity,0,request))
    assert uploads.repository.get(identity)['parts'][0]['sha256'] is None
    assert len(list(uploads.root.glob(identity+'-0-*.media')))==1  # Non-authoritative orphan.
    now[0]+=2
    uploads.probe=original
    assert send(api,identity,b'evil').status_code==200
    assert uploads.path(identity,0).read_bytes()==b'evil'
    assert finalize(api,identity).status_code==200
    assert state['posts'][0]['assets'][0]['locations'][0]['sha256']==hashlib.sha256(b'evil').hexdigest()


def test_staging_budget_is_durable_global_and_released_only_after_cleanup(upload_env,monkeypatch):
    api,runtime,uploads,state=upload_env
    runtime.settings.upload_stage_budget_bytes=8
    first=begin(api,key='one').json()['id']
    second=begin(api,key='two').json()['id']
    assert begin(api,key='one').json()['id']==first  # Duplicate does not reserve twice.
    assert begin(api,key='three').status_code==507
    # A second process sees the same reservation ledger.
    other=Uploads(runtime,probe=uploads.probe)
    from hr_transcription_source.upload_repository import UploadCapacity
    with pytest.raises(UploadCapacity):
        other.begin('portal','other-process',{**EVENT,'files':[{'size':4,'content_type':'audio/wav'}]})
    send(api,first)
    original=Path.unlink
    def interrupted(path,*args,**kwargs):
        if path.name.startswith(first+'-0-'):
            raise OSError('cleanup unavailable')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',interrupted)
    assert finalize(api,first).status_code==200
    assert uploads.repository.get(first)['reserved_bytes']==4
    assert begin(api,key='three').status_code==507
    monkeypatch.setattr(Path,'unlink',original)
    other.cleanup()
    assert uploads.repository.get(first)['reserved_bytes']==0
    assert begin(api,key='three').status_code==202
    assert uploads.repository.get(second)['reserved_bytes']==4


def test_stream_concurrency_global_rejects_before_consuming_body_and_recovers_lease(upload_env):
    import asyncio
    from starlette.requests import Request
    from hr_transcription_source.upload_repository import UploadCapacity
    api,runtime,uploads,state=upload_env
    identity=begin(api).json()['id']
    now=[1000.0]
    runtime.repository.clock=lambda:now[0]
    repo=uploads.repository
    first=repo.claim_stream(identity,0,10,2,2*1024**3)
    second=Uploads(runtime).repository.claim_stream(identity,0,10,2,2*1024**3)
    consumed=[]
    async def receive():
        consumed.append(True)
        return {'type':'http.request','body':b'data','more_body':False}
    request=Request({'type':'http','headers':[(b'content-type',b'application/octet-stream')]},receive)
    with pytest.raises(UploadCapacity) as error:
        asyncio.run(uploads.receive('portal',identity,0,request))
    assert error.value.status==429 and consumed==[]
    now[0]+=11
    assert send(api,identity).status_code==200
    with runtime.repository.db() as db:
        assert db.execute('SELECT count(*) FROM upload_streams').fetchone()[0]==0


def test_abandoned_ttl_releases_only_incomplete_unleased_intent_not_finalizing(upload_env):
    api,runtime,uploads,state=upload_env
    now=[1000.0]
    runtime.repository.clock=lambda:now[0]
    runtime.settings.upload_stage_budget_bytes=8
    abandoned=begin(api,key='abandoned').json()['id']
    pending=begin(api,key='finalizing').json()['id']
    send(api,abandoned)
    send(api,pending)
    state['put_fail']=1
    assert finalize(api,pending).status_code==503
    now[0]+=runtime.settings.upload_idle_ttl_seconds+1
    uploads.cleanup()
    assert uploads.repository.get(abandoned)['status']=='expired'
    assert uploads.repository.get(abandoned)['reserved_bytes']==0
    assert not uploads.path(abandoned,0).exists()
    assert send(api,abandoned).status_code==410
    assert uploads.repository.get(pending)['status']=='finalizing'
    assert uploads.repository.get(pending)['reserved_bytes']==4
    assert uploads.path(pending,0).exists()
    assert begin(api,key='replacement').status_code==202


def test_unreferenced_writer_files_count_against_admission_budget(upload_env):
    api,runtime,uploads,state=upload_env
    runtime.settings.upload_stage_budget_bytes=8
    identity=begin(api).json()['id']
    orphan=uploads.root/(identity+'-0-'+'a'*48+'.media')
    orphan.write_bytes(b'12345')
    assert send(api,identity).status_code==507
    assert begin(api,key='another').status_code==507
    assert uploads.repository.get(identity)['parts'][0]['sha256'] is None
    assert state['puts']==[]


def test_upload_intake_registers_real_core_then_catalogue_enrolls_without_asr(core_env,tmp_path):
    from fastapi.testclient import TestClient
    from hr_materials_core.app import create_app as core_app
    from hr_materials_core.auth import Principal, Tokens
    from test_core import PORTAL, auth
    _,repo,store,service,principals,connect=core_env
    source_token='z'*40
    with connect() as db:
        db.execute("INSERT INTO em_vacancies VALUES ('v1','w1',NULL)")
        db.execute("INSERT INTO em_intake_candidates VALUES ('draft1','w1','v1','{}')")
        db.execute('''CREATE TABLE hr_recording_uploads(id TEXT PRIMARY KEY,capture_identity TEXT,
            owner_kind TEXT,owner_id TEXT,connection_id TEXT,revoked_at REAL)''')
        db.execute("INSERT INTO hr_recording_uploads VALUES ('upload1','product-upload:upload1','intake','draft1','upload',NULL)")
    service.storage_registry[('upload','upload')]=store
    principals={**principals,source_token:Principal('upload','source','upload','upload')}
    with TestClient(core_app(service=service,tokens=Tokens(principals))) as core:
        response=core.post('/v1/bindings',headers=auth(PORTAL),json={'schema_version':1,
            'owner_kind':'intake','owner_id':'draft1','provider':'upload','connection_id':'upload','storage_policy':'owned',
            'source_ref':{'room_id':'upload1','room_name':'upload1','capture_instance_id':'product-upload:upload1'}})
        assert response.status_code==200,response.text
        binding=response.json()
        settings=Settings(tmp_path/'source-real','http://localhost:8093','http://localhost:8106','a'*40,
            {'portal':Caller(TOKEN,PORTAL)},{('upload','upload'):source_token},auto_enabled=True,
            auto_caller='portal',auto_activated_at=1.0)
        def put_file(path,**kwargs):
            data=Path(path).read_bytes()
            assert hashlib.sha256(data).hexdigest()==kwargs['expected_sha256']
            return store.head(store.add(data,kwargs['content_type'])['object_key'])
        store.put_file=put_file
        runtime=Runtime(settings,core=core,asr=object(),storage=store,publish=lambda _:None)
        app=create_app(settings,runtime)
        app.state.uploads.probe=lambda *args:{'kind':'audio','format':'wav','duration':0.1}
        api=TestClient(app)
        event={'owner_kind':'intake','owner_id':'draft1','binding_id':binding['binding_id'],'generation':binding['generation']}
        response=api.post('/v1/uploads',headers={'Authorization':'Bearer '+TOKEN,'Idempotency-Key':'real-core'},
            json={**event,'files':[{'size':4,'content_type':'audio/wav'}]*2})
        assert response.status_code==202,response.text
        identity=response.json()['id']
        assert send(api,identity,b'one!',0).status_code==200
        assert send(api,identity,b'two!',1).status_code==200
        response=finalize(api,identity)
        assert response.status_code==200,response.text
        assert runtime.repository.due()==[]  # Registration itself never enrolls ASR.
        material=core.get('/v1/owners/intake/draft1/materials',headers=auth(PORTAL)).json()
        assert sorted(a['metadata']['part_index'] for a in material['assets'])==[0,1]
        assert material['channels'][0]['acquisition_complete'] is True
        assert runtime.reconcile()['automatic']['accepted']==2
        assert len(runtime.repository.due())==2
        assert runtime.reconcile()['automatic']['accepted']==0
