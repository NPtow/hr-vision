import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys

import pytest

spec=importlib.util.spec_from_file_location('asr_provision',Path(__file__).parents[1]/'provision.py')
ops=importlib.util.module_from_spec(spec);spec.loader.exec_module(ops)


@pytest.fixture
def setup(tmp_path,monkeypatch):
    config=tmp_path/'private';config.mkdir()
    core=tmp_path/'core.env'
    monkeypatch.setattr(ops,'CONFIG',config);monkeypatch.setattr(ops,'CORE',core)
    identity={name:name+'-'+'x'*40 for name in ('ingress','asr','core_read','core_zoom','broker')}
    (config/'credentials.json').write_text(json.dumps(identity))
    calls=[]
    monkeypatch.setattr(ops,'quiet',lambda cmd,**kwargs:calls.append((cmd,kwargs)))
    monkeypatch.setattr(ops,'wait_ready',lambda principals:None)
    return config,core,identity,calls


def core_database(setup,tmp_path,monkeypatch,*,active=False):
    _,core,_,_=setup
    database=tmp_path/'hr.sqlite3'
    connect=sqlite3.connect
    with connect(database) as db:
        db.executescript('CREATE TABLE cp_interviews(status TEXT); CREATE TABLE cp_meetings(status TEXT); CREATE TABLE media_jobs(status TEXT);')
        db.execute('INSERT INTO cp_interviews VALUES(?)',('live' if active else 'completed',))
    ops.write_env(core,{'CORE_HR_DATABASE':'/var/lib/hr-vision-api/test.sqlite3',
        'CORE_PRINCIPALS_JSON':json.dumps([{'name':'existing','role':'portal','token':'previous-secret'}]),'UNCHANGED':'keep me'})
    def redirected(path,*args,**kwargs):
        if str(path).startswith('file:/var/lib/hr-vision-api/'):
            path='file:'+str(database)+'?mode=ro'
        return connect(path,*args,**kwargs)
    monkeypatch.setattr(ops.sqlite3,'connect',redirected)


def test_env_roundtrip_preserves_json_and_modes(setup):
    config,_,_,_=setup
    path=config/'test.env'
    data={'JSON':json.dumps([{'token':'test only','role':'portal'}]),'URL':'https://example.org/a?x=1&y=2'}
    ops.write_env(path,data)
    assert ops.read_env(path)==data
    assert path.stat().st_mode & 0o777==0o600


def test_prepare_rejects_missing_key_without_mutation(setup,monkeypatch):
    config,_,_,calls=setup
    monkeypatch.setattr(sys,'stdin',io.StringIO('{}'))
    with pytest.raises(ValueError,match='invalid_deepgram_key'):ops.prepare()
    assert calls==[] and not (config/'asr.env').exists()


def test_activate_rejects_unexpected_database_before_mutation(setup):
    config,core,_,calls=setup
    ops.write_env(core,{'CORE_HR_DATABASE':'/tmp/not-hr.sqlite3','CORE_PRINCIPALS_JSON':'[]'})
    before=core.read_bytes()
    with pytest.raises(ValueError,match='unexpected_database'):ops.activate_core()
    assert core.read_bytes()==before and calls==[]


def test_activate_preserves_principals_and_online_backup(setup,tmp_path,monkeypatch):
    config,core,_,calls=setup
    core_database(setup,tmp_path,monkeypatch)
    ops.activate_core()
    values=ops.read_env(core)
    principals=json.loads(values['CORE_PRINCIPALS_JSON'])
    assert principals[0]=={'name':'existing','role':'portal','token':'previous-secret'}
    assert len(principals)==3 and values['UNCHANGED']=='keep me'
    assert len(list(config.glob('hr-before-source-*.sqlite3')))==1
    assert calls==[(['systemctl','restart','hr-vision-core'],{})]
    ops.activate_core()
    assert len(calls)==1 and len(json.loads(ops.read_env(core)['CORE_PRINCIPALS_JSON']))==3


def test_activate_live_guard_and_restart_failure_rollback(setup,tmp_path,monkeypatch):
    _,core,_,calls=setup
    core_database(setup,tmp_path,monkeypatch,active=True)
    previous=core.read_bytes()
    with pytest.raises(RuntimeError,match='live_meeting_guard'):ops.activate_core()
    assert core.read_bytes()==previous and calls==[]
    with sqlite3.connect(tmp_path/'hr.sqlite3') as db:db.execute("UPDATE cp_interviews SET status='completed'")
    attempts=[]
    def fail_once(command,**kwargs):
        attempts.append(command)
        if len(attempts)==1:raise RuntimeError('operator_command_failed')
    monkeypatch.setattr(ops,'quiet',fail_once)
    with pytest.raises(RuntimeError):ops.activate_core()
    assert core.read_bytes()==previous and len(attempts)==2


def test_post_restart_unhealthy_restores_previous_principals(setup,tmp_path,monkeypatch):
    _,core,_,calls=setup
    core_database(setup,tmp_path,monkeypatch)
    previous=core.read_bytes();probes=[]
    def health(principals):
        probes.append(principals)
        if len(probes)==1:raise RuntimeError('core_readiness_timeout')
    monkeypatch.setattr(ops,'wait_ready',health)
    with pytest.raises(RuntimeError,match='core_readiness_timeout'):ops.activate_core()
    assert core.read_bytes()==previous and len(calls)==2
    assert probes[-1]==[{'name':'existing','role':'portal','token':'previous-secret'}]
