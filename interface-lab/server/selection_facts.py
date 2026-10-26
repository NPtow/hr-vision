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


def read(db, kind, resource_id):
    import selection
    if kind == 'candidate':
        selection.person(db, resource_id)
        # Only exactly named structured questionnaire fields qualify. Never use
        # residence, CV total experience, role or extracted model assertions.
        profile = json.loads(db.execute('SELECT profile FROM cp_candidates WHERE id=?', (resource_id,)).fetchone()[0])
        fields = {k: profile.get(k) for k in CANDIDATE}
        source = digest(fields)
        try:
            validate(kind, fields)
        except Problem:
            fields = dict(CANDIDATE)
    else:
        source = selection.vacancy(db, resource_id)['contentHash']
        fields = json.loads(dump(VACANCY))
        body = json.loads(db.execute('SELECT body FROM cp_vacancies WHERE id=?', (resource_id,)).fetchone()[0])
        explicit = body.get('required_fields', body.get('requiredFields'))
        if explicit is not None:
            try:
                fields = validate(kind, explicit)
            except Problem:
                pass
    row = db.execute('SELECT id,body,source_revision FROM sl_fact_revisions WHERE kind=? AND resource_id=? ORDER BY created DESC,rowid DESC LIMIT 1',
                     (kind, resource_id)).fetchone()
    if row:
        fields = json.loads(row[1])
    revision = digest([kind, resource_id, source, row[0] if row else None, fields])
    return {('candidateId' if kind == 'candidate' else 'vacancyId'): resource_id,
            'revision': revision, 'sourceRevision': source, 'fields': fields, 'options': OPTIONS}


def update(db, kind, resource_id, body, author='manager'):
    require(isinstance(body, dict) and set(body) == {'revision', 'fields'}, 'Проверьте версию полей.', 400)
    fields = validate(kind, body['fields'])
    current = read(db, kind, resource_id)
    require(body['revision'] == current['revision'], 'Поля изменились. Обновите карточку.', 409)
    identity = digest([kind, resource_id, current['revision'], fields, author])
    db.execute('INSERT OR IGNORE INTO sl_fact_revisions VALUES (?,?,?,?,?,?,?,?)',
               (identity, kind, resource_id, current['revision'], current['sourceRevision'], dump(fields), author, time.time()))
    return read(db, kind, resource_id)


def contract(db, kind, resource_id):
    value = read(db, kind, resource_id)
    return {**value['fields'], 'revision': value['revision'],
            **({'candidate_id': resource_id} if kind == 'candidate' else {})}
