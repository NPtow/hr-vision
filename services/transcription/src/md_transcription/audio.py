"""Local media decoding only. No playlists, network protocols, truncation or shell."""
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import shutil
import subprocess
import tempfile
import wave
from contextlib import contextmanager
from .backends import BackendError

FORMATS = 'mov,matroska,webm,mp3,wav,flac,ogg,mpeg'

def _run(command, cfg, output_limit):
    # Limits are set in a fresh helper interpreter, avoiding unsafe preexec_fn
    # in the worker's heartbeat thread. exec keeps timeout/kill on the decoder.
    wrapper = "import os,resource,sys; n=int(sys.argv[1]); c=int(sys.argv[2]); resource.setrlimit(resource.RLIMIT_FSIZE,(n,n)); resource.setrlimit(resource.RLIMIT_CPU,(c,c)); resource.setrlimit(resource.RLIMIT_NOFILE,(64,64)); os.execvp(sys.argv[3],sys.argv[3:])"
    command = [sys.executable, '-c', wrapper, str(output_limit), str(cfg.media_timeout_seconds), *command]
    try:
        with tempfile.TemporaryFile() as output:
            subprocess.run(command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                timeout=cfg.media_timeout_seconds, check=True, env={'PATH':os.environ.get('PATH','/usr/bin:/bin')})
            output.seek(0)
            result = output.read(1024*1024+1)
            if len(result)>1024*1024: raise BackendError('media_probe_too_large')
            return result
    except (subprocess.SubprocessError, OSError):
        raise BackendError('media_decode_failed') from None

@contextmanager
def prepare_audio(path, cfg):
    path = Path(path).resolve(strict=True)
    root = cfg.data_dir/'audio-staging'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if shutil.disk_usage(root).free < cfg.media_max_audio_bytes + 64*1024**2:
        raise BackendError('media_disk_capacity', retryable=True)
    probe = _run(['ffprobe','-v','error','-protocol_whitelist','file','-format_whitelist',FORMATS,
        '-show_entries','stream=index,codec_type,channels,duration,start_time:format=duration,start_time','-of','json',str(path)], cfg, 1024*1024)
    try:
        value = json.loads(probe)
        streams = [s for s in value['streams'] if s.get('codec_type')=='audio']
        if not streams: raise ValueError()
        duration = float(streams[0].get('duration') or value['format']['duration'])
        if not math.isfinite(duration) or duration<=0: raise ValueError()
        if duration>cfg.media_max_duration_seconds: raise BackendError('media_duration_exceeded')
    except (KeyError, TypeError, ValueError):
        raise BackendError('media_invalid_probe') from None
    with tempfile.TemporaryDirectory(prefix='audio-', dir=root) as temporary:
        output = Path(temporary)/'audio.wav'
        _run(['ffmpeg','-nostdin','-v','error','-protocol_whitelist','file','-format_whitelist',FORMATS,
            '-i',str(path),'-map','0:a:0','-vn','-sn','-dn','-map_metadata','-1',
            '-ac','1','-ar','16000','-c:a','pcm_s16le','-threads','1','-y',str(output)], cfg, cfg.media_max_audio_bytes)
        if not output.is_file() or output.stat().st_size>cfg.media_max_audio_bytes:
            raise BackendError('media_audio_size_exceeded')
        try:
            with wave.open(str(output),'rb') as audio:
                if (audio.getnchannels(),audio.getframerate(),audio.getsampwidth()) != (1,16000,2): raise ValueError()
                actual = audio.getnframes()/audio.getframerate()
                if actual<=0 or actual>cfg.media_max_duration_seconds: raise BackendError('media_duration_exceeded')
        except (wave.Error, ValueError): raise BackendError('media_invalid_audio') from None
        digest = hashlib.sha256()
        with output.open('rb') as stream:
            while chunk:=stream.read(1024*1024): digest.update(chunk)
        yield output, {'input_audio_stream':'0:a:0','input_audio_stream_count':len(streams),
            'input_channels':streams[0].get('channels'), 'derived_audio_sha256':digest.hexdigest(),
            'derived_audio_bytes':output.stat().st_size,'duration_ms':round(actual*1000),
            'sample_rate_hz':16000,'channels':1,'encoding':'PCM_S16LE_WAV', 'timing_offset_ms':None,
            'source_audio_start_ms':_observed_start_ms(streams[0].get('start_time')),
            'source_format_start_ms':_observed_start_ms(value.get('format',{}).get('start_time')),
            'timing_alignment_to_original_media':'not_verified',
            'timing_reference':'decoded_first_audio_stream', 'coverage':'not_independently_verified'}

def _observed_start_ms(value):
    try:
        number=float(value)
        return round(number*1000) if math.isfinite(number) else None
    except (TypeError,ValueError,OverflowError): return None
