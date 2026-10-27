"""Provenance projection against temporary SQLite; no HH network requests."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from hh_recruiting import HHConfig, HHRecruiting, Response
from recruiter_sources import candidate_sources


class RecruiterSourcesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'sources.sqlite3'
        self.db = sqlite3.connect(self.path)
        self.db.execute('''CREATE TABLE cp_intake (
          candidate_id TEXT PRIMARY KEY,source TEXT,external_key TEXT,
          contact_email TEXT,created_at TEXT)''')

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def hh(self):
        HHRecruiting.init(self.db)

    def intake(self, candidate='candidate1', source='hh'):
        self.db.execute('INSERT INTO cp_intake VALUES (?,?,?,?,?)',
                        (candidate, source, 'private-external-key', 'private@example.test', '2026-10-13'))

    def response(self, *, candidate='candidate1', connection='connection1',
                 response='response1', vacancy='123', body=None):
        self.db.execute('''INSERT OR IGNORE INTO hh_connections
          (id,actor_id,employer_id,manager_id,access_token,refresh_token,expires,status,updated)
          VALUES (?, 'recruiter1', ?, 'manager1', 'private-access', 'private-refresh', 999999, 'connected', 1)''',
                        (connection, connection))
        resume_id = candidate + '-resume'
        self.db.execute('INSERT OR IGNORE INTO hh_resumes VALUES (?,?,?,?,?,?)',
                        (connection, resume_id, candidate,
                         json.dumps({'desiredRole': 'NOT A SOURCE VACANCY'}),
                         json.dumps({'resumeUrl': 'https://api.hh.ru/resumes/private?token=private'}), 1))
        self.db.execute('INSERT OR IGNORE INTO hh_vacancies VALUES (?,?,?,?,?,?)',
                        (connection, vacancy, 'post-interview-vacancy-must-not-be-used',
                         json.dumps(body or {'title': 'Sales', 'company': 'Source Co',
                                             'url': 'https://hh.ru/vacancy/' + vacancy}), 1, 1))
        self.db.execute('INSERT INTO hh_negotiations VALUES (?,?,?,?,?,?,?,?)',
                        (connection, response, vacancy, resume_id, 'private-chat-id', 'response', 'available', 1))

    def browser(self):
        # Exact columns used from the stable HHOutreach schema. Extra private
        # columns ensure the public projection never copies an entire row.
        self.db.execute('CREATE TABLE ho_campaigns (id TEXT PRIMARY KEY,mode TEXT,name TEXT)')
        self.db.execute('''CREATE TABLE ho_recipients (
          id TEXT PRIMARY KEY,campaign_id TEXT,candidate_id TEXT,vacancy_id TEXT,
          response_id TEXT,created REAL,chat_url TEXT,resume_url TEXT)''')

    def browser_recipient(self, *, candidate='candidate1', vacancy='123', campaign='campaign1',
                          recipient='recipient1', mode='applicants', response='private-response'):
        self.db.execute('INSERT OR IGNORE INTO ho_campaigns VALUES (?,?,?)',
                        (campaign, mode, 'NOT THE VACANCY TITLE'))
        self.db.execute('INSERT INTO ho_recipients VALUES (?,?,?,?,?,?,?,?)',
                        (recipient, campaign, candidate, vacancy, response, 1,
                         'https://hh.ru/chat/private?booking=secret',
                         'https://hh.ru/resume/private?token=secret'))

    def test_registration_without_hh_tables(self):
        self.assertEqual({'source': {'kind': 'registration', 'label': 'Регистрация'},
                          'sourceApplications': []}, candidate_sources(self.db, 'candidate1'))

    def test_manual_cv_does_not_invent_vacancy(self):
        self.intake(source='manual')
        self.db.execute('CREATE TABLE cp_applications (candidate_id TEXT,vacancy_id TEXT)')
        self.db.execute("INSERT INTO cp_applications VALUES ('candidate1','other-vacancy')")
        self.assertEqual({'source': {'kind': 'manual', 'label': 'Добавлен из CV'},
                          'sourceApplications': []}, candidate_sources(self.db, 'candidate1'))

    def test_hh_intake_is_known_even_before_responses_exist(self):
        self.intake()
        self.assertEqual({'source': {'kind': 'hh', 'label': 'HH'},
                          'sourceApplications': []}, candidate_sources(self.db, 'candidate1'))

    def test_multiple_origins_are_preserved_without_other_candidates_or_credentials(self):
        self.hh()
        self.intake()
        self.response()
        self.response(connection='connection2', response='response1', vacancy='456',
                      body={'title': 'Developer', 'company': 'Another Co', 'url': 'https://spb.hh.ru/vacancy/456'})
        self.response(candidate='other-person', response='response3', vacancy='789',
                      body={'title': 'Private other role', 'company': 'Private other company'})
        result = candidate_sources(self.db, 'candidate1')
        self.assertEqual({'kind': 'hh', 'label': 'HH'}, result['source'])
        self.assertEqual(['Sales', 'Developer'], [x['title'] for x in result['sourceApplications']])
        self.assertEqual(['Source Co', 'Another Co'], [x['company'] for x in result['sourceApplications']])
        ids = [x['id'] for x in result['sourceApplications']]
        self.assertEqual(2, len(set(ids)))
        self.assertEqual(result, candidate_sources(self.db, 'candidate1'))
        self.assertNotIn('private', json.dumps(result).lower())
        self.assertNotIn('post-interview', json.dumps(result))
        self.assertNotIn('connection', json.dumps(result))

    def test_old_record_without_company_remains_unknown(self):
        self.hh()
        self.intake()
        self.response(body={'title': 'Historical vacancy', 'city': 'Moscow'})
        item = candidate_sources(self.db, 'candidate1')['sourceApplications'][0]
        self.assertEqual('', item['company'])
        self.assertEqual('', item['url'])
        self.assertEqual('Historical vacancy', item['title'])

    def test_hh_origin_with_absent_intake_and_sqlite_row_factory(self):
        self.hh()
        self.response()
        self.db.row_factory = sqlite3.Row
        self.assertEqual({'kind': 'hh', 'label': 'HH'}, candidate_sources(self.db, 'candidate1')['source'])

    def test_public_url_strips_secret_queries_and_rejects_other_hosts_or_paths(self):
        self.hh()
        self.intake()
        self.response(body={'title': 'Sales', 'url': 'https://hh.ru/vacancy/123?token=secret#secret'})
        self.assertEqual('https://hh.ru/vacancy/123', candidate_sources(self.db, 'candidate1')['sourceApplications'][0]['url'])
        for url in ('https://evil.example/vacancy/123', 'https://hh.ru.evil.example/vacancy/123',
                    'https://token@hh.ru/vacancy/123', 'https://api.hh.ru/resumes/123',
                    'javascript:alert(1)', 'https://hh.ru:invalid/vacancy/123'):
            with self.subTest(url=url):
                self.db.execute('UPDATE hh_vacancies SET body=?', (json.dumps({'title': 'Sales', 'url': url}),))
                self.assertEqual('', candidate_sources(self.db, 'candidate1')['sourceApplications'][0]['url'])

    def test_projection_does_not_write_or_expose_raw_malformed_body(self):
        self.hh()
        self.response()
        for raw in ('["private-access"]', '{invalid private-access'):
            self.db.execute('UPDATE hh_vacancies SET body=?', (raw,))
            before = self.db.total_changes
            result = candidate_sources(self.db, 'candidate1')
            self.assertEqual(before, self.db.total_changes)
            item = result['sourceApplications'][0]
            self.assertEqual(('', '', ''), (item['title'], item['company'], item['url']))

    def test_hh_vacancy_sync_preserves_provider_company_only(self):
        self.hh()
        self.response()
        self.db.commit()
        calls = []

        def transport(method, url, **kwargs):
            calls.append((method, url))
            return Response(200, {'page': 0, 'pages': 1, 'items': [
                {'id': '123', 'name': 'Sales', 'employer': {'name': 'Actual HH Company'},
                 'alternate_url': 'https://hh.ru/vacancy/123'},
                {'id': '456', 'name': 'Another role'},
            ]})

        config = HHConfig(enabled=True, client_id='synthetic', client_secret='synthetic',
                          user_agent='HR-Test/1 test@example.test', redirect_uri='https://example.test/callback')
        adapter = HHRecruiting(lambda: sqlite3.connect(self.path), config, transport=transport, clock=lambda: 1)
        result = adapter.sync_vacancies('recruiter1', 'connection1')
        self.assertEqual(['Actual HH Company', ''], [v['company'] for v in result])
        self.assertEqual(1, len(calls))
        self.assertEqual('Actual HH Company', candidate_sources(self.db, 'candidate1')['sourceApplications'][0]['company'])

    def test_browser_applicants_project_known_vacancy_without_invented_metadata(self):
        self.browser()
        self.intake()
        self.browser_recipient()
        before = self.db.total_changes
        result = candidate_sources(self.db, 'candidate1')
        self.assertEqual(before, self.db.total_changes)
        self.assertEqual({'kind': 'hh', 'label': 'HH'}, result['source'])
        self.assertEqual(1, len(result['sourceApplications']))
        item = result['sourceApplications'][0]
        self.assertEqual({'id', 'title', 'company', 'url'}, set(item))
        self.assertEqual(('Вакансия HH №123', '', 'https://hh.ru/vacancy/123'),
                         (item['title'], item['company'], item['url']))
        for private in ('private', 'secret', 'NOT THE VACANCY TITLE', 'campaign1', 'recipient1'):
            self.assertNotIn(private, json.dumps(result, ensure_ascii=False))

    def test_browser_same_vacancy_dedupes_across_campaigns_and_has_stable_public_id(self):
        self.browser()
        self.browser_recipient()
        initial = candidate_sources(self.db, 'candidate1')['sourceApplications']
        self.browser_recipient(campaign='campaign2', recipient='recipient2')
        self.assertEqual(initial, candidate_sources(self.db, 'candidate1')['sourceApplications'])
        self.db.execute("DELETE FROM ho_recipients WHERE id='recipient1'")
        self.assertEqual(initial, candidate_sources(self.db, 'candidate1')['sourceApplications'])

    def test_browser_projection_is_scoped_to_candidate_and_supports_row_factory(self):
        self.browser()
        self.browser_recipient()
        self.browser_recipient(candidate='foreign-candidate', vacancy='999', recipient='foreign-recipient')
        self.db.row_factory = sqlite3.Row
        result = candidate_sources(self.db, 'candidate1')
        self.assertEqual(['Вакансия HH №123'], [item['title'] for item in result['sourceApplications']])
        self.assertNotIn('999', json.dumps(result))
        self.assertEqual({'kind': 'hh', 'label': 'HH'}, result['source'])

    def test_browser_direct_without_vacancy_does_not_invent_application(self):
        self.browser()
        self.browser_recipient(mode='direct', vacancy=None, response=None)
        self.assertEqual({'source': {'kind': 'hh', 'label': 'HH'}, 'sourceApplications': []},
                         candidate_sources(self.db, 'candidate1'))

    def test_browser_invalid_vacancy_ids_never_become_links_or_labels(self):
        self.browser()
        for index, invalid in enumerate(('', 'private-id', '123/../../chat', 'https://hh.ru/vacancy/123',
                                         '123?token=secret', '１２３', '1' * 33)):
            self.browser_recipient(vacancy=invalid, recipient='recipient' + str(index))
        self.assertEqual([], candidate_sources(self.db, 'candidate1')['sourceApplications'])

    def test_same_vacancy_across_api_and_browser_preserves_known_title_and_company(self):
        self.hh()
        self.browser()
        self.response()
        self.response(connection='connection2', response='second-response')
        self.browser_recipient()
        result = candidate_sources(self.db, 'candidate1')['sourceApplications']
        self.assertEqual(1, len(result))
        self.assertEqual(('Sales', 'Source Co', 'https://hh.ru/vacancy/123'),
                         (result[0]['title'], result[0]['company'], result[0]['url']))

    def test_browser_fills_only_missing_public_metadata_for_same_api_vacancy(self):
        self.hh()
        self.browser()
        self.response(body={'company': 'Actual Company'})
        self.browser_recipient()
        result = candidate_sources(self.db, 'candidate1')['sourceApplications']
        self.assertEqual(1, len(result))
        self.assertEqual(('Вакансия HH №123', 'Actual Company', 'https://hh.ru/vacancy/123'),
                         (result[0]['title'], result[0]['company'], result[0]['url']))

    def test_public_vacancy_id_stays_stable_when_api_metadata_arrives_later(self):
        self.browser()
        self.browser_recipient()
        first = candidate_sources(self.db, 'candidate1')['sourceApplications'][0]['id']
        self.hh()
        self.response()
        enriched = candidate_sources(self.db, 'candidate1')['sourceApplications']
        self.assertEqual(1, len(enriched))
        self.assertEqual(first, enriched[0]['id'])
        self.assertEqual('Sales', enriched[0]['title'])


if __name__ == '__main__':
    unittest.main()
