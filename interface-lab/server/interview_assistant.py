"""Read-only interview assistance: CLI Proxy answers, Jev transcript highlights.

Callers resolve and re-authorize the source; neither client text nor model output
can choose a candidate, recording, URL, or workflow action. Runs and stage results
are private, bounded and idempotent. No candidate decisions are made here.
"""
import html
import json
import math
import os
from pathlib import Path
import re
import secrets
import threading
import time
import urllib.error
import urllib.request

from domain import Problem, require
from structured_model import BASE, ModelConfig, NoRedirect, complete, digest, dump, scan, unique_object
from meeting_lab import EXTRACT, WRITE, JUDGE, obj, string, provider_turns, contextual_evidence, model_config

VERSION = '2026-10-25.1'
RUN_SECONDS = 135
DAILY_BUDGET = 1500000
RUN_BUDGET = 400000
CAPACITY = threading.BoundedSemaphore(4)
JEV_ENDPOINT = 'https://api.typesafe.ai/v1/systemone'
JEV_MODEL = re.compile(r'jev-(?:1\.\d+\.\d+|latest|preview)')
CRITERIA = [
    'Does not address the search question; shared words alone are not evidence.',
    'Related topic, but gives no information answering the search question.',
    'Provides part of an answer, explicitly supported by words in this passage.',
    'Directly answers the question with concrete information in this passage.',
]


def transcript_turns(value):
    require(isinstance(value, str) and len(value.encode()) <= 1000000,
            'Расшифровка слишком длинная для поиска.', 413)
    if '-->' in value:
        turns = provider_turns('interview', value)
    else:
        turns = []
        for index, line in enumerate(value.splitlines()):
            match = re.fullmatch(r'\s*\[((?:\d+:)?\d{2}:\d{2})-((?:\d+:)?\d{2}:\d{2})\]\s*([РК]):\s*(\S.*?)\s*', line)
            if not match:
                continue
            def stamp(text):
                parts = text.split(':')
                if any(int(part) >= 60 for part in parts[1:]):
                    raise ValueError('time')
                return sum(int(part) * 60 ** i for i, part in enumerate(reversed(parts))) * 1000
            try:
                start, end = stamp(match[1]), stamp(match[2])
            except ValueError:
                continue
            if end < start:
                continue
            turns.append({'id': 'line-' + str(index), 'startMs': start, 'endMs': end,
                'speakerName': 'Кандидат' if match[3] == 'К' else 'Рекрутер',
                'text': html.unescape(match[4]), 'source': 'provider-transcript', 'transcriptId': 'interview'})
    require(len(turns) <= 6000, 'Расшифровка слишком длинная для поиска.', 413)
    return sorted(turns, key=lambda turn: turn['startMs'])


def search_chunks(turns, *, limit=480):
    """Cover the full transcript, preserving speaker and timestamp boundaries."""
    result = []
    for turn in turns:
        value, removed = scan(turn['text'])
        if removed or not value.strip():
            continue
        for offset in range(0, len(value), 900):
            part = value[offset:offset + 900].strip()
            previous = result[-1] if result else None
            if (offset == 0 and previous and previous['speakerName'] and
                    previous['speakerName'] == turn['speakerName'] and
                    0 <= turn['startMs'] - previous['endMs'] <= 2000 and
                    turn['endMs'] - previous['startMs'] <= 20000 and
                    len(previous['text']) + len(part) < 900):
                previous['text'] += ' ' + part
                previous['endMs'] = turn['endMs']
            else:
                result.append({'id': 'fragment-' + str(len(result)), 'text': part,
                    'speakerName': turn['speakerName'], 'startMs': turn['startMs'], 'endMs': turn['endMs']})
    require(len(result) <= limit, 'В этой записи слишком много фрагментов для помощника.', 413)
    return result


def chat_chunks(turns):
    """Keep the entire sanitized recording, coalescing adjacent speaker cues.

    A broad question has no reliable lexical query terms. Selecting only the
    latest matching cues can discard the candidate's experience at the start.
    Every bounded batch goes through extraction; the writer sees only evidence.
    """
    return [{'chunkId': 't_' + digest(row['id'])[:20], 'text': row['text'],
             'sourceId': row['id'], 'sourceType': 'provider-transcript',
             'startMs': row['startMs'], 'speakerName': row['speakerName'], 'offset': 0}
            for row in search_chunks(turns, limit=6000)]


def chat_batches(chunks):
    batch, size = [], 0
    for chunk in chunks:
        cost = len(dump(chunk).encode())
        require(cost <= 24000, 'Слишком большой фрагмент интервью.', 413)
        if batch and size + cost > 24000:
            yield batch
            batch, size = [], 0
        batch.append(chunk)
        size += cost
    if batch:
        yield batch


def jev_config():
    # A Gateway credential must never be sent to the direct TypeSafe endpoint.
    key = next((os.environ[name].strip() for name in
                ('HR_JEV_API_KEY', 'TYPESAFE_API_KEY', 'JEV_API_KEY')
                if os.getenv(name, '').strip()), '')
    if not key:
        key_file = Path(os.getenv('HR_JEV_API_KEY_FILE') or Path.home() / '.config/jev/api-key')
        try:
            key = key_file.read_text(encoding='utf-8').strip()
        except (OSError, UnicodeError):
            key = ''
    model = os.getenv('HR_JEV_MODEL', 'jev-1.14.0').strip()
    require(key and not any(char.isspace() for char in key) and JEV_MODEL.fullmatch(model),
            'Поиск по смыслу пока не подключён. Расшифровка доступна для чтения.', 503)
    return ModelConfig('https://api.typesafe.ai/v1', model, key, timeout=22)


class JevEvaluation(list):
    """Validated source matches plus the provider's actual model and usage."""
    def __init__(self, rows, model, usage):
        super().__init__(rows)
        self.model, self.usage = model, usage


def evaluate_jev(config, query, chunks, key, deadline, check, charge):
    """Typed provider boundary. No chat-model fallback and no generated quotations."""
    ids = {row['id'] for row in chunks}
    payload = {'model': config.model, 'state': {'question': query, 'fragments': chunks}, 'questions': {
        row['id']: {'type': 'score', 'instructions': BASE + ' Evaluate the relevance of fragment ' + row['id'] +
            ' to state.question. Only explicit content is evidence. Do not evaluate employability.',
            'criteria': CRITERIA} for row in chunks}}
    require(len(dump(payload).encode()) <= 65000, 'Слишком большой запрос поиска.', 413)
    for attempt in range(2):
        check()
        require(deadline - time.monotonic() > 1, 'Поиск занял слишком много времени.', 503)
        charge(len(dump(payload).encode()) + 3000)
        request = urllib.request.Request(JEV_ENDPOINT, data=dump(payload).encode(), headers={
            'Content-Type': 'application/json', 'Authorization': 'Bearer ' + config.api_key,
            'Idempotency-Key': key, 'User-Agent': 'HRVision-InterviewSearch/1.0'})
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(
                    request, timeout=min(config.timeout, deadline - time.monotonic())) as response:
                raw = response.read(128001)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise Problem('Поиск по смыслу недоступен: TypeSafe отклонил доступ к Jev. Расшифровку можно читать вручную.', 503) from None
            if error.code == 402:
                raise Problem('Поиск по смыслу недоступен: нужно пополнить баланс Jev.', 503) from None
            if error.code == 429:
                raise Problem('Поиск занят. Попробуйте немного позже.', 429) from None
            raise Problem('Поиск сейчас недоступен. Попробуйте ещё раз.', 503) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise Problem('Поиск сейчас недоступен. Попробуйте ещё раз.', 503) from None
        check()
        try:
            require(len(raw) <= 128000, 'Некорректный ответ поиска.', 502)
            response = json.loads(raw, object_pairs_hook=unique_object)
            if not isinstance(response, dict) or set(response) != {'model', 'answers', 'usage'}:
                raise ValueError('response_fields')
            resolved_model, usage = response['model'], response['usage']
            if not isinstance(resolved_model, str) or not JEV_MODEL.fullmatch(resolved_model):
                raise ValueError('response_model')
            if config.model not in ('jev-latest', 'jev-preview') and resolved_model != config.model:
                raise ValueError('unexpected_model')
            if (not isinstance(usage, dict) or set(usage) != {'input_tokens', 'output_tokens'} or
                    any(type(value) is not int or value < 0 for value in usage.values())):
                raise ValueError('response_usage')
            answers = response['answers']
            if not isinstance(answers, dict) or set(answers) != ids:
                raise ValueError('answer_ids')
            ranked = []
            for row in chunks:
                answer = answers[row['id']]
                if (not isinstance(answer, dict) or
                        set(answer) != {'type', 'score', 'confidence', 'probabilities', 'legend'} or
                        answer.get('type') != 'score'):
                    raise ValueError('score_type')
                values = answer['probabilities']
                if not isinstance(values, dict) or set(values) != {'0', '1', '2', '3'}:
                    raise ValueError('score_levels')
                probabilities = [values[str(i)] for i in range(4)]
                if any(type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities):
                    raise ValueError('probability')
                if not math.isclose(sum(probabilities), 1, abs_tol=.02):
                    raise ValueError('probability_sum')
                score = sum(i * p for i, p in enumerate(probabilities))
                if any(type(answer[field]) not in (int, float) or not math.isfinite(answer[field])
                       or not 0 <= answer[field] <= maximum for field, maximum in (('score', 3), ('confidence', 1))):
                    raise ValueError('score_range')
                if not math.isclose(answer['score'], score, abs_tol=.04):
                    raise ValueError('inconsistent_score')
                if answer['legend'] != {str(index): criterion for index, criterion in enumerate(CRITERIA)}:
                    raise ValueError('score_legend')
                if probabilities[2] + probabilities[3] >= .65 and score >= 1.8:
                    ranked.append({**row, 'score': score})
            return JevEvaluation(ranked, resolved_model, usage)
        except (ValueError, KeyError, TypeError):
            if attempt:
                raise Problem('Не удалось проверить результат поиска. Попробуйте уточнить запрос.', 502) from None


class InterviewAssistant:
    def __init__(self, connect, lock):
        self.connect, self.lock = connect, lock
        with lock, connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS ia_runs (
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, account_id TEXT NOT NULL,
                source_id TEXT NOT NULL, input_hash TEXT NOT NULL, action TEXT NOT NULL,
                started REAL NOT NULL, status TEXT NOT NULL, lease TEXT NOT NULL,
                snapshot TEXT NOT NULL, result TEXT, tokens INTEGER NOT NULL DEFAULT 0)''')
            db.execute('''CREATE TABLE IF NOT EXISTS ia_calls (
                run_id TEXT NOT NULL, stage TEXT NOT NULL, result TEXT NOT NULL, metadata TEXT NOT NULL,
                PRIMARY KEY(run_id,stage))''')
            db.execute('''CREATE TABLE IF NOT EXISTS ia_usage (
                account_id TEXT NOT NULL, day TEXT NOT NULL, tokens INTEGER NOT NULL,
                PRIMARY KEY(account_id,day))''')

    def run(self, principal, source_id, transcript, body, authorize):
        action, query, request_id = body.get('action'), body.get('query'), body.get('requestId')
        require(action in ('chat', 'search'), 'Выберите режим помощника.', 400)
        require(isinstance(query, str) and 2 <= len(query.strip()) <= 2000, 'Введите вопрос короче 2000 символов.', 400)
        require(isinstance(request_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{8,100}', request_id), 'Проверьте запрос.', 400)
        query = query.strip()
        require(not scan(query)[1], 'Сформулируйте вопрос об интервью без посторонних инструкций.', 422)
        require(CAPACITY.acquire(blocking=False), 'Помощник занят. Попробуйте немного позже.', 429)
        try:
            return self._run(principal, source_id, transcript, action, query, request_id, authorize)
        finally:
            CAPACITY.release()

    def _run(self, principal, source_id, transcript, action, query, request_id, authorize):
        run_id = 'ia_' + digest(principal['workspaceId'] + ':' + principal['accountId'] + ':' + request_id)
        config = jev_config() if action == 'search' else model_config()
        identity_hash = digest(dump([source_id, transcript, action, query, VERSION, config.model]))
        lease = secrets.token_hex(16)
        tick = time.time()
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            authorize(db)
            prior = db.execute('SELECT input_hash,status,started,result,snapshot FROM ia_runs WHERE id=?', (run_id,)).fetchone()
            if prior:
                require(prior[0] == identity_hash, 'Запись или запрос изменились. Отправьте вопрос заново.', 409)
                if prior[1] == 'ready':
                    return json.loads(prior[3])
                require(prior[1] != 'running' or tick - prior[2] > RUN_SECONDS + 10, 'Помощник уже готовит ответ.', 409)
                snapshot = json.loads(prior[4])
            else:
                recent = db.execute('SELECT count(*) FROM ia_runs WHERE account_id=? AND started>?',
                                    (principal['accountId'], tick - 60)).fetchone()[0]
                require(recent < 12, 'Слишком много запросов. Подождите немного.', 429)
                turns = transcript_turns(transcript)
                require(turns, 'Расшифровка ещё не готова. Попробуйте позже.', 422)
                if action == 'search':
                    chunks, dropped = search_chunks(turns), 0
                else:
                    chunks, dropped = chat_chunks(turns), 0
                require(chunks, 'В расшифровке пока нет доступных фрагментов.', 422)
                snapshot = {'query': query, 'chunks': chunks, 'dropped': dropped, 'model': config.model, 'version': VERSION}
            db.execute('''INSERT INTO ia_runs(id,workspace_id,account_id,source_id,input_hash,action,started,status,lease,snapshot)
                VALUES (?,?,?,?,?,?,?,'running',?,?) ON CONFLICT(id) DO UPDATE SET
                status='running',started=excluded.started,lease=excluded.lease''',
                (run_id, principal['workspaceId'], principal['accountId'], source_id, identity_hash, action, tick, lease, dump(snapshot)))
        deadline = time.monotonic() + RUN_SECONDS
        def check_in(db):
            authorize(db)
            require(db.execute('SELECT status,lease FROM ia_runs WHERE id=?', (run_id,)).fetchone() == ('running', lease), 'Запрос заменён.', 409)
        def check():
            with self.connect() as db:
                check_in(db)
        def charge(tokens):
            day = time.strftime('%Y-%m-%d', time.gmtime())
            with self.lock, self.connect() as db:
                db.execute('BEGIN IMMEDIATE'); check_in(db)
                used = db.execute('SELECT tokens FROM ia_usage WHERE account_id=? AND day=?', (principal['accountId'], day)).fetchone()
                reserved = db.execute('SELECT tokens FROM ia_runs WHERE id=?', (run_id,)).fetchone()[0]
                require((used[0] if used else 0) + tokens <= DAILY_BUDGET and reserved + tokens <= RUN_BUDGET,
                        'Лимит помощника исчерпан. Попробуйте позже.', 429)
                db.execute('INSERT INTO ia_usage VALUES (?,?,?) ON CONFLICT(account_id,day) DO UPDATE SET tokens=tokens+excluded.tokens', (principal['accountId'], day, tokens))
                db.execute('UPDATE ia_runs SET tokens=tokens+? WHERE id=?', (tokens, run_id))
        def stage(name, role, prompt, schema, value, validator=None):
            with self.connect() as db:
                check_in(db)
                cached = db.execute('SELECT result FROM ia_calls WHERE run_id=? AND stage=?', (run_id, name)).fetchone()
            if cached:
                return json.loads(cached[0])
            result, metadata = complete(config, role, 'interview_' + name, VERSION, prompt, schema, value,
                digest(run_id + name), deadline=deadline, check=check, charge=charge, validator=validator)
            with self.lock, self.connect() as db:
                db.execute('BEGIN IMMEDIATE'); check_in(db)
                db.execute('INSERT INTO ia_calls VALUES (?,?,?,?)', (run_id, name, dump(result), dump(metadata)))
            return result
        try:
            chunks = snapshot['chunks']
            if action == 'search':
                ranked = []
                resolved_models = set()
                usages = []
                for offset in range(0, len(chunks), 16):
                    batch = chunks[offset:offset + 16]
                    stage_id = 'search-' + str(offset)
                    with self.connect() as db:
                        check_in(db)
                        cached = db.execute('SELECT result,metadata FROM ia_calls WHERE run_id=? AND stage=?', (run_id, stage_id)).fetchone()
                    if cached:
                        result, metadata = json.loads(cached[0]), json.loads(cached[1])
                    else:
                        result = evaluate_jev(config, query, batch, digest(run_id + stage_id), deadline, check, charge)
                        metadata = {'model': getattr(result, 'model', config.model),
                            'usage': getattr(result, 'usage', None), 'version': VERSION,
                            'provider': 'typesafe', 'promptHash': digest(dump(batch) + query)}
                        with self.lock, self.connect() as db:
                            db.execute('BEGIN IMMEDIATE'); check_in(db)
                            db.execute('INSERT INTO ia_calls VALUES (?,?,?,?)', (run_id, stage_id, dump(result),
                                dump(metadata)))
                    resolved_models.add(metadata['model'])
                    require(len(resolved_models) == 1, 'Модель поиска обновилась. Отправьте запрос заново.', 503)
                    usages.append(metadata.get('usage'))
                    ranked.extend(result)
                ranked.sort(key=lambda row: (-row['score'], row['startMs']))
                result = {'mode': 'search', 'matches': [{key: row[key] for key in ('id', 'text', 'startMs', 'endMs', 'speakerName')}
                    for row in ranked[:30]], 'coverage': 'entire_transcript', 'totalMatches': len(ranked),
                    'model': next(iter(resolved_models))}
                if all(usage is not None for usage in usages):
                    result['usage'] = {field: sum(usage[field] for usage in usages)
                                       for field in ('input_tokens', 'output_tokens')}
            else:
                by_id = {chunk['chunkId']: chunk for chunk in chunks}
                def verify(value):
                    if bool(value['evidence']) != value['supported']:
                        raise ValueError('evidence_gate')
                    for fact in value['evidence']:
                        if fact['quote'] not in by_id[fact['chunkId']]['text'] or '[[REDACTED_INJECTION]]' in fact['quote']:
                            raise ValueError('unsupported_quote')
                extracted_batches = []
                for index, batch in enumerate(chat_batches(chunks)):
                    schema = obj({'supported': {'type': 'boolean'}, 'evidence': {'type': 'array', 'maxItems': 10,
                        'items': obj({'chunkId': {'type': 'string', 'enum': [row['chunkId'] for row in batch]}, 'quote': string(600)})}})
                    name = 'extract' if index == 0 else 'batch_' + str(index) + '_extract'
                    extracted = stage(name, 'interview-extractor', EXTRACT, schema,
                        {'question': query, 'chunks': batch}, verify)
                    extracted_batches.append(extracted['evidence'])
                # Bound the writer's evidence while representing later batches,
                # rather than filling its budget with the start of the interview.
                facts = [batch[index] for index in range(10)
                         for batch in extracted_batches if len(batch) > index][:10]
                if not facts:
                    result = {'mode': 'chat', 'text': 'В доступных фрагментах интервью нет ответа на этот вопрос. Это можно уточнить на встрече.', 'evidence': [], 'supported': False}
                else:
                    evidence = contextual_evidence(run_id, facts, chunks)
                    allowed = {item['id'] for item in evidence}
                    answer_schema = obj({'text': string(3000), 'evidenceIds': {'type': 'array', 'maxItems': 10, 'items': {'type': 'string', 'enum': sorted(allowed)}}})
                    def grounded(value):
                        if not value['evidenceIds']:
                            raise ValueError('missing_evidence')
                    answer = stage('write', 'interview-writer', WRITE, answer_schema, {'question': query, 'evidence': evidence}, grounded)
                    verdict = stage('judge', 'interview-judge', JUDGE, obj({'grounded': {'type': 'boolean'}}), {'question': query, 'evidence': evidence, 'answer': answer})
                    require(verdict['grounded'], 'Не удалось подтвердить ответ словами из интервью. Попробуйте уточнить вопрос.', 422)
                    result = {'mode': 'chat', 'text': answer['text'], 'evidence': [item for item in evidence if item['id'] in answer['evidenceIds']], 'supported': True}
                result['coverage'] = {'selected': len(chunks), 'omitted': snapshot['dropped']}
            result.setdefault('model', config.model)
            result['version'] = VERSION
            with self.lock, self.connect() as db:
                db.execute('BEGIN IMMEDIATE'); check_in(db)
                db.execute("UPDATE ia_runs SET status='ready',result=? WHERE id=? AND lease=?", (dump(result), run_id, lease))
            return result
        except BaseException as error:
            with self.lock, self.connect() as db:
                db.execute("UPDATE ia_runs SET status='error' WHERE id=? AND lease=? AND status='running'", (run_id, lease))
            if isinstance(error, Problem) and 'резюме' in error.message.casefold():
                raise Problem('Помощник не смог подготовить проверенный ответ. Попробуйте позже.', error.status) from None
            raise
