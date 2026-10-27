"""Remember a private test invitation without putting its key in web assets."""
import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
import secrets
import time

COOKIE = '__Host-hr-vision-access'
LIFETIME = 30 * 86400


def signature(value, key):
    return hmac.new(key.encode(), ('browser-access:' + value).encode(), hashlib.sha256).hexdigest()


def valid(header, key, now=None):
    now = int(time.time()) if now is None else now
    try:
        cookies = SimpleCookie()
        cookies.load(header or '')
        value = cookies[COOKIE].value
        expiry, nonce, digest = value.split('.')
        return (now < int(expiry) <= now + LIFETIME and len(nonce) == 24
                and hmac.compare_digest(digest, signature(expiry + '.' + nonce, key)))
    except (CookieError, KeyError, ValueError, TypeError):
        return False


def issue(key, now=None):
    now = int(time.time()) if now is None else now
    value = str(now + LIFETIME) + '.' + secrets.token_hex(12)
    return f'{COOKIE}={value}.{signature(value, key)}; Max-Age={LIFETIME}; Path=/; Secure; HttpOnly; SameSite=Lax'
