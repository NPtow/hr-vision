"""Environment settings, with no provider credentials or portal runtime import."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sqlite3

from .auth import Principal, Tokens


@dataclass(frozen=True)
class Settings:
    database_path: str
    principals: dict[str, Principal] = field(repr=False)
    default_connections: dict[str, str] = field(default_factory=lambda: {"daily": "daily", "zoom": "zoom"})
    s3_bucket: str | None = None
    s3_prefix: str = "materials"
    s3_endpoint: str | None = None
    s3_region: str | None = None
    host: str = "127.0.0.1"
    port: int = 8093
    collector_enabled: bool = False

    def __post_init__(self):
        Tokens(self.principals)
        if not Path(self.database_path).is_absolute() or not Path(self.database_path).is_file():
            raise ValueError("existing_absolute_hr_database_required")
        if not 1 <= self.port <= 65535:
            raise ValueError("invalid_port")

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        configured = json.loads(env.get("CORE_PRINCIPALS_JSON", "[]"))
        principals = {}
        for value in configured:
            allowed = {"token", "name", "role", "provider", "connection_id", "candidate_ids", "workspace_ids"}
            if not isinstance(value, dict) or not set(value) <= allowed or not {"token", "name", "role"} <= set(value):
                raise ValueError("invalid_principal_configuration")
            item = value.copy()
            token = item.pop("token")
            if token in principals:
                raise ValueError("duplicate_service_token")
            if "candidate_ids" in item:
                ids = item["candidate_ids"]
                if not isinstance(ids, list) or any(not isinstance(x, str) or not x for x in ids):
                    raise ValueError("invalid_candidate_scope")
                item["candidate_ids"] = frozenset(ids)
            if "workspace_ids" in item:
                ids = item["workspace_ids"]
                if not isinstance(ids, list) or any(not isinstance(x, str) or not x for x in ids):
                    raise ValueError("invalid_workspace_scope")
                item["workspace_ids"] = frozenset(ids)
            principals[token] = Principal(**item)
        return cls(database_path=env.get("CORE_HR_DATABASE", ""), principals=principals,
            default_connections=json.loads(env.get("CORE_DEFAULT_CONNECTIONS_JSON", '{"daily":"daily","zoom":"zoom"}')),
            s3_bucket=env.get("CORE_S3_BUCKET") or None, s3_prefix=env.get("CORE_S3_PREFIX", "materials"),
            s3_endpoint=env.get("CORE_S3_ENDPOINT") or None, s3_region=env.get("CORE_S3_REGION") or None,
            host=env.get("CORE_HOST", "127.0.0.1"), port=int(env.get("CORE_PORT", "8093")),
            collector_enabled=env.get("HR_CORE_COLLECTOR_ENABLED", "0") == "1")

    def connect(self):
        # mode=rw prevents SQLite silently creating an unrelated empty HR store.
        return sqlite3.connect(Path(self.database_path).as_uri() + "?mode=rw", uri=True, timeout=10)

    def storage_registry(self):
        if not self.s3_bucket:
            return {}
        from materials_storage import S3Storage
        storage = S3Storage(self.s3_bucket, prefix=self.s3_prefix, endpoint_url=self.s3_endpoint, region_name=self.s3_region)
        return {(provider, connection): storage for provider, connection in self.default_connections.items()}
