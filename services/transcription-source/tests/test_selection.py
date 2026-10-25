"""Primary selection uses real Core CAS and persisted source checkpoints."""
import json

import httpx
import pytest

from test_core import SCOPED,SOURCE,auth
from test_pipeline import setup,submit
from hr_transcription_source.repository import Repository
from hr_transcription_source.worker import Runtime


def primary(core,event):
    response=core.get('/v1/owners/'+event['owner_kind']+'/'+event['owner_id']+'/primary-transcripts',headers=auth(SCOPED))
    assert response.status_code==200,response.text
    return response.json()


def restart(runtime):
    return Runtime(runtime.settings,core=runtime.core,asr=runtime.asr,download=runtime.download,
        storage=runtime.storage,publish=lambda _:None)


def test_primary_flag_default_off_does_not_change_asr_identity(setup):
    api,runtime,event,state,core,_=setup
    identity=runtime.settings.config_identity
    assert runtime.settings.select_primary is False
    job=submit(api,event).json()['id']
    assert runtime.run_once(job)['delivery_status']=='succeeded'
    assert primary(core,event)['status']=='not_requested'
    runtime.settings.select_primary=True
    assert runtime.settings.config_identity==identity
    assert submit(api,event).json()['id']==job
    result=runtime.run_once(job)
    assert result['selection_status']=='ready',result
    assert state['submits']==1


def test_pending_is_durable_before_asr_and_ready_follows_receipt_readback(setup,monkeypatch):
    api,runtime,event,state,core,_=setup
    runtime.settings.select_primary=True
    state['asr_status']='running'
    job=submit(api,event).json()['id']
    original=runtime._json
    def inspect(client,method,path,token,**kwargs):
        if method=='PUT':
            assert runtime.repository.get(job)['selection_revision']==0
            assert kwargs['json']['expected_revision']==0
        if method=='POST' and path.startswith('/v1/jobs'):
            view=primary(core,event)
            assert view['status']=='processing'
            assert view['selections'][0]['status']=='pending'
            assert view['transcripts']==[]
        return original(client,method,path,token,**kwargs)
    monkeypatch.setattr(runtime,'_json',inspect)
    result=runtime.run_once(job)
    assert result['selection_requested']==1
    assert result['selection_status']=='pending'
    assert result['selection_revision']==0
    state['asr_status']='succeeded'
    fresh=restart(runtime)
    result=fresh.run_once(job)
    assert result['selection_status']=='ready',result
    view=primary(core,event)
    assert view['status']=='ready'
    assert [t['id'] for t in view['transcripts']]==result['transcript_ids']
    assert view['selections'][0]['revision']==1
    assert state['submits']==1


def test_pending_ack_loss_reuses_original_base_before_any_asr_payment(setup,monkeypatch):
    api,runtime,event,state,core,_=setup
    runtime.settings.select_primary=True
    job=submit(api,event).json()['id']
    original=runtime._json
    calls=[]
    def lost(client,method,path,token,**kwargs):
        response=original(client,method,path,token,**kwargs)
        if method=='PUT' and kwargs['json']['status']=='pending':
            calls.append(kwargs['json'].copy())
            if len(calls)==1:
                raise httpx.ReadTimeout('lost pending ack')
        return response
    monkeypatch.setattr(runtime,'_json',lost)
    assert runtime.run_once(job)['selection_status']=='requested'
    assert state['submits']==0
    assert runtime.repository.get(job)['selection_revision']==0
    assert primary(core,event)['selections'][0]['revision']==1
    assert runtime.run_once(job)['selection_status']=='ready'
    assert calls[0]==calls[1]
    assert state['submits']==1


def test_ready_ack_loss_restarts_delivery_and_selection_without_asr(setup,monkeypatch):
    api,runtime,event,state,core,_=setup
    runtime.settings.select_primary=True
    job=submit(api,event).json()['id']
    original=runtime._json
    def lost(client,method,path,token,**kwargs):
        result=original(client,method,path,token,**kwargs)
        if method=='PUT' and kwargs['json']['status']=='ready':
            raise httpx.ReadTimeout('lost terminal ack')
        return result
    monkeypatch.setattr(runtime,'_json',lost)
    result=runtime.run_once(job)
    assert result['delivery_status']=='succeeded'
    assert result['selection_status']=='pending'
    assert primary(core,event)['status']=='ready'
    assert runtime.repository.due()==[job]
    fresh=restart(runtime)
    assert fresh.run_once(job)['selection_status']=='ready'
    assert fresh.repository.due()==[]
    assert state['submits']==1 and state['downloaded']==1


@pytest.mark.parametrize('outcome',['failed','uncertain'])
def test_terminal_asr_outcome_is_published_and_never_paid_retried(setup,outcome,monkeypatch):
    api,runtime,event,state,core,_=setup
    runtime.settings.select_primary=True
    state['asr_status']=outcome
    job=submit(api,event).json()['id']
    original=runtime._json
    calls=[]
    def unavailable(client,method,path,token,**kwargs):
        if method=='PUT' and kwargs['json']['status']==outcome:
            calls.append(kwargs['json'].copy())
            if len(calls)==1:
                raise httpx.ConnectError('temporary Core outage')
        return original(client,method,path,token,**kwargs)
    monkeypatch.setattr(runtime,'_json',unavailable)
    result=runtime.run_once(job)
    assert result['transcription_status']==outcome
    assert result['selection_status']=='pending'
    assert runtime.repository.due()==[job]
    result=restart(runtime).run_once(job)
    assert result['selection_status']==outcome,result
    assert result['delivery_status']=='rejected'
    assert primary(core,event)['status']=='error'
    assert primary(core,event)['selections'][0]['status']==outcome
    assert runtime.repository.due()==[]
    assert runtime.run_once(job)=={'claimed':False}
    assert state['submits']==1 and state['downloaded']==1


def test_late_old_request_cannot_reread_base_or_replace_new_selection(setup):
    api,runtime,event,state,core,_=setup
    runtime.settings.select_primary=True
    state['asr_status']='running'
    job=submit(api,event).json()['id']
    assert runtime.run_once(job)['selection_status']=='pending'
    newer={'generation':event['generation'],'request_id':'newer-explicit-request','expected_revision':1,
        'status':'pending','transcript_id':None,'error_code':None}
    path='/v1/bindings/'+event['binding_id']+'/recordings/'+event['asset_id']+'/transcript-selection'
    response=core.put(path,headers=auth(SOURCE),json=newer)
    assert response.status_code==200,response.text
    result=restart(runtime).run_once(job)
    assert result['error_code']=='primary_selection_conflict',result
    assert result['selection_status']=='conflict'
    assert result['selection_revision']==0
    assert runtime.repository.due()==[]
    current=primary(core,event)['selections'][0]
    assert current['request_id']=='newer-explicit-request' and current['revision']==2
    assert state['submits']==1


def test_delivery_retry_keeps_selection_pending_and_reuses_result(setup):
    api,runtime,event,state,core,_=setup
    runtime.settings.select_primary=True
    state['s3_fail']=1
    job=submit(api,event).json()['id']
    result=runtime.run_once(job)
    assert result['transcription_status']=='succeeded'
    assert result['selection_status']=='pending'
    assert primary(core,event)['status']=='processing'
    result=restart(runtime).run_once(job)
    assert result['selection_status']=='ready'
    assert state['submits']==1


def test_migration_old_completed_job_reuses_exact_legacy_receipt_and_result(setup,monkeypatch):
    api,runtime,event,state,core,_=setup
    job=submit(api,event).json()['id']
    original=runtime._json
    def legacy_receipt(client,method,path,token,**kwargs):
        if method=='POST' and path.endswith('/receipts'):
            provenance=kwargs['json']['assets'][0]['transcript']['provenance']
            provenance.pop('input_asset_id',None)
            provenance.pop('input_sha256',None)
            # Model an actual pre-migration source checkpoint and its exact Core
            # receipt; no production path rewrites immutable stored receipts.
            with runtime.repository.db() as db:
                db.execute('UPDATE jobs SET receipt=? WHERE id=?',(json.dumps(kwargs['json']),job))
        return original(client,method,path,token,**kwargs)
    monkeypatch.setattr(runtime,'_json',legacy_receipt)
    assert runtime.run_once(job)['delivery_status']=='succeeded'
    with runtime.repository.db() as db:
        before=dict(db.execute('SELECT * FROM jobs WHERE id=?',(job,)).fetchone())
        for column in ('selection_requested','selection_revision','selection_status','selection_outcome'):
            db.execute('ALTER TABLE jobs DROP COLUMN '+column)
    fresh=restart(runtime)
    migrated=fresh.repository.get(job)
    assert migrated['selection_requested']==0 and migrated['selection_revision'] is None
    assert migrated['selection_status']=='not_requested'
    for field in ('asr_job_id','result','raw','raw_ref','receipt','transcript_ids'):
        with fresh.repository.db() as db:
            assert db.execute('SELECT '+field+' FROM jobs WHERE id=?',(job,)).fetchone()[0]==before[field]
    runtime.settings.select_primary=True
    response=submit(api,event)
    assert response.status_code==202 and response.json()['id']==job and response.json()['created'] is False
    result=fresh.run_once(job)
    assert result['selection_status']=='ready',result
    assert primary(core,event)['transcripts'][0]['id']==result['transcript_ids'][0]
    with fresh.repository.db() as db:
        assert db.execute('SELECT receipt FROM jobs WHERE id=?',(job,)).fetchone()[0]==before['receipt']
    assert state['submits']==1 and state['downloaded']==1


def test_rejected_input_publishes_failed_without_asr_or_stuck_pending(setup):
    api,runtime,event,state,core,_=setup
    runtime.settings.select_primary=True
    state['media']=b'corrupted recording'
    job=submit(api,event).json()['id']
    result=runtime.run_once(job)
    assert result['selection_status']=='failed',result
    assert result['transcription_status']=='failed'
    assert result['delivery_status']=='rejected'
    assert primary(core,event)['status']=='error'
    assert state['submits']==0
    assert runtime.repository.due()==[]


def test_legacy_failed_job_can_publish_failure_only_when_flag_enabled(setup):
    api,runtime,event,state,core,_=setup
    state['asr_status']='failed'
    job=submit(api,event).json()['id']
    assert runtime.run_once(job)['transcription_status']=='failed'
    assert runtime.repository.due()==[]
    runtime.settings.select_primary=True
    assert submit(api,event).json()['id']==job
    result=restart(runtime).run_once(job)
    assert result['selection_status']=='failed',result
    assert primary(core,event)['status']=='error'
    assert state['submits']==1


def test_failed_selection_intent_survives_ack_loss_after_successful_asr(setup,monkeypatch):
    from hr_transcription_source.worker import Rejected
    api,runtime,event,state,core,_=setup
    runtime.settings.select_primary=True
    job=submit(api,event).json()['id']
    def reject_delivery(*args):
        raise Rejected('upstream_rejected_422',422)
    monkeypatch.setattr(runtime,'_deliver',reject_delivery)
    original=runtime._json
    def lost(client,method,path,token,**kwargs):
        result=original(client,method,path,token,**kwargs)
        if method=='PUT' and kwargs['json']['status']=='failed':
            raise httpx.ReadTimeout('lost failed ack')
        return result
    monkeypatch.setattr(runtime,'_json',lost)
    result=runtime.run_once(job)
    assert result['transcription_status']=='succeeded'
    assert runtime.repository.get(job)['selection_outcome']=='failed'
    assert primary(core,event)['status']=='error'
    fresh=restart(runtime)
    def must_not_redeliver(*args):
        raise AssertionError('Persisted failed intent must not change to ready')
    monkeypatch.setattr(fresh,'_deliver',must_not_redeliver)
    result=fresh.run_once(job)
    assert result['selection_status']=='failed',result
    assert result['transcription_status']=='succeeded'
    assert fresh.repository.due()==[]
    assert state['submits']==1


def test_select_primary_environment_flag_is_default_off_and_not_asr_identity(setup):
    from hr_transcription_source.settings import Settings
    _,runtime,_,_,_,_=setup
    caller=runtime.settings.callers['collector']
    env={'TRANSCRIPTION_SOURCE_CORE_URL':runtime.settings.core_url,
        'TRANSCRIPTION_SOURCE_ASR_URL':runtime.settings.asr_url,
        'TRANSCRIPTION_SOURCE_ASR_TOKEN':runtime.settings.asr_token,
        'TRANSCRIPTION_SOURCE_CALLERS_JSON':json.dumps({'collector':{'token':caller.token,'core_read_token':caller.core_read_token}}),
        'TRANSCRIPTION_SOURCE_CORE_SOURCES_JSON':json.dumps([{'provider':'daily','connection_id':'daily','token':SOURCE}]),
        'TRANSCRIPTION_SOURCE_S3_BUCKET':'fixture'}
    disabled=Settings.from_env(env)
    enabled=Settings.from_env({**env,'TRANSCRIPTION_SOURCE_SELECT_PRIMARY':'1'})
    assert disabled.select_primary is False
    assert enabled.select_primary is True
    assert disabled.config_identity==enabled.config_identity


def test_cas_base_survives_crash_before_first_pending_put(setup,monkeypatch):
    api,runtime,event,state,core,_=setup
    runtime.settings.select_primary=True
    job=submit(api,event).json()['id']
    original=runtime._json
    def crash(client,method,path,token,**kwargs):
        if method=='PUT':
            assert runtime.repository.get(job)['selection_revision']==0
            raise httpx.ConnectError('process stopped before PUT')
        return original(client,method,path,token,**kwargs)
    monkeypatch.setattr(runtime,'_json',crash)
    assert runtime.run_once(job)['selection_status']=='requested'
    assert state['submits']==0
    newer={'generation':event['generation'],'request_id':'other-after-crash','expected_revision':0,
        'status':'pending','transcript_id':None,'error_code':None}
    path='/v1/bindings/'+event['binding_id']+'/recordings/'+event['asset_id']+'/transcript-selection'
    assert core.put(path,headers=auth(SOURCE),json=newer).status_code==200
    result=restart(runtime).run_once(job)
    assert result['selection_status']=='conflict'
    assert result['selection_revision']==0
    assert state['submits']==0


def test_invalid_hidden_selection_uses_safe_revision_marker_to_repair_without_asr(setup):
    api,runtime,event,state,core,connect=setup
    job=submit(api,event).json()['id']
    result=runtime.run_once(job)
    transcript_id=result['transcript_ids'][0]
    path='/v1/bindings/'+event['binding_id']+'/recordings/'+event['asset_id']+'/transcript-selection'
    previous={'generation':event['generation'],'request_id':'previous-selection','expected_revision':0,
        'status':'pending','transcript_id':None,'error_code':None}
    assert core.put(path,headers=auth(SOURCE),json=previous).status_code==200
    assert core.put(path,headers=auth(SOURCE),json={**previous,'status':'ready','transcript_id':transcript_id}).status_code==200
    # Model damaged historical linkage. Core must hide its selected content but
    # retain safe current-recording CAS metadata for an explicit repair request.
    with connect() as db:
        db.execute('UPDATE core_transcript_selections SET transcript_id=NULL WHERE binding_id=?',(event['binding_id'],))
    materials=core.get('/v1/owners/interview/i1/materials',headers=auth(SCOPED)).json()
    assert materials['transcript_selections']==[]
    assert materials['transcript_selection_revisions'][0]['revision']==1
    runtime.settings.select_primary=True
    assert submit(api,event).json()['id']==job
    result=restart(runtime).run_once(job)
    assert result['selection_status']=='ready',result
    assert result['selection_revision']==1
    assert primary(core,event)['selections'][0]['revision']==2
    assert primary(core,event)['transcripts'][0]['id']==transcript_id
    assert state['submits']==1


def test_valid_selection_fallback_for_core_without_revision_markers(setup,monkeypatch):
    api,runtime,event,state,core,_=setup
    path='/v1/bindings/'+event['binding_id']+'/recordings/'+event['asset_id']+'/transcript-selection'
    assert core.put(path,headers=auth(SOURCE),json={'generation':event['generation'],'request_id':'prior',
        'expected_revision':0,'status':'pending','transcript_id':None,'error_code':None}).status_code==200
    original=runtime._json
    def legacy_core(client,method,path,token,**kwargs):
        payload,raw=original(client,method,path,token,**kwargs)
        if client is runtime.core and method=='GET' and path.endswith('/materials'):
            payload.pop('transcript_selection_revisions',None)
        return payload,raw
    monkeypatch.setattr(runtime,'_json',legacy_core)
    runtime.settings.select_primary=True
    job=submit(api,event).json()['id']
    result=runtime.run_once(job)
    assert result['selection_status']=='ready',result
    assert result['selection_revision']==1
    assert primary(core,event)['selections'][0]['revision']==2
    assert state['submits']==1


def test_auto_reused_completed_manual_job_selects_saved_result_after_config_change(setup):
    api,runtime,event,state,core,_=setup
    identity=submit(api,event).json()['id']
    done=runtime.run_once(identity)
    assert done['delivery_status']=='succeeded'
    original=runtime.repository.get(identity)
    runtime.settings.revision='new-model-configuration'
    runtime.settings.asr_provider='deepgram'
    runtime.settings.select_primary=True
    reused,created=runtime.repository.submit('collector','auto:'+event['asset_id'],event,runtime.settings.config_identity,
        automatic=True,select_primary=True)
    assert not created and reused['id']==identity and reused['selection_requested']
    result=runtime.run_once(identity)
    assert result['selection_status']=='ready',result
    assert state['submits']==1
    persisted=runtime.repository.get(identity)
    assert persisted['receipt']==original['receipt']
    assert persisted['raw_ref']==original['raw_ref']
    assert persisted['result']==original['result']
    assert primary(core,event)['transcripts'][0]['id']==done['transcript_ids'][0]
