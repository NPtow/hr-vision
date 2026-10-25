"""Explicit, isolated service configuration."""
from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import json
import os
import ipaddress
from urllib.parse import urlsplit

@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path('./data')
    database_url: str = ''
    broker_url: str = field(default='', repr=False)
    api_keys: dict[str, str] = field(default_factory=dict, repr=False)
    backend_kind: str = 'http'
    deepgram_api_key: str = field(default='', repr=False)
    deepgram_model: str = 'nova-3'
    deepgram_language: str = 'ru'
    deepgram_diarize: bool = True
    deepgram_diarize_model: str = 'v2'
    deepgram_config_revision: str = '1'
    speechkit_api_key: str = field(default='', repr=False)
    speechkit_folder_id: str = ''
    speechkit_model: str = 'general'
    speechkit_language: str = 'ru-RU'
    speechkit_speaker_labeling: bool = False
    speechkit_config_revision: str = '1'
    staging_s3_bucket: str = ''
    staging_s3_prefix: str = 'speechkit-staging/'
    staging_s3_endpoint: str = 'https://storage.yandexcloud.net'
    staging_s3_access_key: str = field(default='', repr=False)
    staging_s3_secret_key: str = field(default='', repr=False)
    media_max_duration_seconds: int = 14400
    media_max_audio_bytes: int = 1024**3
    media_timeout_seconds: int = 900
    speechkit_poll_seconds: int = 10
    asr_url: str = ''
    asr_model: str = ''
    asr_api_key: str = field(default='', repr=False)
    allow_insecure_asr_http: bool = False
    asr_response_format: str = 'verbose_json'
    max_input_bytes: int = 512 * 1024 * 1024
    max_result_bytes: int = 20 * 1024 * 1024
    lease_seconds: int = 600
    max_attempts: int = 3
    asr_timeout_seconds: int = 300

    def __post_init__(self):
        object.__setattr__(self, 'data_dir', Path(self.data_dir).resolve())
        if not self.database_url:
            object.__setattr__(self, 'database_url', 'sqlite:///' + str(self.data_dir / 'jobs.sqlite3'))
        for name in ('max_input_bytes', 'max_result_bytes', 'lease_seconds', 'max_attempts', 'asr_timeout_seconds'):
            if isinstance(getattr(self, name), bool) or not isinstance(getattr(self, name), int) or getattr(self, name) <= 0:
                raise ValueError(f'{name} must be positive')
        if not isinstance(self.api_keys, dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k,v in self.api_keys.items()):
            raise ValueError('api_keys must map nonempty client IDs to nonempty secrets')
        if any(len(value) < 24 for value in self.api_keys.values()):
            raise ValueError('API secrets must have at least 24 characters')
        limits = {'max_input_bytes': 10 * 1024**3, 'max_result_bytes': 100 * 1024**2, 'lease_seconds': 86400, 'max_attempts': 20, 'asr_timeout_seconds': 86400}
        if any(getattr(self, name) > maximum for name,maximum in limits.items()):
            raise ValueError('configuration exceeds supported limit')
        if self.backend_kind not in ('http', 'yandex_speechkit', 'deepgram'):
            raise ValueError('unsupported backend_kind')
        for name in ('media_max_duration_seconds', 'media_max_audio_bytes', 'media_timeout_seconds', 'speechkit_poll_seconds'):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError('invalid media or polling limit')
        if self.media_max_duration_seconds > 14400 or self.media_max_audio_bytes > 1024**3:
            raise ValueError('SpeechKit media limit exceeded')
        if type(self.deepgram_diarize) is not bool or not self.deepgram_diarize_model or not self.deepgram_model or not self.deepgram_language or not self.deepgram_config_revision:
            raise ValueError('invalid Deepgram configuration')
        if type(self.speechkit_speaker_labeling) is not bool:
            raise ValueError('invalid speaker labeling flag')
        if not self.speechkit_model or not self.speechkit_config_revision or not self.speechkit_language:
            raise ValueError('SpeechKit configuration is empty')
        if self.staging_s3_endpoint != 'https://storage.yandexcloud.net':
            raise ValueError('SpeechKit staging must use Yandex Object Storage')
        if not self.staging_s3_prefix or self.staging_s3_prefix.startswith('/') or '..' in self.staging_s3_prefix.split('/'):
            raise ValueError('invalid staging prefix')
        if self.asr_response_format not in ('json', 'verbose_json'):
            raise ValueError('unsupported ASR response format')
        if self.asr_url:
            url = urlsplit(self.asr_url)
            if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError('ASR URL must be a full HTTP(S) URL without credentials, query or fragment')
            if not self.asr_model.strip():
                raise ValueError('ASR model is required')
            if url.scheme == 'http':
                try: internal = ipaddress.ip_address(url.hostname).is_private or ipaddress.ip_address(url.hostname).is_loopback
                except ValueError: internal = url.hostname == 'localhost' or '.' not in url.hostname or url.hostname.endswith(('.internal', '.local'))
                if not internal or not self.allow_insecure_asr_http:
                    raise ValueError('internal ASR HTTP requires explicit allow_insecure_asr_http')
        if len(set(self.api_keys.values())) != len(self.api_keys):
            raise ValueError('API secrets must be unique')

    @property
    def backend_identity(self):
        if self.backend_kind == 'deepgram':
            return hashlib.sha256(json.dumps([self.backend_kind,self.deepgram_model,self.deepgram_language,
                self.deepgram_diarize,self.deepgram_diarize_model,self.deepgram_config_revision,'PCM_S16LE_WAV',16000,1,
                'first_audio_stream', 'smart_format=false', 'punctuate=true','utterances=true','numerals=false','profanity_filter=false',
                self.media_max_duration_seconds],separators=(',',':')).encode()).hexdigest()
        if self.backend_kind == 'yandex_speechkit':
            return hashlib.sha256(json.dumps([self.backend_kind, self.speechkit_model, self.speechkit_language,
                self.speechkit_speaker_labeling, self.speechkit_config_revision,
                'PCM_S16LE_WAV', 16000, 1, 'first_audio_stream', 'FULL_DATA',
                'TEXT_NORMALIZATION_DISABLED', False, self.media_max_duration_seconds,
                self.staging_s3_bucket, self.staging_s3_prefix], separators=(',', ':')).encode()).hexdigest()
        return hashlib.sha256(json.dumps([self.asr_url, self.asr_model, self.asr_response_format], separators=(',', ':')).encode()).hexdigest()

    @property
    def asr_configured(self):
        if self.backend_kind == 'deepgram':
            return bool(self.deepgram_api_key and self.deepgram_model and self.deepgram_language)
        if self.backend_kind == 'yandex_speechkit':
            return bool(self.speechkit_api_key and self.staging_s3_bucket)
        return bool(self.asr_url and self.asr_model)

    @property
    def effective_model(self):
        if self.backend_kind == 'deepgram': return self.deepgram_model
        return self.speechkit_model if self.backend_kind == 'yandex_speechkit' else self.asr_model

    @classmethod
    def from_env(cls):
        values = {}
        for name in cls.__dataclass_fields__:
            raw = os.getenv('TRANSCRIPTION_' + name.upper())
            if raw is not None:
                if name in ('allow_insecure_asr_http', 'speechkit_speaker_labeling', 'deepgram_diarize'):
                    if raw.lower() not in ('true', 'false', '1', '0'): raise ValueError('invalid boolean')
                    raw = raw.lower() in ('true', '1')
                elif name == 'api_keys': raw = json.loads(raw)
                elif name in ('max_input_bytes', 'max_result_bytes', 'lease_seconds', 'max_attempts', 'asr_timeout_seconds', 'media_max_duration_seconds', 'media_max_audio_bytes', 'media_timeout_seconds', 'speechkit_poll_seconds'): raw = int(raw)
                values[name] = raw
        return cls(**values)
