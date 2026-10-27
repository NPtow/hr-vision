#!/usr/bin/env python3
"""Operator import of reviewed private DSA material into one employer vacancy.

Default is a transactional dry run. Input is a private JSON export, never a
build asset. It contains vacancy fields, six source profiles, PDF bytes, and
immutable assessment projections. No messages, applications or decisions are
created. Existing source-panel records and unrelated vacancies are untouched.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time


def run(db, payload):
    import employer_intake as intake
    import employer_store as store
    import interview_assessment as assessment
    from domain import require
    wid, batch = payload['workspaceId'], payload['batch']
    people = payload['candidates']
    require(len(people) == 6 and len({p['sourceId'] for p in people}) == 6, 'Expected six distinct source candidates')
    vacancy = store.create_vacancy(db, wid, payload['vacancy'], request_id=batch + '-vacancy')
    vid = vacancy['id']
    entries, prepared, size = [], [], 0
    for person in people:
        source_id, profile = person['sourceId'], person['profile']
        require(profile['source']['candidateId'] == source_id and
                profile['recordings'][0]['sourceCandidateId'] == source_id, 'Source identity mismatch')
        pdf = base64.b64decode(person['resume']['content'], validate=True)
        require(pdf.startswith(b'%PDF-') and len(pdf) <= intake.MAX_BYTES, 'Invalid source PDF')
        # The body digest pins the entire private import, including original
        # transcript/profile/PDF content, so a changed retry cannot mix data.
        entry = {'name': person['name'], 'role': person['role'], 'contact': '', 'link': '', 'note': '',
                 'preparedProfile': profile}
        entries.append({**entry, 'resumeSha256': hashlib.sha256(pdf).hexdigest()})
        prepared.append((entry, {'name': person['resume']['name'], 'mime': 'application/pdf',
            'content': pdf, 'text': person['resume']['text'], 'fields': {}, 'parse_status': 'parsed'}))
        size += len(pdf)
    body = {'batch': batch, 'scope': 'dsa', 'vacancyId': vid, 'entries': entries}
    ready = intake.PreparedBatch(digest=intake._body(body), batch=batch, scope='dsa', entries=tuple(prepared), total_bytes=size)
    stored = intake.add(db, wid, vid, body, ready)['candidates']
    by_source = {p['preparedProfile']['source']['candidateId']: p for p in stored}
    require(set(by_source) == {p['sourceId'] for p in people}, 'Incomplete existing import; stop for review')
    revisions = []
    for person in people:
        candidate = by_source[person['sourceId']]
        scope = {'source': 'intake', 'vacancyId': vid, 'id': candidate['id'], 'index': 0}
        binding, fingerprint, _ = assessment.intake_binding(db, wid, scope)
        saved = assessment.import_assessment(db, wid, binding, fingerprint, person['assessment'])
        revisions.append({'sourceId': person['sourceId'], 'intakeId': candidate['id'], **saved})
    require(not db.execute('PRAGMA foreign_key_check').fetchone(), 'Foreign key validation failed')
    return {'vacancyId': vid, 'workspaceId': wid, 'candidates': revisions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', required=True, type=Path)
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--runtime', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    sys.path.insert(0, str(args.runtime))
    import interview_assessment as assessment
    payload = json.loads(args.payload.read_text())
    if payload.get('schemaVersion') != 1:
        raise ValueError('Unsupported private export')
    with sqlite3.connect(args.database, timeout=15) as db:
        db.execute('PRAGMA foreign_keys=ON')
        if args.apply:
            backup = args.database.with_name('before-orbit-import-' + str(time.time_ns()) + '.sqlite3')
            with sqlite3.connect(backup) as target:
                db.backup(target)
            backup.chmod(0o600)
        db.execute('BEGIN IMMEDIATE')
        try:
            assessment.init(db)
            result = run(db, payload)
            if args.apply:
                db.commit()
            else:
                db.rollback()
        except BaseException:
            db.rollback()
            raise
    print(json.dumps({'applied': args.apply, **result}, ensure_ascii=False))


if __name__ == '__main__':
    main()
