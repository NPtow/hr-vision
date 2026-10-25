"""Operator-run live acceptance. Inputs and full receipts stay in private storage.

Runs only assistant requests, never candidate workflow actions. API mode creates
one short-lived test session for the authorized workspace owner and revokes it.
The plan explicitly lists the candidates and questions authorized for the run.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import urllib.error
import urllib.request


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--mode', choices=['worker', 'api'], required=True)
    parser.add_argument('--label', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    sys.path.insert(0, args.source)
    from employer_routes import EmployerRoutes
    import employer_access
    from interview_assistant import search_chunks, transcript_turns
    plan = json.loads(Path(args.plan).read_text())
    output = Path(args.output); output.mkdir(mode=0o700, parents=True, exist_ok=True)
    db_path = '/var/lib/hr-vision-api/journey.sqlite3'
    readonly = lambda: sqlite3.connect('file:' + db_path + '?mode=ro', uri=True)
    with readonly() as db:
        row = db.execute('SELECT id FROM em_accounts WHERE workspace_id=? AND active=1', (plan['workspaceId'],)).fetchone()
        assert row, 'Authorized workspace has no active account'
        principal = {'workspaceId': plan['workspaceId'], 'accountId': row[0]}
    token = None
    if args.mode == 'api':
        with sqlite3.connect(db_path) as db:
            token = employer_access._issue_session(db, principal['accountId'])['token']
        endpoint = 'https://mashadasha.tech/api/hr/employer/interview-assistant'
    else:
        config = dict(line.split('=', 1) for line in Path('/etc/hr-vision/agent.env').read_text().splitlines() if '=' in line)
        token = config['HR_AGENT_INTERNAL_TOKEN']
        endpoint = 'http://127.0.0.1:8424/answer'

    def post(body):
        request = urllib.request.Request(endpoint, data=json.dumps(body, ensure_ascii=False).encode(), headers={
            'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'Origin': 'https://mashadasha.tech'})
        try:
            with urllib.request.urlopen(request, timeout=150) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    summaries = []
    try:
        for candidate_index, candidate in enumerate(plan['candidates']):
            cid = candidate['id']
            scope = {'source': 'intake', 'vacancyId': plan['vacancyId'], 'id': cid, 'index': 0}
            with readonly() as db:
                row = db.execute('SELECT body FROM em_intake_candidates WHERE id=? AND workspace_id=? AND vacancy_id=?',
                    (cid, plan['workspaceId'], plan['vacancyId'])).fetchone()
                assert row, 'Candidate is outside authorized plan'
                person = json.loads(row[0]); profile = person['preparedProfile']
                context = EmployerRoutes._assistant_context(None, db, principal,
                    {k: v for k, v in scope.items() if k != 'source'}, profile)
            chunks = [{'chunkId': r['id'], **{k: r[k] for k in ('text', 'speakerName', 'startMs', 'endMs')}}
                      for r in search_chunks(transcript_turns(profile['transcriptText']))]
            sources = {r['chunkId']: r for r in chunks}
            history = []
            session_id = digest(args.label + cid)
            for turn_index, question in enumerate(candidate.get('questions', plan['questions'])):
                case = f'{candidate_index + 1}-{turn_index + 1}'
                request_id = digest(args.label + cid + str(turn_index))
                if args.mode == 'api':
                    body = {**scope, 'action': 'chat', 'query': question, 'requestId': request_id}
                else:
                    body = {'version': '2026-10-25.agent1', 'runId': request_id, 'sessionId': session_id,
                            'query': question, 'chunks': chunks, 'context': context, 'history': history}
                print(json.dumps({'case': case, 'stage': 'running', 'chunks': len(chunks)}), flush=True)
                started = time.monotonic(); status, result = post(body)
                metadata = result.get('metadata', {})
                if args.mode == 'api' and status == 200:
                    run_id = 'ia_' + digest(principal['workspaceId'] + ':' + principal['accountId'] + ':' + request_id)
                    with readonly() as db:
                        row = db.execute("SELECT metadata FROM ia_calls WHERE run_id=? AND stage='answer'", (run_id,)).fetchone()
                    metadata = json.loads(row[0]) if row else {}
                answer = result if args.mode == 'api' else result.get('answer', {})
                references = [ref for p in answer.get('parts', []) for ref in p['sourceIds']]
                valid = all(ref in sources for ref in references)
                if args.mode == 'api':
                    valid = valid and all(ref['id'] in sources and all(ref[k] == sources[ref['id']][k]
                        for k in ('startMs', 'endMs', 'speakerName')) and ref['quote'] == sources[ref['id']]['text']
                        for ref in answer.get('evidence', []))
                summary = {'case': case, 'candidateId': cid, 'status': status, 'seconds': round(time.monotonic() - started, 1),
                    'references': len(set(references)), 'referencesValid': valid, 'model': metadata.get('model'),
                    'grounded': metadata.get('grounded'), 'tools': [c['name'] for c in metadata.get('tools', [])],
                    'budgetUnits': metadata.get('budgetUnits'), 'error': result.get('error')}
                receipt = {'summary': summary, 'question': question, 'result': result, 'metadata': metadata,
                           'citedSources': [sources[r] for r in dict.fromkeys(references) if r in sources]}
                (output / (case + '.json')).write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
                summaries.append(summary)
                print(json.dumps(summary, ensure_ascii=False), flush=True)
                if status != 200:
                    break
                assert valid and metadata.get('grounded') is True and metadata.get('version') == '2026-10-25.agent1'
                history.append({'query': question, 'answer': '\n\n'.join(p['text'] for p in answer['parts'])})
                if args.mode == 'api':
                    repeat_status, repeat = post(body)
                    assert repeat_status == 200 and repeat == result, 'Idempotent replay changed the answer'
                    history_status, restored = post({**scope, 'action': 'history'})
                    assert history_status == 200 and any(r['id'] == request_id and r['answer'] == result for r in restored['messages'])
            (output / 'summary.json').write_text(json.dumps(summaries, ensure_ascii=False, indent=2))
    finally:
        if args.mode == 'api' and token:
            with sqlite3.connect(db_path) as db:
                db.execute('DELETE FROM em_sessions WHERE token_hash=?', (digest(token),))
    if any(row['status'] != 200 for row in summaries):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
