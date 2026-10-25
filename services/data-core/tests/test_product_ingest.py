import json
import sqlite3
import pytest

from test_core import env, auth, binding, receipt, video, post, PORTAL, SCOPED, SOURCE
from hr_materials_core.auth import Principal
from hr_materials_core.schema import migrate, is_v2
from hr_materials_core.schemas import BindingInput
from materials_repository import MaterialsRepository, RepositoryError


@pytest.mark.parametrize('failure', [None, 'copied', 'dropped', 'renamed'])
def test_populated_migration_preserves_children_and_rolls_back(tmp_path, failure):
    path = tmp_path / 'migration.sqlite'
    connect = lambda: sqlite3.connect(path)
    with connect() as db:
        db.executescript('''CREATE TABLE cp_interviews(id TEXT PRIMARY KEY);
            CREATE TABLE cp_meetings(id TEXT PRIMARY KEY);
            CREATE TABLE em_intake_candidates(id TEXT PRIMARY KEY);
            INSERT INTO cp_interviews VALUES ('i'); INSERT INTO em_intake_candidates VALUES ('draft');''')
    MaterialsRepository(connect).init()
    with connect() as db:
        db.execute("INSERT INTO media_sources (id,interview_id,provider,connection_id,capture_identity,source_ref,storage_policy,created_at,updated_at) VALUES ('s','i','daily','daily','c','{}','owned',1,1)")
        db.execute("INSERT INTO media_assets VALUES ('a','s','video','external','ready','{}',1,1)")
        db.execute("INSERT INTO asset_locations VALUES ('l','a','s3','object','verified',2,?,'{}',1,1)", ('a'*64,))
        db.execute("CREATE VIEW material_view AS SELECT id FROM media_sources")
        db.execute("CREATE TRIGGER source_protected BEFORE DELETE ON media_sources BEGIN SELECT RAISE(ABORT,'protected'); END")
    def checkpoint(stage):
        if stage == failure:
            raise RuntimeError('injected')
    if failure:
        with pytest.raises(RuntimeError): migrate(connect, checkpoint=checkpoint)
    else:
        assert migrate(connect)
        assert not migrate(connect)
    with connect() as db:
        db.execute('PRAGMA foreign_keys=ON')
        assert is_v2(db) is (failure is None)
        assert db.execute('SELECT id FROM material_view').fetchall() == [('s',)]
        assert db.execute('SELECT source_id FROM media_assets').fetchone() == ('s',)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        with pytest.raises(sqlite3.IntegrityError): db.execute("DELETE FROM media_sources WHERE id='s'")
        if not failure:
            db.execute("INSERT INTO media_sources (id,intake_id,provider,connection_id,capture_identity,source_ref,storage_policy,created_at,updated_at) VALUES ('draft-source','draft','upload','upload','u','{}','owned',1,1)")
            with pytest.raises(sqlite3.IntegrityError): db.execute("UPDATE media_sources SET interview_id='i' WHERE id='draft-source'")
            with pytest.raises(sqlite3.IntegrityError): db.execute("UPDATE media_sources SET intake_id='missing' WHERE id='draft-source'")


def test_catalogue_is_bounded_scoped_owned_only_and_revocation_aware(env):
    client, repo, store, _, _, connect = env
    first = binding(client, policy='owned').json()['binding_id']
    second = binding(client, owner='i2', policy='owned').json()['binding_id']
    for source in (first, second):
        obj = {**store.add(source.encode()), 'state': 'verified'}
        assert post(client, source, receipt(video(external_id=source, location=obj))).status_code == 200
    page = client.get('/v1/assets', params={'created_since': 0, 'limit': 1}, headers=auth()).json()
    assert len(page['items']) == 1 and page['next_cursor']
    assert set(page['items'][0]) == {'asset_id','binding_id','generation','created_at','kind','state','owner_kind','owner_id','provider','connection_id','storage_policy'}
    next_page = client.get('/v1/assets', params={'created_since': 0,'cursor': page['next_cursor']}, headers=auth()).json()
    assert len(next_page['items']) == 1
    assert client.get('/v1/assets?created_since=0', headers=auth(SCOPED)).json()['items'][0]['owner_id'] == 'i1'
    with connect() as db: db.execute("UPDATE media_sources SET metadata=? WHERE id=?", ('{"capture_disabled":true}', first))
    assert client.get('/v1/assets?created_since=0', headers=auth(SCOPED)).json()['items'] == []
    assert client.get('/v1/assets?created_since=0&cursor=bad', headers=auth()).status_code == 400


def test_product_upload_intake_scopes_and_call_room_unchanged(env):
    _, repo, _, _, principals, connect = env
    with connect() as db:
        db.execute("INSERT INTO em_intake_candidates VALUES ('draft','w','v','{}')")
        db.execute("INSERT INTO em_vacancies VALUES ('v','w',NULL)")
        db.execute('''CREATE TABLE hr_recording_uploads(id TEXT,capture_identity TEXT,owner_kind TEXT,owner_id TEXT,
            connection_id TEXT,revoked_at REAL)''')
        for kind, owner in [('intake','draft'),('interview','i1')]:
            db.execute('INSERT INTO hr_recording_uploads VALUES (?,?,?,?,?,NULL)',
                ('u-'+owner,'product-upload:'+owner,kind,owner,'upload'))
        old_room = db.execute("SELECT room FROM cp_interviews WHERE id='i1'").fetchone()[0]
    def create(kind, owner, caller):
        return repo.create_binding(caller, BindingInput(schema_version=1,owner_kind=kind,owner_id=owner,
            provider='upload',connection_id='upload',storage_policy='owned',source_ref={
            'room_id':'u-'+owner,'room_name':'u-'+owner,'capture_instance_id':'product-upload:'+owner}))
    scoped = Principal('workspace','portal',workspace_ids=frozenset({'w'}))
    result = create('intake','draft',scoped)
    assert result['owner_kind'] == 'intake'
    with pytest.raises(RepositoryError): create('intake','draft',principals[SCOPED])
    with pytest.raises(RepositoryError): create('intake','draft',Principal('other','portal',workspace_ids=frozenset({'other'})))
    create('interview','i1',principals[PORTAL])
    with connect() as db:
        assert db.execute("SELECT room FROM cp_interviews WHERE id='i1'").fetchone()[0] == old_room
        db.execute("UPDATE em_intake_candidates SET body='{\"archived\":true}' WHERE id='draft'")
    with pytest.raises(RepositoryError): repo.read_materials(scoped,'intake','draft')
