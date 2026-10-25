"""Import-only transcript normalization. No STT, role guesses or inferred times.

Workers MUST preserve raw bytes in owned storage before calling this module.
Daily stored transcripts use WebVTT; JSON here means our explicit schema only.
"""
import html
from html.parser import HTMLParser
import json
import re

import pysubs2
from pysubs2.formats.webvtt import WebVTTFormat


MAX_BYTES = 16 * 1024 * 1024
MAX_SEGMENTS = 100000
MAX_TIME_MS = 7 * 24 * 60 * 60 * 1000


class TranscriptError(ValueError):
    pass


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'br':
            self.parts.append('\n')

    def handle_data(self, data):
        self.parts.append(data)


def _text(value):
    if not isinstance(value, str) or any((ord(c) < 32 and c not in '\n\t') or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise TranscriptError('Invalid transcript text')
    return value.strip()


def _segment(text, start, end, speaker=None):
    text = _text(text)
    if not text:
        raise TranscriptError('Empty transcript segment')
    if start is None and end is None:
        pass
    elif type(start) is not int or type(end) is not int or not 0 <= start <= end <= MAX_TIME_MS:
        raise TranscriptError('Invalid transcript timestamps')
    if speaker is not None:
        speaker = _text(speaker)
        if not speaker or len(speaker) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in speaker):
            raise TranscriptError('Invalid explicit speaker label')
    return {'start_ms': start, 'end_ms': end, 'text': text, 'speaker': speaker}


def _subtitles(value, format_):
    value = value.replace('\r\n', '\n').replace('\r', '\n')
    if format_ == 'vtt':
        lines = value.split('\n')
        if not lines or not re.fullmatch(r'WEBVTT(?:[ \t].*)?', lines[0]):
            raise TranscriptError('Missing WebVTT header')
        # Header metadata ends at the first blank line, per WebVTT.
        try:
            value = '\n'.join(lines[lines.index('') + 1:])
        except ValueError:
            raise TranscriptError('Missing WebVTT header separator') from None
    stamp = r'(?:\d{2,4}:)?[0-5]\d:[0-5]\d\.\d{3}' if format_ == 'vtt' else r'\d{2,4}:[0-5]\d:[0-5]\d,\d{3}'
    timing = re.compile(rf'^({stamp})[ \t]+-->[ \t]+({stamp})(?:[ \t]+[^\n]*)?$')
    segments, warnings = [], []
    for block in re.split(r'\n[ \t]*\n+', value.strip()):
        if not block.strip():
            continue
        lines = block.split('\n')
        if format_ == 'vtt' and (lines[0] == 'NOTE' or lines[0].startswith('NOTE ') or lines[0] in ('STYLE', 'REGION')):
            warnings.append('non_speech_vtt_block_omitted')
            continue
        index = 0 if '-->' in lines[0] else 1
        if index >= len(lines) or not timing.fullmatch(lines[index].strip()) or index + 1 >= len(lines):
            raise TranscriptError('Malformed subtitle cue; refusing partial import')
        if index == 1 and format_ == 'srt' and not lines[0].strip().isdigit():
            raise TranscriptError('Malformed SRT cue identifier')
        body = '\n'.join(lines[index + 1:])
        if '-->' in body:
            raise TranscriptError('Malformed subtitle cue boundary')
        try:
            # Let pysubs2 perform timestamp conversion; validate cue boundaries
            # first because its permissive parser otherwise skips malformed cues.
            parsed = pysubs2.SSAFile.from_string(lines[index] + '\n' + body + '\n', format_=format_, keep_html_tags=True)
        except Exception as error:
            raise TranscriptError('Could not parse subtitle cue') from error
        if len(parsed) != 1:
            raise TranscriptError('Malformed subtitle cue')
        event = parsed[0]
        voices = set(html.unescape(v).strip() for v in re.findall(r'<v(?:\.[^\s>]+)?\s+([^>]+)>', body))
        # Existing Daily integration also documents <v>Name:</v>speech.
        legacy = re.findall(r'<v>([^<>]+)</v>', body)
        voices.update(html.unescape(v).strip().rstrip(':').strip() for v in legacy)
        speaker = next(iter(voices)) if len(voices) == 1 else None
        if len(voices) > 1:
            warnings.append('multiple_voices_in_cue')
        plain = _PlainText()
        cue_text = event.text.replace(r'\N', '\n')
        cue_text = re.sub(r'<v>[^<>]+</v>', '', cue_text)
        plain.feed(re.sub(r'<(?:\d{2}:)?\d{2}:\d{2}\.\d{3}>', '', cue_text))
        plain.close()
        segments.append(_segment(''.join(plain.parts), event.start, event.end, speaker))
        if len(segments) > MAX_SEGMENTS:
            raise TranscriptError('Too many transcript segments')
    return segments, warnings


def normalize_transcript(raw, format):
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
        raise TranscriptError('Invalid transcript byte size')
    try:
        value = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise TranscriptError('Transcript must be UTF-8') from None
    format_ = str(format).lower().lstrip('.')
    if format_ in ('vtt', 'srt'):
        segments, warnings = _subtitles(value, format_)
    elif format_ in ('json', 'canonical_json'):
        try:
            source = json.loads(value)
        except (ValueError, RecursionError):
            raise TranscriptError('Invalid transcript JSON') from None
        if not isinstance(source, dict) or type(source.get('schema_version')) is not int or source['schema_version'] != 1 or not isinstance(source.get('segments'), list):
            raise TranscriptError('Only canonical transcript JSON schema_version=1 is supported')
        if not 0 < len(source['segments']) <= MAX_SEGMENTS:
            raise TranscriptError('Invalid transcript segment count')
        segments, warnings = [], []
        for item in source['segments']:
            if not isinstance(item, dict):
                raise TranscriptError('Invalid transcript segment')
            segments.append(_segment(item.get('text'), item.get('start_ms'), item.get('end_ms'), item.get('speaker')))
    else:
        raise TranscriptError('Unsupported transcript format')
    if not segments:
        raise TranscriptError('Transcript has no speech segments')
    if any(s['speaker'] is None for s in segments):
        warnings.append('missing_speakers')
    missing_times = any(s['start_ms'] is None for s in segments)
    if missing_times:
        warnings.append('missing_timestamps')
    known = [s['start_ms'] for s in segments if s['start_ms'] is not None]
    if known != sorted(known):
        warnings.append('source_not_chronological')
    # Rendering is a convenience projection, never a replacement for raw bytes.
    vtt = ''
    if not missing_times:
        cues = ['WEBVTT\n\n']
        for index, s in enumerate(segments, 1):
            body = html.escape(s['text'], quote=False)
            if s['speaker'] is not None:
                body = '<v ' + html.escape(s['speaker'], quote=False) + '>' + body + '</v>'
            cues.append(f"{index}\n{WebVTTFormat.ms_to_timestamp(s['start_ms'])} --> "
                        f"{WebVTTFormat.ms_to_timestamp(s['end_ms'])}\n{body}\n\n")
        vtt = ''.join(cues)
    full_text = '\n'.join(s['text'] for s in segments)
    return {'schema_version': 1, 'format': format_, 'segments': segments, 'full_text': full_text,
            'text': full_text, 'vtt': vtt, 'warnings': sorted(set(warnings))}
