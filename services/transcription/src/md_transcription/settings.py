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
        return hashlib.sha256(json.dumps([self.asr_url, self.asr_model, self.asr_response_format], separators=(',', ':')).encode()).hexdigest()

    @property
    def asr_configured(self):
        return bool(self.asr_url and self.asr_model)

    @classmethod
    def from_env(cls):
        values = {}
        for name in cls.__dataclass_fields__:
            raw = os.getenv('TRANSCRIPTION_' + name.upper())
            if raw is not None:
                if name == 'allow_insecure_asr_http':
                    if raw.lower() not in ('true', 'false', '1', '0'): raise ValueError('invalid boolean')
                    raw = raw.lower() in ('true', '1')
                elif name == 'api_keys': raw = json.loads(raw)
                elif name in ('max_input_bytes', 'max_result_bytes', 'lease_seconds', 'max_attempts', 'asr_timeout_seconds'): raw = int(raw)
                values[name] = raw
        return cls(**values)
