"""Direct Deepgram prerecorded REST, with durable raw before canonical parsing."""
import asyncio
import base64
import hashlib
import json
import math
import re
import aiohttp
from .audio import prepare_audio
from .backends import BackendError
from .contracts import validate_normalized
from .normalization import normalize_transcript, MAX_TIME_MS, MAX_SEGMENTS

LISTEN='https://api.deepgram.com/v1/listen'

def _time(value):
    if type(value) not in (float,int) or not math.isfinite(value) or not 0<=value<=MAX_TIME_MS/1000:
        raise BackendError('deepgram_invalid_timestamps')
    return round(value*1000)

def _speaker(value):
    if value is None: return None
    if type(value) is not int or not 0<=value<=1000: raise BackendError('deepgram_invalid_speaker')
    return 'speaker_'+str(value)

def parse_deepgram(payload):
    if not isinstance(payload,dict) or payload.get('err_code') or payload.get('error'):
        raise BackendError('deepgram_invalid_response')
    results=payload.get('results')
    if not isinstance(results,dict): raise BackendError('deepgram_invalid_response')
    channels=results.get('channels')
    if not isinstance(channels,list) or len(channels)!=1:
        raise BackendError('deepgram_invalid_response')
    alternatives=channels[0].get('alternatives') if isinstance(channels[0],dict) else None
    if not isinstance(alternatives,list) or not alternatives or not isinstance(alternatives[0],dict):
        raise BackendError('deepgram_invalid_response')
    best=alternatives[0]
    text=best.get('transcript')
    if not isinstance(text,str) or not text.strip(): raise BackendError('deepgram_no_speech')
    # Word projections preserve all recognized speech and diarization transitions.
    # Utterances can contain only a subset or different punctuation; keep them in raw.
    words=best.get('words')
    if not isinstance(words,list) or not words or len(words)>MAX_SEGMENTS*100:
        raise BackendError('deepgram_invalid_words')
    segments=[]; recognized=[]
    for word in words:
        if not isinstance(word,dict) or not isinstance(word.get('word'),str) or not word['word'].strip():
            raise BackendError('deepgram_invalid_words')
        value=word.get('punctuated_word',word['word'])
        if not isinstance(value,str) or not value.strip(): raise BackendError('deepgram_invalid_words')
        value=value.strip(); recognized.append(value)
        start,end=_time(word.get('start')),_time(word.get('end'))
        if start>end: raise BackendError('deepgram_invalid_timestamps')
        speaker=_speaker(word.get('speaker'))
        # Bound segments by speaker changes and sentence-length chunks, not by roles.
        if segments and segments[-1]['speaker']==speaker and start>=segments[-1]['end_ms'] and len(segments[-1]['text'])+len(value)<400:
            segments[-1]['text']+=' '+value; segments[-1]['end_ms']=end
        else:
            segments.append({'start_ms':start,'end_ms':end,'text':value,'speaker':speaker})
    if len(segments)>MAX_SEGMENTS: raise BackendError('deepgram_response_too_large')
    if ''.join(text.split())!=''.join(''.join(recognized).split()):
        raise BackendError('deepgram_transcript_words_mismatch')
    try:
        transcript=normalize_transcript(json.dumps({'schema_version':1,'segments':segments},ensure_ascii=False).encode(),'json')
        transcript['warnings']=sorted(set(transcript['warnings']+['provider_speaker_labels_are_not_roles','coverage_not_independently_verified']))
        return validate_normalized(transcript)
    except (ValueError,TypeError): raise BackendError('deepgram_invalid_response') from None

class DeepgramBackend:
    def __init__(self,settings): self.settings=settings
    def configuration(self,language):
        cfg=self.settings
        locale=language or cfg.deepgram_language
        if not re.fullmatch(r'[a-z]{2,3}(?:-[A-Z]{2})?',locale): raise BackendError('deepgram_unsupported_language')
        return {'model':cfg.deepgram_model,'language':locale,'diarize':cfg.deepgram_diarize,
            'diarize_model':cfg.deepgram_diarize_model if cfg.deepgram_diarize else None,
            'smart_format':False,'punctuate':True,'utterances':True,'numerals':False,'profanity_filter':False,'config_revision':cfg.deepgram_config_revision}
    def transcribe_job(self,path,job,repository,token):
        cfg=self.settings
        if not cfg.asr_configured: raise BackendError('deepgram_not_configured')
        current=repository.provider_snapshot(job['id'],token)
        if current is None: raise BackendError('worker_lease_lost')
        if current['provider_raw'] is None:
            if current['provider_state']=='submitting': raise BackendError('deepgram_submit_uncertain',uncertain=True)
            digest=hashlib.sha256(); observed=0
            try:
                with path.open('rb') as source:
                    while block:=source.read(1024*1024):
                        digest.update(block); observed+=len(block)
                        if observed>job['input_size']: raise BackendError('input_integrity_failed')
            except OSError: raise BackendError('input_storage_unavailable',retryable=True) from None
            if observed!=job['input_size'] or digest.hexdigest()!=job['input_sha256']: raise BackendError('input_integrity_failed')
            config=self.configuration(job['language'])
            with prepare_audio(path,cfg) as (audio,provenance):
                metadata={'configuration':config,'provenance':{**provenance,'input_sha256':job['input_sha256']}}
                if not repository.prepare_provider(job['id'],token,None,metadata): raise BackendError('worker_lease_lost')
                if not repository.begin_provider_submit(job['id'],token): raise BackendError('worker_lease_lost')
                body=asyncio.run(self.submit(audio,config))
                try:
                    def invalid_constant(_value): raise ValueError()
                    payload=json.loads(body,parse_constant=invalid_constant)
                except (ValueError,RecursionError): payload=None
                raw={'schema_version':1,'provider':'deepgram','job_id':job['id'],
                    'input_sha256':job['input_sha256'],'operation_id':None,'model':config['model'],'language':config['language'],
                    'configuration':config,'provenance':metadata['provenance'],
                    'warnings':['provider_speaker_labels_are_not_roles','coverage_not_independently_verified'],
                    'coverage':'not_independently_verified','events':[payload] if isinstance(payload,dict) else []}
                provider_meta=payload.get('metadata',{}) if isinstance(payload,dict) else {}
                if isinstance(provider_meta,dict):
                    metadata.update(provider_model_info=provider_meta.get('model_info'),provider_diarize_info=provider_meta.get('diarize_info'))
                    raw['provider_request_id']=provider_meta.get('request_id')
                if not isinstance(payload,dict): raw['unparsed_response_base64']=base64.b64encode(body).decode('ascii')
                if not repository.record_provider_response(job['id'],token,raw,metadata):
                    raise BackendError('deepgram_checkpoint_failed',uncertain=True)
            current=repository.provider_snapshot(job['id'],token)
            if current is None: raise BackendError('worker_lease_lost',retryable=True)
        raw=current['provider_raw']
        if not raw.get('events'): raise BackendError('deepgram_invalid_response')
        transcript=parse_deepgram(raw['events'][0])
        metadata=current['provider_metadata']
        if metadata['configuration']['diarize'] and any(segment['speaker'] is None for segment in transcript['segments']):
            transcript['warnings']=sorted(set(transcript['warnings']+['deepgram_speaker_labels_missing']))
        if (metadata['provenance'].get('input_channels') or 1)>1:
            transcript['warnings']=sorted(set(transcript['warnings']+['first_audio_stream_downmixed_to_mono']))
        raw={**raw,'warnings':transcript['warnings']}
        return transcript,raw,metadata
    async def submit(self,path,config):
        params={'model':config['model'],'language':config['language'],
            'smart_format':'false','punctuate':'true','utterances':'true','numerals':'false','profanity_filter':'false'}
        if config['diarize']: params['diarize_model']=config['diarize_model']
        headers={'Authorization':'Token '+self.settings.deepgram_api_key,'Content-Type':'audio/wav'}
        try:
            async with aiohttp.ClientSession(trust_env=False,timeout=aiohttp.ClientTimeout(total=self.settings.asr_timeout_seconds)) as session:
                with path.open('rb') as stream:
                    async with session.post(LISTEN,params=params,headers=headers,data=stream,allow_redirects=False) as response:
                        if response.status!=200:
                            if response.status in (400,401,402,403,404,413,415,422,429): raise BackendError('deepgram_submit_rejected')
                            raise BackendError('deepgram_submit_uncertain',uncertain=True)
                        body=bytearray()
                        async for block in response.content.iter_chunked(65536):
                            body.extend(block)
                            if len(body)>self.settings.max_result_bytes: raise BackendError('deepgram_response_too_large',uncertain=True)
                        return bytes(body)
        except (asyncio.TimeoutError,aiohttp.ClientError):
            raise BackendError('deepgram_submit_uncertain',uncertain=True) from None
