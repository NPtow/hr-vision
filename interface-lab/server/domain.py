"""Small, server-authoritative shared test. No browser can replace the state."""
import copy
import datetime as dt
import secrets
from feedback import questionnaire

IDS = ('anna', 'mikhail', 'elena')
NAMES = {'anna': 'Анна Миронова', 'mikhail': 'Михаил Белов', 'elena': 'Елена Орлова', 'manager': 'Иван Петров'}
UTC = dt.timezone.utc


class Problem(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def require(condition, message, status=409):
    if not condition:
        raise Problem(message, status)


def now():
    return dt.datetime.now(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat(timespec='seconds').replace('+00:00', 'Z')


def timestamp(value):
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        require(parsed.tzinfo is not None, 'Нужен часовой пояс.')
        return parsed.astimezone(UTC)
    except (ValueError, TypeError, AttributeError):
        raise Problem('Некорректное время.')


def text(value, limit=3000, required=True):
    require(isinstance(value, str), 'Ожидается текст.', 400)
    value = value.strip()
    require(len(value) <= limit and (bool(value) or not required), 'Проверьте длину и заполнение поля.', 400)
    return value


def fresh():
    # Ready accounts have suggested availability; the candidate may edit it.
    base = now().astimezone(dt.timezone(dt.timedelta(hours=3))).date()
    slots = [iso(dt.datetime.combine(base + dt.timedelta(days=d), dt.time(h), dt.timezone(dt.timedelta(hours=3))))
             for d in range(1, 5) for h in (11, 14, 16)]
    return {'generation': secrets.token_hex(12), 'revision': 0, 'closedBy': None,
            'vacancy': {'company': 'Сфера', 'role': 'Менеджер по работе с клиентами',
                        'problem': 'Вернуть клиентов к повторным покупкам', 'salary': '150–180 тыс. ₽ + бонус',
                        'format': 'Москва · гибрид', 'expectations': 'Разобраться в причинах ухода клиентов и восстановить повторные продажи.'},
            'candidates': {cid: {'id': cid, 'name': NAMES[cid], 'interest': 'pending', 'auto': False,
                                'slots': slots[:], 'busy': [slots[-1]], 'meeting': None,
                                'feedback': None, 'afterInterest': 'pending', 'decision': 'review',
                                'offer': None, 'messages': []} for cid in IDS}}


def overlap(a, b):
    return abs((timestamp(a) - timestamp(b)).total_seconds()) < 1800


def free_slots(state, cid):
    person = state['candidates'][cid]
    occupied = list(person['busy'])
    # The single hiring manager also cannot interview two people simultaneously.
    for other in state['candidates'].values():
        m = other['meeting']
        if m and m['status'] in ('pending', 'confirmed', 'live'):
            occupied.append(m['start'])
    return [s for s in sorted(person['slots']) if timestamp(s) > now() and not any(overlap(s, b) for b in occupied)]


def public_state(state, actor):
    result = copy.deepcopy(state)
    visible = {cid: c for cid, c in result['candidates'].items()
               if (actor == 'manager' and c['interest'] == 'accepted') or actor == cid}
    result['candidates'] = visible
    result['actor'] = actor
    result['actorName'] = NAMES[actor]
    for cid, person in visible.items():
        person['freeSlots'] = free_slots(state, cid)
        if actor == 'manager':
            person.pop('slots', None)
            person.pop('busy', None)
        else:
            person.pop('feedback', None)
        m = person['meeting']
        if m:
            m.pop('room', None)
            m.pop('transcriptTurns', None)
            if actor != 'manager':
                m.pop('questionnaire', None)
                m.pop('feedbackDraft', None)
        if actor != 'manager' and person['offer'] and person['offer']['status'] == 'draft':
            person['offer'] = None
    return result


def message(person, actor, body, system=False):
    require(len(person['messages']) < 500, 'Лимит сообщений теста достигнут. Начните заново.')
    person['messages'].append({'id': secrets.token_hex(10), 'actor': actor, 'text': body,
                               'at': iso(now()), 'system': system})


def transition(state, actor, data):
    require(data.get('generation') == state['generation'], 'Тест уже перезапущен. Обновите страницу.')
    action, cid = data.get('action'), data.get('candidate')
    if action == 'reset':
        require(actor == 'manager', 'Начать тест заново может работодатель.', 403)
        require(not any(c['meeting'] and c['meeting']['status'] == 'live' for c in state['candidates'].values()),
                'Сначала завершите активный звонок.')
        return fresh()
    require(cid in IDS, 'Кандидат не найден.', 404)
    person = state['candidates'][cid]
    is_manager = actor == 'manager'
    require(is_manager or actor == cid, 'Этот аккаунт не имеет доступа.', 403)
    require(not is_manager or person['interest'] == 'accepted', 'Кандидат ещё не принял вакансию.', 403)
    m = person['meeting']
    if action == 'chat':
        require(person['interest'] == 'accepted', 'Сначала примите вакансию.')
        message(person, actor, text(data.get('text'), 2000))
    elif action in ('interest', 'auto'):
        require(not is_manager, 'Это решение кандидата.', 403)
        require(not state['closedBy'], 'Подбор уже завершён.')
        if action == 'auto':
            require(isinstance(data.get('enabled'), bool), 'Нужно выбрать режим.', 400)
            person['auto'] = data['enabled']
            if person['auto']:
                person['interest'] = 'accepted'
        else:
            require(data.get('value') in ('accepted', 'declined'), 'Выберите ответ.', 400)
            require(not m or m['status'] not in ('confirmed', 'live'), 'Сначала перенесите или отмените встречу.')
            require(not person['offer'] or person['offer']['status'] not in ('sent', 'accepted'), 'Сначала ответьте на оффер.')
            person['interest'] = data['value']
            if data['value'] == 'declined':
                person['auto'] = False
                if m and m['status'] == 'pending':
                    m['status'] = 'cancelled'
    elif action in ('slot.add', 'slot.remove'):
        require(not is_manager, 'Время указывает кандидат.', 403)
        start = iso(timestamp(data.get('start')))
        if action == 'slot.add':
            require(now() < timestamp(start) < now() + dt.timedelta(days=90), 'Выберите время в ближайшие 90 дней.')
            require(len(person['slots']) < 100, 'Достаточно 100 слотов для теста.')
            require(not any(overlap(start, s) for s in person['slots']), 'Этот интервал уже есть.')
            require(not any(overlap(start, s) for s in person['busy']), 'Время занято другой встречей.')
            person['slots'].append(start)
        else:
            require(not m or m['status'] not in ('pending', 'confirmed', 'live') or not overlap(m['start'], start),
                    'Сначала отмените или перенесите назначенную встречу.')
            person['slots'] = [s for s in person['slots'] if s != start]
    elif action == 'meeting.propose':
        require(is_manager, 'Время выбирает работодатель.', 403)
        require(not state['closedBy'], 'Подбор уже завершён.')
        require(not m or (m['status'] in ('pending', 'confirmed', 'cancelled') and not m.get('room')), 'Встреча уже началась или завершена.')
        start = iso(timestamp(data.get('start')))
        require(start in free_slots(state, cid), 'Этот слот уже занят. Выберите другое время.')
        person['meeting'] = {'id': secrets.token_hex(12), 'start': start, 'status': 'pending',
                             'joined': [], 'recording': 'not_started', 'room': None, 'testSkip': False}
        message(person, 'system', 'Работодатель предложил время встречи. Подтвердите его.', True)
    elif action in ('meeting.confirm', 'meeting.cancel'):
        require(m and m['id'] == data.get('meetingId'), 'Время встречи изменилось. Обновите страницу.')
        require(m['status'] in ('pending', 'confirmed') and not m.get('room'), 'Встреча уже началась или завершена.')
        if action == 'meeting.confirm':
            require(not is_manager, 'Встречу подтверждает кандидат.', 403)
            require(m['status'] == 'pending' and timestamp(m['start']) > now(), 'Согласуйте новое время.')
            m['status'] = 'confirmed'
            message(person, 'system', 'Кандидат подтвердил встречу. Можно открыть комнату.', True)
        else:
            m['status'] = 'cancelled'
            message(person, 'system', 'Встреча отменена. Можно выбрать новое время.', True)
    elif action == 'meeting.joined':
        require(m and m['id'] == data.get('meetingId') and m['status'] in ('confirmed', 'live') and m.get('room'), 'Комната недоступна.')
        if actor not in m['joined']:
            m['joined'].append(actor)
        m['status'] = 'live'
        m.setdefault('startedAt', iso(now()))
    elif action == 'meeting.transcript':
        require(is_manager and m and m['id'] == data.get('meetingId') and m['status'] == 'live', 'Встреча недоступна.', 403)
        incoming = data.get('turns')
        require(isinstance(incoming, list) and len(incoming) <= 30, 'Некорректная расшифровка.', 400)
        saved = m.setdefault('transcriptTurns', [])
        for turn in incoming:
            require(isinstance(turn, dict), 'Некорректная реплика.', 400)
            key = text(turn.get('id'), 120)
            words = text(turn.get('text'), 1500)
            seconds = turn.get('seconds')
            require(isinstance(seconds, (float, int)) and 0 <= seconds <= 14400, 'Некорректное время.', 400)
            if not any(t['id'] == key for t in saved):
                require(len(saved) < 1500, 'Достигнут лимит расшифровки теста.')
                saved.append({'id': key, 'text': words, 'seconds': seconds, 'source': 'live'})
    elif action in ('meeting.finish', 'meeting.skip'):
        require(is_manager, 'Встречу завершает работодатель.', 403)
        require(m and m['id'] == data.get('meetingId') and m['status'] in ('confirmed', 'live'), 'Встреча недоступна.')
        if action == 'meeting.skip':
            require(not m.get('room'), 'Нельзя пропустить уже открытую видеокомнату.')
            m['testSkip'] = True
        else:
            require(m['status'] == 'live', 'Сначала войдите в звонок.')
        m['status'] = 'completed'
        m['endedAt'] = iso(now())
        if m.get('transcriptTurns'):
            m['questionnaire'] = questionnaire(m['transcriptTurns'], m['id'])
        message(person, 'system', 'Встреча завершена. Отметьте, хотите ли вы продолжать.', True)
    elif action == 'feedback.questions':
        require(is_manager and m and m['id'] == data.get('meetingId') and m['status'] == 'completed', 'Сначала завершите встречу.')
        require(not person['feedback'] and not m.get('questionnaire'), 'Вопросы уже подготовлены.')
        words = text(data.get('questions'), 12000)
        turns = [{'text': line, 'seconds': 0} for line in words.splitlines() if line.strip()]
        require(0 < len(turns) <= 30, 'Укажите до 30 вопросов, каждый с новой строки.', 400)
        m['questionnaire'] = questionnaire(turns, m['id'], manual=True)
        require(m['questionnaire'], 'Запишите вопрос целиком.', 400)
    elif action in ('feedback', 'feedback.draft'):
        require(is_manager and m and m['id'] == data.get('meetingId') and m['status'] == 'completed', 'Сначала завершите встречу.')
        require(not person['feedback'], 'Обратная связь уже сохранена.')
        q = m.get('questionnaire')
        require(q and q['id'] == data.get('questionnaireId'), 'Вопросы изменились. Обновите страницу.')
        answers = data.get('answers')
        require(isinstance(answers, dict) and set(answers) <= {x['id'] for x in q['questions']}, 'Некорректные ответы.', 400)
        checked = {}
        for key, answer in answers.items():
            require(isinstance(answer, dict) and answer.get('rating') in ('clear', 'unclear', 'unanswered', 'not_asked'), 'Оцените ответ.', 400)
            checked[key] = {'rating': answer['rating'], 'comment': text(answer.get('comment', ''), 3000, required=False)}
        if action == 'feedback.draft':
            m['feedbackDraft'] = checked
        else:
            require(set(checked) == {x['id'] for x in q['questions']}, 'Отметьте ответ на каждый вопрос.', 400)
            person['feedback'] = {'meetingId': m['id'], 'questionnaireId': q['id'], 'questions': q['questions'], 'answers': checked, 'at': iso(now())}
            m.pop('feedbackDraft', None)
    elif action == 'after':
        require(not is_manager and m and m['status'] == 'completed', 'Ответ доступен после встречи.')
        require(data.get('value') in ('yes', 'no'), 'Выберите ответ.', 400)
        require(not person['offer'] or person['offer']['status'] not in ('sent', 'accepted'), 'Ответьте на полученный оффер.')
        person['afterInterest'] = data['value']
        message(person, 'system', 'Кандидат хочет продолжить.' if data['value'] == 'yes' else 'Кандидат не хочет продолжать.', True)
    elif action == 'decision':
        require(is_manager and person['feedback'], 'Сначала сохраните фидбек.', 403)
        require(not state['closedBy'] and (not person['offer'] or person['offer']['status'] == 'draft'), 'По кандидату уже отправлен оффер.')
        require(data.get('value') in ('pool', 'declined'), 'Выберите решение.', 400)
        person['decision'] = data['value']
        if data['value'] == 'declined':
            message(person, 'system', 'Компания решила не продолжать подбор с вами по этой вакансии.', True)
    elif action == 'offer.save':
        require(is_manager and person['decision'] == 'pool' and person['feedback'], 'Сначала добавьте кандидата в пул.', 403)
        require(not state['closedBy'], 'Подбор завершён.')
        require(not person['offer'] or person['offer']['status'] in ('draft', 'sent'), 'На оффер уже получен ответ.')
        offer = {key: text(data.get(key), 1500) for key in ('role', 'compensation', 'format', 'expectations')}
        date = text(data.get('startDate'), 10)
        try:
            parsed = dt.date.fromisoformat(date)
        except ValueError:
            raise Problem('Проверьте дату выхода.')
        require(parsed >= now().astimezone(dt.timezone(dt.timedelta(hours=3))).date(), 'Дата выхода уже прошла.')
        previous = person['offer'] or {}
        offer.update(startDate=date, status=previous.get('status', 'draft'), version=previous.get('version', 0) + 1)
        person['offer'] = offer
        if offer['status'] == 'sent':
            message(person, 'system', 'Работодатель обновил условия оффера. Проверьте новую версию.', True)
    elif action == 'offer.send':
        require(is_manager and person['offer'] and person['offer']['status'] == 'draft', 'Сначала подготовьте оффер.', 403)
        require(person['afterInterest'] != 'no', 'Кандидат не хочет продолжать. Выберите другого.')
        require(not state['closedBy'] and not any(c['offer'] and c['offer']['status'] in ('sent', 'accepted') for c in state['candidates'].values()), 'Сначала дождитесь ответа на текущий оффер.')
        person['offer']['status'] = 'sent'
        message(person, 'system', 'Вы получили оффер. Можно принять, отклонить или обсудить условия.', True)
    elif action == 'offer.reply':
        require(not is_manager and person['offer'] and person['offer']['status'] == 'sent', 'Нет оффера для ответа.', 403)
        require(person['offer']['version'] == data.get('version'), 'Условия изменились. Прочитайте новую версию.')
        require(data.get('value') in ('accepted', 'declined'), 'Выберите ответ.', 400)
        person['offer']['status'] = data['value']
        if data['value'] == 'accepted':
            state['closedBy'] = cid
        message(person, 'system', 'Оффер принят. Подбор завершён.' if data['value'] == 'accepted' else 'Кандидат отклонил оффер.', True)
    else:
        raise Problem('Действие не найдено.', 404)
    state['revision'] += 1
    return state
