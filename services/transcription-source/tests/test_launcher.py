"""Check actual CLI wiring and idle worker heartbeat without a broker."""
from types import SimpleNamespace
import sys
import threading

import pytest

from hr_transcription_source import __main__ as launcher
from hr_transcription_source import queue
from hr_transcription_source.repository import Repository
from hr_transcription_source.settings import Settings,Caller


@pytest.mark.parametrize('command,signals,method',[('worker',True,'worker_main'),('scheduler',False,'start')])
def test_launcher_enables_heartbeat_for_worker_only(tmp_path,monkeypatch,command,signals,method):
    settings=SimpleNamespace(data_dir=tmp_path/'state')
    monkeypatch.setattr(launcher.Settings,'from_env',lambda:settings)
    observed=[]
    class App:
        def worker_main(self,args):
            observed.append(('worker_main',args))
        def start(self,args):
            observed.append(('start',args))
    def build(actual,*,worker_signals=False):
        assert actual is settings
        observed.append(('signals',worker_signals))
        return App()
    monkeypatch.setattr(queue,'build_celery',build)
    monkeypatch.setattr(sys,'argv',['hr-transcription-source',command])
    launcher.main()
    assert observed[0]==('signals',signals)
    assert observed[1][0]==method
    assert settings.data_dir.is_dir()
    if command=='worker':
        assert '--concurrency=1' in observed[1][1]
        assert observed[1][1][observed[1][1].index('-Q')+1]=='hr_transcription_source'


def test_real_worker_ready_signal_pulses_idle_health(tmp_path,monkeypatch):
    # Use private signal instances so this unit test cannot start another app's
    # heartbeat or leave a receiver registered in the test process.
    ready=type(queue.worker_ready)()
    shutdown=type(queue.worker_shutdown)()
    monkeypatch.setattr(queue,'worker_ready',ready)
    monkeypatch.setattr(queue,'worker_shutdown',shutdown)
    settings=SimpleNamespace(data_dir=tmp_path,lease_seconds=600,broker_url='amqp://fixture:fixture@127.0.0.1//')
    pulsed=threading.Event()
    claim=Repository.claim
    def observe(self,identity,seconds):
        result=claim(self,identity,seconds)
        pulsed.set()
        return result
    monkeypatch.setattr(Repository,'claim',observe)
    app=queue.build_celery(settings,worker_signals=True)
    sender=type('WorkerConsumer',(),{'app':app})()
    try:
        ready.send(sender=sender)
        assert pulsed.wait(2),'idle worker did not write health heartbeat'
        assert Repository(tmp_path/'source.sqlite3').worker_seen() is not None
    finally:
        shutdown.send(sender=sender)


def test_config_repr_hides_broker_and_service_credentials(tmp_path):
    broker='amqp://secret-user:secret-password@127.0.0.1//'
    settings=Settings(tmp_path,'http://127.0.0.1:8092','http://127.0.0.1:8091','asr-secret-'*4,
        {'collector':Caller('ingress-secret-'*4,'portal-secret-'*4)},{('zoom','zoom'):'source-secret-'*4},broker_url=broker)
    rendered=repr(settings)
    for secret in ('secret-user','secret-password','asr-secret','ingress-secret','portal-secret','source-secret'):
        assert secret not in rendered
