"""Provider-neutral metadata boundary. No capture adapter downloads media bytes."""
from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Protocol


def owner_source_ref(owner_kind, owner_id, room, provider='daily', connection_id='daily'):
    """Build identical capture identity in completion hooks and recovery scans.

    Only call with a room read from its authorized business owner in the DB.
    The exclusive-room assertion is never accepted from an HTTP request body.
    """
    if owner_kind not in ('interview', 'meeting') or not isinstance(room, dict) or not room.get('id') or not room.get('name'):
        raise ValueError('Invalid material owner room')
    if provider == 'zoom':
        # A reusable meeting number is not a recording instance. Only server-
        # stored exact UUID + connection can authorize capture or backfill.
        number, session = room.get('meetingNumber'), room.get('meetingSessionId')
        if (room.get('provider') != 'zoom' or type(number) not in (str, int) or
                not re.fullmatch(r'[0-9]{9,12}', str(number)) or str(room['id']) != str(number) or
                not isinstance(session, str) or not 0 < len(session) <= 200 or
                any(ord(c) < 33 or ord(c) == 127 for c in session) or
                not isinstance(connection_id, str) or not connection_id or
                room.get('connectionId') != connection_id):
            raise ValueError('Exact Zoom account, meeting number and session UUID required')
        capture_id = hashlib.sha256(json.dumps([owner_kind, owner_id, provider,
            connection_id, str(number), session], separators=(',', ':')).encode()).hexdigest()
        return {'room_id': str(number), 'room_name': room['name'],
                'meeting_number': str(number), 'session_id': session,
                'capture_instance_id': capture_id}
    capture_id = room.get('captureInstanceId') or hashlib.sha256(json.dumps(
        [owner_kind, owner_id, provider, connection_id, room['id']], separators=(',', ':')).encode()).hexdigest()
    ref = {'room_id': room['id'], 'room_name': room['name'], 'capture_instance_id': capture_id}
    session = room.get('meetingSessionId') or room.get('session_id') or room.get('sessionId') or room.get('mtgSessionId')
    if session:
        ref['session_id'] = session
    if provider == 'daily' and room['name'] == 'hrv-' + owner_id:
        ref['exclusive_room_owner'] = {'kind': owner_kind, 'id': owner_id}
    return ref


@dataclass(frozen=True)
class Source:
    provider: str
    connection_id: str
    room_id: str
    room_name: str
    session_id: str
    capture_id: str | None = None

    @classmethod
    def from_ref(cls, value):
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise ValueError('Invalid capture source')
        return cls(**{key: value.get(key, '') for key in
                      ('provider', 'connection_id', 'room_id', 'room_name', 'session_id')},
                   capture_id=value.get('capture_id', value.get('capture_instance_id')))


@dataclass(frozen=True)
class Artifact:
    source: Source
    provider_asset_id: str
    kind: str
    status: str
    metadata: dict = field(default_factory=dict)

    @property
    def identity(self):
        return (self.source.provider, self.source.connection_id, self.source.session_id,
                self.kind, self.provider_asset_id)

    @property
    def asset_key(self):
        return hashlib.sha256(json.dumps(self.identity, separators=(',', ':')).encode()).hexdigest()

    @classmethod
    def from_ref(cls, value):
        if isinstance(value, cls):
            return value
        return cls(Source.from_ref(value['source']), value['provider_asset_id'],
                   value['kind'], value.get('status', 'unknown'), value.get('metadata', {}))


@dataclass(frozen=True)
class Discovery:
    artifacts: list[Artifact]
    statuses: dict[str, dict]
    complete: bool


@dataclass(frozen=True)
class HttpSource:
    url: str = field(repr=False)
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    expires_at: int | None = None


class Capture(Protocol):
    def discover(self, source_ref: dict) -> Discovery: ...
    def inspect(self, artifact: Artifact) -> Artifact: ...
    def resolve(self, artifact: Artifact) -> HttpSource: ...
