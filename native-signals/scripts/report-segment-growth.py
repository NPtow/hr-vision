#!/usr/bin/env python3
"""Compare independently audited app projections, never raw crawler counters."""
import argparse
import csv
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METRICS = {
    'companyMaterialURLPairs': 'Материалы с исходным URL',
    'fullText': 'Полные тексты',
    'datedVacancies': 'Публикации о найме за 6 месяцев',
    'undatedVacancies': 'Публикации о найме без даты',
    'uncertainVacancies': 'Кандидаты на публикации о найме',
    'publishedRoutes': 'Опубликованные рабочие способы связи',
    'profilesWithCompanyClaim': 'Профили с заявленной связью с компанией',
    'contactCandidates': 'Адреса-кандидаты',
    'channels': 'Каналы',
    'chats': 'Чаты',
    'bots': 'Боты',
}


def read(path):
    return json.loads(path.read_text())


def delta(before, after):
    return {key: dict(before=before.get(key, 0), after=after.get(key, 0),
                     addedNet=after.get(key, 0) - before.get(key, 0)) for key in METRICS}


def write_csv(path, rows, metadata):
    headers = metadata + [f'{label} · {phase}' for label in METRICS.values()
                          for phase in ['до', 'после', 'прирост']]
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([row[k] for k in metadata] + [row['delta'][k][phase]
                for k in METRICS for phase in ['before', 'after', 'addedNet']])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, default=ROOT / 'output/segments-2026-10-14')
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    args = parser.parse_args()
    before, after = read(args.before), read(args.after)
    companies = read(args.run_dir / 'companies.json')
    scope = {c['id']: c for c in companies}
    old = {c['companyId']: c for c in before['byCompany']}
    new = {c['companyId']: c for c in after['byCompany']}
    if set(old) != set(scope) or set(new) != set(scope):
        raise ValueError('Before/after must contain exactly the requested company scope')
    rows = [dict(ID=cid, Компания=c['name'], Сегмент=c['segment'],
                 delta=delta(old[cid]['metrics'], new[cid]['metrics'])) for cid, c in scope.items()]
    # Aggregate from the canonical scope, including the pilot as its own group.
    segments = []
    for segment in sorted({c['segment'] for c in companies}):
        ids = [c['id'] for c in companies if c['segment'] == segment]
        sums = lambda data: {key: sum(data[cid]['metrics'].get(key, 0) for cid in ids) for key in METRICS}
        segments.append(dict(Сегмент=segment, Компаний=len(ids), delta=delta(sums(old), sums(new))))
    result = dict(generatedAt=dt.datetime.now(dt.timezone.utc).isoformat(), companyCount=len(companies),
                  beforeAudit=str(args.before.resolve()), afterAudit=str(args.after.resolve()),
                  delta=delta(before['metrics'], after['metrics']),
                  sourcePublicationCountNotOpenings=True, confirmedDecisionMakersNotInferred=True,
                  companies=rows, segments=segments)
    (args.run_dir / 'growth-summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    write_csv(args.run_dir / 'growth-by-company.csv', rows, ['ID', 'Компания', 'Сегмент'])
    write_csv(args.run_dir / 'growth-by-segment.csv', segments, ['Сегмент', 'Компаний'])
    print(json.dumps(result['delta'], ensure_ascii=False))


if __name__ == '__main__':
    main()
