"""Typed Jev assessment. Source text never selects code, URLs or workflow actions."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time

VERSION = '2026-10-25.2'
PACK = json.loads((Path(__file__).parent / 'prompts' / (VERSION + '.json')).read_text())
PACK_HASH = hashlib.sha256(json.dumps(PACK, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
INJECTION = re.compile(r'ignore\s+(?:all\s+)?(?:previous|above|system)|(?:system|developer)\s*(?:prompt|message)|игнорир\w*\s+(?:все\s+)?(?:предыдущ|инструкц)|(?:выведи|раскрой|покажи)\s+(?:ключ|токен|секрет)|<\|(?:im_start|system)|(?:администратор|admin)\s+(?:разреш|says)|you\s+are\s+(?:now|chatgpt)|выполни\s+(?:команд|код)|<script\b', re.I)


class Failure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def check(ok, code='invalid_input'):
    if not ok:
        raise Failure(code)


def unique(pairs):
    value = {}
    for k, v in pairs:
        check(k not in value, 'duplicate_json_key')
        value[k] = v
    return value


def number(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def intake(body):
    check(isinstance(body, dict) and set(body) == {'subject', 'sourceHash', 'chunks'})
    check(isinstance(body['subject'], str) and re.fullmatch(r'[a-zA-Z0-9_:.\-]{1,180}', body['subject']))
    check(isinstance(body['sourceHash'], str) and re.fullmatch('[a-f0-9]{64}', body['sourceHash']))
    rows = body['chunks']
    check(isinstance(rows, list) and 1 <= len(rows) <= 600)
    ids, clean, redacted = set(), [], 0
    for row in rows:
        check(isinstance(row, dict) and set(row) == {'id', 'text', 'speaker', 'startMs', 'endMs'})
        check(isinstance(row['id'], str) and re.fullmatch(r'[a-zA-Z0-9_.-]{1,80}', row['id']) and row['id'] not in ids)
        ids.add(row['id'])
        check(isinstance(row['text'], str) and 1 <= len(row['text']) <= 1200)
        check(isinstance(row['speaker'], str) and len(row['speaker']) <= 120)
        check(number(row['startMs'], 0, 14400000) and number(row['endMs'], row['startMs'], 14400000))
        lines = []
        for line in row['text'].splitlines():
            bad = bool(INJECTION.search(line))
            lines.append('[[REDACTED_INJECTION]]' if bad else line)
            redacted += int(bad)
        clean.append({**row, 'text': '\n'.join(lines), 'speaker': '[[REDACTED_INJECTION]]' if INJECTION.search(row['speaker']) else row['speaker']})
    check(len(dump(clean).encode()) <= 220000, 'input_too_large')
    return {**body, 'chunks': clean, 'redactions': redacted}


def validate_response(response, questions):
    check(isinstance(response, dict) and set(response) == {'model', 'answers', 'usage'}, 'invalid_model_output')
    check(isinstance(response['model'], str) and re.fullmatch(r'jev-\d+\.\d+\.\d+', response['model']), 'invalid_model_output')
    usage = response['usage']
    check(isinstance(usage, dict) and set(usage) == {'input_tokens', 'output_tokens'} and
          all(type(v) is int and 0 <= v <= 1000000 for v in usage.values()), 'invalid_model_output')
    answers = response['answers']
    check(isinstance(answers, dict) and set(answers) == set(questions), 'invalid_model_output')
    for qid, q in questions.items():
        a = answers[qid]
        kind = q['type']
        fields = {'type', 'noul'} if kind == 'noul' else {'type', kind, 'probabilities', 'confidence'} | ({'legend'} if kind == 'score' else set())
        check(isinstance(a, dict) and set(a) == fields and a['type'] == kind, 'invalid_model_output')
        if kind == 'noul':
            check(number(a['noul'], 0, 1), 'invalid_model_output')
            continue
        expected = set(q['criteria']) if kind == 'choice' else {str(i) for i in range(len(q['criteria']))}
        p = a['probabilities']
        check(isinstance(p, dict) and set(p) == expected and all(number(v, 0, 1) for v in p.values()) and abs(sum(p.values()) - 1) <= .021, 'invalid_model_output')
        check(number(a['confidence'], 0, 1), 'invalid_model_output')
        if kind == 'choice':
            check(a['choice'] in expected, 'invalid_model_output')
        else:
            check(number(a['score'], 0, len(expected) - 1) and a['legend'] == {str(i): v for i, v in enumerate(q['criteria'])}, 'invalid_model_output')
    return response


class JevBoundary:
    """One model boundary, no implicit fallback or automatic retry on uncertain POST."""
    def __init__(self, store, model=None, transport=None):
        self.store = store
        self.model = model or os.environ.get('ASSESSMENT_MODEL', 'jev-1.14.0')
        check(re.fullmatch(r'jev-\d+\.\d+\.\d+', self.model), 'invalid_model_config')
        self.transport = transport or self.http

    def http(self, payload, key):
        api_key = os.environ.get('ASSESSMENT_JEV_KEY', '')
        check(api_key and not re.search(r'\s', api_key), 'provider_not_configured')
        proxy = os.environ.get('ASSESSMENT_PROXY', '')
        check(not proxy or re.fullmatch(r'socks5h://127\.0\.0\.1:[0-9]{1,5}', proxy), 'invalid_proxy')
        def quote(v):
            return '"' + v.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t') + '"'
        options = [('url', 'https://api.typesafe.ai/v1/systemone'), ('request', 'POST'),
                   ('header', 'Content-Type: application/json'), ('header', 'Authorization: Bearer ' + api_key),
                   ('header', 'Idempotency-Key: ' + key), ('data', dump(payload))]
        if proxy:
            options.append(('proxy', proxy))
        config = '\n'.join(k + ' = ' + quote(v) for k, v in options) + '\n'
        command = ['/usr/bin/curl', '--disable', '--config', '-', '--silent', '--show-error', '--max-time', '55',
                   '--connect-timeout', '8', '--max-filesize', '128000', '--noproxy', '', '--globoff',
                   '--proto', '=https', '--max-redirs', '0', '--write-out', '\n%{http_code}']
        try:
            with tempfile.TemporaryFile() as f:
                result = subprocess.run(command, input=config.encode(), stdout=f, stderr=subprocess.DEVNULL,
                    timeout=58, env={'PATH': '/usr/bin:/bin', 'LANG': 'C'}, check=False)
                check(result.returncode == 0, 'provider_outcome_unknown')
                check(f.seek(0, 2) <= 128004, 'provider_response_too_large')
                f.seek(0)
                raw = f.read()
        except (OSError, subprocess.SubprocessError):
            raise Failure('provider_outcome_unknown') from None
        check(re.search(rb'\n[0-9]{3}$', raw), 'provider_outcome_unknown')
        code = int(raw[-3:])
        check(code == 200, 'provider_http_' + str(code))
        try:
            return json.loads(raw[:-4], object_pairs_hook=unique)
        except (ValueError, UnicodeError):
            raise Failure('invalid_model_output') from None

    def call(self, job_id, stage, state, questions):
        # TypeSafe has no system-role or max_tokens parameter. Each typed question
        # carries the immutable base; bounded question cardinality bounds output.
        check(1 <= len(questions) <= 48, 'question_budget')
        fence = 'UNTRUSTED_' + digest([job_id, stage])[:32]
        serialized = dump(state)
        check(fence not in serialized, 'invalid_fence')
        payload = {'model': self.model, 'state': fence + '\n' + serialized + '\nEND_' + fence,
                   'questions': {k: {**q, 'instructions': PACK['base'] + '\n' + q['instructions']} for k, q in questions.items()}}
        encoded = dump(payload)
        check(len(encoded.encode()) <= 130000, 'call_input_budget')
        key = digest([job_id, stage, PACK_HASH, self.model, payload])
        for attempt in range(2):
            call_key = key + ':' + str(attempt)
            cached = self.store.begin_call(job_id, call_key, stage, len(encoded.encode()) + 4000)
            if cached is not None:
                return cached
            started = time.monotonic()
            try:
                response = validate_response(self.transport(payload, key), payload['questions'])
                check(response['model'] == self.model, 'model_version_changed')
            except Failure as error:
                self.store.fail_call(call_key, error.code)
                if error.code == 'invalid_model_output' and attempt == 0:
                    # Exactly one bounded contract retry; same provider idempotency key.
                    payload['state'] += '\nContract validation failed: invalid_model_output. Return all requested typed fields.'
                    continue
                raise
            self.store.finish_call(job_id, call_key, response, time.monotonic() - started)
            return response
        raise Failure('invalid_model_output')


def retrieve(chunks, criterion):
    # Bounded, deterministic first pass; retained coverage is recorded. No source
    # supplies a URL, path or executable action. Short stems allow Russian inflection.
    words = {w[:5] for w in re.findall(r'[a-zа-яё]{4,}', criterion['business_quality_justification'].lower())}
    ranked = sorted(enumerate(chunks), key=lambda pair: (-sum(w in pair[1]['text'].lower() for w in words), pair[0]))
    indexes = {i for i, _ in ranked[:10]}
    for i in list(indexes):
        if i > 0:
            indexes.add(i - 1)
        if i + 1 < len(chunks):
            indexes.add(i + 1)
    return [chunks[i] for i in sorted(indexes)]


def assess(body, job_id, boundary):
    chunks = body['chunks']
    by_id = {c['id']: c for c in chunks}
    selected, bases, metadata, coverage = {}, {}, [], {}
    criteria = PACK['criteria']
    for start in range(0, len(criteria), 4):
        group = criteria[start:start + 4]
        states, questions = {}, {}
        for c in group:
            cid = c['criterion_id']
            rows = retrieve(chunks, c)
            coverage[cid] = {'selected': len(rows), 'total': len(chunks)}
            states[cid] = rows
            options = {'NONE': 'Нет конкретного подтверждающего действия кандидата.'}
            options.update({r['id']: 'Фрагмент ' + r['id'] for r in rows})
            questions[cid] = {'type': 'choice', 'instructions': PACK['evidence'] + '\nКритерий: ' + c['business_quality_justification'] + '\nСмотри только state.' + cid,
                              'criteria': options}
        response = boundary.call(job_id, 'evidence-' + str(start), states, questions)
        metadata.append(response)
        for cid, answer in response['answers'].items():
            chosen = answer['choice']
            # Selected IDs resolve back to source rows, never model-generated quotes.
            if chosen != 'NONE' and answer['probabilities'][chosen] >= .55:
                index = next(i for i, r in enumerate(chunks) if r['id'] == chosen)
                selected[cid] = chunks[max(0, index - 1):index + 2]
    scores, judged = {}, {}
    if selected:
        questions = {cid: {'type': 'choice', 'instructions': PACK['basis'] + '\nКритерий ' + next(c['name_ru'] for c in criteria if c['criterion_id'] == cid) + '; state.' + cid,
            'criteria': {'observed': 'Действие непосредственно показано в заданном профессиональном кейсе.',
                         'narrated': 'Конкретный рассказ кандидата о своих прошлых действиях.',
                         'hypothetical': 'Кандидат рассуждает, как поступил бы в такой ситуации.',
                         'unknown': 'Нет конкретных действий кандидата или неясно, кто говорит.'}} for cid in selected}
        response = boundary.call(job_id, 'basis', selected, questions)
        metadata.append(response)
        bases = {cid: a['choice'] for cid, a in response['answers'].items() if a['choice'] != 'unknown' and a['probabilities'][a['choice']] >= .55}
        selected = {cid: rows for cid, rows in selected.items() if cid in bases}
    if selected:
        questions = {cid: {'type': 'score', 'instructions': PACK['score'] + '\n' + next(c['business_quality_justification'] for c in criteria if c['criterion_id'] == cid) + '\nОснование: ' + bases[cid] + '. Используй только state.' + cid,
                         'criteria': PACK['levels']} for cid in selected}
        response = boundary.call(job_id, 'score', selected, questions)
        metadata.append(response)
        scores = response['answers']
        states, questions = {}, {}
        for cid, answer in scores.items():
            # Expected value is calculated from validated probabilities in code.
            raw = 1 + sum(int(i) * p for i, p in answer['probabilities'].items()) / sum(answer['probabilities'].values())
            level = min(3 if bases[cid] == 'hypothetical' else 4, max(1, math.floor(raw + .5)))
            answer['displayLevel'] = level
            answer['normalizedScore'] = round(raw, 3)
            states[cid] = {'fragments': selected[cid], 'basis': bases[cid], 'levelDescription': PACK['levels'][level - 1]}
            questions[cid] = {'type': 'noul', 'instructions': PACK['judge'] + '\n' + next(c['business_quality_justification'] for c in criteria if c['criterion_id'] == cid) + '\nПроверь только state.' + cid}
        response = boundary.call(job_id, 'groundedness', states, questions)
        metadata.append(response)
        judged = response['answers']
    result = []
    for c in criteria:
        cid = c['criterion_id']
        supported = cid in scores and judged[cid]['noul'] >= .75
        evidence = []
        if supported:
            for row in selected[cid]:
                evidence.append({'id': row['id'], 'text': row['text'], 'seconds': row['startMs'] / 1000,
                                 'endSeconds': row['endMs'] / 1000, 'speaker': row['speaker'],
                                 'time': str(int(row['startMs'] // 60000)).zfill(2) + ':' + str(int(row['startMs'] // 1000) % 60).zfill(2)})
        level = scores[cid]['displayLevel'] if supported else None
        result.append({'id': cid, 'label': c['name_ru'], 'state': 'supported' if supported else 'unknown',
            'level': level, 'target': None, 'basis': bases.get(cid) if supported else None,
            'summary': PACK['levels'][level - 1] if supported else 'В интервью недостаточно конкретных действий для оценки этого навыка.',
            'stateLabel': 'По рассказу кандидата' if supported and bases[cid] == 'narrated' else 'По ответу на кейс' if supported else 'Недостаточно данных',
            'limitation': 'Рассказ кандидата; результат работы независимо не проверен.' if supported and bases[cid] == 'narrated' else '',
            'evidence': evidence,
            'modelEstimate': {'value': min(3 if bases[cid] == 'hypothetical' else 4, scores[cid]['normalizedScore']), 'confidence': scores[cid]['confidence'],
                'probabilities': [scores[cid]['probabilities'][str(i)] for i in range(5)], 'model': boundary.model,
                'method': 'Jev · ' + VERSION, 'evidenceIds': [r['id'] for r in selected[cid]]} if supported else None,
            'measurement': {**scores[cid], 'groundedness': judged[cid]['noul']} if supported else None})
    return {'revisionId': job_id, 'criteria': result, 'feedback': [],
            'provenance': {'method': 'jev-evidence-rating', 'promptVersion': VERSION, 'promptHash': PACK_HASH,
                           'model': boundary.model, 'sourceHash': body['sourceHash'], 'coverage': coverage,
                           'redactions': body['redactions'], 'calls': len(metadata),
                           'usage': {k: sum(m['usage'][k] for m in metadata) for k in ('input_tokens', 'output_tokens')}}}


def match_intake(value):
    check(isinstance(value, dict) and set(value) == {'baseJobId', 'vacancyId', 'vacancyHash', 'requirements'})
    check(all(isinstance(value[k], str) and re.fullmatch('[a-f0-9]{64}', value[k]) for k in ('baseJobId', 'vacancyHash')))
    check(isinstance(value['vacancyId'], str) and re.fullmatch('[a-zA-Z0-9_.:-]{1,150}', value['vacancyId']))
    check(isinstance(value['requirements'], list) and 1 <= len(value['requirements']) <= 30)
    requirements = []
    for item in value['requirements']:
        check(isinstance(item, dict) and set(item) == {'id', 'text'} and re.fullmatch('r[0-9]+', item.get('id', '')))
        check(isinstance(item['text'], str) and 1 <= len(item['text']) <= 1800)
        requirements.append({**item, 'text': '\n'.join('[[REDACTED_INJECTION]]' if INJECTION.search(line) else line for line in item['text'].splitlines())})
    return {**value, 'requirements': requirements, 'kind': 'match'}


def match_vacancy(body, job_id, boundary, assessment):
    """Separate advisory comparison. Never re-score skills or accept/reject a person."""
    requirements = body['requirements']
    criteria = assessment['criteria']
    questions = {}
    for c in criteria:
        questions[c['id']] = {'type': 'choice', 'instructions':
            PACK['match'] + '\nНавык: ' + c['label'],
            'criteria': {'evidenced': 'Требование к этому навыку явно есть; приведены конкретные действия кандидата, соответствующие ему.',
                         'clarify': 'Навык явно нужен, но подтверждения достаточного для требования нет или оно противоречиво.',
                         'not_required': 'В вакансии нет явного требования к этому навыку.'}}
    state = {'requirements': requirements, 'skills': [{k: c[k] for k in ('id', 'label', 'state', 'level', 'basis', 'evidence')} for c in criteria]}
    response = boundary.call(job_id, 'vacancy-comparison', state, questions)
    rows = []
    for c in criteria:
        a = response['answers'][c['id']]
        chosen = a['choice']
        if chosen == 'not_required' and a['probabilities'][chosen] >= .6:
            continue
        if a['probabilities'][chosen] < .6 or c['state'] != 'supported':
            chosen = 'clarify'
        rows.append({'id': c['id'], 'label': c['label'], 'status': chosen,
                     'evidenceIds': [e['id'] for e in c['evidence']] if chosen == 'evidenced' else [],
                     'probabilities': a['probabilities'], 'confidence': a['confidence']})
    return {'revisionId': job_id, 'baseRevisionId': body['baseJobId'], 'vacancyId': body['vacancyId'],
            'vacancyHash': body['vacancyHash'], 'requiresReview': True, 'criteria': rows,
            'provenance': {'model': boundary.model, 'promptVersion': VERSION, 'promptHash': PACK_HASH, 'usage': response['usage']}}
