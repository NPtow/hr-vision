"""At-least-once RabbitMQ delivery; durable source DB is queue source of truth."""
from functools import lru_cache
import logging
from pathlib import Path
import shutil
import time
from .config import Settings


def make_celery(settings):
    if not settings.broker_url:
        return None
    from celery import Celery
    app = Celery('source_daily',broker=settings.broker_url)
    app.conf.update(task_default_queue=settings.queue,task_acks_late=True,
        task_reject_on_worker_lost=True,task_ignore_result=True,
        task_serializer='json',accept_content=['json'],worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,broker_connection_timeout=5,
        broker_transport_options={'confirm_publish':True},
        task_publish_retry=False,task_soft_time_limit=settings.lease_seconds-10,
        task_time_limit=settings.lease_seconds,
        worker_hijack_root_logger=False,worker_redirect_stdouts=False)

    @app.task(name='source_daily.acquire')
    def acquire(job_id):
        if not isinstance(job_id,str) or len(job_id)>200:
            return
        from .runtime import build
        _,pipeline,_ = build(settings)
        pipeline.run(job_id)
    return app


def reconcile(settings,*,once=False,interval=15):
    from .store import Store
    store,app = Store(settings.db_path),make_celery(settings)
    if app is None:
        raise ValueError('RabbitMQ broker is required for source scheduler')
    while True:
        cleanup_temp(settings)
        for job_id in store.due():
            try:
                app.send_task('source_daily.acquire',args=[job_id],queue=settings.queue)
            except Exception:
                # Never log the broker URL, password or provider response.
                logging.getLogger('source.scheduler').warning('source_publish_unavailable')
                break
        if once:
            return
        time.sleep(interval)


def cleanup_temp(settings,*,now=None):
    """Recover temporary media from killed workers after every lease has expired."""
    root = Path(settings.temp_root)
    if not root.exists():
        return
    deadline = (time.time() if now is None else now)-max(3600,2*settings.lease_seconds+240)
    for path in root.iterdir():
        if path.name.startswith(('source-','asr-','result-')) and not path.is_symlink() and path.is_dir():
            try:
                if path.stat().st_mtime<deadline:
                    shutil.rmtree(path)
            except OSError:
                logging.getLogger('source.scheduler').warning('source_temp_cleanup_unavailable')
