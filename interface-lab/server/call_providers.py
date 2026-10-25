"""Persist one provider before creating a room; never fall back to a second room."""
import json
import os
import secrets

from domain import Problem, require


class CallProviders:
    def __init__(self, connect, daily, zoom=None):
        self.connect, self._daily, self.zoom = connect, daily, zoom
        self.default_provider = os.environ.get('HR_CALL_PROVIDER', 'daily')
        if self.default_provider not in ('daily', 'zoom'):
            raise ValueError('Invalid default call provider')

    @property
    def daily(self):
        return self._daily() if callable(self._daily) else self._daily

    @staticmethod
    def init(db):
        db.execute('''CREATE TABLE IF NOT EXISTS cp_call_provider_choices (
          meeting_id TEXT PRIMARY KEY, provider TEXT NOT NULL,
          host_id TEXT, actor_id TEXT, state TEXT NOT NULL,
          room TEXT, request_id TEXT NOT NULL)''')

    @staticmethod
    def choice(db, meeting_id):
        row = db.execute('SELECT provider,host_id,actor_id,state,room,request_id '
                         'FROM cp_call_provider_choices WHERE meeting_id=?', (meeting_id,)).fetchone()
        return dict(zip(('provider', 'host_id', 'actor_id', 'state', 'room', 'request_id'), row)) if row else None

    def describe(self, db, meeting_id, room=None, actor_id=None):
        choice = self.choice(db, meeting_id)
        provider = choice['provider'] if choice else room.get('provider', 'daily') if room else self.default_provider
        require(provider in ('daily', 'zoom'), 'Неизвестный видеосервис встречи.', 409)
        zoom_available = bool(self.zoom and self.zoom.selection_enabled and
                              actor_id and self.zoom.host_for(actor_id))
        return {'callProvider': provider,
                'callProviderLocked': bool(room or choice and choice['state'] != 'selected'),
                'videoConfigured': bool(self.daily.configured if provider == 'daily'
                                        else self.zoom and self.zoom.configured),
                'callProviderOptions': [{'id': 'daily', 'available': bool(self.daily.configured)},
                                        {'id': 'zoom', 'available': zoom_available}]}

    def select(self, db, meeting_id, provider, actor_id, room=None):
        require(provider in ('daily', 'zoom'), 'Выберите Daily или Zoom.', 400)
        choice = self.choice(db, meeting_id)
        current = choice['provider'] if choice else (room or {}).get('provider', 'daily')
        locked = bool(room or choice and choice['state'] != 'selected')
        if locked:
            require(provider == current, 'Комната уже создаётся или открыта. Видеосервис менять нельзя.', 409)
            return
        host = None
        if provider == 'zoom':
            require(self.zoom and self.zoom.selection_enabled, 'Zoom пока не включён для новых встреч.', 503)
            host = self.zoom.host_for(actor_id)
            require(host, 'Для организатора не подключён аккаунт Zoom.', 409)
        db.execute('''INSERT INTO cp_call_provider_choices VALUES (?,?,?,?,?,NULL,?)
          ON CONFLICT(meeting_id) DO UPDATE SET provider=excluded.provider,host_id=excluded.host_id,
          actor_id=excluded.actor_id,state='selected',room=NULL,request_id=excluded.request_id''',
          (meeting_id, provider, host, actor_id, 'selected', secrets.token_urlsafe(24)))

    def prepare_default(self, db, meeting_id, room, actor_id):
        """Pin the configured choice once; an existing room always keeps its provider."""
        if self.choice(db, meeting_id):
            return
        if room:
            db.execute('INSERT OR IGNORE INTO cp_call_provider_choices VALUES (?,?,?,?,?,?,?)',
                (meeting_id, room.get('provider', 'daily'), room.get('zoomHostId'), actor_id,
                 'ready', json.dumps(room), secrets.token_urlsafe(24)))
        else:
            host = None
            if self.default_provider == 'zoom':
                require(self.zoom and self.zoom.selection_enabled, 'Zoom пока не включён для новых встреч.', 503)
                host = self.zoom.host_for(actor_id)
                require(host, 'Для организатора не подключён аккаунт Zoom.', 409)
            # Config I/O precedes the first write. A concurrent reservation must
            # never be reset from creating/ready back to selected.
            db.execute('INSERT OR IGNORE INTO cp_call_provider_choices VALUES (?,?,?,?,?,NULL,?)',
                (meeting_id, self.default_provider, host, actor_id, 'selected', secrets.token_urlsafe(24)))

    @staticmethod
    def invoke(operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except Problem:
            raise
        except ValueError as error:
            # ZoomMedia deliberately exposes safe errors, never provider bodies.
            status = getattr(error, 'status', 502)
            raise Problem(str(error), status if isinstance(status, int) else 502) from None

    def zoom_room(self, meeting_id):
        """Caller holds this meeting's operation lock, not the shared DB lock.

        The reservation commits before POST. Lost responses remain unresolved;
        another process or restart cannot blindly create a duplicate meeting.
        """
        require(self.zoom and self.zoom.configured, 'Zoom не настроен.', 503)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            choice = self.choice(db, meeting_id)
            require(choice and choice['provider'] == 'zoom', 'Обновите настройки встречи.', 409)
            if choice['state'] == 'ready':
                return json.loads(choice['room'])
            require(choice['state'] == 'selected',
                    'Создание комнаты Zoom ещё не подтверждено. Нужна проверка команды.', 409)
            db.execute("UPDATE cp_call_provider_choices SET state='creating' WHERE meeting_id=?", (meeting_id,))
        room = self.invoke(self.zoom.room, meeting_id, choice['host_id'], choice['request_id'])
        require(room.get('provider') == 'zoom' and room.get('id') and room.get('name'),
                'Zoom не подтвердил комнату.', 502)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE cp_call_provider_choices SET state='ready',room=? WHERE meeting_id=? AND state='creating' AND request_id=?",
                       (json.dumps(room), meeting_id, choice['request_id']))
        return room

    def daily_room(self, meeting_id, **kwargs):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            choice = self.choice(db, meeting_id)
            require(not choice or choice['provider'] == 'daily', 'Видеосервис изменился. Обновите встречу.', 409)
            db.execute("INSERT OR IGNORE INTO cp_call_provider_choices VALUES (?,'daily',NULL,NULL,'creating',NULL,?)",
                       (meeting_id, secrets.token_urlsafe(24)))
            db.execute("UPDATE cp_call_provider_choices SET state='creating' WHERE meeting_id=? AND state='selected'", (meeting_id,))
        # Daily uses its existing deterministic room name and recovery behavior.
        room = self.daily.room(meeting_id, **kwargs)
        with self.connect() as db:
            db.execute("UPDATE cp_call_provider_choices SET state='ready',room=? WHERE meeting_id=? AND provider='daily'",
                       (json.dumps(room), meeting_id))
        return room

    def for_room(self, room):
        provider = (room or {}).get('provider', 'daily')
        if provider == 'daily':
            return self.daily
        require(provider == 'zoom', 'Неизвестный видеосервис встречи.', 409)
        require(self.zoom and self.zoom.configured, 'Zoom не настроен.', 503)
        return self.zoom

    def finish(self, room):
        return self.invoke(self.for_room(room).finish, room)

    def materials(self, room, known=None):
        return self.invoke(self.for_room(room).materials, room, known)
