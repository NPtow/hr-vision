"""Telegram Mini App validation. Raw credentials are never stored or logged."""
import hashlib
import hmac
import json
import re
import time
from urllib.parse import parse_qsl, urlsplit

from domain import require


def valid_app_url(value, origin):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
        return False
    try:
        target, allowed = urlsplit(value), urlsplit(origin)
        return (target.scheme == allowed.scheme == 'https' and bool(allowed.hostname)
                and target.netloc == allowed.netloc and not target.username and not target.password
                and not allowed.username and not allowed.password and not target.fragment
                and target.path == '/iframe.html')
    except ValueError:
        return False


def verify(raw, bot_token, tick=None):
    try:
        size = len(raw.encode('utf-8')) if isinstance(raw, str) else 0
    except UnicodeEncodeError:
        size = 0
    require(0 < size <= 16384 and bot_token,
            'Не удалось проверить вход из Telegram. Откройте кабинет кнопкой бота.', 401)
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=32)
    except ValueError:
        pairs = []
    fields = dict(pairs)
    require(pairs and len(fields) == len(pairs) and all(re.fullmatch(r'[A-Za-z_]+', key) for key in fields),
            'Некорректные данные Telegram.', 401)
    signature = fields.pop('hash', '')
    require(re.fullmatch(r'[0-9a-f]{64}', signature), 'Некорректная подпись Telegram.', 401)
    secret = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, '\n'.join(f'{k}={v}' for k, v in sorted(fields.items())).encode(), hashlib.sha256).hexdigest()
    require(hmac.compare_digest(signature, expected), 'Некорректная подпись Telegram.', 401)
    date = fields.get('auth_date', '')
    tick = int(time.time()) if tick is None else tick
    require(re.fullmatch(r'[0-9]{1,12}', date) and tick - 300 <= int(date) <= tick + 30,
            'Вход из Telegram устарел. Закройте кабинет и откройте его кнопкой бота ещё раз.', 401)
    try:
        user = json.loads(fields.get('user', 'null'))
    except (ValueError, TypeError):
        user = None
    require(isinstance(user, dict) and type(user.get('id')) is int and 0 < user['id'] < 2 ** 52
            and not user.get('is_bot') and fields.get('chat_type', 'private') == 'private',
            'Откройте кабинет в личном чате с ботом.', 401)
    return str(user['id'])
