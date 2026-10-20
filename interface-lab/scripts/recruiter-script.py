#!/usr/bin/env python3
"""Human-operated agent bridge. Exports context or imports a draft; never approves."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import recruiter_coach
from domain import Problem


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path, help='Existing service SQLite database; trusted operator access only')
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('context')
    export.add_argument('--candidate', required=True)
    export.add_argument('--interview', required=True)
    export.add_argument('--output', type=Path, required=True)
    draft = commands.add_parser('draft')
    draft.add_argument('--file', type=Path, required=True)
    args = parser.parse_args()
    with sqlite3.connect(args.db.resolve().as_uri() + ('?mode=ro' if args.command == 'context' else '?mode=rw'),
                         uri=True, timeout=10) as db:
        db.execute('PRAGMA foreign_keys=ON')
        if args.command == 'context':
            result = recruiter_coach.context(db, args.candidate, args.interview)
            # Do not overwrite an existing export, and keep personal data private.
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump(result, output, ensure_ascii=False, indent=2)
                output.write('\n')
            print('Context saved:', args.output)
        else:
            if args.file.stat().st_size > 256_000:
                raise ValueError('Draft is too large')
            body = json.loads(args.file.read_text(encoding='utf-8'))
            if not isinstance(body, dict):
                raise ValueError('Expected one draft object')
            db.execute('BEGIN IMMEDIATE')
            result = recruiter_coach.handle(db, body.get('candidateId'), body.get('interviewId'),
                                            'draft', body, 'agent:operator')
            print(json.dumps({'interviewId': result['interviewId'], 'revision': result['latest']['revision'],
                              'status': 'draft', 'requiresRecruiterApproval': True}))


if __name__ == '__main__':
    try:
        main()
    except (Problem, ValueError, OSError, sqlite3.Error) as error:
        print(error.message if isinstance(error, Problem) else str(error), file=sys.stderr)
        sys.exit(1)
