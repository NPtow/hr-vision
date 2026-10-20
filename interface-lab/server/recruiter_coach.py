"""Prepared interview scripts and recruiter notes. No model or messaging calls.

Drafts are supplied by a human-operated agent. Approval is a separate recruiter
command; an interview pins its approved revision when the recruiter joins.
All public entry points are called after interview ownership is checked.
"""
import base64
import json
import uuid
import re
from urllib.parse import urlsplit

from domain import require, iso, now, text

PATHS = {'/api/hr/recruiter/interview/coach/' + action for action in
         ('state', 'context', 'pin', 'draft', 'approve', 'progress', 'backup', 'history', 'restore', 'resume')}

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
      CREATE TABLE IF NOT EXISTS cp_interview_note_versions (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE,
        interview_id TEXT NOT NULL REFERENCES cp_interviews(id),
        script_revision INTEGER,
        body TEXT NOT NULL, reason TEXT NOT NULL, created_by TEXT NOT NULL,
        created_at TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS cp_interview_note_history
        ON cp_interview_note_versions(interview_id, sequence);
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


def save_progress(db, cid, iid, body, actor=""):
    state = load(db, cid, iid)
    require(_revision(body.get('expectedRevision')) == state['revision'],
            'Заметки изменились в другом окне. Скопируйте свой текст и обновите данные.', 409)
    active = state['active']
    require((type(body.get('scriptRevision')) is int if active else body.get('scriptRevision') is None) and
            body.get('scriptRevision') == (active['revision'] if active else None), 'Версия скрипта изменилась.', 409)
    require(state['interviewStatus'] in ('live', 'completed'), 'Заметки доступны после начала интервью.', 409)
    value = body.get('progress')
    require(isinstance(value, dict) and set(value) <= {'currentId', 'answers', 'general', 'noteDocument'}, 'Проверьте заметки.', 400)
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
    if 'noteDocument' in value:
        progress['noteDocument'] = note_document(value['noteDocument'])
    elif state['progress'].get('noteDocument') and progress['general'] == state['progress'].get('general', ''):
        # Older clients may update navigation without knowing about rich notes.
        progress['noteDocument'] = state['progress']['noteDocument']
    else:
        progress['noteDocument'] = plain_document(progress['general'])
    if _notes_changed(state['progress'], progress):
        _capture(db, iid, active['revision'] if active else None, state['progress'], actor, 'manual')
        _capture(db, iid, active['revision'] if active else None, progress, actor, 'manual')
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
              'backup': {'expectedRevision', 'url'},
              'history': {'beforeVersionId'}, 'resume': set(),
              'restore': {'expectedRevision', 'scriptRevision', 'versionId'}}
    require(action in fields, 'Не найдено.', 404)
    require(set(body) <= common | fields[action], 'Проверьте поля запроса.', 400)
    if action == 'state':
        return load(db, cid, iid)
    if action == 'context':
        return context(db, cid, iid)
    if action == 'history':
        return history(db, cid, iid, body.get('beforeVersionId'))
    if action == 'restore':
        return restore(db, cid, iid, body, actor)
    if action == 'resume':
        return original_resume(db, cid, iid)
    if action == 'pin':
        return pin(db, cid, iid)
    if action == 'draft':
        return save_draft(db, cid, iid, body, actor)
    if action == 'approve':
        return approve(db, cid, iid, body, actor)
    if action == 'backup':
        return save_backup(db, cid, iid, body)
    return save_progress(db, cid, iid, body, actor)


def plain_document(value):
    """Losslessly migrate the old plain note without manufacturing formatting."""
    return {'type': 'doc', 'content': [
        {'type': 'paragraph', **({'content': [{'type': 'text', 'text': line}]} if line else {})}
        for line in value.replace('\r\n', '\n').split('\n')]}


def note_document(value):
    """Only the editor's non-executable text schema, with bounded tree work."""
    count = 0
    chars = 0
    blocks = {'paragraph', 'heading', 'bulletList', 'orderedList', 'blockquote'}

    def visit(node, depth=0):
        nonlocal count, chars
        count += 1
        require(depth <= 16 and count <= 3000, 'Заметка слишком сложная. Разделите её на части.', 400)
        require(isinstance(node, dict) and set(node) <= {'type', 'content', 'text', 'attrs', 'marks'},
                'Недопустимый формат заметки.', 400)
        kind = node.get('type')
        require(kind in blocks | {'doc', 'listItem', 'text', 'hardBreak'}, 'Недопустимый блок заметки.', 400)
        attrs = node.get('attrs', {})
        require(isinstance(attrs, dict), 'Недопустимые свойства заметки.', 400)
        if kind == 'heading':
            require(set(attrs) == {'level'} and type(attrs['level']) is int and attrs['level'] in (2, 3),
                    'Допустимы заголовки второго и третьего уровня.', 400)
        elif kind == 'orderedList':
            require(set(attrs) <= {'start', 'type'} and type(attrs.get('start', 1)) is int and
                    1 <= attrs.get('start', 1) <= 1000000 and attrs.get('type') in (None, '1', 'a', 'A', 'i', 'I'),
                    'Недопустимая нумерация списка.', 400)
        else:
            require(not attrs, 'Недопустимые свойства блока.', 400)
        marks = node.get('marks', [])
        require(isinstance(marks, list) and len(marks) <= 2 and (kind in ('text', 'hardBreak') or not marks),
                'Недопустимое форматирование заметки.', 400)
        seen = set()
        for mark in marks:
            require(isinstance(mark, dict) and set(mark) == {'type'} and mark.get('type') in ('bold', 'italic') and
                    mark['type'] not in seen, 'Недопустимое форматирование заметки.', 400)
            seen.add(mark['type'])
        if kind == 'text':
            require(isinstance(node.get('text'), str) and len(node['text']) > 0 and 'content' not in node,
                    'Недопустимый текст заметки.', 400)
            chars += len(node['text'])
            require(chars <= 24000, 'Заметка длиннее 24 000 символов.', 400)
        else:
            require('text' not in node, 'Недопустимый текст блока.', 400)
        children = node.get('content', [])
        require(isinstance(children, list), 'Недопустимое содержимое заметки.', 400)
        if kind in ('text', 'hardBreak'):
            require(not children, 'Недопустимое содержимое строки.', 400)
        elif kind in ('paragraph', 'heading'):
            require(all(isinstance(child, dict) and child.get('type') in ('text', 'hardBreak') for child in children),
                    'Недопустимое содержимое абзаца.', 400)
        elif kind in ('doc', 'blockquote'):
            require(bool(children) and all(isinstance(child, dict) and child.get('type') in blocks for child in children),
                    'В заметке должен быть текстовый блок.', 400)
        elif kind in ('bulletList', 'orderedList'):
            require(bool(children) and all(isinstance(child, dict) and child.get('type') == 'listItem' for child in children),
                    'В списке должны быть пункты.', 400)
        elif kind == 'listItem':
            require(bool(children) and isinstance(children[0], dict) and children[0].get('type') == 'paragraph' and
                    all(isinstance(child, dict) and child.get('type') in blocks for child in children),
                    'Недопустимый пункт списка.', 400)
        for child in children:
            visit(child, depth + 1)

    require(isinstance(value, dict) and value.get('type') == 'doc', 'Недопустимый документ заметки.', 400)
    visit(value)
    encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    require(len(encoded.encode('utf-8')) <= 128000, 'Заметка слишком большая.', 400)
    return json.loads(encoded)


def _note_payload(progress):
    return {'noteDocument': progress.get('noteDocument') or plain_document(progress.get('general', '')),
            'general': progress.get('general', ''), 'answers': progress.get('answers', {})}


def _notes_key(progress):
    value = _note_payload(progress)
    value['answers'] = {qid: answer.get('note', '') for qid, answer in value['answers'].items() if answer.get('note', '')}
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _notes_changed(before, after):
    return _notes_key(before) != _notes_key(after)


def _capture(db, iid, script_revision, progress, actor, reason, *, force=False):
    latest = db.execute('SELECT body FROM cp_interview_note_versions WHERE interview_id=? ORDER BY sequence DESC LIMIT 1',
                        (iid,)).fetchone()
    if not force and latest and not _notes_changed(json.loads(latest[0]), progress):
        return
    db.execute('''INSERT INTO cp_interview_note_versions
        (id,interview_id,script_revision,body,reason,created_by,created_at) VALUES (?,?,?,?,?,?,?)''',
               (uuid.uuid4().hex, iid, script_revision, json.dumps(_note_payload(progress), ensure_ascii=False),
                reason, actor, iso(now())))


def history(db, cid, iid, before=None):
    interview(db, cid, iid)
    cursor = None
    if before is not None:
        require(isinstance(before, str) and len(before) <= 64, 'Проверьте версию заметки.', 400)
        cursor = db.execute('SELECT sequence FROM cp_interview_note_versions WHERE id=? AND interview_id=?',
                            (before, iid)).fetchone()
        require(cursor, 'Версия заметки не найдена.', 404)
    rows = db.execute('''SELECT v.id,v.body,v.reason,v.created_by,v.created_at,a.name
        FROM cp_interview_note_versions v LEFT JOIN rc_accounts a ON a.id=v.created_by
        WHERE v.interview_id=? AND (? IS NULL OR v.sequence<?) ORDER BY v.sequence DESC LIMIT 101''',
        (iid, cursor[0] if cursor else None, cursor[0] if cursor else None)).fetchall()
    versions = []
    for row in rows[:100]:
        body = json.loads(row[1])
        versions.append({'id': row[0], 'scope': iid, 'createdAt': row[4], 'reason': row[2],
                         'author': {'id': row[3], 'name': row[5] or ''},
                         'snapshot': {'document': body['noteDocument'],
                                      'questionNotes': {qid: answer.get('note', '') for qid, answer in body['answers'].items()
                                                        if answer.get('note', '')}}})
    return {'versions': versions, 'hasMore': len(rows) > 100,
            'nextCursor': versions[-1]['id'] if len(rows) > 100 else None}


def restore(db, cid, iid, body, actor):
    state = load(db, cid, iid)
    require(_revision(body.get('expectedRevision')) == state['revision'],
            'Заметки изменились в другом окне. Обновите данные перед восстановлением.', 409)
    active = state['active']
    active_revision = active['revision'] if active else None
    require((type(body.get('scriptRevision')) is int if active else body.get('scriptRevision') is None) and
            body.get('scriptRevision') == active_revision, 'Версия скрипта изменилась.', 409)
    require(state['interviewStatus'] in ('live', 'completed'), 'Заметки доступны после начала интервью.', 409)
    version_id = body.get('versionId')
    require(isinstance(version_id, str) and len(version_id) <= 64, 'Укажите версию заметки.', 400)
    row = db.execute('SELECT body,script_revision FROM cp_interview_note_versions WHERE id=? AND interview_id=?',
                     (version_id, iid)).fetchone()
    require(row, 'Версия заметки не найдена.', 404)
    require(row[1] is None or row[1] == active_revision, 'Эта версия относится к другому скрипту.', 409)
    old = json.loads(row[0])
    ids = {q['id'] for q in active['questions']} if active else set()
    require(set(old['answers']) <= ids, 'Эта версия относится к другому скрипту.', 409)
    progress = dict(state['progress'])
    answers = {qid: dict(answer) for qid, answer in progress.get('answers', {}).items()}
    for qid in set(answers) | set(old['answers']):
        answers[qid] = {'status': answers.get(qid, {}).get('status', ''),
                        'note': old['answers'].get(qid, {}).get('note', '')}
    progress.update(general=old['general'], noteDocument=note_document(old['noteDocument']), answers=answers)
    # Both versions and progress are in the caller's BEGIN IMMEDIATE transaction.
    _capture(db, iid, active_revision, state['progress'], actor, 'before_restore', force=True)
    _capture(db, iid, active_revision, progress, actor, 'restored', force=True)
    _state_row(db, iid)
    db.execute('UPDATE cp_interview_coach SET progress=?,revision=revision+1,updated_at=? WHERE interview_id=?',
               (json.dumps(progress, ensure_ascii=False), iso(now()), iid))
    return load(db, cid, iid)


def original_resume(db, cid, iid):
    interview(db, cid, iid)
    row = db.execute('SELECT filename,mime,content FROM cp_resumes WHERE candidate_id=?', (cid,)).fetchone()
    if not row:
        return {'resume': None}
    require(row[1] in ('application/pdf', 'image/jpeg', 'image/png'),
            'Оригинал резюме сохранён, но этот формат пока не поддерживается для просмотра. Нужен PDF, JPG или PNG.', 409)
    require(len(row[2]) <= 20 * 1024 * 1024, 'Файл резюме превышает 20 МБ.', 413)
    return {'resume': {'filename': row[0], 'mimeType': row[1],
                       'contentBase64': base64.b64encode(row[2]).decode('ascii')}}
