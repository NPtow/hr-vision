#!/usr/bin/env python3
"""Stage public vacancy publications for arbitrary companies (history contract v1).

No live application writes, credentials, paid API, Telegram requests, or CAPTCHA
workarounds. Reuses the web collector's cache and saved VK/Telegram documents.
The only optional HTTP phases are linked public career pages and public HH API.
Counts publications, never hires or inferred unique positions. Python 3.9+,
requests and beautifulsoup4. --self-test is offline and needs no input file.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import gzip
import hashlib
import ipaddress
import json
import re
import shutil
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
MOSCOW = ZoneInfo('Europe/Moscow')
DEFAULT_OUT = ROOT / 'output/segments-2026-10-14/hiring'
START = '2026-04-14T00:00:00+03:00'
TRUSTED = {'officialWebsite', 'employerID', 'reviewedIdentity'}
CAREER = re.compile(r'ваканси|карьер|работа\s+у\s+нас|работа\s+в\b|работать\s+с\s+нами|стать\s+(?:преподавател|наставник|автор)|vacanc|career|work.?with.?us|join.?team|(?:^|[/_.-])(?:jobs?|hiring|teachers?|mentors?|become-author)(?:[/_.-]|$)', re.I)
ROLE = r'(?:менеджер[а-яё]*|преподавател[а-яё]*|учител[а-яё]*|репетитор[а-яё]*|куратор[а-яё]*|методист[а-яё]*|наставник[а-яё]*|маркетолог[а-яё]*|таргетолог[а-яё]*|продюсер[а-яё]*|дизайнер[а-яё]*|разработчик[а-яё]*|редактор[а-яё]*|копирайтер[а-яё]*|сотрудник[а-яё]*|специалист[а-яё]*|руководител[а-яё]*|директор[а-яё]*|рекрутер[а-яё]*|оператор[а-яё]*|аналитик[а-яё]*|ассистент[а-яё]*|администратор[а-яё]*|эксперт[а-яё]*|стаж[её]р[а-яё]*|тренер[а-яё]*|педагог[а-яё]*|психолог[а-яё]*|методолог[а-яё]*|программист[а-яё]*|тестировщик[а-яё]*|бухгалтер[а-яё]*|юрист[а-яё]*|HR|SMM|sales|teacher|developer|manager|designer|engineer|recruiter|marketer|DevOps|QA|sdr|лидогенератор[а-яё]*)'
ACTION = r'(?:ищем|ищу|ищет|ищут|требуется|требуются|разыскиваем|приглашаем|нанимаем|набираем|нужен|нужна|нужны|hiring|looking\s+for)'
EMPLOYMENT = re.compile(r'в\s+(?:нашу\s+)?команду|на\s+работу|зарплат|заработн|вознаграждени|гонорар|резюме|отклик|обязанност|ваканси|собеседован|соискател|кандидат|оплачиваем[а-яё]*\s+стажиров|responsibilit|apply\b|join\s+our', re.I)
NEGATIVE = re.compile(r'не\s+(?:ищем|нанимаем|требуется|требуются|нужен|нужна|нужны)|вакансий\s+(?:(?:пока|сейчас)\s+)?нет|пока\s+нет\s+(?:открытых\s+)?вакансий|закрыт\s+набор|нет\s+активного\s+набора', re.I)
JOBSEEKER = re.compile(r'\bищу\s+(?:себе\s+)?работ|ищу\s+(?:вакансию|подработку)|резюме\s+соискателя|рассматриваю\s+(?:предложения|вакансии)', re.I)
EDUCATION = re.compile(r'(?:ищем|приглашаем|набираем).{0,45}(?:ученик|учащ|школьник)|(?:ищем|приглашаем|набираем).{0,30}студент[а-яё]*.{0,50}(?:на\s+курс|на\s+обучени)|(?:поможем|научим|обучим).{0,65}(?:ваканси|найти\s+работ|стать)|(?:обзор|подборка|как\s+найти).{0,45}ваканси|(?:как\s+стать|профессия|курс\s+для).{0,45}' + ROLE, re.I)
OTHER_EMPLOYER = re.compile(r'(?:партн[её]р[а-яё]*|клиент[а-яё]*|друзья|выпускник[а-яё]*).{0,65}(?:ищет|ищут|ищем|нанима|ваканси)|(?:ваканси[а-яё]*|ищ[а-яё]*).{0,65}(?:наш[а-яё]*\s+партн[её]р|друг[а-яё]*\s+компани)|вакансии\s+(?:наших\s+)?партн[её]ров', re.I)
PLACEMENT = re.compile(r'опубликуем\s+ваш[а-яё]*\s+ваканси|размест(?:ите|ить)\s+вакансию|вакансии\s+(?:(?:для|наших)\s+)?выпускник|вакансии\s+(?:от\s+)?(?:наших\s+)?партн[её]ров|работодателям.{0,100}(?:размест|публикац).{0,35}ваканси|работ(?:а|у)\s+для\s+выпускник', re.I)
EDITORIAL_PATH = re.compile(r'/(?:blog|journal|articles?|news|courses?|professions?|programs?|events?)(?:/|$)', re.I)
CAREER_ASSISTANCE = re.compile(r'(?:карьерны[йе]\s+центр|центр\s+карьер[ыа]).{0,500}(?:выпускник|студент|поможем\s+найти\s+работ|карьерные\s+цел)|(?:выпускник|студент).{0,500}(?:карьерны[йе]\s+центр|центр\s+карьер[ыа])', re.I)
COMMENTARY_REQUEST = re.compile(r'журналистск[а-яё]*\s+запрос|комментари[а-яё]*\s+(?:эксперт|для\s+(?:сми|медиа))|готов[а-яё]*\s+рассказать|ищет\s+авторов\s+для\s+экспертной\s+колонки', re.I)
HH_EMPLOYER = re.compile(r'https?://(?:[a-z0-9-]+\.)?hh\.ru/employer/(\d+)', re.I)
HH_VACANCY = re.compile(r'https?://(?:[a-z0-9-]+\.)?hh\.ru/vacancy/(\d+)', re.I)
CONTACT_ACTION = re.compile(r'резюме|отклик|связаться|пиш(?:и|ите)|написать|пиши|контакт|по\s+вопрос|отправ|присыл|присыла|звон|telegram|телеграм|почт', re.I)
HIRING_CONTACT_CONTEXT = re.compile(r'резюме|отклик|соискател|кандидат|работ[аеуы]\s+у\s+нас|ваканси|собеседован|рекрутер|\bhr\b|hiring|career|recruit', re.I)
PRINT_LOCK = threading.Lock()


def now():
    return dt.datetime.now(MOSCOW).isoformat(timespec='seconds')


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()[:24]


def compact(text):
    return re.sub(r'\s+', ' ', str(text or '')).strip()


def host(url):
    return (urlparse(str(url or '')).hostname or '').lower().removeprefix('www.')


def canonical(url):
    p = urlparse(str(url or ''))
    if p.scheme not in ('http', 'https') or not p.hostname:
        return ''
    hostname = host(url)
    hostname = {'m.vk.com': 'vk.com', 'vk.ru': 'vk.com', 'telegram.me': 't.me'}.get(hostname, hostname)
    path = p.path
    if hostname == 't.me' and path.startswith('/s/'):
        path = path[2:]
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid', 'yclid', 'hhtmfrom', 'from', 'trk')]
    return p._replace(scheme='https', netloc=hostname, path=path.rstrip('/') or '/', query=urlencode(sorted(query)), fragment='').geturl()


def public_url(url):
    p = urlparse(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        return False
    if p.hostname in ('localhost', 'localhost.localdomain') or p.hostname.endswith(('.local', '.internal')):
        return False
    try:
        if not ipaddress.ip_address(p.hostname).is_global:
            return False
    except ValueError:
        pass
    return True


def iso(value):
    if not value:
        return None
    try:
        d = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return d.replace(tzinfo=MOSCOW) if d.tzinfo is None else d
    except (ValueError, TypeError):
        return None


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError, TypeError):
        return default


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    tmp.replace(path)


def text_html(raw):
    soup = BeautifulSoup(raw or '', 'html.parser')
    for element in soup(['script', 'style', 'noscript', 'svg', 'nav']):
        element.decompose()
    return (soup.find('body') or soup).get_text('\n', strip=True)


def headline(text):
    return next((compact(x)[:180] for x in text.splitlines() if re.search(r'[a-zа-яё]{3}', x, re.I)), compact(text)[:180])


def classify(title, text, explicit_role=None):
    """Conservative publication classifier with an exact, reviewable quote."""
    combined = title + '\n' + text
    if explicit_role and compact(text):
        pattern = r'\s+'.join(re.escape(x) for x in compact(explicit_role).split())
        position = re.search(pattern, text, re.I) if pattern else None
        quote = text[position.start():position.start() + 700] if position else text[:700]
        return {'kind': 'vacancy', 'quote': quote, 'jobTitle': explicit_role, 'reason': 'explicitRoleInSource'}
    fragments = [x.strip() for x in re.split(r'[\n.!?]+', combined) if x.strip()]
    for fragment in fragments:
        if not re.search(ROLE, fragment, re.I) or not re.search(ACTION + '|ваканси|требуе', fragment, re.I):
            continue
        if NEGATIVE.search(fragment) or JOBSEEKER.search(fragment) or EDUCATION.search(fragment):
            continue
        if re.search(r'(?:для\s+(?:моего\s+)?реб[её]нка|ищ[а-яё]*\s+(?:себе\s+)?репетитор)', fragment, re.I) and not EMPLOYMENT.search(text):
            continue
        if re.search(r'^(?:если|когда|какие|как|исследуем|изучаем|им\s+нужны|именно\s+здесь)\b|\bищете\b|(?:навык|знани|опыт)[а-яё]*\s+нуж|помощь\s+в\s+размещении\s+ваканси|рынок.{0,65}(?:разработ|ваканси)', fragment, re.I):
            continue
        if COMMENTARY_REQUEST.search(fragment):
            continue
        if re.search(r'приглашаем\s+(?:вас\s+|тебя\s+)?на\s+(?:открыт[а-яё]*\s+)?(?:встреч|вебинар|мастер[- ]класс|курс|лекци|конференц|семинар|обучени)', fragment, re.I):
            continue
        matches = re.search(r'\b(?:ищем|ищу|ищет|ищут|требуется|требуются|разыскиваем|приглашаем|нанимаем|набираем|hiring|looking\s+for)\b.{0,85}\b' + ROLE +
                            r'\b|\b(?:нужен|нужна|нужны)\b.{0,25}\b' + ROLE +
                            r'\b|\b' + ROLE + r'\b.{0,30}(?:в\s+(?:нашу\s+)?команду|требуется|требуются)' +
                            r'|(?:^[^\w]*вакансия|(?:открыт[а-яё]*|есть|нов[а-яё]*|горящ[а-яё]*)\s+ваканси[а-яё]*)\b.{0,80}\b' + ROLE, fragment, re.I)
        direct = re.search(r'^\s*(?:мы\s+)?(?:ищем|нанимаем|набираем|требуется|требуются)\s+' + ROLE, fragment, re.I)
        if matches and (EMPLOYMENT.search(combined) or direct):
            role = re.sub(r'^.*?\b(?:ищем|ищу|ищет|ищут|разыскиваем|нанимаем|набираем|приглашаем|требуется|требуются|вакансия|нужен|нужна|нужны|hiring|looking\s+for)\b\s*[:—–-]?\s*(?:в\s+(?:нашу\s+)?команду\s*)?', '', fragment, flags=re.I)
            return {'kind': 'vacancy', 'quote': fragment, 'jobTitle': role[:160], 'reason': 'explicitHiringSentence'}
    return {'kind': 'post', 'quote': '', 'jobTitle': None, 'reason': 'noQualifyingEmployerHiringStatement'}


def role_function(text):
    for name, pattern in [
        ('Руководство продажами', r'(?:руководител|директор|head).{0,45}(?:продаж|sales)|\bроп\b'),
        ('Лидогенерация', r'лидоген|лид[а-яё]*.{0,12}генерац|sdr|lead\s*gen'),
        ('Продажи', r'продаж|sales|закрыти[а-яё]*\s+сдел'),
        ('Преподавание', r'преподавател|учител|репетитор|teacher|педагог|тренер'),
        ('Методика и сопровождение', r'методист|куратор|наставник|методолог'),
        ('Маркетинг и контент', r'маркет|smm|таргет|контент|редактор|копирайтер|продюсер'),
        ('HR и подбор', r'рекрутер|\bhr\b|подбор\s+персонала'),
        ('Разработка и дизайн', r'разработчик|программист|developer|дизайнер|engineer|qa|тестировщик'),
        ('Управление', r'руководител|директор')]:
        if re.search(pattern, text, re.I):
            return name
    return 'Не определена'


def extract_contacts(doc):
    text = doc.get('text', '')
    found = {}
    patterns = [('email', r'(?<![\w.])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![A-Z0-9_-])'),
                ('telegram', r'(?<![\w@])(?:https?://(?:t\.me|telegram\.me)/|@)([A-Z][A-Z0-9_]{3,31})(?![\w/])'),
                ('phone', r'(?<!\d)(?:\+7|8)[\s(.-]*\d{3}[\s).-]*\d{3}[\s.-]*\d{2}[\s.-]*\d{2}(?!\d)')]
    for kind, pattern in patterns:
        for m in re.finditer(pattern, text, re.I):
            quote = text[max(0, m.start() - 180):min(len(text), m.end() + 150)]
            if not HIRING_CONTACT_CONTEXT.search(quote):
                continue  # Generic footer support/registration addresses are not hiring routes.
            if kind != 'email' and not CONTACT_ACTION.search(quote):
                continue
            address = m.group(0)
            if kind == 'telegram':
                name = m.group(1)
                if name.lower() in {'share', 'joinchat', 'addstickers', 'telegram', 'channel', 'username'}:
                    continue
                address = 'https://t.me/' + name
                if canonical(doc['url']).startswith(canonical(address) + '/'):
                    continue  # A post's own channel is not a named recruiter.
            elif kind == 'email':
                address = address.lower()
            else:
                address = '+7' + re.sub(r'\D', '', address)[-10:]
            key = kind + ':' + address.lower()
            url = 'mailto:' + address if kind == 'email' else 'tel:' + address if kind == 'phone' else address
            found[key] = dict(id='hiring-contact:' + digest(doc['companyID'] + key), companyID=doc['companyID'],
                kind=kind, address=address, name=None, role=None, purpose='hiring', purposeLabel='Контакт из объявления о найме',
                url=url, label='Контакт для отклика на вакансию', context=quote,
                basis='officialChannelPost' if doc.get('source') in ('Telegram', 'VK') else 'officialWebsite' if doc['relationship'] in TRUSTED else 'candidate',
                sourceURL=doc['url'], sourceQuote=quote, publishedAt=doc.get('publishedAt'), retrievedAt=doc['retrievedAt'],
                relationship=doc['relationship'], relationshipSourceURL=doc.get('relationshipSourceURL'),
                affiliation='publishedInVerifiedHiringSource' if doc['relationship'] in TRUSTED else 'unconfirmed',
                caveat='Имя, должность и полномочия получателя этим контактом не подтверждены.')
    return list(found.values())


def job_postings(soup):
    def walk(value):
        if isinstance(value, list):
            for item in value:
                yield from walk(item)
        elif isinstance(value, dict):
            typ = value.get('@type', '')
            if typ == 'JobPosting' or isinstance(typ, list) and 'JobPosting' in typ:
                yield value
            for item in value.values():
                if isinstance(item, (list, dict)):
                    yield from walk(item)
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            yield from walk(json.loads(node.string or node.get_text()))
        except (ValueError, TypeError):
            continue


class Collector:
    def __init__(self, args):
        self.args = args
        self.out = args.output_dir.resolve()
        live = Path.home() / 'Library/Application Support/TG Signals'
        if self.out == live or live in self.out.parents:
            raise ValueError('Only staging output is allowed; live application directory rejected.')
        self.out.mkdir(parents=True, exist_ok=True)
        self.end = args.window_end or now()
        if not iso(args.window_start) or not iso(self.end) or iso(args.window_start) > iso(self.end):
            raise ValueError('Invalid requested date window')
        self.run = self.end[:10].replace('-', '') + '-' + digest(now() + str(time.time()))[:8]
        self.lock = threading.Lock()
        self.hh_lock = threading.Lock()
        stops = load(self.out / 'source-stops.json', {})
        if not stops and args.review_input_dir:
            stops = load(args.review_input_dir.parent / 'source-stops.json', {})
        self.hh_blocked = stops.get('hhBlocked')
        self.blocked_hosts = stops.get('blockedHosts', {})
        self.last_request = {}
        self.raw_bytes = sum(x.stat().st_size for x in (self.out / 'raw').rglob('*') if x.is_file())
        self.page_paths = {}
        self.page_files_seen = set()
        for path in (args.web_dir / 'pages').glob('*.json'):
            self.page_files_seen.add(path)
            row = load(path, {})
            for key in ('url', 'requestedURL'):
                if row.get(key):
                    self.page_paths[canonical(row[key])] = path
        for path in (self.out / 'raw').rglob('*.json'):
            row = load(path, {})
            for key in ('url', 'requestedURL'):
                if row.get(key):
                    self.page_paths.setdefault(canonical(row[key]), path)
        self.archives_by_company = defaultdict(list)
        manual = load(args.decisions_file or (args.review_input_dir.parent / 'manual-review.json' if args.review_input_dir else self.out / 'manual-review.json'), {})
        self.manual_reviews = {canonical(x['url']): x for x in manual.get('decisions', [])}
        for directory in args.archive_dir:
            for path in directory.rglob('*.json'):
                if any(p in {'raw', 'pages'} for p in path.relative_to(directory).parts):
                    continue
                record = load(path, {})
                if isinstance(record, dict) and record.get('companyID') and isinstance(record.get('documents'), list):
                    self.archives_by_company[record['companyID']].append(path)

    def in_window(self, value):
        d = iso(value)
        return d is not None and iso(self.args.window_start) <= d <= iso(self.end)

    def cached_page(self, url):
        path = self.page_paths.get(canonical(url))
        if not path:
            # The web worker finishes new immutable receipts while this run is active.
            with self.lock:
                for fresh in (self.args.web_dir / 'pages').glob('*.json'):
                    if fresh in self.page_files_seen:
                        continue
                    self.page_files_seen.add(fresh)
                    record = load(fresh, {})
                    for key in ('url', 'requestedURL'):
                        if record.get(key):
                            self.page_paths[canonical(record[key])] = fresh
                path = self.page_paths.get(canonical(url))
            if not path:
                return None
        page = load(path, {})
        if not page.get('html') and page.get('htmlPath'):
            raw = Path(page['htmlPath'])
            candidates = [raw] if raw.is_absolute() else [path.parent / raw, self.args.web_dir / raw]
            for candidate in candidates:
                if candidate.exists():
                    try:
                        page['html'] = gzip.decompress(candidate.read_bytes()).decode('utf-8') if candidate.suffix == '.gz' else candidate.read_text()
                    except (OSError, UnicodeError):
                        pass
                    break
        page['rawPath'] = str(path.resolve())
        page['reused'] = True
        return page

    def fetch(self, url):
        cached = self.cached_page(url)
        if cached is not None:
            return cached
        key = digest(canonical(url))
        receipt = self.out / 'raw' / self.run / (key + '.json')
        old = load(receipt)
        if old is not None:
            if old.get('htmlPath'):
                old['html'] = gzip.decompress(Path(old['htmlPath']).read_bytes()).decode('utf-8')
            old['rawPath'] = str(receipt.resolve())
            return old
        record = {'requestedURL': url, 'retrievedAt': now(), 'runID': self.run}
        hostname = host(url)
        if hostname in ('t.me', 'telegram.me', 'vk.com', 'vk.ru', 'm.vk.com'):
            return dict(record, error='SocialNetworkOwnedByOtherCollector')
        if not public_url(url):
            return dict(record, error='NotPublicHTTPURL')
        with self.lock:
            if shutil.disk_usage(self.out).free < self.args.min_free_gib * 1024**3 or self.raw_bytes >= self.args.max_raw_mib * 1024**2:
                return dict(record, error='DiskBudgetStop')
            if hostname in self.blocked_hosts:
                return dict(record, error='SourceStoppedAfterBlock', blockReason=self.blocked_hosts[hostname])
            if (hostname == 'api.hh.ru' or hostname.endswith('.hh.ru')) and self.hh_blocked:
                return dict(record, error='SourceStoppedAfterBlock', blockReason=self.hh_blocked)
            delay = max(0, self.args.delay - (time.monotonic() - self.last_request.get(hostname, 0)))
            self.last_request[hostname] = time.monotonic() + delay
        if delay:
            time.sleep(delay)
        try:
            r = requests.get(url, headers={'User-Agent': 'HRVisionPublicResearch/1.0 (public company career research)'}, timeout=(7, 18), stream=True)
            record.update(url=r.url, status=r.status_code, contentType=r.headers.get('Content-Type', ''))
            if not public_url(r.url):
                record['error'] = 'RedirectNotPublicHTTPURL'
            elif r.status_code in (401, 403, 429) or 'captcha' in r.url.lower():
                record['error'] = 'rateLimited' if r.status_code == 429 else 'accessBlocked'
            elif r.status_code >= 400:
                record['error'] = 'HTTP' + str(r.status_code)
            elif not any(x in record['contentType'] for x in ('html', 'json')):
                record['error'] = 'UnsupportedContentType'
            else:
                content = bytearray()
                for chunk in r.iter_content(65536):
                    content.extend(chunk)
                    if len(content) > 6_000_000:
                        record['truncated'] = True
                        break
                decoded = content.decode(r.encoding or 'utf-8', errors='replace')
                # A challenge is a stop, not data or an invitation to retry elsewhere.
                if re.search(r'<title[^>]*>[^<]*(?:captcha|access denied|доступ ограничен|just a moment)', decoded, re.I) or '"captcha_url"' in decoded:
                    record['error'] = 'accessBlocked'
                elif 'json' in record['contentType']:
                    record['json'] = json.loads(decoded)
                else:
                    compressed = gzip.compress(decoded.encode(), compresslevel=6)
                    raw = receipt.with_suffix('.html.gz')
                    raw.parent.mkdir(parents=True, exist_ok=True)
                    with self.lock:
                        if self.raw_bytes + len(compressed) > self.args.max_raw_mib * 1024**2:
                            record['error'] = 'DiskBudgetStop'
                        else:
                            raw.write_bytes(compressed)
                            self.raw_bytes += len(compressed)
                            record.update(htmlPath=str(raw.resolve()), htmlSHA256=hashlib.sha256(decoded.encode()).hexdigest())
                            record['html'] = decoded
            r.close()
        except (requests.RequestException, ValueError) as exc:
            record['error'] = type(exc).__name__
        if record.get('error') in ('accessBlocked', 'rateLimited'):
            with self.lock:
                self.blocked_hosts[hostname] = record['error']
            if hostname == 'api.hh.ru' or hostname.endswith('.hh.ru'):
                self.hh_blocked = record['error']
            save(self.out / 'source-stops.json', {'hhBlocked': self.hh_blocked, 'blockedHosts': self.blocked_hosts, 'updatedAt': now()})
        saved = {k: v for k, v in record.items() if k != 'html'}
        save(receipt, saved)
        with self.lock:
            self.raw_bytes += receipt.stat().st_size
        record['rawPath'] = str(receipt.resolve())
        return record

    def coverage(self, source, url, status, count, note, **extra):
        return dict(id='hiring-check:' + digest(source + url), source=source, sourceURL=url, status=status,
                    requestedFrom=self.args.window_start, requestedTo=self.end, receivedCount=count, note=note, checkedAt=now(), **extra)

    def document(self, company, source, url, title, text, relationship, identity_url, published=None, **extra):
        return dict(id='hiring:' + digest(source + canonical(url)), companyID=company['id'], source=source, url=url,
                    title=title or company['name'], text=text, kind=extra.pop('kind', 'vacancy'),
                    publishedAt=published if iso(published) else None, retrievedAt=extra.pop('retrievedAt', now()),
                    quality=extra.pop('quality', 'fullText'), relationship=relationship,
                    relationshipSourceURL=identity_url, status=extra.pop('status', 'unknown'), **extra)

    def annotate(self, doc, explicit=False):
        result = classify(doc.get('title', ''), doc.get('text', ''), doc.get('jobTitle') if explicit else None)
        doc['classification'] = result
        if result['kind'] == 'vacancy':
            doc['kind'] = 'vacancy'
            doc['jobTitle'] = result['jobTitle']
            doc['jobFunction'] = role_function(result['jobTitle'] or result['quote'])
            if (OTHER_EMPLOYER.search(doc['text']) or PLACEMENT.search(doc['text']) or doc.get('isForwarded')) and not doc.get('employerID'):
                doc['relationship'] = 'candidate'
                doc['identityCaveat'] = 'Текст относит найм к другой организации/партнёру; не подтверждён найм самой компании.'
            contacts = extract_contacts(doc)
            doc['contacts'] = contacts
            doc['contact'] = '; '.join(x['address'] for x in contacts) or None
            doc['hiringDisposition'] = 'reviewedVacancy' if explicit else None
        else:
            doc['kind'] = 'page' if doc.get('source') == 'Сайт' else 'post'
            doc['hiringDisposition'] = 'excluded'
            doc['jobTitle'] = None
            doc['contacts'] = []
            doc['contact'] = None
        return doc

    def cached_phase(self, company, archive):
        accepted, excluded = [], []
        sources = self.archives_by_company.get(company['id'], [])
        for path in sources:
            record = load(path, {})
            kept = 0
            for row in record.get('documents', []):
                if row.get('companyID', company['id']) != company['id'] or not row.get('url') or not row.get('text'):
                    continue
                source = row.get('source') or row.get('provider', '')
                source = 'Telegram' if 'Telegram' in source or 'telegram' in row.get('kind', '').lower() else 'VK' if 'VK' in source else source
                if source not in ('Telegram', 'VK', 'HH', 'SuperJob'):
                    continue
                relationship = row.get('relationship', 'candidate')
                if relationship not in TRUSTED:
                    relationship = 'candidate'
                doc = self.document(company, source, row['url'], row.get('title') or headline(row['text']), row['text'],
                    relationship, row.get('relationshipSourceURL'), row.get('publishedAt'),
                    quality=row.get('quality', 'snippet'), sourceID=str(row.get('sourceID', row.get('id', ''))),
                    author=row.get('author'), retrievedAt=row.get('retrievedAt') or record.get('collectedAt') or now(),
                    rawPath=row.get('rawPath'), reusedArchive=str(path.resolve()), collectionPhase='cached',
                    employerID=row.get('employerID'), jobTitle=row.get('jobTitle'), status=row.get('status', 'unknown'),
                    isForwarded=row.get('isForwarded', False), forwardedFrom=row.get('forwardedFrom'), sourceRelationship=row.get('relationship'))
                self.annotate(doc, explicit=row.get('kind') == 'vacancy' and bool(row.get('jobTitle')))
                if doc['kind'] == 'vacancy':
                    accepted.append(doc)
                    kept += 1
                elif re.search(r'ваканси|\bищем\b|\bhiring\b|требуется', row['text'], re.I) or canonical(row['url']) in self.manual_reviews:
                    excluded.append(dict(url=row['url'], reason=doc['classification']['reason'], textSHA256=hashlib.sha256(row['text'].encode()).hexdigest(), reusedArchive=str(path.resolve())))
                    # Keep the reviewed negative as an authoritative overlay. Otherwise
                    # an original TG/VK copy can be reclassified by the native app.
                    accepted.append(doc)
            self.upsert_coverage(archive, self.coverage(record.get('provider', 'Сохранённые публикации'), 'file://' + str(path.resolve()),
                'partial', kept, 'Повторный локальный разбор сохранённых текстов. Новых сетевых запросов к VK/Telegram нет; полнота зависит от исходного коллектора.',
                inspectedDocumentCount=len(record.get('documents', []))))
        if not sources:
            self.upsert_coverage(archive, self.coverage('Сохранённые публикации', company['website'] + '#saved-social', 'notStarted', 0,
                'Staged архивы VK/Telegram ещё не доступны; локальный разбор можно повторить после их сбора.'))
        archive['documents'] = [d for d in archive['documents'] if d.get('collectionPhase') != 'cached'] + accepted
        archive['excludedDocuments'] = list({x['url']: x for x in excluded}.values())

    @staticmethod
    def upsert_coverage(archive, check):
        archive['coverage'] = [x for x in archive['coverage'] if x['id'] != check['id']] + [check]

    @staticmethod
    def upsert_doc(archive, doc):
        key = canonical(doc['url'])
        old = next((d for d in archive['documents'] if canonical(d['url']) == key), None)
        if old and len(old['text']) > len(doc['text']) and old['quality'] == 'fullText' and old['relationship'] in TRUSTED:
            return
        archive['documents'] = [d for d in archive['documents'] if canonical(d['url']) != key] + [doc]

    def web_phase(self, company, archive):
        # Re-running this phase replaces its own projection, not source receipts.
        if not self.args.web_supplement:
            archive['documents'] = [d for d in archive['documents'] if d.get('collectionPhase') != 'web']
            archive['coverage'] = [c for c in archive['coverage'] if c['source'] != 'Сайт']
        public = load(self.args.web_dir / 'companies' / (company['id'] + '.json'), {})
        shared_domain = bool(public.get('sharedOfficialDomain') or public.get('organizationScope') == 'sharedDomainNotUnitVerified')
        evidence = {'hhEmployerIDs': {}, 'hhVacancyIDs': {}}
        archive['identityEvidence'] = evidence
        careers = {}
        aliases = {host(company['website'])}
        placement_origins = {canonical(d.get('url', '')) for d in public.get('documents', [])
                             if PLACEMENT.search(compact(d.get('text', ''))) or host(d.get('url', '')) == 'career.htmlacademy.ru'}
        def add_link(url, origin, label='', category=''):
            url = urljoin(origin, url)
            if not public_url(url):
                return
            if re.search(r'/(?:login|signup|register|auth|password|search)(?:/|$)', urlparse(url).path, re.I):
                return
            basis = {'basis': 'candidate' if shared_domain else 'officialWebsite', 'sourceURL': origin, 'sharedDomainNotUnitVerified': shared_domain}
            if canonical(origin) not in placement_origins:
                for eid in HH_EMPLOYER.findall(url):
                    evidence['hhEmployerIDs'][eid] = basis
                for vid in HH_VACANCY.findall(url):
                    evidence['hhVacancyIDs'][vid] = basis
            elif HH_EMPLOYER.search(url) or HH_VACANCY.search(url):
                evidence.setdefault('externalEmployerLinks', []).append({'url': url, 'sourceURL': origin, 'reason': 'Graduate placement/job board; employer identity belongs to a third party.'})
            if EDITORIAL_PATH.search(urlparse(url).path):
                return  # Career advice, course pages and market articles are not employer vacancy pages.
            if category in ('career', 'vacancy') or CAREER.search(urlparse(url).path + ' ' + host(url) + ' ' + label):
                if not re.search(r'(^|\.)hh\.ru$|(^|\.)t\.me$|(^|\.)vk\.(?:com|ru)$|(^|\.)superjob\.ru$', host(url)):
                    aliases.add(host(url))
                    careers.setdefault(canonical(url), (url, origin))
        official_rows = [d for d in public.get('documents', []) if d.get('official') or d.get('relationship') in TRUSTED]
        for row in public.get('outboundLinks', []):
            origin = row.get('sourceURL', company['website'])
            if host(origin) == host(company['website']) or any(canonical(x.get('url', '')) == canonical(origin) for x in official_rows):
                add_link(row.get('url', ''), origin, row.get('linkText', ''), row.get('category', ''))
        for row in official_rows:
            if CAREER.search(row.get('url', '') + ' ' + row.get('title', '')):
                add_link(row['url'], row['url'], row.get('title', ''), 'career')
        # Raw official HTML keeps careers/employer backlinks that plain text loses.
        for row in official_rows:
            page = self.cached_page(row['url'])
            if page and page.get('html'):
                soup = BeautifulSoup(page['html'], 'html.parser')
                for a in soup.select('a[href]'):
                    add_link(a['href'], row['url'], a.get_text(' ', strip=True))
        if not public:
            self.upsert_coverage(archive, self.coverage('Сайт', company['website'], 'notStarted', 0, 'Staged official-site packet ещё не получен. Главная страница не запрашивалась повторно.'))
            return
        priority_links = load(self.args.web_links_file, []) if self.args.web_links_file else []
        priority_urls = {canonical(x['url']) for x in priority_links if x.get('companyID') == company['id']}
        # A supplemental seed still has to be present in observed official links.
        # It is a queue selection, never a new source of employer identity.
        if self.args.web_links_file:
            careers = {k: v for k, v in careers.items() if k in priority_urls}
        seen = set()
        while len(seen) < self.args.web_pages:
            pending = [v for k, v in careers.items() if k not in seen and (not self.args.web_links_file or k in priority_urls)]
            if not pending:
                break
            url, origin = pending[0]
            seen.add(canonical(url))
            page = self.fetch(url)
            if not page.get('html'):
                self.upsert_coverage(archive, self.coverage('Сайт', url, 'unavailable', 0, 'Публичная career-страница: ' + page.get('error', 'noHTML'), rawPath=page.get('rawPath')))
                continue
            final = page.get('url', url)
            if host(final) not in aliases:
                self.upsert_coverage(archive, self.coverage('Сайт', url, 'partial', 0, 'Перенаправление на другой домен. Связь конечной страницы с компанией не установлена.', rawPath=page.get('rawPath')))
                continue
            soup = BeautifulSoup(page['html'], 'html.parser')
            title = compact(soup.title.get_text()) if soup.title else company['name'] + ' · карьера'
            page_text = text_html(page['html'])
            placement_page = bool(PLACEMENT.search(compact(page_text)))
            if host(final) == 'career.htmlacademy.ru':
                placement_page = True  # Verified pilot: vacancy board for outside employers/graduates.
            if placement_page:
                placement_origins.add(canonical(final))
            for a in soup.select('a[href]'):
                add_link(a['href'], final, a.get_text(' ', strip=True))
            grouped = defaultdict(list)
            for job in job_postings(soup):
                description = text_html(job.get('description', ''))
                role = compact(job.get('title') or job.get('name'))
                if not description or not role:
                    continue
                original = urljoin(final, job.get('url') or final)
                if not public_url(original):
                    original = final
                add_link(original, final, 'Вакансия', 'vacancy')
                grouped[canonical(original)].append((job, role, description, original))
            kept = 0
            for jobs in grouped.values():
                role = '; '.join(dict.fromkeys(x[1] for x in jobs))
                body = '\n\n'.join((x[1] + '\n' if len(jobs) > 1 else '') + x[2] for x in jobs)
                dates = {str(x[0].get('datePosted')) for x in jobs if iso(x[0].get('datePosted'))}
                published = next(iter(dates)) if len(dates) == 1 and all(iso(x[0].get('datePosted')) for x in jobs) else None
                doc = self.document(company, 'Сайт', jobs[0][3], role, body, 'candidate' if placement_page or shared_domain else 'officialWebsite', origin, published,
                    jobTitle=role, rawPath=page.get('rawPath'), retrievedAt=page.get('retrievedAt', now()),
                    quality='snippet' if page.get('truncated') else 'fullText', collectionPhase='web', schemaJobPostingCount=len(jobs),
                    sourceDateValues=sorted(dates), jobPostingMetadata=[{k: j[0][k] for k in ('identifier', 'datePosted', 'validThrough', 'hiringOrganization', 'employmentType', 'jobLocation', 'baseSalary', 'totalJobOpenings') if k in j[0]} for j in jobs],
                    identityCaveat='Общий домен нескольких подразделений; принадлежность вакансии этому подразделению не подтверждена.' if shared_domain else 'Карьерный центр/площадка для сторонних работодателей: найм самой образовательной компании не подтверждён.' if placement_page else None)
                self.annotate(doc, explicit=True)
                self.upsert_doc(archive, doc)
                kept += 1
            if not grouped:
                text = page_text
                if text:
                    doc = self.document(company, 'Сайт', final, title, text[:160000], 'candidate' if placement_page or shared_domain else 'officialWebsite', origin,
                        kind='page', rawPath=page.get('rawPath'), retrievedAt=page.get('retrievedAt', now()),
                        quality='snippet' if len(text) > 160000 or page.get('truncated') else 'fullText', collectionPhase='web',
                        dateSemantics='No publication date: current career-page observation, not a dated publication in the requested window.',
                        identityCaveat='Общий домен нескольких подразделений; принадлежность вакансии этому подразделению не подтверждена.' if shared_domain else 'Карьерный центр/площадка для сторонних работодателей: найм самой образовательной компании не подтверждён.' if placement_page else None)
                    headings = [compact(h.get_text(' ', strip=True)) for h in soup.select('h1,h2,h3')]
                    roles = [h for h in headings if len(h) < 200 and re.search(ROLE, h, re.I)
                             and not EDUCATION.search(h) and not re.search(r'курс|професси|выпускни|^(?:как|что|почему|зачем|чем|где)\b', h, re.I)]
                    sections = len(re.findall(r'обязанности|требования|условия|чем предстоит заниматься|что нужно делать|что мы предлагаем|что вы будете делать', text, re.I))
                    explicit_role = roles[0] if roles and sections >= 2 and not NEGATIVE.search(text) else None
                    if explicit_role:
                        doc['jobTitle'] = explicit_role
                    self.annotate(doc, explicit=bool(explicit_role))
                    if placement_page and not explicit_role:
                        doc.update(kind='page', hiringDisposition='excluded', jobTitle=None, contacts=[], contact=None,
                                   classification={'kind': 'post', 'quote': '', 'jobTitle': None, 'reason': 'placementServiceNotEmployerHiring'})
                    self.upsert_doc(archive, doc)
                    kept += 1
            self.upsert_coverage(archive, self.coverage('Сайт', final, 'partial' if kept else 'noResults', kept,
                'Официальная карьерная страница. Дата взята только из JobPosting datePosted; дата проверки не заменяет дату публикации. Удалённые страницы/старые версии не восстановлены.',
                rawPath=page.get('rawPath'), reusedCache=bool(page.get('reused'))))
        if not careers:
            self.upsert_coverage(archive, self.coverage('Сайт', company['website'], 'partial', 0,
                'В staged официальных страницах career/HH ссылки не найдены. Это не доказывает отсутствие найма; весь сайт и исторические версии не обходились.'))
        if len(careers) > len(seen):
            self.upsert_coverage(archive, self.coverage('Сайт', company['website'] + '#bounded-career-crawl', 'partial', 0,
                f'Лимит карьерных страниц: {len(seen)} просмотрено, {len(careers)-len(seen)} обнаруженных ссылок не просмотрено.', skippedURLs=[v[0] for k, v in careers.items() if k not in seen]))

    def hh_phase(self, company, archive):
        evidence = archive.get('identityEvidence', {})
        employers = evidence.get('hhEmployerIDs', {})
        vacancy_ids = dict(evidence.get('hhVacancyIDs', {}))
        if not employers and not vacancy_ids:
            self.upsert_coverage(archive, self.coverage('HH', company['website'] + '#hh-identity', 'notStarted', 0,
                'Подтверждённые официальной ссылкой employer ID или вакансия HH не найдены. Совпадение названия не считается связью.'))
            return
        with self.hh_lock:
            if self.hh_blocked:
                self.upsert_coverage(archive, self.coverage('HH', 'https://api.hh.ru/employers/' + next(iter(employers), 'unknown'), 'unavailable', 0,
                    'HH остановлен для всего запуска после ' + self.hh_blocked + '. Повторного обхода/другого IP нет.'))
                return
            for eid, basis in employers.items():
                if self.hh_blocked:
                    break
                identity = self.fetch('https://api.hh.ru/employers/' + eid)
                if identity.get('error'):
                    self.upsert_coverage(archive, self.coverage('HH', 'https://hh.ru/employer/' + eid, 'unavailable', 0,
                        'Публичная employer-проверка: ' + identity['error'], rawPath=identity.get('rawPath'), identitySourceURL=basis['sourceURL']))
                    continue
                employer = identity.get('json', {})
                if host(employer.get('site_url', '')) == host(company['website']) and not basis.get('sharedDomainNotUnitVerified'):
                    basis['basis'] = 'employerID'
                    basis['verifiedWebsite'] = employer['site_url']
                found = 0
                stop = 'pageLimit'
                for page_no in range(self.args.hh_pages):
                    endpoint = 'https://api.hh.ru/vacancies?' + urlencode({'employer_id': eid, 'per_page': 100, 'page': page_no, 'date_from': self.args.window_start})
                    result = self.fetch(endpoint)
                    if result.get('error'):
                        stop = result['error']
                        break
                    data = result.get('json', {})
                    rows = data.get('items', [])
                    found += len(rows)
                    for row in rows:
                        vacancy_ids[str(row['id'])] = dict(basis, employerID=eid)
                    if not rows or page_no + 1 >= data.get('pages', 1):
                        stop = 'searchEnd'
                        break
                self.upsert_coverage(archive, self.coverage('HH', 'https://hh.ru/employer/' + eid, 'unavailable' if self.hh_blocked else 'partial', found,
                    f'Текущая публичная выдача: {found} записей; stop={stop}. Полный шестимесячный архив не доступен этой выдачей.'))
            fetched = 0
            for vid, basis in list(vacancy_ids.items())[:self.args.hh_details]:
                if self.hh_blocked:
                    break
                result = self.fetch('https://api.hh.ru/vacancies/' + vid)
                if result.get('error'):
                    self.upsert_coverage(archive, self.coverage('HH', 'https://hh.ru/vacancy/' + vid, 'unavailable', 0,
                        'Детали вакансии недоступны: ' + result['error'] + '. Это не подтверждает закрытие/найм.', rawPath=result.get('rawPath')))
                    continue
                row = result.get('json', {})
                body = text_html(row.get('description', ''))
                if not body or not row.get('name'):
                    continue
                employer = row.get('employer', {})
                if basis.get('employerID') and str(employer.get('id')) != str(basis['employerID']):
                    relationship = 'candidate'
                else:
                    relationship = 'employerID' if basis.get('basis') in TRUSTED else 'candidate'
                doc = self.document(company, 'HH', row.get('alternate_url') or 'https://hh.ru/vacancy/' + vid,
                    row['name'], body, relationship, basis['sourceURL'], row.get('published_at'), jobTitle=row['name'],
                    sourceID=vid, employerID=str(employer.get('id', '')), salary=json.dumps(row.get('salary'), ensure_ascii=False) if row.get('salary') else None,
                    location=row.get('area', {}).get('name'), employment=row.get('employment', {}).get('name'),
                    status='archived' if row.get('archived') else 'active', rawPath=result.get('rawPath'),
                    retrievedAt=result.get('retrievedAt', now()), collectionPhase='hh')
                self.annotate(doc, explicit=True)
                self.upsert_doc(archive, doc)
                fetched += 1
            self.upsert_coverage(archive, self.coverage('HH', company['website'] + '#hh-details', 'unavailable' if self.hh_blocked else 'partial', fetched,
                f'Детальные публикации: {fetched}. Лимит {self.args.hh_details}; найдено URL {len(vacancy_ids)}. Статус active/archived — наблюдение на момент ответа API.'))

    def review_phase(self, company, archive):
        """Offline quality pass, written to --output-dir (normally hiring/reviewed)."""
        decisions = []
        public = load(self.args.web_dir / 'companies' / (company['id'] + '.json'), {})
        shared = bool(public.get('sharedOfficialDomain') or public.get('organizationScope') == 'sharedDomainNotUnitVerified')
        for doc in archive['documents']:
            before = {k: doc.get(k) for k in ('kind', 'hiringDisposition', 'jobTitle', 'relationship', 'publishedAt')}
            explicit = bool(doc.get('schemaJobPostingCount')) or doc.get('source') == 'HH'
            soup = None
            if doc.get('source') == 'Сайт' and doc.get('rawPath'):
                receipt_path = Path(doc['rawPath'])
                receipt = load(receipt_path, {})
                html = receipt.get('html', '')
                if not html and receipt.get('htmlPath'):
                    raw = Path(receipt['htmlPath'])
                    paths = [raw] if raw.is_absolute() else [self.args.web_dir / raw, receipt_path.parent / raw]
                    for path in paths:
                        if path.exists():
                            try:
                                html = gzip.decompress(path.read_bytes()).decode('utf-8') if path.suffix == '.gz' else path.read_text()
                            except (OSError, UnicodeError):
                                html = ''
                            break
                if html:
                    soup = BeautifulSoup(html, 'html.parser')
            if shared and doc.get('relationship') in TRUSTED:
                doc['relationship'] = 'candidate'
                doc['identityCaveat'] = 'Общий домен/канал нескольких подразделений; найм конкретного подразделения не установлен.'
            if doc.get('source') == 'Сайт':
                types = []
                if soup:
                    for script in soup.select('script[type="application/ld+json"]'):
                        types.extend(re.findall(r'"@type"\s*:\s*"([^"]+)"', script.string or script.get_text()))
                doc['contentSchemaTypes'] = sorted(set(types))
                direct_title = bool(re.search(r'^(?:вакансия|ищем|требуется|требуются|работа\s+в\b|работа\s+у\s+нас)', compact(doc['title']), re.I))
                reason = None
                if not explicit and (EDITORIAL_PATH.search(urlparse(doc['url']).path)
                    or any(t in ('Article', 'BlogPosting', 'NewsArticle') for t in types) and not direct_title
                    or re.search(r'^(?:как|что|почему|зачем|чем|где)\b|гайд|парсю\s+ваканси|что\s+реально\s+нужно\s+знать|секреты\s+.*резюме|^(?:онлайн[- ]?)?курс\b', doc['title'], re.I)
                    or re.search(r'/career-paths/|/sections/career|/soiskatelyam/', urlparse(doc['url']).path)):
                    reason = 'EditorialOrTrainingContentNotEmployerHiring'
                if not explicit and (PLACEMENT.search(compact(doc['text'])) or CAREER_ASSISTANCE.search(compact(doc['text'])) or host(doc['url']) == 'career.htmlacademy.ru'):
                    reason = 'GraduatePlacementServiceNotEmployerHiring'
                if host(doc['url']) in ('career.habr.com', 'geekjob.ru', 'finder.vc', 'zarplata.ru', 'rabota.ru') and urlparse(doc['url']).path.rstrip('/') in ('', '/vacancies', '/jobs'):
                    reason = 'GenericJobBoardNotCompanyEmployer'
                if reason:
                    doc.update(kind='page', hiringDisposition='excluded', jobTitle=None, contacts=[], contact=None,
                        classification={'kind': 'post', 'quote': '', 'jobTitle': None, 'reason': reason})
                else:
                    if not explicit and soup:
                        title_roles = [compact(h.get_text(' ', strip=True)) for h in soup.select('h1,h2,h3')]
                        title_roles = [h for h in title_roles if len(h) < 180 and re.match(r'^(?:senior\s+|junior\s+|ведущий\s+|старший\s+)?' + ROLE, h, re.I)
                                       and not re.search(r'курс|професси|выпускни|как\b', h, re.I)]
                        sections = len(re.findall(r'обязанности|требования|условия|чем предстоит заниматься|что нужно делать|что мы предлагаем|что вы будете делать', doc['text'], re.I))
                        direct_path = bool(re.search(r'/(?:vacanc[^/]*|vakans[^/]*|jobs?|career[^/]*|rabota)(?:/|$)', urlparse(doc['url']).path, re.I))
                        if title_roles and sections >= 2 and (direct_path or direct_title) and not NEGATIVE.search(doc['text']):
                            explicit = True
                            doc['jobTitle'] = title_roles[0]
                    self.annotate(doc, explicit=explicit)
                    if doc['kind'] == 'vacancy' and not doc.get('publishedAt') and soup:
                        # A date immediately after the vacancy H1 is source evidence;
                        # Article metadata and footer dates are deliberately ignored.
                        h1 = soup.find('h1')
                        heading = compact(h1.get_text(' ', strip=True)) if h1 else ''
                        body = compact(doc['text'])
                        where = body.find(heading) if heading and len(heading) > 10 else -1
                        window = body[where + len(heading):where + len(heading) + 95] if where >= 0 else ''
                        months = {'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4, 'мая': 5, 'июня': 6, 'июля': 7, 'августа': 8, 'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12}
                        match = re.match(r'^\s*(\d{1,2})\s+(' + '|'.join(months) + r')\s+(20\d{2})\b', window, re.I)
                        if match:
                            try:
                                date = dt.datetime(int(match[3]), months[match[2].lower()], int(match[1]), tzinfo=MOSCOW).isoformat(timespec='seconds')
                                doc['publishedAt'] = date
                                doc['dateSemantics'] = 'Visible date immediately after vacancy H1; no footer/Article date fallback.'
                                doc['dateEvidence'] = {'quote': heading + ' ' + match.group(0).strip(), 'sourceURL': doc['url']}
                            except ValueError:
                                pass
                        if re.search(r'вакансия\s+в\s+архиве|вакансия\s+закрыта', doc['text'][:4000], re.I):
                            doc['status'] = 'archived'
            else:
                self.annotate(doc, explicit=explicit)
            manual = self.manual_reviews.get(canonical(doc['url']))
            text_hash = hashlib.sha256(doc['text'].encode()).hexdigest()
            if manual and manual.get('textSHA256') == text_hash:
                action = manual['action']
                doc['relevanceReview'] = manual
                if action == 'exclude':
                    doc.update(kind='page' if doc['source'] == 'Сайт' else 'post', hiringDisposition='excluded', jobTitle=None, contacts=[], contact=None,
                               classification={'kind': 'post', 'quote': manual.get('quote', ''), 'jobTitle': None, 'reason': manual['reason']})
                elif action == 'uncertain':
                    doc['relationship'] = 'candidate'
                    doc['identityCaveat'] = manual['reason']
                    doc['contacts'] = []
                    doc['contact'] = None
                elif action == 'confirm':
                    doc['jobTitle'] = manual.get('jobTitle', doc.get('jobTitle'))
                    self.annotate(doc, explicit=bool(doc.get('jobTitle')))
                    if manual.get('verifiedEmployerIdentity'):
                        doc['relationship'] = 'reviewedIdentity'
                        doc['identityCaveat'] = None
                        doc['contacts'] = extract_contacts(doc)
                        doc['contact'] = '; '.join(c['address'] for c in doc['contacts']) or None
                    doc['relevanceReview'] = manual
            for contact in doc.get('contacts', []):
                contact['publishedAt'] = doc.get('publishedAt')
            if doc.get('schemaJobPostingCount') and doc.get('publishedAt'):
                doc['dateSemantics'] = 'Дата из структурированных данных JobPosting.datePosted; видимая дата публикации на странице отдельно не установлена.'
            quote = doc.get('classification', {}).get('quote')
            if doc.get('kind') == 'vacancy':
                if soup:
                    h1 = soup.find('h1')
                    heading = compact(h1.get_text(' ', strip=True)) if h1 else ''
                    if heading and re.search(ROLE, heading, re.I) and not EDUCATION.search(heading):
                        pattern = r'\s+'.join(re.escape(x) for x in heading.split())
                        match = re.search(pattern, doc['text'], re.I)
                        if match:
                            doc['jobTitle'] = heading[:180]
                            quote = doc['text'][match.start():match.start() + 700]
                doc['hiringQuote'] = quote if quote and quote in doc['text'] else None
            else:
                doc['hiringQuote'] = None
            doc['reviewedAt'] = now()
            after = {k: doc.get(k) for k in before}
            if before != after:
                decisions.append({'url': doc['url'], 'before': before, 'after': after, 'reason': doc.get('classification', {}).get('reason'),
                                  'textSHA256': hashlib.sha256(doc['text'].encode()).hexdigest()})
        archive['reviewedAt'] = now()
        archive['reviewDecisions'] = decisions

    def bucket(self, doc):
        if doc.get('kind') != 'vacancy' or doc.get('hiringDisposition') == 'excluded':
            return 'context'
        trusted = doc.get('relationship') in TRUSTED and doc.get('quality') == 'fullText' and canonical(doc.get('url'))
        if not trusted:
            return 'uncertain' if not iso(doc.get('publishedAt')) or self.in_window(doc.get('publishedAt')) else 'outsideWindow'
        if not iso(doc.get('publishedAt')):
            return 'undated'
        return 'dated' if self.in_window(doc['publishedAt']) else 'outsideWindow'

    def run_company(self, company):
        path = self.out / 'archives' / ('hiring-' + company['id'] + '-' + self.end[:10].replace('-', '') + '.json')
        archive = ((load(self.args.review_input_dir / path.name) if self.args.review_input_dir else None) or load(path)) or dict(version=1, id=path.stem, companyID=company['id'], companyName=company['name'], provider='Hiring',
            windowStart=self.args.window_start, windowEnd=self.end, collectedAt=now(), documents=[], coverage=[], notes=[
                'Найденные публичные размещения, не полная история найма и не число нанятых людей.',
                'Дата получения не заменяет дату публикации. Датированные, недатированные и непроверенные объявления считаются отдельно.',
                'Telegram/VK переиспользованы локально; повторных запросов к этим сетям сборщик вакансий не делает.'])
        for phase in self.args.phases.split(','):
            try:
                getattr(self, phase + '_phase')(company, archive)
            except Exception as exc:
                archive.setdefault('errors', []).append({'phase': phase, 'type': type(exc).__name__, 'message': str(exc)[:250]})
                self.upsert_coverage(archive, self.coverage(phase, company['website'] + '#' + phase, 'unavailable', 0, 'Collector error: ' + type(exc).__name__))
            archive['collectedAt'] = now()
            archive['windowStart'], archive['windowEnd'] = self.args.window_start, self.end
            save(path, archive)
        unique = {}
        for doc in archive['documents']:
            key = canonical(doc['url'])
            old = unique.get(key)
            if old is None or (doc['relationship'] in TRUSTED, doc['quality'] == 'fullText', len(doc['text'])) > (old['relationship'] in TRUSTED, old['quality'] == 'fullText', len(old['text'])):
                unique[key] = doc
        archive['documents'] = list(unique.values())
        buckets = defaultdict(list)
        contacts = {}
        for doc in archive['documents']:
            bucket = self.bucket(doc)
            doc['hiringBucket'] = bucket
            buckets[bucket].append(doc['id'])
            for contact in doc.get('contacts', []):
                if bucket not in ('dated', 'undated'):
                    continue
                key = contact['id']
                current = contacts.setdefault(key, dict(contact, sourceURLs=[], evidence=[]))
                if contact['sourceURL'] not in current['sourceURLs']:
                    current['sourceURLs'].append(contact['sourceURL'])
                    current['evidence'].append({'url': contact['sourceURL'], 'quote': contact['sourceQuote'], 'publishedAt': contact['publishedAt'], 'hiringBucket': bucket})
        archive['metrics'] = {k: len(buckets[k]) for k in ('dated', 'undated', 'uncertain', 'outsideWindow', 'context')}
        archive['metrics']['publicHiringContacts'] = len(contacts)
        requisitions = {}
        for doc in archive['documents']:
            if doc['hiringBucket'] not in ('dated', 'undated'):
                continue
            for metadata in doc.get('jobPostingMetadata', []):
                identifier = metadata.get('identifier')
                value = identifier.get('value') if isinstance(identifier, dict) else identifier
                if value:
                    key = 'schema:' + str(value)
                    row = requisitions.setdefault(key, {'id': key, 'basis': 'JobPosting.identifier', 'sourceURLs': [], 'publishedAtValues': [], 'companyID': company['id']})
                    row['sourceURLs'].append(doc['url'])
                    if metadata.get('datePosted'):
                        row['publishedAtValues'].append(metadata['datePosted'])
        archive['identifiedRequisitions'] = list(requisitions.values())
        archive['metrics']['identifiedRequisitions'] = len(requisitions)
        save(path, archive)
        save(self.out / 'companies' / (company['id'] + '.json'), {'companyID': company['id'], 'companyName': company['name'], 'segment': company.get('segment'),
            'archivePath': str(path.resolve()), 'windowStart': self.args.window_start, 'windowEnd': self.end, 'buckets': dict(buckets), 'metrics': archive['metrics'], 'contacts': list(contacts.values()),
            'reviewedAt': archive.get('reviewedAt'), 'collectedAt': archive['collectedAt']})
        with PRINT_LOCK:
            print(json.dumps({'companyID': company['id'], **archive['metrics'], 'coverageChecks': len(archive['coverage']), 'errors': len(archive.get('errors', []))}, ensure_ascii=False), flush=True)
        return archive


def self_test():
    cases = [
        ('Ищем менеджера по продажам', 'Обязанности: работа со входящими заявками. Резюме отправьте hr@example.org', True),
        ('Курс менеджера по продажам', 'Поможем найти работу менеджером. Научим искать вакансии.', False),
        ('Ищем студентов на курс разработчика', 'После курса вакансии и собеседования. Зарплата выпускника 100000', False),
        ('Ищу работу преподавателем', 'Моё резюме и опыт. Ищу работу в команде онлайн-школы.', False),
        ('Вакансия: куратор', 'Ищем куратора в команду. Пишите @hiring_team', True),
        ('Вакансий пока нет', 'Мы не ищем преподавателей. Подпишитесь на обновления.', False),
        ('Как стать программистом', 'Ищем учеников для обучения профессии. Вакансии программистов растут.', False),
        ('Подборка вакансий для дизайнеров', 'Как найти работу и отправить резюме.', False),
        ('Ищем репетитора для моего ребёнка', 'Помогите подготовиться к экзамену.', False),
        ('Требуется преподаватель', 'Зарплата от 80000, обязанности: проведение занятий.', True),
        ('We are hiring a sales manager', 'Join our team, apply by email.', True),
        ('Вакансия администратора', 'Приём входящих заявок. Отклик на hr@example.org.', True),
        ('Курс для HR', 'Обучим специалистов, вакансии в компаниях ждут вас.', False),
        ('Маркетолог', 'Ищем маркетолога в нашу команду. Отклик через форму.', True),
        ('Гайд по профессии', 'Исследуем рынок, чтобы узнать, какие навыки нужны на разных грейдах для развития разработчиков. Вакансии на HH.', False),
        ('Секреты хорошего резюме', 'Если ищут программиста, а вы уже им работали — расскажите в резюме об опыте.', False),
        ('Центр карьеры', 'Если вы компания и ищете разработчика в команду, напишите нам. Опубликуем вашу вакансию.', False),
        ('Размещение вакансий', 'Отдельное спасибо специалистам за помощь в размещении вакансии и коммуникациях с кандидатами.', False),
        ('В AI можно прийти без опыта', 'Для поступления не нужен коммерческий опыт разработчика или аналитика. Вступительное интервью проходит каждый второй кандидат.', False),
        ('Запрос журналиста', 'РБК ищет сотрудников компаний, готовых рассказать, как подработки приносят им доход. Есть вакансии в подборке.', False),
        ('Специалисты на производстве', 'Им нужны специалисты, которые умеют пользоваться СИЗ. После обучения отправьте резюме.', False),
        ('Оказание первой помощи', 'На практике такие знания нужны многим категориям сотрудников: руководителям, рабочим и тренерам. Зарплата зависит от квалификации.', False),
        ('Вальцовщик', 'Именно здесь требуются специалисты высокой квалификации. Высокая зарплата объясняется дефицитом кадров.', False),
        ('Открытая встреча', 'Приглашаем на открытую встречу, где обсудим, какие задачи решает клинический психолог. Ведущий — кандидат медицинских наук.', False),
    ]
    failures = []
    for title, body, expected in cases:
        actual = classify(title, body)['kind'] == 'vacancy'
        if actual != expected:
            failures.append({'title': title, 'expected': expected, 'actual': actual})
    contact_doc = {'companyID': 'test', 'url': 'https://t.me/examplechannel/12', 'text': 'Резюме присылайте @hrcontact или hr@example.org. Канал @examplechannel. Телефон +7 999 123-45-67.', 'retrievedAt': now(), 'relationship': 'officialWebsite'}
    contacts = extract_contacts(contact_doc)
    if any(c['address'] == 'https://t.me/examplechannel' for c in contacts) or len(contacts) != 3:
        failures.append({'contactExtraction': contacts})
    if iso('2026-04-14').utcoffset() != dt.timedelta(hours=3):
        failures.append({'dateTimezone': 'date-only must be Europe/Moscow'})
    for raw in ['http://127.0.0.1/a', 'http://localhost/a', 'file:///tmp/a', 'https://user:pass@example.org']:
        if public_url(raw):
            failures.append({'nonPublicURL': raw})
    if canonical('https://www.example.org/jobs/?utm_source=a#one') != 'https://example.org/jobs':
        failures.append({'canonical': 'tracking/fragments not removed'})
    report = {'passed': not failures, 'classificationCases': len(cases), 'contactCases': 1, 'dateCases': 1, 'urlCases': 5, 'failures': failures}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return not failures


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path)
    p.add_argument('--output-dir', type=Path, default=DEFAULT_OUT)
    p.add_argument('--web-dir', type=Path, default=ROOT / 'output/segments-2026-10-14/web')
    p.add_argument('--web-links-file', type=Path, help='Read only listed companyID/url seeds already present in official-site backlinks.')
    p.add_argument('--web-supplement', action='store_true', help='Keep prior web projection while reading additional observed official links.')
    p.add_argument('--archive-dir', type=Path, action='append', default=[])
    p.add_argument('--review-input-dir', type=Path, help='Read initial archives here before offline review; input receipts remain unchanged.')
    p.add_argument('--decisions-file', type=Path, help='Exact-text SHA-bound manual exclusion/identity decisions.')
    p.add_argument('--company-id', action='append', default=[])
    p.add_argument('--window-start', default=START)
    p.add_argument('--window-end')
    p.add_argument('--phases', default='cached,web,hh', help='Comma-separated cached,web,hh,review; cached/review are offline.')
    p.add_argument('--web-pages', type=int, default=10)
    p.add_argument('--hh-pages', type=int, default=3)
    p.add_argument('--hh-details', type=int, default=100)
    p.add_argument('--workers', type=int, default=3)
    p.add_argument('--delay', type=float, default=0.7)
    p.add_argument('--max-raw-mib', type=int, default=700)
    p.add_argument('--min-free-gib', type=float, default=5)
    p.add_argument('--wait-web-seconds', type=int, default=0, help='Stream completed web company packets for this many seconds before recording missing packets as notStarted.')
    p.add_argument('--self-test', action='store_true')
    args = p.parse_args()
    if args.self_test:
        raise SystemExit(0 if self_test() else 1)
    if not args.input:
        p.error('--input required')
    if not 1 <= args.workers <= 4 or not 1 <= args.web_pages <= 30 or not 1 <= args.hh_pages <= 10 or not 1 <= args.hh_details <= 400:
        p.error('Bounded limits: workers 1..4, web-pages 1..30, hh-pages 1..10, hh-details 1..400')
    if set(args.phases.split(',')) - {'cached', 'web', 'hh', 'review'}:
        p.error('Unknown phase')
    companies = load(args.input, [])
    if isinstance(companies, dict):
        companies = companies.get('companies', [])
    if args.company_id:
        companies = [c for c in companies if c['id'] in args.company_id]
        if len(companies) != len(set(args.company_id)):
            p.error('One or more --company-id values absent from input')
    if not companies or any(not all(c.get(k) for k in ('id', 'name', 'website')) for c in companies):
        p.error('Expected nonempty company list with id,name,website')
    collector = Collector(args)
    save(collector.out / ('run-' + collector.run + '.json'), {'runID': collector.run, 'companyIDs': [c['id'] for c in companies], 'windowStart': args.window_start, 'windowEnd': collector.end, 'phases': args.phases.split(','), 'inputSHA256': hashlib.sha256(args.input.read_bytes()).hexdigest()})
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        if args.wait_web_seconds and 'web' in args.phases.split(','):
            pending = {c['id']: c for c in companies}
            running = {}
            archives = []
            deadline = time.monotonic() + args.wait_web_seconds
            while pending or running:
                timed_out = time.monotonic() > deadline
                ready = [c for c in pending.values() if timed_out or (args.web_dir / 'companies' / (c['id'] + '.json')).exists()]
                for company in ready[:max(0, args.workers * 2 - len(running))]:
                    running[pool.submit(collector.run_company, company)] = company['id']
                    del pending[company['id']]
                done = [f for f in running if f.done()]
                for future in done:
                    archives.append(future.result())
                    del running[future]
                if pending or running:
                    time.sleep(0.3)
        else:
            archives = list(pool.map(collector.run_company, companies))
    totals = Counter()
    by_source = defaultdict(Counter)
    for archive in archives:
        totals.update(archive['metrics'])
        for doc in archive['documents']:
            by_source[doc['source']][doc['hiringBucket']] += 1
    summary = dict(runID=collector.run, generatedAt=now(), companyCount=len(archives), documentCount=sum(len(a['documents']) for a in archives),
        totals=dict(totals), sourceBuckets={k: dict(v) for k, v in by_source.items()}, companiesWithDatedVacancies=sum(a['metrics']['dated'] > 0 for a in archives),
        errorCount=sum(len(a.get('errors', [])) for a in archives), hhStopped=collector.hh_blocked, rawBytes=collector.raw_bytes,
        boundaries={'windowStart': args.window_start, 'windowEnd': collector.end, 'completeHiringHistory': False, 'countsHires': False, 'TelegramNetworkRequests': 0, 'VKNetworkRequests': 0, 'liveWrites': False})
    save(collector.out / ('summary-' + collector.run + '.json'), summary)
    save(collector.out / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
