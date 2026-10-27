"""Read-only adapter for the existing DSA candidate panel. No source mutations."""
import json
import hashlib
import math
import re
import threading
import time
import urllib.request
from domain import Problem

SOURCE = 'https://kabinet.158-160-179-53.sslip.io'
EXAMPLE_IDS = ('378713', '380179', '380455', '380989')


def prepared_example(person, transcript_text):
    """Build an explicitly marked private example from existing source evidence.

    Called only by the operator import script, never by a public request. The
    full transcript must be the saved source file with the exact candidate ID;
    short source quotations remain separate from authored chapter summaries.
    """
    cid = person.get('id')
    if cid not in EXAMPLE_IDS:
        raise ValueError('Source candidate is not in the verified example set')
    if person.get('video') != SOURCE + '/api/video/' + cid:
        raise ValueError('Missing verified source video')
    if person.get('photo') != SOURCE + '/faces/' + cid + '.jpg':
        raise ValueError('Missing source photo')
    duration = person.get('duration')
    if type(duration) not in (int, float) or not 0 < duration <= 14400 or not math.isfinite(duration):
        raise ValueError('Invalid source duration')
    if (not isinstance(transcript_text, str) or not 100 < len(transcript_text) <= 250000 or
            not re.search(r'\bсделка\s+' + re.escape(cid) + r'\b', transcript_text.splitlines()[0])):
        raise ValueError('Transcript does not identify this source candidate')
    clock = re.search(r'длительность (\d+(?::\d{2}){1,2})', transcript_text.splitlines()[0])
    if not clock:
        raise ValueError('Transcript duration is missing')
    seconds = 0
    for part in clock.group(1).split(':'):
        seconds = seconds * 60 + int(part)
    if seconds != duration:
        raise ValueError('Transcript duration differs from the source recording')
    if not re.search(r'^\[\d+(?::\d{2}){1,2}-\d+(?::\d{2}){1,2}\]\s+[РК]:', transcript_text, re.M):
        raise ValueError('Transcript has no source speaker/timecoded text')
    cv = person.get('cv')
    if not isinstance(cv, str) or not cv.strip() or len(cv) > 200000:
        raise ValueError('Missing source CV')
    chapters = []
    for item in person.get('shortChapters') or []:
        start, end, fragment = item.get('seconds'), item.get('endSeconds'), item.get('fragment')
        if (type(start) not in (int, float) or not 0 <= start < duration or not math.isfinite(start) or
                type(end) not in (int, float) or not start < end <= duration or not math.isfinite(end) or
                item.get('isQuote') is not True or not isinstance(fragment, str) or not fragment.strip()):
            raise ValueError('Invalid source quotation or timing')
        chapters.append({'title': str(item.get('title') or ''), 'seconds': start, 'endSeconds': end,
                         'fragment': fragment, 'isQuote': True})
    if not chapters:
        raise ValueError('Missing source interview excerpts')
    score = person.get('score')
    if type(score) not in (int, float) or not math.isfinite(score):
        score = None
    return {
        'sourceLabel': 'Пример из панели ДСА',
        'source': {'kind': 'dsa-panel', 'candidateId': cid, 'example': True},
        'photo': person['photo'], 'score': score, 'profile': person.get('profile') or [],
        'summary': str(person.get('role') or ''),
        'cvText': cv, 'evaluationText': '\n\n'.join(person.get('conclusion') or []),
        'chapters': chapters, 'transcriptText': transcript_text,
        'transcriptSource': {'candidateId': cid, 'kind': 'saved-source-transcript',
                             'sha256': hashlib.sha256(transcript_text.encode()).hexdigest()},
        'recordings': [{'name': 'Интервью с рекрутером', 'publicUrl': person['video'], 'mime': 'video/mp4',
                        'duration': duration, 'source': 'dsa-panel', 'sourceCandidateId': cid}],
    }


def strings(value):
    return [s for s in value if isinstance(s, str)] if isinstance(value, list) else []


def chapters(items, short=False):
    result = []
    for item in items if isinstance(items, list) else []:
        start, end = item.get('начало'), item.get('конец')
        if not isinstance(start, (int, float)) or start < 0:
            continue
        result.append({'title': item.get('метка' if short else 'название', ''),
                       'detail': item.get('о_чём', ''),
                       'fragment': item.get('цитата' if short else 'о_чём', ''),
                       'source': 'Интервью с рекрутером · ' + str(item.get('таймкод', '')),
                       'seconds': start, 'endSeconds': end if isinstance(end, (int, float)) and end > start else None,
                       'isQuote': short})
    return result


def normalize(data):
    vacancy = data.get('вакансия') or {}
    candidates = []
    for person in data.get('кандидаты', []):
        cid = str(person.get('id', ''))
        if not re.fullmatch(r'\d{1,12}', cid):
            raise ValueError('Unexpected source candidate id')
        cut = person.get('нарезка') or {}
        candidates.append({
            'id': cid, 'name': person.get('имя', ''), 'initials': person.get('инициалы', ''),
            'role': person.get('роль', ''), 'interviewRole': person.get('вакансия_интервью', ''),
            'interviewDate': person.get('дата_интервью', ''), 'score': person.get('оценка'),
            'duration': person.get('длительность'),
            'photo': SOURCE + '/faces/' + cid + '.jpg' if person.get('лицо') else None,
            'video': SOURCE + '/api/video/' + cid if (person.get('видео') or {}).get('есть') else None,
            'shortChapters': chapters(cut.get('куски'), short=True),
            'fullChapters': chapters(person.get('главы')),
            'shortSummary': cut.get('о_чём', ''),
            'profile': [{'label': p.get('метка', ''), 'value': p.get('значение', '')} for p in person.get('профиль', [])],
            'cv': person.get('cv', ''), 'conclusion': strings(person.get('заключение')),
            'questions': strings(person.get('уточнить')),
            'sourceStatus': person.get('статус', ''), 'sourceUpdatedAt': person.get('статус_обновлён'),
        })
    if len({c['id'] for c in candidates}) != len(candidates):
        raise ValueError('Duplicate source candidate id')
    return {'source': SOURCE, 'company': vacancy.get('компания', 'ДСА ИНЖИНИРИНГ'),
            'role': vacancy.get('название', ''), 'selection': vacancy.get('подборка', ''),
            'candidates': candidates, 'fetchedAt': int(time.time())}


class DsaPanel:
    def __init__(self):
        self.lock = threading.Lock()
        self.cached = None
        self.expires = 0

    def load(self):
        with self.lock:
            if self.cached is not None and time.monotonic() < self.expires:
                return self.cached
            try:
                request = urllib.request.Request(SOURCE + '/api/candidates', headers={'Accept': 'application/json'})
                with urllib.request.urlopen(request, timeout=15) as response:
                    raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise ValueError('Source response too large')
                self.cached = normalize(json.loads(raw))
                self.expires = time.monotonic() + 60
                return self.cached
            except Exception:
                raise Problem('Панель ДСА временно недоступна. Попробуйте обновить подборку.', 502) from None
