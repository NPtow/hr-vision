#!/usr/bin/env python3
"""Bounded, public-only company website collection, staged for review.

No paid API, browser storage, credentials, login, message sending or live writes.
Records decode as PublicResearchRecord; additional receipt/link fields support
other collectors. A published address is never an inferred person or HR role.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import datetime as dt
import gzip
import hashlib
import heapq
import html
import json
import re
import shutil
import threading
import time
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
APP = Path.home() / 'Library/Application Support/TG Signals'
DEFAULT_OUT = ROOT / 'output/segments-2026-10-14/web'
MAX_RESPONSE = 8_000_000
RAW_BUDGET = 1_000_000_000
MIN_FREE = 5 * 1024**3
EMAIL = re.compile(r'(?<![\w.+-])([A-Z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Z0-9-]+(?:\.[A-Z0-9-]+)+)', re.I)
TG_USER = re.compile(r'^[A-Za-z][A-Za-z0-9_]{3,31}$')
TG_EXCLUDED = {'share', 'addstickers', 'addemoji', 'proxy', 'socks', 'login', 'iv', 'joinchat', 'addlist', 'boost', 'c', 's'}
BAD_PATH = re.compile(r'/(?:login|logout|signin|signup|auth|basket|cart|checkout|pay|payment|privacy|policy|offer|oferta|terms|politika-konfidencialnosti|politika-obrabotki|personal-data|processing-personal-data)(?:/|$)|\.(?:pdf|jpg|png|webp|svg|gif|ico|zip|rar|docx?|xlsx?|pptx?|css|js|mp[34]|woff2?)$', re.I)
BLOCK_TEXT = re.compile(r'checking your browser|verify you are human|just a moment|access denied|доступ ограничен|проверка браузера|подтвердите,? что вы не робот|введите код с картинки', re.I)
REVIEW_CONTEXT = re.compile(r'отзыв|testimonial|review|alumn|graduate|student|ученик|выпускник|истори[яи] успеха|истори[яи] студент', re.I)
PRIVATE_PROFILE = re.compile(r'/id\d+$|/in/[\w-]+/?$', re.I)
SOCIAL_CLASS = re.compile(r'social|soclink|soci?al|footer|header|contact|messenger', re.I)
SOURCE_TEXT = re.compile(r'контакт|contact|partner|партн[её]р|сотруднич|ваканси|career|карьер|команд|team|about|о.компании|о.нас|employer|vacancy|работа.у.нас|пресс|press', re.I)
MULTIPART_SUFFIX = {'co.uk', 'com.ua', 'com.ru', 'org.ru', 'net.ru', 'com.au', 'co.in', 'com.br', 'com.tr', 'co.jp', 'com.cn', 'com.kz'}
IO_LOCK = threading.Lock()
PRINT_LOCK = threading.Lock()
DOMAIN_LOCK = threading.Lock()
DOMAIN_TIMES: dict[str, float] = {}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def compact(value):
    return re.sub(r'\s+', ' ', value or '').strip()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()[:20]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False, indent=2).encode()
    temp = path.with_name(path.name + '.' + str(threading.get_ident()) + '.tmp')
    temp.write_bytes(raw)
    temp.replace(path)


def host(url):
    return (urlparse(url).hostname or '').lower().removeprefix('www.')


def site_domain(url):
    parts = host(url).split('.')
    count = 3 if '.'.join(parts[-2:]) in MULTIPART_SUFFIX else 2
    return '.'.join(parts[-count:])


def same_site(url, base):
    return bool(host(url)) and site_domain(url) == site_domain(base)


def clean_url(url):
    try:
        p = urlparse(html.unescape(url.strip()))
        if p.scheme not in ('http', 'https') or not p.hostname:
            return None
        if p.username or p.password:
            return None
        h = p.hostname.lower()
        if h in ('localhost', '127.0.0.1', '0.0.0.0') or h.endswith('.local'):
            return None
        query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                 if not k.lower().startswith(('utm_', 'yclid', 'gclid', '_openstat', 'fbclid'))]
        return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path or '/', '', urlencode(query), ''))
    except (ValueError, UnicodeError):
        return None


def telegram_url(url):
    p = urlparse(url)
    if (p.hostname or '').lower() not in ('t.me', 'www.t.me', 'telegram.me', 'www.telegram.me'):
        return None
    parts = p.path.strip('/').split('/')
    if parts and parts[0] == 's':
        parts = parts[1:]
    if not parts:
        return None
    name = parts[0]
    if name.startswith('+') and re.fullmatch(r'\+[A-Za-z0-9_-]{8,}', name):
        return 'https://t.me/' + name
    if name == 'joinchat' and len(parts) > 1:
        return 'https://t.me/+' + parts[1]
    if not TG_USER.fullmatch(name) or name.lower() in TG_EXCLUDED:
        return None
    # A post/reference is not a verified company channel.
    if len(parts) > 1:
        return None
    return 'https://t.me/' + name


def social_kind(url):
    p = urlparse(url)
    h = (p.hostname or '').lower().removeprefix('www.').removeprefix('m.')
    if telegram_url(url):
        return 'telegram'
    if h in ('vk.com', 'vk.ru', 'vkontakte.ru') and p.path.strip('/') and not p.path.startswith(('/share', '/away', '/im', '/wall', '/video', '/photo', '/clip')):
        return 'vk'
    if h.endswith('linkedin.com') and p.path.startswith(('/in/', '/company/')):
        return 'linkedin'
    if h in ('wa.me', 'api.whatsapp.com', 'chat.whatsapp.com'):
        return 'whatsapp'
    if h in ('tenchat.ru', 'youtube.com', 'youtu.be', 'instagram.com'):
        return 'publicProfile'
    return None


def category(url, label=''):
    t = compact(unquote(urlparse(url).path) + ' ' + label).lower()
    h = host(url)
    if h.endswith(('hh.ru', 'hh.kz', 'headhunter.ru', 'superjob.ru')) and re.search(r'employer|client|company|работ', t):
        return 'employer', 99
    if re.search(r'vacan|vacant|vakans|ваканси|/jobs?(?:/|$)|работа у нас|work.with.us|присоедин.*команд', t):
        return 'vacancy', 100
    if re.search(r'career|карьер|/hr(?:/|$)|recruit', t) or host(url).startswith(('career.', 'jobs.', 'hr.')):
        return 'career', 98
    if re.search(r'contact|контакт|rekviz|реквизит', t):
        return 'contact', 96
    if re.search(r'cooperat|partner|сотруднич|партн[её]р|franch|франшиз', t):
        return 'cooperation', 86
    if re.search(r'(?:^|[/\s])team(?:$|[/\s])|наша команда|о команде|руководств|leadership|our.people', t):
        return 'team', 82
    if re.search(r'about|о нас|о компании|obrazovatelnoy-organizatsii|sveden', t):
        return 'about', 70
    if re.search(r'press|пресс|news|новост', t):
        return 'news', 50
    if re.search(r'blog|блог|article|стат', t):
        return 'article', 40
    if social_kind(url):
        return 'social', 0
    return 'other', 0


def nearest_context(anchor, limit=380):
    """Smallest useful contiguous text; never borrow an adjacent person's line."""
    label = compact(anchor.get_text(' ', strip=True))
    current = anchor
    fallback = label
    for _ in range(4):
        parent = current.parent
        if parent is None or parent.name in ('body', 'html'):
            break
        # Navigation may contain one phone and twenty unrelated menu items.
        # Never infer that phone's job from the menu's "Вакансии" item.
        if parent.name in ('nav', 'header', 'footer') or len(parent.select('a[href]')) > 3:
            break
        text = compact(parent.get_text(' ', strip=True))
        if len(text) > limit:
            break
        linked_contacts = [a for a in parent.select('a[href]')
                           if a.get('href', '').lower().startswith(('mailto:', 'tel:'))
                           or social_kind(urljoin('https://example.invalid', a.get('href', '')))]
        addresses = set(EMAIL.findall(text))
        if len(linked_contacts) > 1 or len(addresses) > 1:
            break
        if text and len(text) > len(label) + 3:
            return text
        if text:
            fallback = text
        current = parent
    return fallback


def purpose(context, raw_address='', default='unknown'):
    # An email prefix, neighbouring card or site navigation never supplies a role.
    text = compact(context.replace(raw_address, ''))
    if re.search(r'вашу ваканси|если вы компания|для работодател|разместить ваканси|опубликуем.*ваканси', text, re.I):
        return 'cooperation'
    if re.search(r'центр[а-я]* карьер|вакансии.*стажировки|карьер[а-я]* (?:выпускник|консультац|помощник)|комьюнити', text, re.I):
        return default
    if re.search(r'сотруднич|партн[её]р|реклам|сми\b|пресс|partnership|cooperat', text, re.I):
        return 'cooperation'
    if re.search(r'ваканси|работа у нас|отдел кадров|рекрутер|резюме|hr(?:\s+|[-–])(?:менеджер|отдел|специалист)|карьера в |hiring|recruit', text, re.I):
        return 'hiring'
    if re.search(r'поддерж|support|техническ|помощ', text, re.I):
        return 'support'
    if re.search(r'запис|консульт|пробн|поступить|при[её]м|обучени|заявк', text, re.I):
        return 'enrollment'
    return default


def review_ancestor(node):
    for parent in [node, *list(node.parents)[:5]]:
        attrs = ' '.join(parent.get('class', [])) + ' ' + str(parent.get('id', ''))
        if REVIEW_CONTEXT.search(attrs):
            return True
    return False


def company_social(anchor, url):
    """Company social link in navigation/footer, not an author's cited profile."""
    if review_ancestor(anchor):
        return False
    p = urlparse(url)
    if p.path.startswith(('/in/', '/id')):
        return False
    label = compact(anchor.get_text(' ', strip=True))
    context = nearest_context(anchor)
    if re.search(r'наш(?:и|\s+)? (?:канал|соцсет|telegram|телеграм|вконтакте)|мы в (?:telegram|телеграм|vk|вконтакте)', context, re.I):
        return True
    for parent in [anchor, *list(anchor.parents)[:6]]:
        attrs = ' '.join(parent.get('class', [])) + ' ' + str(parent.get('id', ''))
        if parent.name in ('footer', 'header', 'nav') or SOCIAL_CLASS.search(attrs):
            if not re.search(r'author|speaker|teacher|наставник|преподавател', attrs, re.I):
                return True
    return bool(re.fullmatch(r'(?:telegram|телеграм|tg|vk|вконтакте|linkedin)', label, re.I))


def exact_contact(url, anchor, page_url, text, default='general'):
    context = nearest_context(anchor)
    if (context and context not in text) or review_ancestor(anchor):
        return None
    display = compact(anchor.get_text(' ', strip=True))
    address = unquote(url.split(':', 1)[-1].split('?', 1)[0]) if url.startswith(('mailto:', 'tel:')) else url
    # Label is only the link's own text, never a guessed person's name.
    label = display if display and len(display) <= 100 else address
    item = {'url': url, 'label': label, 'purpose': purpose(context, address, default),
            'context': context, 'sourceURL': page_url, 'basis': 'officialWebsite',
            'affiliation': 'publishedWorkContact', 'personIdentityVerified': False,
            'linkEvidence': {'href': anchor.get('href', ''), 'anchorText': display, 'sourceURL': page_url}}
    visible_addresses = [x.lower().rstrip('.') for x in EMAIL.findall(display)]
    if not visible_addresses:
        visible_addresses = [x.lower().rstrip('.') for x in EMAIL.findall(context)]
    if url.startswith('mailto:') and visible_addresses and address.lower() not in visible_addresses:
        item.update(label=address, purpose='unknown', addressTextMismatch=True,
                    attributionNote='Адрес href отличается от видимого адреса ссылки; назначение не переносится между ними.')
    return item


def extract_page(company, page):
    soup = BeautifulSoup(page['html'], 'lxml')
    title = compact(soup.title.get_text(' ', strip=True)) if soup.title else company['name']
    job_postings = []
    published = None
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            stack = [json.loads(script.get_text())]
        except (ValueError, TypeError):
            continue
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                types = item.get('@type', [])
                types = [types] if isinstance(types, str) else types
                if 'JobPosting' in types:
                    job_postings.append({'sourceURL': page['url'], 'retrievedAt': page['retrievedAt'], 'data': item})
                if any(x in types for x in ('Article', 'NewsArticle', 'BlogPosting')):
                    published = item.get('datePublished') or published
                if isinstance(item.get('@graph'), list):
                    stack.extend(item['@graph'])
                if isinstance(item.get('mainEntity'), (dict, list)):
                    stack.append(item['mainEntity'])
    meta = soup.select_one('meta[property="article:published_time"]')
    if meta:
        published = meta.get('content') or published
    for tag in soup(['script', 'style', 'noscript', 'svg', 'iframe', 'canvas']):
        tag.decompose()
    body = soup.body or soup
    text = compact(body.get_text(' ', strip=True))
    if (BLOCK_TEXT.search(title) or (len(text) < 2200 and BLOCK_TEXT.search(text))):
        return {'error': 'accessChallenge', 'title': title}
    if len(text) < 35:
        return {'error': 'noReadableText', 'title': title}
    doc = {'id': 'public:' + digest(company['id'] + page['url']), 'companyID': company['id'],
           'url': page['url'], 'title': title, 'text': text[:240000], 'kind': 'website',
           'provider': 'Официальный сайт · публичное чтение', 'retrievedAt': page['retrievedAt'],
           'publishedAt': published if isinstance(published, str) else None,
           'quality': 'fullText' if len(text) <= 240000 and not page.get('responseTruncated') else 'partialText',
           'official': True, 'receiptID': page['receiptID'], 'htmlSHA256': page.get('htmlSHA256')}
    text = doc['text']
    contacts, channels, links = {}, {}, {}
    for anchor in body.select('a[href]'):
        raw = html.unescape(anchor.get('href', '').strip())
        label = compact(anchor.get_text(' ', strip=True))
        if raw.lower().startswith('mailto:'):
            href_addresses = EMAIL.findall(unquote(raw[7:].split('?', 1)[0]))
            for address in href_addresses:
                if re.search(r'example\.(?:com|ru)|test@|noreply@|no-reply@', address, re.I):
                    continue
                item = exact_contact('mailto:' + address.lower(), anchor, page['url'], text)
                if item:
                    contacts[(item['url'].lower(), item['purpose'])] = item
            # Some sites display an HR mailbox but accidentally link a general
            # mailbox. Preserve both published facts, explicitly separated.
            for address in EMAIL.findall(label):
                if address.lower() in {x.lower() for x in href_addresses}:
                    continue
                context = nearest_context(anchor)
                if not context or context not in text or review_ancestor(anchor):
                    continue
                item = {'url': 'mailto:' + address.lower(), 'label': address,
                        'purpose': purpose(context, address, 'general'), 'context': context,
                        'sourceURL': page['url'], 'basis': 'officialWebsite',
                        'affiliation': 'publishedWorkContact', 'personIdentityVerified': False,
                        'addressTextMismatch': True,
                        'linkEvidence': {'method': 'visibleText', 'sourceURL': page['url'],
                                         'anchorText': label, 'conflictingHref': raw}}
                contacts[(item['url'], item['purpose'])] = item
            continue
        if raw.lower().startswith('tel:'):
            digits = re.sub(r'[^\d+]', '', unquote(raw[4:].split('?', 1)[0]))
            if 7 <= len(re.sub(r'\D', '', digits)) <= 15:
                item = exact_contact('tel:' + digits, anchor, page['url'], text)
                if item:
                    contacts[(item['url'], item['purpose'])] = item
            continue
        url = clean_url(urljoin(page['url'], raw))
        if not url:
            continue
        ctx = nearest_context(anchor)
        cat, score = category(url, label)
        sk = social_kind(url)
        is_social = bool(sk and company_social(anchor, url))
        if cat != 'other' or sk or same_site(url, company['website']):
            links[url] = {'url': url, 'sourceURL': page['url'], 'linkText': label, 'context': ctx,
                          'category': cat, 'priority': score, 'receiptID': page['receiptID'],
                          'sameSite': same_site(url, company['website']),
                          'affiliation': 'officiallyLinked' if is_social else 'publishedReference'}
        if sk and not review_ancestor(anchor):
            if sk == 'telegram' and is_social:
                tg = telegram_url(url)
                account_type = 'bot' if tg.lower().endswith('bot') else 'chat' if '/+' in tg else 'channel' if re.search(r'канал|channel', ctx + ' ' + label, re.I) else 'account'
                channels[tg.lower()] = {'url': tg, 'sourceURL': page['url'], 'context': ctx or label,
                                       'linkEvidence': {'href': raw, 'anchorText': label, 'sourceURL': page['url']},
                                       'basis': 'officialWebsite', 'purpose': account_type if account_type != 'account' else 'unknown',
                                       'accountType': account_type, 'status': 'linkedFromOfficialWebsite',
                                       'affiliation': 'officiallyLinked', 'retrievedAt': page['retrievedAt'],
                                       'receiptID': page['receiptID']}
            # Cited social URLs stay in outboundLinks. Contacts need a company social
            # surface or a short explicit work label, not an article/review mention.
            explicit_work = purpose(ctx, url) in ('hiring', 'cooperation', 'support', 'enrollment')
            if is_social or explicit_work:
                default = (account_type if sk == 'telegram' and is_social and account_type != 'account' else 'unknown' if sk == 'telegram' else 'general')
                item = exact_contact(telegram_url(url) or url, anchor, page['url'], text, default)
                if item:
                    if is_social:
                        item['affiliation'] = 'officiallyLinked'
                    contacts[(item['url'].lower(), item['purpose'])] = item
    # Plain-text emails without mailto remain useful. Each quote is confined to
    # its own text node/line; preceding names and titles never become ownership.
    for string in body.find_all(string=EMAIL):
        parent = string.parent
        if parent is None or parent.name == 'a' or review_ancestor(parent):
            continue
        line = compact(str(string))
        if len(line) > 500 or line not in text:
            continue
        addresses = EMAIL.findall(line)
        for address in addresses:
            if re.search(r'example\.(?:com|ru)|test@|noreply@|no-reply@', address, re.I):
                continue
            url = 'mailto:' + address.lower().rstrip('.')
            context = line
            if len(addresses) == 1:
                item = {'url': url, 'label': address, 'purpose': purpose(context, address, 'general'),
                        'context': context, 'sourceURL': page['url'], 'basis': 'officialWebsite',
                        'affiliation': 'publishedWorkContact', 'personIdentityVerified': False,
                        'linkEvidence': {'method': 'visibleText', 'sourceURL': page['url']}}
                contacts.setdefault((url, item['purpose']), item)
    return {'document': doc, 'contacts': list(contacts.values()), 'channels': list(channels.values()),
            'outboundLinks': list(links.values()), 'jobPostings': job_postings}


class Fetcher:
    def __init__(self, out):
        self.out = out
        self.raw_size = sum(p.stat().st_size for p in (out / 'pages').rglob('*') if p.is_file()) if (out / 'pages').exists() else 0

    def load(self, path):
        value = json.loads(path.read_text())
        if value.get('htmlPath') and not value.get('html'):
            html_path = (self.out / value['htmlPath']).resolve()
            if self.out.resolve() not in html_path.parents:
                raise ValueError('receipt path outside staged output')
            value['html'] = gzip.decompress(html_path.read_bytes()).decode('utf-8')
        return value

    def fetch(self, url, session):
        key = digest(url)
        cache = self.out / 'pages' / (key + '.json')
        if cache.exists():
            return self.load(cache)
        value = {'requestedURL': url, 'retrievedAt': now(), 'receiptID': key}
        with IO_LOCK:
            if shutil.disk_usage(self.out).free < MIN_FREE or self.raw_size >= RAW_BUDGET:
                value['error'] = 'storageBudgetReached'
                return value
        with DOMAIN_LOCK:
            delay = max(0.0, DOMAIN_TIMES.get(site_domain(url), 0) + 0.4 - time.monotonic())
            DOMAIN_TIMES[site_domain(url)] = time.monotonic() + delay
        if delay:
            time.sleep(delay)
        try:
            with session.get(url, timeout=(5, 11), stream=True, allow_redirects=True) as response:
                value.update(url=response.url, status=response.status_code, contentType=response.headers.get('Content-Type', ''))
                if response.status_code != 200:
                    value['error'] = 'http' + str(response.status_code)
                elif 'html' not in value['contentType'].lower():
                    value['error'] = 'nonHTML'
                else:
                    raw = bytearray()
                    for part in response.iter_content(65536):
                        raw.extend(part)
                        if len(raw) > MAX_RESPONSE:
                            raw = raw[:MAX_RESPONSE]
                            value['responseTruncated'] = True
                            break
                    encoding = response.encoding
                    if not encoding or encoding.lower() in ('iso-8859-1', 'ascii'):
                        encoding = 'utf-8'
                        charset = re.search(br'<meta[^>]+charset\s*=\s*["\']?([a-zA-Z0-9_-]+)', raw[:8192], re.I)
                        if charset:
                            encoding = charset.group(1).decode('ascii')
                    try:
                        text = raw.decode(encoding or 'utf-8', errors='replace')
                    except LookupError:
                        text = raw.decode('utf-8', errors='replace')
                    value['html'] = text
                    value['htmlSHA256'] = hashlib.sha256(text.encode()).hexdigest()
                    value['htmlBytes'] = len(text.encode())
        except requests.RequestException as error:
            value['error'] = type(error).__name__
        persisted = dict(value)
        html_bytes = value.get('html', '').encode()
        with IO_LOCK:
            if len(html_bytes) > 1_500_000:
                relative = 'pages/html/' + key + '.html.gz'
                target = self.out / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(gzip.compress(html_bytes))
                persisted.pop('html')
                persisted['htmlPath'] = relative
                value['htmlPath'] = relative
                self.raw_size += target.stat().st_size
            save(cache, persisted)
            self.raw_size += cache.stat().st_size
        return value


def validate_record(record):
    errors = []
    documents = {x['url']: x for x in record['documents']}
    for doc in record['documents']:
        if doc['companyID'] != record['companyID'] or not doc['text']:
            errors.append('invalidDocument:' + doc['id'])
    for item in record['contacts']:
        doc = documents.get(item['sourceURL'])
        href_proof = item.get('linkEvidence', {}).get('href')
        if not doc or (not item['context'] and not href_proof) or item['context'] not in doc['text']:
            errors.append('contactQuoteMissing:' + item['url'])
        if item['purpose'] not in ('cooperation', 'hiring', 'support', 'enrollment', 'general', 'channel', 'chat', 'bot', 'unknown'):
            errors.append('invalidPurpose:' + item['url'])
    return errors


def normalized_date(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            return dt.date.fromisoformat(value).isoformat()
        normalized = re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2', value.replace('Z', '+00:00'))
        parsed = dt.datetime.fromisoformat(normalized)
        # Preserve the native reader's supported naive format, without inventing
        # a time zone not supplied by the source.
        return parsed.isoformat() if parsed.tzinfo else parsed.strftime('%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return None


def telephone_parts(raw, label=''):
    """Keep extension separate; do not silently concatenate it into E.164."""
    value = unquote(raw[4:] if raw.lower().startswith('tel:') else raw).split('?', 1)[0]
    split = re.search(r'(?:;ext=|[,;pPwWxX])\s*(\d{1,8})', value)
    extension = split.group(1) if split else None
    main = value[:split.start()] if split else value
    number = re.sub(r'[^+\d]', '', main)
    if number.startswith('+7') and len(re.sub(r'\D', '', number)) > 11:
        visible = re.search(r'((?:\+7|8)[\s(]*\d{3}[\s)\-]*\d{3}[\s-]*\d{2}[\s-]*\d{2})\s*(?:,\s*|\(?\s*(?:доб(?:авочный)?\.?|ext\.?)\s*)(\d{1,8})', label, re.I)
        if visible:
            number = re.sub(r'[^+\d]', '', visible.group(1))
            extension = visible.group(2)
    digits = re.sub(r'\D', '', number)
    if not 7 <= len(digits) <= 15 or (number.startswith('+7') and len(digits) != 11):
        return None, extension, 'invalidTelephoneLength'
    # If the visible label explicitly shows a different main phone, preserve the
    # conflicting raw data for review instead of deciding which number is right.
    shown = re.search(r'(?<![\d+])(?:\+7|8)[\s(]*\d{3}[\s)\-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}', label)
    if shown:
        shown_digits = re.sub(r'\D', '', shown.group())
        canonical = ('7' + digits[1:]) if len(digits) == 11 and digits.startswith('8') else digits
        shown_digits = ('7' + shown_digits[1:]) if len(shown_digits) == 11 and shown_digits.startswith('8') else shown_digits
        if shown_digits != canonical:
            return None, extension, 'telephoneHrefTextMismatch'
    return 'tel:' + number, extension, None


def whatsapp_route(raw):
    parsed = urlparse(raw)
    address_host = (parsed.hostname or '').lower().removeprefix('www.')
    if address_host == 'wa.me':
        path = parsed.path.strip('/')
        if re.fullmatch(r'\+?[\d\s()-]+', path):
            digits = re.sub(r'\D', '', path)
            return 'https://wa.me/' + digits if 7 <= len(digits) <= 15 else None
        # Business deep-link tokens are opaque and case-sensitive, not phones.
        return 'https://wa.me/' + path if path else None
    if address_host == 'api.whatsapp.com':
        phone = dict(parse_qsl(parsed.query)).get('phone', '')
        if not re.fullmatch(r'\+?[\d\s()-]+', phone):
            return None
        digits = re.sub(r'\D', '', phone)
        return 'https://wa.me/' + digits if 7 <= len(digits) <= 15 else None
    return None


def repair_contact_routes(record, company):
    """Final semantic normalization; never modifies immutable HTML receipts."""
    kept = []
    quarantine = []
    candidates = list(record['contacts'])
    for old in record.get('quarantinedContacts', []):
        if old.get('quarantineReason') in ('telephoneHrefTextMismatch', 'invalidTelephoneLength'):
            retry = dict(old)
            retry.pop('quarantineReason', None)
            candidates.append(retry)
        else:
            quarantine.append(old)
    company_host = host(company['website'])
    company_domain = site_domain(company['website'])
    unit_subdomain = company_host != company_domain
    sibling_sources = set()
    if unit_subdomain:
        for doc in record['documents']:
            source_host = host(doc['url'])
            if source_host != company_host and not source_host.endswith('.' + company_host):
                doc['organizationScope'] = 'parentOrSiblingUnitUnverified'
                doc['official'] = False
                sibling_sources.add(doc['url'])
    for contact in candidates:
        url = contact['url']
        raw_href = contact.get('linkEvidence', {}).get('href', url)
        if contact['sourceURL'] in sibling_sources:
            contact['organizationScope'] = 'parentOrSiblingUnitUnverified'
            contact['quarantineReason'] = 'Parent or sibling domain; this route is not verified for the selected unit.'
            quarantine.append(contact)
            continue
        if url.startswith('tel:'):
            route, extension, error = telephone_parts(raw_href, contact.get('label', ''))
            if error:
                contact['quarantineReason'] = error
                quarantine.append(contact)
                continue
            contact['url'] = route
            if extension:
                contact['extension'] = extension
                contact['rawHref'] = raw_href
        else:
            parsed = urlparse(url)
            address_host = (parsed.hostname or '').lower().removeprefix('www.')
            if address_host in ('wa.me', 'api.whatsapp.com'):
                route = whatsapp_route(urljoin(contact['sourceURL'], raw_href))
                if not route:
                    contact['quarantineReason'] = 'unaddressedWhatsAppLink'
                    quarantine.append(contact)
                    continue
                contact['url'] = route
                contact['rawHref'] = raw_href
            feed = address_host in ('vk.com', 'vk.ru', 'vkontakte.ru', 'youtube.com', 'youtu.be', 'instagram.com', 'rutube.ru', 'dzen.ru')
            feed = feed or (address_host.endswith('linkedin.com') and parsed.path.startswith('/company/'))
            if feed and contact['purpose'] in ('general', 'unknown'):
                contact['purpose'] = 'channel'
                contact['routeCategory'] = 'publicSocialPage'
            if address_host in ('t.me', 'telegram.me') and re.fullmatch(r'/\+\d{7,15}', parsed.path):
                contact['purpose'] = 'unknown'
                contact['routeCategory'] = 'publicTelegramPhoneRoute'
        kept.append(contact)
    record['contacts'] = kept
    extension_rows = list(record.get('phoneExtensions', [])) + [
        {'url': x['url'], 'extension': x['extension'], 'sourceURL': x['sourceURL'], 'context': x['context'], 'rawHref': x.get('rawHref')}
        for x in kept if x.get('extension')]
    record['phoneExtensions'] = list({(x['url'], x['extension'], x['sourceURL']): x for x in extension_rows}.values())
    for channel in record.get('channels', []):
        if channel['sourceURL'] in sibling_sources:
            channel['organizationScope'] = 'parentOrSiblingUnitUnverified'
            channel['basis'] = 'parentOrganizationWebsite'
    record['quarantinedContacts'] = list({(x['url'], x['sourceURL'], x['quarantineReason']): x for x in quarantine}.values())
    if sibling_sources:
        note = 'Материалы и контакты родительского или соседнего подразделения не подтверждают команду выбранной организации. Такие контакты сохранены отдельно для проверки.'
        if note not in record['notes']:
            record['notes'].append(note)
    return record


def review_existing(companies, out):
    """Re-extract local contact context from receipts, without another request.

    Originals stay immutable for concurrent history collectors. The reviewed
    PublicResearch records are the recommended final import layer.
    """
    by_id = {c['id']: c for c in companies}
    fetcher = Fetcher(out)
    reviewed = out / 'reviewed'
    changes, failures = [], []
    for path in sorted((out / 'companies').glob('*.json')):
        original_bytes = path.read_bytes()
        record = json.loads(original_bytes)
        company = by_id.get(record['companyID'])
        if company is None:
            continue
        contacts = {}
        previous = {(x['url'].lower(), x['sourceURL']): x['purpose'] for x in record['contacts']}
        for doc in record['documents']:
            receipt = out / 'pages' / (doc['receiptID'] + '.json')
            page = fetcher.load(receipt)
            if hashlib.sha256(page['html'].encode()).hexdigest() != page.get('htmlSHA256'):
                failures.append({'companyID': record['companyID'], 'receiptID': doc['receiptID'], 'error': 'receiptHashMismatch'})
                continue
            extracted = extract_page(company, page)
            if extracted.get('error') or extracted['document']['text'] != doc['text']:
                failures.append({'companyID': record['companyID'], 'receiptID': doc['receiptID'], 'error': 'documentTextChanged'})
                continue
            raw_date = doc.get('publishedAt')
            if raw_date:
                doc['rawPublishedAt'] = raw_date
                doc['publishedAt'] = normalized_date(raw_date)
            for item in extracted['contacts']:
                item['receiptID'] = doc['receiptID']
                if record.get('sharedOfficialDomain'):
                    item['organizationScope'] = 'sharedDomainNotUnitVerified'
                contacts[(item['url'].lower(), item['purpose'], item['sourceURL'])] = item
        record['contacts'] = list(contacts.values())
        record = repair_contact_routes(record, company)
        record['notes'].append('Contact labels reviewed from the local HTML receipt. Navigation and course-career marketing do not establish an HR role. Href/text mismatches remain explicit.')
        record['collector'] = 'segment-public-research/v3-reviewed'
        record['reviewedAt'] = now()
        record['originalRecordSHA256'] = hashlib.sha256(original_bytes).hexdigest()
        record['validationErrors'] = validate_record(record)
        record['statistics']['uniqueContacts'] = len({x['url'].lower() for x in record['contacts']})
        record['statistics']['contactEvidenceRows'] = len(record['contacts'])
        after = {(x['url'].lower(), x['sourceURL']): x['purpose'] for x in record['contacts']}
        changes.append({'companyID': record['companyID'], 'previousRoutes': len({k[0] for k in previous}),
                        'reviewedRoutes': record['statistics']['uniqueContacts'],
                        'changedPurposeRows': sum(after[k] != value for k, value in previous.items() if k in after),
                        'missingRows': sum(k not in after for k in previous),
                        'hrefOnlyRows': sum(not x['context'] for x in record['contacts']),
                        'mismatchRows': sum(bool(x.get('addressTextMismatch')) for x in record['contacts'])})
        if record['validationErrors']:
            failures.append({'companyID': record['companyID'], 'error': record['validationErrors']})
        else:
            save(reviewed / 'companies' / path.name, record)
    save(reviewed / 'review.json', {'reviewedAt': now(), 'changes': changes, 'errors': failures,
                                   'httpRequests': 0, 'originalsModified': False})
    summary = summarize(reviewed)
    if failures:
        raise SystemExit(1)
    return summary


def crawl(company, args, fetcher, source_rows):
    dest = args.output_dir / 'companies' / (company['id'] + '.json')
    if dest.exists():
        return json.loads(dest.read_text())
    record = {'version': 1, 'companyID': company['id'], 'companyName': company['name'], 'searchedAt': now(),
              'documents': [], 'channels': [], 'contacts': [], 'checks': [], 'notes': [],
              'outboundLinks': [], 'jobPostings': [], 'collector': 'segment-public-research/v2',
              'segment': company.get('segment'), 'maxPages': args.max_pages,
              'sharedOfficialDomain': args.domain_counts.get(site_domain(company['website']), 0) > 1}
    start = clean_url(company['website'])
    if not start:
        record['notes'] = ['Invalid website URL; no source read.']
        save(dest, record)
        return record
    queue, queued, seen, counter = [], set(), set(), 0

    def enqueue(url, priority):
        nonlocal counter
        url = clean_url(url)
        if url and url not in queued and not BAD_PATH.search(urlparse(url).path) and len(url) < 1500:
            if not same_site(url, start):
                return
            queued.add(url)
            counter += 1
            heapq.heappush(queue, (-priority, counter, url))

    enqueue(start, 1000)
    base = urlparse(start)
    enqueue(urlunparse((base.scheme, base.netloc, '/', '', '', '')), 900)
    for row in source_rows.get(company['id'], []):
        url = row.get('url', '')
        cat, priority = category(url, row.get('title', ''))
        if priority >= 40:
            enqueue(url, priority)
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0 (compatible; CompanyResearch/1.0; public company contacts)', 'Accept': 'text/html,application/xhtml+xml'})
    documents, contacts, channels, links, jobs = {}, {}, {}, {}, {}
    attempts = 0
    root_failed = False
    while queue and len(documents) < args.max_pages and attempts < args.max_pages + 4:
        _, _, url = heapq.heappop(queue)
        if url in seen:
            continue
        seen.add(url)
        attempts += 1
        page = fetcher.fetch(url, session)
        check = {'url': url, 'status': page.get('error', 'read'), 'receiptID': page['receiptID']}
        record['checks'].append(check)
        if 'html' not in page:
            if attempts == 1:
                root_failed = True
            if page.get('error') in ('http403', 'http429', 'storageBudgetReached'):
                # Do not cycle alternate paths around a site's access denial.
                break
            continue
        if not same_site(page['url'], start):
            check['status'] = 'externalRedirectUnverified'
            check['redirectURL'] = page['url']
            continue
        if page['url'] in documents:
            check['status'] = 'duplicateFinalURL'
            continue
        extracted = extract_page(company, page)
        if extracted.get('error'):
            check['status'] = extracted['error']
            if extracted['error'] == 'accessChallenge':
                break
            continue
        doc = extracted['document']
        # Different URLs with identical page content are one record, not growth.
        text_hash = hashlib.sha256(doc['text'].encode()).hexdigest()
        if any(x.get('textSHA256') == text_hash for x in documents.values()):
            check['status'] = 'duplicateText'
        else:
            doc['textSHA256'] = text_hash
            documents[page['url']] = doc
            for item in extracted['contacts']:
                item['receiptID'] = page['receiptID']
                if record['sharedOfficialDomain']:
                    item['organizationScope'] = 'sharedDomainNotUnitVerified'
                contacts.setdefault((item['url'].lower(), item['purpose'], item['sourceURL']), item)
            for item in extracted['channels']:
                channels.setdefault(item['url'].lower(), item)
            for item in extracted['jobPostings']:
                jobs[digest(json.dumps(item['data'], sort_keys=True))] = item
        for item in extracted['outboundLinks']:
            links.setdefault((item['url'], item['sourceURL']), item)
            if item['priority'] >= 40:
                enqueue(item['url'], item['priority'])
    session.close()
    record.update(documents=list(documents.values()), contacts=list(contacts.values()),
                  channels=list(channels.values()), outboundLinks=list(links.values()), jobPostings=list(jobs.values()))
    record['notes'] = ['Collected public website pages only. A published address is a route, not verified personal identity or decision-making authority.',
                       'Publication dates are unknown unless explicitly supplied by the page. Missing results do not establish absence of hiring or contacts.']
    if len(documents) >= args.max_pages:
        record['notes'].append('Page budget reached; collection is partial.')
    if record['sharedOfficialDomain']:
        record['notes'].append('Домен используется несколькими компаниями или подразделениями выборки. Общие контакты сайта не подтверждают принадлежность именно этому подразделению.')
    if root_failed:
        record['notes'].append('Homepage unavailable during this run; any saved pages were separately readable.')
    errors = validate_record(record)
    record['validationErrors'] = errors
    record['statistics'] = {'attemptedPages': attempts, 'savedDocuments': len(documents),
                            'uniqueContacts': len({x['url'].lower() for x in contacts.values()}),
                            'contactEvidenceRows': len(contacts), 'officialTelegramChannels': len(channels),
                            'careerLinks': len({x['url'] for x in links.values() if x['category'] in ('career', 'vacancy', 'employer')}),
                            'jobPostings': len(jobs), 'finishedAt': now()}
    if errors:
        save(args.output_dir / 'invalid' / dest.name, record)
        raise ValueError(company['id'] + ': ' + ', '.join(errors))
    save(dest, record)
    with PRINT_LOCK:
        print(json.dumps({'companyID': company['id'], 'name': company['name'], **record['statistics']}, ensure_ascii=False), flush=True)
    return record


def load_sources(directory, ids):
    rows = {}
    if not directory.exists():
        return rows
    for path in directory.glob('*.json'):
        try:
            value = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        for row in value.get('results', []):
            cid = row.get('companyID')
            if cid in ids and SOURCE_TEXT.search(row.get('url', '') + ' ' + row.get('title', '')):
                rows.setdefault(cid, []).append({k: row.get(k) for k in ('url', 'title')})
    return rows


def summarize(out, ids=None):
    records = []
    for path in (out / 'companies').glob('*.json'):
        value = json.loads(path.read_text())
        if ids is None or value['companyID'] in ids:
            records.append(value)
    counts = Counter()
    failures = Counter()
    for record in records:
        counts['companies'] += 1
        counts['companiesWithDocuments'] += bool(record['documents'])
        counts['documents'] += len(record['documents'])
        counts['fullTextDocuments'] += sum(x['quality'] == 'fullText' for x in record['documents'])
        counts['uniqueContactRoutes'] += len({x['url'].lower() for x in record['contacts']})
        counts['contactEvidenceRows'] += len(record['contacts'])
        counts['officialTelegramChannels'] += len(record['channels'])
        counts['jobPostings'] += len(record.get('jobPostings', []))
        counts['validationErrors'] += len(record.get('validationErrors', []))
        for check in record['checks']:
            if check['status'] not in ('read', 'duplicateText', 'duplicateFinalURL'):
                failures[check['status']] += 1
    value = {'generatedAt': now(), **counts, 'checkFailures': dict(failures),
             'rawBytes': sum(p.stat().st_size for p in (out / 'pages').rglob('*') if p.is_file())}
    save(out / 'summary.json', value)
    print(json.dumps(value, ensure_ascii=False), flush=True)
    return value


def selftest():
    company = {'id': 'test', 'name': 'Test School', 'website': 'https://example.org'}
    content = '''<html><head><title>Company contacts</title></head><body>
    <h1>Company contacts and careers</h1><div class="team-card"><span>HR Анна</span><a href="mailto:anna@example.org">anna@example.org</a></div>
    <div><span>Общие вопросы</span><a href="mailto:info@example.org">info@example.org</a></div>
    <section class="student-testimonials"><h2>Отзыв ученика</h2><a href="mailto:child@example.org">child@example.org</a><a href="https://t.me/child_user">ученик</a></section>
    <article><p>Статья гостевого автора <a href="https://t.me/random_author">Автор</a></p></article>
    <footer><div class="social"><a href="https://t.me/example_school">Telegram</a></div><div><span>Для сотрудничества</span><a href="mailto:partnership@example.org">partnership@example.org</a></div><a href="tel:+74951234567">+7 495 123-45-67</a><a href="https://careers.example.org/">Карьера</a></footer>
    </body></html>'''
    page = {'html': content, 'url': company['website'], 'retrievedAt': now(), 'receiptID': 'test'}
    output = extract_page(company, page)
    by_url = {x['url']: x for x in output['contacts']}
    assert 'mailto:child@example.org' not in by_url
    assert 'https://t.me/random_author' not in {x['url'] for x in output['channels']}
    assert 'https://t.me/example_school' in {x['url'] for x in output['channels']}
    assert by_url['mailto:info@example.org']['purpose'] == 'general'
    assert by_url['mailto:partnership@example.org']['purpose'] == 'cooperation'
    assert by_url['mailto:anna@example.org']['label'] == 'anna@example.org'
    assert 'tel:+74951234567' in by_url
    assert same_site('https://careers.example.org', company['website'])
    assert not same_site('https://example.org.attacker.net', company['website'])
    assert not telegram_url('https://t.me/example_school/32')
    record = {'companyID': company['id'], 'documents': [output['document']], 'contacts': output['contacts']}
    assert not validate_record(record)
    bad = dict(record); bad['contacts'] = [dict(output['contacts'][0], context='made-up quote')]
    assert validate_record(bad)
    assert purpose('Карьерный помощник: тест на уровень аналитики', default='general') == 'general'
    assert purpose('Карьерный рост выпускников нашего курса', default='general') == 'general'
    assert purpose('Вместе строим современный HR-портрет России', default='general') == 'general'
    assert purpose('Мы опубликуем вашу вакансию', default='general') == 'cooperation'
    assert normalized_date('2019-12-19T17:01:40+0000') == '2019-12-19T17:01:40+00:00'
    assert normalized_date('yesterday') is None
    assert telephone_parts('tel:+74956467517p6279', '+7 (495) 646-75-17, 6279') == ('tel:+74956467517', '6279', None)
    assert telephone_parts('tel:+74956378273,3', 'доб. 3') == ('tel:+74956378273', '3', None)
    assert telephone_parts('tel:+78123238462,7395', '+7 (812) 323-84-40')[2] == 'telephoneHrefTextMismatch'
    assert telephone_parts('tel:+749564675176249', '+7 (495) 646-75-17 (доб. 6249)') == ('tel:+74956467517', '6249', None)
    assert telephone_parts('tel:+380680777555', '+38 (068) 077 75 55') == ('tel:+380680777555', None, None)
    assert telephone_parts('tel:+380689571494', '+380689571494') == ('tel:+380689571494', None, None)
    assert whatsapp_route('https://wa.me/message/NHUSGGPLVUZMB1') == 'https://wa.me/message/NHUSGGPLVUZMB1'
    assert whatsapp_route('https://wa.me/+79123334455?text=hello') == 'https://wa.me/79123334455'
    assert whatsapp_route('https://api.whatsapp.com/send?phone=%2B79123334455&text=hello') == 'https://wa.me/79123334455'
    assert whatsapp_route('https://wa.me/41') is None
    print(json.dumps({'selfTest': 'passed', 'checks': 28}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--company-list', type=Path, default=ROOT / 'output/segments-2026-10-14/companies.json')
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--source-runs-dir', type=Path, default=APP / 'source-runs')
    parser.add_argument('--max-pages', type=int, default=8)
    parser.add_argument('--workers', type=int, default=5)
    parser.add_argument('--company-id', action='append')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--report-only', action='store_true')
    parser.add_argument('--review-existing', action='store_true', help='Re-extract contact context locally into reviewed/companies; no HTTP')
    args = parser.parse_args()
    if args.self_test:
        selftest(); return
    if not 1 <= args.max_pages <= 16 or not 1 <= args.workers <= 8:
        parser.error('max-pages must be 1..16 and workers 1..8')
    args.output_dir = args.output_dir.resolve()
    if args.output_dir == APP.resolve() or APP.resolve() in args.output_dir.parents:
        parser.error('staged output cannot be the live application directory')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw = json.loads(args.company_list.read_text())
    companies = raw.get('companies', []) if isinstance(raw, dict) else raw
    companies = [dict(c, id=c.get('id') or c.get('companyId') or c.get('companyID')) for c in companies]
    args.domain_counts = Counter(site_domain(c['website']) for c in companies)
    if args.company_id:
        companies = [c for c in companies if c['id'] in set(args.company_id)]
    if args.limit:
        companies = companies[:args.limit]
    ids = {c['id'] for c in companies}
    if args.report_only:
        summarize(args.output_dir, ids); return
    if args.review_existing:
        review_existing(companies, args.output_dir); return
    source_rows = load_sources(args.source_runs_dir, ids)
    fetcher = Fetcher(args.output_dir)
    failures = []
    save(args.output_dir / 'run.json', {'startedAt': now(), 'companyList': str(args.company_list.resolve()),
          'companies': len(companies), 'maxPages': args.max_pages, 'workers': args.workers,
          'rawBudgetBytes': RAW_BUDGET, 'minFreeBytes': MIN_FREE, 'liveWrites': False})
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(crawl, c, args, fetcher, source_rows): c for c in companies}
        for future in concurrent.futures.as_completed(futures):
            c = futures[future]
            try:
                future.result()
            except Exception as error:
                failures.append({'companyID': c['id'], 'error': type(error).__name__, 'detail': str(error)[:500]})
                with PRINT_LOCK:
                    print(json.dumps(failures[-1], ensure_ascii=False), flush=True)
    save(args.output_dir / 'errors.json', failures)
    summarize(args.output_dir, ids)
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
