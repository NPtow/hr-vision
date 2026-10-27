#!/usr/bin/env python3
"""Stage public Telegram histories from company-owned, official-site-linked channels.

Only unauthenticated public HTTPS GETs to t.me are permitted. No paid APIs, user
sessions, messages, private chats, or app writes. SourceHistoryArchive-compatible
company files retain attribution, exact texts, dates, public business contacts,
and page-bound coverage. Gzip raw responses are immutable within this run.
"""
import argparse
import concurrent.futures
import datetime as dt
import gzip
import hashlib
import json
import re
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN = ROOT / 'output/segments-2026-10-14'
POLICY_VERSION = '2026-10-14.2'
USERNAME = re.compile(r'^[A-Za-z][A-Za-z0-9_]{3,31}$')
EXCLUDED = {'share', 'addstickers', 'addemoji', 'proxy', 'socks', 'login', 'iv', 'joinchat', 'addlist', 'boost', 'c', 's'}
PERSON_CONTEXT = re.compile(r'(?:личный|авторский)\s+(?:блог|канал)|канал\s+(?:преподавател|учител|основател|ведущ|эксперт)|(?:блог|канал)\s+автора', re.I)
PURPOSES = [
    ('cooperation', re.compile(r'сотруднич|партн[её]рств|реклам[аеыу]|для\s+сми|пресс[а-]?служб|cooperat|partnership', re.I)),
    ('hiring', re.compile(r'ваканси|резюме|отклик|рекрутер|работ[ауы]\s+у\s+нас|карьер|\bhr\b|в\s+нашу\s+команд', re.I)),
    ('support', re.compile(r'тех(?:ническ[а-я]+|поддержк)|support|служб[ау]\s+(?:заботы|поддержки)|поддержк[аи]\s*[:—–-]|(?:вопрос|пишит|обращ)[\s\S]{0,60}поддержк', re.I)),
    ('enrollment', re.compile(r'запис[ьиы]|записа[тн]|консультац|по\s+обучению|при[её]мн|подбор\s+(?:курс|программ)|оставить\s+заявку|вопрос[ыа][\s\S]{0,30}(?:курс|обучени)', re.I)),
    ('general', re.compile(r'связ[ьа]|контакт|\b(?:на)?пишите\b|связаться\s+с\s+нами|обратн[а-я]+\s+связ|любым\s+вопросам', re.I)),
]
EMAIL = re.compile(r'(?<![\w.+-])[A-Z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9-]*[A-Z0-9])?(?:\.[A-Z0-9](?:[A-Z0-9-]*[A-Z0-9])?)+', re.I)
PHONE = re.compile(r'(?<!\d)(?:\+7|8)[\s(.-]*\d{3}[\s).-]*\d{3}[\s.-]*\d{2}[\s.-]*\d{2}(?!\d)')
ATNAME = re.compile(r'(?<![\w@])@([A-Za-z][A-Za-z0-9_]{3,31})\b')


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def compact(value):
    return re.sub(r'\s+', ' ', value).strip()


def load(path, default=None):
    try:
        if path.suffix == '.gz':
            with gzip.open(path, 'rt', encoding='utf-8') as f:
                return json.load(f)
        return json.loads(path.read_text())
    except (OSError, ValueError, EOFError):
        return default


def save(path, value, compressed=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=None if compressed else 2).encode()
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_bytes(gzip.compress(data, mtime=0) if compressed else data)
    tmp.replace(path)


def host(url):
    return (urlparse(url).hostname or '').lower().removeprefix('www.')


def telegram_url(value):
    p = urlparse(value if '://' in value else 'https://' + value)
    if p.hostname not in ('t.me', 'telegram.me', 'www.t.me'):
        return None
    parts = p.path.strip('/').split('/')
    if parts and parts[0] == 's':
        parts = parts[1:]
    name = parts[0] if parts else ''
    if not USERNAME.fullmatch(name) or name.lower() in EXCLUDED:
        return None
    return 'https://t.me/' + name


def iso(value):
    try:
        date = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return date if date.tzinfo else date.replace(tzinfo=dt.timezone.utc)
    except (ValueError, TypeError):
        return None


def purpose(text):
    # A role word inside a username/domain is not an authored contact label.
    label = re.sub(r'https?://\S+|\b\S+@\S+\b|@[A-Za-z0-9_]+', '', text)
    return next((name for name, pattern in PURPOSES if pattern.search(label)), 'unknown')


def profile_affiliation(title, bio, company):
    """A site-linked creator's channel must not silently become a company channel."""
    text = re.sub(r'[^a-zа-яё0-9]', '', (title + ' ' + bio).lower())
    title_text = re.sub(r'[^a-zа-яё0-9]', '', title.lower())
    name = re.sub(r'[^a-zа-яё0-9]', '', company['name'].lower())
    domain = host(company['website'])
    generic = {'школа', 'онлайн', 'online', 'school', 'academy', 'академия', 'центр', 'учебный', 'образовательный', 'курсы', 'education', 'проект', 'институт', 'университет', 'обучение', 'образования', 'образование', 'учебного'}
    brand_tokens = [t.lower() for t in re.findall(r'[a-zA-Zа-яА-ЯёЁ0-9]{4,}', company['name']) if t.lower() not in generic]
    domain_brand = domain.split('.')[0]
    title_brand = (len(name) >= 4 and name in title_text) or any(token in title_text for token in brand_tokens) or (len(domain_brand) >= 4 and domain_brand in title_text)
    personal = re.search(r'(?:личный|авторский)\s+(?:канал|блог)|(?:рассказываю|делюсь)[\s\S]{0,150}(?:сво[яюей]+\s+жизн|мыслям|ошибк)|(?:создал|создала|основал|основала)\s+(?:свой\s+)?(?:образовательн|школ|проект)|\bя\s+основатель(?:ница)?\b', bio, re.I)
    founder_profile = re.search(r'\bоснователь(?:ница)?\b', bio, re.I) and not title_brand
    if personal or founder_profile:
        return 'relatedAuthor'
    if len(name) >= 4 and name in text or domain and re.search(r'(?<![\w.-])' + re.escape(domain) + r'(?![\w.-])', bio, re.I):
        return 'companyOwned'
    if brand_tokens and any(token in text for token in brand_tokens) or len(domain_brand) >= 4 and domain_brand in text:
        return 'companyOwned'
    return 'unconfirmedRelated'


def text_context(text, token):
    """Local exact quote, not a full paragraph whose role label belongs to another link."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for i, line in enumerate(lines):
        if token.lower() in line.lower():
            if len(line) > 400:
                start = line.lower().find(token.lower())
                return line[max(0, start - 130):start + len(token) + 160]
            if len(line) < 90 and i and len(lines[i - 1]) < 160 and not ATNAME.search(lines[i - 1]) and not EMAIL.search(lines[i - 1]) and 't.me/' not in lines[i - 1]:
                return lines[i - 1] + '\n' + line
            return line
    return ''


def contact_rows(node, source_url, company, channel, profile=False, forwarded=False):
    """Extract only explicitly labelled public work routes; identities stay unknown."""
    if forwarded or node is None:
        return []
    full_text = node.get_text('\n', strip=True)
    candidates = []
    for a in node.select('a[href]'):
        href = a['href']
        label = a.get_text(' ', strip=True)
        tg = telegram_url(href)
        if tg and tg.lower() != channel['url'].lower():
            candidates.append((tg, label or tg, label or tg, 'telegram'))
        elif href.startswith('mailto:'):
            email = href.split(':', 1)[1].split('?', 1)[0]
            if EMAIL.fullmatch(email):
                candidates.append(('mailto:' + email.lower(), label or email, label or email, 'email'))
        elif href.startswith('tel:'):
            number = re.sub(r'[^+0-9]', '', href.split(':', 1)[1])
            if len(number.lstrip('+')) in (10, 11):
                candidates.append(('tel:' + number, label or number, label or number, 'phone'))
    for m in EMAIL.finditer(full_text):
        candidates.append(('mailto:' + m.group().lower(), m.group(), m.group(), 'email'))
    for m in ATNAME.finditer(full_text):
        if m[1].lower() not in EXCLUDED and 'https://t.me/' + m[1].lower() != channel['url'].lower():
            candidates.append(('https://t.me/' + m[1], '@' + m[1], '@' + m[1], 'telegram'))
    for m in PHONE.finditer(full_text):
        number = re.sub(r'\D', '', m.group())
        number = '7' + number[1:] if len(number) == 11 and number.startswith('8') else number
        candidates.append(('tel:+' + number, m.group(), m.group(), 'phone'))
    result = {}
    for url, label, token, kind in candidates:
        quote = text_context(full_text, token)
        role = purpose(quote)
        if profile and role == 'unknown' and kind in ('email', 'phone'):
            role = 'general'
        if role == 'unknown' or not quote:
            continue
        # Posts may advertise somebody else's business. Require a local first-person
        # connection; bios themselves are explicit publisher contact declarations.
        if not profile and not re.search(r'\b(?:на)?пишите\b|\bписать\b|связаться|свяжи[тс]|обращайтесь|отправляйте|отправить|присылайте|отклик|резюме|сотрудничеств[ауо]\s*[:—–-]|реклам[аеу]\s*[:—–-]|(?:поддержк[аи]|контакты)\s*[:—–-]', quote, re.I):
            continue
        if not profile and re.search(r'комментар', quote, re.I) and not re.search(r'личк|личн[а-я]+\s+сообщ|по\s+номеру|телефон|телеграм|telegram|почт|\bв\s+тг\b', quote, re.I):
            continue
        row = dict(id='tgcontact:' + digest(company['id'] + url.lower() + role + source_url),
                   companyID=company['id'], url=url, label=label, purpose=role, context=quote,
                   sourceURL=source_url, sourceQuote=quote, basis='officialChannelPost',
                   addressKind=kind, identity='notInferred', affiliation='publishedBusinessRoute',
                   relationshipSourceURL=channel['sourceURL'], retrievedAt=channel['retrievedAt'],
                   sourceTextSHA256=hashlib.sha256(full_text.encode()).hexdigest())
        result[(url.lower(), role)] = row
    return list(result.values())


def parse_page(html, expected_name):
    soup = BeautifulSoup(html, 'html.parser')
    info = soup.select_one('.tgme_channel_info')
    title = soup.select_one('.tgme_channel_info_header_title') or soup.select_one('.tgme_page_title')
    desc = soup.select_one('.tgme_channel_info_description') or soup.select_one('.tgme_page_description')
    posts = []
    for element in soup.select('.tgme_widget_message[data-post]'):
        ref = element.get('data-post', '')
        parts = ref.split('/')
        if len(parts) != 2 or parts[0].lower() != expected_name.lower() or not parts[1].isdigit():
            continue
        text_node = element.select_one('.tgme_widget_message_text')
        published = element.select_one('time[datetime]')
        forward = element.select_one('.tgme_widget_message_forwarded_from')
        text = text_node.get_text('\n', strip=True) if text_node else ''
        posts.append(dict(ref=ref, postID=int(parts[1]), url='https://t.me/' + ref,
                          text=text, node=text_node, publishedAt=published.get('datetime') if published else None,
                          isForwarded=forward is not None,
                          forwardedFrom=forward.get_text(' ', strip=True) if forward else None,
                          hasMedia=bool(element.select_one('.tgme_widget_message_photo_wrap,.tgme_widget_message_video_player,.tgme_widget_message_voice_player,.tgme_widget_message_document_wrap')),
                          mediaNotExtracted=bool(element.select_one('.tgme_widget_message_photo_wrap,.tgme_widget_message_video_player,.tgme_widget_message_voice_player,.tgme_widget_message_document_wrap'))))
    more = soup.select_one('a.tme_messages_more[data-before]') or soup.select_one('a.tme_messages_more[href*="before="]')
    before = None
    if more:
        before = more.get('data-before')
        if not before:
            before = parse_qs(urlparse(more.get('href', '')).query).get('before', [None])[0]
        if not before or not str(before).isdigit():
            before = None
    return dict(title=title.get_text(' ', strip=True) if title else expected_name,
                bio=desc.get_text('\n', strip=True) if desc else '', descriptionNode=desc,
                channelPreview=bool(info or posts), posts=posts, before=before,
                accountType='channel' if info or posts else 'bot' if expected_name.lower().endswith('bot') else 'accountOrChat')


class Fetcher:
    def __init__(self, out, interval=1.05, raw_limit=700_000_000, free_min=5_000_000_000):
        self.out = out
        self.interval = interval
        self.raw_limit = raw_limit
        self.free_min = free_min
        self.lock = threading.Lock()
        self.last = 0.0
        self.blocked = load(out / 'blocked.json')
        self.raw_bytes = sum(p.stat().st_size for p in (out / 'raw').glob('*.json.gz'))
        self.network_count = 0

    def get(self, url):
        if host(url) != 't.me' or not url.startswith('https://t.me/'):
            raise ValueError('Only public HTTPS t.me requests are allowed')
        path = self.out / 'raw' / (digest(url) + '.json.gz')
        old = load(path)
        if old:
            return dict(old, rawPath=str(path.relative_to(self.out)), fromCache=True)
        with self.lock:
            old = load(path)
            if old:
                return dict(old, rawPath=str(path.relative_to(self.out)), fromCache=True)
            if self.blocked:
                return dict(requestedURL=url, retrievedAt=now(), error='globalStop', reason=self.blocked.get('reason'))
            reason = 'diskReserve' if shutil.disk_usage(self.out).free < self.free_min else 'rawBudget' if self.raw_bytes >= self.raw_limit else None
            if reason:
                self.blocked = {'reason': reason, 'at': now()}
                save(self.out / 'blocked.json', self.blocked)
                return dict(requestedURL=url, retrievedAt=now(), error='globalStop', reason=reason)
            time.sleep(max(0, self.interval - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            record = {'requestedURL': url, 'retrievedAt': now()}
            try:
                # No session, cookies, credential files, redirect to third parties,
                # retries, or embedded media downloads are used.
                r = requests.get(url, timeout=(7, 20), allow_redirects=False,
                                 headers={'User-Agent': 'Mozilla/5.0 (compatible; PublicCompanyResearch/1.0)'})
                self.network_count += 1
                record.update(status=r.status_code, url=url, contentType=r.headers.get('Content-Type', ''))
                if r.status_code in (401, 403, 429):
                    record['error'] = 'rateLimited' if r.status_code == 429 else 'accessBlocked'
                    self.blocked = {'reason': record['error'], 'httpStatus': r.status_code, 'at': now()}
                    save(self.out / 'blocked.json', self.blocked)
                elif r.is_redirect:
                    target = urljoin(url, r.headers.get('Location', ''))
                    # Redirects to t.me/<name> are normal when no public stream is
                    # available; record the boundary and do not traverse a login.
                    record.update(error='redirectNoPublicHistory', redirectURL=target if host(target) == 't.me' else None)
                elif r.status_code >= 400:
                    record['error'] = 'HTTP' + str(r.status_code)
                elif 'html' not in record['contentType']:
                    record['error'] = 'unsupportedContentType'
                elif len(r.content) > 3_000_000:
                    record['error'] = 'responseSizeLimit'
                elif re.search(r'<(?:title|h1)[^>]*>[^<]*(?:captcha|access denied|too many requests)', r.text, re.I):
                    record['error'] = 'accessBlocked'
                    self.blocked = {'reason': 'challengePage', 'at': now()}
                    save(self.out / 'blocked.json', self.blocked)
                else:
                    record['html'] = r.text
                    record['bodySHA256'] = hashlib.sha256(r.content).hexdigest()
            except requests.RequestException as exc:
                record['error'] = type(exc).__name__
            save(path, record, compressed=True)
            self.raw_bytes += path.stat().st_size
        return dict(record, rawPath=str(path.relative_to(self.out)), fromCache=False)


def seed_list(company, args):
    path = args.web_dir / 'companies' / (company['id'] + '.json')
    public = load(path, {})
    accepted, excluded = {}, []
    for entry in public.get('channels', []):
        url = telegram_url(entry.get('url', ''))
        if not url:
            continue
        item = dict(entry, url=url)
        if entry.get('basis') not in ('officialWebsite', 'officialWebsiteBacklink') or not entry.get('sourceURL'):
            excluded.append(dict(item, exclusionReason='unconfirmedWebsiteLink'))
        elif entry.get('affiliation') not in ('companyOwned', 'companyChannel', 'officiallyLinked'):
            excluded.append(dict(item, exclusionReason='companyOwnershipNotConfirmed'))
        elif PERSON_CONTEXT.search(entry.get('context', '')):
            excluded.append(dict(item, exclusionReason='personOrAuthorChannel'))
        elif entry.get('accountType') == 'bot' or url.lower().endswith('bot'):
            excluded.append(dict(item, exclusionReason='botNotPublicationChannel'))
        else:
            accepted[url.lower()] = item
    return list(accepted.values()), excluded, path.exists()


def archive_company(company, args, fetcher):
    dest = args.output_dir / 'companies' / (company['id'] + '.json')
    channels, excluded, website_ready = seed_list(company, args)
    # Reprocessing reads the immutable raw cache and merges newly discovered seeds;
    # no app records or existing archive are destructively cleared.
    old = load(dest, {})
    archive = dict(version=1, id='telegram-' + company['id'] + '-20261014', companyID=company['id'],
                   companyName=company['name'], segment=company.get('segment'), provider='Telegram',
                   windowStart=args.window_start, windowEnd=args.window_end, collectedAt=now(),
                   documents=[], channels=[], contacts=[], coverage=[], excludedChannels=excluded, relatedAuthorDocuments=[],
                   notes=['Публичные тексты из канала, связанного с официальным сайтом; это не полный архив Telegram.',
                          'Дата получения не подменяет публикацию. Удалённые посты, медиа и закрытые чаты не восстановлены.',
                          'Пересылка публикуется в канале, но не доказывает, что исходный текст или вакансия принадлежат компании.',
                          'Контакт имеет опубликованное деловое назначение; имя, должность и право решать о закупке не выведены из username.'])
    for seed in channels[:args.max_channels]:
        name = seed['url'].rsplit('/', 1)[-1]
        base = 'https://t.me/s/' + name
        url = base
        seen_urls, seen_posts = set(), set()
        dates, retained_dates, raw_paths = [], [], []
        retained, contacts = {}, {}
        pages, media_only, undated, outside = 0, 0, 0, 0
        stop, profile = 'pageLimit', None
        previous_cursor = None
        for _ in range(args.max_pages):
            if url in seen_urls:
                stop = 'repeatedCursor'
                break
            seen_urls.add(url)
            page = fetcher.get(url)
            if page.get('rawPath'):
                raw_paths.append(page['rawPath'])
            pages += 1
            if 'html' not in page:
                stop = page.get('error', 'noHTML')
                break
            parsed = parse_page(page['html'], name)
            if profile is None:
                affiliation = profile_affiliation(parsed['title'], parsed['bio'], company)
                profile = dict(seed, title=parsed['title'], bio=parsed['bio'], accountType=parsed['accountType'],
                               affiliation=affiliation, profileSourceURL=seed['url'],
                               retrievedAt=page['retrievedAt'], status='read' if parsed['channelPreview'] else 'noPublicPreview')
                archive['channels'].append(profile)
                if parsed['bio']:
                    profile_document = dict(id='telegram:' + digest(seed['url']), companyID=company['id'],
                        source='Telegram', sourceID=name, provider='Telegram · открытая страница', url=seed['url'],
                        title=parsed['title'] + ' · описание канала', text=parsed['bio'], kind='profile', originalKind='telegramProfile',
                        author=parsed['title'], publishedAt=None, retrievedAt=page['retrievedAt'], quality='fullText',
                        relationship=affiliation if affiliation != 'companyOwned' else 'officialWebsite', relationshipSourceURL=seed['sourceURL'], status='unknown',
                        rawPath=page.get('rawPath'), official=affiliation == 'companyOwned')
                    archive['relatedAuthorDocuments' if affiliation != 'companyOwned' else 'documents'].append(profile_document)
                for row in ([] if affiliation == 'unconfirmedRelated' else contact_rows(parsed['descriptionNode'], seed['url'], company, profile, profile=True)):
                    contacts[(row['url'].lower(), row['purpose'], row['sourceURL'])] = row
            if not parsed['channelPreview'] or not parsed['posts']:
                stop = 'noPublicPosts'
                break
            dates_this_page = []
            newly_seen = 0
            for post in parsed['posts']:
                if post['ref'].lower() in seen_posts:
                    continue
                seen_posts.add(post['ref'].lower())
                newly_seen += 1
                published = iso(post['publishedAt'])
                if published:
                    dates.append(post['publishedAt'])
                    dates_this_page.append(published)
                if not published:
                    undated += 1
                    continue
                if not iso(args.window_start) <= published <= iso(args.window_end):
                    outside += 1
                    continue
                if not post['text']:
                    media_only += 1
                    continue
                title = next((compact(line)[:180] for line in post['text'].splitlines() if re.search(r'[\wА-Яа-я]', line)), parsed['title'])
                doc = dict(id='telegram:' + digest(post['url']), companyID=company['id'], source='Telegram',
                    sourceID=post['ref'], provider='Telegram · открытый пост', url=post['url'], title=title,
                    text=post['text'], kind='post', originalKind='telegramPost', author=parsed['title'],
                    publishedAt=post['publishedAt'], retrievedAt=page['retrievedAt'], quality='fullText',
                    relationship=profile['affiliation'] if profile['affiliation'] != 'companyOwned' else 'officialWebsite', relationshipSourceURL=seed['sourceURL'], status='unknown', official=profile['affiliation'] == 'companyOwned',
                    rawPath=page.get('rawPath'), isForwarded=post['isForwarded'], forwardedFrom=post['forwardedFrom'],
                    mediaNotExtracted=post['mediaNotExtracted'], textSHA256=hashlib.sha256(post['text'].encode()).hexdigest())
                retained[post['url'].lower()] = doc
                retained_dates.append(post['publishedAt'])
                for row in ([] if profile['affiliation'] == 'unconfirmedRelated' else contact_rows(post['node'], post['url'], company, profile, forwarded=post['isForwarded'])):
                    contacts[(row['url'].lower(), row['purpose'], row['sourceURL'])] = row
            if dates_this_page and min(dates_this_page) < iso(args.window_start):
                stop = 'lowerDateReached'
                break
            if profile and profile['affiliation'] != 'companyOwned':
                stop = 'relatedAuthorReviewRequired'
                break
            if not newly_seen:
                stop = 'repeatedPage'
                break
            cursor = parsed['before']
            if not cursor:
                stop = 'publicStreamEnd'
                break
            if previous_cursor is not None and int(cursor) >= previous_cursor:
                stop = 'nondecreasingCursor'
                break
            previous_cursor = int(cursor)
            url = base + '?' + urlencode({'before': cursor})
        archive['relatedAuthorDocuments' if profile and profile['affiliation'] != 'companyOwned' else 'documents'].extend(retained.values())
        archive['contacts'].extend(contacts.values())
        if profile is None:
            archive['channels'].append(dict(seed, status='unavailable', accountType='unverified', retrievedAt=now()))
        available_stop = stop in ('lowerDateReached', 'publicStreamEnd')
        unavailable = stop in ('globalStop', 'accessBlocked', 'rateLimited', 'HTTP403', 'HTTP429', 'ConnectionError', 'ReadTimeout', 'ConnectTimeout', 'redirectNoPublicHistory', 'noPublicPosts')
        related_count = len(retained) if profile and profile['affiliation'] != 'companyOwned' else 0
        archive['coverage'].append(dict(id='telegram:' + digest(base), source='Telegram', sourceURL=base,
            status='unavailable' if unavailable and not retained else 'partial' if seen_posts else 'noResults',
            requestedFrom=args.window_start, requestedTo=args.window_end, receivedCount=len(retained) - related_count, relatedAuthorCount=related_count,
            earliestPublishedAt=min(retained_dates, key=iso) if retained_dates else None,
            latestPublishedAt=max(retained_dates, key=iso) if retained_dates else None,
            note=f'Открытая лента: {pages} страниц, {len(seen_posts)} уникальных сообщений просмотрено, {len(retained)} текстов за период. Остановка: {stop}. Это доступные тексты, а не полный архив или число вакансий.',
            checkedAt=now(), pageCount=pages, scannedCount=len(seen_posts), mediaOnlyCount=media_only,
            undatedCount=undated, outsideWindowCount=outside, stopReason=stop,
            windowBoundaryReached=available_stop, fullArchive=False, rawPaths=raw_paths,
            relationshipSourceURL=seed['sourceURL'], affiliation=profile.get('affiliation') if profile else 'unknown'))
    if not channels:
        archive['coverage'].append(dict(id='telegram:' + digest(company['id'] + ':notfound'), source='Telegram',
            sourceURL=company['website'], status='notStarted' if not website_ready else 'unavailable',
            requestedFrom=args.window_start, requestedTo=args.window_end, receivedCount=0, checkedAt=now(),
            note='Сайт ещё не обработан.' if not website_ready else 'Подтверждённый принадлежащий компании канал не найден в обработанных ссылках официального сайта. Это не означает, что канала нет.'))
    if len(channels) > args.max_channels:
        archive['coverage'].append(dict(id='telegram:' + digest(company['id'] + ':channelLimit'), source='Telegram',
            status='notStarted', receivedCount=0, checkedAt=now(), note=f'{len(channels) - args.max_channels} каналов ещё не обработаны из-за ограничения прохода.',
            skippedChannelURLs=[x['url'] for x in channels[args.max_channels:]]))
    # A shorter rerun must not discard a previously retained source or route.
    # An ownership correction quarantines earlier pilot results rather than
    # keeping their former company attribution during an incremental merge.
    related_channels = {x['url'].lower() for x in archive['channels'] if x.get('affiliation') in ('relatedAuthor', 'unconfirmedRelated')}
    def related(doc):
        return (telegram_url(doc.get('url', '')) or '').lower() in related_channels
    old_related = [dict(d, relationship='relatedAuthor', official=False) for d in old.get('documents', []) if related(d)]
    by_related = {d['url'].lower(): d for d in old.get('relatedAuthorDocuments', []) + old_related + archive['relatedAuthorDocuments']}
    archive['relatedAuthorDocuments'] = list(by_related.values())
    by_url = {d['url'].lower(): d for d in old.get('documents', []) if not related(d)}
    by_url.update({d['url'].lower(): d for d in archive['documents']})
    archive['documents'] = list(by_url.values())
    archive['relatedAuthorDocuments'] = [d for d in archive['relatedAuthorDocuments'] if d['url'].lower() not in by_url]
    by_contact = {(c['url'].lower(), c['purpose'], c['sourceURL']): c for c in old.get('contacts', [])}
    by_contact.update({(c['url'].lower(), c['purpose'], c['sourceURL']): c for c in archive['contacts']})
    archive['contacts'] = list(by_contact.values())
    archive['collectionFingerprint'] = digest(POLICY_VERSION + json.dumps(channels, sort_keys=True) + str(args.max_pages) + str(args.max_channels) + args.window_start + args.window_end)
    archive['collectionPolicyVersion'] = POLICY_VERSION
    save(dest, archive)
    return archive


def summary(args, rows, fetcher):
    counts = dict(companies=len(rows), companiesWithPosts=sum(any(d['kind'] == 'post' for d in r['documents']) for r in rows),
        documents=sum(len(r['documents']) for r in rows),
        posts=sum(d['kind'] == 'post' for r in rows for d in r['documents']),
        relatedAuthorDocuments=sum(len(r.get('relatedAuthorDocuments', [])) for r in rows),
        uniquePostURLs=len({d['url'].lower() for r in rows for d in r['documents'] if d['kind'] == 'post'}),
        publicWorkContactObservations=sum(len(r['contacts']) for r in rows),
        companyWorkRoutes=len({(r['companyID'], c['url'].lower(), c['purpose']) for r in rows for c in r['contacts']}),
        companyWorkAddresses=len({(r['companyID'], c['url'].lower()) for r in rows for c in r['contacts']}),
        uniqueWorkAddresses=len({c['url'].lower() for r in rows for c in r['contacts']}),
        candidateContactObservations=sum(len(r.get('contactCandidates', [])) for r in rows),
        channelCount=sum(len(r['channels']) for r in rows), networkRequestsThisProcess=fetcher.network_count,
        verifiedCompanyChannelCount=sum(ch.get('accountType') == 'channel' and ch.get('affiliation') == 'companyOwned' and ch.get('status') == 'read' for r in rows for ch in r['channels']),
        rawBytes=fetcher.raw_bytes, rawReceiptCount=len(list((fetcher.out / 'raw').glob('*.json.gz'))), blocked=fetcher.blocked, updatedAt=now())
    save(args.output_dir / 'summary.json', counts)
    return counts


def normalize_contacts(args, companies):
    """Re-extract exact, source-resolved routes offline after an extraction change."""
    checks = []
    for company in companies:
        path = args.output_dir / 'companies' / (company['id'] + '.json')
        archive = load(path)
        if not archive:
            continue
        main_docs = {d['url'].lower(): d for d in archive['documents']}
        related_docs = {d['url'].lower(): d for d in archive.get('relatedAuthorDocuments', [])}
        accepted, candidates = {}, {}
        profiles = {telegram_url(ch['url']).lower(): ch for ch in archive['channels'] if telegram_url(ch['url'])}
        for coverage in archive['coverage']:
            channel_url = telegram_url(coverage.get('sourceURL', ''))
            if not channel_url or channel_url.lower() not in profiles:
                continue
            profile = profiles[channel_url.lower()]
            if profile.get('status') != 'read':
                continue
            name = channel_url.rsplit('/', 1)[-1]
            profile_seen = False
            for raw_path in coverage.get('rawPaths', []):
                raw = load(args.output_dir / raw_path, {})
                if 'html' not in raw:
                    continue
                parsed = parse_page(raw['html'], name)
                blocks = []
                if not profile_seen:
                    blocks.append((channel_url, parsed['descriptionNode'], True, False))
                    profile_seen = True
                blocks.extend((post['url'], post['node'], False, post['isForwarded']) for post in parsed['posts'])
                for source_url, node, is_profile, forwarded in blocks:
                    document = main_docs.get(source_url.lower()) or related_docs.get(source_url.lower())
                    if not document:
                        continue
                    rows = contact_rows(node, source_url, company, profile, profile=is_profile, forwarded=forwarded)
                    for row in rows:
                        if row['sourceQuote'] not in document['text']:
                            checks.append({'companyID': company['id'], 'sourceURL': source_url, 'error': 'contactQuoteNotExact'})
                            continue
                        key = (row['url'].lower(), row['purpose'], row['sourceURL'])
                        foreign_email = row['url'].startswith('mailto:') and not is_profile and not (row['url'].split('@')[-1] == host(company['website']) or host(company['website']).endswith('.' + row['url'].split('@')[-1]))
                        if source_url.lower() in main_docs and document.get('relationship') == 'officialWebsite' and not foreign_email:
                            row['materialID'] = document['id']
                            accepted[key] = row
                        else:
                            row['basis'] = 'relatedAuthorReference' if not foreign_email else 'externalBusinessReference'
                            row['affiliation'] = 'candidateBusinessRoute'
                            row['candidateReason'] = 'Source is a related or unconfirmed author profile; not imported as a company contact.' if not foreign_email else 'A post refers to an email on a third-party domain; company ownership/purpose needs review.'
                            row['materialID'] = document['id']
                            candidates[key] = row
        removed = []
        for contact in archive.get('contacts', []):
            key = (contact['url'].lower(), contact['purpose'], contact['sourceURL'])
            if key not in accepted and key not in candidates:
                removed.append(dict(contact, exclusionReason='notAnExplicitSourceResolvedBusinessRoute'))
        archive['contacts'] = list(accepted.values())
        archive['contactCandidates'] = list(candidates.values())
        archive['excludedContactObservations'] = list({c['id']: c for c in archive.get('excludedContactObservations', []) + removed}.values())
        archive['contactExtractionVersion'] = 2
        archive['sourceArchivePath'] = str(path)
        archive['sourceArchiveSHA256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        archive['relatedChannels'] = [dict(ch, basis='relatedAuthorReference') for ch in archive['channels']
                                      if ch.get('affiliation') in ('relatedAuthor', 'unconfirmedRelated')]
        archive['unavailableChannels'] = [ch for ch in archive['channels'] if ch.get('status') != 'read']
        archive['channels'] = [ch for ch in archive['channels'] if ch.get('affiliation') == 'companyOwned' and ch.get('status') == 'read' and ch.get('accountType') == 'channel']
        archive['reviewedAt'] = now()
        save(args.review_dir / 'companies' / path.name, archive)
    report = {'normalizedAt': now(), 'errors': checks, 'errorCount': len(checks), 'networkRequests': 0}
    save(args.review_dir / 'contact-normalization.json', report)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--company-list', type=Path, default=DEFAULT_RUN / 'companies.json')
    p.add_argument('--web-dir', type=Path, default=DEFAULT_RUN / 'web')
    p.add_argument('--output-dir', type=Path, default=DEFAULT_RUN / 'telegram')
    p.add_argument('--company-id', action='append')
    p.add_argument('--limit', type=int)
    p.add_argument('--max-pages', type=int, default=40)
    p.add_argument('--max-channels', type=int, default=8)
    p.add_argument('--workers', type=int, default=2)
    p.add_argument('--interval', type=float, default=1.05)
    p.add_argument('--window-start', default='2026-04-14T00:00:00+03:00')
    p.add_argument('--window-end', default='2026-10-14T23:59:59+03:00')
    p.add_argument('--ready-only', action='store_true', help='Process only companies with an already staged web record')
    p.add_argument('--skip-completed', action='store_true', help='Skip unchanged seed list/page-bound/window')
    p.add_argument('--normalize-only', action='store_true', help='Offline re-extraction of contacts with exact source resolution')
    p.add_argument('--review-dir', type=Path, help='Normalized layer; defaults to <output-dir>/reviewed')
    args = p.parse_args()
    args.review_dir = args.review_dir or args.output_dir / 'reviewed'
    if not iso(args.window_start) or not iso(args.window_end) or iso(args.window_start) > iso(args.window_end):
        p.error('Invalid date window')
    if not 1 <= args.workers <= 3 or args.interval < 1 or not 1 <= args.max_pages <= 100 or not 1 <= args.max_channels <= 30:
        p.error('Expected workers 1..3, interval >=1, pages 1..100, channels 1..30')
    if args.output_dir.resolve() == (Path.home() / 'Library/Application Support/TG Signals').resolve() or 'Application Support/TG Signals' in str(args.output_dir.resolve()):
        p.error('Collector is staged-only; writing to app data is not allowed')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    companies = load(args.company_list, [])
    if args.company_id:
        companies = [c for c in companies if c['id'] in args.company_id]
    if args.normalize_only:
        print(json.dumps(normalize_contacts(args, companies), ensure_ascii=False))
        fetcher = Fetcher(args.output_dir, interval=args.interval)
        all_rows = [load(path, {}) for path in (args.review_dir / 'companies').glob('*.json')]
        summary_args = argparse.Namespace(**dict(vars(args), output_dir=args.review_dir))
        print(json.dumps({'summary': summary(summary_args, all_rows, fetcher)}, ensure_ascii=False))
        return
    if args.ready_only:
        companies = [c for c in companies if (args.web_dir / 'companies' / (c['id'] + '.json')).exists()]
    if args.skip_completed:
        selected = []
        for c in companies:
            seeds, _, _ = seed_list(c, args)
            expected = digest(POLICY_VERSION + json.dumps(seeds, sort_keys=True) + str(args.max_pages) + str(args.max_channels) + args.window_start + args.window_end)
            old = load(args.output_dir / 'companies' / (c['id'] + '.json'), {})
            if old.get('collectionFingerprint') != expected:
                selected.append(c)
        companies = selected
    if args.limit:
        companies = companies[:args.limit]
    fetcher = Fetcher(args.output_dir, interval=args.interval)
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for archive in pool.map(lambda c: archive_company(c, args, fetcher), companies):
            done += 1
            print(json.dumps({'companyID': archive['companyID'], 'company': archive['companyName'],
                'posts': sum(d['kind'] == 'post' for d in archive['documents']), 'channels': len(archive['channels']),
                'workContactObservations': len(archive['contacts']),
                'stops': [c.get('stopReason') for c in archive['coverage']], 'completed': done}, ensure_ascii=False), flush=True)
            if done % 10 == 0:
                all_rows = [load(path, {}) for path in (args.output_dir / 'companies').glob('*.json')]
                print(json.dumps({'progress': summary(args, all_rows, fetcher)}, ensure_ascii=False), flush=True)
    all_rows = [load(path, {}) for path in (args.output_dir / 'companies').glob('*.json')]
    print(json.dumps({'summary': summary(args, all_rows, fetcher)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
