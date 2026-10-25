"""SpeechKit v3 async REST. Only GET may be retried automatically after submit."""
import asyncio
import base64
import hashlib
import json
import re
from urllib.parse import quote
import aiohttp
from .backends import BackendError
from .normalization import normalize_transcript, MAX_TIME_MS, MAX_SEGMENTS
from .contracts import validate_normalized
from .audio import prepare_audio

STT = 'https://stt.api.cloud.yandex.net/stt/v3'
OPERATIONS = 'https://operation.api.cloud.yandex.net/operations'

def _ms(value):
    if type(value) is int: number=value
    elif isinstance(value,str) and re.fullmatch(r'\d+',value): number=int(value)
    else: raise BackendError('speechkit_invalid_timestamps')
    if not 0<=number<=MAX_TIME_MS: raise BackendError('speechkit_invalid_timestamps')
    return number

def _field(value, camel, snake=None, default=None):
    if not isinstance(value,dict): raise BackendError('speechkit_invalid_response')
    snake=snake or re.sub(r'(?<!^)(?=[A-Z])','_',camel).lower()
    if camel in value and snake in value and value[camel]!=value[snake]:
        raise BackendError('speechkit_ambiguous_response')
    return value.get(camel,value.get(snake,default))

def _channel(result,item):
    outer=_field(result,'channelTag'); inner=_field(item,'channelTag')
    if outer is not None and inner is not None and outer!=inner: raise BackendError('speechkit_ambiguous_response')
    channel=inner if inner is not None else outer
    if channel is not None and (not isinstance(channel,str) or len(channel)>128): raise BackendError('speechkit_invalid_response')
    return channel

def parse_recognition(body):
    """Retain original events; finalRefinement replaces its final, never duplicates it."""
    try:
        events = [json.loads(line) for line in body.splitlines() if line.strip()]
    except (ValueError, RecursionError): raise BackendError('speechkit_invalid_response') from None
    if not events or any(not isinstance(event,dict) for event in events): raise BackendError('speechkit_invalid_response')
    finals, refinements, provider_warnings = {}, {}, []
    for event in events:
        if 'error' in event: raise BackendError('speechkit_result_stream_error')
        result=event.get('result',event)
        if not isinstance(result,dict): raise BackendError('speechkit_invalid_response')
        if 'error' in result: raise BackendError('speechkit_result_stream_error')
        status=_field(result,'statusCode')
        if status is not None:
            code=_field(status,'codeType')
            if code not in ('WORKING','WARNING','CLOSED'): raise BackendError('speechkit_result_status_error')
            if code=='WARNING': provider_warnings.append('speechkit_provider_warning')
        cursors=_field(result,'audioCursors',default={})
        index=_field(cursors,'finalIndex')
        if 'final' in result:
            item=result['final']
            if index is None: index=str(len(finals))
            if not isinstance(item,dict): raise BackendError('speechkit_invalid_response')
            channel=_channel(result,item)
            key=(channel,str(index))
            if key in finals: raise BackendError('speechkit_duplicate_final')
            finals[key]=item
        refinement=_field(result,'finalRefinement')
        if refinement is not None:
            if not isinstance(refinement,dict) or not isinstance(_field(refinement,'normalizedText'),dict) or _field(refinement,'finalIndex') is None:
                raise BackendError('speechkit_invalid_response')
            item=_field(refinement,'normalizedText')
            refinements[(_channel(result,item),str(_field(refinement,'finalIndex')))]=item
    if not finals: raise BackendError('speechkit_no_speech')
    # Some provider events omit a channel on refinement; associate only when unique.
    for key in list(refinements):
        if key not in finals and key[0] is None:
            matches=[candidate for candidate in finals if candidate[1]==key[1]]
            if len(matches)==1: refinements[matches[0]]=refinements.pop(key)
    if any(key not in finals for key in refinements): raise BackendError('speechkit_orphan_refinement')
    segments, channels=[], []
    for key, initial in finals.items():
        item=refinements.get(key,initial)
        alternatives=item.get('alternatives')
        if not isinstance(alternatives,list) or not alternatives or not isinstance(alternatives[0],dict): raise BackendError('speechkit_invalid_response')
        alt=alternatives[0]
        text=alt.get('text')
        if not isinstance(text,str) or not text.strip(): raise BackendError('speechkit_invalid_response')
        words=alt.get('words',[])
        start=_field(alt,'startTimeMs'); end=_field(alt,'endTimeMs')
        if start is None and end is None and isinstance(words,list) and words:
            start=_field(words[0],'startTimeMs'); end=_field(words[-1],'endTimeMs')
        if start is None and end is None: start_ms=end_ms=None
        else:
            start_ms,end_ms=_ms(start),_ms(end)
            if start_ms>end_ms: raise BackendError('speechkit_invalid_timestamps')
        # channelTag identifies provider audio/voice channels, never candidate roles.
        channel=key[0]
        if channel is not None and (not isinstance(channel,str) or len(channel)>128): raise BackendError('speechkit_invalid_response')
        channels.append(channel)
        segments.append({'text':text.strip(),'start_ms':start_ms,'end_ms':end_ms,'speaker':None})
    if len(segments)>MAX_SEGMENTS: raise BackendError('speechkit_response_too_large')
    ordering=sorted(range(len(segments)),key=lambda i:(segments[i]['start_ms'] is None, segments[i]['start_ms'] or 0,i))
    segments=[segments[i] for i in ordering]; channels=[channels[i] for i in ordering]
    try:
        transcript=normalize_transcript(json.dumps({'schema_version':1,'segments':segments},ensure_ascii=False).encode(),'json')
        transcript['warnings']=sorted(set(transcript['warnings']+provider_warnings+['provider_channel_labels_are_not_roles','coverage_not_independently_verified']))
        return validate_normalized(transcript), events, channels
    except (ValueError,TypeError): raise BackendError('speechkit_invalid_response') from None

class S3Staging:
    def __init__(self,cfg):
        import boto3
        from botocore.config import Config
        kwargs={'endpoint_url':cfg.staging_s3_endpoint,'region_name':'ru-central1',
                'config':Config(connect_timeout=15,read_timeout=60,retries={'max_attempts':3},s3={'addressing_style':'path'})}
        if cfg.staging_s3_access_key:
            kwargs.update(aws_access_key_id=cfg.staging_s3_access_key,aws_secret_access_key=cfg.staging_s3_secret_key)
        self.client=boto3.client('s3',**kwargs); self.cfg=cfg
    def upload(self,path,key):
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            while block:=stream.read(1024*1024): digest.update(block)
        sha=digest.hexdigest()
        self.client.upload_file(str(path),self.cfg.staging_s3_bucket,key,
            ExtraArgs={'ContentType':'audio/wav','Metadata':{'sha256':sha}})
        stored=self.client.head_object(Bucket=self.cfg.staging_s3_bucket,Key=key)
        if stored.get('ContentLength')!=path.stat().st_size or stored.get('Metadata',{}).get('sha256')!=sha:
            raise BackendError('speechkit_staging_integrity_failed')
        # SpeechKit ignores signed query parameters; its service account needs storage.viewer.
        return self.cfg.staging_s3_endpoint+'/'+quote(self.cfg.staging_s3_bucket,safe='')+'/'+quote(key,safe='/')
    def delete(self,key):
        if not key.startswith(self.cfg.staging_s3_prefix): raise ValueError('invalid staging key')
        self.client.delete_object(Bucket=self.cfg.staging_s3_bucket,Key=key)

class SpeechKitBackend:
    def __init__(self,settings,staging=None):
        self.settings=settings; self._staging=staging
    @property
    def staging(self):
        if self._staging is None: self._staging=S3Staging(self.settings)
        return self._staging
    def configuration(self,language):
        cfg=self.settings
        locale={'ru':'ru-RU','en':'en-US'}.get(language,language or cfg.speechkit_language)
        if not re.fullmatch('[a-z]{2,3}-[A-Z]{2}',locale): raise BackendError('speechkit_unsupported_language')
        return {'model':cfg.speechkit_model,'language':locale,'audio_processing_type':'FULL_DATA',
            'text_normalization':'TEXT_NORMALIZATION_DISABLED','literature_text':False,
            'speaker_labeling':cfg.speechkit_speaker_labeling,'config_revision':cfg.speechkit_config_revision}
    def transcribe_job(self,path,job,repository,token):
        cfg=self.settings
        if not cfg.asr_configured: raise BackendError('speechkit_not_configured')
        current=repository.provider_snapshot(job['id'],token)
        if current is None: raise BackendError('worker_lease_lost')
        if current['provider_state']=='submitting' and not current['provider_operation_id']:
            raise BackendError('speechkit_submit_uncertain',uncertain=True)
        config=self.configuration(job['language'])
        if not current['provider_operation_id']:
            try:
                digest=hashlib.sha256(); observed=0
                with path.open('rb') as stream:
                    while block:=stream.read(1024*1024):
                        observed+=len(block); digest.update(block)
                        if observed>job['input_size']: raise BackendError('input_integrity_failed')
                if observed!=job['input_size'] or digest.hexdigest()!=job['input_sha256']:
                    raise BackendError('input_integrity_failed')
            except OSError: raise BackendError('input_storage_unavailable',retryable=True) from None
            with prepare_audio(path,cfg) as (audio,provenance):
                key=cfg.staging_s3_prefix+job['id']+'/'+provenance['derived_audio_sha256']+'.wav'
                metadata={'configuration':config,'provenance':{**provenance,'input_sha256':job['input_sha256']}}
                if not repository.prepare_provider(job['id'],token,key,metadata): raise BackendError('worker_lease_lost')
                try: uri=self.staging.upload(audio,key)
                except Exception: raise BackendError('speechkit_staging_failed',retryable=True) from None
                if not repository.begin_provider_submit(job['id'],token): raise BackendError('worker_lease_lost')
                operation=asyncio.run(self.submit(uri,config))
                # The operation is checkpointed immediately, including late submit responses.
                if not repository.record_provider_operation(job['id'],token,operation): raise BackendError('speechkit_checkpoint_failed',uncertain=True)
            current=repository.provider_snapshot(job['id'],token)
            if current is None: raise BackendError('worker_lease_lost',retryable=True)
        operation=current['provider_operation_id']
        metadata=current['provider_metadata']
        if current['provider_raw'] is None:
            body=asyncio.run(self.fetch(operation))
            try:
                def invalid_constant(_value): raise ValueError()
                events=[json.loads(line,parse_constant=invalid_constant) for line in body.splitlines() if line.strip()]
                if not events or any(not isinstance(event,dict) for event in events): raise ValueError()
            except (ValueError,RecursionError): events=[]
            raw={'schema_version':1,'provider':'yandex_speechkit','job_id':job['id'],
                'input_sha256':job['input_sha256'],'operation_id':operation,'model':cfg.speechkit_model,
                'language':config['language'],'configuration':metadata['configuration'],
                'provenance':metadata['provenance'],'warnings':[], 'events':events,
                'coverage':'not_independently_verified'}
            if not events: raw['unparsed_response_base64']=base64.b64encode(body).decode('ascii')
            if not repository.record_provider_fetch(job['id'],token,raw,metadata):
                raise BackendError('worker_lease_lost',retryable=True)
        else: raw=current['provider_raw']
        if not raw['events']: raise BackendError('speechkit_invalid_response')
        body=b'\n'.join(json.dumps(event,ensure_ascii=False).encode() for event in raw['events'])
        transcript,events,channels=parse_recognition(body)
        metadata={**metadata,'provider_channel_labels':channels}
        warnings=list(transcript['warnings'])
        if (metadata['provenance'].get('input_channels') or 1)>1: warnings.append('first_audio_stream_downmixed_to_mono')
        if not cfg.speechkit_speaker_labeling: warnings.append('speaker_labeling_disabled')
        transcript['warnings']=sorted(set(warnings))
        raw={**raw,'warnings':transcript['warnings'],'provider_channel_labels':channels}
        return transcript,raw,metadata
    def cleanup(self,job):
        key=job.get('provider_staging_key')
        if key: self.staging.delete(key)
    def headers(self):
        headers={'Authorization':'Api-Key '+self.settings.speechkit_api_key}
        if self.settings.speechkit_folder_id: headers['x-folder-id']=self.settings.speechkit_folder_id
        return headers
    async def request(self,session,method,url,**kwargs):
        try:
            async with session.request(method,url,headers=self.headers(),allow_redirects=False,**kwargs) as response:
                if response.status!=200:
                    if method=='POST':
                        if response.status in (400,401,403,404,413,422,429): raise BackendError('speechkit_submit_rejected')
                        raise BackendError('speechkit_submit_uncertain',uncertain=True)
                    raise BackendError('speechkit_poll_temporary' if response.status in (429,500,502,503,504) else 'speechkit_poll_rejected',retryable=response.status in (429,500,502,503,504))
                data=bytearray()
                async for block in response.content.iter_chunked(65536):
                    data.extend(block)
                    if len(data)>self.settings.max_result_bytes:
                        raise BackendError('speechkit_response_too_large',uncertain=method=='POST')
                return bytes(data)
        except (asyncio.TimeoutError,aiohttp.ClientError):
            raise BackendError('speechkit_submit_uncertain' if method=='POST' else 'speechkit_poll_temporary',uncertain=method=='POST',retryable=method!='POST') from None
    async def submit(self,uri,config):
        body={'uri':uri,'recognitionModel':{'model':config['model'],'audioFormat':{'containerAudio':{'containerAudioType':'WAV'}},
            'languageRestriction':{'restrictionType':'WHITELIST','languageCode':[config['language']]},
            'audioProcessingType':'FULL_DATA','textNormalization':{'textNormalization':'TEXT_NORMALIZATION_DISABLED','literatureText':False,'profanityFilter':False}},
            'speakerLabeling':{'speakerLabeling':'SPEAKER_LABELING_ENABLED' if config['speaker_labeling'] else 'SPEAKER_LABELING_DISABLED'}}
        async with aiohttp.ClientSession(trust_env=False,timeout=aiohttp.ClientTimeout(total=self.settings.asr_timeout_seconds)) as session:
            raw=await self.request(session,'POST',STT+'/recognizeFileAsync',json=body)
        try:
            operation=json.loads(raw)['id']
            if not isinstance(operation,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}',operation): raise ValueError()
            return operation
        except (ValueError,KeyError,TypeError): raise BackendError('speechkit_submit_uncertain',uncertain=True) from None
    async def fetch(self,operation):
        async with aiohttp.ClientSession(trust_env=False,timeout=aiohttp.ClientTimeout(total=self.settings.asr_timeout_seconds)) as session:
            raw=await self.request(session,'GET',OPERATIONS+'/'+quote(operation,safe=''))
            try: value=json.loads(raw)
            except (ValueError,RecursionError): raise BackendError('speechkit_invalid_operation') from None
            if not isinstance(value,dict) or type(value.get('done')) is not bool: raise BackendError('speechkit_invalid_operation')
            if not value['done']:
                error=BackendError('speechkit_operation_pending',retryable=True)
                error.retry_delay=self.settings.speechkit_poll_seconds
                raise error
            if value.get('error'): raise BackendError('speechkit_operation_failed')
            return await self.request(session,'GET',STT+'/getRecognition',params={'operationId':operation})
