"""Zoom cloud recordings as provider-neutral capture metadata.

Contract: https://developers.zoom.us/docs/api/meetings/#tag/Cloud-Recording
Only the exact stored meeting UUID is queried. The reusable numeric meeting ID
is also checked, but never substitutes for the session UUID. Provider responses,
temporary download URLs and bearer tokens are not stored in the catalog.
"""
import datetime as dt
import re
from urllib.parse import urlsplit

from domain import Problem
from materials_capture import Artifact, Discovery, HttpSource, Source
from media_runtime import provider_budget


def _identifier(value):
    return isinstance(value, str) and 0 < len(value) <= 200 and not any(ord(c) < 33 or ord(c) == 127 for c in value)


def _meeting_number(value):
    return type(value) in (str, int) and bool(re.fullmatch(r'[0-9]{9,12}', str(value)))


class ZoomCapture:
    def __init__(self, media, connection_id='zoom', *, max_files=1000):
        if not _identifier(connection_id) or type(max_files) is not int or not 1 <= max_files <= 1000:
            raise ValueError('Invalid Zoom adapter configuration')
        self.media, self.connection_id, self.max_files = media, connection_id, max_files

    def _source(self, ref):
        source = Source.from_ref(ref)
        if (source.provider != 'zoom' or source.connection_id != self.connection_id or
                not _meeting_number(source.room_id) or not _identifier(source.room_name) or
                not _identifier(source.session_id) or
                (source.capture_id is not None and not _identifier(source.capture_id))):
            raise ValueError('Exact Zoom account, meeting number and session UUID required')
        if isinstance(ref, dict) and 'meeting_number' in ref and str(ref['meeting_number']) != source.room_id:
            raise ValueError('Conflicting Zoom meeting number')
        return source

    @staticmethod
    def _room(source):
        return {'provider': 'zoom', 'id': source.room_id, 'name': source.room_name,
                'meetingNumber': source.room_id, 'meetingSessionId': source.session_id,
                'connectionId': source.connection_id}

    def _rows(self, source):
        result = self.media.recordings(self._room(source))
        if (not isinstance(result, dict) or not _meeting_number(result.get('id')) or
                str(result['id']) != source.room_id or result.get('uuid') != source.session_id):
            raise ValueError('Zoom response belongs to a different meeting instance')
        rows = result.get('recording_files')
        count = result.get('recording_count')
        if (not isinstance(rows, list) or len(rows) > self.max_files or
                (count is not None and (type(count) is not int or count < len(rows)))):
            raise ValueError('Invalid Zoom metadata response')
        complete = result.get('listing_complete', True) is True and not result.get('next_page_token') and (count is None or count == len(rows))
        return rows, complete

    @staticmethod
    def _kind(row):
        if row.get('file_type') == 'MP4':
            return 'video'
        if (row.get('file_type') == 'TRANSCRIPT' and row.get('recording_type') == 'audio_transcript'
                and row.get('file_extension', 'VTT') == 'VTT'):
            return 'transcript'
        # M4A, chat, CC and generated summaries are outside the current worker
        # contract. In particular, do not treat AI summaries as transcripts.
        return None

    def _artifact(self, source, row):
        if not isinstance(row, dict):
            raise ValueError('Invalid Zoom file metadata')
        kind = self._kind(row)
        if kind is None:
            return None
        if not _identifier(row.get('id')) or (row.get('meeting_id') is not None and
                row['meeting_id'] != source.session_id):
            raise ValueError('Zoom file belongs to a different meeting instance')
        raw_status = row.get('status')
        status = {'completed': 'ready', 'processing': 'processing', 'failed': 'failed',
                  'deleted': 'missing'}.get(raw_status, 'blocked')
        metadata = {'provider_status': raw_status if raw_status in
                    ('completed', 'processing', 'failed', 'deleted') else 'unknown',
                    'requires_owned_playback': kind == 'video'}
        if kind == 'transcript':
            metadata['format'] = 'vtt'
        size = row.get('file_size')
        if size is not None:
            if type(size) is not int or size < 0:
                raise ValueError('Invalid Zoom file size')
            metadata['size'] = size
            if status == 'ready' and size == 0:
                status = 'missing'
        # Parse rather than persist arbitrary strings from external metadata.
        dates = []
        for field in ('recording_start', 'recording_end'):
            value = row.get(field)
            try:
                parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    raise ValueError()
                dates.append(parsed)
                metadata[field] = parsed.isoformat()
            except (ValueError, TypeError, AttributeError):
                dates.append(None)
        if all(dates) and dates[1] >= dates[0]:
            metadata['duration'] = (dates[1] - dates[0]).total_seconds()
        return Artifact(source, row['id'], kind, status, metadata)

    def discover(self, source_ref):
        try:
            source = self._source(source_ref)
        except (ValueError, TypeError):
            state = {'status': 'blocked', 'complete': False, 'reason': 'exact_session_required'}
            return Discovery([], {k: dict(state) for k in ('video', 'transcript')}, False)
        try:
            with provider_budget():
                rows, complete = self._rows(source)
        except (Problem, OSError, TimeoutError, ValueError, TypeError, KeyError):
            state = {'status': 'error', 'complete': False, 'reason': 'provider_metadata_unavailable'}
            return Discovery([], {k: dict(state) for k in ('video', 'transcript')}, False)
        artifacts, invalid, conflicts = {}, set(), set()
        for row in rows:
            kind = self._kind(row) if isinstance(row, dict) else None
            try:
                artifact = self._artifact(source, row)
                if artifact:
                    if artifact.identity in conflicts:
                        continue
                    previous = artifacts.get(artifact.identity)
                    if previous is not None and previous != artifact:
                        del artifacts[artifact.identity]
                        conflicts.add(artifact.identity)
                        raise ValueError('Conflicting Zoom file metadata')
                    artifacts[artifact.identity] = artifact
            except (ValueError, TypeError):
                invalid.update((kind,) if kind else ('video', 'transcript'))
        states = {}
        for kind in ('video', 'transcript'):
            values = [a.status for a in artifacts.values() if a.kind == kind]
            ready = 'ready' in values
            blocked = kind in invalid or 'blocked' in values or not complete
            waiting = 'processing' in values
            failed = any(value in ('failed', 'missing') for value in values)
            state = ('partial' if ready else 'blocked') if blocked else \
                    'partial' if ready and (waiting or failed) else 'ready' if ready else \
                    'error' if failed else 'processing' if waiting else 'absent'
            states[kind] = {'status': state, 'complete': not blocked and not waiting,
                            **({'reason': 'ambiguous_provider_binding' if kind in invalid else
                               'metadata_listing_incomplete' if not complete else 'unknown_provider_status'}
                               if blocked else {})}
        return Discovery(list(artifacts.values()), states, all(s['complete'] for s in states.values()))

    def _inspect_row(self, artifact):
        artifact = Artifact.from_ref(artifact)
        source = self._source(artifact.source)
        if artifact.kind not in ('video', 'transcript') or not _identifier(artifact.provider_asset_id):
            raise ValueError('Invalid Zoom artifact')
        rows, complete = self._rows(source)
        matches = [row for row in rows if isinstance(row, dict) and row.get('id') == artifact.provider_asset_id]
        if len(matches) != 1:
            raise ValueError('Exact Zoom recording file unavailable')
        inspected = self._artifact(source, matches[0])
        if inspected is None or inspected.kind != artifact.kind:
            raise ValueError('Zoom artifact kind changed')
        return inspected, matches[0]

    def inspect(self, artifact):
        return self._inspect_row(artifact)[0]

    def resolve(self, artifact):
        inspected, row = self._inspect_row(artifact)
        if inspected.status != 'ready':
            raise ValueError('Zoom artifact is not ready')
        from zoom_api_client import ZoomAPI
        if isinstance(self.media, ZoomAPI):
            return self.media.download_source(self._room(inspected.source), inspected.provider_asset_id)
        resolved = self.media.resolve_download(row)
        url, headers = resolved.get('url'), resolved.get('headers')
        if not isinstance(url, str) or len(url) > 16384 or '\\' in url or any(ord(c) < 33 for c in url):
            raise ValueError('Invalid Zoom download source')
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower()
        if (parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None or
                parsed.port not in (None, 443) or parsed.fragment or
                not (host in ('zoom.us', 'zoom.com') or host.endswith(('.zoom.us', '.zoom.com'))) or
                not isinstance(headers, dict) or set(headers) != {'Authorization'} or
                not isinstance(headers['Authorization'], str) or
                not headers['Authorization'].startswith('Bearer ') or
                len(headers['Authorization']) <= 7 or
                any(ord(c) < 32 or ord(c) == 127 for c in headers['Authorization'])):
            raise ValueError('Invalid Zoom download source')
        return HttpSource(url, headers=dict(headers))

    def verify_webhook(self, raw, headers):
        # No unverified webhook can create or rebind a material source. This
        # pilot uses the existing bounded scheduler, not a new subscription.
        raise Problem('Zoom materials webhook ingestion is disabled.', 503)
