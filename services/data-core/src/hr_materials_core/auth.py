"""Service principals are server-configured; request bodies carry no identity."""
from dataclasses import dataclass
import secrets

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials


@dataclass(frozen=True)
class Principal:
    name: str
    role: str
    provider: str | None = None
    connection_id: str | None = None
    candidate_ids: frozenset[str] | None = None
    workspace_ids: frozenset[str] | None = None

    def __post_init__(self):
        if self.role not in ("portal", "source") or not self.name:
            raise ValueError("invalid_principal")
        if self.role == "source" and (not self.provider or not self.connection_id):
            raise ValueError("source_identity_required")


class Tokens:
    def __init__(self, principals: dict[str, Principal]):
        if not principals or any(len(token) < 32 or not token.isascii() or any(c.isspace() for c in token)
                                 for token in principals):
            raise ValueError("configured_service_tokens_must_have_32_characters")
        self._principals = principals.copy()

    def authenticate(self, credentials: HTTPAuthorizationCredentials | None) -> Principal:
        principal = None
        if credentials and credentials.scheme.lower() == "bearer":
            for token, configured in self._principals.items():
                if secrets.compare_digest(credentials.credentials.encode(), token.encode()):
                    principal = configured
        if principal is None:
            raise HTTPException(401, "unauthorized", headers={"WWW-Authenticate": "Bearer"})
        return principal
