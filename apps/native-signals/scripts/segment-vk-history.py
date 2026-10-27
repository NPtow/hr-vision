#!/usr/bin/env python3
"""Stage public VK history for non-school catalog segments.

Reuse the audited school HTML extractor without modifying school artifacts.
Identity requires an actual outgoing anchor from an official website receipt.
No sessions, API tokens, private RPCs, TargetHunter or access-block workarounds.
Only public pagination links are followed; partial coverage is always explicit.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
import re
import shutil
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR_VERSION = 'segment-vk-public-html-3'
COMMUNITY_RELATIONSHIP_REVIEWS = {
    ('discovery-aroken-ru', 117682176): ('relatedAuthor',
        'Публичное сообщество «Максим Арокен» связано с сайтом Aroken, но автор пишет также о других проектах. '
        'Нельзя автоматически относить объявления о найме к школе.'),
}
RECOVERY_SITE_REVIEWS = {
    'discovery-imatclass-com': {
        'websites': ['https://xn--80aa3aec8aad.xn--p1ai/'],
        'allowedCommunities': ['https://vk.com/matclassru'],
        'note': 'Маткласс: восстановленный официальный сайт с тем же брендом и опубликованной ссылкой на исходный imatclass.com. Recovery identityEvidence сохранено отдельно.',
    },
    'discovery-mba-sseu-ru': {
        'websites': ['https://www.sseu.ru/biznes-obrazovanie/mba/', 'https://adm.sseu.ru/biznes-obrazovanie/mba/contact'],
        'allowedCommunities': ['https://vk.com/club77206275'],
        'note': 'Контактная страница Высшей школы менеджмента СГЭУ прямо публикует club77206275. Общеуниверситетский footer sfsseu исключён.',
    },
}
spec = importlib.util.spec_from_file_location('school_vk_extractor', ROOT / 'scripts/school-vk-history.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def company_list(path):
    raw = json.loads(path.read_text())
    rows = raw.get('companies', []) if isinstance(raw, dict) else raw
    result = []
    for row in rows:
        c = dict(row)
        c['id'] = c.get('id') or c.get('companyID') or c.get('companyId')
        c['name'] = c.get('name') or c.get('companyName')
        c['website'] = c.get('website') or c.get('officialWebsite')
        if not all(isinstance(c.get(k), str) and c[k] for k in ('id', 'name', 'website')):
            raise ValueError('Each company requires id, name, website')
        segment = str(c.get('segment', c.get('category', '')))
        if 'школьное образование' in segment.casefold():
            raise ValueError('School company is excluded from this run: ' + c['id'])
        result.append(c)
    ids = [c['id'] for c in result]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate company IDs in input')
    return result


def community_link(url):
    if urlparse(url).path.startswith('/page-'):
        return None, None
    direct = base.vk_url(url)
    if direct:
        return direct, None
    p = urlparse(url)
    if base.host(url) not in ('vk.com', 'vk.ru', 'm.vk.com', 'm.vk.ru'):
        return None, None
    # A negative owner ID in an actual official-site post/topic link identifies
    # a community. A positive owner ID never establishes a company community.
    m = re.fullmatch(r'/(?:wall|topic|photo|video|album)-([0-9]+)_[0-9]+/?', p.path)
    if m:
        return 'https://vk.com/club' + m.group(1), url
    m = re.fullmatch(r'/board([0-9]+)/?', p.path)
    if m:
        return 'https://vk.com/club' + m.group(1), url
    return None, None


def discover(companies, research):
    """Read real anchors; don't accept a free-standing claimed identity file."""
    byhost = {}
    for c in companies:
        byhost.setdefault(base.host(c['website']), []).append(c)
    links = {c['id']: {} for c in companies}
    checks = {c['id']: [] for c in companies}
    for path in sorted((research / 'pages').glob('*.json')):
        raw = path.read_bytes()
        try:
            page = json.loads(raw)
        except ValueError:
            continue
        source = page.get('url') or page.get('requestedURL', '')
        matching = byhost.get(base.host(source), [])
        if not matching:
            continue
        receipt_hash = hashlib.sha256(raw).hexdigest()
        for c in matching:
            checks[c['id']].append({'sourceURL': source, 'receiptPath': str(path.resolve()),
                'receiptSHA256': receipt_hash,
                'status': page.get('error', page.get('status', 'unknown'))})
        if page.get('error'):
            continue
        status = page.get('status', page.get('httpStatus'))
        if isinstance(status, int) and status >= 400:
            continue
        html_text = page.get('html')
        if not html_text and page.get('htmlPath'):
            html_path = Path(page['htmlPath'])
            if not html_path.is_absolute():
                html_path = (research if html_path.parts and html_path.parts[0] == 'pages'
                             else path.parent) / html_path
            html_path = html_path.resolve()
            if not html_path.is_relative_to(research.resolve()):
                continue
            try:
                html_bytes = gzip.decompress(html_path.read_bytes()) if html_path.suffix == '.gz' else html_path.read_bytes()
            except (OSError, EOFError):
                continue
            if page.get('htmlSHA256') and hashlib.sha256(html_bytes).hexdigest() != page['htmlSHA256']:
                continue
            html_text = html_bytes.decode('utf-8', errors='replace')
        if not html_text:
            continue
        soup = BeautifulSoup(html_text, 'html.parser')
        for anchor in soup.select('a[href]'):
            ancestors = ' '.join(' '.join(z.get('class', [])) for z in list(anchor.parents)[:5])
            if re.search(r'testimonial|student|review|feedback|otzyv', ancestors, re.I):
                continue
            target = urljoin(source, anchor['href'])
            url, derived = community_link(target)
            if not url:
                continue
            priority = 10 if re.search(r'footer|social|contact', ancestors, re.I) else 70 if 'teacher' in ancestors else 50
            evidence = {'url': url, 'sourceURL': source, 'receiptPath': str(path.resolve()),
                'identityReceiptSHA256': receipt_hash, 'retrievedAt': page.get('retrievedAt'),
                'label': anchor.get_text(' ', strip=True), 'relationship': 'officialWebsite',
                'anchorURL': target, 'priority': priority}
            if derived:
                evidence['derivedFromURL'] = derived
            for c in matching:
                old = links[c['id']].get(url.casefold())
                if old is None or priority < old['priority']:
                    links[c['id']][url.casefold()] = evidence
    return links, checks


def add_recovery(companies, directory, links, checks):
    if not directory.exists():
        return
    extra_links, extra_checks = discover(companies, directory)
    for c in companies:
        cid = c['id']
        links[cid].update(extra_links[cid])
        checks[cid].extend(extra_checks[cid])
        review = RECOVERY_SITE_REVIEWS.get(cid)
        record_path = directory / 'companies' / (cid + '.json')
        if not review or not record_path.exists():
            continue
        for website in review['websites']:
            alias = {**c, 'website': website}
            alias_links, alias_checks = discover([alias], directory)
            allowed = {u.casefold() for u in review['allowedCommunities']}
            for key, seed in alias_links[cid].items():
                if key not in allowed:
                    continue
                seed.update(identityReview=review['note'],
                    originalCatalogWebsite=c['website'],
                    recoveryRecordPath=str(record_path.resolve()),
                    recoveryRecordSHA256=hashlib.sha256(record_path.read_bytes()).hexdigest())
                links[cid][key] = seed
            checks[cid].extend(alias_checks[cid])
        # A university-wide footer must not re-enter a business-school archive.
        if cid == 'discovery-mba-sseu-ru':
            links[cid] = {k: v for k, v in links[cid].items()
                          if k in {u.casefold() for u in review['allowedCommunities']}}


class BoundedHTTP(base.HTTP):
    """Stop new reads at access blocks or a bounded local raw receipt budget."""
    def __init__(self, out, run, delay, max_raw_mib=700, min_free_gib=5):
        super().__init__(out, run, delay)
        self.max_raw_bytes = max_raw_mib * 1024 * 1024
        self.min_free_bytes = min_free_gib * 1024 ** 3
        self.raw_bytes = sum(p.stat().st_size for p in (out / 'raw').rglob('*') if p.is_file())
        self.resource_stop = None
        # A resumed run must not retry another VK URL after an earlier block.
        for path in self.base.glob('*.json'):
            try:
                receipt = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if receipt.get('error') == 'AccessBlock':
                self.hard_block = str(path.resolve())
                break

    def get(self, url):
        receipt_path = self.base / (base.sid(url) + '.json')
        cached = receipt_path.exists()
        if not cached:
            if shutil.disk_usage(self.out).free < self.min_free_bytes:
                self.resource_stop = 'LowDiskSpace'
            if self.raw_bytes >= self.max_raw_bytes:
                self.resource_stop = 'RawBudgetLimit'
            if self.resource_stop:
                return {'url': url, 'retrievedAt': base.now(), 'error': self.resource_stop}, ''
        receipt, page = super().get(url)
        if not cached:
            self.raw_bytes += sum(p.stat().st_size for p in (
                receipt_path, self.base / (base.sid(url) + '.html.gz')) if p.exists())
        return receipt, page


def collect(c, seeds, checks, http, args, end):
    archive_id = 'vk-' + c['id'] + '-' + args.snapshot_date.replace('-', '')
    dest = http.out / 'archives' / (archive_id + '.json')
    state = http.out / 'checkpoints' / (c['id'] + '.json')
    identity_sha = hashlib.sha256(json.dumps({'links': list(seeds.values()), 'checks': checks},
                                           sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if state.exists() and not args.refresh:
        st = json.loads(state.read_text())
        if (st.get('runID') == http.run and st.get('completed') and dest.exists()
                and st.get('identitySHA256') == identity_sha
                and st.get('collectorVersion') == COLLECTOR_VERSION):
            base.emit(c, 'cached', 'Saved snapshot reused; no new network requests')
            return json.loads(dest.read_text())
    result = {'version': 1, 'id': archive_id, 'companyID': c['id'], 'companyName': c['name'],
        'provider': 'VK', 'windowStart': args.from_date, 'windowEnd': end, 'collectedAt': base.now(),
        'documents': [], 'coverage': [], 'notes': [
            'Публичный HTML VK без авторизации, cookies, API-ключей и TargetHunter.',
            'Связь с компанией подтверждена исходящей ссылкой официального сайта; это не утверждение о владении всеми публикациями.',
            'Полнота шестимесячной истории не доказана. Использованы только опубликованные ссылки пагинации.',
            'Комментарии читателей и личные профили не индексировались.'],
        'identityEvidence': list(seeds.values()), 'identityChecks': checks, 'runID': http.run,
        'resolvedCommunities': [], 'collectorVersion': COLLECTOR_VERSION}
    if not seeds:
        result['coverage'].append(base.coverage(c, c['website'], 'notStarted', args.from_date, end,
            'В прочитанных официальных HTML-страницах не найдена ссылка на допустимое VK-сообщество. '
            'Совпадение по названию не использовано как доказательство связи. История VK не проверена.', suffix='identity'))
    observed = set()
    ordered = sorted(seeds.values(), key=lambda s: (s.get('priority', 50), bool(s.get('label')), s['url']))
    for seed in ordered[:args.max_communities]:
        base.emit(c, 'resolving', seed['url'])
        receipt, page = http.get(seed['url'])
        group = base.resolve(page, seed['url']) if not receipt.get('error') else None
        if not group:
            result['coverage'].append(base.coverage(c, seed['url'], 'unavailable', args.from_date, end,
                'Ссылка официального сайта подтверждена; публичные метаданные VK не прочитаны: '
                + receipt.get('error', 'NoPublicGroupMetadata') + '.'))
            continue
        if group['id'] in observed:
            continue
        observed.add(group['id'])
        relationship, identity_note = COMMUNITY_RELATIONSHIP_REVIEWS.get((c['id'], group['id']),
            ('officiallyLinked', 'Исходящая ссылка официального сайта подтверждает связь с сообществом. '
             'Принадлежность отдельного объявления о найме этому работодателю требует проверки.'))
        result['resolvedCommunities'].append({'id': group['id'], 'name': group.get('name'),
            'screenName': group.get('screen_name'), 'website': group.get('site'),
            'isClosed': group.get('is_closed'), 'relationship': relationship,
            'officialSourceURL': seed['sourceURL'], 'officialAnchorURL': seed.get('anchorURL', seed['url']),
            'metadataReceiptPath': receipt.get('receiptPath'),
            'identityScope': 'An outgoing official-site link confirms association, not independent ownership of this community.'})
        if group.get('is_closed'):
            result['coverage'].append(base.coverage(c, seed['url'], 'unavailable', args.from_date, end,
                'VK указывает закрытое/частное сообщество; содержимое не запрашивалось.'))
            continue
        docs, cov = base.wall(c, group, seed, http, args, end)
        for doc in docs:
            doc['relationship'] = relationship
            doc['provenance'].update(communityRelationship=relationship, identityNote=identity_note,
                communityName=group.get('name'), communitySite=group.get('site'),
                companyOwnershipStatus='notIndependentlyVerified')
        result['documents'].extend(docs)
        result['coverage'].append(cov)
        if args.discussions:
            docs, cov = base.board(c, group, seed, http, args, end)
            for doc in docs:
                doc['relationship'] = relationship
                doc['provenance'].update(communityRelationship=relationship, identityNote=identity_note,
                    companyOwnershipStatus='notIndependentlyVerified')
            result['documents'].extend(docs)
            result['coverage'].append(cov)
        base.atomic(dest, result)
    for seed in ordered[args.max_communities:]:
        result['coverage'].append(base.coverage(c, seed['url'], 'notStarted', args.from_date, end,
            f'Официальная исходящая ссылка подтверждена; достигнут лимит {args.max_communities} сообществ на компанию.'))
    result['documents'] = sorted({d['id']: d for d in result['documents']}.values(),
                                key=lambda d: d.get('publishedAt') or '', reverse=True)
    result['collectedAt'] = base.now()
    base.atomic(dest, result)
    base.atomic(state, {'companyID': c['id'], 'runID': http.run, 'completed': True,
        'identitySHA256': identity_sha, 'collectorVersion': COLLECTOR_VERSION,
        'receivedCount': len(result['documents']), 'checkedAt': base.now()})
    base.emit(c, 'complete', f"Сохранено {len(result['documents'])} записей; архив {dest}")
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--company-list', type=Path, default=ROOT / 'output/segments-2026-10-14/companies.json')
    ap.add_argument('--company-id', action='append', help='Repeat to run a bounded pilot')
    ap.add_argument('--research-dir', type=Path, default=ROOT / 'output/segments-2026-10-14/web')
    ap.add_argument('--recovery-dir', type=Path, default=ROOT / 'output/segments-2026-10-14/recovery')
    ap.add_argument('--output-dir', type=Path, default=ROOT / 'output/segments-2026-10-14/vk')
    ap.add_argument('--window-start', '--from-date', dest='from_date', default='2026-04-14T00:00:00+03:00')
    ap.add_argument('--window-end')
    ap.add_argument('--snapshot-date', default='2026-10-14')
    ap.add_argument('--max-pages', type=int, default=25)
    ap.add_argument('--max-communities', type=int, default=8)
    ap.add_argument('--delay', type=float, default=1.0)
    ap.add_argument('--max-raw-mib', type=int, default=700)
    ap.add_argument('--min-free-gib', type=float, default=5.0)
    ap.add_argument('--discussions', action='store_true')
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--discover-only', action='store_true')
    args = ap.parse_args()
    if not 1 <= args.max_pages <= 100 or not 1 <= args.max_communities <= 100 or args.delay < 0.6:
        ap.error('pages/communities must be 1..100 and delay >=0.6s')
    if args.max_raw_mib <= 0 or args.min_free_gib < 5:
        ap.error('raw budget must be positive and minimum free space >=5 GiB')
    dt.date.fromisoformat(args.snapshot_date)
    base.timestamp(args.from_date)
    end = args.window_end or base.now()
    if base.timestamp(end) > time.time() + 60 or base.timestamp(end) <= base.timestamp(args.from_date):
        ap.error('Invalid collection window')
    companies = company_list(args.company_list)
    all_ids = {c['id'] for c in companies}
    if args.company_id:
        if set(args.company_id) - all_ids:
            ap.error('Unknown company-id: ' + ', '.join(sorted(set(args.company_id) - all_ids)))
        companies = [c for c in companies if c['id'] in set(args.company_id)]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    links, checks = discover(companies, args.research_dir)
    add_recovery(companies, args.recovery_dir, links, checks)
    identity = {'collectedAt': base.now(), 'companies': [{'companyID': c['id'], 'companyName': c['name'],
        'links': list(links[c['id']].values()), 'checks': checks[c['id']]} for c in companies]}
    base.atomic(args.output_dir / 'identity-extracted.json', identity)
    if args.discover_only:
        print(json.dumps({'companies': len(companies), 'withOfficialVKLinks': sum(bool(links[c['id']]) for c in companies),
                          'officialLinks': sum(len(v) for v in links.values())}, ensure_ascii=False))
        return
    config = args.output_dir / 'run.json'
    if config.exists() and not args.refresh:
        run = json.loads(config.read_text())
        if run['windowStart'] != args.from_date or run.get('snapshotDate') != args.snapshot_date:
            ap.error('Existing run has another window or snapshot date; use --refresh')
    else:
        run = {'runID': dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'),
               'windowStart': args.from_date, 'windowEnd': end, 'snapshotDate': args.snapshot_date,
               'createdAt': base.now(), 'publicHTTPOnly': True}
        if config.exists():
            old = json.loads(config.read_text())
            base.atomic(args.output_dir / 'runs' / (old['runID'] + '.json'), old)
        base.atomic(config, run)
    http = BoundedHTTP(args.output_dir, run['runID'], args.delay, args.max_raw_mib, args.min_free_gib)
    results = []
    for c in companies:
        results.append(collect(c, links[c['id']], checks[c['id']], http, args, run['windowEnd']))
    docs = [d for result in results for d in result['documents']]
    segment_for = {c['id']: c.get('segment', 'Не указан') for c in companies}
    segment_counts = []
    for segment in sorted(set(segment_for.values())):
        segment_results = [r for r in results if segment_for[r['companyID']] == segment]
        segment_docs = [d for r in segment_results for d in r['documents']]
        segment_counts.append({'segment': segment, 'companies': len(segment_results),
            'companiesWithDocuments': sum(bool(r['documents']) for r in segment_results),
            'documents': len(segment_docs), 'uniqueDocumentURLs': len({d['url'] for d in segment_docs}),
            'fullTextDocuments': sum(d['quality'] == 'fullText' for d in segment_docs),
            'metadataRecords': sum(d['quality'] != 'fullText' for d in segment_docs)})
    summary = {'collectedAt': base.now(), 'runID': run['runID'], 'companies': len(results),
        'companiesWithDocuments': sum(bool(r['documents']) for r in results), 'documents': len(docs),
        'uniqueDocumentURLs': len({d['url'] for d in docs}),
        'fullTextDocuments': sum(d['quality'] == 'fullText' for d in docs),
        'metadataRecords': sum(d['quality'] != 'fullText' for d in docs),
        'bySegment': segment_counts,
        'withOfficialVKLinks': sum(bool(links[c['id']]) for c in companies),
        'coverage': dict(Counter(v['status'] for r in results for v in r['coverage'])),
        'stopReasons': dict(Counter(v.get('traversal', {}).get('stopReason') for r in results
                                   for v in r['coverage'] if v.get('traversal'))),
        'windowStart': args.from_date, 'windowEnd': run['windowEnd'],
        'maxPagesPerCommunity': args.max_pages, 'maxCommunitiesPerCompany': args.max_communities,
        'rawReceiptBytes': http.raw_bytes, 'resourceStop': http.resource_stop,
        'accessBlockReceipt': http.hard_block,
        'publicHTTPOnly': True, 'noTargetHunter': True, 'stagedOnly': True}
    base.atomic(args.output_dir / 'summary.json', summary)
    print(json.dumps({'status': 'finished', **summary}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
