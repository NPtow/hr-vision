"""Automatic enrollment uses authorized catalogue IDs and no provider calls."""
from dataclasses import replace
import json

import httpx
import pytest

from hr_transcription_source.catalogue import Catalogue
from hr_transcription_source.repository import Repository
from hr_transcription_source.settings import Settings, Caller
from hr_transcription_source.worker import Runtime, Retryable, Rejected


def item(identity='asset1', **changes):
    return {'owner_kind':'intake','owner_id':'draft1','binding_id':'binding1','generation':1,
        'asset_id':identity,'created_at':1100.0,'kind':'audio','state':'ready','storage_policy':'owned',
        'provider':'upload','connection_id':'upload',**changes}


def fixture(tmp_path, **changes):
    settings=Settings(tmp_path,'http://localhost:8093','http://localhost:8106','a'*40,
        {'collector':Caller('p'*40,'r'*40)},{('upload','upload'):'s'*40},auto_enabled=True,auto_activated_at=1000.0,
        auto_pages_per_tick=1,**changes)
    state={'pages':{None:{'items':[item()],'next_cursor':None}},'calls':[],'enqueues':[],'authorizations':[]}
    def core(request):
        state['calls'].append(request)
        assert request.url.path=='/v1/assets'
        assert request.headers['authorization']=='Bearer '+'r'*40
        cursor=request.url.params.get('cursor')
        assert float(request.url.params['created_since'])==1000.0
        return httpx.Response(200,content=json.dumps(state['pages'][cursor]))
    runtime=Runtime(settings,core=httpx.Client(base_url='http://core.test',transport=httpx.MockTransport(core)),
        storage=object(),publish=state['enqueues'].append)
    def authorize(caller,event):
        state['authorizations'].append(event)
        return {},{}, {},'write-token'
    runtime.authorize=authorize
    return runtime,state


def test_new_ready_owned_asset_enrolls_once_across_generation_revision_restart(tmp_path):
    runtime,state=fixture(tmp_path)
    assert Catalogue(runtime).sweep()['accepted']==1
    job_id=state['enqueues'][0]
    assert runtime.repository.get(job_id)['event']['owner_kind']=='intake'
    runtime.repository=Repository(runtime.repository.path)
    runtime.settings=replace(runtime.settings,revision='changed-provider-configuration')
    state['pages'][None]={'items':[item(generation=2)],'next_cursor':None}
    assert Catalogue(runtime).sweep()=={'enabled':True,'accepted':0,'skipped':1}
    assert runtime.repository.get(job_id)['revision']=='yandex_speechkit:speechkit-v1'
    assert len(state['enqueues'])==1 and len(state['authorizations'])==1
    with runtime.repository.db() as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==1


def test_activation_excludes_history_pending_to_ready_not_missed_by_keyset(tmp_path):
    runtime,state=fixture(tmp_path)
    state['pages']={None:{'items':[item('old',created_at=999),item('pending',state='pending')],'next_cursor':'page2'},
        'page2':{'items':[item('new')],'next_cursor':None}}
    assert Catalogue(runtime).sweep()['accepted']==0
    assert runtime.repository.catalogue('collector',1000)['cursor']=='page2'
    assert Catalogue(runtime).sweep()['accepted']==1
    state['pages'][None]={'items':[item('old',created_at=999),item('pending',state='ready')],'next_cursor':None}
    assert Catalogue(runtime).sweep()['accepted']==1
    with runtime.repository.db() as db:
        events=[json.loads(row[0]) for row in db.execute('SELECT event FROM jobs')]
    assert {event['asset_id'] for event in events}=={'new','pending'}
    with pytest.raises(ValueError,match='cannot_move_backwards'):
        runtime.repository.catalogue('collector',999)


def test_page_crash_replays_already_committed_enrollment_without_new_job(tmp_path):
    runtime,state=fixture(tmp_path)
    state['pages'][None]={'items':[item('first'),item('second')],'next_cursor':None}
    original=runtime.authorize
    def broken(caller,event):
        if event['asset_id']=='second':
            raise Retryable('core unavailable')
        return original(caller,event)
    runtime.authorize=broken
    with pytest.raises(Retryable):
        Catalogue(runtime).sweep()
    assert runtime.repository.enrolled('collector','first')
    assert runtime.repository.catalogue('collector',1000)['cursor'] is None
    runtime.authorize=original
    assert Catalogue(runtime).sweep()['accepted']==1
    assert len(set(state['enqueues']))==2


def test_manual_job_is_reused_even_under_changed_configuration(tmp_path):
    runtime,state=fixture(tmp_path)
    event={key:item()[key] for key in ('owner_kind','owner_id','binding_id','generation','asset_id')}
    job,_=runtime.repository.submit('collector','manual',event,'old-provider:model-v1')
    assert Catalogue(runtime).sweep()['accepted']==0
    assert runtime.repository.enrolled('collector','asset1')==job['id']
    assert state['enqueues']==[job['id']]
    with runtime.repository.db() as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==1


def test_only_server_configured_owned_sources_and_explicit_activation(tmp_path):
    runtime,state=fixture(tmp_path)
    state['pages'][None]={'items':[item('remote',storage_policy='remote'),item('foreign',connection_id='other'),
        item('text',kind='transcript'),item('gone',state='error')],'next_cursor':None}
    assert Catalogue(runtime).sweep()['accepted']==0
    assert state['authorizations']==[]
    runtime.settings=replace(runtime.settings,auto_enabled=False)
    assert Catalogue(runtime).sweep()=={'enabled':False}
    assert len(state['calls'])==1
    with pytest.raises(ValueError,match='activation'):
        replace(runtime.settings,auto_enabled=True,auto_activated_at=None)


def test_concurrent_enrollment_transaction_and_cursor_fencing(tmp_path):
    runtime,state=fixture(tmp_path)
    event={key:item()[key] for key in ('owner_kind','owner_id','binding_id','generation','asset_id')}
    first,created=runtime.repository.submit('collector','auto:asset1',event,'first:configuration',automatic=True)
    second,created2=Repository(runtime.repository.path).submit('collector','otherkey',{**event,'generation':2},'second:configuration',automatic=True)
    assert created and not created2 and first['id']==second['id']
    runtime.repository.catalogue_cursor('collector',1000,None,'page2')
    from hr_transcription_source.repository import Conflict
    with pytest.raises(Conflict):
        runtime.repository.catalogue_cursor('collector',1000,None,'stale-writer')


def test_invalid_or_unavailable_page_keeps_cursor_and_existing_job_reconciliation(tmp_path):
    runtime,state=fixture(tmp_path)
    state['pages'][None]={'items':[item(created_at=float('nan'))],'next_cursor':None}
    with pytest.raises(Retryable):
        Catalogue(runtime).sweep()
    assert runtime.repository.catalogue('collector',1000)['cursor'] is None
    event={key:item()[key] for key in ('owner_kind','owner_id','binding_id','generation','asset_id')}
    job,_=runtime.repository.submit('collector','manual',event,runtime.settings.config_identity)
    result=runtime.reconcile()
    assert job['id'] in state['enqueues']
    assert result['automatic']['error_code']=='catalogue_unavailable'


def test_reused_manual_job_gets_durable_selection_opt_in(tmp_path):
    runtime,state=fixture(tmp_path,select_primary=True)
    event={key:item()[key] for key in ('owner_kind','owner_id','binding_id','generation','asset_id')}
    prior,_=runtime.repository.submit('collector','manual',event,'old:configuration')
    Catalogue(runtime).sweep()
    job=runtime.repository.get(prior['id'])
    assert job['selection_requested']==1 and job['selection_status']=='requested'
    assert runtime.repository.enrolled('collector','asset1')==prior['id']


def test_primary_later_enable_awakens_same_auto_job_without_another_enrollment(tmp_path):
    runtime,state=fixture(tmp_path)
    Catalogue(runtime).sweep()
    identity=state['enqueues'][0]
    assert not runtime.repository.get(identity)['selection_requested']
    runtime.settings=replace(runtime.settings,select_primary=True)
    Catalogue(runtime).sweep()
    assert runtime.repository.get(identity)['selection_requested']==1
    assert state['enqueues']==[identity,identity]
    Catalogue(runtime).sweep()
    assert state['enqueues']==[identity,identity]
