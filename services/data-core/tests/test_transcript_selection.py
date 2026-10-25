"""Explicit immutable primary versions: CAS, acknowledgement replay and ACL."""
import copy
from dataclasses import asdict
import json
import concurrent.futures

import pytest
from materials_capture import Source
from materials_repository import MaterialsRepository
from test_core import env, auth, binding, post, receipt, transcript, video, PORTAL, SCOPED, SOURCE, OTHER


def url(binding_id, recording_id):
    return f'/v1/bindings/{binding_id}/recordings/{recording_id}/transcript-selection'


def select(client, binding_id, recording_id, *, request='job:one', revision=0, status='pending', generation=1,
           transcript_id=None, error_code=None, token=SOURCE):
    return client.put(url(binding_id,recording_id),headers=auth(token),json={
        'generation':generation,'request_id':request,'expected_revision':revision,'status':status,
        'transcript_id':transcript_id,'error_code':error_code})


def materials(client):
    return client.get('/v1/owners/interview/i1/materials',headers=auth()).json()


def primary(client,token=PORTAL,owner='i1'):
    return client.get(f'/v1/owners/interview/{owner}/primary-transcripts',headers=auth(token))


def seed(env, *, owner='i1', external='recording-one', linked=False):
    client,repo,store,*_=env
    identity=binding(client,owner=owner).json()['binding_id']
    recording=post(client,identity,receipt(video(external),receipt_id='video:'+owner)).json()['asset_ids'][0]
    text=transcript(store,'Explicit immutable speech')
    if owner!='i1': text['external_id']='transcript:'+owner
    if linked:
        text['transcript']['provenance'].update(input_asset_id=recording,input_sha256='a'*64)
    published=post(client,identity,receipt(text,receipt_id='text:'+owner,transcript_status='ready',transcription_status='succeeded'))
    assert published.status_code==200,published.text
    return identity,recording,published.json()['transcript_ids'][0]


def test_primary_is_explicit_and_replays_do_not_reset_terminal(env):
    client,*_=env
    identity,recording,version=seed(env)
    assert primary(client).json()['status']=='not_requested'
    assert primary(client).json()['transcripts']==[]
    pending=select(client,identity,recording)
    assert pending.status_code==200,pending.text
    assert pending.json()['revision']==1
    assert primary(client).json()['status']=='processing'
    assert primary(client).json()['transcripts']==[]
    chosen=select(client,identity,recording,status='ready',transcript_id=version)
    assert chosen.status_code==200,chosen.text
    value=chosen.json()
    assert value['revision']==1 and value['transcript_id']==version
    assert select(client,identity,recording,status='ready',transcript_id=version).json()==value
    # ACK loss during pending registration must preserve the terminal success.
    assert select(client,identity,recording).json()==value
    assert select(client,identity,recording,status='failed',error_code='asr_failed').status_code==409
    assert select(client,identity,recording,status='ready',transcript_id=version,revision=1).status_code==409
    read=primary(client).json()
    assert read['status']=='ready'
    assert [item['id'] for item in read['transcripts']]==[version]
    assert read['selections']==[value]
    assert read['transcripts'][0]['content_sha256']
    assert materials(client)['transcript_selections']==[value]
    dossier=client.get('/v1/candidates/c1/dossier',headers=auth()).json()
    assert dossier['interviews'][0]['materials']['transcript_selections']==[value]


def test_new_request_requires_cas_pending_and_late_response_cannot_replace_it(env):
    client,*_=env
    identity,recording,version=seed(env)
    select(client,identity,recording)
    select(client,identity,recording,status='ready',transcript_id=version)
    assert select(client,identity,recording,request='job:two',revision=1,status='ready',transcript_id=version).status_code==409
    assert select(client,identity,recording,request='job:two',revision=0).status_code==409
    newer=select(client,identity,recording,request='job:two',revision=1)
    assert newer.json()['revision']==2 and newer.json()['transcript_id'] is None
    assert primary(client).json()['status']=='processing'
    assert primary(client).json()['transcripts']==[]
    assert select(client,identity,recording,status='ready',transcript_id=version).status_code==409
    assert select(client,identity,recording).status_code==409
    assert select(client,identity,recording,revision=2).json()['detail']=='selection_request_superseded'
    failed=select(client,identity,recording,request='job:two',revision=1,status='failed',error_code='asr_failed')
    assert failed.status_code==200,failed.text
    assert failed.json()['revision']==2
    assert select(client,identity,recording,request='job:two',revision=1).json()==failed.json()
    assert select(client,identity,recording,request='job:two',revision=1,status='failed',error_code='asr_failed').json()==failed.json()
    assert primary(client).json()['status']=='error'
    assert primary(client).json()['transcripts']==[]


def test_source_principal_and_portal_owner_scopes(env):
    client,*_=env
    identity,recording,version=seed(env)
    assert select(client,identity,recording,token=PORTAL).status_code==403
    assert select(client,identity,recording,token=OTHER).status_code==403
    assert client.put(url(identity,recording),json={}).status_code==401
    assert primary(client,token=SOURCE).status_code==403
    assert primary(client,token=SCOPED).status_code==200
    assert primary(client,token=SCOPED,owner='i2').status_code==403
    foreign,foreign_recording,foreign_version=seed(env,owner='i2',external='other-owner-recording')
    assert select(client,identity,foreign_recording).status_code==409
    select(client,identity,recording)
    assert select(client,identity,recording,status='ready',transcript_id=foreign_version).status_code==409
    assert select(client,identity,version).status_code==404  # version is not an asset/recording


@pytest.mark.parametrize('status', ['failed','uncertain'])
def test_failure_selection_has_no_implicit_provider_fallback(env,status):
    client,*_=env
    identity,recording,version=seed(env)
    select(client,identity,recording)
    response=select(client,identity,recording,status=status,error_code='provider_outcome_unknown')
    assert response.status_code==200
    assert primary(client).json()['status']=='error'
    assert primary(client).json()['transcripts']==[]
    value=materials(client)
    assert [item['id'] for item in value['transcripts']]==[version]  # retained immutable variant
    assert value['transcript_selections'][0]['transcript_id'] is None
    assert value['transcript_selection_required_recording_ids']==[recording]


def test_provenance_binds_exact_input_and_known_sha(env):
    client,repo,store,*_=env
    identity=binding(client).json()['binding_id']
    location={**store.add(b'input-bytes'),'state':'verified'}
    record=post(client,identity,receipt(video(location=location))).json()['asset_ids'][0]
    second=post(client,identity,receipt(video('second-input'),receipt_id='second-video')).json()['asset_ids'][0]
    text=transcript(store)
    text['transcript']['provenance'].update(input_asset_id=record,input_sha256=location['sha256'])
    version=post(client,identity,receipt(text,receipt_id='linked-text',transcript_status='ready')).json()['transcript_ids'][0]
    select(client,identity,record)
    assert select(client,identity,record,status='ready',transcript_id=version).status_code==200
    select(client,identity,second)
    assert select(client,identity,second,status='ready',transcript_id=version).json()['detail']=='transcript_recording_conflict'
    wrong=copy.deepcopy(text);wrong['external_id']='wrong-sha';wrong['transcript']['provenance']['input_sha256']='a'*64
    wrong_id=post(client,identity,receipt(wrong,receipt_id='wrong-sha-text')).json()['transcript_ids'][0]
    select(client,identity,record,request='wrong-sha',revision=1)
    assert select(client,identity,record,request='wrong-sha',revision=1,status='ready',transcript_id=wrong_id).json()['detail']=='transcript_input_sha256_conflict'


def test_legacy_transcript_requires_unambiguous_single_recording(env):
    client,*_=env
    identity,recording,version=seed(env)
    select(client,identity,recording)
    assert select(client,identity,recording,status='ready',transcript_id=version).status_code==200
    post(client,identity,receipt(video('another-recording'),receipt_id='another-video'))
    select(client,identity,recording,request='new',revision=1)
    bad=select(client,identity,recording,request='new',revision=1,status='ready',transcript_id=version)
    assert bad.status_code==409 and bad.json()['detail']=='ambiguous_transcript_recording'
    # Existing selection's formerly unambiguous target also fails closed on read.
    assert primary(client).json()['transcripts']==[]


def test_generation_change_hides_stale_choice_but_retains_enrollment(env):
    client,repo,store,service,principals,connect=env
    identity,recording,version=seed(env)
    select(client,identity,recording)
    select(client,identity,recording,status='ready',transcript_id=version)
    with repo._db(write=True) as db: repo._invalidate(db,identity)
    view=materials(client)
    assert view['transcript_selections']==[]
    assert view['transcript_selection_required_recording_ids']==[recording]
    assert primary(client).json()['status']=='processing'
    assert primary(client).json()['transcripts']==[]
    assert select(client,identity,recording,status='ready',transcript_id=version).json()['detail']=='stale_generation'
    new=select(client,identity,recording,generation=2,request='generation-two')
    assert new.status_code==200 and new.json()['revision']==1
    assert select(client,identity,recording,generation=2,request='generation-two',status='ready',transcript_id=version).json()['detail']=='transcript_not_confirmed'
    # Same immutable version can be confirmed by a new-generation Core receipt.
    confirmation=post(client,identity,receipt(transcript(store,'Explicit immutable speech'),generation=2,receipt_id='new-gen-confirmation'))
    assert confirmation.json()['transcript_ids']==[version]
    assert select(client,identity,recording,generation=2,request='generation-two',status='ready',transcript_id=version).status_code==200
    assert primary(client).json()['status']=='ready'


def test_root_binding_selects_confirmed_child_material_and_revoke_hides_it(env):
    client,repo,store,service,principals,connect=env
    legacy=MaterialsRepository(connect)
    root=legacy.ensure_source('interview','i1','daily','daily',{'room_id':'r1','room_name':'hrv-i1','capture_instance_id':'cap1'})
    child=legacy.ensure_source('interview','i1','daily','daily',asdict(Source('daily','daily','r1','hrv-i1','historical','cap1')))
    with connect() as db:db.execute('UPDATE media_sources SET parent_source_id=? WHERE id=?',(root['id'],child['id']))
    old=legacy.upsert_asset(child['id'],'video','child-video','ready')
    legacy.upsert_asset(child['id'],'transcript','transcript-one','ready')
    identity=binding(client).json()['binding_id']
    result=post(client,identity,receipt(video('child-video'),transcript(store),transcript_status='ready')).json()
    version=result['transcript_ids'][0]
    select(client,identity,old['id'])
    assert select(client,identity,old['id'],status='ready',transcript_id=version).status_code==200
    repo.suspend_source(identity)
    assert materials(client)['transcript_selections']==[]
    assert materials(client)['transcript_selection_required_recording_ids']==[]
    assert primary(client).status_code==410
    assert select(client,identity,old['id']).status_code==410


def test_revoked_transcript_child_is_not_selected_from_active_recording(env):
    client,repo,store,service,principals,connect=env
    identity=binding(client).json()['binding_id']
    record=post(client,identity,receipt(video())).json()['asset_ids'][0]
    child=repo.ensure_source('interview','i1','daily','daily',asdict(Source('daily','daily','r1','hrv-i1','historical','cap1')))
    with connect() as db:db.execute('UPDATE media_sources SET parent_source_id=? WHERE id=?',(identity,child['id']))
    repo.upsert_asset(child['id'],'transcript','transcript-one','ready')
    version=post(client,identity,receipt(transcript(store),receipt_id='child-text')).json()['transcript_ids'][0]
    select(client,identity,record)
    assert select(client,identity,record,status='ready',transcript_id=version).status_code==200
    repo.suspend_source(child['id'])
    read=primary(client)
    assert read.status_code==200 and read.json()['status']=='error'
    assert read.json()['transcripts']==[]
    assert materials(client)['transcript_selections']==[]
    assert materials(client)['transcript_selection_required_recording_ids']==[record]


def test_concurrent_new_requests_have_one_cas_winner(env):
    client,repo,store,service,principals,connect=env
    identity,recording,version=seed(env)
    from hr_materials_core.schemas import TranscriptSelectionInput
    def claim(index):
        try:
            value=repo.select_transcript(principals[SOURCE],identity,recording,TranscriptSelectionInput(
                generation=1,request_id=f'job:{index}',expected_revision=0,status='pending'))
            return value['request_id']
        except Exception as error:
            assert getattr(error,'code',None)=='selection_revision_conflict'
            return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool: results=list(pool.map(claim,range(5)))
    assert sum(item is not None for item in results)==1


def test_wire_rejects_invalid_selection_and_unpaired_provenance(env):
    client,repo,store,*_=env
    identity,recording,version=seed(env)
    body={'generation':1,'request_id':'job:one','expected_revision':0,'status':'pending','transcript_id':None,'error_code':None}
    for patch in ({'generation':True},{'expected_revision':True},{'status':'ready'},
                  {'transcript_id':version},{'error_code':'private provider message'}, {'external':'secret'}):
        assert client.put(url(identity,recording),headers=auth(SOURCE),json={**body,**patch}).status_code==422
    text=transcript(store);text['transcript']['provenance']['input_asset_id']=recording
    assert post(client,identity,receipt(text,receipt_id='unpaired')).status_code==422


def test_current_generation_collector_version_without_core_confirmation_is_rejected(env):
    client,repo,store,*_=env
    identity=binding(client).json()['binding_id']
    record=post(client,identity,receipt(video())).json()['asset_ids'][0]
    asset=repo.upsert_asset(identity,'transcript','collector-local','ready')
    version=repo.save_transcript(asset['id'],{'backend':'s3','object_key':'materials/local'},
        [{'text':'Unconfirmed','start_ms':None,'end_ms':None,'speaker':None}])
    select(client,identity,record)
    response=select(client,identity,record,status='ready',transcript_id=version['id'])
    assert response.status_code==409 and response.json()['detail']=='transcript_not_confirmed'


def test_meeting_primary_uses_authoritative_application_owner(env):
    client,repo,store,*_=env
    identity=binding(client,owner='m1').json()['binding_id']
    result=post(client,identity,receipt(video('meeting-video'),transcript(store),transcript_status='ready')).json()
    record=result['asset_ids'][0]; version=result['transcript_ids'][0]
    select(client,identity,record)
    assert select(client,identity,record,status='ready',transcript_id=version).status_code==200
    response=client.get('/v1/owners/meeting/m1/primary-transcripts',headers=auth(SCOPED))
    assert response.status_code==200
    assert response.json()['owner_kind']=='meeting' and response.json()['status']=='ready'
    assert [item['id'] for item in response.json()['transcripts']]==[version]


def test_pending_replay_does_not_acknowledge_invalid_ready_reference(env):
    client,repo,store,service,principals,connect=env
    identity,record,version=seed(env)
    select(client,identity,record)
    select(client,identity,record,status='ready',transcript_id=version)
    with connect() as db:
        db.execute('UPDATE media_assets SET state=? WHERE id=(SELECT asset_id FROM transcript_versions WHERE id=?)',('error',version))
    assert select(client,identity,record).status_code==409
    assert primary(client).json()['status']=='error'
    assert materials(client)['transcript_selection_required_recording_ids']==[record]


def test_invalid_target_hides_content_but_keeps_safe_cas_revision_for_new_request(env):
    client,repo,store,*_=env
    identity,record,version=seed(env)
    select(client,identity,record)
    select(client,identity,record,status='ready',transcript_id=version)
    post(client,identity,receipt(video('second-recording'),receipt_id='second-video'))
    read=materials(client)
    assert read['transcript_selections']==[]
    assert read['transcript_selection_required_recording_ids']==[record]
    assert read['transcript_selection_revisions']==[{'binding_id':identity,'generation':1,'recording_id':record,'revision':1}]
    assert primary(client).json()['status']=='error'
    assert primary(client).json()['transcripts']==[]
    newer=select(client,identity,record,request='repair',revision=read['transcript_selection_revisions'][0]['revision'])
    assert newer.status_code==200 and newer.json()['revision']==2
    assert primary(client).json()['status']=='processing'
    with repo._db(write=True) as db: repo._invalidate(db,identity)
    assert materials(client)['transcript_selection_revisions']==[]
    assert materials(client)['transcript_selection_required_recording_ids']==[record]
    repo.suspend_source(identity)
    assert materials(client)['transcript_selection_revisions']==[]
    assert materials(client)['transcript_selection_required_recording_ids']==[]
