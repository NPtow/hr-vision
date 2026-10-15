#!/usr/bin/env python3
"""Operator CLI: local CV -> grounded draft -> personal booking invitation.

Run on the API host with the service environment and its deployed server directory.
Without --apply, only validate/extract the file (no database/model/network writes).
--apply uses the same ResumeModel, prepared candidate, booking and access flow as
the service. Never creates consent, an interview, credentials or outgoing mail.
The invitation is private: result files are 0600 and stdout contains no token.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile


def private_result(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.outreach-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare(service, resume, campaign, recipient):
    """Testable trusted entry; caller must own operator access to this database."""
    from candidate_booking import _id
    from domain import require
    _id(campaign)
    _id(recipient)
    digest = hashlib.sha256(resume['content']).hexdigest()

    def authorize(db):
        row = db.execute('''SELECT candidate_id,cv_sha256 FROM cp_booking_manual_sources
          WHERE campaign_id=? AND recipient_id=?''', (campaign, recipient)).fetchone()
        require(not row or row[1] == digest,
                'Для этого получателя уже использовано другое резюме.', 409)
        return row

    # Recover existing issuance before model work. Do not overwrite profile edits.
    with service.CANDIDATE_PORTAL.lock, service.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old = authorize(db)
        if old:
            return {'candidateId': old[0], **service.BOOKING.issue(db, old[0], campaign, recipient),
                    'reused': True, 'cvSha256': digest}
    owner = 'manual-outreach:' + hashlib.sha256(
        json.dumps([campaign, recipient], separators=(',', ':')).encode()).hexdigest()
    generated = service.CANDIDATE_PORTAL.resume_model.generate(resume, owner, authorize)
    with service.CANDIDATE_PORTAL.lock, service.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        authorize(db)
        service.CANDIDATE_PORTAL.resume_model.assert_current(db, owner, generated['runId'])
        issued = service.BOOKING.issue_manual(db, generated, campaign, recipient)
    return {**issued, 'reused': False, 'cvSha256': digest, 'modelRunId': generated['runId']}


def inspect(service, invitation_id):
    from domain import require
    with service.connect() as db:
        row = db.execute('''SELECT i.candidate_id,i.campaign_id,i.recipient_id,i.expires,i.revoked_at
          FROM cp_booking_invitations i JOIN cp_booking_manual_sources m ON m.candidate_id=i.candidate_id
          WHERE i.id=?''', (invitation_id,)).fetchone()
        require(row, 'Приглашение не найдено.', 404)
        events = [dict(zip(('kind', 'createdAt'), item)) for item in db.execute(
            'SELECT kind,created_at FROM cp_booking_manual_events WHERE invitation_id=? ORDER BY created_at',
            (invitation_id,))]
        interview = db.execute('SELECT id,status FROM cp_interviews WHERE candidate_id=?', (row[0],)).fetchone()
        access = db.execute('SELECT status FROM cp_access_outbox WHERE candidate_id=?', (row[0],)).fetchone()
        return {'candidateId': row[0], 'campaignId': row[1], 'recipientId': row[2],
                'expires': row[3], 'revoked': bool(row[4]), 'events': events,
                'interview': {'id': interview[0], 'status': interview[1]} if interview else None,
                'accessEmailStatus': access[0] if access else 'not_requested',
                'messageDelivery': 'external_not_observed'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-dir', type=Path, default=Path(__file__).resolve().parents[1] / 'server')
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('prepare')
    create.add_argument('--resume', type=Path, required=True)
    create.add_argument('--campaign', required=True)
    create.add_argument('--recipient', required=True)
    create.add_argument('--output', type=Path)
    create.add_argument('--apply', action='store_true')
    state = commands.add_parser('status')
    state.add_argument('--invitation', required=True)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(args.server_dir.resolve()))
    from domain import Problem, require
    try:
        if args.command == 'prepare':
            from candidate_resume import MAX_BYTES, parse_upload
            from candidate_booking import _id
            _id(args.campaign)
            _id(args.recipient)
            require(args.resume.is_file() and 0 < args.resume.stat().st_size <= MAX_BYTES,
                    'Резюме должно быть не больше 5 МБ.', 400)
            resume = parse_upload({'filename': args.resume.name,
                'contentBase64': base64.b64encode(args.resume.read_bytes()).decode('ascii')})
            if not args.apply:
                print(json.dumps({'validated': True, 'filename': resume['filename'],
                    'bytes': len(resume['content']), 'textCharacters': len(resume['text']),
                    'cvSha256': hashlib.sha256(resume['content']).hexdigest(),
                    'applied': False}, ensure_ascii=False))
                return 0
            require(args.output is not None, 'Укажите приватный файл --output.', 400)
            # Resolve output errors before committing an invitation. A retry still
            # recovers the same URL after a crash between commit and result write.
            args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            require(args.output.resolve() != args.resume.resolve(), 'Файл результата совпадает с CV.', 400)
            import app as service
            result = prepare(service, resume, args.campaign, args.recipient)
            private_result(args.output, result)
            print(json.dumps({'applied': True, 'reused': result['reused'],
                'candidateId': result['candidateId'], 'invitationId': result['invitationId'],
                'resultFile': str(args.output)}, ensure_ascii=False))
        else:
            import app as service
            print(json.dumps(inspect(service, args.invitation), ensure_ascii=False))
        return 0
    except Problem as error:
        print(json.dumps({'error': error.message, 'status': error.status}, ensure_ascii=False), file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print(json.dumps({'error': 'Не удалось прочитать файл или конфигурацию. Изменения не подтверждены.'},
                         ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
