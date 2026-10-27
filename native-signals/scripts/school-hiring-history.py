#!/usr/bin/env python3
"""Bounded public hiring-publication archive; never implies hiring outcomes.

Uses saved official identities, public career HTML/JobPosting, public Telegram
search pagination and HH only if its public endpoint permits access. No tokens,
paid APIs, browser automation, TargetHunter, or CAPTCHA bypass. Dependencies:
requests and beautifulsoup4. Raw HTTP responses are immutable within a run.
"""
import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse, urljoin, urlencode, unquote

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
APP = Path.home() / 'Library/Application Support/TG Signals'
START = '2026-04-12T00:00:00+03:00'
CAREER = re.compile(r'ваканси|карьер|работа\s+у\s+нас|работа\s+в\s+компан|vacanc|career|(?:^|[/_.-])jobs?(?:[/_.-]|$)', re.I)
ROLE = r'преподавател|учител|методист|куратор|наставник|менеджер|маркетолог|редактор|копирайтер|дизайнер|разработчик|аналитик|специалист|руководител|продюсер|ассистент|администратор|тьютор|репетитор|педагог|smm|hr|таргетолог|контент|программис'
HIRING = re.compile(r'(?:ищем|ищу|требуется|требуются|нужен|нужна|нанимаем|открыта\s+вакансия|набор\s+(?:на|в|куратор|преподават))[^.!?\n]{0,150}(?:'+ROLE+r')|(?:'+ROLE+r')[^.!?\n]{0,90}(?:в\s+(?:нашу\s+)?команд|ваканси)|(?:ваканси|резюме|отклик)[\s\S]{0,400}(?:'+ROLE+r')|(?:'+ROLE+r')[\s\S]{0,400}(?:ваканси|резюме|отклик)', re.I)
HH_EMPLOYER = re.compile(r'https?://(?:[a-z0-9-]+\.)?hh\.ru/employer/(\d+)', re.I)
HH_VACANCY = re.compile(r'https?://(?:[a-z0-9-]+\.)?hh\.ru/vacancy/(\d+)', re.I)
TG_NAME = re.compile(r'^https?://(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z][A-Za-z0-9_]{3,31})(?:[/?#]|$)')
LOCK = threading.Lock()
TG_LOCK = threading.Lock()
HH_LOCK = threading.Lock()
TG_LAST = 0.0
HH_BLOCKED = None


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def digest(s):
    return hashlib.sha256(s.encode()).hexdigest()[:24]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    tmp.replace(path)


def load(path, fallback=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return fallback


def host(url):
    return (urlparse(url).hostname or '').lower().removeprefix('www.')


def compact(text):
    return re.sub(r'\s+', ' ', text).strip()


def hiring_kind(text):
    """Separate recruitment context/future hiring from an actual role announcement."""
    s = compact(text)
    roles = re.search(ROLE.replace('|hr|', '|рекрутер|'), s, re.I)
    negated = re.search(r'нет активного набора|пока нет набора|почему сейчас нет|пока нет открытых вакансий|вакансий (?:пока )?нет|предсписок|набор амбассадор', s, re.I)
    if not roles or negated:
        return 'post'
    current = re.search(r'ваканси|ищем|ищу|требуется|требуются|в поиске|нужен|нужна|откры(?:т|ва)[а-я]* набор|набираем|нанимаем|при[её]м (?:заявок|анкет)|заполняй.{0,40}(?:форму|анкету)|(?:форму|анкету).{0,40}отклик', s, re.I)
    return 'vacancy' if current else 'post'


def headline(text):
    return next((compact(line)[:180] for line in text.split('\n') if re.search(r'[A-Za-zА-Яа-яЁё]{3}', line)), compact(text)[:180])


def normalize_archive(archive):
    kept = []
    excluded = archive.get('excludedDocuments', [])
    for doc in archive['documents']:
        if doc.get('source') == 'Telegram':
            doc['kind'] = hiring_kind(doc['text'])
            doc['title'] = headline(doc['text'])
            doc['quality'] = 'snippet' if doc['text'].rstrip().endswith(('…', '...')) else 'fullText'
        review = REVIEWS.get(doc['url'])
        if review and review.get('textSHA256') == hashlib.sha256(doc['text'].encode()).hexdigest():
            doc['relevanceReview'] = {'action': review['action'], 'reason': review.get('reason'), 'textSHA256': review['textSHA256']}
            if review['action'] == 'exclude':
                excluded.append({'url': doc['url'], 'reason': review.get('reason'), 'textSHA256': review['textSHA256'], 'rawPath': doc.get('rawPath')})
                continue
            doc['kind'] = review['action']
            doc['hiringDisposition'] = 'excluded' if review['action'] == 'post' else 'reviewedVacancy'
            if review.get('jobTitle'): doc['jobTitle'] = review['jobTitle']
        kept.append(doc)
    archive['documents'] = kept
    archive['excludedDocuments'] = list({x['url']: x for x in excluded}.values())
    for item in archive['coverage']:
        if item['source'] != 'Telegram' or not item.get('rawPaths'):
            continue
        relevant = [d for d in kept if d.get('rawPath') in item['rawPaths']]
        item['retainedDocumentCount'] = len(relevant)
        item['datedVacancyCount'] = sum(d['kind'] == 'vacancy' and in_window(d.get('publishedAt')) for d in relevant)


def iso(value):
    if not value:
        return None
    try:
        d = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return d.replace(tzinfo=dt.timezone.utc) if d.tzinfo is None else d
    except ValueError:
        return None


def in_window(value):
    d = iso(value)
    return d is not None and iso(ARGS.window_start) <= d <= iso(END)


def clean_url(url):
    p = urlparse(url)
    return p._replace(fragment='').geturl()


def fetch(url):
    global TG_LAST
    path = OUT / 'raw' / RUN / (digest(url) + '.json')
    old = load(path)
    if old is not None:
        old['rawPath'] = str(path.relative_to(OUT))
        return old
    if host(url) == 't.me':
        with TG_LOCK:
            time.sleep(max(0, 0.3 - (time.monotonic() - TG_LAST)))
            TG_LAST = time.monotonic()
    record = {'requestedURL': url, 'retrievedAt': now(), 'runID': RUN}
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; HRVisionPublicResearch/1.0)'}, timeout=(7, 18))
        record.update(url=r.url, status=r.status_code, contentType=r.headers.get('Content-Type', ''))
        # Authentication/captcha bodies are never printed, stored, or worked around.
        blocked = r.status_code in (401, 403, 429) or 'captcha' in r.url.lower()
        if blocked:
            record['error'] = 'accessBlocked' if r.status_code != 429 else 'rateLimited'
        elif r.status_code >= 400:
            record['error'] = 'HTTP' + str(r.status_code)
        elif 'json' in record['contentType']:
            record['json'] = r.json()
        elif 'html' in record['contentType']:
            record['html'] = r.text[:3_000_000]
            record['truncated'] = len(r.text) > 3_000_000
        else:
            record['error'] = 'unsupportedContentType'
    except (requests.RequestException, ValueError) as exc:
        record['error'] = type(exc).__name__
    save(path, record)
    record['rawPath'] = str(path.relative_to(OUT))
    return record


def document(c, source, url, title, text, relationship, relationship_url, published=None,
             kind='vacancy', quality='fullText', status='unknown', **extra):
    return dict(id=source.lower() + ':' + digest(url), companyID=c['id'], source=source,
                sourceID=extra.pop('sourceID', None), url=url, title=title or c['name'],
                text=text, kind=kind, author=extra.pop('author', None), publishedAt=published,
                retrievedAt=extra.pop('retrievedAt', now()), quality=quality,
                relationship=relationship, relationshipSourceURL=relationship_url,
                status=status, **extra)


def coverage(source, url, status, count, note, dates=None, **extra):
    dates = sorted(x for x in (dates or []) if iso(x))
    return dict(id=source.lower() + ':' + digest(url + extra.get('query', '')),
                source=source, sourceURL=url, status=status, requestedFrom=ARGS.window_start,
                requestedTo=END, earliestPublishedAt=dates[0] if dates else None,
                latestPublishedAt=dates[-1] if dates else None, receivedCount=count,
                note=note, checkedAt=now(), **extra)


def texts_from_html(html):
    soup = BeautifulSoup(html, 'html.parser')
    for element in soup(['script', 'style', 'noscript', 'svg', 'nav']):
        element.decompose()
    return (soup.find('body') or soup).get_text('\n', strip=True)


def job_postings(soup):
    def walk(v):
        if isinstance(v, list):
            for e in v:
                yield from walk(e)
        elif isinstance(v, dict):
            typ = v.get('@type', '')
            if typ == 'JobPosting' or isinstance(typ, list) and 'JobPosting' in typ:
                yield v
            for val in v.values():
                if isinstance(val, (dict, list)):
                    yield from walk(val)
    for s in soup.select('script[type="application/ld+json"]'):
        try:
            yield from walk(json.loads(s.string or s.get_text()))
        except (ValueError, TypeError):
            pass


def append_docs(archive, docs):
    by_url = {d['url']: d for d in archive['documents']}
    for d in docs:
        old = by_url.get(d['url'])
        if old is None or len(d['text']) > len(old['text']) or (old.get('quality') != 'fullText' and d.get('quality') == 'fullText'):
            by_url[d['url']] = d
    archive['documents'] = list(by_url.values())


def phase_web(c, public, archive):
    official = host(c['website'])
    aliases = {official}
    todo = []
    hh = {}
    vacancies = {}
    careers = []
    new_channels = []
    saved_pages = [x for x in public.get('documents', []) if x.get('kind') == 'website' and x.get('official')]
    for row in saved_pages:
        if CAREER.search(row.get('url', '') + ' ' + row.get('title', '')):
            careers.append((row['url'], row['url']))
    # Saved raw official pages retain outbound links missing from plain text.
    cached = [x for x in PAGE_CACHE if host(x.get('url', x.get('requestedURL', ''))) == official]
    def links(page_url, html):
        soup = BeautifulSoup(html, 'html.parser')
        for a in soup.select('a[href]'):
            u = clean_url(urljoin(page_url, a['href']))
            label = a.get_text(' ', strip=True)
            if host(u) == official and CAREER.search(u + ' ' + label) and u not in [x[0] for x in careers]:
                careers.append((u, page_url))
            elif CAREER.search(label) and host(u).endswith('.' + official):
                aliases.add(host(u))
                if u not in [x[0] for x in careers]:
                    careers.append((u, page_url))
            for e in HH_EMPLOYER.findall(u):
                hh[e] = {'basis': 'officialWebsite', 'sourceURL': page_url}
            for v in HH_VACANCY.findall(u):
                vacancies[v] = {'basis': 'officialWebsite', 'sourceURL': page_url}
            tg = TG_NAME.match(u)
            if tg and CAREER.search(label):
                new_channels.append(dict(url='https://t.me/' + tg[1], basis='officialWebsite', sourceURL=page_url,
                                         title=label, purpose='hiring', accountType='channel'))
        return soup
    for page in cached:
        links(page.get('url', page['requestedURL']), page.get('html', ''))
    identity = IDENTITIES.get(c['id'], {})
    for entry in identity.get('hiringChannels', []):
        if entry.get('basis') in ('officialWebsiteBacklink', 'officialWebsite'):
            new_channels.append(dict(entry, basis='officialWebsite', accountType='channel', purpose='hiring', title=entry.get('anchorText', c['name'] + ' · вакансии')))
    for entry in identity.get('hhEmployerEvidence', []):
        employer = entry.get('employerID')
        if employer:
            hh.setdefault(employer, {'basis': 'candidate', 'sourceURL': entry.get('sourceURL', entry.get('url')), 'quote': entry.get('quote', '')})
    for entry in identity.get('hhVacancyURLs', []):
        for vid in HH_VACANCY.findall(entry.get('url', '')):
            vacancies.setdefault(vid, {'basis': 'officialWebsite' if entry.get('basis') in ('officialWebsiteBacklink', 'officialWebsite') else 'candidate', 'sourceURL': entry.get('sourceURL', entry['url'])})
    for entry in identity.get('careerURLs', []):
        u = entry.get('url') if isinstance(entry, dict) else entry
        if isinstance(entry, dict) and entry.get('basis') in ('officialWebsiteBacklink', 'officialWebsite') and u:
            aliases.add(host(u))
        if u and u not in [x[0] for x in careers]:
            careers.append((u, entry.get('sourceURL', c['website']) if isinstance(entry, dict) else c['website']))
    if not cached:
        todo.append((c['website'], c['website']))
    todo += careers[:ARGS.web_pages]
    seen = set()
    docs = []
    checks = []
    for u, origin in todo:
        if u in seen or len(seen) >= ARGS.web_pages + 1:
            continue
        seen.add(u)
        page = fetch(u)
        if 'html' not in page:
            checks.append(coverage('Сайт', u, 'unavailable', 0, 'Публичная страница: ' + page.get('error', 'noHTML') + '. Архив старых версий недоступен.', rawPath=page.get('rawPath')))
            continue
        final = page.get('url', u)
        soup = links(final, page['html'])
        if host(final) not in aliases:
            checks.append(coverage('Сайт', u, 'partial', 0, 'Перенаправление на другой домен; связь не подтверждена автоматически.'))
            continue
        title = compact(soup.title.get_text()) if soup.title else c['name']
        jobs = list(job_postings(soup))
        text = texts_from_html(page['html'])
        n = 0
        for job in jobs:
            published = job.get('datePosted')
            if published and not in_window(published):
                continue
            ju = urljoin(final, job.get('url') or '')
            if not ju or ju == final:
                ju = final
            description = texts_from_html(job.get('description', ''))
            if not description:
                continue
            docs.append(document(c, 'Сайт', ju, job.get('title') or title, description, 'officialWebsite', origin,
                                 published, jobTitle=job.get('title'), salary=compact(json.dumps(job['baseSalary'], ensure_ascii=False)) if job.get('baseSalary') else None,
                                 location=compact(json.dumps(job['jobLocation'], ensure_ascii=False)) if job.get('jobLocation') else None,
                                 employment=', '.join(job['employmentType']) if isinstance(job.get('employmentType'), list) else job.get('employmentType'),
                                 retrievedAt=page['retrievedAt'], rawPath=page.get('rawPath')))
            n += 1
        if not jobs and CAREER.search(u + ' ' + title) and (HIRING.search(text) or re.search(r'ваканси|как устроиться|чем предстоит заниматься', text, re.I)):
            docs.append(document(c, 'Сайт', final, title, text[:120000], 'officialWebsite', origin,
                                 kind='page', quality='snippet' if len(text) > 120000 else 'fullText', retrievedAt=page['retrievedAt'], rawPath=page.get('rawPath'),
                                 dateSemantics='Publication date unknown; current career page observation, not a dated publication in the window.'))
            n += 1
        checks.append(coverage('Сайт', final, 'partial' if n else 'noResults', n,
                               f'Текущая публичная HTML-страница; JobPosting: {len(jobs)}. Дата отсутствует — страница не считается датированной публикацией за период. Старые удалённые страницы не восстановлены.', rawPath=page.get('rawPath')))
        if len(todo) < ARGS.web_pages + 1:
            for pair in careers:
                if pair[0] not in seen and pair not in todo:
                    todo.append(pair)
                    if len(todo) >= ARGS.web_pages + 1:
                        break
    if not checks:
        checks.append(coverage('Сайт', c['website'], 'partial', 0,
                               f'Разобраны сохранённые официальные страницы ({len(cached)} HTML, {len(saved_pages)} текстовых); явная ссылка на карьеру не найдена. Полный сайт и исторические версии не обходились.',
                               cachedRetrievedAt=max((x.get('retrievedAt', '') for x in cached), default=None)))
    append_docs(archive, docs)
    archive['coverage'] += checks
    archive['identityEvidence'] = {'hhEmployerIDs': hh, 'hhVacancyIDs': vacancies, 'hiringChannels': new_channels}


def phase_tg(c, public, archive):
    channels = {x['url'].lower(): x for x in public.get('channels', []) if x.get('accountType') == 'channel' and TG_NAME.match(x.get('url', ''))}
    for x in archive.get('identityEvidence', {}).get('hiringChannels', []):
        channels[x['url'].lower()] = x
    verified = [x for x in channels.values() if x.get('basis') in ('officialWebsite', 'reviewedIdentity', 'officialChannelPost', 'domainInBio')]
    verified.sort(key=lambda x: (x.get('purpose') != 'hiring', bool(re.search(r'хими|биологи|русск|матема|физик|истори|обществ|англий|географ', x.get('title', ''), re.I)), x['url']))
    selected = verified[:ARGS.tg_channels]
    if len(verified) > len(selected):
        archive['coverage'].append(coverage('Telegram', c['website'], 'notStarted', 0,
                 f'Ограниченный запуск: {len(verified)-len(selected)} дополнительных связанных каналов не обходились; приоритет каналам найма и школы.', skippedSourceURLs=[x['url'] for x in verified[len(selected):]]))
    candidates = [x for x in channels.values() if x not in verified]
    if candidates:
        archive['coverage'].append(coverage('Telegram', c['website'] + '#candidate-channels', 'notStarted', 0,
                 f'{len(candidates)} поисковых кандидатов не приписаны компании без подтверждения связи.', candidateSourceURLs=[x['url'] for x in candidates]))
    if not selected:
        archive['coverage'].append(coverage('Telegram', c['website'] + '#no-confirmed-channel', 'unavailable', 0,
                 'Подтверждённый открытый канал школы или найма не найден в сохранённых источниках. Это не означает отсутствие вакансий.'))
    for ch in selected:
        name = TG_NAME.match(ch['url'])[1]
        base = 'https://t.me/s/' + name
        relationship = 'officialWebsite' if ch.get('basis') == 'officialWebsite' else 'reviewedIdentity'
        for term in [''] + [x for x in ARGS.tg_terms.split(',') if x]:
            url = base + ('?' + urlencode({'q': term}) if term else '')
            seen = set()
            scanned_dates = []
            scanned_count = 0
            accepted = []
            page_count = 0
            stop = 'pageLimit'
            raw_paths = []
            for _ in range(ARGS.tg_pages if term else ARGS.history_pages):
                if url in seen:
                    stop = 'repeatedCursor'
                    break
                seen.add(url)
                page = fetch(url)
                raw_paths.append(page.get('rawPath'))
                page_count += 1
                if 'html' not in page:
                    stop = page.get('error', 'noHTML')
                    break
                soup = BeautifulSoup(page['html'], 'html.parser')
                posts = soup.select('.tgme_widget_message[data-post]')
                if not posts:
                    stop = 'noMatchingPublicPosts'
                    break
                dates_this_page = []
                for post in posts:
                    ref = post.get('data-post')
                    if not ref or ref.split('/')[0].lower() != name.lower():
                        continue
                    node = post.select_one('.tgme_widget_message_text')
                    d = post.select_one('time[datetime]')
                    published = d.get('datetime') if d else None
                    if published:
                        dates_this_page.append(published)
                        scanned_dates.append(published)
                    scanned_count += 1
                    if node is None or not in_window(published):
                        continue
                    text = node.get_text('\n', strip=True)
                    if not HIRING.search(text):
                        continue
                    for a in node.select('a[href]'):
                        for employer in HH_EMPLOYER.findall(a['href']):
                            archive['identityEvidence']['hhEmployerIDs'][employer] = {'basis': relationship, 'sourceURL': 'https://t.me/' + ref}
                        for vacancy in HH_VACANCY.findall(a['href']):
                            archive['identityEvidence']['hhVacancyIDs'][vacancy] = {'basis': relationship, 'sourceURL': 'https://t.me/' + ref}
                    accepted.append(document(c, 'Telegram', 'https://t.me/' + ref, headline(text), text,
                                  relationship, ch.get('sourceURL', ch['url']), published,
                                  kind=hiring_kind(text),
                                  quality='snippet' if text.rstrip().endswith(('…', '...')) else 'fullText',
                                  sourceID=ref, author=ch.get('title', name), retrievedAt=page['retrievedAt'], rawPath=page.get('rawPath'),
                                  classification='hiringWordingCandidate', identityBasis=ch.get('basis')))
                if dates_this_page and min(iso(x) for x in dates_this_page) < iso(ARGS.window_start):
                    stop = 'lowerDateReachedForSearch'
                    break
                previous = soup.select_one('a.tme_messages_more[data-before]') or soup.select_one('a.tme_messages_more[href*="before="]')
                if not previous:
                    stop = 'searchEnd'
                    break
                url = urljoin(base, previous.get('href', ''))
                if term and 'q=' not in url:
                    url += ('&' if '?' in url else '?') + urlencode({'q': term})
            append_docs(archive, accepted)
            unavailable = stop in ('accessBlocked', 'rateLimited', 'HTTP403', 'HTTP429', 'ReadTimeout', 'ConnectTimeout', 'ConnectionError')
            archive['coverage'].append(coverage('Telegram', base + ('?' + urlencode({'q': term}) if term else ''),
                 'unavailable' if unavailable else 'partial' if scanned_count else 'noResults', len({x['url'] for x in accepted}),
                 f'{"Поиск по открытому каналу, запрос «" + term + "»" if term else "Пагинация открытой ленты"}: {page_count} страниц, {scanned_count} просмотренных сообщений; остановка: {stop}. {"Выборка по формулировке; выдача поиска может пропускать свежие публикации." if term else "Ограниченный обход доступных текстов до даты/лимита."} Медиа и удалённые посты не восстановлены.',
                 scanned_dates, query=term, scannedCount=scanned_count, pageCount=page_count, stopReason=stop, rawPaths=[x for x in raw_paths if x]))
            if unavailable:
                break


def phase_hh(c, archive):
    global HH_BLOCKED
    evidence = archive.get('identityEvidence', {})
    employers = evidence.get('hhEmployerIDs', {})
    vacancy_ids = evidence.get('hhVacancyIDs', {})
    if not employers and not vacancy_ids:
        archive['coverage'].append(coverage('HH', 'https://hh.ru/', 'notStarted', 0,
           'HH employer ID и прямые ссылки на вакансии не найдены в проверенных официальных источниках. Поиск по одному названию не считается подтверждением работодателя; полный исторический архив не доступен публичным поиском.'))
        return
    docs = []
    # Serial access, one global stop on forbidden/rate-limit: no mass retry.
    with HH_LOCK:
        for employer, basis in employers.items():
            endpoint = 'https://api.hh.ru/employers/' + employer
            if HH_BLOCKED:
                archive['coverage'].append(coverage('HH', endpoint, 'unavailable', 0,
                    'Запросы остановлены после ' + HH_BLOCKED + '; обход ограничений не выполнялся. ID связан ссылкой из официального источника.', identitySourceURL=basis['sourceURL']))
                continue
            identity = fetch(endpoint)
            if identity.get('error'):
                if identity.get('status') in (401, 403, 429):
                    HH_BLOCKED = 'HTTP ' + str(identity['status'])
                archive['coverage'].append(coverage('HH', endpoint, 'unavailable', 0,
                    'Проверка публичного employer API: ' + identity.get('error') + '. Архив не получен.', rawPath=identity.get('rawPath'), identitySourceURL=basis['sourceURL']))
                continue
            employer_site = identity.get('json', {}).get('site_url', '')
            if host(employer_site) == host(c['website']):
                basis['basis'] = 'employerID'
                basis['verifiedWebsite'] = employer_site
            elif basis.get('basis') == 'candidate':
                archive['coverage'].append(coverage('HH', endpoint + '#identity', 'partial', 0,
                    'Профиль работодателя доступен, но официальный домен не совпал/не указан; документы остаются identity candidate.', identitySourceURL=basis['sourceURL']))
            result = fetch('https://api.hh.ru/vacancies?' + urlencode({'employer_id': employer, 'per_page': 100, 'page': 0}))
            if result.get('error'):
                if result.get('status') in (401, 403, 429):
                    HH_BLOCKED = 'HTTP ' + str(result['status'])
                archive['coverage'].append(coverage('HH', endpoint, 'unavailable', 0, 'Текущий публичный поиск: ' + result.get('error') + '; полный архив не заявляется.'))
                continue
            rows = result.get('json', {}).get('items', [])
            for row in rows:
                vacancy_ids[row['id']] = dict(basis, employerID=employer)
            archive['coverage'].append(coverage('HH', endpoint, 'partial', len(rows),
                 f'Текущий публичный поиск: получено {len(rows)}, reported found {result.get("json", {}).get("found")}; максимум одна страница. Это не исторический архив.', rawPath=result.get('rawPath')))
        for vid, basis in list(vacancy_ids.items())[:ARGS.hh_details]:
            endpoint = 'https://api.hh.ru/vacancies/' + vid
            if HH_BLOCKED:
                archive['coverage'].append(coverage('HH', endpoint, 'unavailable', 0, 'Детали не запрошены: публичный HH API ранее вернул ' + HH_BLOCKED + '.', identitySourceURL=basis['sourceURL']))
                continue
            result = fetch(endpoint)
            if result.get('error'):
                if result.get('status') in (401, 403, 429):
                    HH_BLOCKED = 'HTTP ' + str(result['status'])
                archive['coverage'].append(coverage('HH', endpoint, 'unavailable', 0, 'Детали вакансии: ' + result.get('error') + '; недоступность не означает закрытие или найм.', rawPath=result.get('rawPath')))
                continue
            row = result.get('json', {})
            if not in_window(row.get('published_at')):
                continue
            employer = row.get('employer', {})
            docs.append(document(c, 'HH', row.get('alternate_url', 'https://hh.ru/vacancy/' + vid), row.get('name'), texts_from_html(row.get('description', '')),
                 ('employerID' if employers.get(employer.get('id'), {}).get('basis') in ('officialWebsite', 'reviewedIdentity', 'employerID') else basis.get('basis', 'candidate')), basis['sourceURL'], row.get('published_at'),
                 status='archived' if row.get('archived') else 'active', sourceID=vid, employerID=employer.get('id'), jobTitle=row.get('name'),
                 salary=json.dumps(row['salary'], ensure_ascii=False) if row.get('salary') else None,
                 location=row.get('area', {}).get('name'), employment=row.get('employment', {}).get('name'), rawPath=result.get('rawPath')))
    append_docs(archive, docs)


def run_company(c):
    dest = OUT / 'archives' / ('hiring-' + c['id'] + '-20261012.json')
    archive = load(dest) if ARGS.resume or ARGS.rerun_phase or ARGS.normalize_only else None
    if not archive:
        archive = dict(version=1, id='hiring-' + c['id'] + '-20261012', companyID=c['id'], companyName=c['name'], provider='Hiring',
              windowStart=ARGS.window_start, windowEnd=END, collectedAt=now(), documents=[], coverage=[], completedPhases=[],
              notes=['Архив найденных публикаций о найме. Не является полной ретроспективой всех вакансий и не доказывает число наймов.',
                     'Telegram: автоматический отбор по явным формулировкам найма; полные исходные тексты сохранены для проверки релевантности.',
                     'Недатированные карьерные страницы сохранены отдельно как kind=page, publishedAt=null; дата проверки не подменяет публикацию.'])
    normalize_archive(archive)
    if ARGS.rerun_phase == 'telegram':
        archive['coverage'] = [x for x in archive['coverage'] if x['source'] != 'Telegram']
        archive['documents'] = [x for x in archive['documents'] if x['source'] != 'Telegram']
        archive['completedPhases'] = [x for x in archive['completedPhases'] if x != 'telegram']
    public = load(ARGS.app_data_dir / 'public-research' / (c['id'] + '.json'), {})
    if not public:
        public = load(ARGS.research_dir / 'companies' / (c['id'] + '.json'), {})
    for phase, handler in [('web', lambda: phase_web(c, public, archive)), ('telegram', lambda: phase_tg(c, public, archive)), ('hh', lambda: phase_hh(c, archive))]:
        if ARGS.normalize_only or phase in archive.get('completedPhases', []):
            continue
        try:
            handler()
            archive.setdefault('completedPhases', []).append(phase)
        except Exception as exc:
            archive['coverage'].append(coverage('Сайт' if phase == 'web' else 'Telegram' if phase == 'telegram' else 'HH', c['website'], 'unavailable', 0, 'Collector error: ' + type(exc).__name__))
            archive.setdefault('errors', []).append({'phase': phase, 'type': type(exc).__name__})
        archive['collectedAt'] = now()
        archive['windowEnd'] = END
        save(dest, archive)
    normalize_archive(archive)
    save(dest, archive)
    with LOCK:
        print(json.dumps({'companyID': c['id'], 'documents': len(archive['documents']), 'datedVacancies': sum(x['kind'] == 'vacancy' and in_window(x.get('publishedAt')) for x in archive['documents']), 'coverageChecks': len(archive['coverage']), 'completedPhases': archive['completedPhases']}, ensure_ascii=False), flush=True)
    return archive


def main():
    global ARGS, OUT, RUN, END, PAGE_CACHE, IDENTITIES, REVIEWS
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--company-id')
    p.add_argument('--output-dir', type=Path, default=ROOT / 'output/school-history-2026-10-12/hiring')
    p.add_argument('--school-list', type=Path, default=ROOT / 'output/schools-research-2026-10-12/schools.json')
    p.add_argument('--research-dir', type=Path, default=ROOT / 'output/schools-research-2026-10-12')
    p.add_argument('--app-data-dir', type=Path, default=APP)
    p.add_argument('--identity-file', type=Path)
    p.add_argument('--review-file', type=Path)
    p.add_argument('--refresh', action='store_true', help='Fresh live run; overrides --resume')
    p.add_argument('--window-start', default=START)
    p.add_argument('--tg-pages', type=int, default=4)
    p.add_argument('--history-pages', type=int, default=12, help='Unfiltered Telegram history page bound per channel')
    p.add_argument('--tg-channels', type=int, default=4)
    p.add_argument('--tg-terms', default='ваканс,команду,ищем')
    p.add_argument('--web-pages', type=int, default=4)
    p.add_argument('--hh-details', type=int, default=40)
    p.add_argument('--workers', type=int, default=3)
    p.add_argument('--resume', action='store_true')
    p.add_argument('--rerun-phase', choices=['telegram'])
    p.add_argument('--normalize-only', action='store_true', help='Apply exact-text relevance review locally; no HTTP requests')
    ARGS = p.parse_args()
    if ARGS.refresh:
        ARGS.resume = False
    if ARGS.tg_pages < 1 or ARGS.tg_pages > 12 or ARGS.history_pages < 1 or ARGS.history_pages > 24 or ARGS.tg_channels < 1 or ARGS.tg_channels > 40 or ARGS.workers < 1 or ARGS.workers > 4:
        p.error('Bounded limits: tg-pages 1..12, history-pages 1..24, tg-channels 1..40, workers 1..4')
    OUT = ARGS.output_dir.resolve()
    END = now()
    RUN = END.replace(':', '').replace('-', '') + '-' + digest(str(time.time()))[:6]
    schools = load(ARGS.school_list, [])
    if ARGS.company_id:
        schools = [x for x in schools if x['id'] == ARGS.company_id]
        if not schools:
            p.error('Unknown --company-id')
    PAGE_CACHE = []
    for path in (ARGS.research_dir / 'pages').glob('*.json'):
        row = load(path, {})
        if host(row.get('requestedURL', '')) not in ('t.me', 'telegram.me') and 'html' in row:
            PAGE_CACHE.append(row)
    identity = load(ARGS.identity_file or OUT / 'identity-discovery.json', {})
    IDENTITIES = {x['companyID']: x for x in identity.get('companies', [])} if isinstance(identity, dict) else {}
    review = load(ARGS.review_file or OUT / 'manual-review.json', {})
    REVIEWS = {x['url']: x for x in review.get('decisions', [])}
    with concurrent.futures.ThreadPoolExecutor(max_workers=ARGS.workers) as pool:
        archives = list(pool.map(run_company, schools))
    docs = [x for a in archives for x in a['documents']]
    cov = [x for a in archives for x in a['coverage']]
    summary = dict(runID=RUN, generatedAt=now(), companyCount=len(archives), documentCount=len(docs),
                   datedVacancyPublications=sum(x['kind'] == 'vacancy' and in_window(x.get('publishedAt')) for x in docs),
                   companiesWithDatedVacancies=sum(any(x['kind'] == 'vacancy' and in_window(x.get('publishedAt')) for x in a['documents']) for a in archives),
                   undatedCareerPages=sum(x['kind'] == 'page' for x in docs), sourceCounts={s: sum(x['source'] == s for x in docs) for s in ('Telegram', 'HH', 'Сайт')},
                   coverageChecks=len(cov), rawResponseCount=len(list((OUT / 'raw' / RUN).glob('*.json'))),
                   boundaries={'windowStart': ARGS.window_start, 'windowEnd': END, 'fullHHHistory': False, 'hiringOutcomesKnown': False, 'TelegramSearchOnly': False, 'TelegramHistoryPageBound': ARGS.history_pages})
    save(OUT / ('summary-' + RUN + '.json'), summary)
    save(OUT / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
