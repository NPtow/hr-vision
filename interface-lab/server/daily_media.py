"""Daily rooms are recoverable across retries and expired invitation links."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from domain import Problem, require


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

    def request(self, path, body=None, method=None):
        require(self.configured, 'Видеосервис ещё не подключён. Нужна настройка Daily для команды.', 503)
        req = urllib.request.Request('https://api.daily.co/v1' + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'},
            method=method or ('GET' if body is None else 'POST'))
        try:
            with urllib.request.urlopen(req, timeout=18) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            # Provider bodies can contain credentials/account details.
            raise ProviderError(error.code) from None
        except (urllib.error.URLError, TimeoutError):
            raise Problem('Видеосервис не отвечает. Попробуйте ещё раз.', 502) from None

    def room(self, meeting_id):
        name = 'hrv-' + meeting_id
        path = '/rooms/' + urllib.parse.quote(name, safe='')
        properties = {
            'exp': int(time.time()) + 3600, 'eject_at_room_exp': True, 'max_participants': 2,
            'enable_prejoin_ui': True, 'enable_screenshare': False, 'enable_chat': False,
            'enable_knocking': False, 'enforce_unique_user_ids': True,
            'enable_recording': 'cloud', 'enable_transcription_storage': True,
            'enable_live_captions_ui': True,
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
        try:
            self.request('/rooms/' + urllib.parse.quote(room['name'], safe=''),
                         {'properties': {'exp': int(time.time()) + 2, 'eject_at_room_exp': True}})
        except ProviderError as error:
            # An expired/deleted provider room must not trap the interview in "live".
            if error.provider_status != 404:
                raise
