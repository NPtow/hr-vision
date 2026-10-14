#!/usr/bin/env python3
"""Attach four verified original PDFs to already imported private DSA examples.

prepare writes a private payload from the original files and prior import plan.
plan only reads SQLite. apply requires the unchanged plan and saves a rollback
file before writing. Only em_intake_files, body.files and the operation ledger
change. Source profiles, review history and canonical candidate data stay intact.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys


ORIGINALS = {
    '378713': (44342, 'd81dbedf992a9328a1268d69d3a2ccac353cdcd18d3314c7482cc12a929000ff'),
    '380179': (568928, '25fbd299bb06afe92fc85103fba13e05d9b38256828e71558a4bff20c323fafa'),
    '380455': (548535, 'a06a9100f2829b957a14fd953451c75cf26ecf9553c4624dce58a08fa9aacf09'),
    '380989': (588174, 'b07cf7887a48db474f38fd66d86a2dfdb7d77cb11ebf98b1f50fa50dbccf911c'),
}


def setup(server_dir=None):
    sys.path.insert(0, str(Path(server_dir) if server_dir else Path(__file__).resolve().parents[1] / 'server'))
    global intake, store
    import employer_intake as intake
    import employer_store as store


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def sha(value):
    return hashlib.sha256(value).hexdigest()


def digest(value):
    return sha(dump(value).encode())


def check(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def connect(path, readonly=False):
    db = sqlite3.connect(Path(path).resolve().as_uri() + ('?mode=ro' if readonly else '?mode=rw'), uri=True, timeout=10)
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=10000')
    return db


def prepare(source_dir, import_plan):
    entries = []
    for addition in import_plan['add']:
        cid = addition['sourceCandidateId']
        check(cid in ORIGINALS, 'Unexpected source candidate')
        source_file = (Path(source_dir) / (cid + '.pdf')).resolve()
        content = source_file.read_bytes()
        entries.append({'sourceCandidateId': cid, 'intakeId': addition['id'],
                        'name': 'Резюме — ' + addition['name'] + '.pdf', 'sourcePath': str(source_file),
                        'mime': 'application/pdf', 'size': len(content), 'sha256': sha(content),
                        'data': base64.b64encode(content).decode('ascii')})
    payload = {'schema': 1, 'kind': 'dsa-original-resumes', 'workspaceId': import_plan['workspaceId'],
               'vacancyId': import_plan['vacancyId'], 'importOperationId': import_plan['operationId'],
               'files': entries}
    validate_payload(payload)
    return payload


def validate_payload(payload):
    check(isinstance(payload, dict) and payload.get('schema') == 1 and
          payload.get('kind') == 'dsa-original-resumes', 'Unexpected payload')
    check(all(isinstance(payload.get(key), str) and payload[key].strip()
              for key in ('workspaceId', 'vacancyId', 'importOperationId')), 'Missing exact target scope')
    check(re.fullmatch(r'dsa-examples-[a-f0-9]{24}', payload['importOperationId']), 'Unexpected source import batch')
    entries = payload.get('files')
    check(isinstance(entries, list) and len(entries) == 4 and
          [entry.get('sourceCandidateId') for entry in entries] == list(ORIGINALS),
          'Exactly the four verified source candidates are required in order')
    for entry in entries:
        cid = entry['sourceCandidateId']
        expected_id = sha((payload['importOperationId'] + ':' + cid).encode())[:24]
        check(entry.get('intakeId') == expected_id, 'Intake ID does not match the source import')
        check(isinstance(entry.get('name'), str) and entry['name'].startswith('Резюме — ') and
              entry['name'].endswith('.pdf') and len(entry['name']) <= 240 and
              not any(c in entry['name'] for c in '/\\\r\n\0') and entry.get('mime') == 'application/pdf' and
              isinstance(entry.get('sourcePath'), str) and Path(entry['sourcePath']).is_absolute() and
              Path(entry['sourcePath']).name == cid + '.pdf',
              'Original PDF metadata differs')
        check(type(entry.get('size')) is int and (entry['size'], entry.get('sha256')) == ORIGINALS[cid],
              'Original PDF size or fingerprint differs')
        check(isinstance(entry.get('data'), str) and len(entry['data']) <= 4 * ((intake.MAX_BYTES + 2) // 3),
              'Invalid file payload size')
        content = base64.b64decode(entry['data'], validate=True)
        check(content.startswith(b'%PDF-') and len(content) == entry['size'] and sha(content) == entry['sha256'],
              'Original PDF content differs')
    return entries


def file_id(entry):
    return sha(('dsa-original-cv:' + entry['intakeId'] + ':' + entry['sha256']).encode())[:24]


def target_rows(db, payload):
    workspace, vacancy = payload['workspaceId'], payload['vacancyId']
    store.ensure_open(db, workspace, vacancy)
    result = []
    for entry in validate_payload(payload):
        row = db.execute('''SELECT batch,body FROM em_intake_candidates
          WHERE id=? AND workspace_id=? AND vacancy_id=?''', (entry['intakeId'], workspace, vacancy)).fetchone()
        check(row is not None and row[0] == payload['importOperationId'], 'Target profile is outside the exact import scope')
        body = json.loads(row[1])
        check(body.get('id') == entry['intakeId'] and not body.get('archived') and
              (body.get('preparedProfile') or {}).get('source') == {
                  'kind': 'dsa-panel', 'candidateId': entry['sourceCandidateId'], 'example': True},
              'Stored example identity or status differs')
        check(isinstance(body.get('files'), list), 'Stored file list is invalid')
        check(entry['name'] == 'Резюме — ' + body.get('name', '') + '.pdf',
              'Resume display name does not match the stored profile')
        result.append(row[1])
    return result


def make_plan(db, payload):
    rows = target_rows(db, payload)
    attachments = []
    for entry, raw_body in zip(payload['files'], rows):
        fid = file_id(entry)
        check(db.execute('SELECT 1 FROM em_intake_files WHERE id=?', (fid,)).fetchone() is None,
              'Planned file already exists; use the original plan for an idempotent retry')
        check(all(item.get('id') != fid for item in json.loads(raw_body)['files']), 'File is already linked')
        attachments.append({'intakeId': entry['intakeId'], 'sourceCandidateId': entry['sourceCandidateId'],
                            'fileId': fid, 'name': entry['name'], 'size': entry['size'], 'sha256': entry['sha256'],
                            'beforeBodySha256': sha(raw_body.encode())})
    return {'schema': 1, 'operationId': 'dsa-resumes-' + digest(payload)[:24],
            'workspaceId': payload['workspaceId'], 'vacancyId': payload['vacancyId'],
            'importOperationId': payload['importOperationId'], 'payloadSha256': digest(payload),
            'attachments': attachments, 'totalBytes': sum(entry['size'] for entry in payload['files'])}


def apply(db, payload, plan, rollback_path):
    validate_payload(payload)
    check(plan.get('schema') == 1 and plan.get('payloadSha256') == digest(payload) and
          plan.get('workspaceId') == payload['workspaceId'] and plan.get('vacancyId') == payload['vacancyId'] and
          plan.get('operationId') == 'dsa-resumes-' + digest(payload)[:24], 'Plan and payload differ')
    workspace, vacancy, operation = plan['workspaceId'], plan['vacancyId'], plan['operationId']
    command = {'planSha256': digest(plan), 'payloadSha256': digest(payload)}
    with store._atomic(db):
        store.ensure_open(db, workspace, vacancy)
        check(db.execute('SELECT 1 FROM em_store_commands WHERE workspace_id=? AND request_id=?',
                         (workspace, operation + ':rollback')).fetchone() is None,
              'This attachment was rolled back; an explicit new operation is required')
        previous, command_digest = store._command(db, workspace, operation, 'intake.resume.attach', command)
        if previous is not None:
            return {**previous, 'replayed': True}
        check(make_plan(db, payload) == plan, 'Target changed after the read-only plan')
        stored = db.execute('SELECT COALESCE(SUM(LENGTH(content)),0) FROM em_intake_files').fetchone()[0]
        check(stored + plan['totalBytes'] <= intake.MAX_STORAGE_BYTES, 'Intake file storage limit reached')
        changes = []
        for entry, raw_body in zip(payload['files'], target_rows(db, payload)):
            body = json.loads(raw_body)
            body['files'].append({'id': file_id(entry), 'name': entry['name'], 'size': entry['size']})
            changes.append({'id': entry['intakeId'], 'beforeBody': raw_body, 'afterBody': dump(body),
                            'fileId': file_id(entry), 'name': entry['name'], 'sha256': entry['sha256']})
        backup = {'schema': 1, 'operationId': operation, 'workspaceId': workspace, 'vacancyId': vacancy,
                  'importOperationId': payload['importOperationId'], 'planSha256': digest(plan), 'changes': changes}
        rollback_path = Path(rollback_path)
        if rollback_path.exists():
            check(read_json(rollback_path) == backup, 'Existing rollback file differs')
            check(rollback_path.stat().st_mode & 0o777 == 0o600, 'Rollback file permissions must be 0600')
        else:
            private_json(rollback_path, backup)
        for entry, change in zip(payload['files'], changes):
            db.execute('INSERT INTO em_intake_files VALUES (?,?,?,?,?,?,?,?)',
                       (change['fileId'], entry['intakeId'], entry['name'], 'application/pdf',
                        base64.b64decode(entry['data'], validate=True), '', '{}', 'unsupported'))
            db.execute('''UPDATE em_intake_candidates SET body=?
              WHERE id=? AND workspace_id=? AND vacancy_id=?''',
                       (change['afterBody'], entry['intakeId'], workspace, vacancy))
        result = {'operationId': operation, 'attached': [change['fileId'] for change in changes],
                  'totalBytes': plan['totalBytes'], 'rollbackSha256': digest(backup)}
        store._remember(db, workspace, operation, 'intake.resume.attach', command_digest, result)
        return result


def rollback(db, backup):
    check(backup.get('schema') == 1, 'Unexpected rollback schema')
    workspace, vacancy, operation = backup['workspaceId'], backup['vacancyId'], backup['operationId']
    command = {'rollbackSha256': digest(backup)}
    with store._atomic(db):
        store.vacancy(db, workspace, vacancy, include_archived=True)
        previous, command_digest = store._command(db, workspace, operation + ':rollback', 'intake.resume.rollback', command)
        if previous is not None:
            return {**previous, 'replayed': True}
        completed = db.execute('SELECT kind,result FROM em_store_commands WHERE workspace_id=? AND request_id=?',
                               (workspace, operation)).fetchone()
        check(completed is not None and completed[0] == 'intake.resume.attach' and
              json.loads(completed[1]).get('rollbackSha256') == digest(backup),
              'Rollback does not match the completed attachment')
        for change in backup['changes']:
            row = db.execute('''SELECT batch,body FROM em_intake_candidates
              WHERE id=? AND workspace_id=? AND vacancy_id=?''', (change['id'], workspace, vacancy)).fetchone()
            check(row is not None and row == (backup['importOperationId'], change['afterBody']),
                  'Profile changed after attachment; refusing to overwrite it')
            row = db.execute('''SELECT candidate_id,name,mime,content,text_content,extracted,parse_status
              FROM em_intake_files WHERE id=?''', (change['fileId'],)).fetchone()
            check(row is not None and row[:3] == (change['id'], change['name'], 'application/pdf') and
                  sha(row[3]) == change['sha256'] and row[4:] == ('', '{}', 'unsupported'),
                  'Attached file changed; refusing to remove it')
        for change in backup['changes']:
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=? AND workspace_id=? AND vacancy_id=?',
                       (change['beforeBody'], change['id'], workspace, vacancy))
            db.execute('DELETE FROM em_intake_files WHERE id=? AND candidate_id=?', (change['fileId'], change['id']))
        result = {'operationId': operation, 'detached': [change['fileId'] for change in backup['changes']]}
        store._remember(db, workspace, operation + ':rollback', 'intake.resume.rollback', command_digest, result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-dir')
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--source-dir', required=True); prep.add_argument('--import-plan', required=True)
    prep.add_argument('--output', required=True)
    planning = commands.add_parser('plan')
    planning.add_argument('--database', required=True); planning.add_argument('--payload', required=True)
    planning.add_argument('--output', required=True)
    applying = commands.add_parser('apply')
    applying.add_argument('--database', required=True); applying.add_argument('--payload', required=True)
    applying.add_argument('--plan', required=True); applying.add_argument('--rollback-file', required=True)
    restoring = commands.add_parser('rollback')
    restoring.add_argument('--database', required=True); restoring.add_argument('--rollback-file', required=True)
    args = parser.parse_args(); setup(args.server_dir)
    if args.command == 'prepare':
        payload = prepare(args.source_dir, read_json(args.import_plan)); private_json(args.output, payload)
        print(dump({'payloadSha256': digest(payload), 'fileCount': len(payload['files']),
                    'totalBytes': sum(entry['size'] for entry in payload['files'])}))
        return
    db = connect(args.database, readonly=args.command == 'plan')
    try:
        with db:
            if args.command == 'plan':
                result = make_plan(db, read_json(args.payload)); private_json(args.output, result)
            elif args.command == 'apply':
                result = apply(db, read_json(args.payload), read_json(args.plan), args.rollback_file)
            else:
                result = rollback(db, read_json(args.rollback_file))
        print(dump(result))
    finally:
        db.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
