#!/usr/bin/env python3
"""Stage additive source data; publish only a verified, unchanged receipt.

No network or credentials. A staging folder is a copy of public app stores,
never a symlink into them. Old source versions and board files are retained.
"""
import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

ROOT = Path(__file__).resolve().parents[1]
LIVE = Path.home() / 'Library/Application Support/TG Signals'
STORES = ['public-research', 'source-history', 'source-runs', 'collection-runs', 'boards']


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def source_fingerprint(directory):
    manifest = {str(path.relative_to(directory)): digest(path)
                for store in ['public-research', 'source-history', 'source-runs', 'collection-runs']
                for path in sorted((directory / store).glob('*.json'))}
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def load_companies(run):
    rows = load(run / 'companies.json')
    if not isinstance(rows, list) or not rows:
        raise ValueError('Expected a nonempty company list')
    companies = {}
    for company in rows:
        cid = company.get('id', '')
        if not isinstance(cid, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', cid) or cid in companies:
            raise ValueError('Invalid or duplicate company ID')
        if company.get('isDemo') or company.get('segment') == 'Школьное образование и экзамены':
            raise ValueError('Expected non-demo, non-school company scope')
        companies[cid] = company
    # Do not trust a caller-supplied segment label to authorize writes to schools.
    # This native catalog command does not load or modify app data.
    with tempfile.TemporaryDirectory(prefix='tg-segment-scope-') as temp:
        catalog_path = Path(temp) / 'companies.json'
        subprocess.run([str(ROOT / '.build/catalog-board-io'), 'companies', str(catalog_path), '--scope', 'non-school'],
                       capture_output=True, text=True, check=True)
        allowed = {c['id']: c for c in load(catalog_path)}
    for cid, company in companies.items():
        if cid not in allowed or company.get('segment') != allowed[cid]['segment']:
            raise ValueError('Company is outside the native non-school catalog scope: ' + cid)
    return companies


def safe_publish_path(raw):
    relative = Path(raw)
    if (relative.is_absolute() or len(relative.parts) != 2
            or relative.parts[0] not in ['source-history', 'public-research']
            or not re.fullmatch(r'[A-Za-z0-9_-]+\.json', relative.name)):
        raise ValueError('Unsafe publish path')
    return relative


def checked_path(base, relative):
    path = base
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('Source-store symlinks are not supported: ' + str(path))
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError('Path escapes app data directory')
    return path


def check_receipt_scope(receipt, run, dest):
    companies = load_companies(run)
    if (receipt.get('version') != 2 or receipt.get('runDirectory') != str(run)
            or receipt.get('companyIDs') != sorted(companies)
            or receipt.get('companyScopeSHA256') != digest(run / 'companies.json')):
        raise ValueError('Company scope differs from stage receipt; rebuild stage')
    seen = set()
    for entry in receipt['changes']:
        relative = safe_publish_path(entry['path'])
        if str(relative) in seen:
            raise ValueError('Duplicate publish path')
        seen.add(str(relative))
        path = checked_path(dest, relative)
        document = load(path)
        cid = document.get('companyID')
        if cid not in companies or document.get('version') != 1:
            raise ValueError('Staged publication is outside reviewed company scope: ' + str(relative))
        expected_name = cid + '.json' if relative.parts[0] == 'public-research' else str(document.get('id', '')) + '.json'
        if relative.name != expected_name:
            raise ValueError('Published filename does not match record identity')
    return companies


def validate(args):
    run, dest = args.run_dir.resolve(), args.stage_dir.resolve()
    receipt_path = dest / 'stage-receipt.json'
    receipt_sha = digest(receipt_path)
    scope_sha = digest(run / 'companies.json')
    stage_receipt = load(receipt_path)
    check_receipt_scope(stage_receipt, run, dest)
    before = source_fingerprint(dest)
    result = subprocess.run([str(ROOT / '.build/catalog-board-io'), 'validate-data', str(dest)],
                            capture_output=True, text=True, check=True)
    receipt = json.loads(result.stdout)
    if (receipt.get('status') != 'valid' or receipt.get('errors') or source_fingerprint(dest) != before
            or digest(receipt_path) != receipt_sha or digest(run / 'companies.json') != scope_sha):
        raise ValueError('Native validation failed or staged sources/receipt/scope changed during validation')
    receipt['validatedSourcesSHA256'] = before
    receipt['validatedStageReceiptSHA256'] = receipt_sha
    receipt['validatedCompanyScopeSHA256'] = scope_sha
    save(dest / 'native-validation.json', receipt)
    print(json.dumps(receipt, ensure_ascii=False))


def canonical(raw):
    p = urlsplit(raw)
    query = sorted((k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith('utm_') and k.lower() not in ['gclid', 'yclid', 'trk', 'from', 'hhtmfrom'])
    scheme = 'https' if p.scheme.lower() in ['http', 'https'] else p.scheme.lower()
    return urlunsplit((scheme, p.netloc.lower().removeprefix('www.'), p.path.rstrip('/'), urlencode(query), ''))


def merge_records(records, cid, name):
    docs, channels, contacts, checks, notes = {}, {}, {}, {}, []
    searched = []
    for record in records:
        if record.get('companyID') != cid or record.get('version') != 1:
            raise ValueError('Public record scope/version mismatch: ' + cid)
        searched.append(record.get('searchedAt', record.get('collectedAt', '')))
        for item in record.get('documents', []):
            if item.get('companyID') != cid:
                raise ValueError('Document company mismatch')
            key = canonical(item['url'])
            old = docs.get(key)
            # Preserve full/trusted evidence before considering freshness.
            score = lambda d: (bool(d.get('official')), d.get('quality') == 'fullText', d.get('retrievedAt', ''), len(d.get('text', '')))
            if old is None or score(item) > score(old):
                # A refreshed URL remains the same material in existing boards.
                docs[key] = dict(item, id=old['id']) if old else item
        for item in record.get('channels', []):
            channels[(canonical(item['url']), item['sourceURL'])] = item
        for item in record.get('contacts', []):
            # Different source proofs are retained; projection deduplicates addresses.
            contacts[(item['url'], item['sourceURL'], item.get('purpose'))] = item
        for item in record.get('checks', []):
            checks[(item['url'], item['status'])] = item
        notes.extend(record.get('notes') or [])
    return dict(version=1, companyID=cid, companyName=name, searchedAt=max(searched),
                documents=list(docs.values()), channels=list(channels.values()),
                contacts=list(contacts.values()), checks=list(checks.values()), notes=list(dict.fromkeys(notes)))


def telegram_public(archive):
    docs = []
    for item in archive.get('documents', []):
        d = {k: item[k] for k in ['id', 'companyID', 'url', 'title', 'text', 'retrievedAt', 'quality']}
        d.update(kind='telegramProfile' if item['kind'] == 'profile' else 'telegramPost',
                 provider='Telegram · открытая страница' if item['kind'] == 'profile' else 'Telegram · открытый пост',
                 publishedAt=item.get('publishedAt'), official=item.get('relationship') in ['officialWebsite', 'reviewedIdentity'])
        docs.append(d)
    return dict(version=1, companyID=archive['companyID'], companyName=archive['companyName'],
                searchedAt=archive['collectedAt'], documents=docs,
                channels=archive.get('channels', []), contacts=archive.get('contacts', []),
                checks=[{'url': c.get('sourceURL') or '', 'status': c.get('status', 'partial')} for c in archive.get('coverage', []) if c.get('sourceURL')],
                notes=archive.get('notes', []))


def stage(args):
    run, live, dest = args.run_dir.resolve(), args.live_dir.resolve(), args.stage_dir.resolve()
    if dest == live or live in dest.parents or dest.exists():
        raise ValueError('Stage directory must be new and outside the live app store')
    if shutil.disk_usage(run).free < 5 * 2**30:
        raise ValueError('Less than 5 GiB available; staging stopped')
    companies = load_companies(run)
    dest.mkdir(parents=True)
    copied_hashes = {}
    for store in STORES:
        (dest / store).mkdir()
        checked_path(live, Path(store))
        for path in (live / store).glob('*.json'):
            relative = Path(store) / path.name
            checked_path(live, relative)
            before = digest(path)
            shutil.copy2(path, dest / relative)
            if digest(path) != before or digest(dest / relative) != before:
                raise ValueError('Live file changed while staging: ' + str(relative))
            copied_hashes[str(relative)] = before
    if (live / 'workspace.json').exists():
        checked_path(live, Path('workspace.json'))
        shutil.copy2(live / 'workspace.json', dest / 'workspace.json')
    candidates = set()
    for cid, company in companies.items():
        records = []
        old = dest / 'public-research' / (cid + '.json')
        if old.exists():
            records.append(load(old))
        web = run / 'web/reviewed/companies' / (cid + '.json')
        recovery = run / 'recovery/reviewed/companies' / (cid + '.json')
        vk_contacts = run / 'vk/reviewed/companies' / (cid + '.json')
        tg = run / 'telegram/reviewed/companies' / (cid + '.json')
        hiring = run / 'hiring/reviewed/companies' / (cid + '.json')
        if web.exists():
            records.append(load(web))
        if recovery.exists():
            records.append(load(recovery))
        if vk_contacts.exists():
            records.append(load(vk_contacts))
        if tg.exists():
            records.append(telegram_public(load(tg)))
        if hiring.exists():
            summary = load(hiring)
            if summary.get('companyID') != cid:
                raise ValueError('Hiring contact scope mismatch: ' + cid)
            records.append(dict(version=1, companyID=cid, companyName=company['name'],
                                searchedAt=summary.get('collectedAt') or dt.datetime.now(dt.timezone.utc).isoformat(),
                                contacts=summary.get('contacts', [])))
        if web.exists() or recovery.exists() or vk_contacts.exists() or tg.exists() or hiring.exists():
            relative = Path('public-research') / (cid + '.json')
            save(dest / relative, merge_records(records, cid, company['name']))
            candidates.add(str(relative))
    for source in ['vk/archives', 'hiring/reviewed/archives', 'telegram/reviewed/companies']:
        for path in sorted((run / source).glob('*.json')):
            a = load(path)
            if a.get('companyID') not in companies or a.get('version') != 1 or not re.fullmatch(r'[A-Za-z0-9_-]+', a.get('id', '')):
                raise ValueError('Invalid archive scope/identity: ' + str(path))
            relative = Path('source-history') / (a['id'] + '.json')
            # Date-specific collector snapshots must not overwrite an older run.
            old = dest / relative
            if old.exists() and load(old) != a:
                raise ValueError('Archive ID collision with existing or incoming snapshot: ' + a['id'])
            save(dest / relative, a)
            candidates.add(str(relative))
    entries = []
    for relative in sorted(candidates):
        before, after = copied_hashes.get(relative), digest(dest / relative)
        if digest(live / relative) != before:
            raise ValueError('Live file changed after staging snapshot: ' + relative)
        if before != after:
            entries.append(dict(path=relative, beforeSHA256=before, stagedSHA256=after))
    receipt = dict(version=2, stagedAt=dt.datetime.now(dt.timezone.utc).isoformat(),
                   runDirectory=str(run), liveDirectory=str(live), stageDirectory=str(dest), companyCount=len(companies),
                   companyIDs=sorted(companies), companyScopeSHA256=digest(run / 'companies.json'),
                   schoolWrites=0, changes=entries, nativeValidationRequired=True)
    save(dest / 'stage-receipt.json', receipt)
    print(json.dumps({'stagedFiles': len(entries), 'stageDirectory': str(dest), 'liveWrites': 0}))


def publish(args):
    run, dest, live = args.run_dir.resolve(), args.stage_dir.resolve(), args.live_dir.resolve()
    receipt = load(dest / 'stage-receipt.json')
    validation = load(dest / 'native-validation.json')
    if receipt['liveDirectory'] != str(live) or receipt['stageDirectory'] != str(dest):
        raise ValueError('Receipt directory mismatch')
    if validation.get('status') != 'valid' or validation.get('errors'):
        raise ValueError('Successful native validation receipt required')
    if Path(validation.get('dataDir', '')).resolve() != dest:
        raise ValueError('Native validation belongs to a different staging directory')
    if (validation.get('validatedStageReceiptSHA256') != digest(dest / 'stage-receipt.json')
            or validation.get('validatedCompanyScopeSHA256') != digest(run / 'companies.json')):
        raise ValueError('Stage receipt or company scope changed after native validation')
    if validation.get('validatedSourcesSHA256') != source_fingerprint(dest):
        raise ValueError('Staged sources differ from the native validation snapshot')
    check_receipt_scope(receipt, run, dest)
    # Finish every preflight and every backup before the first live replacement.
    for entry in receipt['changes']:
        relative = safe_publish_path(entry['path'])
        source, target = checked_path(dest, relative), checked_path(live, relative)
        if digest(source) != entry['stagedSHA256'] or digest(target) != entry['beforeSHA256']:
            raise ValueError('Stage or live changed since receipt; rebuild stage: ' + str(relative))
    backup = run / 'publish-backup' / dest.name
    for entry in receipt['changes']:
        relative = safe_publish_path(entry['path']); target = checked_path(live, relative)
        if target.exists():
            saved = backup / relative
            if saved.exists() and digest(saved) != entry['beforeSHA256']:
                raise ValueError('Existing backup differs from the original file: ' + str(relative))
            saved.parent.mkdir(parents=True, exist_ok=True)
            if not saved.exists():
                shutil.copy2(target, saved)
    journal_path = run / ('published-' + dest.name + '.json')
    journal = dict(receipt, status='publishing', publishedFiles=0, completedPaths=[], backupDirectory=str(backup))
    save(journal_path, journal)
    try:
        for entry in receipt['changes']:
            relative = safe_publish_path(entry['path']); target = checked_path(live, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.segment-publish-', suffix='.tmp', delete=False) as handle:
                temp = Path(handle.name)
            try:
                shutil.copy2(checked_path(dest, relative), temp)
                if digest(temp) != entry['stagedSHA256'] or digest(target) != entry['beforeSHA256']:
                    raise ValueError('Stage or live changed during publication: ' + str(relative))
                temp.replace(target)
            finally:
                temp.unlink(missing_ok=True)
            journal['completedPaths'].append(str(relative))
            journal['publishedFiles'] = len(journal['completedPaths'])
            save(journal_path, journal)
    except Exception as error:
        journal.update(status='failed', error=str(error), failedAt=dt.datetime.now(dt.timezone.utc).isoformat())
        save(journal_path, journal)
        raise
    journal.update(status='complete', publishedAt=dt.datetime.now(dt.timezone.utc).isoformat())
    save(journal_path, journal)
    print(json.dumps({'publishedFiles': journal['publishedFiles'], 'schoolWrites': 0}))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['stage', 'validate', 'publish'])
    p.add_argument('--run-dir', type=Path, default=ROOT / 'output/segments-2026-10-14')
    p.add_argument('--live-dir', type=Path, default=LIVE)
    p.add_argument('--stage-dir', type=Path, required=True)
    args = p.parse_args()
    {'stage': stage, 'validate': validate, 'publish': publish}[args.action](args)


if __name__ == '__main__':
    main()
