"""Process construction. All dependencies are local package modules or HTTP clients."""
from ._vendor.daily_media import DailyMedia
from ._vendor.materials_daily import DailyCapture
from ._vendor.materials_storage import S3Storage
from .clients import CoreClient, TranscriptionClient
from .config import Settings
from .pipeline import Pipeline
from .store import Store


def build(settings=None):
    settings = settings or Settings.from_env()
    media = DailyMedia(settings.daily_key,True)
    storage = S3Storage(settings.s3_bucket,prefix=settings.s3_prefix,
                endpoint_url=settings.s3_endpoint or None,region_name=settings.s3_region or None) if settings.s3_bucket else None
    pipeline = Pipeline(settings,Store(settings.db_path),CoreClient(settings.core_url,settings.core_token),
                        DailyCapture(media,settings.connection_id,webhook_hmac=settings.daily_webhook_hmac or None),
                        TranscriptionClient(settings.transcription_url,settings.transcription_token),storage)
    return settings,pipeline,media


def api():
    from .api import create_app
    from .worker import make_celery
    settings,pipeline,media = build()
    celery = make_celery(settings)
    def publish(job_id):
        if celery is not None:
            celery.send_task('source_daily.acquire',args=[job_id],queue=settings.queue)
    return create_app(settings,pipeline,media,publish=publish)
