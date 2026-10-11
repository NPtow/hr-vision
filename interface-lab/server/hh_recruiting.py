"""Bounded HH source + invitation outbox for the existing recruiter portal.

No routes, scheduler, default audience, or automatic account/consent creation.
Callers authorize the recruiter before passing their trusted actor_id. The DB and
its WAL contain tokens and candidate data and must remain private (0700 directory).
Provider responses are untrusted; exceptions and public DTOs never include them.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import re
import secrets
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


API = 'https://api.hh.ru'
AUTHORIZE = 'https://hh.ru/oauth/authorize'
MAX_RESPONSE = 8 * 1024 * 1024
MAX_ATTEMPTS = 5


class HHError(Exception):
    """Only an allowlisted code/status may cross the HTTP integration boundary."""
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


class TransportUnavailable(Exception):
    pass


@dataclass(frozen=True)
class HHConfig:
    enabled: bool = False
    sending_enabled: bool = False
    client_id: str = field(default='', repr=False)
    client_secret: str = field(default='', repr=False)
    redirect_uri: str = ''
    user_agent: str = ''
    invite_origin: str = ''
    max_pages: int = 100


@dataclass
class Response:
    status: int
    body: object = None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTPTransport:
    """Fixed host, no redirects, bounded response/timeout, no request logging."""
    def __call__(self, method, url, *, headers, json_body=None, form=None):
        safe_path(url)
        headers = dict(headers)
        data = None
        if json_body is not None:
            headers['Content-Type'] = 'application/json'
            data = encode(json_body).encode()
        elif form is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
            data = urllib.parse.urlencode(form).encode()
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            try:
                result = urllib.request.build_opener(_NoRedirect()).open(request, timeout=20)
            except urllib.error.HTTPError as response:
                result = response
            with result:
                raw = result.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise HHError('provider_response_too_large', 502)
                try:
                    body = json.loads(raw) if raw else None
                except (ValueError, UnicodeDecodeError):
                    body = None
                return Response(result.status, body)
        except (urllib.error.URLError, TimeoutError, OSError):
            raise TransportUnavailable('hh_unavailable') from None


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def require(condition, code, status=400):
    if not condition:
        raise HHError(code, status)


def identifier(value):
    value = str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) else ''
    require(bool(re.fullmatch(r'[A-Za-z0-9_-]{1,160}', value)), 'invalid_identifier')
    return value


def short(value, limit=200):
    return value.strip()[:limit] if isinstance(value, str) else ''


def safe_path(value):
    require(isinstance(value, str), 'invalid_provider_url', 502)
    url = urllib.parse.urlsplit(value)
    require(not url.fragment and not url.username and not url.password and
            (not url.netloc and not url.scheme or url.scheme == 'https' and url.netloc == 'api.hh.ru'),
            'invalid_provider_url', 502)
    require(url.path.startswith('/') and not url.path.startswith('//') and
            not any(x in urllib.parse.unquote(url.path) for x in ('..', '\\', '\r', '\n')),
            'invalid_provider_url', 502)
    return urllib.parse.urlunsplit(('', '', url.path, url.query, ''))


def query_path(path, **values):
    parsed = urllib.parse.urlsplit(safe_path(path))
    pairs = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query) if k not in values]
    pairs.extend((k, str(v).lower() if isinstance(v, bool) else str(v)) for k, v in values.items())
    return parsed.path + '?' + urllib.parse.urlencode(pairs)


def error_values(body):
    if not isinstance(body, dict):
        return set()
    return {item.get('value') for item in body.get('errors', []) if isinstance(item, dict)}


def normalize_resume(resume):
    """Only supplied CV facts. No invented salary range, consent, or work dates."""
    profile = {}
    name = ' '.join(value for k in ('first_name', 'middle_name', 'last_name') if (value := short(resume.get(k), 70)))
    if name:
        profile['name'] = name[:140]
    area = resume.get('area') or {}
    if isinstance(area, dict) and short(area.get('name'), 120):
        profile['city'] = short(area['name'], 120)
    if short(resume.get('title')):
        profile['desiredRole'] = short(resume['title'])
    if short(resume.get('skills'), 1500):
        profile['about'] = short(resume['skills'], 1500)
    employment = resume.get('employment') or {}
    employment_id = employment.get('id') if isinstance(employment, dict) else None
    mapping = {'full': 'full-time', 'part': 'part-time', 'project': 'project', 'probation': 'internship'}
    if employment_id in mapping:
        profile['employmentType'] = mapping[employment_id]
    contact_email = ''
    for contact in resume.get('contact') or []:
        if isinstance(contact, dict) and isinstance(contact.get('type'), dict) and contact['type'].get('id') == 'email':
            value = short(contact.get('value'), 254)
            if re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
                contact_email = value
                break
    lines = [profile.get('name', ''), profile.get('desiredRole', ''), profile.get('city', ''), profile.get('about', '')]
    for job in (resume.get('experience') or [])[:50]:
        if isinstance(job, dict):
            lines.append(' · '.join(short(job.get(k), 300) for k in ('start', 'end', 'company', 'position')))
            lines.append(short(job.get('description'), 4000))
    return profile, {'contactEmail': contact_email,
                     'canViewFullInfo': resume.get('can_view_full_info') is True,
                     'cvText': '\n'.join(line for line in lines if line)[:80000]}


class HHRecruiting:
    def __init__(self, connect, config=None, *, transport=None, prepare_candidate=None, clock=time.time):
        self.connect, self.config = connect, config or HHConfig()
        self.transport = transport or HTTPTransport()
        self.prepare_candidate, self.clock = prepare_candidate, clock
        with self.db() as db:
            self.init(db)

    @contextmanager
    def db(self, write=False):
        db = self.connect()
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA busy_timeout=5000')
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            if write:
                db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def init(db):
        db.executescript('''
        CREATE TABLE IF NOT EXISTS hh_oauth_states (
          digest TEXT PRIMARY KEY, actor_id TEXT NOT NULL, expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS hh_connections (
          id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, employer_id TEXT NOT NULL, manager_id TEXT NOT NULL,
          access_token TEXT NOT NULL, refresh_token TEXT NOT NULL, expires REAL NOT NULL,
          status TEXT NOT NULL, permissions TEXT NOT NULL DEFAULT '{}',
          refresh_owner TEXT, refresh_until REAL NOT NULL DEFAULT 0, updated REAL NOT NULL,
          UNIQUE(actor_id,employer_id,manager_id));
        CREATE TABLE IF NOT EXISTS hh_vacancies (
          connection_id TEXT NOT NULL REFERENCES hh_connections(id), external_id TEXT NOT NULL,
          local_id TEXT, body TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, synced REAL NOT NULL,
          PRIMARY KEY(connection_id,external_id));
        CREATE TABLE IF NOT EXISTS hh_resumes (
          connection_id TEXT NOT NULL REFERENCES hh_connections(id), external_id TEXT NOT NULL,
          candidate_id TEXT NOT NULL, profile TEXT NOT NULL, source TEXT NOT NULL, synced REAL NOT NULL,
          PRIMARY KEY(connection_id,external_id));
        CREATE TABLE IF NOT EXISTS hh_negotiations (
          connection_id TEXT NOT NULL REFERENCES hh_connections(id), external_id TEXT NOT NULL,
          vacancy_id TEXT NOT NULL, resume_id TEXT NOT NULL, chat_id TEXT,
          state TEXT NOT NULL, cv_status TEXT NOT NULL, synced REAL NOT NULL,
          PRIMARY KEY(connection_id,external_id));
        CREATE TABLE IF NOT EXISTS hh_send_rules (
          id TEXT PRIMARY KEY, connection_id TEXT NOT NULL REFERENCES hh_connections(id),
          vacancy_id TEXT NOT NULL, event_type TEXT NOT NULL, template_id TEXT NOT NULL,
          template TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
          UNIQUE(connection_id,vacancy_id,event_type,template_id));
        CREATE TABLE IF NOT EXISTS hh_outbox (
          id TEXT PRIMARY KEY, connection_id TEXT NOT NULL REFERENCES hh_connections(id),
          negotiation_id TEXT NOT NULL, candidate_id TEXT NOT NULL, rule_id TEXT NOT NULL REFERENCES hh_send_rules(id),
          event_key TEXT NOT NULL, invitation_id TEXT NOT NULL, invitation_url TEXT NOT NULL,
          text TEXT NOT NULL, chat_id TEXT NOT NULL, message_uuid TEXT NOT NULL UNIQUE,
          status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
          next_attempt REAL NOT NULL, lease_until REAL NOT NULL DEFAULT 0, claim_token TEXT,
          provider_message_id TEXT, last_error TEXT, created REAL NOT NULL, updated REAL NOT NULL,
          UNIQUE(connection_id,negotiation_id,event_key));
        CREATE INDEX IF NOT EXISTS hh_outbox_due ON hh_outbox(status,next_attempt);
        ''')

    def configured(self):
        require(self.config.enabled, 'hh_disabled', 503)
        require(bool(self.config.client_id and self.config.client_secret and self.config.user_agent),
                'hh_not_configured', 503)
        uri = urllib.parse.urlsplit(self.config.redirect_uri)
        require(uri.scheme == 'https' and bool(uri.netloc) and not uri.fragment and not uri.username,
                'hh_not_configured', 503)

    def headers(self, token=None):
        headers = {'HH-User-Agent': self.config.user_agent, 'Accept': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        return headers

    def raw(self, method, path, *, token=None, form=None, json_body=None):
        self.configured()
        return self.transport(method, API + safe_path(path), headers=self.headers(token),
                              form=form, json_body=json_body)

    def begin_oauth(self, actor_id):
        self.configured()
        actor_id = identifier(actor_id)
        state = secrets.token_urlsafe(32)
        with self.db(write=True) as db:
            db.execute('DELETE FROM hh_oauth_states WHERE expires<?', (self.clock(),))
            db.execute('INSERT INTO hh_oauth_states VALUES (?,?,?,0)',
                       (hashlib.sha256(state.encode()).hexdigest(), actor_id, self.clock() + 600))
        return AUTHORIZE + '?' + urllib.parse.urlencode({'response_type': 'code', 'client_id': self.config.client_id,
                                                         'redirect_uri': self.config.redirect_uri, 'state': state})

    @staticmethod
    def tokens(response):
        require(response.status == 200 and isinstance(response.body, dict), 'oauth_exchange_failed', 502)
        body = response.body
        require(all(isinstance(body.get(k), str) and 0 < len(body[k]) <= 8192 for k in ('access_token', 'refresh_token'))
                and isinstance(body.get('expires_in'), (int, float)) and not isinstance(body['expires_in'], bool)
                and 0 < body['expires_in'] <= 366 * 86400 and body.get('token_type', '').lower() == 'bearer',
                'oauth_invalid_response', 502)
        return body['access_token'], body['refresh_token'], body['expires_in']

    def finish_oauth(self, actor_id, state, code):
        self.configured()
        require(isinstance(state, str) and 20 <= len(state) <= 200 and isinstance(code, str) and 1 <= len(code) <= 4096,
                'oauth_invalid_state')
        digest = hashlib.sha256(state.encode()).hexdigest()
        with self.db(write=True) as db:
            changed = db.execute('UPDATE hh_oauth_states SET used=1 WHERE digest=? AND actor_id=? AND used=0 AND expires>?',
                                 (digest, actor_id, self.clock())).rowcount
            require(changed == 1, 'oauth_invalid_state')
        access, refresh, duration = self.tokens(self.raw('POST', '/token', form={
            'grant_type': 'authorization_code', 'client_id': self.config.client_id,
            'client_secret': self.config.client_secret, 'redirect_uri': self.config.redirect_uri, 'code': code}))
        response = self.raw('GET', '/me', token=access)
        body = response.body if isinstance(response.body, dict) else {}
        require(response.status == 200 and body.get('auth_type') == 'employer', 'employer_authorization_required', 403)
        employer = identifier((body.get('employer') or {}).get('id'))
        manager = identifier((body.get('manager') or {}).get('id'))
        connection_id = 'hh_' + secrets.token_hex(12)
        with self.db(write=True) as db:
            db.execute('''INSERT INTO hh_connections
              (id,actor_id,employer_id,manager_id,access_token,refresh_token,expires,status,updated)
              VALUES (?,?,?,?,?,?,?,'connected',?) ON CONFLICT(actor_id,employer_id,manager_id) DO UPDATE SET
              access_token=excluded.access_token,refresh_token=excluded.refresh_token,expires=excluded.expires,
              status='connected',refresh_owner=NULL,refresh_until=0,updated=excluded.updated''',
                       (connection_id, actor_id, employer, manager, access, refresh, self.clock() + duration, self.clock()))
            connection_id = db.execute('SELECT id FROM hh_connections WHERE actor_id=? AND employer_id=? AND manager_id=?',
                                       (actor_id, employer, manager)).fetchone()[0]
        return self.connection(actor_id, connection_id)

    def owned(self, db, actor_id, connection_id):
        row = db.execute('SELECT * FROM hh_connections WHERE id=? AND actor_id=?', (connection_id, actor_id)).fetchone()
        require(row is not None, 'connection_not_found', 404)
        return dict(row)

    def connection(self, actor_id, connection_id):
        with self.db() as db:
            row = self.owned(db, actor_id, connection_id)
        return {key: row[key] for key in ('id', 'employer_id', 'manager_id', 'status', 'updated')}

    def mark_connection(self, connection_id, status):
        with self.db(write=True) as db:
            db.execute('UPDATE hh_connections SET status=?,updated=? WHERE id=?', (status, self.clock(), connection_id))

    def access_token(self, actor_id, connection_id, stale=None):
        owner = secrets.token_hex(12)
        with self.db(write=True) as db:
            row = self.owned(db, actor_id, connection_id)
            require(row['status'] != 'reauth_required', 'reauth_required', 401)
            if row['expires'] > self.clock() + 30 and (stale is None or stale != row['access_token']):
                return row['access_token']
            require(row['refresh_until'] <= self.clock(), 'refresh_in_progress', 503)
            db.execute('UPDATE hh_connections SET refresh_owner=?,refresh_until=? WHERE id=?',
                       (owner, self.clock() + 60, connection_id))
        try:
            response = self.raw('POST', '/token', form={'grant_type': 'refresh_token', 'refresh_token': row['refresh_token']})
            if response.status in (400, 401, 403):
                self.mark_connection(connection_id, 'reauth_required')
                raise HHError('reauth_required', 401)
            access, refresh, duration = self.tokens(response)
            with self.db(write=True) as db:
                changed = db.execute('''UPDATE hh_connections SET access_token=?,refresh_token=?,expires=?,status='connected',
                  refresh_owner=NULL,refresh_until=0,updated=? WHERE id=? AND refresh_owner=?''',
                                     (access, refresh, self.clock() + duration, self.clock(), connection_id, owner)).rowcount
                require(changed == 1, 'refresh_replaced', 503)
            return access
        finally:
            with self.db(write=True) as db:
                db.execute('UPDATE hh_connections SET refresh_owner=NULL,refresh_until=0 WHERE id=? AND refresh_owner=?',
                           (connection_id, owner))

    def request(self, actor_id, connection_id, method, path, *, json_body=None):
        token = self.access_token(actor_id, connection_id)
        response = self.raw(method, path, token=token, json_body=json_body)
        values = error_values(response.body)
        if response.status in (401, 403) and 'token_expired' in values:
            token = self.access_token(actor_id, connection_id, stale=token)
            response = self.raw(method, path, token=token, json_body=json_body)
            values = error_values(response.body)
        if response.status in (401, 403):
            if values & {'token_revoked', 'bad_authorization', 'application_not_found', 'token_expired'}:
                self.mark_connection(connection_id, 'reauth_required')
            elif 'no_scope' in values:
                self.mark_connection(connection_id, 'access_required')
        return response

    def get(self, actor_id, connection_id, path):
        response = self.request(actor_id, connection_id, 'GET', path)
        require(response.status == 200 and isinstance(response.body, dict),
                'provider_access_denied' if response.status in (401, 403) else 'provider_read_failed',
                403 if response.status in (401, 403) else 502)
        return response.body

    def check_access(self, actor_id, connection_id):
        row = self.connection(actor_id, connection_id)
        body = self.get(actor_id, connection_id,
                        f"/employers/{row['employer_id']}/managers/{row['manager_id']}/method_access")
        with self.db(write=True) as db:
            db.execute('UPDATE hh_connections SET permissions=?,updated=? WHERE id=?', (encode(body), self.clock(), connection_id))
        return body

    def pages(self, actor_id, connection_id, path):
        items = []
        for page in range(self.config.max_pages):
            body = self.get(actor_id, connection_id, query_path(path, page=page, per_page=50))
            count = body.get('pages')
            require(isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= self.config.max_pages
                    and body.get('page') == page and isinstance(body.get('items'), list), 'invalid_pagination', 502)
            items.extend(body['items'])
            require(len(items) <= 10000, 'sync_limit_exceeded', 502)
            if count == 0 or page + 1 >= count:
                return items
        raise HHError('sync_limit_exceeded', 502)

    def sync_vacancies(self, actor_id, connection_id):
        row = self.connection(actor_id, connection_id)
        items = self.pages(actor_id, connection_id, f"/employers/{row['employer_id']}/vacancies/active?all_accessible=true")
        require(all(isinstance(item, dict) for item in items), 'invalid_vacancy_response', 502)
        normalized = [(identifier(item.get('id')), {'id': identifier(item.get('id')), 'title': short(item.get('name')),
                       'url': short(item.get('alternate_url'), 500), 'city': short((item.get('area') or {}).get('name'), 120)})
                      for item in items if isinstance(item, dict)]
        with self.db(write=True) as db:
            self.owned(db, actor_id, connection_id)
            db.execute('UPDATE hh_vacancies SET active=0 WHERE connection_id=?', (connection_id,))
            for external_id, body in normalized:
                db.execute('''INSERT INTO hh_vacancies(connection_id,external_id,body,active,synced) VALUES (?,?,?,1,?)
                  ON CONFLICT(connection_id,external_id) DO UPDATE SET body=excluded.body,active=1,synced=excluded.synced''',
                           (connection_id, external_id, encode(body), self.clock()))
        return self.vacancies(actor_id, connection_id)

    def vacancies(self, actor_id, connection_id):
        with self.db() as db:
            self.owned(db, actor_id, connection_id)
            return [dict(json.loads(row['body']), localId=row['local_id'], active=bool(row['active']), synced=row['synced'])
                    for row in db.execute('SELECT * FROM hh_vacancies WHERE connection_id=? ORDER BY external_id', (connection_id,))]

    def link_vacancy(self, actor_id, connection_id, external_id, local_id):
        identifier(local_id)
        with self.db(write=True) as db:
            self.owned(db, actor_id, connection_id)
            require(db.execute('UPDATE hh_vacancies SET local_id=? WHERE connection_id=? AND external_id=? AND active=1',
                               (local_id, connection_id, external_id)).rowcount == 1, 'vacancy_not_found', 404)

    def sync_vacancy(self, actor_id, connection_id, vacancy_id, *, collection_ids):
        require(callable(self.prepare_candidate), 'prepared_profile_adapter_required', 503)
        vacancy_id = identifier(vacancy_id)
        require(isinstance(collection_ids, (list, tuple)) and 0 < len(collection_ids) <= 50, 'collections_required')
        selected = {identifier(value) for value in collection_ids}
        with self.db() as db:
            self.owned(db, actor_id, connection_id)
            row = db.execute('SELECT local_id FROM hh_vacancies WHERE connection_id=? AND external_id=? AND active=1',
                             (connection_id, vacancy_id)).fetchone()
            require(row and row[0], 'linked_vacancy_required')
        catalog = self.get(actor_id, connection_id, '/negotiations?vacancy_id=' + vacancy_id)
        collection_urls = {}
        def collect(collections):
            require(isinstance(collections, list), 'invalid_collections', 502)
            for item in collections:
                require(isinstance(item, dict), 'invalid_collections', 502)
                key = identifier(item.get('id'))
                if key in selected:
                    path = safe_path(item.get('url'))
                    require(urllib.parse.urlsplit(path).path == '/negotiations/' + key, 'invalid_collection_url', 502)
                    collection_urls[key] = query_path(path, vacancy_id=vacancy_id)
                if item.get('sub_collections'):
                    collect(item['sub_collections'])
        collect(catalog.get('collections'))
        require(selected <= collection_urls.keys(), 'collection_unavailable')
        records, resumes, skipped = {}, {}, set()
        for collection in sorted(selected):
            for item in self.pages(actor_id, connection_id, collection_urls[collection]):
                require(isinstance(item, dict), 'invalid_negotiation', 502)
                nid = identifier(item.get('id'))
                resume = item.get('resume') or {}
                if not isinstance(resume, dict) or not resume.get('id'):
                    # HH permits a null resume (deleted/unavailable). Do not mint
                    # a fake person or fail unrelated, available responses.
                    skipped.add(nid)
                    continue
                rid = identifier(resume.get('id'))
                require(not item.get('vacancy') or str(item['vacancy'].get('id')) == vacancy_id, 'vacancy_mismatch', 502)
                if rid not in resumes:
                    response = self.request(actor_id, connection_id, 'GET', '/resumes/' + rid)
                    status = 'available'
                    if response.status in (403, 404) and not error_values(response.body) & {'token_expired', 'token_revoked', 'bad_authorization'}:
                        full, status = resume, 'unavailable'
                    else:
                        require(response.status == 200 and isinstance(response.body, dict), 'resume_read_failed', 502)
                        full = response.body
                        require(str(full.get('id')) == rid, 'resume_identity_mismatch', 502)
                    profile, source = normalize_resume(full)
                    if not source['canViewFullInfo']:
                        status = 'unavailable'
                    source.update(provider='hh', resumeId=rid, resumeUrl=API + '/resumes/' + rid,
                                  cvAvailable=status == 'available' and source['canViewFullInfo'])
                    resumes[rid] = (profile, source, status)
                records[nid] = {'resume': rid, 'chat': str(item['chat_id']) if item.get('chat_id') is not None else None,
                                'state': short((item.get('employer_state') or item.get('state') or {}).get('id'), 100)}
        with self.db(write=True) as db:
            self.owned(db, actor_id, connection_id)
            for rid, (profile, source, status) in resumes.items():
                external_key = f'hh_resume:v1:{connection_id}:{rid}'
                candidate_id = identifier(self.prepare_candidate(db, external_key, profile, source))
                db.execute('''INSERT INTO hh_resumes VALUES (?,?,?,?,?,?) ON CONFLICT(connection_id,external_id)
                  DO UPDATE SET candidate_id=excluded.candidate_id,profile=excluded.profile,source=excluded.source,synced=excluded.synced''',
                           (connection_id, rid, candidate_id, encode(profile), encode(source), self.clock()))
            for nid, item in records.items():
                db.execute('''INSERT INTO hh_negotiations VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(connection_id,external_id)
                  DO UPDATE SET vacancy_id=excluded.vacancy_id,resume_id=excluded.resume_id,chat_id=excluded.chat_id,
                  state=excluded.state,cv_status=excluded.cv_status,synced=excluded.synced''',
                           (connection_id, nid, vacancy_id, item['resume'], item['chat'], item['state'], resumes[item['resume']][2], self.clock()))
        result = {'negotiations': len(records), 'resumes': len(resumes)}
        if skipped:
            result['skipped'] = len(skipped)
        return result

    def negotiations(self, actor_id, connection_id, vacancy_id):
        """Source references for the existing candidate list, without CV/contact/token bodies."""
        with self.db() as db:
            self.owned(db, actor_id, connection_id)
            return [dict(row) for row in db.execute('''SELECT n.external_id,n.vacancy_id,n.resume_id,
              n.state,n.cv_status,n.synced,r.candidate_id FROM hh_negotiations n JOIN hh_resumes r
              ON r.connection_id=n.connection_id AND r.external_id=n.resume_id
              WHERE n.connection_id=? AND n.vacancy_id=? ORDER BY n.external_id''', (connection_id, vacancy_id))]

    def configure_rule(self, actor_id, connection_id, vacancy_id, *, event_type, template_id, template, enabled=False):
        identifier(event_type); identifier(template_id)
        require(isinstance(template, str) and '{invite_url}' in template and 1 <= len(template) <= 16000, 'invalid_template')
        require(isinstance(enabled, bool), 'invalid_enabled')
        rule_id = 'hhr_' + secrets.token_hex(12)
        with self.db(write=True) as db:
            self.owned(db, actor_id, connection_id)
            row = db.execute('SELECT local_id FROM hh_vacancies WHERE connection_id=? AND external_id=? AND active=1',
                             (connection_id, vacancy_id)).fetchone()
            require(row and row[0], 'linked_vacancy_required')
            old = db.execute('SELECT id,template FROM hh_send_rules WHERE connection_id=? AND vacancy_id=? AND event_type=? AND template_id=?',
                             (connection_id, vacancy_id, event_type, template_id)).fetchone()
            if old:
                require(old['template'] == template, 'template_version_conflict', 409)
                db.execute('UPDATE hh_send_rules SET enabled=? WHERE id=?', (int(enabled), old['id']))
                return old['id']
            db.execute('INSERT INTO hh_send_rules VALUES (?,?,?,?,?,?,?)',
                       (rule_id, connection_id, vacancy_id, event_type, template_id, template, int(enabled)))
        return rule_id

    def enqueue_invitation(self, actor_id, connection_id, negotiation_id, *, rule_id, event_key, invitation_id, url, db=None):
        # The routes adapter supplies its transaction to atomically issue a scoped
        # invitation and enqueue it. Retries must never mint a new capability/UUID.
        if db is None:
            with self.db(write=True) as transaction:
                return self.enqueue_invitation(actor_id, connection_id, negotiation_id, rule_id=rule_id,
                    event_key=event_key, invitation_id=invitation_id, url=url, db=transaction)
        identifier(event_key); identifier(invitation_id)
        parsed = urllib.parse.urlsplit(url)
        origin = urllib.parse.urlsplit(self.config.invite_origin)
        require(origin.scheme == 'https' and bool(origin.netloc) and parsed.scheme == 'https' and
                parsed.netloc == origin.netloc and not parsed.username and not parsed.password and
                (not parsed.fragment or re.fullmatch(r'(invite|claim)=[A-Za-z0-9_-]{40,100}', parsed.fragment))
                and len(url) <= 4096, 'invalid_invitation_url')
        if db is not None:
            self.owned(db, actor_id, connection_id)
            item = db.execute('''SELECT n.*,r.candidate_id FROM hh_negotiations n JOIN hh_resumes r
              ON r.connection_id=n.connection_id AND r.external_id=n.resume_id
              WHERE n.connection_id=? AND n.external_id=?''', (connection_id, negotiation_id)).fetchone()
            require(item is not None, 'negotiation_not_found', 404)
            rule = db.execute('SELECT * FROM hh_send_rules WHERE id=? AND connection_id=? AND vacancy_id=?',
                              (rule_id, connection_id, item['vacancy_id'])).fetchone()
            require(rule is not None, 'sending_rule_required')
            require(item['chat_id'] is not None, 'chat_unavailable')
            chat_id = identifier(item['chat_id'])
            message = rule['template'].replace('{invite_url}', url)
            require(len(message) <= 20000, 'message_too_long')
            old = db.execute('SELECT * FROM hh_outbox WHERE connection_id=? AND negotiation_id=? AND event_key=?',
                             (connection_id, negotiation_id, event_key)).fetchone()
            if old:
                require(all(old[k] == value for k, value in {'rule_id': rule_id, 'invitation_id': invitation_id,
                            'invitation_url': url, 'text': message, 'chat_id': chat_id}.items()), 'event_payload_conflict', 409)
                return self.outbox_dto(old)
            outbox_id = 'hho_' + secrets.token_hex(12)
            db.execute('''INSERT INTO hh_outbox(id,connection_id,negotiation_id,candidate_id,rule_id,event_key,
              invitation_id,invitation_url,text,chat_id,message_uuid,status,next_attempt,created,updated)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,'queued',?,?,?)''',
                       (outbox_id, connection_id, negotiation_id, item['candidate_id'], rule_id, event_key,
                        invitation_id, url, message, chat_id, str(uuid.uuid4()), self.clock(), self.clock(), self.clock()))
            return self.outbox_dto(db.execute('SELECT * FROM hh_outbox WHERE id=?', (outbox_id,)).fetchone())

    @staticmethod
    def outbox_dto(row):
        return {k: row[k] for k in ('id', 'status', 'attempts', 'next_attempt', 'provider_message_id', 'last_error')}

    def outbox_item(self, actor_id, outbox_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM hh_outbox WHERE id=?', (outbox_id,)).fetchone()
            require(row is not None, 'outbox_not_found', 404)
            self.owned(db, actor_id, row['connection_id'])
            return self.outbox_dto(row)

    def finish_send(self, outbox_id, claim, status, error=None, provider_id=None):
        with self.db(write=True) as db:
            row = db.execute('SELECT attempts FROM hh_outbox WHERE id=? AND claim_token=?', (outbox_id, claim)).fetchone()
            if row:
                if status in ('retry', 'unknown') and row[0] >= MAX_ATTEMPTS:
                    status = 'needs_review'
                db.execute('''UPDATE hh_outbox SET status=?,last_error=?,provider_message_id=?,next_attempt=?,
                  lease_until=0,claim_token=NULL,updated=? WHERE id=? AND claim_token=?''',
                           (status, error, provider_id, self.clock() + min(3600, 30 * 2 ** row[0]), self.clock(), outbox_id, claim))

    def send_once(self, actor_id, outbox_id, *, is_current):
        """One explicitly configured attempt; never creates its own invitation/audience.

        is_current(db, invitation_id, candidate_id) must check the live invitation,
        consent/decline/meeting state and the caller-approved audience policy.
        This callback must be read-only and must not commit the supplied transaction.
        """
        self.configured()
        require(self.config.sending_enabled, 'sending_disabled', 503)
        require(callable(is_current), 'invitation_guard_required')
        claim = secrets.token_hex(16)
        with self.db(write=True) as db:
            row = db.execute('SELECT * FROM hh_outbox WHERE id=?', (outbox_id,)).fetchone()
            require(row is not None, 'outbox_not_found', 404)
            item = dict(row)
            self.owned(db, actor_id, item['connection_id'])
            rule = db.execute('SELECT enabled FROM hh_send_rules WHERE id=?', (item['rule_id'],)).fetchone()
            require(rule and rule[0], 'sending_rule_disabled', 503)
            if item['status'] == 'sending' and item['lease_until'] > self.clock():
                return self.outbox_dto(item)
            if item['status'] not in ('queued', 'retry', 'unknown', 'sending') or item['next_attempt'] > self.clock():
                return self.outbox_dto(item)
            if item['attempts'] >= MAX_ATTEMPTS:
                db.execute("UPDATE hh_outbox SET status='needs_review',last_error='attempt_limit' WHERE id=?", (outbox_id,))
                return self.outbox_dto(db.execute('SELECT * FROM hh_outbox WHERE id=?', (outbox_id,)).fetchone())
            if not is_current(db, item['invitation_id'], item['candidate_id']):
                db.execute("UPDATE hh_outbox SET status='cancelled',last_error='invitation_no_longer_current' WHERE id=?", (outbox_id,))
                return self.outbox_dto(db.execute('SELECT * FROM hh_outbox WHERE id=?', (outbox_id,)).fetchone())
            db.execute("UPDATE hh_outbox SET status='sending',attempts=attempts+1,claim_token=?,lease_until=?,updated=? WHERE id=?",
                       (claim, self.clock() + 120, self.clock(), outbox_id))
        posting = False
        try:
            detail = self.get(actor_id, item['connection_id'], '/negotiations/' + identifier(item['negotiation_id']))
            with self.db() as db:
                current = db.execute('''SELECT n.vacancy_id,v.active FROM hh_negotiations n JOIN hh_vacancies v
                  ON v.connection_id=n.connection_id AND v.external_id=n.vacancy_id
                  WHERE n.connection_id=? AND n.external_id=?''',
                                     (item['connection_id'], item['negotiation_id'])).fetchone()
                valid = current and current['active'] and str(detail.get('id')) == item['negotiation_id']
                valid = valid and str((detail.get('vacancy') or {}).get('id')) == current['vacancy_id']
                valid = valid and str(detail.get('chat_id')) == item['chat_id'] and detail.get('messaging_status') == 'ok'
                rule = db.execute('SELECT enabled FROM hh_send_rules WHERE id=?', (item['rule_id'],)).fetchone()
                valid = valid and rule and rule[0] and is_current(db, item['invitation_id'], item['candidate_id'])
            if not valid:
                self.finish_send(outbox_id, claim, 'cancelled', 'invitation_or_chat_changed')
            else:
                posting = True
                response = self.request(actor_id, item['connection_id'], 'POST',
                                        '/common/chats/' + identifier(item['chat_id']) + '/messages',
                                        json_body={'text': item['text'], 'idempotency_key': item['message_uuid'], 'is_automated': True})
                if response.status == 201 and isinstance(response.body, dict) and response.body.get('id') is not None:
                    self.finish_send(outbox_id, claim, 'sent', provider_id=identifier(response.body['id']))
                elif response.status == 409:
                    self.finish_send(outbox_id, claim, 'needs_review', 'idempotency_conflict')
                elif response.status in (401, 403, 404):
                    self.finish_send(outbox_id, claim, 'blocked', 'provider_access_denied')
                elif response.status == 429 or response.status >= 500:
                    self.finish_send(outbox_id, claim, 'unknown' if response.status >= 500 else 'retry', 'provider_temporary_error')
                elif response.status == 201:
                    self.finish_send(outbox_id, claim, 'unknown', 'invalid_success_response')
                else:
                    self.finish_send(outbox_id, claim, 'failed', 'provider_rejected_message')
        except TransportUnavailable:
            self.finish_send(outbox_id, claim, 'unknown' if posting else 'retry', 'provider_unavailable')
        except HHError as error:
            self.finish_send(outbox_id, claim, 'blocked' if error.status in (401, 403) else 'needs_review', error.code)
        return self.outbox_item(actor_id, outbox_id)
