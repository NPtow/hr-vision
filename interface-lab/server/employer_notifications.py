"""Personal employer Telegram binding and canonical shortlist outbox.

Uses the single candidate notification worker and transport. Transactions belong
to callers; no provider I/O here. Observations persist even when delivery is off.
"""
import hashlib
import json
import re
import secrets
from urllib.parse import urlencode

import candidate_notifications as delivery
import employer_access as access
import employer_store as store
from domain import Problem, require
from telegram_miniapp import valid_app_url, verify


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS en_bindings (
        account_id TEXT PRIMARY KEY REFERENCES em_accounts(id), chat_id TEXT NOT NULL UNIQUE,
        generation TEXT NOT NULL, linked_at INTEGER NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS en_tokens (
        digest TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES em_accounts(id),
        expires_at INTEGER NOT NULL, used_at INTEGER)''')
    db.execute('''CREATE TABLE IF NOT EXISTS en_sessions (token_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS en_seen (
        account_id TEXT NOT NULL, application_id TEXT NOT NULL,
        PRIMARY KEY(account_id,application_id))''')


def personal(db, principal):
    row = db.execute('''SELECT a.id,a.workspace_id FROM em_accounts a JOIN em_workspaces w ON w.id=a.workspace_id
        WHERE a.id=? AND a.workspace_id=? AND a.active=1 AND a.is_team=0 AND w.kind='employer' ''',
        (principal['accountId'], principal['workspaceId'])).fetchone()
    require(row, 'Подключите личный кабинет работодателя.', 403)
    return row


def app_url(config=None, vacancy_id=None):
    config = delivery._config(config)
    query = {'id': 'hr-vision-product--manager', 'viewMode': 'story', 'miniapp': 'employer', 'screen': 'shortlist'}
    if vacancy_id:
        query['vacancyId'] = vacancy_id
    url = config.product_origin.rstrip('/') + '/iframe.html?' + urlencode(query)
    require(valid_app_url(url, config.product_origin), 'Адрес кабинета Telegram не настроен.', 503)
    return url


def button(config=None, vacancy_id=None):
    return {'inline_keyboard': [[{'text': 'Посмотреть подборку', 'web_app': {'url': app_url(config, vacancy_id)}}]]}


def status(db, principal, config=None):
    personal(db, principal)
    config = delivery._config(config)
    linked = db.execute('SELECT 1 FROM en_bindings WHERE account_id=?', (principal['accountId'],)).fetchone()
    return {'linked': bool(linked), 'available': config.telegram_binding_available,
            'status': 'connected' if linked else 'not_connected',
            'reminderChannels': {'email': 'ready' if config.email_configured else 'disabled',
                                 'telegram': 'ready' if linked and config.telegram_configured else
                                     'unbound' if config.telegram_configured else 'disabled'}}


def create_link(db, principal, config=None, tick=None):
    personal(db, principal)
    config, tick = delivery._config(config), delivery._now(tick)
    require(config.telegram_binding_available, 'Telegram временно недоступен.', 503)
    app_url(config)
    token = secrets.token_urlsafe(32)
    db.execute('DELETE FROM en_tokens WHERE account_id=? OR expires_at<=?', (principal['accountId'], tick))
    db.execute('INSERT INTO en_tokens VALUES (?,?,?,NULL)',
               (hashlib.sha256(token.encode()).hexdigest(), principal['accountId'], tick + 600))
    return {'url': f'https://t.me/{config.telegram_username}?start=em_{token}', 'expiresAt': tick + 600}


def _revoke_sessions(db, account_id):
    db.execute('DELETE FROM em_sessions WHERE token_hash IN (SELECT token_hash FROM en_sessions WHERE account_id=?)', (account_id,))
    db.execute('DELETE FROM en_sessions WHERE account_id=?', (account_id,))


def disconnect(db, principal):
    personal(db, principal)
    _revoke_sessions(db, principal['accountId'])
    db.execute('DELETE FROM en_bindings WHERE account_id=?', (principal['accountId'],))
    db.execute('DELETE FROM en_tokens WHERE account_id=?', (principal['accountId'],))
    # Pending rows keep their audit record and fail binding-generation validation.
    return {'linked': False}


def _visible(db, workspace_id):
    return store.applications(db, workspace_id, view='proposed')


def _observe(db, account_id, apps):
    added = []
    for app in apps:
        if db.execute('INSERT OR IGNORE INTO en_seen VALUES (?,?)', (account_id, app['applicationId'])).rowcount:
            added.append(app)
    return added


def consume(db, update, tick, config=None):
    config = delivery._config(config)
    init(db)
    message = update['message']
    chat = delivery._private_chat_id(message['chat']['id'])
    match = re.fullmatch(r'/start(?:@[A-Za-z0-9_]+)? em_([A-Za-z0-9_-]{43})', message.get('text', ''))
    row = db.execute('''SELECT t.account_id,a.workspace_id FROM en_tokens t JOIN em_accounts a ON a.id=t.account_id
        JOIN em_workspaces w ON w.id=a.workspace_id WHERE t.digest=? AND t.used_at IS NULL AND t.expires_at>?
        AND a.active=1 AND a.is_team=0 AND w.kind='employer' ''',
        (hashlib.sha256(match[1].encode()).hexdigest() if match else '', tick)).fetchone()
    result, text, markup = 'expired', 'Ссылка истекла или уже использована. В кабинете работодателя нажмите «Подключить Telegram» ещё раз.', None
    if not match:
        result, text = 'wrong_link', 'Некорректная ссылка. Подключите Telegram из меню кабинета работодателя.'
    elif row:
        other = db.execute('SELECT account_id FROM en_bindings WHERE chat_id=?', (chat,)).fetchone()
        if other and other[0] != row[0]:
            result, text = 'chat_already_linked', 'Этот Telegram подключён к другому работодателю. Сначала отключите его в том кабинете.'
        else:
            _revoke_sessions(db, row[0])
            db.execute('UPDATE en_tokens SET used_at=? WHERE digest=?', (tick, hashlib.sha256(match[1].encode()).hexdigest()))
            db.execute('''INSERT INTO en_bindings VALUES (?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET
                chat_id=excluded.chat_id,generation=excluded.generation,linked_at=excluded.linked_at''',
                (row[0], chat, secrets.token_hex(16), tick))
            _observe(db, row[0], _visible(db, row[1]))
            result, text, markup = 'linked', 'Telegram подключён. Новые подборки кандидатов будут приходить сюда.', button(config)
    delivery.enqueue_telegram_reply(db, chat, 'employer-link:' + str(update['update_id']), text, markup, settings=config, now=tick)
    return {'status': result}


def exchange(db, body, config=None):
    require(isinstance(body, dict) and set(body) == {'initData'}, 'Проверьте поля входа.', 400)
    config = delivery._config(config)
    require(config.telegram_configured, 'Telegram временно недоступен.', 503)
    chat = verify(body['initData'], config.telegram_token)
    row = db.execute('''SELECT b.account_id FROM en_bindings b JOIN em_accounts a ON a.id=b.account_id
        JOIN em_workspaces w ON w.id=a.workspace_id WHERE b.chat_id=? AND a.active=1 AND a.is_team=0 AND w.kind='employer' ''', (chat,)).fetchone()
    require(row, 'Telegram ещё не подключён. Войдите в свой кабинет и подключите его в меню аккаунта.', 403)
    session = access._issue_session(db, row[0])
    db.execute('DELETE FROM en_sessions WHERE token_hash NOT IN (SELECT token_hash FROM em_sessions)')
    db.execute('INSERT INTO en_sessions VALUES (?,?)', (hashlib.sha256(session['token'].encode()).hexdigest(), row[0]))
    return session


def collect(db, config=None, tick=None):
    """Observe canonical availability, group new applications per vacancy/cycle.

    Also runs in publication/approval transactions. Disabled/unbound observations
    are consumed, so enabling delivery or linking later never replays history.
    """
    if not delivery._columns(db, 'en_seen') or not delivery._columns(db, 'em_reviews'):
        return
    config, tick = delivery._config(config), delivery._now(tick)
    accounts = db.execute('''SELECT a.id,a.workspace_id,b.chat_id,b.generation FROM em_accounts a
        JOIN em_workspaces w ON w.id=a.workspace_id LEFT JOIN en_bindings b ON b.account_id=a.id
        WHERE a.is_team=0 AND a.active=1 AND w.kind='employer' ''').fetchall()
    for account, workspace, chat, generation in accounts:
        added = _observe(db, account, _visible(db, workspace))
        if not chat or not config.telegram_configured:
            continue
        groups = {}
        for app in added:
            groups.setdefault(app['vacancyId'], []).append(app['applicationId'])
        for vacancy, ids in groups.items():
            # Merge into an unclaimed notice for this vacancy and binding.
            pending = db.execute("SELECT id,payload_json FROM cn_outbox WHERE event_type='telegram.reply' AND status='pending' AND attempts=0 AND event_key LIKE 'employer-shortlist:%'").fetchall()
            existing = next(((oid, json.loads(raw)) for oid, raw in pending
                             if json.loads(raw).get('employer') == [account, workspace, vacancy, generation]), None)
            payload = {'text': 'Новая подборка кандидатов', 'employer': [account, workspace, vacancy, generation], 'applications': ids}
            if existing:
                oid, payload = existing
                payload['applications'] = sorted(set(payload['applications'] + ids))
                db.execute('UPDATE cn_outbox SET payload_json=? WHERE id=?', (json.dumps(payload), oid))
            else:
                delivery._enqueue_telegram(db, chat, 'employer-shortlist:' + secrets.token_hex(16), 'telegram.reply', payload, None, config, tick)


def prepare(db, row, config):
    """Revalidate identity, ownership and exact visibility before claiming delivery."""
    payload = json.loads(row['payload_json'] or '{}')
    identity = payload.get('employer')
    if not identity:
        return None
    account, workspace, vacancy, generation = identity
    binding = db.execute('''SELECT b.chat_id,b.generation FROM en_bindings b JOIN em_accounts a ON a.id=b.account_id
        JOIN em_workspaces w ON w.id=a.workspace_id WHERE a.id=? AND a.workspace_id=? AND a.active=1
        AND a.is_team=0 AND w.kind='employer' ''', (account, workspace)).fetchone()
    if not binding or tuple(binding) != (row['recipient'], generation):
        return 'employer_binding_changed'
    try:
        title = store.vacancy(db, workspace, vacancy)['title']
        visible = {a['applicationId'] for a in _visible(db, workspace) if a['vacancyId'] == vacancy}
    except Problem:
        return 'employer_shortlist_unavailable'
    count = len(visible.intersection(payload['applications']))
    if not count:
        return 'employer_shortlist_unavailable'
    payload.update(text=f'Новые кандидаты по вакансии «{title}»\nДоступно новых кандидатов: {count}', reply_markup=button(config, vacancy))
    row['payload_json'] = json.dumps(payload)
    return None
