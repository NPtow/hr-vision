"""Reviewed local import -> private objects -> actual Core API, no HR SQL in tool."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[5]
for path in (ROOT/'services/recruiting-api',ROOT/'services/data-core/src',ROOT/'services/data-core/tests',ROOT/'services/transcription-source/src'):
    sys.path.insert(0,str(path))
from test_core import env as core_env,PORTAL,SCOPED,auth
from hr_materials_core.app import create_app
from hr_materials_core.auth import Principal,Tokens

spec=importlib.util.spec_from_file_location('owned_import',Path(__file__).parents[1]/'import_owned.py')
ops=importlib.util.module_from_spec(spec);sys.modules[spec.name]=ops;spec.loader.exec_module(ops)
UPLOAD='u'*40


@pytest.fixture
def setup(core_env,tmp_path):
    _,repo,store,service,principals,connect=core_env
    manifest=[]
    for number in (1,2):
        owner='i'+str(number)
        capture=str(number)*64
        room={'provider':'upload','id':'upload-'+owner,'name':'upload-'+owner,'connectionId':'upload',
            'captureInstanceId':capture}
        with connect() as db:
            db.execute('UPDATE cp_interviews SET room=? WHERE id=?',(json.dumps(room),owner))
        files=[]
        for part in range(2 if number==1 else 1):
            content=('synthetic-original-'+str(number)+'-'+str(part)).encode()
            path=tmp_path/(owner+'-'+str(part)+('.mov' if part else '.mp4'))
            path.write_bytes(content)
            files.append({'path':str(path),'sha256':ops.digest(content),'size':len(content),
                'duration':11.0+part,'format':'mov' if part else 'mp4',
                'content_type':'video/quicktime' if part else 'video/mp4'})
        manifest.append({'owner_kind':'interview','owner_id':owner,'candidate_id':'c'+str(number),
            'candidate_name':'private candidate '+str(number),'connection_id':'upload','capture_instance_id':capture,'files':files})
    service.storage_registry[('upload','upload')]=store
    principals={**principals,UPLOAD:Principal('upload','source','upload','upload')}
    client=TestClient(create_app(service=service,tokens=Tokens(principals)))
    calls={'put':0,'head':0}
    original=store.head
    def head(key):
        calls['head']+=1
        if key not in store.objects:
            raise ClientError({'Error':{'Code':'NoSuchKey'}},'HeadObject')
        return original(key)
    def put(path,**kwargs):
        calls['put']+=1
        content=Path(path).read_bytes()
        assert ops.digest(content)==kwargs['expected_sha256']
        assert len(content)==kwargs['expected_size']
        ref=store.add(content,kwargs['content_type'])
        return original(ref['object_key'])
    store.head=head
    store.put_file=put
    importer=ops.Importer(ops.Configuration('http://127.0.0.1:8093',PORTAL,{'upload':UPLOAD}),store,core=client)
    with client:
        yield importer,manifest,calls,client,connect,store


def test_actual_core_import_is_owned_multi_file_and_idempotent(setup):
    importer,manifest,calls,client,_,_=setup
    first=importer.run(manifest)
    assert (first['owners'],first['recordings'],calls['put'])==(2,3,3)
    second=importer.run([{**manifest[0],'files':list(reversed(manifest[0]['files']))},manifest[1]])
    assert calls['put']==3
    assert first['results']==second['results']
    for entry,result in zip(manifest,first['results']):
        material=client.get('/v1/owners/interview/'+entry['owner_id']+'/materials',headers=auth()).json()
        assert len(material['assets'])==len(entry['files'])
        assert all(a['kind']=='video' and a['state']=='ready' for a in material['assets'])
        assert {a['external_id'] for a in material['assets']}=={'upload:'+f['sha256'] for f in entry['files']}
        assert {a['metadata']['duration'] for a in material['assets']}=={f['duration'] for f in entry['files']}
        assert material['transcripts']==[]
        assert material['channels'][0]['acquisition_complete'] is True
        assert material['channels'][0]['transcription_status']=='not_requested'
        assert material['channels'][0]['transcript_status']=='processing'
        assert result['asset_ids']==[r['asset_id'] for r in result['recordings']]
        dossier=client.get('/v1/candidates/'+entry['candidate_id']+'/dossier',headers=auth()).json()
        assert all(identity in json.dumps(dossier) for identity in result['asset_ids'])


def test_all_files_verified_before_any_core_or_s3_mutation(setup):
    importer,manifest,calls,client,connect,_=setup
    Path(manifest[-1]['files'][0]['path']).write_bytes(b'corrupted')
    with pytest.raises(ops.ImportFailure,match='local_file_size_mismatch'):
        importer.run(manifest)
    assert calls['put']==0 and calls['head']==0
    with connect() as db:
        assert db.execute('SELECT count(*) FROM media_sources').fetchone()[0]==0


def test_unprepared_room_and_candidate_mismatch_fail_before_upload(setup):
    importer,manifest,calls,_,connect,_=setup
    with connect() as db:
        db.execute("UPDATE cp_interviews SET room=? WHERE id='i2'",(json.dumps({'id':'old-room','name':'old-room','provider':'daily'}),))
    with pytest.raises(ops.ImportFailure,match='core_request_rejected'):
        importer.run(manifest)
    assert calls['put']==0
    with pytest.raises(ops.ImportFailure,match='candidate_owner_mismatch'):
        importer.run([{**manifest[0],'candidate_id':'c2'}])
    assert calls['put']==0


def test_scoped_portal_cannot_import_another_candidate(setup):
    importer,manifest,calls,_,_,_=setup
    importer.configuration.portal_token=SCOPED
    with pytest.raises(ops.ImportFailure,match='core_request_rejected'):
        importer.run([manifest[1]])
    assert calls['put']==0


def test_receipt_ack_loss_reuses_objects_binding_and_same_receipt(setup,monkeypatch):
    importer,manifest,calls,client,_,_=setup
    original=importer.call
    attempts=[]
    def lost(method,path,token,body=None):
        result=original(method,path,token,body)
        if path.endswith('/receipts'):
            attempts.append(body)
            if len(attempts)==1:
                raise ops.ImportFailure('core_unavailable')
        return result
    monkeypatch.setattr(importer,'call',lost)
    with pytest.raises(ops.ImportFailure,match='core_unavailable'):
        importer.run([manifest[0]])
    result=importer.run([manifest[0]])
    assert attempts[0]==attempts[1]
    assert calls['put']==2
    material=client.get('/v1/owners/interview/i1/materials',headers=auth()).json()
    assert len(material['assets'])==2
    assert set(result['results'][0]['asset_ids'])=={a['id'] for a in material['assets']}


def test_storage_mismatch_is_not_overwritten(setup):
    importer,manifest,calls,_,_,store=setup
    file=manifest[0]['files'][0]
    # A pre-existing key with inconsistent size must fail before receipt.
    store.objects[store.object_key(file['sha256'])]=type('Object',(),{'key':store.object_key(file['sha256']),
        'sha256':file['sha256'],'size':file['size']+1,'content_type':file['content_type'],'backend':'s3'})()
    with pytest.raises(ops.ImportFailure,match='stored_object_proof_mismatch'):
        importer.run([{**manifest[0],'files':[file]}])
    assert calls['put']==0


def test_private_environment_and_output_exclude_pii_from_stdout(tmp_path,capsys,monkeypatch):
    path=tmp_path/'source.env'
    path.write_text('SECRET="private value"\n')
    path.chmod(0o644)
    with pytest.raises(ops.ImportFailure,match='environment_not_private'):
        ops.read_private_env(path)
    path.chmod(0o600)
    assert ops.read_private_env(path)=={'SECRET':'private value'}
    output=tmp_path/'receipt.json'
    data={'results':[{'candidate_name':'private-person','asset_ids':['private-id']}]}
    ops.write_private_output(output,data)
    assert output.stat().st_mode & 0o777==0o600
    assert json.loads(output.read_text())==data
    with pytest.raises(FileExistsError):
        ops.write_private_output(output,data)
    assert capsys.readouterr().out==''


def test_configuration_uses_only_explicit_upload_credentials():
    env={'TRANSCRIPTION_SOURCE_CORE_URL':'http://127.0.0.1:8093',
        'TRANSCRIPTION_SOURCE_CALLERS_JSON':json.dumps({'collector':{'core_read_token':PORTAL}}),
        'TRANSCRIPTION_SOURCE_CORE_SOURCES_JSON':json.dumps([{'provider':'zoom','connection_id':'zoom','token':'z'*40}])}
    with pytest.raises(ops.ImportFailure,match='upload_credentials_required'):
        ops.Configuration.from_env(env)
    env['TRANSCRIPTION_SOURCE_CORE_SOURCES_JSON']=json.dumps([{'provider':'upload','connection_id':'upload','token':UPLOAD}])
    configured=ops.Configuration.from_env(env)
    assert configured.portal_token==PORTAL and configured.source_tokens=={'upload':UPLOAD}
    assert PORTAL not in repr(configured) and UPLOAD not in repr(configured)


def test_nonfinite_duration_duplicate_owner_content_and_symlink_fail_preflight(setup,tmp_path):
    importer,manifest,calls,_,_,_=setup
    file=manifest[0]['files'][0]
    for duration in (float('nan'),float('inf'),-1):
        with pytest.raises(ops.ImportFailure,match='invalid_duration'):
            importer.run([{**manifest[0],'files':[{**file,'duration':duration}]}])
    with pytest.raises(ops.ImportFailure,match='duplicate_recording_across_owners'):
        importer.run([manifest[0],{**manifest[1],'files':[file]}])
    link=tmp_path/'symlink.mp4'
    link.symlink_to(file['path'])
    with pytest.raises(ops.ImportFailure,match='local_file_unavailable'):
        importer.run([{**manifest[0],'files':[{**file,'path':str(link)}]}])
    assert calls['put']==0 and calls['head']==0
