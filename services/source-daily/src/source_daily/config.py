from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def endpoint(value):
    if not value:
        return ''
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment):
        raise ValueError('Invalid service endpoint')
    return value.rstrip('/')


@dataclass(frozen=True)
class Settings:
    db_path: str = '/var/lib/md-source-daily/jobs.sqlite3'
    temp_root: str = '/var/lib/md-source-daily/tmp'
    api_keys: dict = field(default_factory=dict, repr=False)
    daily_key: str = field(default='', repr=False)
    daily_webhook_hmac: str = field(default='', repr=False)
    connection_id: str = 'daily'
    default_storage_policy: str = 'remote'
    core_url: str = ''
    core_token: str = field(default='', repr=False)
    transcription_url: str = ''
    transcription_token: str = field(default='', repr=False)
    enable_asr: bool = False
    daily_transcription_model: str = ''
    broker_url: str = field(default='', repr=False)
    queue: str = 'source-daily'
    s3_bucket: str = ''
    s3_prefix: str = 'materials'
    s3_endpoint: str = ''
    s3_region: str = ''
    max_attempts: int = 8
    settle_attempts: int = 3
    max_asr_polls: int = 120
    retry_seconds: int = 30
    lease_seconds: int = 600
    max_video_bytes: int = 512 * 1024 * 1024
    max_transcript_bytes: int = 16 * 1024 * 1024
    max_body_bytes: int = 256 * 1024
    max_artifacts: int = 100
    legacy_materials: bool = False

    def __post_init__(self):
        endpoint(self.core_url)
        endpoint(self.transcription_url)
        endpoint(self.s3_endpoint)
        if self.default_storage_policy not in ('remote', 'owned'):
            raise ValueError('Invalid default storage policy')
        if not self.connection_id or len(self.connection_id) > 100:
            raise ValueError('Invalid connection identity')
        if (not 1 <= self.settle_attempts <= self.max_attempts <= 100 or
                not 1 <= self.max_asr_polls <= 10000 or not 1 <= self.retry_seconds <= 3600 or
                not 60 <= self.lease_seconds <= 3600 or not 1 <= self.max_artifacts <= 1000):
            raise ValueError('Invalid source work limits')
        if not 1 <= self.max_transcript_bytes <= 16*1024*1024 or not 1 <= self.max_video_bytes <= 5*1024**3:
            raise ValueError('Invalid download limits')
        if not 1024 <= self.max_body_bytes <= 1024*1024:
            raise ValueError('Invalid API body limit')
        if not isinstance(self.api_keys, dict) or any(not isinstance(k, str) or len(k) < 16 or
                not isinstance(v, str) or not v or len(v) > 100 for k, v in self.api_keys.items()):
            raise ValueError('Invalid source API principals')
        if self.broker_url and urlsplit(self.broker_url).scheme not in ('amqp', 'amqps'):
            raise ValueError('Source queue requires RabbitMQ')
        if Path(self.db_path).name in ('candidates.sqlite3', 'hr.sqlite3', 'state.sqlite3'):
            raise ValueError('Use a private source job database')

    @classmethod
    def from_env(cls):
        values = {}
        for name, value in cls.__dataclass_fields__.items():
            env = os.getenv('SOURCE_DAILY_' + name.upper())
            if env is None:
                continue
            if name == 'api_keys':
                env = json.loads(env)
            elif value.type is bool:
                if env not in ('0', '1', 'true', 'false'):
                    raise ValueError('Invalid boolean source setting')
                env = env in ('1', 'true')
            elif value.type is int:
                env = int(env)
            values[name] = env
        return cls(**values)
