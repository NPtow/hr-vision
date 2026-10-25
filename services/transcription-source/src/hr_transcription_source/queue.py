from celery import Celery
from celery.signals import worker_ready,worker_shutdown
import threading


def build_celery(settings, *, worker_signals=False):
    if not settings.broker_url or settings.broker_url.startswith('memory:'):
        raise ValueError('durable_source_broker_required')
    app=Celery('hr_transcription_source',broker=settings.broker_url)
    app.conf.update(task_serializer='json',accept_content=['json'],result_serializer='json',
        task_default_queue='hr_transcription_source',task_default_delivery_mode='persistent',
        task_acks_late=True,task_reject_on_worker_lost=True,worker_prefetch_multiplier=1,
        worker_enable_remote_control=False,task_ignore_result=True,
        broker_connection_retry_on_startup=True,broker_connection_timeout=3,
        broker_transport_options={'confirm_publish':True},task_publish_retry=False,
        beat_schedule={'source-reconcile':{'task':'hr_transcription_source.reconcile','schedule':30.0}})
    if worker_signals:
        stop=threading.Event()
        @worker_ready.connect(weak=False)
        def heartbeat_start(sender=None,**kwargs):
            if getattr(sender,'app',None) is not app:
                return
            def pulse():
                from .repository import Repository
                repo=Repository(settings.data_dir/'source.sqlite3')
                while not stop.is_set():
                    repo.claim('worker-heartbeat',settings.lease_seconds)
                    stop.wait(30)
            threading.Thread(target=pulse,daemon=True,name='source-worker-heartbeat').start()
        @worker_shutdown.connect(weak=False)
        def heartbeat_stop(sender=None,**kwargs):
            if getattr(sender,'app',None) is app:
                stop.set()
    @app.task(name='hr_transcription_source.run')
    def run(identity):
        from .worker import Runtime
        return Runtime(settings).run_once(identity)
    @app.task(name='hr_transcription_source.reconcile')
    def reconcile():
        from .worker import Runtime
        return Runtime(settings).reconcile()
    return app
