"""Read-only adapter for the existing DSA candidate panel. No source mutations."""
import json
import re
import threading
import time
import urllib.request
from domain import Problem

SOURCE = 'https://kabinet.158-160-179-53.sslip.io'


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
