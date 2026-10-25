from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Caller:
    token: str = field(repr=False)
    core_read_token: str = field(repr=False)


@dataclass
class Settings:
    data_dir: Path
    core_url: str
    asr_url: str
    asr_token: str = field(repr=False)
    callers: dict[str, Caller] = field(repr=False)
    source_tokens: dict[tuple[str, str], str] = field(repr=False)
    revision: str = 'speechkit-v1'
    asr_provider: str = 'yandex_speechkit'
    download_hosts: frozenset[str] = frozenset()
    max_input_bytes: int = 1024 ** 3
    max_result_bytes: int = 24 * 1024 ** 2
    lease_seconds: int = 1800
    retry_seconds: int = 30
    broker_url: str | None = field(default=None,repr=False)
    s3_bucket: str = ''
    s3_prefix: str = 'materials'
    s3_endpoint: str | None = None
    s3_region: str | None = None
    remote_resolvers: dict = field(default_factory=dict)

    def __post_init__(self):
        import re
        self.data_dir = Path(self.data_dir)
        for url in (self.core_url, self.asr_url):
            parsed = urlsplit(url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('invalid_service_url')
            if parsed.scheme == 'http' and parsed.hostname not in ('localhost','127.0.0.1','::1'):
                raise ValueError('service_url_requires_tls_or_loopback')
        if not re.fullmatch(r'[a-z][a-z0-9_]{1,63}', self.asr_provider):
            raise ValueError('invalid_asr_provider')
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,64}', self.revision):
            raise ValueError('invalid_config_revision')
        if not self.callers or any(len(c.token) < 32 or len(c.core_read_token) < 32 for c in self.callers.values()):
            raise ValueError('caller_and_core_read_credentials_required')
        if len({c.token for c in self.callers.values()}) != len(self.callers):
            raise ValueError('duplicate_caller_credential')
        if not self.source_tokens or any(len(t) < 32 for t in self.source_tokens.values()) or len(self.asr_token) < 32:
            raise ValueError('source_and_asr_credentials_required')
        if self.lease_seconds < 600 or self.max_input_bytes <= 0 or self.max_result_bytes <= 0:
            raise ValueError('invalid_limits')

    @property
    def config_identity(self):
        return self.asr_provider+':'+self.revision

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        callers = {name: Caller(**value) for name, value in json.loads(env['TRANSCRIPTION_SOURCE_CALLERS_JSON']).items()}
        source_tokens = {(v['provider'], v['connection_id']): v['token'] for v in json.loads(env['TRANSCRIPTION_SOURCE_CORE_SOURCES_JSON'])}
        return cls(data_dir=Path(env.get('TRANSCRIPTION_SOURCE_DATA_DIR', '/var/lib/hr-transcription-source')),
            core_url=env['TRANSCRIPTION_SOURCE_CORE_URL'], asr_url=env['TRANSCRIPTION_SOURCE_ASR_URL'],
            asr_token=env['TRANSCRIPTION_SOURCE_ASR_TOKEN'], callers=callers, source_tokens=source_tokens,
            revision=env.get('TRANSCRIPTION_SOURCE_CONFIG_REVISION', 'speechkit-v1'),
            asr_provider=env.get('TRANSCRIPTION_SOURCE_ASR_PROVIDER', 'yandex_speechkit'),
            download_hosts=frozenset(filter(None, env.get('TRANSCRIPTION_SOURCE_DOWNLOAD_HOSTS', '').split(','))),
            max_input_bytes=int(env.get('TRANSCRIPTION_SOURCE_MAX_INPUT_BYTES', str(1024**3))),
            max_result_bytes=int(env.get('TRANSCRIPTION_SOURCE_MAX_RESULT_BYTES', str(24*1024**2))),
            lease_seconds=int(env.get('TRANSCRIPTION_SOURCE_LEASE_SECONDS', '1800')),
            retry_seconds=int(env.get('TRANSCRIPTION_SOURCE_RETRY_SECONDS', '30')),
            broker_url=env.get('TRANSCRIPTION_SOURCE_BROKER_URL'), s3_bucket=env['TRANSCRIPTION_SOURCE_S3_BUCKET'],
            s3_prefix=env.get('TRANSCRIPTION_SOURCE_S3_PREFIX', 'materials'),
            s3_endpoint=env.get('TRANSCRIPTION_SOURCE_S3_ENDPOINT'), s3_region=env.get('TRANSCRIPTION_SOURCE_S3_REGION'))
