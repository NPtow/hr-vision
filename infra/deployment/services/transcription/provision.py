#!/usr/bin/env python3
"""Run as root on outreach. Read Deepgram key JSON from stdin, never argv.

Prepare isolated runtime credentials without starting ASR. --activate-core is a
separate guarded step after image/tests, preserving existing Core principals.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

CONFIG=Path('/etc/hr-vision-asr')
CORE=Path('/etc/hr-vision-services/core.env')


def read_env(path):
    result={}
    for line in path.read_text().splitlines():
        if line.strip() and not line.startswith('#') and '=' in line:
            key,value=line.split('=',1)
            result[key]=' '.join(shlex.split(value))
    return result


def atomic(path,data):
    temporary=path.with_suffix(path.suffix+'.new')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())
    temporary.replace(path)
    path.chmod(0o600)


def write_env(path,values):
    atomic(path,''.join(key+'='+shlex.quote(str(value))+'\n' for key,value in values.items()))


def quiet(command,*,body=None):
    result=subprocess.run(command,input=body,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError('operator_command_failed: '+command[0])


def wait_ready(principals,timeout=20):
    """Type=simple restart success does not prove the process stayed up."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect)
    deadline=time.monotonic()+timeout
    successes=0
    while time.monotonic()<deadline:
        try:
            quiet(['systemctl','is-active','--quiet','hr-vision-core'])
            with opener.open('http://127.0.0.1:8093/health/ready',timeout=2) as response:
                if response.status!=200 or json.load(response).get('status')!='ready':
                    raise RuntimeError('core_not_ready')
            for principal in principals:
                # The auth dependency executes before lookup. A deliberate absent
                # resource gives404;401/403 or an old config cannot pass this.
                path='/v1/bindings/__transcription_readiness_probe__'
                request=urllib.request.Request('http://127.0.0.1:8093'+path,
                    headers={'Authorization':'Bearer '+principal['token']})
                try:
                    with opener.open(request,timeout=2) as response:
                        raise RuntimeError('probe_resource_must_be_absent')
                except urllib.error.HTTPError as error:
                    if error.code!=404:raise RuntimeError('core_principal_not_ready') from None
            successes+=1
            if successes>=3:return
        except (OSError,ValueError,RuntimeError):
            successes=0
        time.sleep(.5)
    raise RuntimeError('core_readiness_timeout')


def prepare():
    body=json.load(sys.stdin)
    key=body.get('deepgram_api_key')
    if not isinstance(key,str) or len(key)<24 or any(c.isspace() for c in key):
        raise ValueError('invalid_deepgram_key')
    CONFIG.mkdir(mode=0o700,parents=True,exist_ok=True);CONFIG.chmod(0o700)
    identity_file=CONFIG/'credentials.json'
    identity=json.loads(identity_file.read_text()) if identity_file.exists() else {
        name:secrets.token_urlsafe(40) for name in ('ingress','asr','core_read','core_zoom','broker')}
    if not identity_file.exists():
        atomic(identity_file,json.dumps(identity))
    core=read_env(CORE)
    bucket=core.get('CORE_S3_BUCKET') or core.get('HR_MATERIALS_S3_BUCKET')
    prefix=core.get('CORE_S3_PREFIX') or core.get('HR_MATERIALS_S3_PREFIX','materials')
    endpoint=core.get('CORE_S3_ENDPOINT') or core.get('HR_MATERIALS_S3_ENDPOINT')
    region=core.get('CORE_S3_REGION') or core.get('HR_MATERIALS_S3_REGION','ru-central1')
    if not bucket or endpoint!='https://storage.yandexcloud.net':
        raise ValueError('expected_existing_yandex_materials_storage')
    if not all(core.get(k) for k in ('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY')):
        raise ValueError('existing_materials_credentials_missing')
    # Only a new isolated vhost and user: import_definitions merges, never clears.
    salt=secrets.token_bytes(4)
    password_hash=base64.b64encode(salt+hashlib.sha256(salt+identity['broker'].encode()).digest()).decode()
    definitions={'vhosts':[{'name':'hr-transcription'}],
        'users':[{'name':'hr-transcription','password_hash':password_hash,
                  'hashing_algorithm':'rabbit_password_hashing_sha256','tags':[]}],
        'permissions':[{'user':'hr-transcription','vhost':'hr-transcription',
                        'configure':'.*','write':'.*','read':'.*'}]}
    quiet(['docker','exec','-i','hr-vision-materials-rabbitmq','rabbitmqctl','import_definitions'],body=json.dumps(definitions))
    broker='amqp://hr-transcription:'+identity['broker']+'@127.0.0.1:5672/hr-transcription'
    write_env(CONFIG/'asr.env',{
        'TRANSCRIPTION_DATA_DIR':'/var/lib/transcription',
        'TRANSCRIPTION_API_KEYS':json.dumps({'source':identity['asr']}),
        'TRANSCRIPTION_BROKER_URL':broker,'TRANSCRIPTION_BACKEND_KIND':'deepgram',
        'TRANSCRIPTION_DEEPGRAM_API_KEY':key,'TRANSCRIPTION_DEEPGRAM_MODEL':'nova-3',
        'TRANSCRIPTION_DEEPGRAM_LANGUAGE':'ru','TRANSCRIPTION_DEEPGRAM_DIARIZE':'true',
        'TRANSCRIPTION_DEEPGRAM_DIARIZE_MODEL':'v2','TRANSCRIPTION_DEEPGRAM_CONFIG_REVISION':'1',
        'TRANSCRIPTION_MAX_INPUT_BYTES':1073741824,'TRANSCRIPTION_ASR_TIMEOUT_SECONDS':900,
        'AWS_EC2_METADATA_DISABLED':'true'})
    write_env(CONFIG/'source.env',{
        'TRANSCRIPTION_SOURCE_DATA_DIR':'/var/lib/hr-transcription-source',
        'TRANSCRIPTION_SOURCE_CORE_URL':'http://127.0.0.1:8093',
        'TRANSCRIPTION_SOURCE_ASR_URL':'http://127.0.0.1:8106',
        'TRANSCRIPTION_SOURCE_ASR_TOKEN':identity['asr'],
        'TRANSCRIPTION_SOURCE_CALLERS_JSON':json.dumps({'collector':{'token':identity['ingress'],'core_read_token':identity['core_read']}}),
        'TRANSCRIPTION_SOURCE_CORE_SOURCES_JSON':json.dumps([{'provider':'zoom','connection_id':'zoom','token':identity['core_zoom']}]),
        'TRANSCRIPTION_SOURCE_ASR_PROVIDER':'deepgram','TRANSCRIPTION_SOURCE_CONFIG_REVISION':'deepgram-v1',
        'TRANSCRIPTION_SOURCE_DOWNLOAD_HOSTS':'storage.yandexcloud.net,'+bucket+'.storage.yandexcloud.net',
        'TRANSCRIPTION_SOURCE_S3_BUCKET':bucket,'TRANSCRIPTION_SOURCE_S3_PREFIX':prefix,
        'TRANSCRIPTION_SOURCE_S3_ENDPOINT':endpoint,'TRANSCRIPTION_SOURCE_S3_REGION':region,
        'TRANSCRIPTION_SOURCE_BROKER_URL':broker,
        'AWS_ACCESS_KEY_ID':core['AWS_ACCESS_KEY_ID'],'AWS_SECRET_ACCESS_KEY':core['AWS_SECRET_ACCESS_KEY'],
        'AWS_DEFAULT_REGION':region,'AWS_EC2_METADATA_DISABLED':'true'})
    write_env(CONFIG/'collector.env',{'HR_MATERIALS_ASR_ENABLED':'0',
        'HR_MATERIALS_ASR_URL':'http://127.0.0.1:8107','HR_MATERIALS_ASR_TOKEN':identity['ingress'],
        'HR_MATERIALS_ASR_PROVIDERS':'zoom','HR_MATERIALS_ASR_ACTIVATED_AT':str(time.time())})
    for path in (Path('/var/lib/hr-vision-asr'),Path('/var/lib/hr-transcription-source')):
        path.mkdir(mode=0o700,parents=True,exist_ok=True);path.chmod(0o700);os.chown(path,10001,10001)
    print(json.dumps({'prepared':True,'core_changed':False,'asr_started':False,'automatic_capture':False}))


def activate_core():
    identity=json.loads((CONFIG/'credentials.json').read_text())
    core=read_env(CORE)
    db_path=Path(core['CORE_HR_DATABASE'])
    if not str(db_path).startswith('/var/lib/hr-vision-api/'):
        raise ValueError('unexpected_database')
    with sqlite3.connect('file:'+str(db_path)+'?mode=ro',uri=True,timeout=10) as database:
        tables={row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('cp_interviews','cp_meetings','ml_sessions'):
            if table not in tables:continue
            active=database.execute('SELECT count(*) FROM '+table+" WHERE status IN ('live','in_progress','active','started')").fetchone()[0]
            if active:raise RuntimeError('live_meeting_guard')
        if database.execute("SELECT count(*) FROM media_jobs WHERE status='running'").fetchone()[0]:
            raise RuntimeError('active_material_job_guard')
        backup=CONFIG/('hr-before-source-'+str(int(time.time()))+'.sqlite3')
        with sqlite3.connect(backup) as destination:database.backup(destination)
        backup.chmod(0o600)
    principals=json.loads(core['CORE_PRINCIPALS_JSON'])
    previous_principals=list(principals)
    desired=[{'name':'transcription-source-reader','role':'portal','token':identity['core_read']},
        {'name':'transcription-source-zoom','role':'source','provider':'zoom','connection_id':'zoom','token':identity['core_zoom']}]
    for principal in desired:
        existing=[p for p in principals if p.get('name')==principal['name']]
        if existing and existing!=[principal]:raise ValueError('source_principal_conflict')
        if not existing:principals.append(principal)
    previous=CORE.read_text()
    updated='\n'.join('CORE_PRINCIPALS_JSON='+shlex.quote(json.dumps(principals)) if l.startswith('CORE_PRINCIPALS_JSON=') else l for l in previous.splitlines())+'\n'
    if updated!=previous:
        atomic(CONFIG/('core-before-source-'+str(int(time.time()))+'.env'),previous)
        atomic(CORE,updated)
        try:
            quiet(['systemctl','restart','hr-vision-core'])
            wait_ready(desired)
        except Exception:
            atomic(CORE,previous)
            quiet(['systemctl','restart','hr-vision-core'])
            wait_ready(previous_principals)
            raise
    else:
        wait_ready(desired)
    print(json.dumps({'core_principals_added':2,'backup':str(backup),'automatic_capture':False}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','activate-core'])
    args=parser.parse_args()
    if os.geteuid()!=0:raise SystemExit('root required')
    os.umask(0o077)
    try:
        prepare() if args.action=='prepare' else activate_core()
    except Exception as error:
        print(json.dumps({'failed':True,'reason':str(error) if isinstance(error,(RuntimeError,ValueError)) else type(error).__name__}),file=sys.stderr)
        raise SystemExit(1)
