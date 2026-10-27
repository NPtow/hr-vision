#!/usr/bin/env python3
"""Import reviewed local original recordings into an already authorized upload room.

No HR DB access, room mutation, ASR submission, concatenation or provider fetch.
Run with the source package on PYTHONPATH and a protected source.env. A manifest
is a JSON array of {owner_kind,owner_id,candidate_id,candidate_name,connection_id,
capture_instance_id,files:[{path,sha256,size,duration,format,content_type}]}.
Stdout contains counts/hash only; --output stores private IDs/audit as mode0600.
"""
import argparse
from dataclasses import dataclass,field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import stat
import sys
from urllib.parse import quote,urlsplit

import httpx


class ImportFailure(ValueError):
    """Only fixed machine-readable codes; never file names, keys or PII."""


def require(condition,code):
    if not condition:
        raise ImportFailure(code)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def read_private_env(path):
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            info=os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_mode & 0o077==0,'environment_not_private')
            content=stream.read(1024*1024+1)
        require(len(content)<=1024*1024,'environment_too_large')
        values={}
        for line in content.splitlines():
            if line.strip() and not line.lstrip().startswith('#'):
                key,value=line.split('=',1)
                values[key]=' '.join(shlex.split(value))
        return values
    except ImportFailure:
        raise
    except Exception:
        raise ImportFailure('environment_unavailable') from None


def load_manifest(path):
    try:
        with open(path,'rb') as stream:
            raw=stream.read(1024*1024+1)
        require(len(raw)<=1024*1024,'manifest_too_large')
        return validate_manifest(json.loads(raw))
    except ImportFailure:
        raise
    except Exception:
        raise ImportFailure('invalid_manifest') from None


def validate_manifest(entries):
    require(isinstance(entries,list) and 0<len(entries)<=100,'invalid_manifest')
    owners=set()
    recordings=set()
    formats={'mp4':'video/mp4','mov':'video/quicktime','m4v':'video/mp4','webm':'video/webm'}
    clean=[]
    for entry in entries:
        require(isinstance(entry,dict) and set(entry)=={'owner_kind','owner_id','candidate_id','candidate_name',
            'connection_id','capture_instance_id','files'},'invalid_manifest_entry')
        require(entry['owner_kind'] in ('interview','meeting'),'invalid_owner_kind')
        for name in ('owner_id','candidate_id','connection_id'):
            require(isinstance(entry[name],str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',entry[name]),'invalid_identifier')
        require(isinstance(entry['candidate_name'],str) and 0<len(entry['candidate_name'])<=500 and
            not any(ord(c)<32 for c in entry['candidate_name']),'invalid_candidate_audit')
        require(isinstance(entry['capture_instance_id'],str) and re.fullmatch('[a-f0-9]{64}',entry['capture_instance_id']),
            'invalid_capture_identity')
        owner=(entry['owner_kind'],entry['owner_id'])
        require(owner not in owners,'duplicate_owner')
        owners.add(owner)
        require(isinstance(entry['files'],list) and 0<len(entry['files'])<=100,'invalid_files')
        hashes=set()
        for item in entry['files']:
            require(isinstance(item,dict) and set(item)=={'path','sha256','size','duration','format','content_type'},'invalid_file_entry')
            require(isinstance(item['path'],str) and Path(item['path']).is_absolute(),'local_absolute_file_required')
            require(isinstance(item['sha256'],str) and re.fullmatch('[a-f0-9]{64}',item['sha256']),'invalid_file_digest')
            require(item['sha256'] not in hashes,'duplicate_file_content')
            hashes.add(item['sha256'])
            require(item['sha256'] not in recordings,'duplicate_recording_across_owners')
            recordings.add(item['sha256'])
            require(type(item['size']) is int and 0<item['size']<=5*1024**3,'invalid_file_size')
            require(type(item['duration']) in (int,float) and math.isfinite(item['duration']) and item['duration']>0,'invalid_duration')
            require(item['format'] in formats and item['content_type']==formats[item['format']],'invalid_media_format')
            require(Path(item['path']).suffix.lower()=='.'+item['format'],'file_extension_mismatch')
        clean.append(entry)
    return clean


def check_local_files(entries):
    # Check every file before a binding or object-store write. Storage.put_file
    # repeats SHA/size validation at upload to detect changes after preflight.
    for entry in entries:
        for item in entry['files']:
            try:
                fd=os.open(item['path'],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                with os.fdopen(fd,'rb') as stream:
                    info=os.fstat(stream.fileno())
                    require(stat.S_ISREG(info.st_mode) and info.st_size==item['size'],'local_file_size_mismatch')
                    sha,size=hashlib.sha256(),0
                    while block:=stream.read(1024*1024):
                        size+=len(block)
                        require(size<=item['size'],'local_file_size_mismatch')
                        sha.update(block)
                require(size==item['size'] and sha.hexdigest()==item['sha256'],'local_file_integrity_mismatch')
            except ImportFailure:
                raise
            except Exception:
                raise ImportFailure('local_file_unavailable') from None


@dataclass
class Configuration:
    core_url:str
    portal_token:str=field(repr=False)
    source_tokens:dict=field(repr=False)

    @classmethod
    def from_env(cls,env,caller='collector'):
        try:
            url=env['TRANSCRIPTION_SOURCE_CORE_URL']
            parsed=urlsplit(url)
            require(parsed.scheme in ('https','http') and parsed.hostname and not parsed.username and not parsed.password and
                parsed.path in ('','/') and not parsed.query and not parsed.fragment,'invalid_core_url')
            require(parsed.scheme=='https' or parsed.hostname in ('127.0.0.1','localhost','::1'),'core_tls_or_loopback_required')
            token=json.loads(env['TRANSCRIPTION_SOURCE_CALLERS_JSON'])[caller]['core_read_token']
            pairs=json.loads(env['TRANSCRIPTION_SOURCE_CORE_SOURCES_JSON'])
            sources={v['connection_id']:v['token'] for v in pairs if v['provider']=='upload'}
            require(isinstance(token,str) and len(token)>=32 and sources and
                all(isinstance(v,str) and len(v)>=32 for v in sources.values()),'upload_credentials_required')
            return cls(url.rstrip('/'),token,sources)
        except ImportFailure:
            raise
        except Exception:
            raise ImportFailure('upload_configuration_invalid') from None


def configured_storage(env):
    from hr_transcription_source.storage import S3Storage
    import boto3
    from botocore.config import Config
    try:
        # Explicit credentials from protected source.env, no accidental metadata
        # service/profile fallback or environment mutation in this operator tool.
        client=boto3.client('s3',endpoint_url=env.get('TRANSCRIPTION_SOURCE_S3_ENDPOINT'),
            region_name=env.get('TRANSCRIPTION_SOURCE_S3_REGION'),
            aws_access_key_id=env['AWS_ACCESS_KEY_ID'],aws_secret_access_key=env['AWS_SECRET_ACCESS_KEY'],
            aws_session_token=env.get('AWS_SESSION_TOKEN'),
            config=Config(signature_version='s3v4',connect_timeout=10,read_timeout=60,
                retries={'mode':'standard','total_max_attempts':3}))
        return S3Storage(env['TRANSCRIPTION_SOURCE_S3_BUCKET'],prefix=env.get('TRANSCRIPTION_SOURCE_S3_PREFIX','materials'),
            endpoint_url=env.get('TRANSCRIPTION_SOURCE_S3_ENDPOINT'),client=client)
    except Exception:
        raise ImportFailure('object_storage_configuration_invalid') from None


class Importer:
    def __init__(self,configuration,storage,*,core=None):
        self.configuration,self.storage=configuration,storage
        self.core=core or httpx.Client(base_url=configuration.core_url,timeout=60,follow_redirects=False,trust_env=False)

    def call(self,method,path,token,body=None):
        try:
            with self.core.stream(method,path,headers={'Authorization':'Bearer '+token},json=body) as response:
                raw=bytearray()
                for block in response.iter_bytes():
                    raw.extend(block)
                    require(len(raw)<=24*1024**2,'core_response_too_large')
                require(response.status_code==200,'core_request_rejected')
            result=json.loads(raw)
            require(isinstance(result,dict),'invalid_core_response')
            return result
        except ImportFailure:
            raise
        except Exception:
            raise ImportFailure('core_unavailable') from None

    def authorize(self,entry):
        config=self.configuration
        write=config.source_tokens.get(entry['connection_id'])
        require(write is not None,'upload_connection_unconfigured')
        candidate=self.call('GET','/v1/candidates/'+quote(entry['candidate_id'],safe='')+'/dossier',config.portal_token)
        owners=candidate.get('interviews' if entry['owner_kind']=='interview' else 'meetings',[])
        require(candidate.get('candidate_id')==entry['candidate_id'] and
            any(owner.get('id')==entry['owner_id'] and owner.get('status')=='completed' for owner in owners),'candidate_owner_mismatch')
        room='upload-'+entry['owner_id']
        body={'schema_version':1,'owner_kind':entry['owner_kind'],'owner_id':entry['owner_id'],
            'provider':'upload','connection_id':entry['connection_id'],'storage_policy':'owned',
            'source_ref':{'room_id':room,'room_name':room,'capture_instance_id':entry['capture_instance_id']}}
        binding=self.call('POST','/v1/bindings',config.portal_token,body)
        self.check_binding(binding,entry)
        active=self.call('GET','/v1/bindings/'+quote(binding['binding_id'],safe=''),write)
        self.check_binding(active,entry)
        require(active['generation']==binding['generation'],'binding_changed')
        return active,write

    @staticmethod
    def check_binding(binding,entry):
        require((binding.get('owner_kind'),binding.get('owner_id'),binding.get('provider'),binding.get('connection_id'),
            binding.get('storage_policy'))==(entry['owner_kind'],entry['owner_id'],'upload',entry['connection_id'],'owned') and
            not binding.get('revoked') and isinstance(binding.get('binding_id'),str) and type(binding.get('generation')) is int,
            'binding_proof_mismatch')
        room='upload-'+entry['owner_id']
        require(binding.get('source_ref')=={'room_id':room,'room_name':room,'capture_instance_id':entry['capture_instance_id']},
            'binding_proof_mismatch')

    def store(self,item):
        from botocore.exceptions import ClientError
        key=self.storage.object_key(item['sha256'])
        try:
            stored=self.storage.head(key)
        except ClientError as error:
            require(error.response.get('Error',{}).get('Code') in ('404','NoSuchKey','NotFound'),'object_storage_unavailable')
            stored=self.storage.put_file(item['path'],expected_sha256=item['sha256'],expected_size=item['size'],
                content_type=item['content_type'])
        require(stored.key==key and stored.sha256==item['sha256'] and stored.size==item['size'] and
            stored.content_type==item['content_type'] and stored.backend=='s3','stored_object_proof_mismatch')
        return {'backend':'s3','object_key':key,'state':'verified','size':stored.size,'sha256':stored.sha256,
            'content_type':stored.content_type}

    def run(self,manifest):
        entries=validate_manifest(manifest)
        check_local_files(entries)
        # Authorize every reviewed owner/upload room before the first S3 write.
        bindings=[self.authorize(entry) for entry in entries]
        results=[]
        uploads=0
        for entry,(binding,write) in zip(entries,bindings):
            assets=[]
            files=sorted(entry['files'],key=lambda item:item['sha256'])
            for item in files:
                assets.append({'external_id':'upload:'+item['sha256'],'kind':'video','state':'ready',
                    'metadata':{'duration':float(item['duration']),'format':item['format']},'locations':[self.store(item)]})
                uploads+=1
            # Paths/names are audit input only; they do not affect receipt identity.
            receipt={'schema_version':1,'generation':binding['generation'],
                'receipt_id':'upload:'+digest(canonical([binding['binding_id'],binding['generation'],assets])),
                'assets':assets,'video_status':'ready','transcript_status':'processing','acquisition_complete':True,
                'transcription_status':'not_requested'}
            current=self.call('GET','/v1/bindings/'+quote(binding['binding_id'],safe=''),write)
            self.check_binding(current,entry)
            require(current['generation']==binding['generation'],'binding_changed')
            ack=self.call('POST','/v1/bindings/'+quote(binding['binding_id'],safe='')+'/receipts',write,receipt)
            require(ack.get('binding_id')==binding['binding_id'] and ack.get('generation')==binding['generation'] and
                ack.get('receipt_id')==receipt['receipt_id'] and len(ack.get('asset_ids',[]))==len(assets),'receipt_proof_mismatch')
            readback=self.call('GET','/v1/owners/'+entry['owner_kind']+'/'+quote(entry['owner_id'],safe='')+'/materials',
                self.configuration.portal_token)
            visible={asset['id']:asset for asset in readback.get('assets',[])}
            for identity,item in zip(ack['asset_ids'],files):
                asset=visible.get(identity,{})
                require(asset.get('source_id')==binding['binding_id'] and asset.get('external_id')=='upload:'+item['sha256'] and
                    asset.get('state')=='ready' and asset.get('metadata',{}).get('duration')==float(item['duration']) and
                    any(loc.get('backend')=='s3' and loc.get('state')=='verified' and loc.get('sha256')==item['sha256'] and
                        loc.get('size')==item['size'] for loc in asset.get('locations',[])),'material_readback_failed')
            results.append({'owner_kind':entry['owner_kind'],'owner_id':entry['owner_id'],'candidate_id':entry['candidate_id'],
                'candidate_name':entry['candidate_name'],'binding_id':binding['binding_id'],'generation':binding['generation'],
                'receipt_id':receipt['receipt_id'],'asset_ids':ack['asset_ids'],
                'recordings':[{'asset_id':identity,'sha256':item['sha256'],'duration':float(item['duration']),
                    'format':item['format']} for identity,item in zip(ack['asset_ids'],files)]})
        return {'owners':len(entries),'recordings':uploads,'manifest_sha256':digest(canonical(entries)),'results':results}


def write_private_output(path,result):
    # Refuse overwrite: preserve a previous audit even after a repeated import.
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(canonical(result)+b'\n')
        stream.flush()
        os.fsync(stream.fileno())


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--source-env',default='/etc/hr-vision-asr/source.env')
    parser.add_argument('--caller',default='collector')
    parser.add_argument('--output',required=True,help='new private output path (contains reviewed owner/candidate IDs)')
    args=parser.parse_args(argv)
    try:
        manifest=load_manifest(args.manifest)
        check_local_files(manifest)
        env=read_private_env(args.source_env)
        importer=Importer(Configuration.from_env(env,args.caller),configured_storage(env))
        result=importer.run(manifest)
        write_private_output(args.output,result)
        print(json.dumps({k:result[k] for k in ('owners','recordings','manifest_sha256')}))
    except Exception as error:
        # SDK/network exceptions may contain credentials or signed URLs.
        code=str(error) if isinstance(error,ImportFailure) else 'import_failed'
        print(json.dumps({'error':code}),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
