"""Read-only provenance for an already-authorized recruiter's candidate.

Source responses are distinct from the vacancies proposed after an interview.
Only public vacancy metadata is projected; connection credentials, CV payloads
and invitation capabilities never cross this boundary.
"""
import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit


LABELS = {'hh': 'HH', 'manual': 'Добавлен из CV', 'registration': 'Регистрация'}


def _text(value, limit=200):
    return value.strip()[:limit] if isinstance(value, str) else ''


def _vacancy_id(value):
    """Only an observed numeric HH vacancy ID can form a public fallback URL."""
    return value if isinstance(value, str) and re.fullmatch(r'[0-9]{1,32}', value) else ''


def _vacancy_url(value):
    """Keep only a public HH vacancy path, without query or fragment secrets."""
    if not isinstance(value, str):
        return ''
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        if (parsed.scheme != 'https' or parsed.username or parsed.password
                or parsed.port not in (None, 443)
                or not (host == 'hh.ru' or host.endswith('.hh.ru'))
                or not re.fullmatch(r'/vacancy/[0-9]+/?', parsed.path)):
            return ''
        return urlunsplit(('https', host, parsed.path, '', ''))
    except ValueError:
        return ''


def candidate_sources(db, candidate_id):
    """Caller must first scope candidate_id to the recruiter's assigned work."""
    tables = {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    intake = (db.execute('SELECT source FROM cp_intake WHERE candidate_id=?',
                         (candidate_id,)).fetchone() if 'cp_intake' in tables else None)
    kind = intake[0] if intake and intake[0] in ('hh', 'manual') else 'registration'
    applications, vacancy_indices = [], {}
    if {'hh_resumes', 'hh_negotiations', 'hh_vacancies'} <= tables:
        rows = db.execute('''SELECT n.connection_id,n.external_id,v.body,v.external_id
          FROM hh_resumes r JOIN hh_negotiations n
            ON n.connection_id=r.connection_id AND n.resume_id=r.external_id
          JOIN hh_vacancies v
            ON v.connection_id=n.connection_id AND v.external_id=n.vacancy_id
          WHERE r.candidate_id=?
          ORDER BY n.synced DESC,n.connection_id,n.external_id''', (candidate_id,))
        for connection, response_id, raw_body, external_vacancy_id in rows:
            vacancy_id = _vacancy_id(external_vacancy_id)
            if vacancy_id and vacancy_id in vacancy_indices:
                continue
            try:
                body = json.loads(raw_body)
            except (ValueError, TypeError):
                body = {}
            if not isinstance(body, dict):
                body = {}
            employer = body.get('employer')
            company = _text(body.get('company'))
            if not company and isinstance(employer, dict):
                company = _text(employer.get('name'))
            identity = ('vacancy:' + vacancy_id) if vacancy_id else json.dumps([connection, response_id], separators=(',', ':'))
            if vacancy_id:
                vacancy_indices[vacancy_id] = len(applications)
            applications.append({
                'id': 'hh_' + hashlib.sha256(identity.encode()).hexdigest()[:24],
                'title': _text(body.get('title')),
                'company': company,
                'url': _vacancy_url(body.get('url')),
            })
        if applications and not intake:
            kind = 'hh'
    if {'ho_recipients', 'ho_campaigns'} <= tables:
        # Browser campaigns have source identifiers, not provider vacancy copy.
        # Do not use campaign names, CV roles or proposed post-interview jobs as
        # a source vacancy title; these describe different things.
        rows = db.execute('''SELECT r.vacancy_id FROM ho_recipients r
          JOIN ho_campaigns c ON c.id=r.campaign_id
          WHERE r.candidate_id=? AND c.mode IN ('direct','applicants')
          ORDER BY r.created DESC,r.id''', (candidate_id,))
        for row in rows:
            if not intake:
                kind = 'hh'
            vacancy_id = _vacancy_id(row[0])
            if not vacancy_id:
                continue
            title, url = 'Вакансия HH №' + vacancy_id, 'https://hh.ru/vacancy/' + vacancy_id
            if vacancy_id in vacancy_indices:
                # Preserve actual API metadata when the same vacancy is known
                # through both transports; fill only missing public context.
                existing = applications[vacancy_indices[vacancy_id]]
                existing['title'] = existing['title'] or title
                existing['url'] = existing['url'] or url
                continue
            vacancy_indices[vacancy_id] = len(applications)
            applications.append({
                'id': 'hh_' + hashlib.sha256(('vacancy:' + vacancy_id).encode()).hexdigest()[:24],
                'title': title, 'company': '', 'url': url,
            })
    return {'source': {'kind': kind, 'label': LABELS[kind]},
            'sourceApplications': applications}
