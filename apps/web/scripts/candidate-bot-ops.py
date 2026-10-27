#!/usr/bin/env python3
"""Candidate bot operator checks. No updates, messages, worker, or server deployment.

inspect is offline; check calls only getMe/getWebhookInfo. set-webhook is a local
plan unless --apply is explicit. Apply requires the API runtime already configured
with the same secret; it preserves pending updates and refuses another webhook.
Credentials are loaded from an owner-only file, never command-line arguments.
Provider replies, URLs containing tokens, and exception messages are never logged.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_ENV = Path.home() / '.config/hr-vision/candidate-bot-20261013.env'
ORIGIN = 'https://hr-vision.158-160-179-53.sslip.io'
WEBHOOK_PATH = '/api/hr/candidate/telegram/webhook'
KEYS = (
    'HR_NOTIFICATIONS_ENABLED', 'HR_TELEGRAM_BOT_TOKEN',
    'HR_TELEGRAM_BOT_USERNAME', 'HR_TELEGRAM_WEBHOOK_SECRET',
    'HR_TELEGRAM_POLLING', 'HR_ORIGIN', 'HR_CANDIDATE_PUBLIC_URL',
)
TOKEN = re.compile(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,100}\Z')
USERNAME = re.compile(r'[A-Za-z0-9_]{5,32}\Z')
SECRET = re.compile(r'[A-Za-z0-9_-]{32,256}\Z')
MAX_RESPONSE = 65536


class OpsError(Exception):
    """Only module-owned error codes may cross the console boundary."""


def load_env(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or
                    stat.S_IMODE(info.st_mode) not in (0o400, 0o600) or
                    info.st_uid != os.geteuid()):
                raise OpsError('env_file_must_be_owned_by_current_user_with_mode_0600_or_0400')
            source = stream.read(65537)
    except OSError:
        raise OpsError('env_file_unreadable_or_symlink') from None
    if len(source) > 65536:
        raise OpsError('env_file_too_large')
    values = {}
    for line in source.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:]
        key, separator, raw = line.partition('=')
        key = key.strip()
        if not separator or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key) or key in values:
            raise OpsError('env_file_invalid_or_duplicate_assignment')
        try:
            parts = shlex.split(raw, comments=True, posix=True)
        except ValueError:
            raise OpsError('env_file_invalid_quoting') from None
        if len(parts) > 1:
            raise OpsError('env_file_unquoted_whitespace')
        value = parts[0] if parts else ''
        if any(ord(char) < 32 for char in value):
            raise OpsError('env_file_control_character')
        values[key] = value
    return values


def inspect_config(env):
    return {
        'present': {key: bool(env.get(key)) for key in KEYS},
        'notifications_enabled': env.get('HR_NOTIFICATIONS_ENABLED') == '1',
        'polling_enabled': env.get('HR_TELEGRAM_POLLING') == '1',
        'token_format_valid': bool(TOKEN.fullmatch(env.get('HR_TELEGRAM_BOT_TOKEN', ''))),
        'username_format_valid': bool(USERNAME.fullmatch(env.get('HR_TELEGRAM_BOT_USERNAME', '').lstrip('@'))),
        'webhook_secret_format_valid': bool(SECRET.fullmatch(env.get('HR_TELEGRAM_WEBHOOK_SECRET', ''))),
        'runtime_configuration_verified': False,
    }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a token in a URL or a secret header through a redirect.
        return None


def request_json(url, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, headers={
        'Content-Type': 'application/json', 'User-Agent': 'HRVisionCandidateBotOps/1',
    })
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise OpsError('provider_response_too_large')
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise OpsError('provider_response_invalid')
        return result
    except OpsError:
        raise
    except urllib.error.HTTPError as error:
        raise OpsError('http_status_' + str(error.code)) from None
    except Exception:
        # repr/str of errors can contain the complete credential-bearing URL.
        raise OpsError('network_or_json_error') from None


def telegram(env, method, payload=None):
    token = env.get('HR_TELEGRAM_BOT_TOKEN', '')
    if not TOKEN.fullmatch(token):
        raise OpsError('token_format_invalid')
    if method not in ('getMe', 'getWebhookInfo', 'setWebhook'):
        raise OpsError('unsupported_method')
    reply = request_json('https://api.telegram.org/bot' + token + '/' + method,
                         {} if payload is None else payload)
    if reply.get('ok') is not True:
        raise OpsError('telegram_request_rejected')
    return reply.get('result')


def check_provider(env, webhook_url):
    expected = env.get('HR_TELEGRAM_BOT_USERNAME', '').lstrip('@')
    if not USERNAME.fullmatch(expected):
        raise OpsError('username_format_invalid')
    identity = telegram(env, 'getMe')
    if (not isinstance(identity, dict) or identity.get('is_bot') is not True or
            str(identity.get('username', '')).lower() != expected.lower()):
        raise OpsError('bot_identity_mismatch')
    info = telegram(env, 'getWebhookInfo')
    if not isinstance(info, dict) or not isinstance(info.get('url'), str):
        raise OpsError('webhook_info_invalid')
    count = info.get('pending_update_count')
    return {
        'identity_matches_config': True,
        'webhook_registered': bool(info['url']),
        'webhook_matches_target': info['url'] == webhook_url,
        'pending_update_count': count if type(count) is int and count >= 0 else None,
        'last_delivery_error_present': bool(info.get('last_error_date') or info.get('last_error_message')),
        'message_updates_enabled': 'message' in info.get('allowed_updates', []),
        'callback_updates_enabled': 'callback_query' in info.get('allowed_updates', []),
    }


def webhook_target(env, origin):
    try:
        parsed = urllib.parse.urlsplit(origin)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
                parsed.password or parsed.query or parsed.fragment or
                parsed.path not in ('', '/') or parsed.port not in (None, 443, 80, 88, 8443) or
                any(ord(char) < 33 for char in origin)):
            raise ValueError()
    except ValueError:
        raise OpsError('origin_must_be_plain_https_origin') from None
    target = origin.rstrip('/')
    if env.get('HR_ORIGIN', ORIGIN).rstrip('/') != target:
        raise OpsError('origin_must_match_runtime_config')
    return target + WEBHOOK_PATH


def set_webhook(env, url, apply):
    config = inspect_config(env)
    blockers = []
    for key, code in (
            ('notifications_enabled', 'notifications_disabled'),
            ('token_format_valid', 'token_format_invalid'),
            ('username_format_valid', 'username_format_invalid'),
            ('webhook_secret_format_valid', 'webhook_secret_missing_or_invalid')):
        if not config[key]:
            blockers.append(code)
    if config['polling_enabled']:
        blockers.append('polling_must_be_disabled')
    plan = {
        'action': 'setWebhook', 'applied': False, 'blockers': blockers,
        'allowed_updates': ['message', 'callback_query'],
        'drop_pending_updates': False, 'max_connections': 1,
        'requires_api_runtime_with_same_secret': True,
        'starts_worker_or_deploys_api': False,
    }
    if not apply:
        return plan
    if blockers:
        raise OpsError('webhook_configuration_incomplete')
    state = check_provider(env, url)
    if state['webhook_registered'] and not state['webhook_matches_target']:
        raise OpsError('another_webhook_already_registered')
    origin = url[:-len(WEBHOOK_PATH)]
    if request_json(origin + '/api/hr/health').get('ok') is not True:
        raise OpsError('api_health_failed')
    if telegram(env, 'setWebhook', {
            'url': url, 'secret_token': env['HR_TELEGRAM_WEBHOOK_SECRET'],
            'allowed_updates': plan['allowed_updates'], 'max_connections': 1,
            'drop_pending_updates': False}) is not True:
        raise OpsError('set_webhook_not_confirmed')
    plan['applied'] = True
    # No automatic retry: a network failure may have happened after mutation.
    plan['verification'] = check_provider(env, url)
    if not plan['verification']['webhook_matches_target']:
        raise OpsError('set_webhook_verification_failed')
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=DEFAULT_ENV)
    parser.add_argument('--origin', default=ORIGIN)
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('inspect', help='Offline config presence/validity; no values printed.')
    commands.add_parser('check', help='Read-only bot identity and webhook metadata.')
    apply_parser = commands.add_parser('set-webhook', help='Local plan, unless --apply is explicit.')
    apply_parser.add_argument('--apply', action='store_true', help='Register webhook; runtime must already use this config.')
    args = parser.parse_args(argv)
    try:
        env = load_env(args.env_file)
        url = webhook_target(env, args.origin)
        if args.action == 'inspect':
            result = inspect_config(env)
        elif args.action == 'check':
            result = check_provider(env, url)
        else:
            result = set_webhook(env, url, args.apply)
        print(json.dumps(result, sort_keys=True))
        return 0
    except OpsError as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        return 1
    except Exception:
        print(json.dumps({'ok': False, 'error': 'operation_failed'}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
