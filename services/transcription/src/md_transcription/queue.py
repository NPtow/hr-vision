"""RabbitMQ delivery is a hint; SQL records remain the source of truth."""
from celery import Celery

def build_celery(settings):
    app = Celery('md_transcription',broker=settings.broker_url)
    app.conf.update(task_serializer='json',accept_content=['json'],result_serializer='json',
        task_default_queue='md_transcription',task_default_delivery_mode='persistent',
        task_acks_late=True,task_reject_on_worker_lost=True,worker_prefetch_multiplier=1,
        worker_enable_remote_control=False,task_ignore_result=True,
        broker_connection_retry_on_startup=True,broker_transport_options={'confirm_publish':True},
        beat_schedule={'reconcile':{'task':'md_transcription.reconcile','schedule':30.0}})
    from celery.signals import worker_ready, worker_shutdown
    import threading
    stop = threading.Event()
    @worker_ready.connect(weak=False)
    def record_worker(sender=None, **_kwargs):
        if sender is not None and getattr(sender, 'app', None) is not app: return
        from .repository import Repository
        repository = Repository(settings.database_url)
        repository.init()
        def pulse():
            while not stop.is_set():
                try: repository.touch_worker()
                except Exception: pass
                stop.wait(20)
        threading.Thread(target=pulse, daemon=True).start()
    @worker_shutdown.connect(weak=False)
    def shutdown_worker(**_kwargs): stop.set()
    @app.task(name='md_transcription.run')
    def run(job_id):
        from .worker import Runtime
        return Runtime(settings).run_once(job_id)
    @app.task(name='md_transcription.reconcile')
    def reconcile():
        from .worker import Runtime
        return Runtime(settings).reconcile()
    return app
