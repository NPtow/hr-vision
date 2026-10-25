import json
import sqlite3

import pytest
from materials_repository import MaterialsRepository, RepositoryError
from hr_materials_core.auth import Principal
from hr_materials_core.schema import migrate, is_v2
from hr_materials_core.schemas import BindingInput
from test_core import env, auth, binding, post, receipt, video


def test_catalogue_pagination_scopes_revocation_and_late_ready(env):
    client, repo, store, _, _, connect = env
    ids = []
    for owner in ('i1', 'i2'):
        bid = binding(client, owner=owner, policy='owned').json()['binding_id']
        item = video(owner, {**store.add(owner.encode()), 'state': 'verified'})
        ids.extend(post(client, bid, receipt(item)).json()['asset_ids'])
    response = client.get('/v1/assets?created_since=0&limit=1', headers=auth()).json()
    assert len(response['items']) == 1 and response['next_cursor']
    page = client.get('/v1/assets', params={'created_since': 0, 'limit': 1, 'cursor': response['next_cursor']}, headers=auth()).json()
    assert {response['items'][0]['asset_id'], page['items'][0]['asset_id']} == set(ids)
    assert 'locations' not in json.dumps(response) and 'url' not in json.dumps(response)
    assert len(repo.catalogue_assets(Principal('one','portal',candidate_ids=frozenset({'c1'})),0)['items']) == 1
    assert not repo.catalogue_assets(Principal('other','source','zoom','zoom'),0)['items']
    with connect() as db: db.execute("UPDATE media_assets SET state='processing' WHERE id=?", (ids[0],))
    assert ids[0] not in {x['asset_id'] for x in repo.catalogue_assets(Principal('all','portal'),0)['items']}
    with connect() as db: db.execute("UPDATE media_assets SET state='ready' WHERE id=?", (ids[0],))
    assert ids[0] in {x['asset_id'] for x in repo.catalogue_assets(Principal('all','portal'),0)['items']}
    repo.suspend_owner('interview','i1')
    assert ids[0] not in {x['asset_id'] for x in repo.catalogue_assets(Principal('all','portal'),0)['items']}


def test_intake_owner_and_upload_registry_do_not_rewrite_room(env):
    _, repo, _, _, _, connect = env
    with connect() as db:
        db.executescript('''CREATE TABLE hr_recording_uploads(id TEXT PRIMARY KEY,capture_identity TEXT,
            owner_kind TEXT,owner_id TEXT,connection_id TEXT,revoked_at REAL);
            INSERT INTO em_vacancies VALUES ('v1','w1',NULL);
            INSERT INTO em_vacancies VALUES ('v2','w2',NULL);
            INSERT INTO em_intake_candidates VALUES ('d1','w1','v1','{}');
            INSERT INTO em_intake_candidates VALUES ('d2','w2','v2','{}');
            INSERT INTO hr_recording_uploads VALUES ('u1','product-upload:u1','intake','d1','upload',NULL);
            INSERT INTO hr_recording_uploads VALUES ('u2','product-upload:u2','interview','i1','upload',NULL);''')
        before = db.execute("SELECT room FROM cp_interviews WHERE id='i1'").fetchone()[0]
    def body(kind, owner, upload):
        return BindingInput(schema_version=1,owner_kind=kind,owner_id=owner,provider='upload',connection_id='upload',
            source_ref={'room_id':upload,'room_name':upload,'capture_instance_id':'product-upload:'+upload},storage_policy='owned')
    scoped = Principal('workspace','portal',workspace_ids=frozenset({'w1'}))
    row = repo.create_binding(scoped,body('intake','d1','u1'))
    assert row['owner_kind'] == 'intake'
    for principal in (Principal('other','portal',workspace_ids=frozenset({'w2'})), Principal('candidate','portal',candidate_ids=frozenset({'c1'}))):
        with pytest.raises(RepositoryError): repo.read_binding(principal,row['id'])
    with pytest.raises(RepositoryError): repo.create_binding(Principal('all','portal'),body('intake','d2','u1'))
    repo.create_binding(Principal('all','portal'),body('interview','i1','u2'))
    with connect() as db:
        assert db.execute("SELECT room FROM cp_interviews WHERE id='i1'").fetchone()[0] == before
        db.execute("UPDATE em_intake_candidates SET body='{\"archived\":true}' WHERE id='d1'")
    with pytest.raises(RepositoryError): repo.read_binding(scoped,row['id'])


@pytest.mark.parametrize('failure', [None, 'copied', 'dropped', 'renamed'])
def test_populated_migration_preserves_dependencies_and_rolls_back(tmp_path, failure):
    path = tmp_path/'old.sqlite'
    connect = lambda: sqlite3.connect(path)
    with connect() as db:
        db.executescript('''CREATE TABLE cp_interviews(id TEXT PRIMARY KEY);
            CREATE TABLE cp_meetings(id TEXT PRIMARY KEY);
            CREATE TABLE em_intake_candidates(id TEXT PRIMARY KEY);
            INSERT INTO cp_interviews VALUES ('i1'); INSERT INTO em_intake_candidates VALUES ('d1');''')
    repo = MaterialsRepository(connect); repo.init()
    root = repo.ensure_source('interview','i1','upload','upload',{'capture_instance_id':'cap'})
    with connect() as db:
        db.execute("INSERT INTO media_assets VALUES ('a',?,'video','v','ready','{}',1,1)", (root['id'],))
        db.execute("INSERT INTO asset_locations VALUES ('l','a','s3','key','verified',1,?,'{}',1,1)", ('a'*64,))
        db.execute("INSERT INTO transcript_versions VALUES ('t','a',1,'{}','[]','{}',?,1)", ('b'*64,))
        db.execute('CREATE VIEW media_source_view AS SELECT id FROM media_sources')
        db.execute('CREATE TRIGGER source_view_trigger AFTER UPDATE ON media_assets BEGIN SELECT id FROM media_source_view; END')
        before = db.execute('SELECT * FROM media_sources').fetchall()
    def checkpoint(stage):
        if stage == failure: raise RuntimeError('injected')
    if failure:
        with pytest.raises(RuntimeError): migrate(connect, checkpoint=checkpoint)
    else:
        assert migrate(connect) is True and migrate(connect) is False
    with connect() as db:
        db.execute('PRAGMA foreign_keys=ON')
        assert bool(is_v2(db)) == (failure is None)
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert db.execute('SELECT id FROM media_source_view').fetchone()[0] == root['id']
        assert db.execute('SELECT source_id FROM media_assets').fetchone()[0] == root['id']
        assert db.execute('SELECT object_key FROM asset_locations').fetchone()[0] == 'key'
        with pytest.raises(sqlite3.IntegrityError): db.execute("UPDATE transcript_versions SET segments='[1]'")
        if failure: assert db.execute('SELECT * FROM media_sources').fetchall() == before
        else:
            for iid, mid, intake in ((None,None,None),('i1',None,'d1'),(None,None,'missing')):
                with pytest.raises(sqlite3.IntegrityError):
                    db.execute('''INSERT INTO media_sources(id,interview_id,meeting_id,intake_id,provider,connection_id,
                        capture_identity,source_ref,storage_policy,created_at,updated_at) VALUES ('bad',?,?,?,'upload','upload','bad','{}','owned',1,1)''', (iid,mid,intake))
