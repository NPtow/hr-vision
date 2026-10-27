"""Daily rooms are recoverable across retries and expired invitation links."""
from . import observability
import json
import http.client
import logging
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.parse
import urllib.request
from .support import Problem, require, iso, now
from .media_runtime import current_deadline, provider_budget, provider_timeout


log = logging.getLogger(__name__)


class ProviderError(Problem):
    def __init__(self, code):
        self.provider_status = code
        super().__init__(f'Видеосервис не выполнил запрос (HTTP {code}). Проверьте подключение и тариф.', 502)


class DailyMedia:
    def __init__(self, key, enabled):
        self.key = key
        self.enabled = enabled

    @property
    def configured(self):
        return bool(self.key and self.enabled)

    @observability.operation("daily.request")
    def request(self, path, body=None, method=None):
        started = time.monotonic()
        try:
            return self._request(path, body, method)
        except Problem as error:
            status = getattr(error, 'provider_status', error.status)
            if status != 404:  # Missing/expired rooms are a normal recovery path.
                segments = path.partition('?')[0].strip('/').split('/')
                operation = segments[0] if segments[0] in ('rooms', 'recordings', 'transcript', 'meeting-tokens') else 'api'
                if segments[-1] in ('start', 'stop', 'presence', 'eject', 'access-link'):
                    operation += '.' + segments[-1]
                log.warning('daily_request_failed operation=%s status=%s elapsed_ms=%d',
                            operation, status, int((time.monotonic() - started) * 1000))
            raise

    def _request(self, path, body=None, method=None):
        require(self.configured, 'Видеосервис ещё не подключён. Нужна настройка Daily для команды.', 503)
        req = urllib.request.Request('https://api.daily.co/v1' + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'},
            method=method or ('GET' if body is None else 'POST'))
        try:
            with urllib.request.urlopen(req, timeout=provider_timeout()) as response:
                content = response.read(2 * 1024 * 1024 + 1)
            require(len(content) <= 2 * 1024 * 1024, 'Видеосервис вернул слишком большой ответ.', 502)
            result = json.loads(content)
            require(isinstance(result, dict), 'Видеосервис вернул неверный ответ. Повторите попытку.', 502)
            return result
        except urllib.error.HTTPError as error:
            # Provider bodies can contain credentials/account details.
            raise ProviderError(error.code) from None
        except (OSError, http.client.HTTPException):
            raise Problem('Видеосервис не отвечает. Попробуйте ещё раз.', 502) from None
        except ValueError:
            raise Problem('Видеосервис вернул неверный ответ. Повторите попытку.', 502) from None

    def room(self, meeting_id, *, recordable=True, screenshare=False):
        name = 'hrv-' + meeting_id
        path = '/rooms/' + urllib.parse.quote(name, safe='')
        properties = {
            'exp': int(time.time()) + 3600, 'eject_at_room_exp': True, 'max_participants': 2,
            'enable_prejoin_ui': True, 'enable_screenshare': bool(screenshare), 'enable_chat': False,
            'enable_knocking': False, 'enforce_unique_user_ids': True,
            'enable_transcription_storage': bool(recordable),
            'enable_live_captions_ui': bool(recordable),
            'permissions': {'canAdmin': False},
            # The Daily live API accepts false and returns disabled mode "".
            # Its published OpenAPI omits this supported boolean value; [] is
            # rejected in the real API. Verified by isolated provider probe.
            'enable_recording': 'cloud' if recordable else False,
        }
        try:
            existing = self.request(path)
        except ProviderError as error:
            if error.provider_status != 404:
                raise
            room = self.request('/rooms', {'name': name, 'privacy': 'private', 'properties': properties})
        else:
            require(existing.get('privacy') == 'private', 'Настройки комнаты изменились. Обратитесь к команде.', 502)
            # Also recovers a room created before a token request/DB commit failed.
            room = self.request(path, {'properties': properties})
        parsed = urllib.parse.urlparse(str(room.get('url', '')))
        require(room.get('name') == name and room.get('privacy') == 'private'
                and parsed.scheme == 'https' and parsed.hostname
                and parsed.hostname.endswith('.daily.co') and room.get('id'),
                'Провайдер не вернул приватную комнату.', 502)
        return {key: room[key] for key in ('name', 'url', 'id')}

    def finish(self, room):
        """Stop capture and disconnect the live session before expiring its URL.

        Changing exp does not eject clients already joined (verified live).
        Expired room GET404 is not session absence either: presence is separate.
        Every retry repeats the provider commands; failures never imply success.
        """
        path = '/rooms/' + urllib.parse.quote(room['name'], safe='')
        recording = {'type': 'cloud'}
        if room.get('recordingInstanceId'):
            recording['instanceId'] = room['recordingInstanceId']
        errors = []
        # Even if one capture channel fails, still try to close the other and
        # disconnect users. Keep the meeting live for an explicit safe retry.
        stops = () if room.get('captureNeverStarted') is True else (
            ('/recordings/stop', recording), ('/transcription/stop', {}))
        for suffix, body in stops:
            try:
                self.request(path + suffix, body)
            except Problem as error:
                errors.append(error)
        participants = self._presence(room)
        if participants:
            ids = [person['id'] for person in participants]
            user_ids = list(dict.fromkeys(person['userId'] for person in participants if person.get('userId')))
            ejected = self.request(path + '/eject', {'ids': ids, 'user_ids': user_ids, 'ban': True})
            confirmed = ejected.get('ejectedIds') if isinstance(ejected, dict) else None
            # Daily presence is eventually consistent. An explicit per-ID
            # acknowledgement is authoritative; generic HTTP200 is not.
            if (isinstance(confirmed, list) and all(isinstance(value, str) for value in confirmed)
                    and set(ids).issubset(confirmed)):
                participants = []
            else:
                for _ in range(12):
                    participants = self._presence(room)
                    if not participants:
                        break
                    time.sleep(0.25)
        require(not participants, 'Видеосервис ещё завершает звонок. Попробуйте завершить встречу ещё раз.', 502)
        for error in errors:
            # A missing room/action is safe only after independent proof that
            # its session is empty. Never swallow auth, limit or service errors.
            if not isinstance(error, ProviderError) or error.provider_status != 404:
                raise error
        try:
            self.request(path, {'properties': {'exp': int(time.time()) + 2, 'eject_at_room_exp': True}})
        except ProviderError as error:
            if error.provider_status != 404:
                raise

    def _presence(self, room):
        result = self.request('/rooms/' + urllib.parse.quote(room['name'], safe='') + '/presence?limit=100')
        require(isinstance(result, dict) and type(result.get('total_count')) is int and
                isinstance(result.get('data'), list) and result['total_count'] == len(result['data']) <= 100,
                'Не удалось подтвердить завершение звонка у видеосервиса.', 502)
        participants = result['data']
        require(all(isinstance(person, dict) and isinstance(person.get('id'), str) and person['id'] and
                    person.get('room', room['name']) == room['name'] and
                    (person.get('userId') is None or isinstance(person['userId'], str)) for person in participants),
                'Не удалось определить участников звонка.', 502)
        return participants

    def materials(self, room, known=None, *, include_metadata=False):
        """Read the two provider channels independently, without storing media URLs.

        `known` must be trusted metadata already bound to this exact room. It is
        useful when list API is temporarily unavailable but direct access still
        works. Provider events/metadata, never completed status, prove readiness.
        """
        require(isinstance(room, dict) and room.get('name') and room.get('id'), 'Комната не найдена.', 409)
        known = known or {}
        with provider_budget():
            deadline = current_deadline()
            with ThreadPoolExecutor(max_workers=2) as pool:
                video = pool.submit(self._bounded_channel, deadline, room, 'video', known.get('recordings', []), include_metadata)
                transcript = pool.submit(self._bounded_channel, deadline, room, 'transcription', known.get('transcripts', []), include_metadata)
                recordings, video_state = video.result()
                transcripts, transcript_state = transcript.result()
        return {'recordings': recordings, 'transcripts': transcripts,
                'video': video_state, 'transcription': transcript_state, 'checkedAt': iso(now())}

    def _bounded_channel(self, deadline, *args):
        # Thread-local request budgets must be carried into both executor workers.
        with provider_budget(deadline=deadline):
            return self._material_channel(*args)

    def _material_channel(self, room, kind, known, include_metadata=False):
        video = kind == 'video'
        label = 'видео' if video else 'расшифровку'
        identifier, room_key = ('id', 'room_name') if video else ('transcriptId', 'roomId')
        room_value = room['name'] if video else room['id']
        path = '/recordings?' + urllib.parse.urlencode({'room_name': room['name'], 'limit': 100}) if video else \
               '/transcript?' + urllib.parse.urlencode({'roomId': room['id'], 'limit': 100})
        errors, candidates, pending = [], {}, False
        for item in known[:20]:
            if isinstance(item, dict) and isinstance(item.get('id'), str) and 0 < len(item['id']) <= 200:
                candidates[item['id']] = {'id': item['id'], 'duration': item.get('duration'),
                                          'providerMetadata': item.get('providerMetadata', {})}
        try:
            result = self.request(path)
            require(isinstance(result, dict) and isinstance(result.get('data'), list), 'Неверный ответ видеосервиса.', 502)
            rows = [row for row in result['data'] if isinstance(row, dict) and row.get(room_key) == room_value]
            if len(rows) > 20:
                errors.append('Слишком много частей записи. Показаны первые 20.')
            for row in rows[:20]:
                status = row.get('status')
                rid = row.get(identifier)
                if status in ('finished', 't_finished'):
                    require(isinstance(rid, str) and 0 < len(rid) <= 200, 'Не удалось определить файл.', 502)
                    metadata = {}
                    if isinstance(row.get('mtgSessionId'), str) and len(row['mtgSessionId']) <= 200:
                        metadata['meetingSessionId'] = row['mtgSessionId']
                    if video and type(row.get('start_ts')) in (int, float) and 0 < row['start_ts'] < 4102444800:
                        metadata['startTs'] = row['start_ts']
                    for field in ('created_at', 'updated_at') if not video else ():
                        if isinstance(row.get(field), str) and len(row[field]) <= 80:
                            metadata[field] = row[field]
                    candidates[rid] = {'id': rid, 'duration': row.get('duration'), 'providerMetadata': metadata}
                elif status in ('failed', 'error', 't_failed', 't_error', 'canceled', 'cancelled'):
                    errors.append('Видеосервис сообщил об ошибке обработки.')
                else:
                    pending = True
        except (Problem, OSError, TimeoutError, ValueError, TypeError):
            errors.append(f'Не удалось проверить {label} у видеосервиса. Попробуйте ещё раз.')
        files = []
        for rid, item in list(candidates.items())[:20]:
            try:
                provider_timeout()
                access_path = ('/recordings/' if video else '/transcript/') + urllib.parse.quote(rid, safe='') + '/access-link'
                access = self.request(access_path)
                require(isinstance(access, dict), 'Неверный ответ видеосервиса.', 502)
                url = access.get('download_link') or (access.get('link') if not video else None)
                require(isinstance(url, str) and urllib.parse.urlsplit(url).scheme == 'https'
                        and urllib.parse.urlsplit(url).hostname, 'Не удалось получить файл.', 502)
                if video:
                    expires = access.get('expires')
                    files.append({'id': rid, 'url': url, 'duration': item.get('duration'),
                                  'expires': expires if type(expires) is int and expires > 0 else None})
                else:
                    with urllib.request.urlopen(url, timeout=provider_timeout()) as response:
                        content = response.read(1024 * 1024 + 1)
                    require(len(content) <= 1024 * 1024, 'Расшифровка слишком большая.', 502)
                    files.append({'id': rid, 'text': content.decode('utf-8')})
                if include_metadata:
                    files[-1]['providerMetadata'] = item.get('providerMetadata', {})
            except (Problem, OSError, http.client.HTTPException, ValueError, TypeError):
                errors.append(f'Не удалось получить {label}. Попробуйте ещё раз.')
        state = 'ready' if files else 'error' if errors else 'processing' if pending else 'absent'
        return files, {'status': state, 'ids': list(candidates)[:20],
                       **({'complete': bool(files and not errors and not pending and len(files) == len(candidates))}
                          if include_metadata else {}),
                       **({'error': ' '.join(dict.fromkeys(errors))} if errors else {})}
