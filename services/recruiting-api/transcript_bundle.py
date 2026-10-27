"""Canonical selected transcript parts; timestamps and voices belong to one recording.

Adapters provide authorized materials. This module has no network/owner lookup and
never chooses a transcript version or manufactures text for an incomplete part.
"""
import hashlib
import math
import re

from domain import Problem
import selection
from interview_assistant import transcript_turns, search_chunks
from structured_model import scan


def _check(condition, code='transcript_recording_mismatch', status=409):
    if not condition:
        raise Problem(code, status)


def _identifier(value):
    return isinstance(value, str) and bool(value) and len(value) <= 180


def assessment_chunks(transcript, candidate_name):
    """Legacy timed text, with a name match only when unambiguous within a part."""
    alphabet = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюя',
        ('a','b','v','g','d','e','e','zh','z','i','y','k','l','m','n','o','p','r','s','t','u','f','kh','ts','ch','sh','shch','','y','','e','yu','ya')))
    def tokens(value):
        return set(re.findall('[a-z]+', ''.join(alphabet.get(c, c) for c in value.lower())))
    turns = transcript_turns(transcript)
    for turn in turns:
        if not turn['speakerName']:
            prefix = re.match(r"^([A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё .'-]{0,79}):\s+", turn['text'])
            if prefix:
                turn['speakerName'] = prefix[1]
    labels = {t['speakerName'] for t in turns if t['speakerName']}
    matches = [label for label in labels if tokens(label) and tokens(label) <= tokens(candidate_name)]
    if len(matches) == 1:
        for turn in turns:
            if turn['speakerName'] == matches[0]:
                turn['speakerName'] = 'Кандидат'
    return search_chunks(turns)


def _segments(version, recording_id):
    rows = version.get('segments')
    if not rows:
        return []
    _check(isinstance(rows, list) and len(rows) <= 6000, 'transcript_format_unsupported', 422)
    result = []
    for i, row in enumerate(rows):
        _check(isinstance(row, dict), 'transcript_format_unsupported', 422)
        start, end = row.get('start_ms', row.get('startMs')), row.get('end_ms', row.get('endMs'))
        text, voice = row.get('text'), row.get('speaker', row.get('speakerId'))
        _check(type(start) in (int, float) and math.isfinite(start) and
               type(end) in (int, float) and math.isfinite(end) and 0 <= start <= end <= 14400000 and
               isinstance(text, str) and bool(text.strip()), 'transcript_format_unsupported', 422)
        _check(voice is None or isinstance(voice, str), 'transcript_format_unsupported', 422)
        result.append({'id': 'segment-' + str(i), 'startMs': start, 'endMs': end, 'text': text,
            'speakerId': recording_id + ':' + (voice or 'unknown'), 'speakerName': voice or ''})
    return sorted(result, key=lambda row: (row['startMs'], row['endMs'], row['id']))


def bundle(materials, *, allow_legacy=False):
    """Return every required recording exactly once, or fail closed.

    Multipart always requires explicit recordingId links. Legacy inference is
    restricted to one recording/one transcript and an explicitly enabled caller.
    Ordinals must be complete and unique; otherwise order is stable by ID, with
    no assertion that that order is chronological.
    """
    _check(isinstance(materials, dict), 'waiting_for_transcript')
    channel = materials.get('transcription', {})
    _check(isinstance(channel, dict), 'waiting_for_transcript')
    state = channel.get('status')
    _check(state == 'ready' or allow_legacy and state is None, 'waiting_for_transcript')
    recordings, versions = materials.get('recordings', []), materials.get('transcripts', [])
    _check(isinstance(recordings, list) and 1 <= len(recordings) <= 10 and
           isinstance(versions, list) and len(versions) == len(recordings), 'waiting_for_transcript')
    by_recording = {}
    for recording in recordings:
        _check(isinstance(recording, dict) and _identifier(recording.get('id')))
        _check(recording['id'] not in by_recording)
        by_recording[recording['id']] = recording
    mapped, tids = {}, set()
    for version in versions:
        _check(isinstance(version, dict))
        rid = version.get('recordingId')
        if rid is None and allow_legacy and len(recordings) == 1:
            rid = recordings[0]['id']
        _check(_identifier(rid) and rid in by_recording and rid not in mapped)
        text = version.get('text') or version.get('fullText') or ''
        _check(isinstance(text, str) and len(text.encode()) <= 1000000, 'transcript_format_unsupported', 422)
        segments = _segments(version, rid)
        _check(text.strip() or segments, 'waiting_for_transcript')
        tid = version.get('id')
        if not tid and allow_legacy and len(recordings) == 1:
            tid = 'legacy-' + hashlib.sha256(text.encode()).hexdigest()
        _check(_identifier(tid) and tid not in tids)
        tids.add(tid)
        duration = by_recording[rid].get('duration', by_recording[rid].get('durationSeconds'))
        if duration is not None:
            _check(type(duration) in (int, float) and math.isfinite(duration) and
                   0 < duration <= 14400, 'transcript_format_unsupported', 422)
        mapped[rid] = {'recordingId': rid, 'duration': duration, 'transcriptId': tid, 'text': text, 'segments': segments,
            'textSha256': hashlib.sha256(text.encode()).hexdigest(),
            **{key: version.get(key) for key in ('revision', 'selectionRevision', 'generation', 'origin', 'model')}}
    ordinals = [r.get('partIndex', r.get('part_index')) for r in recordings]
    explicit = all(type(n) is int and n >= 0 for n in ordinals) and len(set(ordinals)) == len(ordinals)
    ordered = sorted(recordings, key=lambda r: (r.get('partIndex', r.get('part_index')), r['id']) if explicit else r['id'])
    return {'schemaVersion': 1, 'status': 'ready', 'parts': [
        {**mapped[r['id']], 'partIndex': index} for index, r in enumerate(ordered)]}


def metadata(value):
    return [{key: part.get(key) for key in ('recordingId', 'partIndex', 'transcriptId', 'revision',
        'selectionRevision', 'generation', 'textSha256', 'origin', 'model', 'duration')} for part in value['parts']]


def fingerprint(value):
    # Segment identity also covers local timing and voices, independently of text.
    return selection.digest([metadata(value), [p['segments'] for p in value['parts']]])


def chunks(value, candidate_name=''):
    result = []
    for part in value['parts']:
        if part['segments']:
            turns = [{**s, 'speakerName': s['speakerId']} for s in part['segments']]
            rows = search_chunks(turns)
        else:
            rows = assessment_chunks(part['text'], candidate_name)
        _check(bool(rows), 'transcript_format_unsupported', 422)
        prefix = hashlib.sha256((part['recordingId'] + '\0' + part['transcriptId']).encode()).hexdigest()[:24]
        for row in rows:
            _check(part.get('duration') is None or row['endMs'] <= part['duration'] * 1000,
                   'transcript_format_unsupported', 422)
            text, _ = scan(row['text'])
            # Speaker codes have only recording-local meaning, including legacy VTT.
            speaker = row['speakerName']
            if speaker not in ('Кандидат', 'Рекрутер'):
                # Bounded display label, distinct even for identical provider codes.
                raw_voice = speaker.partition(':')[2] if part['segments'] else speaker
                speaker = 'r' + prefix + ':' + (raw_voice or 'unknown')[:65] + ':' + hashlib.sha256((raw_voice or 'unknown').encode()).hexdigest()[:12]
            speaker, _ = scan(speaker)
            result.append({'id': 'p' + prefix + '.' + row['id'], 'text': text,
                'speaker': speaker, 'startMs': row['startMs'], 'endMs': row['endMs'],
                'recordingId': part['recordingId'], 'transcriptId': part['transcriptId']})
    _check(len(result) <= 600, 'transcript_too_large', 413)
    return result


def check_evidence(assessment, source):
    """Resolve service evidence against the immutable submitted fragment map."""
    if source.get('sourceHash'):
        _check(assessment.get('provenance', {}).get('sourceHash') == source['sourceHash'],
               'invalid_assessment_response', 502)
    expected = {row['id']: row for row in source.get('inputChunks', [])}
    _check(bool(expected), 'invalid_assessment_response', 502)
    for criterion in assessment.get('criteria', []):
        for evidence in criterion.get('evidence', []):
            row = expected.get(evidence.get('id'))
            _check(row is not None and evidence.get('recordingId') == row['recordingId'] and
                evidence.get('transcriptId') == row['transcriptId'] and evidence.get('text') == row['text'] and
                evidence.get('speaker') == row['speaker'] and evidence.get('seconds') == row['startMs'] / 1000 and
                evidence.get('endSeconds') == row['endMs'] / 1000, 'invalid_assessment_response', 502)
