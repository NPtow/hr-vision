"""Daily metadata adapter; API authentication stays in DailyMedia.request.

Contract references (checked 2026-10-25): Daily recordings/list-recordings,
recordings/get-recording, transcripts/list-transcripts, transcripts/get-transcript,
and https://docs.daily.co/reference/rest-api/webhooks#hmac.
Room names are reusable: never bind a file without an exact mtgSessionId.
"""
import base64
import binascii
import hashlib
import hmac
import json
import math
import time
from dataclasses import replace
from urllib.parse import quote, urlencode, urlsplit

from domain import Problem
from materials_capture import Artifact, Discovery, HttpSource, Source
from media_runtime import provider_budget


def _identifier(value):
    return isinstance(value, str) and 0 < len(value) <= 200 and not any(ord(c) < 32 for c in value)


class DailyCapture:
    def __init__(self, media, connection_id='daily', *, webhook_hmac=None,
                 max_pages=10, page_size=100, webhook_tolerance=300):
        if not _identifier(connection_id) or not 1 <= max_pages <= 100 or not 1 <= page_size <= 100:
            raise ValueError('Invalid Daily adapter configuration')
        self.media = media
        self.connection_id = connection_id
        self.max_pages = max_pages
        self.page_size = page_size
        self.webhook_hmac = webhook_hmac
        self.webhook_tolerance = webhook_tolerance

    def _source(self, source_ref, *, allow_owned_room=False):
        source = Source.from_ref(source_ref)
        proof = source_ref.get('exclusive_room_owner') if isinstance(source_ref, dict) else None
        # This attestation is server-owned metadata, never a public request option.
        # A room exclusively named for one domain owner can discover session refs;
        # every emitted artifact still receives its own exact provider session.
        owned = (allow_owned_room and isinstance(proof, dict) and
                 proof.get('kind') in ('interview', 'meeting') and _identifier(proof.get('id')) and
                 source.room_name == 'hrv-' + proof['id'])
        if (source.provider != 'daily' or source.connection_id != self.connection_id or
                not all(_identifier(v) for v in (source.room_id, source.room_name)) or
                not (_identifier(source.session_id) or (owned and source.session_id == '')) or
                (source.capture_id is not None and not _identifier(source.capture_id))):
            raise ValueError('Exact Daily room and session binding required')
        return source

    def discover(self, source_ref: dict) -> Discovery:
        try:
            source = self._source(source_ref, allow_owned_room=True)
        except (ValueError, TypeError):
            blocked = {'status': 'blocked', 'complete': False, 'reason': 'exact_session_required'}
            return Discovery([], {kind: dict(blocked) for kind in ('video', 'transcript')}, False)
        artifacts, statuses = [], {}
        # Channel failures are independent; neither access-link nor file GET occurs here.
        for kind in ('video', 'transcript'):
            try:
                with provider_budget():
                    found, state = self._discover_channel(source, kind)
            except (Problem, OSError, TimeoutError, ValueError, TypeError, KeyError):
                found, state = [], {'status': 'error', 'complete': False, 'reason': 'provider_metadata_unavailable'}
            artifacts.extend(found)
            statuses[kind] = state
        return Discovery(artifacts, statuses, all(v['complete'] for v in statuses.values()))

    def _discover_channel(self, source, kind):
        transcript = kind == 'transcript'
        identifier = 'transcriptId' if transcript else 'id'
        base = '/transcript' if transcript else '/recordings'
        params = {'roomId': source.room_id} if transcript else {
            'room_name': source.room_name}
        if transcript and source.session_id:
            params['mtgSessionId'] = source.session_id
        params['limit'] = self.page_size
        seen, cursors, artifacts = set(), set(), {}
        pending = failed = ambiguous = False
        complete, reason = False, 'metadata_page_limit'
        for _ in range(self.max_pages):
            result = self.media.request(base + '?' + urlencode(params))
            rows, total = result.get('data'), result.get('total_count')
            if (not isinstance(rows, list) or len(rows) > self.page_size or type(total) is not int or
                    total < len(rows) or total < 0):
                raise ValueError('Invalid metadata page')
            page_ids = []
            for row in rows:
                if not isinstance(row, dict) or not _identifier(row.get(identifier)):
                    raise ValueError('Invalid provider asset identity')
                rid = row[identifier]
                page_ids.append(rid)
                if rid in seen:
                    continue
                seen.add(rid)
                session = row.get('mtgSessionId')
                if source.session_id and session and session != source.session_id:
                    continue
                room = row.get('roomId') if transcript else row.get('room_name')
                expected = source.room_id if transcript else source.room_name
                if room and room != expected:
                    continue
                # Recording list schemas omit room_name; inspect the exact record.
                if not room or not session:
                    row = self.media.request(base + '/' + quote(rid, safe=''))
                try:
                    exact_source = source
                    if not source.session_id:
                        if not _identifier(row.get('mtgSessionId')):
                            raise ValueError('Missing session')
                        exact_source = replace(source, session_id=row['mtgSessionId'])
                    item = self._artifact(exact_source, row, kind, rid)
                except ValueError:
                    ambiguous = True
                    continue
                artifacts[item.asset_key] = item
                pending |= item.status == 'processing'
                failed |= item.status in ('failed', 'missing')
                ambiguous |= item.status == 'blocked'
            if len(seen) >= total:
                complete, reason = True, None
                break
            if not page_ids or page_ids[-1] in cursors:
                reason = 'metadata_pagination_stalled'
                break
            cursors.add(page_ids[-1])
            params['starting_after'] = page_ids[-1]
        complete = complete and not ambiguous
        items = list(artifacts.values())
        ready = any(item.status == 'ready' for item in items)
        state = ('partial' if ready else 'blocked') if ambiguous or not complete else \
                'partial' if ready and (pending or failed) else 'ready' if ready else \
                'error' if failed else 'processing' if pending else 'absent'
        return items, {'status': state, 'complete': complete,
                       **({'reason': 'ambiguous_provider_binding' if ambiguous else reason} if not complete else {})}

    def _artifact(self, source, row, channel, rid):
        transcript = channel == 'transcript'
        if (not isinstance(row, dict) or row.get('transcriptId' if transcript else 'id') != rid or
                row.get('mtgSessionId') != source.session_id or
                row.get('roomId' if transcript else 'room_name') !=
                (source.room_id if transcript else source.room_name)):
            raise ValueError('Provider file is not bound to this room and session')
        raw_status = row.get('status')
        ready = raw_status == ('t_finished' if transcript else 'finished')
        status = ('ready' if ready else 'missing' if raw_status == 't_deleted' else
                  'failed' if raw_status in ('error', 'failed', 'canceled', 'cancelled', 't_error') else
                  'processing' if raw_status in ('in-progress', 't_in_progress') else 'blocked')
        if transcript and ready and row.get('isVttAvailable') is False:
            status = 'missing'
        if not transcript and ready and row.get('duration') == 0:
            status = 'missing'
        # Raw tracks require per-track object sources; do not resolve a bundle as video.
        if row.get('type') == 'raw-tracks' or row.get('tracks'):
            status = 'blocked'
        kind = 'audio' if row.get('type') == 'cloud-audio-only' else channel
        metadata = {'provider_status': raw_status}
        for field in ('duration', 'start_ts'):
            value = row.get(field)
            if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                metadata[field] = value
        for field in ('created_at', 'updated_at'):
            value = row.get(field)
            if isinstance(value, str) and len(value) <= 80:
                metadata[field] = value
        return Artifact(source, rid, kind, status, metadata)

    def inspect(self, artifact: Artifact) -> Artifact:
        artifact = Artifact.from_ref(artifact)
        source = self._source(artifact.source)
        if artifact.kind not in ('video', 'audio', 'transcript') or not _identifier(artifact.provider_asset_id):
            raise ValueError('Invalid artifact')
        channel = 'transcript' if artifact.kind == 'transcript' else 'video'
        base = '/transcript/' if channel == 'transcript' else '/recordings/'
        row = self.media.request(base + quote(artifact.provider_asset_id, safe=''))
        result = self._artifact(source, row, channel, artifact.provider_asset_id)
        # A known audio kind may be absent in Daily's get schema; keep its trusted identity.
        if artifact.kind == 'audio' and result.kind == 'video' and row.get('type') is None:
            result = Artifact(source, result.provider_asset_id, 'audio', result.status, result.metadata)
        if result.kind != artifact.kind:
            raise ValueError('Artifact kind changed')
        return result

    def resolve(self, artifact: Artifact) -> HttpSource:
        artifact = self.inspect(artifact)
        if artifact.status != 'ready':
            raise ValueError('Provider artifact is not ready')
        transcript = artifact.kind == 'transcript'
        path = ('/transcript/' if transcript else '/recordings/') + quote(artifact.provider_asset_id, safe='') + '/access-link'
        access = self.media.request(path)
        url = access.get('link') if transcript else access.get('download_link')
        if not isinstance(url, str) or len(url) > 16384:
            raise ValueError('Invalid access link')
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError('Invalid access link')
        expires = access.get('expires')
        if expires is not None and (type(expires) is not int or expires <= time.time()):
            raise ValueError('Expired access link')
        return HttpSource(url, expires_at=expires)

    def verify_webhook(self, raw: bytes, headers: dict, *, now=None) -> dict:
        """Authenticate, return URL-free hints. Events alone never bind a recording.

        Official HMAC recipe: base64(HMAC_SHA256(base64decode(secret),
        timestamp + '.' + JSON.stringify(event))). Raw transport bytes preserve
        the provider's serialization; parsing/re-serializing can change signatures.
        The application must durably deduplicate the returned event_id.
        """
        if not self.webhook_hmac:
            raise Problem('Daily webhook verification is disabled.', 503)
        if not isinstance(raw, bytes) or len(raw) > 256 * 1024:
            raise Problem('Invalid webhook.', 400)
        headers = {key.lower(): value for key, value in headers.items()}
        timestamp, signature = headers.get('x-webhook-timestamp'), headers.get('x-webhook-signature')
        try:
            if (not isinstance(timestamp, str) or not timestamp.isdigit() or len(timestamp) > 16 or
                    not isinstance(signature, str)):
                raise ValueError()
            timestamp_seconds = int(timestamp)
            if timestamp_seconds >= 100_000_000_000:
                timestamp_seconds /= 1000
            if abs((time.time() if now is None else now) - timestamp_seconds) > self.webhook_tolerance:
                raise ValueError()
            secret = base64.b64decode(self.webhook_hmac, validate=True)
            if len(secret) < 16:
                raise ValueError()
            expected = base64.b64encode(hmac.digest(secret, timestamp.encode() + b'.' + raw, hashlib.sha256)).decode()
            if not hmac.compare_digest(expected, signature):
                raise ValueError()
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise ValueError()
        except (ValueError, TypeError, binascii.Error, UnicodeError):
            raise Problem('Invalid webhook signature.', 401) from None
        if event == {'test': 'test'}:
            return {'provider': 'daily', 'connection_id': self.connection_id, 'test': True}
        allowed = {'recording.started', 'recording.ready-to-download', 'recording.error',
                   'transcript.started', 'transcript.ready-to-download', 'transcript.error',
                   'meeting.started', 'meeting.ended'}
        payload = event.get('payload')
        if event.get('type') not in allowed or not _identifier(event.get('id')) or not isinstance(payload, dict):
            raise Problem('Unsupported webhook event.', 400)
        clean = {}
        for key in ('recording_id', 'id', 'room_id', 'room_name', 'mtg_session_id', 'mtgSessionId',
                    'meeting_id', 'instance_id', 'status', 'type'):
            if _identifier(payload.get(key)):
                clean[key] = payload[key]
        return {'provider': 'daily', 'connection_id': self.connection_id,
                'event_id': event['id'], 'type': event['type'], 'payload': clean}
