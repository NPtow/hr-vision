"""Opt-in IDs-only notification; coordinator/Core own ASR authorization.

Reconciliation retries HTTP acknowledgement loss with the same event identity.
A required activation timestamp bounds automatic processing to recordings first
discovered after activation; a binding may have been scheduled before activation.
"""
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class ASRDispatch:
    def __init__(self,url,token,providers,activated_at,*,send=None,checkpoint=None,batch_limit=3):
        parsed=urlsplit(url)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Invalid ASR coordinator URL')
        if parsed.scheme=='http' and parsed.hostname not in ('localhost','127.0.0.1','::1'):
            raise ValueError('ASR coordinator requires TLS or loopback')
        if not token or len(token)<32 or not providers or not math.isfinite(activated_at) or activated_at<=0:
            raise ValueError('ASR token, providers and activation timestamp required')
        self.url,self.token,self.providers,self.activated_at=url.rstrip('/'),token,frozenset(providers),activated_at
        self.send=send or self._send
        self.batch_limit=batch_limit
        self.checkpoint=str(checkpoint) if checkpoint else ':memory:'
        if checkpoint:
            Path(checkpoint).parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.ack=sqlite3.connect(self.checkpoint,check_same_thread=False,timeout=3)
        self.ack.execute('CREATE TABLE IF NOT EXISTS accepted(event_key TEXT PRIMARY KEY, accepted_at REAL NOT NULL)')
        self.ack.commit()

    def _send(self,event,key):
        request=urllib.request.Request(self.url+'/v1/recordings',data=json.dumps(event).encode(),
            headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json','Idempotency-Key':key},method='POST')
        # This configured service endpoint is never taken from provider/request
        # data. Disable redirects so the bearer cannot leave its origin.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):
                return None
        with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect).open(request,timeout=3) as response:
            if response.status!=202:
                raise ValueError('ASR notification rejected')
            if len(response.read(4097))>4096:
                raise ValueError('ASR notification response too large')

    def dispatch(self,sources,repository):
        accepted=failed=attempted=0
        for source in sources:
            if (source['provider'] not in self.providers or source.get('revoked') or
                    source['metadata'].get('capture_disabled')):
                continue
            for asset in repository.list_assets(source['id']):
                # Discovery time is immutable. A later ready transition, copy,
                # location or generation cannot turn an existing asset into a
                # new automatic request. Pre-activation assets use manual API.
                if (asset['kind'] not in ('video','audio') or asset['state']!='ready' or
                        asset['created_at']<self.activated_at):
                    continue
                event={'owner_kind':source['owner_kind'],'owner_id':source['owner_id'],'binding_id':source['id'],
                       'generation':source['generation'],'asset_id':asset['id']}
                key='recording:'+hashlib.sha256(json.dumps(event,sort_keys=True,separators=(',',':')).encode()).hexdigest()
                if self.ack.execute('SELECT 1 FROM accepted WHERE event_key=?',(key,)).fetchone():
                    continue
                if attempted>=self.batch_limit:
                    return {'accepted':accepted,'failed':failed}
                attempted+=1
                try:
                    self.send(event,key)
                    with self.ack:
                        self.ack.execute('INSERT OR IGNORE INTO accepted VALUES(?,?)',(key,time.time()))
                    accepted+=1
                except Exception:
                    # Keep discovery independent; next scheduled reconciliation
                    # retries, without logging URLs/token/exception payload.
                    failed+=1
        return {'accepted':accepted,'failed':failed}


def from_env(env):
    if env.get('HR_MATERIALS_ASR_ENABLED','0')!='1':
        return None
    return ASRDispatch(env['HR_MATERIALS_ASR_URL'],env['HR_MATERIALS_ASR_TOKEN'],
        [p.strip() for p in env.get('HR_MATERIALS_ASR_PROVIDERS','').split(',') if p.strip()],
        float(env['HR_MATERIALS_ASR_ACTIVATED_AT']),
        checkpoint=env.get('HR_MATERIALS_ASR_DISPATCH_DB',str(Path(env.get('HR_DATA_DIR','/var/lib/hr-vision-api'))/'asr-dispatch.sqlite3')))
