"""Prepared interview scripts and recruiter notes. No model or messaging calls.

Drafts are supplied by a human-operated agent. Approval is a separate recruiter
command; an interview pins its approved revision when the recruiter joins.
All public entry points are called after interview ownership is checked.
"""
import json
import re
from urllib.parse import urlsplit

from domain import require, iso, now, text

PATHS = {'/api/hr/recruiter/interview/coach/' + action for action in
         ('state', 'context', 'pin', 'draft', 'approve', 'progress', 'backup')}

def init(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS cp_interview_scripts (
        interview_id TEXT NOT NULL REFERENCES cp_interviews(id),
        revision INTEGER NOT NULL, body TEXT NOT NULL, created_by TEXT NOT NULL,
        created_at TEXT NOT NULL, approved_by TEXT, approved_at TEXT,
        PRIMARY KEY(interview_id, revision));
      CREATE TABLE IF NOT EXISTS cp_interview_coach (
        interview_id TEXT PRIMARY KEY REFERENCES cp_interviews(id),
        script_revision INTEGER, revision INTEGER NOT NULL DEFAULT 0,
        progress TEXT NOT NULL DEFAULT '{}', backup_url TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL);
    ''')


def interview(db, cid, iid):
    require(isinstance(cid, str) and isinstance(iid, str), 'Укажите интервью.', 400)
    row = db.execute('SELECT id,candidate_id,status FROM cp_interviews WHERE id=? AND candidate_id=?',
                     (iid, cid)).fetchone()
    require(row, 'Интервью изменилось. Обновите кабинет.', 404)
    return {'id': row[0], 'candidateId': row[1], 'status': row[2]}


def script(row):
    if not row:
        return None
    return {**json.loads(row[1]), 'revision': row[0], 'createdAt': row[2],
            'approvedAt': row[3], 'approvedBy': row[4]}


def load(db, cid, iid):
    target = interview(db, cid, iid)
    latest = script(db.execute('''SELECT revision,body,created_at,approved_at,approved_by
        FROM cp_interview_scripts WHERE interview_id=? ORDER BY revision DESC LIMIT 1''', (iid,)).fetchone())
    approved = script(db.execute('''SELECT revision,body,created_at,approved_at,approved_by
        FROM cp_interview_scripts WHERE interview_id=? AND approved_at IS NOT NULL
        ORDER BY revision DESC LIMIT 1''', (iid,)).fetchone())
    state = db.execute('SELECT script_revision,revision,progress,backup_url FROM cp_interview_coach WHERE interview_id=?',
                       (iid,)).fetchone()
    used = state[0] if state else None
    active = script(db.execute('''SELECT revision,body,created_at,approved_at,approved_by
        FROM cp_interview_scripts WHERE interview_id=? AND revision=?''', (iid, used)).fetchone()) if used else approved
    return {'interviewId': iid, 'candidateId': cid, 'interviewStatus': target['status'],
            'latest': latest, 'approved': approved, 'active': active, 'pinnedRevision': used,
            'revision': state[1] if state else 0,
            'progress': json.loads(state[2]) if state else {},
            'backupUrl': state[3] if state else ''}


def _state_row(db, iid):
    db.execute('INSERT OR IGNORE INTO cp_interview_coach(interview_id,updated_at) VALUES (?,?)', (iid, iso(now())))


def _revision(value):
    require(type(value) is int and value >= 0, 'Укажите версию данных.', 400)
    return value


def _questions(value):
    require(isinstance(value, list) and 1 <= len(value) <= 40, 'В скрипте должно быть от 1 до 40 вопросов.', 400)
    result, ids = [], set()
    for index, item in enumerate(value):
        require(isinstance(item, dict) and set(item) <= {'id', 'topic', 'question', 'why', 'followups'},
                'Проверьте поля вопроса.', 400)
        qid = item.get('id') or f'q{index + 1}'
        require(isinstance(qid, str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', qid) and qid not in ids,
                'У вопросов должны быть разные идентификаторы.', 400)
        ids.add(qid)
        followups = item.get('followups', [])
        require(isinstance(followups, list) and len(followups) <= 8, 'Не больше 8 уточнений к вопросу.', 400)
        result.append({'id': qid, 'topic': text(item.get('topic', ''), 200, required=False),
                       'question': text(item.get('question'), 3000),
                       'why': text(item.get('why', ''), 2000, required=False),
                       'followups': [text(value, 1500) for value in followups]})
    return result


def save_draft(db, cid, iid, body, actor):
    state = load(db, cid, iid)
    require(state['interviewStatus'] != 'completed', 'Интервью завершено. Его скрипт сохранён в истории.', 409)
    require(_revision(body.get('expectedRevision')) == (state['latest']['revision'] if state['latest'] else 0),
            'Появилась новая версия скрипта. Обновите его перед сохранением.', 409)
    content = {'questions': _questions(body.get('questions')),
               'source': text(body.get('source', ''), 4000, required=False)}
    revision = (state['latest']['revision'] if state['latest'] else 0) + 1
    db.execute('INSERT INTO cp_interview_scripts VALUES (?,?,?,?,?,NULL,NULL)',
               (iid, revision, json.dumps(content, ensure_ascii=False), actor, iso(now())))
    return load(db, cid, iid)


def approve(db, cid, iid, body, actor):
    state = load(db, cid, iid)
    require(state['interviewStatus'] != 'completed' and not state['pinnedRevision'],
            'Во время интервью используется уже закреплённая версия скрипта.', 409)
    require(state['latest'] and _revision(body.get('revision')) == state['latest']['revision'],
            'Черновик изменился. Прочитайте новую версию перед согласованием.', 409)
    db.execute('UPDATE cp_interview_scripts SET approved_by=?,approved_at=? WHERE interview_id=? AND revision=? AND approved_at IS NULL',
               (actor, iso(now()), iid, state['latest']['revision']))
    return load(db, cid, iid)


def pin(db, cid, iid):
    state = load(db, cid, iid)
    require(state['interviewStatus'] == 'live', 'Сначала подключитесь к интервью.', 409)
    if state['approved'] and not state['pinnedRevision']:
        _state_row(db, iid)
        progress = state['progress']
        progress['currentId'] = state['approved']['questions'][0]['id']
        db.execute('UPDATE cp_interview_coach SET script_revision=?,progress=?,revision=revision+1,updated_at=? WHERE interview_id=?',
                   (state['approved']['revision'], json.dumps(progress, ensure_ascii=False), iso(now()), iid))
    return load(db, cid, iid)


def save_progress(db, cid, iid, body):
    state = load(db, cid, iid)
    require(_revision(body.get('expectedRevision')) == state['revision'],
            'Заметки изменились в другом окне. Скопируйте свой текст и обновите данные.', 409)
    active = state['active']
    require((type(body.get('scriptRevision')) is int if active else body.get('scriptRevision') is None) and
            body.get('scriptRevision') == (active['revision'] if active else None), 'Версия скрипта изменилась.', 409)
    require(state['interviewStatus'] in ('live', 'completed'), 'Заметки доступны после начала интервью.', 409)
    value = body.get('progress')
    require(isinstance(value, dict) and set(value) <= {'currentId', 'answers', 'general'}, 'Проверьте заметки.', 400)
    ids = {q['id'] for q in active['questions']} if active else set()
    current = value.get('currentId', active['questions'][0]['id'] if active else '')
    require(isinstance(current, str) and (current in ids if ids else current == ''),
            'Вопрос не найден в согласованном скрипте.', 400)
    answers = value.get('answers', {})
    require(isinstance(answers, dict) and set(answers) <= ids, 'Вопрос не найден в согласованном скрипте.', 400)
    cleaned = {}
    for qid, answer in answers.items():
        require(isinstance(answer, dict) and set(answer) <= {'status', 'note'} and
                answer.get('status', '') in ('', 'answered', 'skipped'), 'Проверьте ответ.', 400)
        cleaned[qid] = {'status': answer.get('status', ''), 'note': text(answer.get('note', ''), 12000, required=False)}
    progress = {'currentId': current, 'answers': cleaned, 'general': text(value.get('general', ''), 24000, required=False)}
    _state_row(db, iid)
    db.execute('''UPDATE cp_interview_coach SET progress=?,script_revision=?,revision=revision+1,updated_at=?
                  WHERE interview_id=?''',
               (json.dumps(progress, ensure_ascii=False), active['revision'] if active else None, iso(now()), iid))
    return load(db, cid, iid)


def save_backup(db, cid, iid, body):
    state = load(db, cid, iid)
    require(state['interviewStatus'] != 'completed', 'Встреча уже завершена.', 409)
    require(_revision(body.get('expectedRevision')) == state['revision'],
            'Данные изменились. Обновите их перед сохранением ссылки.', 409)
    url = text(body.get('url', ''), 2000, required=False)
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
    except ValueError:
        require(False, 'Проверьте резервную ссылку.', 400)
    require(not url or (parsed.scheme == 'https' and host and not parsed.username and
                       not parsed.password and not re.search(r'[\x00-\x20\x7f]', url)),
            'Укажите полную HTTPS-ссылку на резервную встречу.', 400)
    _state_row(db, iid)
    db.execute('UPDATE cp_interview_coach SET backup_url=?,revision=revision+1,updated_at=? WHERE interview_id=?',
               (url, iso(now()), iid))
    return load(db, cid, iid)


def context(db, cid, iid):
    state = load(db, cid, iid)
    profile = db.execute('SELECT profile FROM cp_candidates WHERE id=?', (cid,)).fetchone()
    resume = db.execute('SELECT filename,text_content FROM cp_resumes WHERE candidate_id=?', (cid,)).fetchone()
    return {'candidateId': cid, 'interviewId': iid, 'profile': json.loads(profile[0]),
            'resume': {'filename': resume[0], 'text': resume[1]} if resume else None,
            'expectedRevision': state['latest']['revision'] if state['latest'] else 0,
            'draftTemplate': {'candidateId': cid, 'interviewId': iid,
                              'expectedRevision': state['latest']['revision'] if state['latest'] else 0,
                              'source': 'Укажите версию промпта и использованные источники',
                              'questions': [{'id': 'q1', 'topic': '', 'question': '', 'why': '', 'followups': []}]},
            'instruction': 'Подготовить черновик по согласованному системному промпту и фактам резюме. '
                           'Не придумывать опыт и ответы кандидата. Сохранить questions и source; '
                           'вернуть JSON по draftTemplate. Согласование выполняет рекрутёр отдельно.'}


def handle(db, cid, iid, action, body, actor):
    common = {'candidateId', 'interviewId'}
    fields = {'state': set(), 'context': set(), 'pin': set(),
              'draft': {'expectedRevision', 'questions', 'source'}, 'approve': {'revision'},
              'progress': {'expectedRevision', 'scriptRevision', 'progress'},
              'backup': {'expectedRevision', 'url'}}
    require(action in fields, 'Не найдено.', 404)
    require(set(body) <= common | fields[action], 'Проверьте поля запроса.', 400)
    if action == 'state':
        return load(db, cid, iid)
    if action == 'context':
        return context(db, cid, iid)
    if action == 'pin':
        return pin(db, cid, iid)
    if action == 'draft':
        return save_draft(db, cid, iid, body, actor)
    if action == 'approve':
        return approve(db, cid, iid, body, actor)
    if action == 'backup':
        return save_backup(db, cid, iid, body)
    return save_progress(db, cid, iid, body)
