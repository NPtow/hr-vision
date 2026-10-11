"""HH contract tests use synthetic fixtures only; no network, tokens or real people."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from hh_recruiting import (API, HHConfig, HHError, HHRecruiting, Response,
                           TransportUnavailable, normalize_resume, safe_path)


class Provider:
    def __init__(self):
        self.expected, self.calls = [], []

    def add(self, method, path, body=None, status=200, verify=None):
        self.expected.append((method, path, Response(status, body), verify))

    def unavailable(self, method, path):
        self.expected.append((method, path, TransportUnavailable('synthetic timeout'), None))

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.expected:
            raise AssertionError('Unexpected provider call: ' + method + ' ' + url)
        expected_method, expected_path, result, verify = self.expected.pop(0)
        assert (method, url) == (expected_method, API + expected_path), (method, url, expected_path)
        if verify:
            verify(kwargs)
        if isinstance(result, Exception):
            raise result
        return result


TOKEN = {'access_token': 'synthetic-access', 'refresh_token': 'synthetic-refresh', 'expires_in': 3600, 'token_type': 'bearer'}
ME = {'auth_type': 'employer', 'employer': {'id': '10'}, 'manager': {'id': '20'}}
CV = {'id': 'resume1', 'first_name': 'Анна', 'last_name': 'Тестовая', 'title': 'Менеджер',
      'area': {'name': 'Москва'}, 'employment': {'id': 'full'}, 'skills': 'Опыт работы с клиентами.',
      'can_view_full_info': True, 'contact': [{'type': {'id': 'email'}, 'value': 'synthetic@example.test'}]}


class HHRecruitingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'private.sqlite3')
        self.time = 100000.
        self.provider = Provider()
        self.config = HHConfig(enabled=True, sending_enabled=True, client_id='test-client', client_secret='test-secret',
                               redirect_uri='https://example.test/hh/callback', user_agent='HR-Test/1 test@example.test',
                               invite_origin='https://example.test')
        with self.connect() as db:
            db.execute('CREATE TABLE prepared_test (id TEXT PRIMARY KEY, external_key TEXT UNIQUE, profile TEXT)')
        self.module = HHRecruiting(self.connect, self.config, transport=self.provider,
                                   prepare_candidate=self.prepare, clock=lambda: self.time)

    def tearDown(self):
        self.assertEqual([], self.provider.expected, 'Unconsumed provider fixtures')
        self.temp.cleanup()

    def connect(self):
        return sqlite3.connect(self.path)

    def prepare(self, db, key, profile, source):
        self.assertNotIn('consentReuse', profile)
        self.assertNotIn('autoApprove', profile)
        cid = 'prepared_' + hashlib.sha256(key.encode()).hexdigest()[:24]
        db.execute('INSERT OR IGNORE INTO prepared_test VALUES (?,?,?)', (cid, key, json.dumps(profile)))
        return cid

    def oauth(self, actor='recruiter1'):
        url = self.module.begin_oauth(actor)
        self.assertTrue(url.startswith('https://hh.ru/oauth/authorize?'))
        state = parse_qs(urlsplit(url).query)['state'][0]
        self.provider.add('POST', '/token', TOKEN, verify=lambda call: self.assertEqual('authorization_code', call['form']['grant_type']))
        self.provider.add('GET', '/me', ME)
        connection = self.module.finish_oauth(actor, state, 'one-use-code')
        return connection['id']

    def vacancy(self, connection, external='30', local='vacancy1'):
        self.provider.add('GET', '/employers/10/vacancies/active?all_accessible=true&page=0&per_page=50',
                          {'items': [{'id': external, 'name': 'Менеджер', 'area': {'name': 'Москва'}}], 'page': 0, 'pages': 1})
        self.module.sync_vacancies('recruiter1', connection)
        self.module.link_vacancy('recruiter1', connection, external, local)

    def import_fixture(self, connection, records=None, cvs=None, vacancy='30'):
        records = records or [{'id': '40', 'resume': {'id': 'resume1'}, 'chat_id': 50, 'employer_state': {'id': 'response'}}]
        self.provider.add('GET', '/negotiations?vacancy_id=' + vacancy,
                          {'collections': [{'id': 'response', 'url': API + '/negotiations/response?vacancy_id=' + vacancy}]})
        self.provider.add('GET', '/negotiations/response?vacancy_id=' + vacancy + '&page=0&per_page=50',
                          {'items': records, 'pages': 1, 'page': 0})
        for rid, cv in (cvs or {'resume1': CV}).items():
            self.provider.add('GET', '/resumes/' + rid, cv)

    def ready(self, enabled=True):
        connection = self.oauth()
        self.vacancy(connection)
        self.import_fixture(connection)
        self.module.sync_vacancy('recruiter1', connection, '30', collection_ids=['response'])
        rule = self.module.configure_rule('recruiter1', connection, '30', event_type='call_ready', template_id='v1',
                                           template='Приглашение: {invite_url}', enabled=enabled)
        job = self.module.enqueue_invitation('recruiter1', connection, '40', rule_id=rule, event_key='call1',
                                              invitation_id='invite1', url='https://example.test/call?token=synthetic')
        return connection, rule, job

    def preflight(self, *, chat_id=50, vacancy='30', messaging='ok'):
        self.provider.add('GET', '/negotiations/40', {'id': '40', 'vacancy': {'id': vacancy},
                                                     'chat_id': chat_id, 'messaging_status': messaging})

    def problem(self, code, fn):
        with self.assertRaises(HHError) as result:
            fn()
        self.assertEqual(code, result.exception.code)
        return result.exception

    def sql(self, query, parameters=()):
        with self.connect() as db:
            return db.execute(query, parameters).fetchall()

    def test_disabled_by_default_has_no_network(self):
        module = HHRecruiting(self.connect, transport=self.provider)
        self.problem('hh_disabled', lambda: module.begin_oauth('r1'))
        self.assertFalse(module.config.sending_enabled)
        self.assertEqual([], self.provider.calls)

    def test_oauth_state_is_hashed_single_use_and_actor_bound(self):
        url = self.module.begin_oauth('r1')
        state = parse_qs(urlsplit(url).query)['state'][0]
        self.assertNotIn(state, repr(self.sql('SELECT * FROM hh_oauth_states')))
        self.problem('oauth_invalid_state', lambda: self.module.finish_oauth('r2', state, 'code'))
        self.provider.add('POST', '/token', TOKEN)
        self.provider.add('GET', '/me', ME)
        connection = self.module.finish_oauth('r1', state, 'code')
        self.problem('oauth_invalid_state', lambda: self.module.finish_oauth('r1', state, 'code'))
        self.assertEqual('connected', connection['status'])
        self.assertNotIn('access', repr(connection))

    def test_expired_oauth_state_does_not_exchange(self):
        state = parse_qs(urlsplit(self.module.begin_oauth('r1')).query)['state'][0]
        self.time += 601
        self.problem('oauth_invalid_state', lambda: self.module.finish_oauth('r1', state, 'code'))
        self.assertEqual([], self.provider.calls)

    def test_reconnect_keeps_connection_identity(self):
        first = self.oauth()
        self.assertEqual(first, self.oauth())
        self.assertEqual(1, len(self.sql('SELECT id FROM hh_connections')))

    def test_candidate_authorization_is_not_employer_authorization(self):
        state = parse_qs(urlsplit(self.module.begin_oauth('r1')).query)['state'][0]
        self.provider.add('POST', '/token', TOKEN)
        self.provider.add('GET', '/me', {'auth_type': 'applicant'})
        self.problem('employer_authorization_required', lambda: self.module.finish_oauth('r1', state, 'code'))
        self.assertEqual([], self.sql('SELECT id FROM hh_connections'))

    def test_tokens_are_private_and_config_repr_does_not_leak_secrets(self):
        connection = self.oauth()
        self.assertNotIn('test-secret', repr(self.config))
        self.assertNotIn(TOKEN['access_token'], repr(self.module.connection('recruiter1', connection)))
        self.problem('connection_not_found', lambda: self.module.connection('other', connection))

    def test_documented_403_expired_token_refreshes_once(self):
        connection = self.oauth()
        self.provider.add('GET', '/test', {'errors': [{'type': 'oauth', 'value': 'token_expired'}]}, status=403)
        replacement = dict(TOKEN, access_token='replacement', refresh_token='replacement-refresh')
        self.provider.add('POST', '/token', replacement, verify=lambda call: self.assertEqual(
            {'grant_type': 'refresh_token', 'refresh_token': TOKEN['refresh_token']}, call['form']))
        self.provider.add('GET', '/test', {'ok': True}, verify=lambda call: self.assertEqual('Bearer replacement', call['headers']['Authorization']))
        self.assertEqual({'ok': True}, self.module.get('recruiter1', connection, '/test'))

    def test_refresh_lease_blocks_concurrent_refresh(self):
        connection = self.oauth()
        self.sql('UPDATE hh_connections SET expires=0,refresh_until=? WHERE id=?', (self.time + 10, connection))
        self.problem('refresh_in_progress', lambda: self.module.access_token('recruiter1', connection))

    def test_failed_refresh_requires_reauth_and_releases_lease(self):
        connection = self.oauth()
        self.sql('UPDATE hh_connections SET expires=0 WHERE id=?', (connection,))
        self.provider.add('POST', '/token', {'error': 'invalid_grant', 'details': 'sensitive'}, status=400)
        error = self.problem('reauth_required', lambda: self.module.access_token('recruiter1', connection))
        self.assertNotIn('sensitive', str(error))
        self.assertEqual('reauth_required', self.module.connection('recruiter1', connection)['status'])
        self.assertEqual(0, self.sql('SELECT refresh_until FROM hh_connections')[0][0])

    def test_revoked_token_does_not_refresh_or_retry(self):
        connection = self.oauth()
        self.provider.add('GET', '/test', {'errors': [{'value': 'token_revoked'}]}, status=403)
        self.problem('provider_access_denied', lambda: self.module.get('recruiter1', connection, '/test'))
        self.assertEqual('reauth_required', self.module.connection('recruiter1', connection)['status'])

    def test_vacancies_pagination_and_link_survive_refresh(self):
        connection = self.oauth()
        for page, vacancy in enumerate(('30', '31')):
            self.provider.add('GET', f'/employers/10/vacancies/active?all_accessible=true&page={page}&per_page=50',
                              {'items': [{'id': vacancy, 'name': 'Role'}], 'page': page, 'pages': 2})
        self.assertEqual(2, len(self.module.sync_vacancies('recruiter1', connection)))
        self.module.link_vacancy('recruiter1', connection, '30', 'local1')
        self.provider.add('GET', '/employers/10/vacancies/active?all_accessible=true&page=0&per_page=50',
                          {'items': [{'id': '30', 'name': 'Updated'}], 'page': 0, 'pages': 1})
        rows = self.module.sync_vacancies('recruiter1', connection)
        self.assertEqual(('local1', 'Updated', True), (rows[0]['localId'], rows[0]['title'], rows[0]['active']))
        self.assertFalse(rows[1]['active'])

    def test_failed_second_page_does_not_empty_current_vacancies(self):
        connection = self.oauth()
        self.vacancy(connection)
        self.provider.add('GET', '/employers/10/vacancies/active?all_accessible=true&page=0&per_page=50',
                          {'items': [], 'page': 0, 'pages': 2})
        self.provider.add('GET', '/employers/10/vacancies/active?all_accessible=true&page=1&per_page=50', {}, status=500)
        self.problem('provider_read_failed', lambda: self.module.sync_vacancies('recruiter1', connection))
        self.assertTrue(self.module.vacancies('recruiter1', connection)[0]['active'])

    def test_over_limit_pages_fail_instead_of_partial_success(self):
        connection = self.oauth()
        self.provider.add('GET', '/employers/10/vacancies/active?all_accessible=true&page=0&per_page=50',
                          {'items': [], 'page': 0, 'pages': 1000})
        self.problem('invalid_pagination', lambda: self.module.sync_vacancies('recruiter1', connection))

    def test_ssrf_collection_url_cannot_receive_credentials(self):
        connection = self.oauth()
        self.vacancy(connection)
        self.provider.add('GET', '/negotiations?vacancy_id=30', {'collections': [{'id': 'response', 'url': 'https://evil.test/negotiations/response'}]})
        self.problem('invalid_provider_url', lambda: self.module.sync_vacancy('recruiter1', connection, '30', collection_ids=['response']))
        for url in ['//evil.test/a', 'http://api.hh.ru/a', 'https://api.hh.ru@evil.test/a', '/a/../token', '/a/%2e%2e/token']:
            self.problem('invalid_provider_url', lambda value=url: safe_path(value))

    def test_sync_deduplicates_resumes_and_negotiations_without_email_identity(self):
        connection = self.oauth()
        self.vacancy(connection)
        records = [{'id': '40', 'resume': {'id': 'resume1'}, 'chat_id': 50},
                   {'id': '41', 'resume': {'id': 'resume1'}, 'chat_id': 50},
                   {'id': '42', 'resume': {'id': 'resume2'}, 'chat_id': 51}]
        for _ in range(2):
            self.import_fixture(connection, records, {'resume1': CV, 'resume2': dict(CV, id='resume2')})
            self.assertEqual({'negotiations': 3, 'resumes': 2}, self.module.sync_vacancy('recruiter1', connection, '30', collection_ids=['response']))
        self.assertEqual(2, len(self.sql('SELECT * FROM prepared_test')))
        self.assertEqual(3, len(self.sql('SELECT * FROM hh_negotiations')))

    def test_missing_cv_permission_preserves_partial_profile_explicitly(self):
        connection = self.oauth()
        self.vacancy(connection)
        self.provider.add('GET', '/negotiations?vacancy_id=30', {'collections': [{'id': 'response', 'url': '/negotiations/response?vacancy_id=30'}]})
        self.provider.add('GET', '/negotiations/response?vacancy_id=30&page=0&per_page=50',
                          {'items': [{'id': '40', 'resume': {'id': 'resume1', 'title': 'Role'}}], 'pages': 1, 'page': 0})
        self.provider.add('GET', '/resumes/resume1', {'errors': [{'value': 'not_enough_permissions'}]}, status=403)
        self.module.sync_vacancy('recruiter1', connection, '30', collection_ids=['response'])
        self.assertEqual('unavailable', self.sql('SELECT cv_status FROM hh_negotiations')[0][0])
        source = json.loads(self.sql('SELECT source FROM hh_resumes')[0][0])
        self.assertEqual('', source['contactEmail'])
        self.assertFalse(source['canViewFullInfo'])

    def test_prepare_failure_rolls_back_entire_import(self):
        connection = self.oauth()
        self.vacancy(connection)
        self.import_fixture(connection)
        def failing(db, key, profile, source):
            self.prepare(db, key, profile, source)
            raise ValueError('synthetic failure')
        self.module.prepare_candidate = failing
        with self.assertRaises(ValueError):
            self.module.sync_vacancy('recruiter1', connection, '30', collection_ids=['response'])
        self.assertEqual([], self.sql('SELECT * FROM prepared_test'))
        self.assertEqual([], self.sql('SELECT * FROM hh_resumes'))

    def test_cv_normalization_does_not_invent_preferences(self):
        profile, source = normalize_resume(CV)
        self.assertEqual('Анна Тестовая', profile['name'])
        self.assertEqual('full-time', profile['employmentType'])
        self.assertNotIn('salaryMin', profile)
        self.assertNotIn('latestStartDate', profile)
        self.assertNotIn('workMode', profile)
        self.assertEqual('synthetic@example.test', source['contactEmail'])

    def test_resume_identity_mismatch_cannot_prepare_another_person(self):
        connection = self.oauth()
        self.vacancy(connection)
        self.import_fixture(connection, cvs={'resume1': dict(CV, id='someone_else')})
        self.problem('resume_identity_mismatch', lambda: self.module.sync_vacancy('recruiter1', connection, '30', collection_ids=['response']))
        self.assertEqual([], self.sql('SELECT * FROM prepared_test'))

    def test_malformed_vacancy_page_does_not_hide_existing_list(self):
        connection = self.oauth()
        self.vacancy(connection)
        self.provider.add('GET', '/employers/10/vacancies/active?all_accessible=true&page=0&per_page=50',
                          {'items': [None], 'page': 0, 'pages': 1})
        self.problem('invalid_vacancy_response', lambda: self.module.sync_vacancies('recruiter1', connection))
        self.assertTrue(self.module.vacancies('recruiter1', connection)[0]['active'])

    def test_explicit_rule_is_required_and_queue_idempotent(self):
        connection, rule, job = self.ready()
        same = self.module.enqueue_invitation('recruiter1', connection, '40', rule_id=rule, event_key='call1',
                                              invitation_id='invite1', url='https://example.test/call?token=synthetic')
        self.assertEqual(job['id'], same['id'])
        self.assertEqual(1, len(self.sql('SELECT message_uuid FROM hh_outbox')))
        self.problem('event_payload_conflict', lambda: self.module.enqueue_invitation('recruiter1', connection, '40',
            rule_id=rule, event_key='call1', invitation_id='invite2', url='https://example.test/other'))
        self.problem('invalid_invitation_url', lambda: self.module.enqueue_invitation('recruiter1', connection, '40',
            rule_id=rule, event_key='call2', invitation_id='invite1', url='https://evil.test/call'))
        self.assertNotIn('synthetic', repr(job))

    def test_disabled_rule_never_sends(self):
        _, _, job = self.ready(enabled=False)
        self.problem('sending_rule_disabled', lambda: self.module.send_once('recruiter1', job['id'], is_current=lambda *a: True))
        self.assertEqual('queued', self.module.outbox_item('recruiter1', job['id'])['status'])

    def test_existing_domain_fragment_invitation_is_preserved(self):
        connection, rule, _ = self.ready()
        url = 'https://example.test/iframe.html?id=hr-vision-product--candidate&cp=guest#invite=' + 'a' * 43
        job = self.module.enqueue_invitation('recruiter1', connection, '40', rule_id=rule, event_key='call2',
                                              invitation_id='invite2', url=url)
        self.preflight()
        self.provider.add('POST', '/common/chats/50/messages', {'id': 'message2'}, status=201,
                          verify=lambda call: self.assertIn(url, call['json_body']['text']))
        self.assertEqual('sent', self.module.send_once('recruiter1', job['id'], is_current=lambda *a: True)['status'])

    def test_global_worker_switch_never_sends(self):
        _, _, job = self.ready()
        self.module.config = replace(self.config, sending_enabled=False)
        self.problem('sending_disabled', lambda: self.module.send_once('recruiter1', job['id'], is_current=lambda *a: True))

    def test_revoked_invitation_is_cancelled_before_provider_call(self):
        _, _, job = self.ready()
        result = self.module.send_once('recruiter1', job['id'], is_current=lambda *a: False)
        self.assertEqual('cancelled', result['status'])
        self.assertEqual(0, result['attempts'])

    def test_send_201_requires_provider_id_and_does_not_send_twice(self):
        _, _, job = self.ready()
        self.preflight()
        self.provider.add('POST', '/common/chats/50/messages', {'id': 'message1'}, status=201,
                          verify=lambda call: self.assertTrue(call['json_body']['is_automated']))
        result = self.module.send_once('recruiter1', job['id'], is_current=lambda *a: True)
        self.assertEqual(('sent', 'message1'), (result['status'], result['provider_message_id']))
        self.assertEqual(result, self.module.send_once('recruiter1', job['id'], is_current=lambda *a: True))

    def test_timeout_then_conflict_keeps_uuid_and_needs_review(self):
        _, _, job = self.ready()
        self.preflight()
        self.provider.unavailable('POST', '/common/chats/50/messages')
        first = self.module.send_once('recruiter1', job['id'], is_current=lambda *a: True)
        self.assertEqual('unknown', first['status'])
        self.time = first['next_attempt'] + 1
        self.preflight()
        self.provider.add('POST', '/common/chats/50/messages', {}, status=409)
        second = self.module.send_once('recruiter1', job['id'], is_current=lambda *a: True)
        self.assertEqual('needs_review', second['status'])
        self.assertIsNone(second['provider_message_id'])
        sends = [call[2]['json_body'] for call in self.provider.calls if '/messages' in call[1]]
        self.assertEqual(sends[0], sends[1])

    def test_busy_lease_prevents_second_worker_and_expired_lease_recovers(self):
        _, _, job = self.ready()
        old_uuid = self.sql('SELECT message_uuid FROM hh_outbox')[0][0]
        self.sql("UPDATE hh_outbox SET status='sending',attempts=1,lease_until=?,claim_token='old'", (self.time + 120,))
        self.assertEqual('sending', self.module.send_once('recruiter1', job['id'], is_current=lambda *a: True)['status'])
        self.time += 121
        self.preflight()
        self.provider.add('POST', '/common/chats/50/messages', {}, status=409)
        self.assertEqual('needs_review', self.module.send_once('recruiter1', job['id'], is_current=lambda *a: True)['status'])
        self.assertEqual(old_uuid, self.sql('SELECT message_uuid FROM hh_outbox')[0][0])
        self.module.finish_send(job['id'], 'old', 'sent', provider_id='old-response')
        self.assertEqual('needs_review', self.module.outbox_item('recruiter1', job['id'])['status'])

    def test_changed_chat_is_not_sent_with_reused_message_uuid(self):
        _, _, job = self.ready()
        self.preflight(chat_id=999)
        self.assertEqual('cancelled', self.module.send_once('recruiter1', job['id'], is_current=lambda *a: True)['status'])

    def test_invitation_can_be_revoked_during_preflight(self):
        _, _, job = self.ready()
        self.preflight()
        decisions = iter([True, False])
        self.assertEqual('cancelled', self.module.send_once('recruiter1', job['id'], is_current=lambda *a: next(decisions))['status'])

    def test_archived_source_vacancy_stops_queued_sends(self):
        _, _, job = self.ready()
        self.sql('UPDATE hh_vacancies SET active=0')
        self.preflight()
        self.assertEqual('cancelled', self.module.send_once('recruiter1', job['id'], is_current=lambda *a: True)['status'])

    def test_preflight_failure_has_no_unknown_delivery_claim(self):
        _, _, job = self.ready()
        self.provider.unavailable('GET', '/negotiations/40')
        result = self.module.send_once('recruiter1', job['id'], is_current=lambda *a: True)
        self.assertEqual('retry', result['status'])
        self.assertIsNone(result['provider_message_id'])

    def test_connection_method_access_is_stored_without_inventing_permissions(self):
        connection = self.oauth()
        fixture = {'items': [{'id': 'resume', 'access': False}]}
        self.provider.add('GET', '/employers/10/managers/20/method_access', fixture)
        self.assertEqual(fixture, self.module.check_access('recruiter1', connection))
        self.assertEqual(fixture, json.loads(self.sql('SELECT permissions FROM hh_connections')[0][0]))

    def test_temporary_error_retry_is_bounded(self):
        _, _, job = self.ready()
        for count in range(5):
            self.preflight()
            self.provider.add('POST', '/common/chats/50/messages', {}, status=503)
            result = self.module.send_once('recruiter1', job['id'], is_current=lambda *a: True)
            self.time = result['next_attempt'] + 1
        self.assertEqual('needs_review', result['status'])
        self.assertEqual(5, result['attempts'])
        self.assertEqual(result, self.module.send_once('recruiter1', job['id'], is_current=lambda *a: True))

    def test_provider_permission_error_stops_retry(self):
        _, _, job = self.ready()
        self.preflight()
        self.provider.add('POST', '/common/chats/50/messages', {'secret': 'not echoed'}, status=403)
        result = self.module.send_once('recruiter1', job['id'], is_current=lambda *a: True)
        self.assertEqual('blocked', result['status'])
        self.assertNotIn('not echoed', repr(result))

    def test_201_without_id_is_unknown_not_sent(self):
        _, _, job = self.ready()
        self.preflight()
        self.provider.add('POST', '/common/chats/50/messages', {}, status=201)
        result = self.module.send_once('recruiter1', job['id'], is_current=lambda *a: True)
        self.assertEqual('unknown', result['status'])
        self.assertIsNone(result['provider_message_id'])


if __name__ == '__main__':
    unittest.main()
