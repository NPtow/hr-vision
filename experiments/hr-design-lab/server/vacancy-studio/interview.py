"""A bounded interview over catalog skills and hiring conditions.

The model finds evidence and proposes skills. It cannot invent the next question.
Questions, answer effects and completion are owned by this module.
"""
import copy
from task_scenarios import TEMPLATES

VERSION = '2026-10-26.targeted.1'
CONDITIONS = {
    'experience': ('Опыт работы', 'Какой опыт работы нужен будущему сотруднику до выхода к вам: какие задачи он уже должен был выполнять и есть ли минимальный срок?', 'Коммерческий опыт не обязателен — готовы обучать.'),
    'industry_experience': ('Опыт в отрасли', 'Нужен ли будущему сотруднику опыт именно в вашей отрасли? Если да, какой опыт вы считаете необходимым?', 'Опыт именно в нашей отрасли не обязателен.'),
    'location': ('Локация', 'Где должен находиться будущий сотрудник? Укажите допустимые города или страны либо отсутствие ограничений.', 'Ограничений по месту проживания нет.'),
    'work_mode': ('Формат работы', 'Какой формат работы нужен для этой вакансии: офис, гибрид или удалённая работа?', None),
    'employment': ('Занятость', 'Какая занятость нужна будущему сотруднику: полная или частичная?', None),
}


def identity(proposal):
    return proposal.get('key', proposal.get('parameter_id'))


def interview(state):
    return state.setdefault('interview', {'version': VERSION, 'topics': {}, 'decisions': {}})


def active_fact(state, key):
    return next((p for p in reversed(state['proposals']) if p['kind'] == 'fact' and p['status'] == 'accepted' and p['key'] == key), None)


def closed_skills(state):
    # Pending cards already offer an actionable decision; don't interview over them again.
    return {p['parameter_id'] for p in state['proposals'] if p['kind'] == 'skill' and p['status'] in ('accepted', 'pending', 'dismissed')}


def question_key(target):
    return '.'.join((target['kind'], target['key'], target['decision']))


def asked(state):
    return {question_key(m['question_target']) for m in state['messages'] if m.get('question_target')}


def decision_known(state, key):
    fact = active_fact(state, key)
    return bool(fact and (fact.get('manual') or not fact.get('automatic') or
                         state.get('interview', {}).get('decisions', {}).get(key, {}).get('status') in ('required', 'preferred')))


def eligible(state, target):
    if target['kind'] == 'skill':
        return target['key'] not in closed_skills(state)
    if target['kind'] == 'condition':
        fact = active_fact(state, target['key'])
        if any(p['kind'] == 'fact' and p['key'] == target['key'] and p['status'] == 'dismissed' for p in state['proposals']):
            return False
        return not fact if target['decision'] == 'value' else bool(fact and not decision_known(state, target['key']))
    return not state.get('interview', {}).get('topics') and not closed_skills(state)


def next_question(state, parameters):
    seen = asked(state)

    def make(kind, key, decision, label, question, options=(), effects=()):
        target = {'kind': kind, 'key': key, 'decision': decision, 'label': label}
        if question_key(target) in seen or not eligible(state, target):
            return None
        return {'question': question, 'options': list(options), 'question_target': target, 'option_effects': list(effects)}

    # Only skills whose relevance is supported by the employer's actual tasks.
    for pid in state.get('interview', {}).get('topics', {}):
        question = make('skill', pid, 'expectation', parameters[pid]['title'],
                        f"Что будущий сотрудник должен уметь делать в области «{parameters[pid]['title']}»? Выберите ближайшую задачу или опишите свою.",
                        TEMPLATES['parameters'][pid],
                        [{'kind': 'skill', 'parameter_id': pid, 'level': n} for n in range(2, 6)])
        if question:
            return question

    if not state.get('interview', {}).get('topics') and not closed_skills(state):
        question = make('context', 'work_tasks', 'scope', 'Задачи будущего сотрудника',
                        'Какие задачи в продажах будущий сотрудник должен выполнять самостоятельно? По ним предложим нужные навыки.')
        if question:
            return question

    for key, (label, question, exemption) in CONDITIONS.items():
        fact = active_fact(state, key)
        if fact:
            text = f"{label}: «{fact['value']}». Кандидата без этого условия исключаем или готовы рассмотреть?"
            answers = [f"Обязательно: {fact['value']}. Без этого кандидата не рассматриваем.",
                       f"Желательно: {fact['value']}, но готовы рассмотреть кандидата без этого условия."]
            value = make('condition', key, 'requiredness', label, text, answers,
                         [{'kind': 'condition_decision', 'key': key, 'status': status, 'proposal_id': fact['id']}
                          for status in ('required', 'preferred')])
        else:
            value = make('condition', key, 'value', label, question,
                         [exemption] if exemption else [],
                         [{'kind': 'condition_value', 'key': key, 'value': exemption, 'status': 'preferred'}] if exemption else [])
        if value:
            return value
    return None


def current_question(state):
    if not state['messages']:
        return None
    message = state['messages'][-1]
    if message['role'] != 'assistant' or not message.get('question'):
        return None
    target = message.get('question_target')
    return message if not target or eligible(state, target) else None


def append_reply(state, job_id, content, ids, parameters):
    message = {'id': 'reply-' + job_id, 'role': 'assistant', 'content': content, 'proposalIds': ids}
    question = next_question(state, parameters)
    if question:
        message.update(question)
    else:
        message.update(question='', options=[], interview_complete=True)
        message['content'] += ' Вопросы по выявленным навыкам и условиям пройдены. Проверьте предложения; неуточнённые требования остаются неизвестными.'
    state['messages'].append(message)


def record_planning(state, planning, user_message):
    progress = interview(state)
    for topic in planning['skill_topics']:
        progress['topics'][topic['parameter_id']] = copy.deepcopy(topic)
    # A free-text answer can confirm requiredness only for the condition we asked
    # about, with direct evidence in THIS answer. General mentions remain suggestions.
    target = user_message.get('in_reply_to', {}).get('question_target', {})
    for decision in planning['conditions']:
        fact = active_fact(state, decision['key'])
        if not fact or fact.get('manual') or not fact.get('automatic'):
            continue
        if target.get('kind') != 'condition' or target.get('key') != decision['key'] or decision['source_id'] != user_message['id']:
            continue
        if decision['status'] == 'unknown':
            continue
        fact['hard_filter'] = decision['status'] == 'required'
        fact['requirement_status'] = decision['status']
        fact['requirement_evidence'] = copy.deepcopy(decision)
        progress['decisions'][decision['key']] = copy.deepcopy(decision)


def apply_choice(state, message, effect, question, make_id):
    progress = interview(state)
    evidence = {'source_id': message['id'], 'quote': message['content'], 'reason': 'Прямой ответ работодателя на вопрос о требовании.'}
    if effect['kind'] == 'defer':
        return [], 'Оставили этот пункт неуточнённым. Его можно описать позже своими словами.'
    if effect['kind'] == 'skill':
        item = {**evidence, 'parameter_id': effect['parameter_id'], 'level': effect['level'],
                'importance': 2, 'confidence': 'high', 'importance_confirmed': False,
                'origin': 'employer_task_choice', 'question_id': question['id']}
        proposal = {**item, 'id': make_id(), 'kind': 'skill', 'status': 'pending', 'manual': True, 'original': copy.deepcopy(item)}
        state['proposals'].append(proposal)
        return [proposal['id']], 'Предложили навык по выбранной задаче. В карточке можно уточнить задачу и её важность, затем добавить в профиль.'
    key, status = effect['key'], effect['status']
    if effect['kind'] == 'condition_value':
        item = {**evidence, 'key': key, 'value': effect['value'], 'hard_filter': False}
        fact = {**item, 'id': make_id(), 'kind': 'fact', 'status': 'accepted', 'automatic': True, 'manual': False, 'original': copy.deepcopy(item)}
        state['proposals'].append(fact)
    else:
        fact = active_fact(state, key)
        if not fact or fact['id'] != effect['proposal_id']:
            raise ValueError('Stale question')
        fact.update(hard_filter=status == 'required', manual=True)
    decision = {**evidence, 'key': key, 'status': status, 'question_id': question['id']}
    fact.update(requirement_status=status, requirement_evidence=decision)
    progress['decisions'][key] = decision
    return [fact['id']], 'Сохранили обязательное условие.' if status == 'required' else 'Сохранили: отсутствие этого условия не исключает кандидата.'


def answer_context(state, reply_id=None, option_index=None, defer=False):
    question = current_question(state)
    if reply_id and (not question or question['id'] != reply_id):
        raise ValueError('Stale question')
    snapshot = {k: copy.deepcopy(question[k]) for k in ('id', 'question', 'question_target') if question and k in question}
    if option_index is None and not defer:
        return snapshot, None, None
    if not reply_id or not question or not question.get('question_target'):
        raise ValueError('Stale question')
    if defer:
        return snapshot, {'kind': 'defer'}, 'Пока не могу уточнить это требование.'
    if type(option_index) != int or not 0 <= option_index < len(question.get('option_effects', [])):
        raise ValueError('Invalid answer option')
    return snapshot, copy.deepcopy(question['option_effects'][option_index]), question['options'][option_index]


def public_interview(state):
    unresolved = []
    for message in state['messages']:
        target = message.get('question_target')
        if target and eligible(state, target) and question_key(target) not in {question_key(t) for t in unresolved}:
            unresolved.append(target)
    return {'version': VERSION, 'unresolved': unresolved}


def upgrade(state, parameters):
    """Keep the old conversation, but retire its unbound open question once.

    No extraction, provider requests or changes to employer decisions on read.
    Running turns are left alone until their normal completion.
    """
    if state.get('interview_version') == VERSION or (state.get('pending') and state['pending']['status'] == 'running'):
        return False
    last=state['messages'][-1] if state['messages'] else None
    if last and last['role']=='assistant' and last.get('question') and not last.get('question_target'):
        append_reply(state, 'targeted-upgrade', 'Продолжим с требованиями к будущему сотруднику.', [], parameters)
    state['interview_version']=VERSION
    state['revision']+=1
    return True
