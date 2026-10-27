"""Offline HH browser protocol tests. No real profile, HH network or messages."""
import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from hh_browser import BrowserBridgeClient, BrowserError, HHBrowserAdapter, SelectorProfile, WorkerBridge
from hh_browser.adapter import hh_url, message_hash
from hh_browser.integration import integrate


HERE = Path(__file__).parent
TARGET = {'accountId': 'account1', 'resumeId': 'resume1', 'chatId': 'chat1', 'chatUrl': 'https://hh.ru/chat/1'}
MESSAGE = 'Анна, выберите время: https://example.test/book?token=synthetic'
TOKEN = 'test-only-local-bridge-token-00000000'


def profile(calibrated=True):
    data = json.loads((HERE / 'hh_browser/selector-profile.example.json').read_text())
    data['calibrated'] = calibrated
    return SelectorProfile(data)


class Node:
    def __init__(self, *, text='', attrs=None, children=None, visible=True, action=None):
        self.text, self.attrs, self.children = text, attrs or {}, children or {}
        self.visible, self.action, self.value, self.on_input = visible, action, '', None

    def locator(self, selector):
        return Locator(self.children.get(selector, []))


class Locator:
    def __init__(self, nodes):
        self.nodes = nodes

    def count(self): return len(self.nodes)
    def nth(self, index): return Locator([self.nodes[index]])
    def locator(self, selector): return self.nodes[0].locator(selector)
    def get_attribute(self, name): return self.nodes[0].attrs.get(name)
    def inner_text(self, **kwargs): return self.nodes[0].text
    def is_visible(self): return self.nodes[0].visible
    def fill(self, value, **kwargs): self.nodes[0].value = value
    def input_value(self, **kwargs):
        if self.nodes[0].on_input: self.nodes[0].on_input()
        return self.nodes[0].value
    def click(self, **kwargs): return self.nodes[0].action()


class Page:
    """Minimal locator double with persistent versus optimistic messages."""
    def __init__(self):
        self.url = TARGET['chatUrl']
        self.account, self.role, self.resume_id, self.chat_id = 'account1', 'employer', 'resume1', 'chat1'
        self.captcha, self.login, self.full_cv, self.chat_available = False, False, True, True
        self.persisted, self.clicks, self.navigations = [], 0, []
        self.optimistic_only, self.timeout_after_click = False, False
        self.composer = Node()
        self.applicants = [('resume1', 'response1', 'https://hh.ru/resume/1'),
                           ('resume2', 'response2', 'https://hh.ru/resume/2'),
                           ('resume3', 'response3', 'https://hh.ru/resume/3')]

    def goto(self, url, **kwargs):
        self.navigations.append(url); self.url = url
        if '/resume/' in url:
            number = url.rsplit('/', 1)[-1]
            self.resume_id, self.chat_id = 'resume' + number, 'chat' + number
        if '/chat/' in url:
            number = url.rsplit('/', 1)[-1]
            self.resume_id, self.chat_id = 'resume' + number, 'chat' + number

    def reload(self, **kwargs): pass

    def send(self):
        self.clicks += 1
        if not self.optimistic_only:
            self.persisted.append(('message' + str(self.clicks), self.composer.value))
        if self.timeout_after_click:
            raise TimeoutError('synthetic post-click timeout')

    def locator(self, selector):
        values = {
            '[data-account-id]': [Node(attrs={'data-account-id': self.account})],
            '[data-role]': [Node(attrs={'data-role': self.role})],
            '[data-resume-id]': [Node(attrs={'data-resume-id': self.resume_id})],
            '[data-chat-id]': [Node(attrs={'data-chat-id': self.chat_id})],
            '[data-vacancy-id]': [Node(attrs={'data-vacancy-id': 'vacancy1'})],
            'h1': [Node(text='Анна Тестовая')],
            "[data-test='cv-text']": [Node(text='Опыт продаж. Синтетическое резюме.')],
            "[data-test='full-cv']": [Node()] if self.full_cv else [],
            "[data-test='chat-link']": [Node(attrs={'href': 'https://hh.ru/chat/' + self.resume_id[6:]})] if self.chat_available else [],
            "[data-test='captcha']": [Node()] if self.captcha else [],
            "[data-test='login']": [Node()] if self.login else [],
            'textarea': [self.composer],
            "[data-test='send']": [Node(action=self.send)],
            "[data-outgoing='true']": [Node(children={
                '[data-message-id]': [Node(attrs={'data-message-id': mid})],
                "[data-test='message-text']": [Node(text=text)]}) for mid, text in self.persisted],
            "[data-test='applicant']": [Node(children={
                "[data-test='resume-link']": [Node(attrs={'href': url})],
                '[data-response-id]': [Node(attrs={'data-response-id': response})],
                "[data-test='chat-link']": [Node(attrs={'href': 'https://hh.ru/chat/' + rid[6:]})]
            }) for rid, response, url in self.applicants],
        }
        return Locator(values.get(selector, []))


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.page = Page()
        self.adapter = HHBrowserAdapter(self.page, profile(), clock=lambda: 1234.5)

    def assert_code(self, code, fn):
        with self.assertRaises(BrowserError) as caught: fn()
        self.assertEqual(code, caught.exception.code)

    def test_only_hh_https_no_credentials_or_trick_hosts(self):
        for url in ['https://hh.ru.evil.test/chat/1', 'http://hh.ru/a', 'https://a@hh.ru/a', '//example.test/a', 'https://hh.ru/a#b']:
            self.assert_code('invalid_hh_url', lambda: hh_url(url))
        self.assertEqual('https://spb.hh.ru/a', hh_url('https://spb.hh.ru/a'))

    def test_uncalibrated_profile_never_fills_or_sends(self):
        adapter = HHBrowserAdapter(self.page, profile(False))
        self.assert_code('selectors_not_calibrated', lambda: adapter.prepare(TARGET, MESSAGE))
        self.assertEqual('', self.page.composer.value)
        self.assertEqual(0, self.page.clicks)

    def test_account_role_and_challenge_are_hard_stops(self):
        for field, value, code in [('account', 'other', 'account_mismatch'), ('role', 'applicant', 'employer_role_required'),
                                   ('captcha', True, 'captcha'), ('login', True, 'login_required')]:
            page = Page(); setattr(page, field, value)
            self.assert_code(code, lambda: HHBrowserAdapter(page, profile()).prepare(TARGET, MESSAGE))
            self.assertEqual(0, page.clicks)

    def test_recipient_mismatch_cannot_send(self):
        wrong = TARGET | {'resumeId': 'another'}
        self.assert_code('recipient_mismatch', lambda: self.adapter.prepare(wrong, MESSAGE))
        self.assertEqual(0, self.page.clicks)

    def test_direct_discovery_facts_full_cv_and_cursor(self):
        source = {'kind': 'direct', 'accountId': 'account1', 'resumeUrls': ['https://hh.ru/resume/1', 'https://hh.ru/resume/2'], 'limit': 1}
        result = self.adapter.discover(source)
        row = result['candidates'][0]
        self.assertEqual(('resume1', 'chat1', True), (row['resumeId'], row['chatId'], row['cvAvailable']))
        self.assertEqual({'offset': 1}, result['cursor'])
        self.page.full_cv = False
        result2 = self.adapter.discover(source | {'cursor': result['cursor']})
        self.assertEqual('resume2', result2['candidates'][0]['resumeId'])
        self.assertFalse(result2['candidates'][0]['cvAvailable'])
        self.assertTrue(result2['complete'])
        self.assertEqual(0, self.page.clicks)

    def test_no_chat_does_not_click_invite_or_buy_contacts(self):
        self.page.chat_available = False
        result = self.adapter.discover({'kind': 'direct', 'accountId': 'account1', 'resumeUrls': ['https://hh.ru/resume/1']})
        self.assertEqual('chat_unavailable', result['candidates'][0]['discoveryStatus'])
        self.assertEqual(0, self.page.clicks)

    def test_applicants_provenance_partial_page_resume(self):
        source = {'kind': 'vacancy_applicants', 'accountId': 'account1', 'vacancyId': 'vacancy1',
                  'listUrl': 'https://hh.ru/applicants/1', 'limit': 2}
        first = self.adapter.discover(source)
        self.assertEqual(['response1', 'response2'], [r['responseId'] for r in first['candidates']])
        second = self.adapter.discover(source | {'cursor': first['cursor']})
        self.assertEqual(['resume3'], [r['resumeId'] for r in second['candidates']])
        self.assertTrue(second['complete'])
        self.assertTrue(all(r['vacancyId'] == 'vacancy1' for r in first['candidates']))

    def test_prepare_is_not_send_then_persisted_message_has_evidence(self):
        prepared = self.adapter.prepare(TARGET, MESSAGE)
        self.assertEqual(0, self.page.clicks)
        self.adapter.click_send(prepared)
        result = self.adapter.observe(prepared)
        self.assertEqual('sent_observed', result['outcome'])
        self.assertEqual({'accountId': 'account1', 'resumeId': 'resume1', 'chatId': 'chat1',
                          'messageHash': message_hash(MESSAGE), 'messageId': 'message1',
                          'observedAt': 1234.5, 'persisted': True}, result['evidence'])

    def test_optimistic_send_without_persistence_is_unknown(self):
        self.page.optimistic_only = True
        prepared = self.adapter.prepare(TARGET, MESSAGE)
        self.adapter.click_send(prepared)
        self.assertEqual('unknown', self.adapter.observe(prepared)['outcome'])

    def test_existing_same_text_is_not_resent_and_changed_composer_fails(self):
        self.page.persisted = [('old1', MESSAGE)]
        self.assert_code('matching_message_exists', lambda: self.adapter.prepare(TARGET, MESSAGE))
        self.page.persisted = []
        prepared = self.adapter.prepare(TARGET, MESSAGE)
        self.page.composer.value = 'different'
        self.assert_code('message_changed', lambda: self.adapter.click_send(prepared))
        self.assertEqual(0, self.page.clicks)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = WorkerBridge(self.temp.name, profile(), TOKEN)
        self.page = Page()
        self.adapter = HHBrowserAdapter(self.page, profile(), ack_timeout=0.02)

    def tearDown(self):
        self.bridge.close(); self.temp.cleanup()

    def prepared(self):
        return self.bridge._execute(self.adapter, 'prepare', {'target': TARGET, 'message': MESSAGE})

    def send(self, prepared, attempt='attempt1'):
        return self.bridge._execute(self.adapter, 'send', {'preparedId': prepared['preparedId'], 'attemptId': attempt, 'campaignStarted': True})

    def test_durable_marker_precedes_click_and_duplicate_send_never_clicks(self):
        prepared = self.prepared()
        self.assertFalse(self.bridge.legacy_allowed())
        self.page.composer.on_input = lambda: self.assertEqual('send_started', self.bridge.store.get('attempt1')['state'])
        result = self.send(prepared)
        self.assertEqual('sent_observed', result['outcome'])
        self.assertEqual(result, self.send(prepared))
        self.assertEqual(1, self.page.clicks)
        self.assertTrue(self.bridge.legacy_allowed())

    def test_post_click_timeout_unknown_then_read_only_reconcile(self):
        self.page.timeout_after_click = True
        prepared = self.prepared()
        self.assertEqual('unknown', self.send(prepared)['outcome'])
        self.assertTrue(self.bridge.store.control()['paused'])
        result = self.bridge._execute(self.adapter, 'observe', {'attemptId': 'attempt1'})
        self.assertEqual('sent_observed', result['outcome'])
        self.bridge._execute(self.adapter, 'resume', {})
        self.assertEqual(1, self.page.clicks)

    def test_pause_arriving_after_barrier_prevents_click_and_preserves_unknown(self):
        prepared = self.prepared()
        self.page.composer.on_input = lambda: self.bridge.store.pause('operator_pause')
        result = self.send(prepared)
        self.assertEqual('unknown', result['outcome'])
        self.assertEqual(0, self.page.clicks)
        self.assertEqual('unknown', self.bridge.store.get('attempt1')['state'])

    def test_recovery_and_manual_do_not_release_on_restart(self):
        payload = self.adapter.prepare(TARGET, MESSAGE)
        self.bridge.store.start('attempt1', 'prep1', payload)
        self.bridge.store.manual(True)
        self.bridge.close()
        self.bridge = WorkerBridge(self.temp.name, profile(), TOKEN)
        self.assertEqual('unknown', self.bridge.store.get('attempt1')['state'])
        self.assertTrue(self.bridge.store.control()['manual'])
        self.assertFalse(self.bridge.legacy_allowed())

    def test_acknowledge_allows_other_jobs_never_repeat_uncertain(self):
        self.page.optimistic_only = True
        prepared = self.prepared()
        self.send(prepared)
        with self.assertRaises(BrowserError): self.bridge.store.resume()
        self.bridge._execute(self.adapter, 'acknowledge', {'attemptId': 'attempt1'})
        self.bridge._execute(self.adapter, 'resume', {})
        calls = len(self.page.navigations)
        observation = self.bridge._execute(self.adapter, 'observe', {'attemptId': 'attempt1'})
        self.assertTrue(observation['acknowledged'])
        self.assertEqual(calls, len(self.page.navigations))
        self.assertFalse(self.bridge.store.control()['paused'])
        self.assertEqual('unknown', self.send(prepared)['outcome'])
        self.assertEqual(1, self.page.clicks)

    def test_observation_cannot_steal_prepared_browser(self):
        payload = self.adapter.prepare(TARGET, MESSAGE)
        self.bridge.store.start('old1', 'old-prep', payload)
        self.bridge.store.finish('old1', {'outcome': 'unknown'})
        self.bridge.store.acknowledge('old1'); self.bridge.store.resume()
        self.prepared()
        calls = len(self.page.navigations)
        with self.assertRaises(BrowserError) as caught:
            self.bridge._execute(self.adapter, 'observe', {'attemptId': 'old1', 'recheck': True})
        self.assertEqual('prepared_send_holds_browser', caught.exception.code)
        self.assertEqual(calls, len(self.page.navigations))

    def test_second_owner_rejected(self):
        with self.assertRaises(BrowserError) as caught: WorkerBridge(self.temp.name, profile(), TOKEN)
        self.assertEqual('browser_owner_exists', caught.exception.code)

    def test_uncalibrated_bridge_status_does_not_probe_fake_selectors(self):
        self.bridge.close()
        self.bridge = WorkerBridge(self.temp.name, profile(False), TOKEN)
        self.bridge.sample_status(object())
        status = self.bridge.status()
        self.assertTrue(status['configured'])
        self.assertFalse(status['selectorsCalibrated'])
        self.assertEqual('', status['accountId'])
        self.assertEqual('selectors_not_calibrated', status['error'])
        self.assertTrue(self.bridge.legacy_allowed())
        with self.assertRaises(BrowserError) as caught:
            self.bridge._execute(self.adapter, 'discover', {'source': {'kind': 'direct'}})
        self.assertEqual('selectors_not_calibrated', caught.exception.code)
        self.assertFalse(caught.exception.pause)
        self.assertEqual([], self.page.navigations)

    def test_rpc_auth_and_async_discovery_poll_without_network(self):
        self.assertEqual(401, self.bridge.request('GET', '/rpa/status', {}, 'bad')[0])
        body = {'source': {'kind': 'direct', 'accountId': 'account1', 'resumeUrls': ['https://hh.ru/resume/1']}}
        code, operation = self.bridge.request('POST', '/rpa/discover', body, 'Bearer ' + TOKEN)
        self.assertEqual(202, code)
        self.assertEqual('queued', operation['status'])
        self.bridge.poll(self.page)
        code, done = self.bridge.request('GET', '/rpa/operations/' + operation['operationId'], {}, 'Bearer ' + TOKEN)
        self.assertEqual('completed', done['status'])
        self.assertEqual('resume1', done['result']['candidates'][0]['resumeId'])

    def test_http_timeout_before_execution_cancels_prepare(self):
        code, result = self.bridge.request('POST', '/rpa/prepare', {'target': TARGET, 'message': MESSAGE}, 'Bearer ' + TOKEN, timeout=0)
        self.assertEqual(504, code)
        self.bridge.poll(self.page)
        self.assertIsNone(self.bridge.prepared)
        self.assertEqual('', self.page.composer.value)

    def test_client_callback_commits_before_request_and_no_retry(self):
        client = BrowserBridgeClient(token=TOKEN)
        order = []
        def request(action, body):
            order.append('http'); raise BrowserError('bridge_unavailable')
        client._request = request
        result = client.send('prep', 'attempt', before_send=lambda _: order.append('commit'))
        self.assertEqual(['commit', 'http'], order)
        self.assertEqual('unknown', result['outcome'])
        order.clear()
        with self.assertRaises(BrowserError): client.send('prep', 'attempt', before_send=lambda _: False)
        self.assertEqual([], order)


class IntegrationTests(unittest.TestCase):
    def test_actual_existing_worker_generates_ast_without_writing_source(self):
        source = Path('/Users/NIKITA/Downloads/masha-dasha-mvp/hh-daily/worker.py')
        if not source.exists(): self.skipTest('local donor worker not present')
        original = source.read_text()
        result = integrate(original)
        ast.parse(result)
        self.assertIn('if RPA.poll(page): continue', result)
        self.assertIn('RPA.begin_legacy()', result)
        self.assertEqual(original, source.read_text())
        with self.assertRaises(ValueError): integrate(result)
        with self.assertRaises(ValueError): integrate(original.replace('import alert\n', ''))

    @unittest.skipUnless(importlib.util.find_spec('playwright'), 'optional local Playwright not installed')
    def test_offline_html_delayed_dom_ack_before_reload(self):
        from playwright.sync_api import sync_playwright
        html = '''<!doctype html><meta charset="utf-8">
        <header data-account-id="account1" data-role="employer"></header>
        <main data-resume-id="resume1" data-chat-id="chat1">
          <textarea></textarea><button data-test="send">Отправить</button><section id="messages"></section>
        </main><script>
        function render(){
          const box=document.getElementById('messages');box.textContent='';
          for(const msg of JSON.parse(localStorage.getItem('messages')||'[]')){
            const row=document.createElement('article');row.dataset.outgoing='true';
            const id=document.createElement('span');id.dataset.messageId=msg.id;
            const txt=document.createElement('div');txt.dataset.test='message-text';txt.textContent=msg.text;
            row.append(id,txt);box.append(row);
          }
        }
        render();document.querySelector('button').onclick=()=>{
          const text=document.querySelector('textarea').value;
          setTimeout(()=>{localStorage.setItem('messages',JSON.stringify([{id:'persisted1',text}]));render()},450);
        };
        </script>'''
        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                self.skipTest('optional offline test browser not installed')
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(service_workers='block')
                requests = []
                def serve(route):
                    requests.append(route.request.url)
                    if route.request.url == TARGET['chatUrl']:
                        route.fulfill(status=200, content_type='text/html', body=html)
                    else:
                        route.abort()
                context.route('**/*', serve)
                page = context.new_page()
                adapter = HHBrowserAdapter(page, profile())
                prepared = adapter.prepare(TARGET, MESSAGE)
                started = time.monotonic()
                adapter.click_send(prepared)
                adapter.wait_for_ack(prepared)
                self.assertGreaterEqual(time.monotonic() - started, 0.35)
                result = adapter.observe(prepared)
                self.assertEqual('sent_observed', result['outcome'])
                self.assertEqual('persisted1', result['evidence']['messageId'])
                self.assertTrue(all(url == TARGET['chatUrl'] for url in requests))
                context.close()
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
