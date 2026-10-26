"""Versioned explicit questionnaire facts. No text inference or provider writes."""
import json
import re
import time
from domain import require, Problem
from assessment_core_bridge import digest, dump

CANDIDATE = {'work_locations': None, 'employment_types': None, 'industry_months': None}
VACANCY = {'location': {'required': None, 'allowed_ids': None},
           'employment': {'required': None, 'allowed_ids': None},
           'industry_experience': {'required': None, 'industry_id': None, 'minimum_months': None}}


OPTIONS = {
    'locations': [{'id': key, 'label': label} for key, label in (
        ('ru:moscow', 'Москва'), ('ru:moscow-oblast', 'Московская область'),
        ('ru:saint-petersburg', 'Санкт-Петербург'), ('kz:almaty', 'Алматы'),
        ('kz:astana', 'Астана'), ('work-mode:remote', 'Удалённая работа'))],
    'employmentTypes': [{'id': key, 'label': label} for key, label in (
        ('full-time', 'Полная занятость'), ('part-time', 'Частичная занятость'),
        ('project', 'Проектная работа'), ('internship', 'Стажировка'))],
    'industries': [{'id': key, 'label': label} for key, label in (
        ('logistics', 'Логистика'), ('it', 'Информационные технологии'), ('finance', 'Финансы'),
        ('manufacturing', 'Производство'), ('retail', 'Розничная торговля'),
        ('professional-services', 'Профессиональные услуги'))]}


def allowed(value, catalogue):
    return ids(value) and (value is None or set(value) <= {i['id'] for i in OPTIONS[catalogue]})


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS sl_fact_revisions (
        id TEXT PRIMARY KEY,kind TEXT NOT NULL,resource_id TEXT NOT NULL,parent_revision TEXT NOT NULL,
        source_revision TEXT NOT NULL,body TEXT NOT NULL,author TEXT NOT NULL,created REAL NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS sl_fact_resource ON sl_fact_revisions(kind,resource_id,created DESC)')


def ids(value):
    return value is None or (isinstance(value, list) and len(value) <= 100 and
        all(isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}', v) for v in value) and len(set(value)) == len(value))


def validate(kind, fields):
    expected = CANDIDATE if kind == 'candidate' else VACANCY
    require(isinstance(fields, dict) and set(fields) == set(expected), 'Проверьте обязательные поля.', 400)
    if kind == 'candidate':
        months = fields['industry_months']
        require(allowed(fields['work_locations'], 'locations') and allowed(fields['employment_types'], 'employmentTypes') and
            (months is None or isinstance(months, dict) and len(months) <= 100 and allowed(list(months), 'industries') and
             all(type(v) is int and 0 <= v <= 1200 for v in months.values())), 'Нужны ID и месяцы из анкеты.', 400)
    else:
        for key in ('location', 'employment', 'industry_experience'):
            value = fields[key]
            require(isinstance(value, dict) and set(value) == set(expected[key]) and
                    (value['required'] is None or type(value['required']) is bool), 'Укажите обязательность явно.', 400)
            if key == 'industry_experience':
                require((value['industry_id'] is None or allowed([value['industry_id']], 'industries')) and
                    (value['minimum_months'] is None or type(value['minimum_months']) is int and 0 <= value['minimum_months'] <= 1200),
                    'Проверьте отрасль и минимальный стаж в месяцах.', 400)
            else:
                require(allowed(value['allowed_ids'], 'locations' if key == 'location' else 'employmentTypes'), 'Проверьте канонические ID.', 400)
    return fields


# Only these explicit form inputs may feed the fact adapter. Parser/default
# values deliberately have no confirmation marker.
PROFILE_SOURCE_KEY = '_selectionFactSources'
PROFILE_INPUTS = set(CANDIDATE) | {'employmentType', 'geographies', 'workMode'}


def confirm_profile(profile, submitted):
    """Called only after a successful authenticated questionnaire save."""
    sources = dict(profile.get(PROFILE_SOURCE_KEY, {}))
    sequence = max((item.get('sequence', 0) for item in sources.values() if isinstance(item, dict)), default=0) + 1
    for key in PROFILE_INPUTS & set(submitted):
        sources[key] = {'kind': 'candidate_questionnaire', 'sourceField': key,
                        'sourceRevision': digest([key, profile.get(key)]), 'sequence': sequence}
    if sources:
        profile[PROFILE_SOURCE_KEY] = sources
    return profile


def _empty(kind):
    return json.loads(dump(CANDIDATE if kind == 'candidate' else VACANCY))


def _paths(kind):
    return list(CANDIDATE) if kind == 'candidate' else [key + '.' + child for key, value in VACANCY.items() for child in value]


def _get(fields, path):
    if '.' not in path:
        return fields[path]
    key, child = path.split('.')
    return fields[key][child]


def _set(fields, path, value):
    if '.' not in path:
        fields[path] = value
    else:
        key, child = path.split('.')
        fields[key][child] = value


def valid_field(kind, path, value):
    fields = _empty(kind)
    _set(fields, path, value)
    try:
        validate(kind, fields)
        return True
    except (Problem, TypeError, ValueError):
        return False


def _city(value):
    if not isinstance(value, str):
        return None
    normalized = ' '.join(value.casefold().replace('ё', 'е').split())
    for option in OPTIONS['locations']:
        if option['id'] == value or option['label'].casefold().replace('ё', 'е') == normalized:
            return option['id']
    return None


def _candidate_sources(profile):
    markers = profile.get(PROFILE_SOURCE_KEY, {})
    markers = markers if isinstance(markers, dict) else {}

    def confirmed(key):
        marker = markers.get(key)
        return isinstance(marker, dict) and marker.get('kind') == 'candidate_questionnaire' and marker.get('sourceRevision') == digest([key, profile.get(key)])

    def order(key):
        marker = markers.get(key)
        return marker.get('sequence', -1) if isinstance(marker, dict) and type(marker.get('sequence')) is int else -1

    result = {}
    for key in CANDIDATE:
        # Neither canonical names nor old camelCase fields prove confirmation.
        # Historical forms stored preferences and parser output in the same row.
        present = key in profile
        value = profile.get(key)
        kind = 'candidate_questionnaire' if confirmed(key) else 'unconfirmed_legacy_source'
        if not confirmed(key):
            value, kind = None, 'unconfirmed_source_change' if key in markers else 'unconfirmed_legacy_source'
        inputs, sequence = {key: profile.get(key)} if present else {}, order(key)
        if key == 'employment_types' and confirmed('employmentType') and order('employmentType') > sequence:
            raw = profile.get('employmentType')
            value = [raw] if raw else None
            inputs, kind = {'employmentType': raw}, 'candidate_questionnaire'
        elif key == 'work_locations':
            geo, mode = confirmed('geographies'), confirmed('workMode')
            if (geo or mode) and max(order('geographies') if geo else -1, order('workMode') if mode else -1) > sequence:
                values = [_city(city) for city in profile.get('geographies', [])] if geo else []
                if mode and profile.get('workMode') == 'remote':
                    values.append('work-mode:remote')
                value = list(dict.fromkeys(values)) if values and None not in values else None
                inputs = {name: profile.get(name) for name, yes in (('geographies', geo), ('workMode', mode)) if yes}
                kind = 'candidate_questionnaire'
        result[key] = (value, kind if inputs else 'missing', inputs)
    return result


def _source(db, kind, resource_id):
    import selection
    fields, sources = _empty(kind), {}
    if kind == 'candidate':
        selection.person(db, resource_id)
        profile = json.loads(db.execute('SELECT profile FROM cp_candidates WHERE id=?', (resource_id,)).fetchone()[0])
        raw = _candidate_sources(profile)
        source = digest([profile.get(key) for key in sorted(PROFILE_INPUTS)] + [profile.get(PROFILE_SOURCE_KEY)])
    else:
        source = selection.vacancy(db, resource_id)['contentHash']
        body = json.loads(db.execute('SELECT body FROM cp_vacancies WHERE id=?', (resource_id,)).fetchone()[0])
        explicit_key = 'required_fields' if 'required_fields' in body else 'requiredFields'
        explicit = body.get(explicit_key)
        raw = {}
        details = body.get('details') if isinstance(body.get('details'), dict) else {}
        for path in _paths(kind):
            key, child = path.split('.')
            item = explicit.get(key) if isinstance(explicit, dict) else None
            if isinstance(item, dict) and child in item:
                raw[path] = (item[child], 'vacancy_questionnaire', {explicit_key + '.' + path: item[child]})
            elif path == 'location.allowed_ids':
                city = _city(body.get('city'))
                raw[path] = ([city] if city else None, 'vacancy_structured', {'city': body.get('city')})
            elif path == 'employment.allowed_ids':
                employment = details.get('employmentType', body.get('employmentType'))
                employment_key = 'details.employmentType' if 'employmentType' in details else 'employmentType'
                raw[path] = ([employment] if employment else None, 'vacancy_structured', {employment_key: employment})
            else:
                raw[path] = (None, 'missing', {})
    for path in _paths(kind):
        value, origin, inputs = raw[path]
        valid = valid_field(kind, path, value)
        # An empty industry map confirms no industry's duration, unlike {id: 0}.
        if path == 'industry_months' and value == {}:
            value = None
        _set(fields, path, value if valid else None)
        sources[path] = {'kind': origin, 'sourceRevision': digest([origin, inputs]),
                         'sourceFields': list(inputs), 'state': 'known' if valid and value is not None else 'unknown' if valid else 'invalid'}
    return source, fields, sources


def _overrides(db, kind, resource_id, source, fields, sources):
    row = db.execute('SELECT id,body,source_revision,author FROM sl_fact_revisions WHERE kind=? AND resource_id=? ORDER BY created DESC,rowid DESC LIMIT 1',
                     (kind, resource_id)).fetchone()
    if not row:
        return None, {}
    saved = json.loads(row[1])
    if saved.get('format') == 'field-overrides-v1':
        return row[0], saved['overrides']
    # Legacy rows did not save a changed-field mask or old source values. Do not
    # silently apply their full snapshots over a newly changed questionnaire.
    overrides = {}
    for path in _paths(kind):
        value = _get(saved, path)
        if value != _get(fields, path) and value is not None:
            overrides[path] = {'value': value, 'sourceRevision': sources[path]['sourceRevision'] if row[2] == source else None,
                               'author': row[3], 'legacy': True}
    return row[0], overrides


def read(db, kind, resource_id):
    require(kind in ('candidate', 'vacancy'), 'Проверьте тип анкеты.', 400)
    source, fields, sources = _source(db, kind, resource_id)
    row_id, overrides = _overrides(db, kind, resource_id, source, fields, sources)
    conflicts = {}
    for path, override in overrides.items():
        if path not in sources:
            continue
        origin = sources[path]
        if override['sourceRevision'] != origin['sourceRevision']:
            conflicts[path] = {'manualValue': override['value'], 'sourceValue': _get(fields, path),
                               'manualSourceRevision': override['sourceRevision'], 'sourceRevision': origin['sourceRevision']}
            _set(fields, path, None)
            sources[path] = {**origin, 'kind': 'conflict', 'state': 'conflict'}
        else:
            _set(fields, path, override['value'])
            sources[path] = {**origin, 'kind': 'manual', 'state': 'known' if override['value'] is not None else 'unknown',
                             'author': override['author'], 'manualRevision': row_id}
    revision = digest([kind, resource_id, source, row_id, fields, sources, conflicts])
    return {('candidateId' if kind == 'candidate' else 'vacancyId'): resource_id,
            'revision': revision, 'sourceRevision': source, 'fields': fields, 'options': OPTIONS,
            'fieldSources': sources, 'conflicts': conflicts}


def update(db, kind, resource_id, body, author='manager'):
    require(isinstance(body, dict) and set(body) == {'revision', 'fields'}, 'Проверьте версию полей.', 400)
    fields = validate(kind, body['fields'])
    current = read(db, kind, resource_id)
    require(body['revision'] == current['revision'], 'Поля изменились. Обновите карточку.', 409)
    source, original, sources = _source(db, kind, resource_id)
    _, overrides = _overrides(db, kind, resource_id, source, original, sources)
    for path in _paths(kind):
        value = _get(fields, path)
        if path == 'industry_months' and value == {}:
            value = None
        if value == _get(current['fields'], path):
            continue
        if value == _get(original, path):
            overrides.pop(path, None)
        else:
            overrides[path] = {'value': value, 'sourceRevision': sources[path]['sourceRevision'], 'author': author}
    if not any(_get(fields, path) != _get(current['fields'], path) for path in _paths(kind)):
        return current
    saved = {'format': 'field-overrides-v1', 'overrides': overrides}
    identity = digest([kind, resource_id, current['revision'], saved, author])
    db.execute('INSERT OR IGNORE INTO sl_fact_revisions VALUES (?,?,?,?,?,?,?,?)',
               (identity, kind, resource_id, current['revision'], current['sourceRevision'], dump(saved), author, time.time()))
    return read(db, kind, resource_id)


def contract(db, kind, resource_id):
    value = read(db, kind, resource_id)
    return {**value['fields'], 'revision': value['revision'],
            **({'candidate_id': resource_id} if kind == 'candidate' else {})}
