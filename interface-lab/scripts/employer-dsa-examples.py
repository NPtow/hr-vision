#!/usr/bin/env python3
"""Operator-only, scoped import of four existing DSA profiles as private examples.

prepare: reads source GET/CV/transcript files and writes a private payload.
plan: opens SQLite read-only and identifies exact workbook rows to archive.
apply: needs that unchanged plan; saves a private rollback file before the transaction.
rollback: restores the old visibility and archives the new examples, retaining all history.

No cp_* rows, source decisions, consent, messages or invitations are written.
Production use is an explicit operator step, never part of deployment/startup.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys


def setup(server_dir=None):
    sys.path.insert(0, str(Path(server_dir) if server_dir else Path(__file__).resolve().parents[1] / 'server'))
    global intake, store, panel, iso, now
    import employer_intake as intake
    import employer_store as store
    import dsa_panel as panel
    from domain import iso, now


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def raw_digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def check(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def connect(path, readonly=False):
    db = sqlite3.connect(Path(path).resolve().as_uri() + ('?mode=ro' if readonly else '?mode=rw'), uri=True, timeout=10)
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=10000')
    return db


def prepare(transcript_dir):
    source = panel.DsaPanel().load()
    candidates = {person['id']: person for person in source['candidates']}
    profiles = []
    for cid in panel.EXAMPLE_IDS:
        check(cid in candidates, 'Verified candidate is missing from current DSA source: ' + cid)
        person = candidates[cid]
        transcript = (Path(transcript_dir) / (cid + '.txt')).read_text(encoding='utf-8')
        profiles.append({'sourceCandidateId': cid, 'name': person['name'], 'role': person['interviewRole'],
                         'preparedProfile': panel.prepared_example(person, transcript)})
    return {'schema': 1, 'kind': 'dsa-panel-examples', 'source': panel.SOURCE,
            'preparedAt': iso(now()), 'profiles': profiles}


def validate_payload(payload):
    check(isinstance(payload, dict) and payload.get('schema') == 1 and
          payload.get('kind') == 'dsa-panel-examples' and payload.get('source') == panel.SOURCE,
          'Unexpected source payload')
    profiles = payload.get('profiles')
    check(isinstance(payload.get('preparedAt'), str) and bool(payload['preparedAt']), 'Missing preparation time')
    check(isinstance(profiles, list) and len(profiles) == 4 and
          [p.get('sourceCandidateId') for p in profiles] == list(panel.EXAMPLE_IDS),
          'Payload must contain exactly the four verified source IDs in order')
    for person in profiles:
        cid, prepared = person['sourceCandidateId'], person.get('preparedProfile')
        check(isinstance(prepared, dict) and prepared.get('source') == {
            'kind': 'dsa-panel', 'candidateId': cid, 'example': True}, 'Example source identity differs')
        check(isinstance(person.get('name'), str) and person['name'].strip() and
              isinstance(person.get('role'), str), 'Missing source name or role')
        check(isinstance(prepared.get('recordings'), list) and len(prepared['recordings']) == 1,
              'An example must contain one recording')
        recording = intake._stored_recording({'preparedProfile': prepared}, 0)
        rebuilt = panel.prepared_example({
            'id': cid, 'photo': prepared.get('photo'), 'score': prepared.get('score'),
            'duration': recording.get('duration'), 'video': recording.get('publicUrl'),
            'cv': prepared.get('cvText'), 'profile': prepared.get('profile'),
            'role': prepared.get('summary'),
            'shortChapters': prepared.get('chapters'), 'conclusion': [prepared.get('evaluationText', '')],
        }, prepared.get('transcriptText'))
        check(rebuilt['transcriptSource'] == prepared.get('transcriptSource'), 'Transcript fingerprint differs')
    return profiles


def target_rows(db, workspace, vacancy, batches):
    store.ensure_open(db, workspace, vacancy)
    check(batches and len(set(batches)) == len(batches) and all(b.startswith('workbook-') for b in batches),
          'Only explicitly named workbook example batches can be replaced')
    placeholders = ','.join('?' for _ in batches)
    rows = db.execute('''SELECT id,batch,body FROM em_intake_candidates
      WHERE workspace_id=? AND vacancy_id=? AND batch IN (''' + placeholders + ') ORDER BY rowid',
      (workspace, vacancy, *batches)).fetchall()
    check(set(row[1] for row in rows) == set(batches), 'A replacement batch is absent in the exact scope')
    for _, _, body in rows:
        person = json.loads(body)
        check(not person.get('archived') and (person.get('preparedProfile') or {}).get('sourceLabel') ==
              'Подборка от 16.09.2026', 'The target is not an active example from the original workbook')
    return rows


def make_plan(db, payload, workspace, vacancy, batches):
    profiles = validate_payload(payload)
    rows = target_rows(db, workspace, vacancy, batches)
    old = []
    for cid, batch, body in rows:
        person = json.loads(body)
        old.append({'id': cid, 'batch': batch, 'name': person['name'], 'bodySha256': raw_digest(body),
                    'reviewEvents': db.execute('SELECT COUNT(*) FROM em_intake_review_events WHERE candidate_id=?',
                                               (cid,)).fetchone()[0]})
    target = {'workspaceId': workspace, 'vacancyId': vacancy, 'replaceBatches': batches,
              'sourcePayloadSha256': digest(payload)}
    operation = 'dsa-examples-' + digest(target)[:24]
    return {'schema': 1, **target, 'operationId': operation,
            'vacancyTitle': store.vacancy(db, workspace, vacancy).get('role'), 'archive': old,
            'add': [{'sourceCandidateId': p['sourceCandidateId'], 'name': p['name'],
                     'id': hashlib.sha256((operation + ':' + p['sourceCandidateId']).encode()).hexdigest()[:24]}
                    for p in profiles],
            'canonicalApplicationsUntouched': db.execute('SELECT COUNT(*) FROM cp_applications WHERE vacancy_id=?',
                                                         (vacancy,)).fetchone()[0]}


def apply(db, payload, plan, rollback_path):
    profiles = validate_payload(payload)
    check(plan.get('schema') == 1 and plan.get('sourcePayloadSha256') == digest(payload), 'Payload changed after plan')
    workspace, vacancy, operation = plan['workspaceId'], plan['vacancyId'], plan['operationId']
    command = {'plan': plan, 'payloadSha256': digest(payload)}
    with store._atomic(db):
        store.ensure_open(db, workspace, vacancy)
        rolled_back = db.execute('SELECT 1 FROM em_store_commands WHERE workspace_id=? AND request_id=?',
                                 (workspace, operation + ':rollback')).fetchone()
        check(rolled_back is None, 'This import was rolled back; create a fresh explicit operation instead')
        previous, command_digest = store._command(db, workspace, operation, 'intake.example.replace', command)
        if previous is not None:
            return {**previous, 'replayed': True}
        current_plan = make_plan(db, payload, workspace, vacancy, plan['replaceBatches'])
        check(current_plan == plan, 'Target rows changed after read-only plan; nothing was imported')
        old_rows = target_rows(db, workspace, vacancy, plan['replaceBatches'])
        # The payload preparation time is stable across interrupted retries.
        created_at = payload['preparedAt']
        batch = operation
        existing = db.execute('SELECT 1 FROM em_intake_batches WHERE workspace_id=? AND vacancy_id=? AND batch=?',
                              (workspace, vacancy, batch)).fetchone()
        check(existing is None, 'Import batch already exists without its completion ledger')
        count = db.execute('SELECT COUNT(*) FROM em_intake_candidates WHERE workspace_id=?', (workspace,)).fetchone()[0]
        check(count + len(profiles) <= intake.MAX_WORKSPACE_CANDIDATES, 'Workspace intake limit reached')
        scope = json.loads(old_rows[0][2]).get('scope', 'sfera')
        additions = []
        for profile, entry in zip(profiles, plan['add']):
            person = {'id': entry['id'], 'name': profile['name'], 'role': profile['role'], 'contact': '',
                      'link': '', 'note': '', 'scope': scope, 'files': [], 'createdAt': created_at,
                      'archived': False, 'preparedProfile': profile['preparedProfile']}
            check(db.execute('SELECT 1 FROM em_intake_candidates WHERE id=?', (person['id'],)).fetchone() is None,
                  'A new example ID already exists')
            additions.append({'id': person['id'], 'batch': batch, 'body': dump(person)})
        before = []
        for cid, old_batch, body in old_rows:
            archived = {**json.loads(body), 'archived': True}
            before.append({'id': cid, 'batch': old_batch, 'body': body,
                           'afterBody': dump(archived)})
        backup = {'schema': 1, 'operationId': operation, 'workspaceId': workspace, 'vacancyId': vacancy,
                  'planSha256': digest(plan), 'old': before, 'created': additions}
        rollback_path = Path(rollback_path)
        if rollback_path.exists():
            prior_backup = read_json(rollback_path)
            check(prior_backup == backup, 'Rollback file differs from the exact planned operation')
            # Reuse a backup written before an interrupted/rolled-back transaction.
            backup = prior_backup
            additions = backup['created']
        else:
            private_json(rollback_path, backup)
        db.execute('INSERT INTO em_intake_batches VALUES (?,?,?,?,?)',
                   (workspace, vacancy, batch, digest(payload), created_at))
        for entry in backup['old']:
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=? AND workspace_id=? AND vacancy_id=?',
                       (entry['afterBody'], entry['id'], workspace, vacancy))
        for entry in additions:
            db.execute('INSERT INTO em_intake_candidates VALUES (?,?,?,?,?)',
                       (entry['id'], workspace, vacancy, batch, entry['body']))
        result = {'operationId': operation, 'workspaceId': workspace, 'vacancyId': vacancy,
                  'archived': [p['id'] for p in before], 'added': [p['id'] for p in additions],
                  'rollbackSha256': digest(backup)}
        store._remember(db, workspace, operation, 'intake.example.replace', command_digest, result)
        return result


def rollback(db, backup):
    workspace, vacancy, operation = backup['workspaceId'], backup['vacancyId'], backup['operationId']
    check(backup.get('schema') == 1, 'Unexpected rollback schema')
    command = {'operationId': operation, 'rollbackSha256': digest(backup)}
    with store._atomic(db):
        store.vacancy(db, workspace, vacancy, include_archived=True)
        previous, command_digest = store._command(db, workspace, operation + ':rollback', 'intake.example.rollback', command)
        if previous is not None:
            return {**previous, 'replayed': True}
        row = db.execute('SELECT kind,result FROM em_store_commands WHERE workspace_id=? AND request_id=?',
                         (workspace, operation)).fetchone()
        check(row is not None and row[0] == 'intake.example.replace' and
              json.loads(row[1]).get('rollbackSha256') == digest(backup), 'Rollback does not match a completed import')
        for entry in backup['old'] + backup['created']:
            row = db.execute('SELECT batch,body FROM em_intake_candidates WHERE id=? AND workspace_id=? AND vacancy_id=?',
                             (entry['id'], workspace, vacancy)).fetchone()
            expected = entry.get('afterBody', entry['body'])
            check(row is not None and row[0] == entry['batch'] and row[1] == expected,
                  'A profile changed after import; rollback would overwrite it')
        for entry in backup['old']:
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=? AND workspace_id=? AND vacancy_id=?',
                       (entry['body'], entry['id'], workspace, vacancy))
        for entry in backup['created']:
            person = {**json.loads(entry['body']), 'archived': True}
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=? AND workspace_id=? AND vacancy_id=?',
                       (dump(person), entry['id'], workspace, vacancy))
        result = {'operationId': operation, 'restored': [p['id'] for p in backup['old']],
                  'archivedExamples': [p['id'] for p in backup['created']]}
        store._remember(db, workspace, operation + ':rollback', 'intake.example.rollback', command_digest, result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-dir', help='Installed hr-vision server module directory')
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--transcripts', required=True); prep.add_argument('--output', required=True)
    planning = commands.add_parser('plan')
    planning.add_argument('--database', required=True); planning.add_argument('--payload', required=True)
    planning.add_argument('--workspace', required=True); planning.add_argument('--vacancy', required=True)
    planning.add_argument('--replace-batch', action='append', required=True); planning.add_argument('--output', required=True)
    applying = commands.add_parser('apply')
    applying.add_argument('--database', required=True); applying.add_argument('--payload', required=True)
    applying.add_argument('--plan', required=True); applying.add_argument('--rollback-file', required=True)
    restoring = commands.add_parser('rollback')
    restoring.add_argument('--database', required=True); restoring.add_argument('--rollback-file', required=True)
    args = parser.parse_args(); setup(args.server_dir)
    if args.command == 'prepare':
        payload = prepare(args.transcripts); private_json(args.output, payload)
        print(dump({'path': str(Path(args.output).resolve()), 'sourceIds': [p['sourceCandidateId'] for p in payload['profiles']]}))
        return
    db = connect(args.database, readonly=args.command == 'plan')
    try:
        with db:
            if args.command == 'plan':
                result = make_plan(db, read_json(args.payload), args.workspace, args.vacancy, args.replace_batch)
                private_json(args.output, result)
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
