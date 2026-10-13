"""Strict DOM adapter. A calibrated selector profile is required for writes.

Selectors belong to an observed HH employer UI version, not inferred API routes.
The injected Page is exclusively owned by hh-daily's existing Playwright thread.
"""
from dataclasses import dataclass
import hashlib
import json
import re
import time
from urllib.parse import urljoin, urlsplit


class BrowserError(Exception):
    def __init__(self, code, *, pause=False):
        self.code, self.pause = code, pause
        super().__init__(code)


def identifier(value):
    value = str(value or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', value):
        raise BrowserError('invalid_identifier')
    return value


def hh_url(value, base='https://hh.ru'):
    if not isinstance(value, str) or not value or any(c in value for c in '\r\n\\'):
        raise BrowserError('invalid_hh_url')
    url = urljoin(base, value)
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or not (host == 'hh.ru' or host.endswith('.hh.ru'))
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.fragment):
        raise BrowserError('invalid_hh_url')
    return url


def message_hash(message):
    return hashlib.sha256(message.encode('utf-8')).hexdigest()


def exact_text(value):
    # Browser inner_text uses LF; do not collapse whitespace or normalize names.
    return str(value).replace('\r\n', '\n').strip()


@dataclass(frozen=True)
class SelectorProfile:
    """Each value extractor is {selector, attribute?, pattern?}.

    pattern must have exactly one capture group. Missing/ambiguous identity is
    a hard stop. Neither the account ID nor recipient ID comes from job data.
    """
    data: dict

    @classmethod
    def from_file(cls, path):
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
        return cls(data)

    def __post_init__(self):
        if self.data.get('version') != 1 or not self.data.get('name'):
            raise BrowserError('invalid_selector_profile')
        for section, fields in {
            'identity': ('accountId', 'role'),
            'resume': ('resumeId', 'name', 'cvText', 'fullCVMarker', 'chatUrl'),
            'chat': ('resumeId', 'chatId', 'composer', 'send', 'messages', 'messageText', 'messageId'),
            'applicants': ('rows', 'resumeUrl', 'vacancyId', 'responseId'),
        }.items():
            for field in fields:
                item = self.data.get(section, {}).get(field)
                if not isinstance(item, dict) or not isinstance(item.get('selector'), str) or not item['selector']:
                    raise BrowserError('invalid_selector_profile')
                if item.get('pattern') and re.compile(item['pattern']).groups != 1:
                    raise BrowserError('invalid_selector_profile')
        if not self.data.get('guards', {}).get('captcha') or not self.data.get('guards', {}).get('login'):
            raise BrowserError('invalid_selector_profile')


class HHBrowserAdapter:
    def __init__(self, page, profile, *, clock=time.time, checkpoint=lambda: None, ack_timeout=10):
        self.page, self.profile, self.clock = page, profile.data, clock
        self.checkpoint = checkpoint
        self.ack_timeout = ack_timeout

    def one(self, root, spec, *, optional=False):
        loc = root.locator(spec['selector'])
        count = loc.count()
        if optional and count == 0:
            return None
        if count != 1:
            raise BrowserError('page_structure_changed', pause=True)
        return loc

    def value(self, root, spec, *, optional=False):
        loc = self.one(root, spec, optional=optional)
        if loc is None:
            return ''
        value = loc.get_attribute(spec['attribute']) if spec.get('attribute') else loc.inner_text(timeout=5000)
        value = str(value or '').strip()
        if spec.get('pattern'):
            match = re.search(spec['pattern'], value)
            if not match:
                raise BrowserError('page_structure_changed', pause=True)
            value = match.group(1)
        return value

    def guard(self, account_id):
        hh_url(self.page.url)
        for kind in ('captcha', 'login'):
            for selector in self.profile['guards'][kind]:
                loc = self.page.locator(selector)
                if loc.count() and any(loc.nth(i).is_visible() for i in range(loc.count())):
                    raise BrowserError('captcha' if kind == 'captcha' else 'login_required', pause=True)
        ident = self.profile['identity']
        if self.value(self.page, ident['role']) != ident.get('employerRole', 'employer'):
            raise BrowserError('employer_role_required', pause=True)
        if self.value(self.page, ident['accountId']) != account_id:
            raise BrowserError('account_mismatch', pause=True)

    def navigate(self, url, account_id):
        self.checkpoint()
        self.page.goto(hh_url(url), wait_until='domcontentloaded', timeout=20000)
        self.guard(account_id)

    def resume(self, url, account_id, *, source, vacancy_id='', response_id='', chat_url=''):
        self.navigate(url, account_id)
        spec = self.profile['resume']
        full_marker = self.one(self.page, spec['fullCVMarker'], optional=True)
        full_cv = bool(full_marker is not None and full_marker.is_visible())
        candidate = {
            'accountId': account_id,
            'resumeId': identifier(self.value(self.page, spec['resumeId'])),
            'name': self.value(self.page, spec['name'])[:200],
            'cvText': self.value(self.page, spec['cvText'])[:80000],
            'cvAvailable': full_cv,
            'resumeUrl': hh_url(self.page.url), 'source': source,
            'vacancyId': vacancy_id, 'responseId': response_id,
        }
        # Only an already available chat link: never click an invitation/contact
        # purchase action just to create a conversation during discovery.
        link = chat_url or self.value(self.page, spec['chatUrl'], optional=True)
        if link:
            candidate['chatUrl'] = hh_url(link, self.page.url)
            self.navigate(candidate['chatUrl'], account_id)
            chat = self.chat_identity(account_id)
            if chat['resumeId'] != candidate['resumeId']:
                raise BrowserError('recipient_mismatch', pause=True)
            candidate['chatId'] = chat['chatId']
            candidate['discoveryStatus'] = 'ready'
        else:
            candidate.update(chatId='', chatUrl='', discoveryStatus='chat_unavailable')
        return candidate

    def discover(self, source):
        account = identifier(source.get('accountId'))
        kind = source.get('kind')
        limit = source.get('limit', 50)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 2000:
            raise BrowserError('invalid_discovery_limit')
        # One result stays below 20 MiB even for escaped 80K-character CV text.
        # The durable caller carries cursor across pages/operations.
        limit = min(limit, 25)
        cursor = source.get('cursor') or {}
        if not isinstance(cursor, dict):
            raise BrowserError('invalid_cursor')
        offset = cursor.get('offset', 0)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise BrowserError('invalid_cursor')
        found, seen = [], set()
        if kind == 'direct':
            urls = source.get('resumeUrls')
            if not isinstance(urls, list) or not urls or len(urls) > 2000 or offset > len(urls):
                raise BrowserError('selected_resumes_required')
            for url in urls[offset:offset + limit]:
                candidate = self.resume(url, account, source='direct')
                if candidate['resumeId'] not in seen:
                    seen.add(candidate['resumeId']); found.append(candidate)
            complete = offset + limit >= len(urls)
            return {'candidates': found, 'complete': complete,
                    'cursor': None if complete else {'offset': offset + limit}}
        if kind != 'vacancy_applicants':
            raise BrowserError('invalid_discovery_source')
        vacancy = identifier(source.get('vacancyId'))
        url = hh_url(cursor.get('listUrl') or source.get('listUrl'))
        pages, next_url = set(), url
        spec = self.profile['applicants']
        while next_url and len(pages) < 20 and len(found) < limit:
            if next_url in pages:
                raise BrowserError('repeated_page', pause=True)
            pages.add(next_url)
            self.navigate(next_url, account)
            current_url = next_url
            if self.value(self.page, spec['vacancyId']) != vacancy:
                raise BrowserError('vacancy_mismatch', pause=True)
            rows = self.page.locator(spec['rows']['selector'])
            entries = []
            for i in range(min(rows.count(), 200)):
                row = rows.nth(i)
                entries.append({
                    'url': hh_url(self.value(row, spec['resumeUrl']), self.page.url),
                    'response_id': identifier(self.value(row, spec['responseId'])),
                    'chat_url': self.value(row, spec['chatUrl'], optional=True) if spec.get('chatUrl') else '',
                })
            next_link = self.value(self.page, spec['next'], optional=True) if spec.get('next') else ''
            next_url = hh_url(next_link, self.page.url) if next_link else ''
            if offset > len(entries):
                raise BrowserError('list_changed_requires_restart', pause=True)
            for index in range(offset, len(entries)):
                entry = entries[index]
                candidate = self.resume(entry['url'], account, source=kind, vacancy_id=vacancy,
                                        response_id=entry['response_id'], chat_url=entry['chat_url'])
                key = (candidate['resumeId'], candidate['responseId'])
                if key not in seen:
                    seen.add(key); found.append(candidate)
                if len(found) == limit:
                    more_here = index + 1 < len(entries)
                    next_cursor = ({'listUrl': current_url, 'offset': index + 1} if more_here
                                   else {'listUrl': next_url, 'offset': 0} if next_url else None)
                    return {'candidates': found, 'complete': next_cursor is None, 'cursor': next_cursor}
            offset = 0
        return {'candidates': found, 'complete': not next_url,
                'cursor': {'listUrl': next_url, 'offset': 0} if next_url else None}

    def chat_identity(self, account):
        self.guard(account)
        spec = self.profile['chat']
        return {'accountId': account, 'resumeId': identifier(self.value(self.page, spec['resumeId'])),
                'chatId': identifier(self.value(self.page, spec['chatId']))}

    def verify_target(self, target):
        observed = self.chat_identity(target['accountId'])
        if any(observed[k] != target[k] for k in ('accountId', 'resumeId', 'chatId')):
            raise BrowserError('recipient_mismatch', pause=True)
        return observed

    def persisted_messages(self, target):
        self.verify_target(target)
        spec = self.profile['chat']
        rows = self.page.locator(spec['messages']['selector'])
        result = []
        if rows.count() > 2000:
            raise BrowserError('message_list_too_large', pause=True)
        for i in range(rows.count()):
            row = rows.nth(i)
            # Profile messages selector must select outgoing messages only.
            mid = self.value(row, spec['messageId'])
            if not mid:
                continue
            result.append({'messageId': identifier(mid), 'text': exact_text(self.value(row, spec['messageText']))})
        return result

    def prepare(self, target, message):
        if self.profile.get('calibrated') is not True:
            raise BrowserError('selectors_not_calibrated', pause=True)
        target = {k: identifier(target.get(k)) for k in ('accountId', 'resumeId', 'chatId')} | {
            'chatUrl': hh_url(target.get('chatUrl'))}
        if not isinstance(message, str) or not 1 <= len(message) <= 10000 or message != exact_text(message):
            raise BrowserError('invalid_message')
        self.navigate(target['chatUrl'], target['accountId'])
        self.verify_target(target)
        # Reload before baseline: distinguish persistent messages from client-only
        # optimistic DOM. A matching pre-existing message is never blindly resent.
        self.page.reload(wait_until='domcontentloaded', timeout=20000)
        baseline = self.persisted_messages(target)
        if any(row['text'] == message for row in baseline):
            raise BrowserError('matching_message_exists', pause=True)
        self.one(self.page, self.profile['chat']['composer']).fill(message, timeout=5000)
        self.one(self.page, self.profile['chat']['send'])
        return {'target': target, 'message': message, 'messageHash': message_hash(message),
                'baseline': [row['messageId'] for row in baseline]}

    def click_send(self, prepared, *, checkpoint=lambda: None):
        self.verify_target(prepared['target'])
        spec = self.profile['chat']
        composer = self.one(self.page, spec['composer'])
        value = composer.input_value(timeout=5000) if spec.get('composerValue', 'input') == 'input' else composer.inner_text(timeout=5000)
        if exact_text(value) != prepared['message']:
            raise BrowserError('message_changed', pause=True)
        # No force=True, Enter fallback, or retry. An exception is indeterminate.
        send = self.one(self.page, spec['send'])
        checkpoint()
        send.click(timeout=5000)

    def observe(self, prepared):
        target = prepared['target']
        self.navigate(target['chatUrl'], target['accountId'])
        self.page.reload(wait_until='domcontentloaded', timeout=20000)
        matches = [row for row in self.persisted_messages(target)
                   if row['messageId'] not in prepared['baseline'] and row['text'] == prepared['message']]
        if len(matches) != 1:
            return {'outcome': 'unknown', 'reason': 'message_not_uniquely_observed'}
        return {'outcome': 'sent_observed', 'evidence': {
            **{k: target[k] for k in ('accountId', 'resumeId', 'chatId')},
            'messageHash': prepared['messageHash'], 'messageId': matches[0]['messageId'],
            'observedAt': self.clock(), 'persisted': True,
        }}

    def wait_for_ack(self, prepared):
        """Do not navigate away while the website is still saving the message.

        A new outgoing DOM row with an ID is only a provisional acknowledgement;
        observe() must subsequently reload to establish persistence.
        """
        deadline = time.monotonic() + self.ack_timeout
        while True:
            matches = [row for row in self.persisted_messages(prepared['target'])
                       if row['messageId'] not in prepared['baseline'] and row['text'] == prepared['message']]
            if len(matches) == 1:
                return
            if len(matches) > 1 or time.monotonic() >= deadline:
                raise BrowserError('send_ack_uncertain', pause=True)
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
