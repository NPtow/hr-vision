import pytest
from materials_asr_dispatch import ASRDispatch,from_env


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
    assert dispatcher.dispatch([source,{**source,'provider':'daily'},{**source,'created_at':99}],repo)=={'accepted':1,'failed':0}
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
