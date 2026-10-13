"""Interactive Telegram adapter over the canonical candidate portal.

This module never performs network I/O or invents matches. The notification worker
sends durable replies after commit. Telegram ownership comes from the existing
one-use, authenticated portal link; a plain /start never creates a profile.
"""
import hashlib
import json
import re
import secrets
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

from domain import Problem, require
import employer_workflow


ACTION_SECONDS = 24 * 3600
PAGE_SIZE = 5
CALLBACK_RE = re.compile(r'^cb:([A-Za-z0-9_-]{32})$')
APPLICATION_LABELS = {'pending': 'Ждёт вашего решения', 'accepted': 'Вы согласовали вакансию',
                      'declined': 'Вы отказались от вакансии'}
MEETING_LABELS = {'pending': 'Ждёт подтверждения времени', 'confirmed': 'Время подтверждено',
                 'live': 'Встреча идёт', 'completed': 'Встреча завершена',
                 'cancelled': 'Встреча отменена', 'declined': 'Вы отклонили время'}


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _dump(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _time(value):
    # Always name the timezone, so a button approves an exact instant.
    moment = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(ZoneInfo('Europe/Moscow'))
    return moment.strftime('%d.%m.%Y %H:%M') + ' МСК (UTC+3)'


class CandidateBot:
    def __init__(self, portal, notifications):
        self.portal, self.notifications = portal, notifications
        with portal.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS cb_actions (
                digest TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, chat_id TEXT NOT NULL,
                action TEXT NOT NULL, application_id TEXT, meeting_id TEXT, revision INTEGER,
                start TEXT, end TEXT, snapshot TEXT NOT NULL, created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL, used_at INTEGER)''')
            db.execute('CREATE INDEX IF NOT EXISTS cb_actions_expiry ON cb_actions(expires_at)')

    def _candidate(self, db, chat_id):
        row = db.execute('''SELECT r.candidate_id FROM cn_recipients r JOIN cp_candidates c
            ON c.id=r.candidate_id WHERE r.telegram_chat_id=? AND r.telegram_verified_at IS NOT NULL
            AND c.email IS NOT NULL AND LENGTH(c.email)>0''', (chat_id,)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _url(config, connect=False):
        parts = urllib.parse.urlsplit(config.public_url)
        query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if k != 'telegram']
        if connect:
            query.append(('telegram', 'connect'))
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), ''))

    def _button(self, db, cid, chat, action, title, tick, application=None, meeting=None, snapshot=None):
        raw = secrets.token_urlsafe(24)
        db.execute('''INSERT INTO cb_actions
            (digest,candidate_id,chat_id,action,application_id,meeting_id,revision,start,end,
             snapshot,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            (_digest(raw), cid, chat, action, application['id'] if application else None,
             meeting['id'] if meeting else None, meeting['revision'] if meeting else None,
             meeting['start'] if meeting else None, meeting['end'] if meeting else None,
             _dump(snapshot or {}), tick, tick + ACTION_SECONDS))
        return {'text': title, 'callback_data': 'cb:' + raw}

    def _menu(self, config):
        return {'inline_keyboard': [[{'text': 'Открыть кабинет', 'url': self._url(config)}]]}

    def _role_content(self, config, role='menu'):
        if role == 'candidate':
            text = ('Пройдите одно интервью с нашим рекрутером — дальше выбирайте подходящие вакансии в личном кабинете.\n\n'
                    'Вы решаете, каким компаниям передать профиль. Предложения и приглашения можно получать здесь. '
                    'Время каждой встречи подтверждаете отдельно.')
            button = {'text': 'Открыть кабинет кандидата', 'url': self._url(config, True)}
        elif role == 'employer':
            text = ('Создайте вакансию и получайте подборки кандидатов с готовыми интервью. '
                    'Просматривайте профили, приглашайте на встречи и отправляйте офферы в кабинете работодателя.')
            parts = urllib.parse.urlsplit(config.public_url)
            url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, '/iframe.html',
                urllib.parse.urlencode({'id': 'hr-vision-product--manager', 'viewMode': 'story'}), ''))
            button = {'text': 'Открыть кабинет работодателя', 'url': url}
        else:
            return {'text': 'HR Vision помогает кандидатам находить работу, а компаниям — знакомиться с подходящими людьми по готовым профилям и интервью.\n\nКак вы хотите пользоваться сервисом?',
                'reply_markup': {'inline_keyboard': [[
                    {'text': 'Я кандидат', 'callback_data': 'role:candidate'},
                    {'text': 'Я работодатель', 'callback_data': 'role:employer'}]]}}
        return {'text': text, 'reply_markup': {'inline_keyboard': [[button],
            [{'text': 'Выбрать другую роль', 'callback_data': 'role:menu'}]]}}

    def _application_card(self, db, cid, chat, application, config, tick):
        vacancy = application['vacancy']
        workflow = employer_workflow.load(db, application['id'], actor='candidate', candidate_id=cid)
        description = vacancy.get('description', '')
        text = (vacancy.get('title', 'Вакансия') + '\n' + vacancy.get('company', '') + '\n' +
                vacancy.get('city', '') + (' · удалённо' if vacancy.get('remote') else '') + '\n\n' +
                APPLICATION_LABELS.get(application['status'], application['status']))
        if application['approvedAutomatically']:
            text += '\nАвтосогласование включено вами. Время встречи подтверждается отдельно.'
        if workflow['closedBy']:
            text += '\nПодбор по вакансии завершён.'
        elif workflow['decision'] == 'declined':
            text += '\nРаботодатель завершил рассмотрение.'
        elif workflow['decision'] == 'pool':
            text += '\nРаботодатель включил вас в финальный пул.'
        offer = workflow.get('offer')
        if offer:
            text += '\nПредложение о работе: ' + {'sent': 'получено — ответьте в кабинете',
                'accepted': 'принято', 'declined': 'отклонено', 'withdrawn': 'отозвано'}.get(offer['status'], offer['status'])
        if description:
            text += '\n\n' + description[:1800] + ('…' if len(description) > 1800 else '')
        rows = []
        if application['status'] == 'pending' and not workflow['closedBy'] and workflow['decision'] != 'declined':
            snapshot = {'status': 'pending', 'vacancyDigest': _digest(_dump(vacancy))}
            rows.append([self._button(db, cid, chat, 'application.accept', 'Согласиться', tick, application, snapshot=snapshot),
                         self._button(db, cid, chat, 'application.decline', 'Отказаться', tick, application, snapshot=snapshot)])
            text += '\n\nСогласие относится к этой вакансии. Время встречи подтвердите отдельно.'
        rows.extend(self._menu(config)['inline_keyboard'])
        return {'text': text, 'reply_markup': {'inline_keyboard': rows}}

    def _meeting_card(self, db, cid, chat, application, config, tick):
        meeting, vacancy = application['meeting'], application['vacancy']
        text = (vacancy.get('title', 'Вакансия') + ' · ' + vacancy.get('company', '') + '\n' +
                _time(meeting['start']) + ' — ' + _time(meeting['end']) + '\n' +
                MEETING_LABELS.get(meeting['status'], meeting['status']))
        rows = []
        if meeting['status'] == 'pending':
            rows.append([self._button(db, cid, chat, 'meeting.confirm', 'Подтвердить это время', tick, application, meeting),
                         self._button(db, cid, chat, 'meeting.decline', 'Время не подходит', tick, application, meeting)])
            text += '\n\nНажатием вы подтверждаете именно указанные дату и время.'
        rows.extend(self._menu(config)['inline_keyboard'])
        return {'text': text, 'reply_markup': {'inline_keyboard': rows}}

    def _send(self, db, chat, cid, key, content, config, tick):
        return self.notifications.enqueue_telegram_reply(db, chat, key, content['text'],
            reply_markup=content.get('reply_markup'), candidate_id=cid, settings=config, now=tick)

    def _vacancies(self, db, cid, chat, config, tick, key, offset=0):
        total = db.execute('SELECT COUNT(*) FROM cp_applications WHERE candidate_id=?', (cid,)).fetchone()[0]
        rows = db.execute('''SELECT id FROM cp_applications WHERE candidate_id=?
            ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?''', (cid, PAGE_SIZE, offset)).fetchall()
        if not rows:
            self._send(db, chat, cid, key + ':empty', {'text': 'Предложений вакансий пока нет. Когда команда предложит вам вакансию, она появится здесь и в кабинете.', 'reply_markup': self._menu(config)}, config, tick)
        for index, row in enumerate(rows):
            app = self.portal.application(db, row[0], cid)
            self._send(db, chat, cid, key + ':' + str(index), self._application_card(db, cid, chat, app, config, tick), config, tick)
        if offset + len(rows) < total:
            button = self._button(db, cid, chat, 'vacancies.page', 'Следующие вакансии', tick, snapshot={'offset': offset + len(rows)})
            self._send(db, chat, cid, key + ':next', {'text': f'Показано {offset + len(rows)} из {total}.', 'reply_markup': {'inline_keyboard': [[button]]}}, config, tick)

    def _meetings(self, db, cid, chat, config, tick, key):
        rows = db.execute('''SELECT DISTINCT a.id FROM cp_applications a JOIN cp_meetings m
            ON m.application_id=a.id WHERE a.candidate_id=? AND m.status IN ('pending','confirmed','live')
            ORDER BY m.start LIMIT 20''', (cid,)).fetchall()
        if not rows:
            self._send(db, chat, cid, key + ':empty', {'text': 'Предстоящих встреч пока нет. Согласие на вакансию не подтверждает время встречи.', 'reply_markup': self._menu(config)}, config, tick)
        for index, row in enumerate(rows):
            app = self.portal.application(db, row[0], cid)
            self._send(db, chat, cid, key + ':' + str(index), self._meeting_card(db, cid, chat, app, config, tick), config, tick)

    def _status(self, db, cid, config):
        state = self.portal.candidate_state(db, cid)
        step = state['onboarding']['step']
        onboarding = {'resume': 'Загрузите обязательное резюме в кабинете.',
                      'preferences': 'Проверьте резюме, укажите зарплату и дату выхода в кабинете.',
                      'complete': 'Профиль заполнен.'}.get(step, 'Проверьте профиль в кабинете.')
        interview = state['interview']
        interview_text = ('Первичное интервью завершено.' if interview and interview['status'] == 'completed'
                          else 'Первичное интервью: ' + _time(interview['start']) if interview else 'Первичное интервью ещё не назначено.')
        return {'text': onboarding + '\n' + interview_text + '\nПредложений вакансий: ' + str(len(state['applications'])) +
                '\nАвтосогласование вакансий: ' + ('включено' if state['autoApprove'] else 'выключено') +
                '\n\n/vacancies — вакансии и решения\n/meetings — время встреч\n/settings — автосогласование\n/status — состояние профиля',
                'reply_markup': self._menu(config)}

    def _settings(self, db, cid, chat, config, tick):
        person = self.portal.person(db, cid)
        enabled = person['autoApprove']
        action = 'settings.auto_off' if enabled else 'settings.auto_on'
        title = 'Выключить автосогласование' if enabled else 'Включить автосогласование'
        button = self._button(db, cid, chat, action, title, tick, snapshot={'autoApprove': enabled})
        return {'text': 'Автосогласование вакансий сейчас ' + ('включено.' if enabled else 'выключено.') +
                '\nПри включении будущие предложения могут согласовываться по вашим условиям профиля. Уже полученные предложения не меняются. Каждое время встречи всегда подтверждается отдельно.',
                'reply_markup': {'inline_keyboard': [[button], *self._menu(config)['inline_keyboard']]}}

    def _callback(self, db, update, cid, chat, config, tick, key):
        query = update['callback_query']
        match = CALLBACK_RE.fullmatch(query.get('data', '')) if isinstance(query.get('data'), str) else None
        row = db.execute('SELECT * FROM cb_actions WHERE digest=?', (_digest(match[1]),)).fetchone() if match else None
        columns = ['digest', 'candidate_id', 'chat_id', 'action', 'application_id', 'meeting_id', 'revision', 'start', 'end', 'snapshot', 'created_at', 'expires_at', 'used_at']
        action = dict(zip(columns, row)) if row else None
        status, answer = 'invalid_callback', 'Кнопка устарела. Откройте /vacancies или /meetings.'
        valid = action and cid and action['candidate_id'] == cid and action['chat_id'] == chat and action['expires_at'] > tick and action['used_at'] is None
        if valid:
            snapshot = json.loads(action['snapshot'])
            try:
                # Domain methods remain authoritative and share this transaction.
                db.execute('SAVEPOINT candidate_bot_action')
                name = action['action']
                if name.startswith('application.'):
                    app = self.portal.application(db, action['application_id'], cid)
                    require(app['status'] == snapshot['status'] and _digest(_dump(app['vacancy'])) == snapshot['vacancyDigest'], 'Предложение изменилось. Откройте /vacancies.', 409)
                    workflow = employer_workflow.load(db, app['id'], actor='candidate', candidate_id=cid)
                    require(not workflow['closedBy'] and workflow['decision'] != 'declined', 'Рассмотрение завершено. Откройте /vacancies.', 409)
                    self.portal.decide_application(db, cid, {'applicationId': app['id'], 'decision': 'accepted' if name == 'application.accept' else 'declined'})
                    answer = 'Согласие сохранено. Время встречи подтверждается отдельно.' if name == 'application.accept' else 'Отказ от вакансии сохранён.'
                elif name.startswith('meeting.'):
                    app = self.portal.application(db, action['application_id'], cid)
                    meeting = app['meeting']
                    require(meeting and all(meeting[k] == action[v] for k, v in [('id', 'meeting_id'), ('revision', 'revision'), ('start', 'start'), ('end', 'end')]) and meeting['status'] == 'pending', 'Время встречи изменилось. Откройте /meetings.', 409)
                    body = {'applicationId': app['id'], 'meetingId': meeting['id'], 'revision': meeting['revision']}
                    (self.portal.confirm_meeting if name == 'meeting.confirm' else self.portal.decline_meeting)(db, cid, body)
                    answer = ('Встреча подтверждена: ' if name == 'meeting.confirm' else 'Время отклонено: ') + _time(meeting['start'])
                elif name.startswith('settings.'):
                    person = self.portal.person(db, cid)
                    require(person['autoApprove'] == snapshot['autoApprove'], 'Настройка изменилась. Откройте /settings.', 409)
                    self.portal.profile(db, cid, {'autoApprove': name == 'settings.auto_on'})
                    answer = 'Автосогласование включено. Время встречи подтверждаете отдельно.' if name == 'settings.auto_on' else 'Автосогласование выключено.'
                elif name == 'vacancies.page':
                    require(type(snapshot.get('offset')) is int and snapshot['offset'] >= 0, 'Откройте /vacancies.', 400)
                    self._vacancies(db, cid, chat, config, tick, key + ':page', snapshot['offset'])
                    answer = 'Следующие предложения'
                else:
                    raise Problem('Кнопка недоступна.', 400)
                db.execute('UPDATE cb_actions SET used_at=? WHERE digest=? AND used_at IS NULL', (tick, action['digest']))
                db.execute('RELEASE candidate_bot_action')
                status = 'updated'
            except Problem as error:
                db.execute('ROLLBACK TO candidate_bot_action')
                db.execute('RELEASE candidate_bot_action')
                answer, status = error.message[:200], 'stale_callback'
        # A foreign click never consumes another person's token or exposes its data.
        self.notifications.enqueue_telegram_callback(db, chat, key + ':answer', query['id'], answer[:200],
            show_alert=status != 'updated', candidate_id=cid, settings=config, now=tick)
        if valid and status == 'updated' and action['action'] != 'vacancies.page':
            self._send(db, chat, cid, key + ':receipt', {'text': answer, 'reply_markup': self._menu(config)}, config, tick)
        return {'status': status}

    def handle_update(self, db, update, timestamp, config, binding_result=None):
        # Dispatcher validates webhook secret, update id and private sender first.
        query = update.get('callback_query')
        message = query['message'] if isinstance(query, dict) else update['message']
        chat = str(message['chat']['id'])
        cid = self._candidate(db, chat)
        key = 'bot:' + str(update['update_id'])
        db.execute('DELETE FROM cb_actions WHERE expires_at<?', (timestamp - ACTION_SECONDS,))
        if isinstance(query, dict):
            if query.get('data') in ('role:candidate', 'role:employer', 'role:menu'):
                # Public navigation only: it never selects an account, binds a
                # profile or grants application access. Private sender is checked
                # by the notification dispatcher, just like every other callback.
                role = query['data'].split(':', 1)[1]
                self.notifications.enqueue_telegram_callback(db, chat, key + ':answer', query['id'],
                    candidate_id=cid, settings=config, now=timestamp)
                self._send(db, chat, cid, key + ':role', self._role_content(config, role), config, timestamp)
                return {'status': 'replied'}
            return self._callback(db, update, cid, chat, config, timestamp, key)
        if binding_result and binding_result['status'] != 'linked':
            messages = {'invalid_link': 'Ссылка привязки истекла или уже использована. Войдите в кабинет и нажмите «Подключить Telegram» снова.',
                        'chat_already_linked': 'Этот Telegram уже привязан к другому кабинету. Войдите в свой кабинет.'}
            self._send(db, chat, cid, key + ':binding', {'text': messages.get(binding_result['status'], 'Откройте кабинет и повторите привязку.'), 'reply_markup': self._menu(config)}, config, timestamp)
            return binding_result
        text = message.get('text', '')
        parts = text.split() if isinstance(text, str) else []
        command = parts[0].split('@')[0] if parts else ''
        if not cid or (command == '/start' and not binding_result):
            self._send(db, chat, cid, key + ':roles', self._role_content(config), config, timestamp)
            return {'status': 'replied'}
        if binding_result and binding_result['status'] == 'linked':
            self._send(db, chat, cid, key + ':linked', {'text': 'Telegram подключён к вашему кабинету HR Vision. Решения здесь сразу сохраняются в кабинете.\n\n/vacancies — вакансии\n/meetings — время встреч\n/status — состояние профиля\n/settings — автосогласование', 'reply_markup': self._menu(config)}, config, timestamp)
            self._vacancies(db, cid, chat, config, timestamp, key + ':vacancies')
        elif command in ('/vacancies', '/jobs'):
            self._vacancies(db, cid, chat, config, timestamp, key)
        elif command == '/meetings':
            self._meetings(db, cid, chat, config, timestamp, key)
        elif command == '/settings':
            self._send(db, chat, cid, key + ':settings', self._settings(db, cid, chat, config, timestamp), config, timestamp)
        else:
            self._send(db, chat, cid, key + ':status', self._status(db, cid, config), config, timestamp)
        return binding_result or {'status': 'replied'}

    def render_notification(self, db, row, config, timestamp):
        """Render live canonical state when an eligible lifecycle event is dispatched."""
        cid, chat, kind = row['candidate_id'], row['recipient'], row['event_type']
        if self._candidate(db, chat) != cid:
            return None
        if kind not in ('application.proposed', 'meeting.proposed', 'meeting.rescheduled'):
            return None
        entity = row['event_key'][len(kind) + 1:]
        try:
            if kind == 'application.proposed':
                app = self.portal.application(db, entity, cid)
                return self._application_card(db, cid, chat, app, config, timestamp)
            owned = db.execute('''SELECT a.id FROM cp_meetings m JOIN cp_applications a ON a.id=m.application_id
                WHERE m.id=? AND a.candidate_id=?''', (entity, cid)).fetchone()
            if owned:
                app = self.portal.application(db, owned[0], cid)
                if app['meeting'] and app['meeting']['id'] == entity:
                    return self._meeting_card(db, cid, chat, app, config, timestamp)
        except Problem:
            pass
        return None
