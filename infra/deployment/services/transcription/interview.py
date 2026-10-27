#!/usr/bin/env python3
"""Operator API client: submit one existing recording or inspect its delivery.

Run on the host with read access to /etc/hr-vision-asr. It never reads the HR DB,
downloads media, or calls a recognition provider directly. No secrets in argv.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import urllib.error
import urllib.request


def env(path):
    values={}
    for line in path.read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            key,value=line.split('=',1);values[key]=' '.join(shlex.split(value))
    return values


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def call(origin,path,token,*,body=None,key=None):
    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
    if key:headers['Idempotency-Key']=key
    request=urllib.request.Request(origin+path,headers=headers,
        data=None if body is None else json.dumps(body,separators=(',',':')).encode())
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect)
    try:
        with opener.open(request,timeout=60) as response:
            raw=response.read(25*1024*1024+1)
            if len(raw)>25*1024*1024:raise RuntimeError('response_limit')
            return json.loads(raw)
    except urllib.error.HTTPError as error:
        raise RuntimeError('service_http_'+str(error.code)) from None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    start=sub.add_parser('start');start.add_argument('interview_id')
    start.add_argument('--asset-id')
    status=sub.add_parser('status');status.add_argument('job_id')
    materials=sub.add_parser('materials');materials.add_argument('interview_id')
    args=parser.parse_args()
    import re
    identity=getattr(args,'interview_id',None) or args.job_id
    if not re.fullmatch('[A-Za-z0-9_-]{1,128}',identity):raise ValueError('invalid_identity')
    cfg=env(Path('/etc/hr-vision-asr/source.env'))
    callers=json.loads(cfg['TRANSCRIPTION_SOURCE_CALLERS_JSON'])
    caller=callers['collector']
    core=cfg['TRANSCRIPTION_SOURCE_CORE_URL']
    ingress=env(Path('/etc/hr-vision-asr/collector.env'))['HR_MATERIALS_ASR_URL']
    if args.action=='status':
        result=call(ingress,'/v1/jobs/'+args.job_id,caller['token'])
    else:
        data=call(core,'/v1/owners/interview/'+args.interview_id+'/materials',caller['core_read_token'])
        if args.action=='materials':result=data
        else:
            assets=[a for a in data['assets'] if a['kind'] in ('video','audio') and a['state']=='ready'
                    and (not args.asset_id or a['id']==args.asset_id)]
            if len(assets)!=1:raise ValueError('select_one_ready_recording_with_asset_id')
            asset=assets[0]
            sources=[s for s in data['sources'] if s['id']==asset['source_id'] and not s.get('revoked')]
            if len(sources)!=1:raise ValueError('recording_binding_unavailable')
            source=sources[0]
            event={'owner_kind':'interview','owner_id':args.interview_id,'binding_id':source['id'],
                   'generation':source['generation'],'asset_id':asset['id']}
            identity=[event,cfg['TRANSCRIPTION_SOURCE_ASR_PROVIDER'],cfg['TRANSCRIPTION_SOURCE_CONFIG_REVISION']]
            key='operator:'+hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            result=call(ingress,'/v1/recordings',caller['token'],body=event,key=key)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
