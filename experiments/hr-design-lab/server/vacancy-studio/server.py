"""Isolated vacancy experiment. Adapted from HR Vision's Pulse intake, not its database.

Loopback only. Explicitly accepted proposals form a draft; there are no production writes.
"""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse, unquote, urlencode
import mimetypes
import base64
import copy
import ipaddress
import json
import os
import re
import secrets
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from jsonschema import Draft202012Validator, ValidationError
from task_scenarios import SCHEMA as TASK_SCENARIO_SCHEMA, validate_scenarios, task_catalog
import interview

ROOT = Path(__file__).resolve().parent
LAB = ROOT.parent.parent
VERSION = '2026-10-26.6'
CONTEXT_VERSION = 1
RUBRIC = json.loads((LAB/'src/scenes/vacancy-research/sales-rubric.json').read_text())
PARAMETERS = {p['parameter_id']: p for p in RUBRIC['parameters']}
PORT = int(os.getenv('STUDIO_PORT', '4321'))
BASE_URL = os.getenv('OPENAI_BASE_URL', 'http://127.0.0.1:8317/v1').rstrip('/')
MODEL = os.getenv('STUDIO_MODEL', 'gemini-3.5-flash-lite')
REASONING_EFFORT = os.getenv('STUDIO_REASONING_EFFORT', 'minimal')
JUDGE_MODEL = os.getenv('STUDIO_JUDGE_MODEL', MODEL)
STATE_PATH = Path(os.getenv('STUDIO_STATE', str(LAB/'output/vacancy-studio-20261026/state.sqlite3')))
ORIGINS = {'http://127.0.0.1:6006', 'http://localhost:6006',f'http://127.0.0.1:{PORT}',f'http://localhost:{PORT}'}
STATIC = LAB/'output/vacancy-studio-20261026/storybook'
LOCK = threading.RLock()
POOL = ThreadPoolExecutor(max_workers=3)
LIMIT = threading.BoundedSemaphore(6)
PROMPTS = {p: (ROOT/'prompts'/f'{p}.md').read_text() for p in ('extract', 'interview', 'judge', 'compose')}
PROMPTS['compose'] += (ROOT/'prompts/task-scenarios.md').read_text()
FACT_KEYS = ['role','location','experience','industry','industry_experience','employment','work_mode','salary']

class Problem(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status

def dumps(v):
    return json.dumps(v, ensure_ascii=False, separators=(',', ':'), sort_keys=True)

def db():
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = sqlite3.connect(STATE_PATH, timeout=15)
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
    connection.execute('CREATE TABLE IF NOT EXISTS sources (session TEXT, id TEXT, body TEXT, PRIMARY KEY(session,id))')
    connection.execute('CREATE TABLE IF NOT EXISTS cache (id TEXT PRIMARY KEY, body TEXT)')
    connection.execute('CREATE TABLE IF NOT EXISTS usage (session TEXT PRIMARY KEY, calls INTEGER NOT NULL)')
    connection.commit()
    os.chmod(STATE_PATH, 0o600)
    return connection

def save(state):
    with db() as c:
        c.execute('INSERT OR REPLACE INTO sessions VALUES (?,?)', (state['id'], dumps(state)))

def load(sid):
    if not isinstance(sid,str) or not re.fullmatch(r'[a-f0-9]{48}', sid):
        raise Problem('SESSION_REQUIRED', 401)
    # A first read can migrate a legacy draft. Serialize that read/write with
    # model completion and employer edits so a stale read cannot replace either.
    with LOCK:
        with db() as c:
            row = c.execute('SELECT body FROM sessions WHERE id=?', (sid,)).fetchone()
        if not row:
            raise Problem('SESSION_REQUIRED', 401)
        state = json.loads(row[0])
        changed=normalize_context(state)
        if interview.upgrade(state,PARAMETERS): changed=True
        if changed:
            save(state)
        return state

def now_ms():
    return time.time_ns() // 1_000_000

def normalize_context(state):
    # One-time local migration; accepted/dismissed choices and original evidence stay intact.
    if state.get('context_version') == CONTEXT_VERSION:
        return False
    occupied = {p['key'] for p in state['proposals'] if p['kind']=='fact' and p['status']=='accepted'}
    for p in reversed(state['proposals']):
        if p['kind']=='fact' and p['status']=='pending' and not p.get('manual') and p['key'] not in occupied:
            p.update(status='accepted', automatic=True, hard_filter=False)
            occupied.add(p['key'])
    state['context_version'] = CONTEXT_VERSION
    state['revision'] += 1
    return True

def fresh():
    with LOCK:
        state = {'id': secrets.token_hex(24), 'revision': 0, 'version': VERSION,
                 'context_version': CONTEXT_VERSION, 'interview_version':interview.VERSION, 'rubricVersion': RUBRIC['version'], 'title': 'Новая вакансия', 'summary': '',
                 'messages': [], 'proposals': [], 'sources': [], 'jobs': {}, 'pending': None, 'confirmed': False}
        save(state)
    return state

def public(state):
    s = copy.deepcopy(state)
    s.pop('jobs', None)
    s['vacancy_context'] = {p['key']: {
        'value':p['value'], 'proposal_id':p['id'], 'source_id':p['source_id'],
        'quote':p['quote'], 'reason':p['reason'], 'manual':p.get('manual',False),
        'automatic':p.get('automatic',False), 'hard_filter':p.get('hard_filter',False),
        'requirement_status':('required' if p.get('hard_filter') else 'preferred') if (p.get('manual') or not p.get('automatic')) else p.get('requirement_status','unknown'),
        'suggested_hard_filter':p.get('original',{}).get('hard_filter',False)
    } for p in s['proposals'] if p['kind']=='fact' and p['status']=='accepted'}
    if s['vacancy_context'].get('role'):
        s['title'] = s['vacancy_context']['role']['value']
    question = interview.current_question(state)
    s['active_question_id'] = question['id'] if question else None
    s['interview_progress'] = interview.public_interview(state)
    s.pop('interview', None)
    for message in s['messages']:
        message.pop('option_effects', None)
    return s

def obj(props):
    return {'type':'object','properties':props,'required':list(props),'additionalProperties':False}

def string(n=1000, minimum=0):
    return {'type':'string','minLength':minimum,'maxLength':n}

def arr(item, n):
    return {'type':'array','items':item,'maxItems':n}

EVIDENCE = {'source_id': string(100,1), 'quote':string(1500,1), 'reason':string(800,1)}
EXTRACT_SCHEMA = obj({
    'title':string(100,1), 'summary':string(350),
    'facts':arr(obj({'key':{'enum':FACT_KEYS}, 'value':string(250,1), 'hard_filter':{'type':'boolean'}, **EVIDENCE}),8),
    'skills':arr(obj({'parameter_id':{'enum':list(PARAMETERS)}, 'level':{'type':'integer','minimum':1,'maximum':5},
                      'importance':{'type':'integer','minimum':1,'maximum':3},
                      'confidence':{'enum':['low','medium','high']}, **EVIDENCE}),3)
})
# Optional on stored/legacy outputs; new compose responses include task scenarios.
EXTRACT_SCHEMA['properties']['skills']['items']['properties']['task_scenarios'] = TASK_SCENARIO_SCHEMA
REPLY_SCHEMA = obj({'message':string(380,1)})
JUDGE_SCHEMA = obj({'pass':{'type':'boolean'}})
PLANNING_SCHEMA = obj({
    'skill_topics':arr(obj({'parameter_id':{'enum':list(PARAMETERS)}, **EVIDENCE}),16),
    'conditions':arr(obj({'key':{'enum':list(interview.CONDITIONS)}, 'status':{'enum':['required','preferred','unknown']}, **EVIDENCE}),5),
})
COMPOSE_SCHEMA = obj({'extraction':EXTRACT_SCHEMA, 'planning':PLANNING_SCHEMA, 'reply':REPLY_SCHEMA})

def composition_schema(state):
    schema=copy.deepcopy(COMPOSE_SCHEMA)
    target=state['messages'][-1].get('in_reply_to',{}).get('question_target',{}) if state['messages'] else {}
    conditions=schema['properties']['planning']['properties']['conditions']
    conditions['maxItems']=1 if target.get('kind')=='condition' else 0
    if target.get('kind')=='condition':
        conditions['items']['properties']['key']={'enum':[target['key']]}
    return schema
INJECTION = re.compile(r'ignore\s+(all\s+)?(previous|system)\s+instructions|игнориру[йя].{0,35}(инструкц|системн)|раскрой.{0,20}(ключ|промпт)|reveal.{0,20}(secret|api.key|system.prompt)',re.I)

def ensure_clean(value):
    if INJECTION.search(dumps(value)):
        raise Problem('UNSAFE_INSTRUCTIONS',422)

def validate_evidence(extracted, sources):
    Draft202012Validator(EXTRACT_SCHEMA).validate(extracted)
    source_map = {s['id']:s['text'] for s in sources}
    unique = set()
    for kind in ('facts','skills'):
        for item in extracted[kind]:
            if item['source_id'] not in source_map or item['quote'] not in source_map[item['source_id']]:
                raise ValueError('Unsupported evidence')
            if kind=='facts' and item['hard_filter']:
                if item['key'] in ('role','industry'):
                    raise ValueError('Role and industry are context, not hard filters')
                if re.search(r'не\s*обязател|не\s+нужен|не\s+требуется|не\s+важен', item['quote'],re.I):
                    raise ValueError('Negated hard filter')
                if not re.search(r'обязател|необходим|минимум|не менее|строго|только|must|required|at least',item['quote'],re.I):
                    raise ValueError('No explicit hard filter evidence')
            if kind=='skills' and f"L{item['level']}" not in {level['level_id'] for level in PARAMETERS[item['parameter_id']]['levels']}:
                raise ValueError('Unknown catalog level')
            if kind=='skills': validate_scenarios(item)
            key = (kind, item.get('key',item.get('parameter_id')))
            if key in unique:
                raise ValueError('Duplicate proposal')
            unique.add(key)

def validate_composition(result, sources):
    # Structural and literal-evidence checks, not an independent semantic judge.
    ensure_clean(result)
    Draft202012Validator(COMPOSE_SCHEMA).validate(result)
    validate_evidence(result['extraction'], sources)
    if '?' in result['reply']['message'] or '？' in result['reply']['message']:
        raise ValueError('Unbound question')
    # A literal citation can still be empty evidence: "Ищем SDR" names a role,
    # not a work task. Reject this mechanically rather than trusting self-confidence.
    context_values=[result['extraction']['title']]+[f['value'] for f in result['extraction']['facts']]
    for item in result['extraction']['skills'] + result['planning']['skill_topics']:
        remainder=item['quote'].casefold()
        for value in sorted(context_values,key=len,reverse=True):
            remainder=re.sub(r'(?<!\w)'+re.escape(value.casefold())+r'(?!\w)',' ',remainder)
        remainder=re.sub(r'\b(ищем|ищу|нужен|нужна|нужны|нужно|требуется|вакансия|сотрудник|сотрудника|человек|человека|нам|мне|на|роль|роли|позицию|позиции|должность|должности|команду|команде|в|и|с|у|нас)\b',' ',remainder)
        if not re.search(r'\w',remainder): raise ValueError('Role without work task')
    source_map = {s['id']:s['text'] for s in sources}
    for group in ('skill_topics','conditions'):
        unique=set()
        for item in result['planning'][group]:
            if item['source_id'] not in source_map or item['quote'] not in source_map[item['source_id']]:
                raise ValueError('Unsupported evidence')
            key=item.get('key',item.get('parameter_id'))
            if key in unique: raise ValueError('Duplicate proposal')
            unique.add(key)
            if group=='conditions' and item['status']=='required':
                if re.search(r'не\s*обязател|не\s+нужен|не\s+требуется|не\s+важен',item['quote'],re.I): raise ValueError('Negated hard filter')
                if not re.search(r'обязател|необходим|минимум|не менее|строго|только|без.{0,70}не.{0,20}(рассматр|подход)|must|required|at least',item['quote'],re.I):
                    raise ValueError('No explicit hard filter evidence')
            if group=='conditions' and item['status']=='preferred' and not re.search(r'не\s*обязател|не\s+нужен|не\s+требуется|не\s+важ|желател|готов.{0,40}(без|обуч|рассмотр)|ограничени.{0,30}нет|не исключа|не принципиаль',item['quote'],re.I):
                raise ValueError('No explicit optional evidence')

def validation_failure(error):
    # Only fixed codes and schema paths. Never echo source text or rejected values to logs.
    codes = {'Unsupported evidence':'UNSUPPORTED_EVIDENCE',
             'Role and industry are context, not hard filters':'CONTEXT_AS_FILTER',
             'Negated hard filter':'NEGATED_FILTER',
             'No explicit hard filter evidence':'FILTER_WITHOUT_OBLIGATION',
             'No explicit optional evidence':'OPTIONAL_WITHOUT_EVIDENCE',
             'Unbound question':'UNBOUND_QUESTION',
             'Role without work task':'ROLE_WITHOUT_TASK',
             'Unknown catalog level':'UNKNOWN_LEVEL',
             'Duplicate proposal':'DUPLICATE_PROPOSAL',
             'Duplicate answer options':'DUPLICATE_OPTIONS'}
    if isinstance(error, Problem):
        return {'code':'UNSAFE_OUTPUT'}
    if isinstance(error, json.JSONDecodeError):
        return {'code':'INVALID_JSON'}
    if isinstance(error, ValidationError):
        allowed={'extraction','reply','title','summary','facts','skills','key','value','hard_filter',
                 'source_id','quote','reason','parameter_id','level','importance','confidence',
                 'message','question','options','planning','skill_topics','conditions','status'}
        return {'code':'INVALID_SCHEMA','path':[p if isinstance(p,int) or p in allowed else '?' for p in error.absolute_path]}
    return {'code':codes.get(str(error),'INVALID_OUTPUT')}

def model_call(stage, payload, schema, sid, validator=None, *, on_repair=None):
    ensure_clean(payload)
    encoded = dumps(payload)
    if len(encoded.encode()) > 180_000:
        raise Problem('CORPUS_TOO_LARGE',413)
    model = JUDGE_MODEL if stage == 'judge' else MODEL
    digest = sha256(dumps([VERSION,stage,model,REASONING_EFFORT,PROMPTS[stage],payload]).encode()).hexdigest()
    with db() as c:
        row = c.execute('SELECT body FROM cache WHERE id=?',(digest,)).fetchone()
    if row:
        result=json.loads(row[0])
        if validator: validator(result)
        return result
    credential = os.getenv('OPENAI_API_KEY')
    if not credential: raise Problem('MODEL_NOT_CONFIGURED',503)
    fence = 'DATA_'+secrets.token_hex(16)
    messages = [{'role':'system','content':PROMPTS[stage]}, {'role':'user','content':f'<{fence}>\n{encoded}\n</{fence}>'}]
    for attempt in range(2):
        with LOCK, db() as c:
            c.execute('INSERT OR IGNORE INTO usage VALUES (?,0)',(sid,))
            calls=c.execute('SELECT calls FROM usage WHERE session=?',(sid,)).fetchone()[0]
            if calls>=150: raise Problem('MODEL_BUDGET',429)
            c.execute('UPDATE usage SET calls=calls+1 WHERE session=?',(sid,))
        body={'model':model,'messages':messages,'stream':False,'reasoning_effort':REASONING_EFFORT,'max_tokens':5000 if stage in ('extract','compose') else 1200,
              'response_format':{'type':'json_schema','json_schema':{'name':stage,'strict':True,'schema':schema}}}
        req=urllib.request.Request(BASE_URL+'/chat/completions',dumps(body).encode(),headers={
            'Content-Type':'application/json','Authorization':'Bearer '+credential,'Idempotency-Key':digest})
        if attempt and on_repair:
            on_repair()
        start=time.monotonic()
        try:
            with urllib.request.urlopen(req,timeout=55) as response:
                envelope=json.loads(response.read(500_000))
            content=envelope['choices'][0]['message']['content']
        except Exception:
            raise Problem('MODEL_UNAVAILABLE',503)
        print(dumps({'stage':stage,'model':model,'resolved_model':envelope.get('model',model),'reasoning_effort':REASONING_EFFORT,'input_hash':digest[:16],
                     'ms':round((time.monotonic()-start)*1000),'attempt':attempt,'usage':envelope.get('usage')}),flush=True)
        try:
            result=json.loads(content)
            ensure_clean(result)
            Draft202012Validator(schema).validate(result)
            if validator: validator(result)
        except Exception as error:
            failure=validation_failure(error)
            print(dumps({'stage':stage,'input_hash':digest[:16],'attempt':attempt,'validation_error':failure}),flush=True)
            if attempt: raise Problem('INVALID_MODEL_OUTPUT',502)
            messages.append({'role':'assistant','content':content})
            messages.append({'role':'user','content':'VALIDATION_ERROR '+dumps(failure)+'. Исправь указанный дефект в предыдущем JSON, сохрани корректные факты. Верни весь JSON по схеме. Все source_id должны существовать, цитаты быть дословными; не дублируй параметры. role и industry всегда hard_filter=false. Для hard_filter=true нужна узкая цитата с явной обязательностью и без отрицания; иначе укажи false. OPTIONAL_WITHOUT_EVIDENCE: простое упоминание условия не значит preferred. Без прямой необязательности ставь unknown или убери conditions. ROLE_WITHOUT_TASK: в цитате только роль или общие факты. Удали такие навыки и skill_topics; при отсутствии описания задач оба массива пустые. UNBOUND_QUESTION: убери вопрос из message, следующий вопрос задаёт код. Не добавляй пояснения вне JSON.'})
            continue
        with db() as c: c.execute('INSERT OR REPLACE INTO cache VALUES (?,?)',(digest,dumps(result)))
        return result
    raise Problem('INVALID_MODEL_OUTPUT',502)

def source(sid, source_id):
    with db() as c:
        row=c.execute('SELECT body FROM sources WHERE session=? AND id=?',(sid,source_id)).fetchone()
    if not row: raise Problem('SOURCE_NOT_FOUND',404)
    return json.loads(row[0])

def corpus(state):
    result=[]
    for msg in state['messages']:
        if msg['role']=='user':
            item={'id':msg['id'],'name':'Ответ работодателя','text':msg['content']}
            if msg.get('in_reply_to'): item['answer_to']=msg['in_reply_to']
            result.append(item)
    used={i for m in state['messages'] for i in m.get('sourceIds',[])}
    result += [source(state['id'],i) for i in sorted(used)]
    return result

def phase(sid, job_id, value):
    with LOCK:
        s=load(sid)
        s['jobs'][job_id].update(phase=value,updated_at=now_ms(),phase_started_at=now_ms())
        s['pending']=s['jobs'][job_id].copy()
        save(s)

def run_turn(sid, job_id):
    try:
        state=load(sid)
        sources=corpus(state)
        existing=[p for p in state['proposals'] if p['status'] in ('accepted','pending','dismissed')]
        schema=composition_schema(state)
        current_target=state['messages'][-1].get('in_reply_to',{}).get('question_target',{})
        phase(sid,job_id,'compose')
        payload={'catalog':task_catalog(RUBRIC),'sources':sources,'profile':{'title':state['title'],'proposals':existing},
                 'current_condition':current_target['key'] if current_target.get('kind')=='condition' else None,
                 'interview':state.get('interview',{}), 'recent_conversation':state['messages'][-8:]}
        def validate_turn(result):
            Draft202012Validator(schema).validate(result)
            validate_composition(result,sources)
        composed=model_call('compose',payload,schema,sid,validate_turn,
                            on_repair=lambda:phase(sid,job_id,'repair'))
        phase(sid,job_id,'validate')
        # Revalidate at the write boundary even if the provider/cache implementation changes.
        validate_turn(composed)
        extraction,reply=composed['extraction'],composed['reply']
        with LOCK:
            s=load(sid)
            ids=[]
            for kind,items in [('fact',extraction['facts']),('skill',extraction['skills'])]:
                for item in items:
                    identity=item.get('key',item.get('parameter_id'))
                    same=[p for p in s['proposals'] if p['kind']==kind and p.get('key',p.get('parameter_id'))==identity and p['status'] in ('accepted','pending')]
                    if same:
                        if any(p.get('manual') or (p['status']=='accepted' and not (kind=='fact' and p.get('automatic'))) for p in same): continue
                        newest=s['messages'][-1]
                        new_sources={newest['id'],*newest.get('sourceIds',[])}
                        if item['source_id'] not in new_sources or all(p['original']==item for p in same): continue
                        # Keep historical evidence. A new answer can refine an unaccepted proposal.
                        for old in same: old['status']='superseded'
                        if kind=='fact' and any(p['value'] != item['value'] for p in same):
                            s.get('interview',{}).get('decisions',{}).pop(identity,None)
                    if any(p['kind']==kind and p.get('key',p.get('parameter_id'))==identity and p['status']=='dismissed' for p in s['proposals']): continue
                    proposal={**item,'id':secrets.token_hex(8),'kind':kind,'status':'pending','manual':False,'original':copy.deepcopy(item)}
                    if kind=='fact': proposal.update(status='accepted',automatic=True,hard_filter=False)
                    s['proposals'].append(proposal)
                    ids.append(proposal['id'])
            s['title'],s['summary']=extraction['title'],extraction['summary']
            interview.record_planning(s,composed['planning'],s['messages'][-1])
            interview.append_reply(s,job_id,reply['message'],ids,PARAMETERS)
            s['jobs'][job_id].update(status='done',phase='done',updated_at=now_ms(),phase_started_at=now_ms())
            s['pending']=None
            s['version']=VERSION
            s['revision']+=1
            s['confirmed']=False
            save(s)
    except Exception as e:
        with LOCK:
            s=load(sid)
            code=e.code if isinstance(e,Problem) else 'PROCESSING_FAILED'
            s['jobs'][job_id].update(status='failed',phase='failed',error=code,updated_at=now_ms(),phase_started_at=now_ms())
            s['pending']=s['jobs'][job_id].copy()
            s['revision']+=1
            save(s)
        print(dumps({'stage':'turn','status':'failed','code':code}),flush=True)
    finally:
        LIMIT.release()

def start_turn(sid, body):
    text=body.get('message','')
    job_id=body.get('message_id','')
    source_ids=body.get('sourceIds',[])
    if not isinstance(text,str) or not 1<=len(text.strip())<=6000 or not re.fullmatch(r'[a-zA-Z0-9-]{12,80}',job_id):
        raise Problem('INVALID_INPUT')
    if not isinstance(source_ids,list) or len(source_ids)>4 or not all(isinstance(i,str) for i in source_ids): raise Problem('INVALID_INPUT')
    ensure_clean(text)
    answer=body.get('answer')
    if answer is not None and (not isinstance(answer,dict) or len(answer)!=1 or set(answer)-{'option_index','defer'} or ('defer' in answer and answer['defer'] is not True)):
        raise Problem('INVALID_INPUT')
    if answer is not None and source_ids: raise Problem('INVALID_INPUT')
    fingerprint=sha256(dumps([text,source_ids]+([body.get('reply_to'),answer] if answer is not None else [])).encode()).hexdigest()
    with LOCK:
        s=load(sid)
        previous=s['jobs'].get(job_id)
        if previous:
            if previous['fingerprint']!=fingerprint: raise Problem('IDEMPOTENCY_CONFLICT',409)
            if previous['status']!='failed' or not body.get('retry'): return public(s)
        if body.get('revision')!=s['revision']: raise Problem('REVISION_CONFLICT',409)
        if s['pending'] and s['pending']['status']=='running': raise Problem('TURN_RUNNING',409)
        if len(s['jobs'])>=40 and not previous: raise Problem('CONVERSATION_LIMIT',429)
        try:
            if previous:
                context=next(m for m in s['messages'] if m['id']==job_id).get('in_reply_to',{})
                effect,answer_text=None,None
            else:
                context,effect,answer_text = interview.answer_context(s,body.get('reply_to'),
                    answer.get('option_index') if answer else None, bool(answer and answer.get('defer')))
        except ValueError:
            raise Problem('STALE_QUESTION',409)
        if effect and text != answer_text: raise Problem('INVALID_INPUT')
        for i in source_ids: source(sid,i)
        if effect:
            message={'id':job_id,'role':'user','content':text,'sourceIds':[], 'in_reply_to':context, 'answer':answer}
            ids,content=interview.apply_choice(s,message,effect,context,lambda:secrets.token_hex(8))
            s['messages'].append(message)
            interview.append_reply(s,job_id,content,ids,PARAMETERS)
            s['jobs'][job_id]={'id':job_id,'fingerprint':fingerprint,'status':'done','phase':'done','prompt_version':VERSION,
                               'template_id':'structured-answer','model':None,'started_at':now_ms(),'updated_at':now_ms()}
            s.update(pending=None,confirmed=False,revision=s['revision']+1,version=VERSION)
            save(s)
            return public(s)
        if not LIMIT.acquire(blocking=False): raise Problem('BUSY',429)
        try:
            stamp=now_ms()
            job={'id':job_id,'fingerprint':fingerprint,'status':'running','phase':'queued',
                 'prompt_version':VERSION,'template_id':'compose','model':MODEL,'reasoning_effort':REASONING_EFFORT,
                 'started_at':previous.get('started_at',stamp) if previous else stamp,
                 'attempt_started_at':stamp,'updated_at':stamp,'phase_started_at':stamp}
            s['jobs'][job_id]=job
            s['pending']=job.copy()
            if not previous:
                s['messages'].append({'id':job_id,'role':'user','content':text,'sourceIds':source_ids,'in_reply_to':context})
            s['revision']+=1
            s['confirmed']=False
            save(s)
            POOL.submit(run_turn,sid,job_id)
        except Exception:
            LIMIT.release()
            raise
        return public(s)

def mutate(sid,body):
    with LOCK:
        s=load(sid)
        if body.get('revision')!=s['revision']: raise Problem('REVISION_CONFLICT',409)
        if s['pending'] and s['pending']['status']=='running': raise Problem('TURN_RUNNING',409)
        action=body.get('action')
        if action=='confirm':
            if not any(p['status']=='accepted' for p in s['proposals']): raise Problem('EMPTY_PROFILE')
            s['confirmed']=True
        else:
            p=next((p for p in s['proposals'] if p['id']==body.get('id')),None)
            if not p: raise Problem('PROPOSAL_NOT_FOUND',404)
            if p['status']=='superseded': raise Problem('PARAMETER_ALREADY_PRESENT',409)
            if action in ('accept','dismiss','restore'):
                if action!='dismiss' and any(q['id']!=p['id'] and q['kind']==p['kind'] and q.get('key',q.get('parameter_id'))==p.get('key',p.get('parameter_id')) and q['status'] in ('accepted','pending') for q in s['proposals']):
                    raise Problem('PARAMETER_ALREADY_PRESENT',409)
                p['status']={'accept':'accepted','dismiss':'dismissed','restore':'pending'}[action]
                if p['kind']=='fact': p['automatic']=False
            elif action=='edit':
                if p['kind']=='skill':
                    level,importance=body.get('level'),body.get('importance')
                    if type(level)!=int or level not in range(1,6) or type(importance)!=int or importance not in range(1,4): raise Problem('INVALID_INPUT')
                    p.update(level=level,importance=importance,manual=True)
                else:
                    value=body.get('value')
                    if not isinstance(value,str) or not 1<=len(value.strip())<=250 or type(body.get('hard_filter'))!=bool: raise Problem('INVALID_INPUT')
                    if p['key'] in ('role','industry') and body['hard_filter']: raise Problem('CONTEXT_NOT_FILTER')
                    ensure_clean(value)
                    p.update(value=value.strip(),hard_filter=body['hard_filter'],manual=True,requirement_status='required' if body['hard_filter'] else 'preferred')
            else: raise Problem('INVALID_ACTION')
            s['confirmed']=False
            last=s['messages'][-1] if s['messages'] else None
            if last and last.get('question_target') and not interview.eligible(s,last['question_target']):
                interview.append_reply(s,'edit-'+secrets.token_hex(8),'Сохранили решение по этому требованию.',[],PARAMETERS)
        s['revision']+=1
        save(s)
        return public(s)

def add_source(sid,name,text,kind):
    text=text.strip()
    if not text: raise Problem('EMPTY_DOCUMENT',422)
    if len(text)>60_000: raise Problem('DOCUMENT_TOO_LONG',413)
    ensure_clean(text)
    item={'id':'source-'+sha256((name+text).encode()).hexdigest()[:24],'name':name[:180],'text':text,'kind':kind}
    with LOCK,db() as c:
        count=c.execute('SELECT COUNT(*) FROM sources WHERE session=?',(sid,)).fetchone()[0]
        if count>=20: raise Problem('SOURCE_LIMIT',429)
        c.execute('INSERT OR IGNORE INTO sources VALUES (?,?,?)',(sid,item['id'],dumps(item)))
    return {'id':item['id'],'name':item['name'],'characters':len(text),'kind':kind}

def file_text(name,data):
    suffix=Path(name).suffix.lower()
    if len(data)>5_000_000: raise Problem('FILE_TOO_LARGE',413)
    try:
        if suffix in ('.txt','.md','.csv'):
            return data.decode('utf-8-sig')
        if suffix=='.pdf':
            from pypdf import PdfReader
            reader=PdfReader(BytesIO(data))
            if reader.is_encrypted or len(reader.pages)>60: raise Problem('PDF_UNSUPPORTED',422)
            return '\n\n'.join(p.extract_text() or '' for p in reader.pages)
        if suffix=='.docx':
            with zipfile.ZipFile(BytesIO(data)) as z:
                info=z.getinfo('word/document.xml')
                if info.file_size>10_000_000: raise Problem('FILE_TOO_LARGE',413)
                root=ET.fromstring(z.read(info))
                ns='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
                return '\n'.join(''.join(p.itertext()) for p in root.iter(ns+'p'))
    except Problem: raise
    except Exception: raise Problem('FILE_UNREADABLE',422)
    raise Problem('FILE_TYPE',415)

class PageText(HTMLParser):
    def __init__(self):
        super().__init__(); self.skip=0; self.parts=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','noscript','svg'): self.skip+=1
        if tag in ('p','div','h1','h2','h3','li','br'): self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript','svg'): self.skip=max(0,self.skip-1)
    def handle_data(self,data):
        if not self.skip: self.parts.append(data)

def public_url(url):
    p=urlparse(url)
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.port not in (None,443): raise Problem('LINK_UNSAFE')
    try: ips={r[4][0] for r in socket.getaddrinfo(p.hostname,443,type=socket.SOCK_STREAM)}
    except OSError: raise Problem('LINK_UNAVAILABLE',422)
    # Local VPN fake-DNS addresses cannot be used as targets. Ask a fixed public
    # resolver for the real A record, validate again, and pin that public address.
    # Private/link-local answers never become permitted network destinations.
    fake_network=ipaddress.ip_network('198.18.0.0/15')
    if ips and all(ipaddress.ip_address(i) in fake_network for i in ips):
        try:
            req=urllib.request.Request('https://cloudflare-dns.com/dns-query?'+urlencode({'name':p.hostname,'type':'A'}),headers={'Accept':'application/dns-json'})
            with urllib.request.urlopen(req,timeout=8) as response: answer=json.loads(response.read(32_000))
            ips={a['data'] for a in answer.get('Answer',[]) if a.get('type')==1} if answer.get('Status')==0 else set()
        except Exception: raise Problem('LINK_UNAVAILABLE',422)
    if not ips or any(not ipaddress.ip_address(i).is_global for i in ips): raise Problem('LINK_UNSAFE')
    return p,ips

def link_text(url):
    # Resolve and pin the connection address. No redirect can bypass the address check.
    import http.client
    import ssl
    p,ips=public_url(url)
    class PinnedHTTPS(http.client.HTTPSConnection):
        def connect(self):
            self.sock=socket.create_connection((sorted(ips,key=lambda x:(':' in x,x))[0],443),timeout=15)
            self.sock=ssl.create_default_context().wrap_socket(self.sock,server_hostname=p.hostname)
    conn=PinnedHTTPS(p.hostname,timeout=15)
    try:
        path=(p.path or '/')+('?' + p.query if p.query else '')
        conn.request('GET',path,headers={'User-Agent':'HRVision-VacancyStudio/1.0','Accept':'text/html,text/plain'})
        res=conn.getresponse()
        if res.status!=200: raise Problem('LINK_UNAVAILABLE',422)
        if not any(t in res.getheader('Content-Type','') for t in ('text/html','text/plain')): raise Problem('LINK_TYPE',415)
        data=res.read(1_500_001)
        if len(data)>1_500_000: raise Problem('FILE_TOO_LARGE',413)
        parser=PageText(); parser.feed(data.decode('utf-8','replace'))
        text=re.sub(r'[ \t]+',' ',' '.join(parser.parts))
        text=re.sub(r'\n\s*\n+','\n',text).strip()
        if len(text)<100: raise Problem('LINK_UNAVAILABLE',422)
        return text
    except Problem: raise
    except Exception: raise Problem('LINK_UNAVAILABLE',422)
    finally: conn.close()

def transcribe(sid,data,mime):
    if mime.split(';')[0] not in {'audio/webm','audio/mp4','audio/ogg','audio/wav','audio/x-wav','audio/mpeg'}: raise Problem('AUDIO_TYPE',415)
    if not 100<=len(data)<=5_000_000: raise Problem('FILE_TOO_LARGE',413)
    if not (data.startswith((b'\x1a\x45\xdf\xa3',b'OggS',b'RIFF',b'ID3',b'\xff')) or data[4:8]==b'ftyp'): raise Problem('AUDIO_TYPE',415)
    credential=os.getenv('DEEPGRAM_API_KEY')
    if not credential: raise Problem('VOICE_NOT_CONFIGURED',503)
    digest=sha256(sid.encode()+data).hexdigest()
    with LOCK,db() as c:
        cached=c.execute('SELECT body FROM cache WHERE id=?',('audio-'+digest,)).fetchone()
        if cached: return json.loads(cached[0])
        usage_id='audio-'+sid
        c.execute('INSERT OR IGNORE INTO usage VALUES (?,0)',(usage_id,))
        count=c.execute('SELECT calls FROM usage WHERE session=?',(usage_id,)).fetchone()[0]
        if count>=60: raise Problem('VOICE_BUDGET',429)
        c.execute('UPDATE usage SET calls=calls+1 WHERE session=?',(usage_id,))
    req=urllib.request.Request('https://api.deepgram.com/v1/listen?model=nova-3&language=ru&smart_format=true&punctuate=true&mip_opt_out=true',data,
                               headers={'Content-Type':mime,'Authorization':'Token '+credential})
    try:
        with urllib.request.urlopen(req,timeout=60) as r: payload=json.loads(r.read(1_000_000))
        text=payload['results']['channels'][0]['alternatives'][0]['transcript'].strip()
        if payload['metadata']['duration']>125: raise Problem('AUDIO_TOO_LONG',413)
        if not text: raise Problem('NO_SPEECH',422)
        if len(text)>6000: raise Problem('DOCUMENT_TOO_LONG',413)
    except Problem: raise
    except Exception: raise Problem('TRANSCRIPTION_FAILED',503)
    result={'text':text}
    with db() as c: c.execute('INSERT OR REPLACE INTO cache VALUES (?,?)',('audio-'+digest,dumps(result)))
    return result

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send(self,status,body):
        data=dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        if self.headers.get('Origin') in ORIGINS:
            self.send_header('Access-Control-Allow-Origin',self.headers['Origin'])
            self.send_header('Vary','Origin')
        self.end_headers(); self.wfile.write(data)
    def check_origin(self):
        if self.headers.get('Host') not in {f'127.0.0.1:{PORT}',f'localhost:{PORT}'}: raise Problem('FORBIDDEN',403)
        origin=self.headers.get('Origin')
        # CLI acceptance has no Origin. Browser cross-origin requests are explicit and restricted.
        if origin and origin not in ORIGINS: raise Problem('FORBIDDEN',403)
        if self.headers.get('Sec-Fetch-Site')=='cross-site' and origin not in ORIGINS: raise Problem('FORBIDDEN',403)
    def sid(self):
        token=self.headers.get('Authorization','').removeprefix('Bearer ')
        load(token)
        return token
    def raw(self,limit=7_000_000):
        try: size=int(self.headers.get('Content-Length','0'))
        except ValueError: raise Problem('INVALID_INPUT')
        if size<0 or size>limit: raise Problem('FILE_TOO_LARGE',413)
        return self.rfile.read(size)
    def do_OPTIONS(self):
        try:
            self.check_origin()
            self.send_response(204)
            if self.headers.get('Origin') in ORIGINS: self.send_header('Access-Control-Allow-Origin',self.headers['Origin'])
            self.send_header('Access-Control-Allow-Methods','GET,POST,OPTIONS')
            self.send_header('Access-Control-Allow-Headers','Authorization,Content-Type')
            self.send_header('Access-Control-Max-Age','600')
            self.end_headers()
        except Problem as e: self.send(e.status,{'error':e.code})
    def do_GET(self):
        try:
            self.check_origin()
            if self.path=='/healthz': return self.send(200,{'ok':True,'version':VERSION,'model':MODEL,'reasoning_effort':REASONING_EFFORT,'voice':bool(os.getenv('DEEPGRAM_API_KEY')),'rubricVersion':RUBRIC['version']})
            if self.path=='/api/state': return self.send(200,public(load(self.sid())))
            pathname=unquote(urlparse(self.path).path).lstrip('/') or 'iframe.html'
            target=(STATIC/pathname).resolve()
            if target.is_relative_to(STATIC.resolve()) and target.is_file() and target.suffix.lower() in {'.html','.js','.css','.json','.svg','.png','.jpg','.woff','.woff2','.ttf','.ico'}:
                data=target.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type',mimetypes.guess_type(target)[0] or 'application/octet-stream')
                self.send_header('Content-Length',str(len(data)))
                self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Cache-Control','no-cache')
                self.end_headers();self.wfile.write(data);return
            raise Problem('NOT_FOUND',404)
        except Problem as e: self.send(e.status,{'error':e.code})
    def do_POST(self):
        try:
            self.check_origin()
            if self.path=='/api/sessions':
                if self.headers.get('Content-Type')!='application/json': raise Problem('INVALID_INPUT')
                self.raw(100)
                return self.send(201,public(fresh()))
            sid=self.sid()
            if self.path=='/api/transcribe': return self.send(200,transcribe(sid,self.raw(5_000_000),self.headers.get('Content-Type','')))
            if self.headers.get('Content-Type')!='application/json': raise Problem('INVALID_INPUT')
            body=json.loads(self.raw())
            if not isinstance(body,dict): raise Problem('INVALID_INPUT')
            if self.path=='/api/turn': return self.send(202,start_turn(sid,body))
            if self.path=='/api/proposals': return self.send(200,mutate(sid,body))
            if self.path=='/api/sources/file':
                name=body.get('name','')
                if not isinstance(name,str) or len(name)>200: raise Problem('INVALID_INPUT')
                try: data=base64.b64decode(body.get('data',''),validate=True)
                except Exception: raise Problem('INVALID_INPUT')
                return self.send(201,add_source(sid,name,file_text(name,data),'file'))
            if self.path=='/api/sources/link':
                url=body.get('url','')
                if not isinstance(url,str) or len(url)>2000: raise Problem('INVALID_INPUT')
                return self.send(201,add_source(sid,url,link_text(url),'link'))
            raise Problem('NOT_FOUND',404)
        except Problem as e: self.send(e.status,{'error':e.code})
        except (json.JSONDecodeError,UnicodeError,TypeError,ValueError): self.send(400,{'error':'INVALID_INPUT'})
        except Exception: self.send(500,{'error':'SERVER_ERROR'})

if __name__=='__main__':
    with LOCK,db() as c:
        for sid,body in c.execute('SELECT id,body FROM sessions').fetchall():
            state=json.loads(body)
            if state.get('pending') and state['pending']['status']=='running':
                job=state['jobs'][state['pending']['id']]
                job.update(status='failed',phase='failed',error='SERVER_RESTARTED',updated_at=now_ms(),phase_started_at=now_ms())
                state['pending']=job.copy(); state['revision']+=1
                c.execute('UPDATE sessions SET body=? WHERE id=?',(dumps(state),sid))
    print(f'Vacancy studio ready on 127.0.0.1:{PORT}; model={MODEL}; rubric={RUBRIC["version"]}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',PORT),Handler).serve_forever()
