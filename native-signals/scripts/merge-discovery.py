#!/usr/bin/env python3
"""Validate agent batches, preserve provenance, deduplicate company brands by domain.

Run from any directory. Emits an auditable resource for the native app; never touches
user notes, Telegram collections or credential stores. No network or model calls.
"""
import csv
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'data' / 'discovery'
OUT = ROOT / 'Sources' / 'SignalsCore' / 'Resources' / 'discovered-companies.json'
EXISTING = {'brunoyam.com', 'purpleschool.ru', 'mosdigitals.ru', 'changellenge-education.com',
            'setters.education', 'karpov.courses', 'hexlet.io', 'maths-h.com', 'slurm.io', 'sf.education'}
SEGMENTS = {
    'it': 'IT и данные', 'marketing': 'Маркетинг и продажи',
    'business': 'Бизнес, управление и финансы', 'languages': 'Иностранные языки',
    'exams': 'Школьное образование и экзамены', 'kids': 'Детское развитие и STEM',
    'design': 'Дизайн и архитектура', 'creative': 'Творческие профессии и искусство',
    'psychology': 'Психология, коучинг и педагогика', 'health': 'Здоровье, спорт и медицина',
    'vocational': 'Техническое и профессиональное ДПО', 'lifestyle': 'Ремесло, красота и сервис',
}

def host(url):
    parsed = urlparse(url)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Expected public HTTP(S) URL')
    return parsed.hostname.lower().removeprefix('www.').rstrip('.')

def existing_domain(domain):
    return any(domain == known or domain.endswith('.' + known) for known in EXISTING)

def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)

def merge():
    accepted = {}
    rejected, duplicates, inputs = [], [], []
    overrides_path = BASE / 'review-overrides.json'
    overrides = json.loads(overrides_path.read_text()) if overrides_path.exists() else {}
    excluded = overrides.get('exclude', {})
    aliases = overrides.get('domainAliases', {})
    for path in sorted((BASE / 'inbox').glob('*.json')):
        batch = json.loads(path.read_text())
        if not isinstance(batch, list):
            raise ValueError(f'{path}: expected JSON array')
        inputs.append({'file': path.name, 'records': len(batch)})
        for record in batch:
            try:
                for key in ['name', 'website', 'segment', 'product', 'audience', 'sizeStatus', 'groupNote', 'discoveryQuery', 'discoveryUrl']:
                    if not isinstance(record.get(key), str) or not record[key].strip():
                        raise ValueError(f'missing/empty {key}')
                domain = host(record['website'])
                if domain in overrides.get('websiteOverrides', {}):
                    record['website'] = overrides['websiteOverrides'][domain]
                    domain = host(record['website'])
                domain = aliases.get(domain, domain)
                if domain in excluded:
                    raise ValueError('Reviewer exclusion: ' + excluded[domain])
                if existing_domain(domain):
                    duplicates.append({'name': record['name'], 'domain': domain, 'reason': 'existing seed', 'batch': path.name})
                    continue
                host(record['discoveryUrl'])
                sources = record.get('sources')
                if not isinstance(sources, list) or not sources:
                    raise ValueError('no sources')
                for source in sources:
                    host(source['url'])
                    for key in ['title', 'excerpt', 'checkedAt']:
                        if not isinstance(source.get(key), str) or not source[key].strip():
                            raise ValueError(f'empty source.{key}')
                    if source.get('access') not in ('page_read', 'search_index'):
                        raise ValueError('unknown source access')
                    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', source['checkedAt']):
                        raise ValueError('invalid checkedAt')
                record['id'] = 'discovery-' + re.sub(r'[^a-z0-9]+', '-', domain.encode('idna').decode()).strip('-')
                record['subsegment'] = record.get('subsegment') or record['segment']
                record['segment'] = SEGMENTS.get(path.stem, record['segment'])
                if domain in accepted:
                    old = accepted[domain]
                    duplicates.append({'name': record['name'], 'domain': domain, 'kept': old['name'], 'batch': path.name})
                    if host(old['website']) != domain and host(record['website']) == domain:
                        record['product'] += ' Дополнительное направление: ' + old['product']
                        record['groupNote'] += ' В этой карточке объединено направление «' + old['name'] + '»; юридическая структура отдельно не проверена.'
                        record['sources'].extend(s for s in old['sources'] if s['url'] not in {v['url'] for v in record['sources']})
                        accepted[domain] = record
                        duplicates[-1]['kept'] = record['name']
                        continue
                    seen_sources = {s['url'] for s in old['sources']}
                    old['sources'].extend(s for s in sources if s['url'] not in seen_sources)
                    continue
                accepted[domain] = record
            except (ValueError, KeyError, TypeError) as error:
                rejected.append({'batch': path.name, 'name': record.get('name'), 'reason': str(error)})
    rows = sorted(accepted.values(), key=lambda row: (row['segment'], row['name'].casefold()))
    ids = [r['id'] for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError('ID collision after domain normalization')
    write_json(OUT, rows)
    report = {
        'checkedAt': '2026-10-11', 'scope': 'First broad pass, not exhaustive market coverage',
        'inputs': inputs, 'rawRecords': sum(i['records'] for i in inputs),
        'newCompanies': len(rows), 'existingCompanies': 10, 'totalRealCompanies': 10 + len(rows),
        'segments': dict(sorted(Counter(r['segment'] for r in rows).items())),
        'companiesWithPageRead': sum(any(s['access'] == 'page_read' for s in r['sources']) for r in rows),
        'companiesOnlySearchIndex': sum(all(s['access'] == 'search_index' for s in r['sources']) for r in rows),
        'duplicateRecords': duplicates, 'rejectedRecords': rejected,
        'reviewOverrides': overrides,
    }
    write_json(BASE / 'merge-report.json', report)
    with (BASE / 'companies.csv').open('w', newline='') as handle:
        fields = ['id', 'name', 'website', 'segment', 'product', 'audience', 'sizeStatus', 'groupNote', 'discoveryQuery', 'discoveryUrl', 'sourceUrls', 'sourceAccess']
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({**{f: row.get(f, '') for f in fields[:-2]},
                             'sourceUrls': ' | '.join(s['url'] for s in row['sources']),
                             'sourceAccess': ' | '.join(s['access'] for s in row['sources'])})
    print(json.dumps({k: v for k, v in report.items() if k not in ('duplicateRecords', 'rejectedRecords', 'reviewOverrides')}, ensure_ascii=False, indent=2))
    print(f"Duplicates: {len(duplicates)}; rejected: {len(rejected)}")

if __name__ == '__main__':
    merge()
