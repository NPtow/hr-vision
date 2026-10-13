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
    applications = []
    if {'hh_resumes', 'hh_negotiations', 'hh_vacancies'} <= tables:
        rows = db.execute('''SELECT n.connection_id,n.external_id,v.body
          FROM hh_resumes r JOIN hh_negotiations n
            ON n.connection_id=r.connection_id AND n.resume_id=r.external_id
          JOIN hh_vacancies v
            ON v.connection_id=n.connection_id AND v.external_id=n.vacancy_id
          WHERE r.candidate_id=?
          ORDER BY n.synced DESC,n.connection_id,n.external_id''', (candidate_id,))
        for connection, response_id, raw_body in rows:
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
            identity = json.dumps([connection, response_id], separators=(',', ':'))
            applications.append({
                'id': 'hh_' + hashlib.sha256(identity.encode()).hexdigest()[:24],
                'title': _text(body.get('title')),
                'company': company,
                'url': _vacancy_url(body.get('url')),
            })
        if applications and not intake:
            kind = 'hh'
    return {'source': {'kind': kind, 'label': LABELS[kind]},
            'sourceApplications': applications}
