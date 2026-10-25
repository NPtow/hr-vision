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
    @app.task(name='md_transcription.run')
    def run(job_id):
        from .worker import Runtime
        return Runtime(settings).run_once(job_id)
    @app.task(name='md_transcription.reconcile')
    def reconcile():
        from .worker import Runtime
        return Runtime(settings).reconcile()
    return app
