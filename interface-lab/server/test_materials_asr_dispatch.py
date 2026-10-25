import pytest
from materials_asr_dispatch import ASRDispatch,from_env


@pytest.mark.parametrize('activation',[float('nan'),float('inf'),float('-inf'),0,-1])
def test_invalid_activation_cannot_enable_historical_paid_requests(activation):
    with pytest.raises(ValueError):
        ASRDispatch('http://127.0.0.1:8107','x'*40,['zoom'],activation)


def test_disabled_by_default_and_requires_bounded_activation():
    assert from_env({}) is None
    with pytest.raises(KeyError):
        from_env({'HR_MATERIALS_ASR_ENABLED':'1'})
    with pytest.raises(ValueError):
        ASRDispatch('https://source.example','x'*40,['zoom'],0)


def test_only_explicit_providers_new_recordings_and_stable_retries():
    sent=[]
    dispatcher=ASRDispatch('http://127.0.0.1:8093','x'*40,['zoom'],100,
        send=lambda event,key:sent.append((event,key)))
    source={'id':'binding','owner_kind':'interview','owner_id':'i1','provider':'zoom','generation':1,'created_at':101,'metadata':{}}
    class Repo:
        def list_assets(self,identity):
            return [{'id':'recording','kind':'video','state':'ready','created_at':102},
                    {'id':'old','kind':'video','state':'ready','created_at':99},
                    {'id':'text','kind':'transcript','state':'ready','created_at':102}]
    repo=Repo()
    assert dispatcher.dispatch([source,{**source,'provider':'daily'}],repo)=={'accepted':1,'failed':0}
    dispatcher.dispatch([source],repo)
    assert len(sent)==1
    assert set(sent[0][0])=={'owner_kind','owner_id','binding_id','generation','asset_id'}


def test_failure_is_retryable_without_changing_video_policy():
    def fail(*args):
        raise OSError('unavailable')
    dispatcher=ASRDispatch('https://source.example','x'*40,['daily'],100,send=fail)
    source={'id':'b','owner_kind':'interview','owner_id':'i','provider':'daily','generation':1,'created_at':101,'metadata':{},'storage_policy':'remote'}
    class Repo:
        def list_assets(self,_):
            return [{'id':'v','kind':'video','state':'ready','created_at':101}]
    assert dispatcher.dispatch([source],Repo())=={'accepted':0,'failed':1}
    assert source['storage_policy']=='remote'


def test_ack_checkpoint_survives_restart_and_batch_is_bounded(tmp_path):
    sent=[]
    source={'id':'b','owner_kind':'interview','owner_id':'i','provider':'zoom','generation':1,'created_at':101,'metadata':{}}
    class Repo:
        def list_assets(self,_):
            return [{'id':str(n),'kind':'video','state':'ready','created_at':101} for n in range(10)]
    def build():
        return ASRDispatch('https://source.example','x'*40,['zoom'],100,
            send=lambda event,key:sent.append(key),checkpoint=tmp_path/'dispatch.sqlite',batch_limit=2)
    assert build().dispatch([source],Repo())=={'accepted':2,'failed':0}
    assert build().dispatch([source],Repo())=={'accepted':2,'failed':0}
    assert len(set(sent))==4


def test_pre_activation_binding_can_dispatch_new_recording():
    sent=[]
    dispatcher=ASRDispatch('https://source.example','x'*40,['zoom'],100,
        send=lambda event,key:sent.append(event))
    source={'id':'old-binding','owner_kind':'interview','owner_id':'scheduled-before-activation',
        'provider':'zoom','generation':1,'created_at':50,'metadata':{}}
    class Repo:
        def list_assets(self,_):
            return [{'id':'new-recording','kind':'video','state':'ready','created_at':101,'updated_at':103},
                    {'id':'historical-recording','kind':'video','state':'ready','created_at':90,'updated_at':105}]
    assert dispatcher.dispatch([source],Repo())=={'accepted':1,'failed':0}
    assert [event['asset_id'] for event in sent]==['new-recording']



def test_old_asset_new_copy_location_generation_and_update_never_trigger_backfill():
    sent=[]
    dispatcher=ASRDispatch('https://source.example','x'*40,['zoom'],100,
        send=lambda event,key:sent.append(event))
    source={'id':'old-binding','owner_kind':'interview','owner_id':'interview',
        'provider':'zoom','generation':2,'created_at':50,'metadata':{}}
    class Repo:
        def list_assets(self,_):
            return [{'id':'historical','kind':'video','state':'ready','created_at':90,'updated_at':120}]
        def list_jobs(self,_):
            raise AssertionError('Copy history must not widen the immutable asset activation gate')
        def list_locations(self,_):
            raise AssertionError('Locations must not widen the immutable asset activation gate')
    # The repository can contain a new-generation recopy and newly verified S3
    # locations; none is evidence that this previously discovered asset is new.
    assert dispatcher.dispatch([source],Repo())=={'accepted':0,'failed':0}
    assert sent==[]


def test_old_pending_asset_stays_manual_after_becoming_ready():
    sent=[]
    dispatcher=ASRDispatch('https://source.example','x'*40,['zoom'],100,
        send=lambda event,key:sent.append(event))
    source={'id':'old-binding','owner_kind':'interview','owner_id':'interview',
        'provider':'zoom','generation':1,'created_at':50,'metadata':{}}
    pending={'id':'pending-before-activation','kind':'video','state':'pending','created_at':90,'updated_at':90}
    class Repo:
        def list_assets(self,_):
            return [pending]
    repo=Repo()
    assert dispatcher.dispatch([source],repo)=={'accepted':0,'failed':0}
    pending.update(state='ready',updated_at=120)
    assert dispatcher.dispatch([{**source,'generation':2}],repo)=={'accepted':0,'failed':0}
    assert sent==[]


def test_revoked_or_capture_disabled_source_never_dispatches_new_asset():
    sent=[]
    dispatcher=ASRDispatch('https://source.example','x'*40,['zoom'],100,
        send=lambda event,key:sent.append(event))
    source={'id':'old-binding','owner_kind':'interview','owner_id':'interview',
        'provider':'zoom','generation':1,'created_at':50,'metadata':{}}
    class Repo:
        def list_assets(self,_):
            return [{'id':'new','kind':'video','state':'ready','created_at':100}]
    repo=Repo()
    assert dispatcher.dispatch([{**source,'revoked':True},{**source,'metadata':{'capture_disabled':True}}],repo)=={'accepted':0,'failed':0}
    assert sent==[]
    assert dispatcher.dispatch([source],repo)=={'accepted':1,'failed':0}
    assert [event['asset_id'] for event in sent]==['new']


def test_real_catalog_old_pending_and_recopy_stay_manual_new_recording_dispatches(tmp_path):
    import sqlite3
    from materials_repository import MaterialsRepository
    clock=[50.0]
    database=tmp_path/'catalog.sqlite3'
    connect=lambda:sqlite3.connect(database)
    with connect() as db:
        db.executescript('CREATE TABLE cp_interviews(id TEXT PRIMARY KEY); CREATE TABLE cp_meetings(id TEXT PRIMARY KEY);')
        db.execute("INSERT INTO cp_interviews VALUES('interview')")
    repository=MaterialsRepository(connect,clock=lambda:clock[0])
    repository.init()
    source=repository.ensure_source('interview','interview','zoom','zoom',
        {'session_id':'scheduled-session'},storage_policy='owned')
    clock[0]=90.0
    pending=repository.upsert_asset(source['id'],'video','pending-before-activation','pending')
    historical=repository.upsert_asset(source['id'],'video','already-ready','ready')
    # Recopy and newly verified locations after activation must not broaden the
    # gate, even when no old copy history exists for an imported legacy asset.
    clock[0]=110.0
    for asset in (pending,historical):
        job=repository.enqueue_job(source['id'],'copy_asset',asset['id'],{'asset_id':asset['id']})
        claim=repository.claim_job('test-copy',600,job_id=job['id'])
        repository.upsert_asset(source['id'],'video',asset['provider_asset_id'],'ready')
        repository.add_location(asset['id'],'s3','test-object-'+asset['id'],'verified',1,'a'*64)
        repository.finish_job(job['id'],claim['lease_token'],{'asset_id':asset['id']})
    new=repository.upsert_asset(source['id'],'video','discovered-after-activation','ready')
    assert repository.get_asset(pending['id'])['created_at']==90.0
    sent=[]
    dispatcher=ASRDispatch('https://source.example','x'*40,['zoom'],100,
        send=lambda event,key:sent.append(event))
    assert dispatcher.dispatch([repository.get_source(source['id'])],repository)=={'accepted':1,'failed':0}
    assert [event['asset_id'] for event in sent]==[new['id']]
