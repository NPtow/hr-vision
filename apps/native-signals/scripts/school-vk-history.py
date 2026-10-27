#!/usr/bin/env python3
"""Read public VK walls linked by official school sites; stage history v1.

No credentials, cookies, browser session, TargetHunter, purchases or API keys.
Requires Python 3.9+, requests and beautifulsoup4 (already installed locally).
Public HTML only: metadata in apiPrefetchCache and post content in data-exec.
Never invoke the private RPCs named inside HTML or reuse public page tokens.
Resume reuses immutable gzip HTTP receipts. --refresh begins a new snapshot.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import html
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
START = '2026-04-12T00:00:00+03:00'
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
SKIP = {'share', 'share.php', 'away.php', 'im', 'feed', 'video', 'clips', 'login',
        'join', 'apps', 'video_ext.php', 'widget_community.php', 'ads', 'stories'}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def timestamp(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('ISO8601 dates require a timezone')
    return parsed.timestamp()


def sid(value):
    return hashlib.sha256(value.encode()).hexdigest()[:20]


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def host(url):
    return (urlparse(url).hostname or '').lower().removeprefix('www.')


def vk_url(url):
    if url.startswith('//'):
        url = 'https:' + url
    p = urlparse(url)
    if p.hostname not in ('vk.com', 'vk.ru', 'www.vk.com', 'www.vk.ru', 'm.vk.com', 'm.vk.ru'):
        return None
    name = p.path.strip('/').split('/')[0]
    if not name or name in SKIP or not re.fullmatch(r'[\w.-]+', name, re.ASCII):
        return None
    if name.startswith(('wall', 'topic', 'board', 'photo', 'video', 'clip', 'album', 'app', 'event', 'id')):
        return None
    return 'https://vk.com/' + name


def emit(c, status, message):
    print(json.dumps({'companyID': c['id'], 'companyName': c['name'],
                      'status': status, 'message': message}, ensure_ascii=False), flush=True)


def discover(schools, research):
    """Only actual outgoing anchors on a catalog school hostname prove a link."""
    byhost = {}
    for c in schools:
        byhost.setdefault(host(c['website']), []).append(c)
    links = {c['id']: {} for c in schools}
    checks = {c['id']: [] for c in schools}
    for p in sorted((research / 'pages').glob('*.json')):
        try:
            d = json.loads(p.read_text())
        except (ValueError, OSError):
            continue
        source = d.get('url', d.get('requestedURL', ''))
        matching = byhost.get(host(source), [])
        if not matching:
            continue
        for c in matching:
            checks[c['id']].append({'sourceURL': source, 'receiptPath': str(p.resolve()),
                                   'status': d.get('error', d.get('status', 'unknown'))})
        if not d.get('html'):
            continue
        soup = BeautifulSoup(d['html'], 'html.parser')
        for a in soup.select('a[href]'):
            ancestors = ' '.join(' '.join(z.get('class', [])) for z in list(a.parents)[:5])
            if re.search(r'testimonial|student|review|feedback|otzyv', ancestors, re.I):
                continue
            normalized = vk_url(urljoin(source, a['href']))
            if not normalized:
                continue
            for c in matching:
                links[c['id']].setdefault(normalized.lower(), {
                    'url': normalized, 'sourceURL': source,
                    'receiptPath': str(p.resolve()), 'retrievedAt': d.get('retrievedAt'),
                    'label': a.get_text(' ', strip=True), 'relationship': 'officialWebsite'})
    return links, checks


class HTTP:
    def __init__(self, out, run, delay):
        self.out, self.run, self.delay = out, run, delay
        self.base = out / 'raw' / run
        self.base.mkdir(parents=True, exist_ok=True)
        self.last = 0
        self.hard_block = None

    def get(self, url):
        key = sid(url)
        receipt = self.base / (key + '.json')
        raw = self.base / (key + '.html.gz')
        if receipt.exists():
            value = json.loads(receipt.read_text())
            if value.get('error') == 'AccessBlock':
                self.hard_block = str(receipt.resolve())
            return value, gzip.decompress(raw.read_bytes()).decode('utf-8') if raw.exists() else ''
        if self.hard_block and host(url) in ('vk.com', 'vk.ru'):
            return {'url': url, 'retrievedAt': now(), 'error': 'RunStoppedAfterAccessBlock',
                    'blockReceipt': self.hard_block}, ''
        time.sleep(max(0, self.delay - (time.monotonic() - self.last)))
        value = {'url': url, 'retrievedAt': now(), 'method': 'GET', 'authenticated': False}
        content = ''
        try:
            # New request each time: no cookie jar or session is retained.
            r = requests.get(url, headers={'User-Agent': UA, 'Accept-Language': 'ru,en;q=0.5'},
                             timeout=(8, 25), stream=True)
            chunks, total = [], 0
            for chunk in r.iter_content(65536):
                total += len(chunk)
                if total > 12_000_000:
                    value['error'] = 'BodySizeLimit'
                    break
                chunks.append(chunk)
            body = b''.join(chunks)
            content = body.decode(r.encoding or 'utf-8', errors='replace')
            value.update(httpStatus=r.status_code, finalURL=r.url, bytes=len(body),
                         originalSHA256=hashlib.sha256(body).hexdigest(),
                         contentType=r.headers.get('Content-Type', ''),
                         receiptPath=str(receipt.resolve()), rawPath=str(raw.resolve()))
            r.close()
            if r.status_code >= 400:
                value['error'] = 'HTTP' + str(r.status_code)
            # Inspect visible content, not language strings buried in site JS.
            visible = BeautifulSoup(content, 'html.parser').get_text(' ', strip=True)
            if r.status_code in (429, 403) or re.search(r'провер(ка|ьте).{0,30}(не робот|безопасност)|captcha',
                                                       visible[:5000], re.I):
                value['error'] = 'AccessBlock'
                self.hard_block = str(receipt.resolve())
            if 'Ваш браузер устарел' in visible:
                value['error'] = 'UnsupportedBrowserPage'
            # Raw public responses can incidentally include server-rendered comments.
            # These are never converted into student profiles or indexed documents.
            with gzip.open(raw, 'wb') as f:
                f.write(content.encode('utf-8'))
            value['decodedSHA256'] = hashlib.sha256(content.encode('utf-8')).hexdigest()
        except requests.RequestException as e:
            value['error'] = type(e).__name__
        finally:
            self.last = time.monotonic()
        atomic(receipt, value)
        return value, content


def prefetched(page):
    marker = '"apiPrefetchCache":'
    pos = page.find(marker)
    if pos < 0:
        return []
    try:
        return json.JSONDecoder().raw_decode(page[pos + len(marker):])[0]
    except ValueError:
        return []


def resolve(page, url):
    """No invocation of embedded methods; read public response already in HTML."""
    for value in prefetched(page):
        if value.get('method') == 'groups.getById':
            resp = value.get('response') or {}
            groups = resp.get('groups', []) if isinstance(resp, dict) else resp
            if groups:
                return groups[0]
    # Numeric URLs from official sites are themselves identifiers, not name matches.
    m = re.fullmatch(r'/(?:club|public)(\d+)', urlparse(url).path)
    if m:
        return {'id': int(m.group(1)), 'name': None, 'is_closed': None}
    return None


def post_items(soup, page=''):
    found = []
    for node in soup.select('div[data-testid="post"]'):
        for rendered in node.select('[data-exec]'):
            try:
                value = json.loads(rendered['data-exec']).get('PostContentContainer/init')
            except (ValueError, TypeError):
                continue
            if value and isinstance(value.get('item'), dict):
                item = value['item']
                if item.get('id') is not None and item.get('owner_id') is not None:
                    found.append(item)
                break
    if not found:
        for row in prefetched(page):
            if row.get('method') == 'wall.get' and isinstance(row.get('response'), dict):
                found.extend(x for x in row['response'].get('items', [])
                             if isinstance(x, dict) and x.get('owner_id') is not None)
    return found


def plain(text):
    return BeautifulSoup(html.unescape(text or '').replace('<br>', '\n'), 'html.parser').get_text('\n', strip=True)


def attachment_summary(items):
    result = []
    for a in items or []:
        typ = a.get('type', 'unknown')
        obj = a.get(typ) or {}
        row = {'type': typ}
        for k in ('id', 'owner_id', 'title', 'description', 'url'):
            if k in obj:
                row[k] = obj[k]
        result.append(row)
    return result


def owned(item, groupid):
    if item.get('owner_id') == -groupid and item.get('from_id') == -groupid:
        return True
    # Coauthor posts can have a different canonical owner. Require explicit ID.
    for co in item.get('coowners') or []:
        if co == -groupid or (isinstance(co, dict) and co.get('id') == -groupid):
            return True
    return False


def document(c, item, group, seed, receipt):
    owner, pid = item['owner_id'], item['id']
    txt = plain(item.get('text'))
    quality = 'fullText' if txt else 'metadata'
    if not txt:
        txt = 'Публикация без собственного текста; вложения: ' + ', '.join(
            a.get('type', 'unknown') for a in item.get('attachments') or [])
    copies = item.get('copy_history') or []
    if copies:
        txt += '\n\n[Репост; текст исходного автора приведён отдельно]\n' + '\n\n'.join(
            f"Автор {a.get('from_id', a.get('owner_id'))}: {plain(a.get('text'))}" for a in copies)
    stamp = item.get('date')
    published = dt.datetime.fromtimestamp(stamp, dt.timezone.utc).isoformat() if stamp else None
    return {'id': f'vk:wall{owner}_{pid}', 'companyID': c['id'], 'source': 'VK',
            'sourceID': f'wall{owner}_{pid}', 'url': f'https://vk.com/wall{owner}_{pid}',
            'title': (group.get('name') or c['name']) + ' · публикация', 'text': txt,
            'kind': 'post', 'author': group.get('name') if item.get('from_id') == -group['id'] else str(item.get('from_id')),
            'publishedAt': published, 'retrievedAt': receipt['retrievedAt'], 'quality': quality,
            'relationship': 'officialWebsite', 'relationshipSourceURL': seed['sourceURL'],
            'status': 'unknown', 'provenance': {'receiptPath': receipt.get('receiptPath'),
            'identityReceiptPath': seed.get('receiptPath'), 'collectionURL': receipt['url'],
            'communityID': group['id'], 'ownerID': owner, 'authorID': item.get('from_id'), 'isPinned': bool(item.get('is_pinned')),
            'attachments': attachment_summary(item.get('attachments')),
            'reposts': [{'ownerID': a.get('owner_id'), 'postID': a.get('id'),
                         'authorID': a.get('from_id'), 'publishedEpoch': a.get('date')}
                        for a in copies], 'fullTextScope': 'Post text from public HTML; no image OCR or external article text'}}


def coverage(c, url, status, start, end, message, docs=None, suffix='wall'):
    docs = docs or []
    dates = sorted(x['publishedAt'] for x in docs if x.get('publishedAt'))
    return {'id': 'vk-check-' + sid(c['id'] + url + suffix), 'source': 'VK', 'sourceURL': url,
            'status': status, 'requestedFrom': start, 'requestedTo': end,
            'earliestPublishedAt': dates[0] if dates else None,
            'latestPublishedAt': dates[-1] if dates else None, 'receivedCount': len(docs),
            'note': message, 'checkedAt': now()}


def wall(c, group, seed, http, args, end):
    gid = group['id']
    base = f'https://vk.com/wall-{gid}?own=1'
    docs, seen = {}, set()
    nexturl, traversed, reason, done = base, 0, 'pageLimit', False
    earliest_seen, latest_seen = None, None
    start_epoch = timestamp(args.from_date)
    end_epoch = timestamp(end)
    for n in range(args.max_pages):
        receipt, page = http.get(nexturl)
        if receipt.get('error'):
            reason = receipt['error']
            break
        soup = BeautifulSoup(page, 'html.parser')
        rows = post_items(soup, page)
        if not rows:
            reason = 'NoReadablePostPayload'
            break
        traversed += 1
        fresh = 0
        unpinned = []
        for item in rows:
            key = (item['owner_id'], item['id'])
            if key not in seen:
                fresh += 1
                seen.add(key)
            date = item.get('date')
            if date and not item.get('is_pinned'):
                unpinned.append(date)
                earliest_seen = min(date, earliest_seen or date)
                latest_seen = max(date, latest_seen or date)
            if date and start_epoch <= date <= end_epoch and owned(item, gid):
                doc = document(c, item, group, seed, receipt)
                docs[doc['id']] = doc
        cursor = {'companyID': c['id'], 'groupID': gid, 'url': nexturl,
                  'pagesRead': traversed, 'receivedCount': len(docs), 'checkedAt': now(),
                  'earliestSeenEpoch': earliest_seen, 'latestSeenEpoch': latest_seen}
        atomic(http.out / 'checkpoints' / (c['id'] + '-' + str(gid) + '.json'), cursor)
        emit(c, 'collecting', f'VK {gid}: {traversed} страниц, {len(docs)} публикаций в периоде')
        if not fresh:
            reason = 'RepeatedPage'
            break
        if unpinned and max(unpinned) < start_epoch:
            reason, done = 'ReachedWindowStart', True
            break
        current = int(parse_qs(urlparse(nexturl).query).get('offset', ['0'])[0])
        options = []
        for a in soup.select('a[href]'):
            u = urljoin('https://vk.com/', a['href'])
            p = urlparse(u)
            if p.path != f'/wall-{gid}':
                continue
            q = parse_qs(p.query)
            if q.get('offset', [''])[0].isdigit():
                off = int(q['offset'][0])
                if off > current:
                    options.append((off, u))
        if not options:
            # New public SPA can ignore offset and return initial bootstrap only.
            # Missing HTML pagination therefore never establishes corpus end.
            reason, done = 'NoPublicPaginationLink', False
            break
        nexturl = min(options)[1]
    documents = sorted(docs.values(), key=lambda d: d.get('publishedAt') or '', reverse=True)
    # HTML is a public view, not a guarantee that VK exposed every owner post.
    status = 'partial' if traversed else 'unavailable'
    note = (f'Прочитано {traversed} страниц публичной стены; {len(seen)} объектов просмотрено, '
            f'{len(documents)} записей подтверждённого сообщества в окне. Остановка: {reason}. '
            'Тексты извлечены из публичного HTML; фото, скрытые/удалённые посты, комментарии читателей '
            'и внешние статьи не включены. Полнота всей истории VK не доказана.')
    cov = coverage(c, base, status, args.from_date, end, note, documents)
    cov['traversal'] = {'pages': traversed, 'seenCount': len(seen), 'stopReason': reason,
                        'windowBoundaryReached': done, 'nextURL': None if done else nexturl}
    return documents, cov


def board(c, group, seed, http, args, end):
    """Inspect public discussions list, without bulk collecting student comments."""
    url = f"https://vk.com/board{group['id']}"
    receipt, page = http.get(url)
    if receipt.get('error'):
        return [], coverage(c, url, 'unavailable', args.from_date, end,
                            'Публичный список обсуждений: ' + receipt['error'], suffix='board')
    soup = BeautifulSoup(page, 'html.parser')
    topics = sorted({urljoin('https://vk.com', a['href']).split('?')[0]
                     for a in soup.select('a[href]')
                     if re.match(rf'^/?topic-{group["id"]}_\d+', a['href'])})
    documents = []
    for value in prefetched(page):
        if value.get('method') != 'board.getTopics':
            continue
        for topic in (value.get('response') or {}).get('items', []):
            topic_url = f'https://vk.com/topic-{group["id"]}_{topic["id"]}'
            topics.append(topic_url)
            created = topic.get('created')
            if not created or not (timestamp(args.from_date) <= created <= timestamp(end)):
                continue
            title = plain(topic.get('title'))
            documents.append({'id': f'vk:topic-{group["id"]}_{topic["id"]}', 'companyID': c['id'],
                'source': 'VK', 'sourceID': f'topic-{group["id"]}_{topic["id"]}', 'url': topic_url, 'title': title,
                'text': f'Обсуждение в сообществе: {title}. Показанное VK число комментариев: {topic.get("comments", "не указано")}. '
                        'Сохранены метаданные темы; тексты участников не собирались.',
                'kind': 'discussion', 'author': None,
                'publishedAt': dt.datetime.fromtimestamp(created, dt.timezone.utc).isoformat(),
                'retrievedAt': receipt['retrievedAt'], 'quality': 'metadata',
                'relationship': 'officialWebsite', 'relationshipSourceURL': seed['sourceURL'],
                'status': 'unknown', 'provenance': {'receiptPath': receipt.get('receiptPath'),
                'identityReceiptPath': seed.get('receiptPath'), 'communityID': group['id'],
                'authorAttribution': 'Topic author is not assumed to represent school'}})
    topics = sorted(set(topics))
    cov = coverage(c, url, 'partial' if topics else 'unavailable', args.from_date, end,
                   f'Публичный список обсуждений прочитан; распознано тем: {len(topics)}. '
                   'Содержание тем и комментарии читателей не собирались в этом проходе. '
                   'Ноль распознанных ссылок не доказывает отсутствие обсуждений.', documents, suffix='board')
    cov['topicURLs'] = topics
    cov['receiptPath'] = receipt.get('receiptPath')
    return documents, cov


def collect(c, seeds, checks, http, args, end):
    dest = http.out / 'archives' / ('vk-' + c['id'] + '-20261012.json')
    state = http.out / 'checkpoints' / (c['id'] + '.json')
    if state.exists() and not args.refresh:
        st = json.loads(state.read_text())
        if st.get('runID') == http.run and st.get('completed') and dest.exists():
            emit(c, 'cached', 'Сохранённый проход уже завершён; --refresh создаёт новый снимок')
            return json.loads(dest.read_text())
    result = {'version': 1, 'id': 'vk-' + c['id'] + '-20261012',
              'companyID': c['id'], 'companyName': c['name'], 'provider': 'VK',
              'windowStart': args.from_date, 'windowEnd': end, 'collectedAt': now(),
              'documents': [], 'coverage': [], 'notes': [
                  'Только публичные страницы VK без авторизации, cookies и TargetHunter.',
                  'Связь установлена исходящей ссылкой с домена официального сайта; это не доказательство владения каждой предметной страницей.',
                  'Публикации читателей, личные профили учеников и комментарии читателей не индексировались.'],
              'identityEvidence': list(seeds.values()), 'identityChecks': checks,
              'runID': http.run}
    if not seeds:
        result['coverage'].append(coverage(c, c['website'], 'notStarted', args.from_date, end,
            'В прочитанных страницах официального сайта не найдена исходящая ссылка на допустимое VK-сообщество. '
            'Поисковое совпадение по имени не использовано как идентификация. История VK не проверена.', suffix='identity'))
    observed_groups = set()
    # Metadata-only URL links in footers are preferred to teacher links in cards.
    ordered = sorted(seeds.values(), key=lambda s: (s.get('priority', 50), bool(s.get('label')), s['url']))
    for seed in ordered[:args.max_communities]:
        emit(c, 'resolving', seed['url'])
        receipt, page = http.get(seed['url'])
        group = resolve(page, seed['url']) if not receipt.get('error') else None
        if not group:
            result['coverage'].append(coverage(c, seed['url'], 'unavailable', args.from_date, end,
                'Ссылка официального сайта проверена, но публичные метаданные сообщества не прочитаны: '
                + receipt.get('error', 'NoPublicGroupMetadata') + '. Личная страница не считается сообществом.'))
            continue
        if group['id'] in observed_groups:
            continue
        observed_groups.add(group['id'])
        if group.get('is_closed'):
            result['coverage'].append(coverage(c, seed['url'], 'unavailable', args.from_date, end,
                'VK указывает закрытое/частное сообщество; содержимое не запрашивалось.'))
            continue
        documents, cov = wall(c, group, seed, http, args, end)
        result['documents'].extend(documents)
        result['coverage'].append(cov)
        if args.discussions:
            topic_docs, topic_cov = board(c, group, seed, http, args, end)
            result['documents'].extend(topic_docs)
            result['coverage'].append(topic_cov)
        atomic(dest, result)
    if len(ordered) > args.max_communities:
        for seed in ordered[args.max_communities:]:
            result['coverage'].append(coverage(c, seed['url'], 'notStarted', args.from_date, end,
                f'Подтверждена исходящая ссылка официального сайта; достигнут лимит {args.max_communities} сообществ на школу.'))
    dedup = {d['id']: d for d in result['documents']}
    result['documents'] = sorted(dedup.values(), key=lambda d: d.get('publishedAt') or '', reverse=True)
    result['collectedAt'] = now()
    atomic(dest, result)
    atomic(state, {'companyID': c['id'], 'runID': http.run, 'completed': True,
                   'receivedCount': len(result['documents']), 'checkedAt': now()})
    emit(c, 'complete', f"Сохранено {len(result['documents'])} публикаций; архив {dest}")
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--company-id')
    ap.add_argument('--output-dir', type=Path, default=ROOT / 'output/school-history-2026-10-12/vk')
    ap.add_argument('--research-dir', type=Path, default=ROOT / 'output/schools-research-2026-10-12')
    ap.add_argument('--school-list', type=Path)
    ap.add_argument('--identity-file', type=Path, help='Optional verified official-site link evidence JSON')
    ap.add_argument('--max-pages', type=int, default=15)
    ap.add_argument('--max-communities', type=int, default=6)
    ap.add_argument('--from-date', '--window-start', dest='from_date', default=START)
    ap.add_argument('--window-end', help='ISO8601; defaults to actual collection start')
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--discussions', action='store_true')
    ap.add_argument('--delay', type=float, default=0.6)
    args = ap.parse_args()
    if not 1 <= args.max_pages <= 100 or not 1 <= args.max_communities <= 100 or args.delay < 0.4:
        ap.error('pages/communities must be 1..100 and delay >=0.4s')
    timestamp(args.from_date)
    if args.window_end:
        if timestamp(args.window_end) > time.time() + 60:
            ap.error('window-end cannot be in the future')
        if timestamp(args.window_end) <= timestamp(args.from_date):
            ap.error('window-end must be after window-start')
    schools = json.loads((args.school_list or args.research_dir / 'schools.json').read_text())
    if args.company_id:
        schools = [c for c in schools if c['id'] == args.company_id]
        if not schools:
            ap.error('company-id does not exist in school-list')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    config = args.output_dir / 'run.json'
    if config.exists() and not args.refresh:
        run = json.loads(config.read_text())
    else:
        run = {'runID': dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'),
               'windowStart': args.from_date, 'windowEnd': args.window_end or now(), 'createdAt': now()}
        if config.exists():
            old = json.loads(config.read_text())
            atomic(args.output_dir / 'runs' / (old['runID'] + '.json'), old)
        atomic(config, run)
    if run['windowStart'] != args.from_date:
        ap.error('Existing run has another window start; use --refresh')
    links, checks = discover(schools, args.research_dir)
    identity_path = args.identity_file or args.output_dir / 'identity-cached.json'
    if identity_path.exists():
        identity = json.loads(identity_path.read_text())
        for row in identity.get('schools', []):
            cid = row.get('schoolID')
            if cid not in links:
                continue
            preferred = row.get('preferredAccountURLs', [])
            allowed = {u.lower() for u in row.get('collectionAccountURLs', [])}
            if allowed:
                links[cid] = {k: v for k, v in links[cid].items() if k in allowed}
            for linked in row.get('verifiedLinks', []):
                if linked.get('targetType') not in ('account_alias', 'community_id'):
                    continue
                u = vk_url(linked.get('url', ''))
                evidences = linked.get('evidence', [])
                if not u or not evidences:
                    continue
                if allowed and u.lower() not in allowed:
                    continue
                ev = evidences[0]
                # Only HTML anchors (or prior direct extraction) from official hostname.
                school = next(c for c in schools if c['id'] == cid)
                if host(ev.get('sourceURL', '')) != host(school['website']):
                    continue
                ancestry = ' '.join(a.get('class') or '' for a in ev.get('ancestry', []))
                priority = 10 if any(t in ancestry for t in ('footer', 'social', 'contact')) else 70 if 'teacher' in ancestry else 50
                if u in preferred:
                    priority = preferred.index(u)
                links[cid][u.lower()] = {'url': u, 'sourceURL': ev['sourceURL'],
                    'receiptPath': ev.get('receiptPath'), 'retrievedAt': ev.get('retrievedAt'),
                    'label': ev.get('linkText'), 'relationship': 'officialWebsite', 'priority': priority}
            # Includes explicit negative VK owner IDs from actual official outgoing
            # app/wall/topic links; the relationship remains website-link evidence.
            for rank, linked in enumerate(row.get('rankedAccounts', [])):
                u = vk_url(linked.get('url', ''))
                school = next(c for c in schools if c['id'] == cid)
                if not u or host(linked.get('sourceURL', '')) != host(school['website']):
                    continue
                links[cid][u.lower()] = {'url': u, 'sourceURL': linked['sourceURL'],
                    'receiptPath': linked.get('receiptPath'), 'relationship': 'officialWebsite',
                    'priority': rank, 'identityStatus': linked.get('identityStatus'),
                    'derivedFromURL': linked.get('derivedFromURL')}
    atomic(args.output_dir / 'identity-extracted.json', {
        'retrievedAt': now(), 'schools': [{'companyID': c['id'], 'companyName': c['name'],
            'links': list(links[c['id']].values()), 'checks': checks[c['id']]} for c in schools]})
    http = HTTP(args.output_dir, run['runID'], args.delay)
    results = []
    for c in schools:
        try:
            results.append(collect(c, links[c['id']], checks[c['id']], http, args, run['windowEnd']))
        except Exception as e:
            emit(c, 'error', type(e).__name__ + ': ' + str(e)[:200])
            raise
    summary = {'collectedAt': now(), 'runID': run['runID'], 'companies': len(results),
               'companiesWithDocuments': sum(bool(r['documents']) for r in results),
               'documents': sum(len(r['documents']) for r in results),
               'coverage': dict(Counter(v['status'] for r in results for v in r['coverage'])),
               'windowStart': args.from_date, 'windowEnd': run['windowEnd'],
               'maxPagesPerCommunity': args.max_pages, 'maxCommunitiesPerSchool': args.max_communities,
               'publicHTTPOnly': True, 'noTargetHunter': True}
    atomic(args.output_dir / ('summary-' + args.company_id + '.json' if args.company_id else 'summary.json'), summary)
    print(json.dumps({'status': 'finished', **summary}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
