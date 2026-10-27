"""Integration through real SQLite with a simulated browser; never contacts HH."""
import datetime as dt
import hashlib
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from candidate_booking import CandidateBooking
from candidate_portal import CandidatePortal, ClosingConnection
from domain import Problem, iso, now
from hh_outreach import HHOutreach
from hh_outreach_worker import OutreachBrowserService
from hh_routes import HHRecruiterRoutes
from hh_browser import BrowserError
from test_candidate_portal import FakeMedia, assign_fixture_slot


class FakeBrowser:
    def __init__(self):
        self.sent, self.paused = [], False
        self.outcome = 'sent_observed'
        self.candidates = []

    def status(self):
        return {'accountId': 'test-account', 'loggedIn': True, 'selectorsCalibrated': True,
                'paused': self.paused, 'busy': False, 'captcha': False}

    def prepare(self, target, message):
        self.prepared = target
        return {'preparedId': 'prepared-test', 'messageHash': hashlib.sha256(message.encode()).hexdigest()}

    def send(self, prepared_id, attempt_id, *, before_send):
        before_send(attempt_id)
        self.sent.append(attempt_id)
        return {'outcome': self.outcome, 'evidence': self.evidence()}

    def evidence(self):
        return {key: self.prepared[key] for key in ('accountId', 'resumeId', 'chatId', 'messageHash')} | {
            'messageId': 'message-test', 'persisted': True, 'observedAt': time.time()}

    def observe(self, attempt_id):
        return {'outcome': self.outcome, 'evidence': self.evidence()}

    def pause(self, reason):
        self.paused = True

    def resume(self):
        self.paused = False

    def discover(self, source):
        return {'candidates': self.candidates, 'complete': True}

    def begin_discovery(self, source):
        self.source = source
        return {'operationId': 'operation-test', 'status': 'queued'}

    def discovery_status(self, operation_id):
        return {'operationId': operation_id, 'status': 'completed', 'result': self.discover(self.source)}


class BrowserServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test.sqlite3'
        def connect():
            db = sqlite3.connect(self.path, factory=ClosingConnection)
            db.execute('PRAGMA foreign_keys=ON')
            return db
        self.connect = connect
        self.portal = CandidatePortal(connect, threading.RLock(), FakeMedia(), 'https://test.example', None)
        self.booking = CandidateBooking(self.portal)
        hh = HHRecruiterRoutes(connect, self.portal)
        self.engine = HHOutreach(connect, issue_booking=self.booking.issue,
            booking_current=self.booking.current, prepare_candidate=hh.prepare_candidate, sending_enabled=True)
        self.booking.event_sink = self.engine.event_sink
        self.browser = FakeBrowser()
        self.service = OutreachBrowserService(self.engine, self.portal, bridge=self.browser,
                                              account_id='test-account', worker_enabled=True)
        self.campaign = self.engine.create_campaign({'name': 'Test', 'mode': 'direct', 'accountId': 'test-account',
            'template': 'Здравствуйте, {name}! Выберите время: {booking_url}', 'limit': 3, 'paceSeconds': 30})
        self.record = {'accountId': 'test-account', 'resumeId': 'resume-one', 'chatId': 'chat-one',
            'resumeUrl': 'https://hh.ru/resume/resume-one', 'chatUrl': 'https://hh.ru/chat/chat-one',
            'name': 'Тестовый кандидат', 'cvAvailable': True, 'cvText': 'Полное резюме тестового кандидата: опыт и навыки.',
            'discoveryStatus': 'ready'}

    def queued(self):
        self.engine.import_recipients(self.campaign['id'], [self.record])
        self.engine.queue(self.campaign['id'])
        self.engine.control(self.campaign['id'], 'resume')

    def test_uncalibrated_browser_does_not_claim_another_account(self):
        self.browser.status = lambda: {'configured': True, 'loggedIn': False, 'accountId': '',
            'selectorsCalibrated': False, 'error': 'selectors_not_calibrated'}
        state = self.service.browser_state()
        self.assertTrue(state['configured'])
        self.assertFalse(state['loggedIn'])
        self.assertFalse(state['calibrationReady'])
        self.assertIn('Нужно проверить элементы HH', state['error'])
        self.queued()
        self.service.tick()
        self.assertEqual([], self.browser.sent)

    def test_calibrated_browser_rejects_an_observed_different_account(self):
        self.browser.status = lambda: {'loggedIn': True, 'accountId': 'other-account',
            'selectorsCalibrated': True}
        state = self.service.browser_state()
        self.assertFalse(state['loggedIn'])
        self.assertIn('другой аккаунт HH', state['error'])

    def test_worker_snapshot_to_send_to_booking_and_owner(self):
        self.queued()
        self.service.tick()
        self.assertEqual(len(self.browser.sent), 1)
        self.assertEqual(self.engine.detail(self.campaign['id'])['stats']['sentObserved'], 1)
        with self.connect() as db:
            url = db.execute('SELECT booking_url FROM ho_outbox').fetchone()[0]
            start = now() + dt.timedelta(days=1)
            slot = {'id': 'slot-one', 'start': iso(start), 'end': iso(start + dt.timedelta(minutes=30)),
                    'recruiterName': 'Тестовый рекрутер'}
            db.execute('INSERT INTO cp_recruiter_slots VALUES (?,?,?,?,NULL)',
                       (slot['id'], slot['start'], slot['end'], slot['recruiterName']))
            assign_fixture_slot(db, slot)
        token = parse_qs(urlsplit(url).fragment)['booking'][0]
        body = {'token': token, 'slotId': 'slot-one', 'requestId': 'test-request-booking-123',
                'email': 'booking@example.test', 'consentProfile': True}
        result, _ = self.booking.handle('POST', '/api/hr/booking/book', {'Content-Type': 'application/json'}, body, '127.0.0.1')
        retry, _ = self.booking.handle('POST', '/api/hr/booking/book', {'Content-Type': 'application/json'}, body, '127.0.0.1')
        self.assertEqual(result['booking']['id'], retry['booking']['id'])
        stats = self.engine.detail(self.campaign['id'])['stats']
        self.assertEqual(stats['booked'], 1)
        self.assertEqual(stats['completed'], 0)
        self.assertEqual(stats['videoReady'], 0)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_booking_attribution').fetchone()[0], 1)
            self.assertTrue(db.execute('SELECT COUNT(*) FROM rn_outbox').fetchone()[0] > 0)

    def test_uncertain_send_is_not_repeated_and_can_be_observed(self):
        self.queued()
        self.browser.outcome = 'unknown'
        self.service.tick()
        self.service.tick()
        self.assertEqual(len(self.browser.sent), 1)
        self.assertTrue(self.browser.paused)
        self.assertEqual(self.engine.detail(self.campaign['id'])['stats']['unknown'], 1)
        self.browser.outcome = 'sent_observed'
        self.service.tick()
        self.assertEqual(len(self.browser.sent), 1)
        self.assertEqual(self.engine.detail(self.campaign['id'])['stats']['sentObserved'], 1)

    def test_pause_before_click_cancels_effect(self):
        self.queued()
        original = self.browser.prepare
        def pause_during_prepare(target, message):
            prepared = original(target, message)
            self.engine.control(self.campaign['id'], 'pause')
            return prepared
        self.browser.prepare = pause_during_prepare
        self.service.tick()
        self.assertEqual(self.browser.sent, [])
        detail = self.engine.detail(self.campaign['id'])
        self.assertEqual(detail['recipients'][0]['state'], 'blocked')
        self.assertEqual(detail['stats']['sentObserved'], 0)

    def test_discovery_prepares_cv_without_account_or_consent(self):
        self.browser.candidates = [self.record | {'responseId': '', 'vacancyId': ''},
                                   self.record | {'cvAvailable': False}]
        self.service.begin_discovery(self.campaign['id'], {'kind': 'direct', 'limit': 2})
        self.service.poll_discovery()
        result = self.service.discovery_state(self.campaign['id'])['result']
        self.assertEqual((result['imported'], result['skipped']), (1, 1))
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_resumes').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_consent_log').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ho_outbox').fetchone()[0], 0)

    def test_browser_control_requires_team_manager(self):
        with self.assertRaises(Problem) as error:
            self.service.handle('GET', '/api/hr/outreach/browser', {}, None, lambda db: 'candidate')
        self.assertEqual(error.exception.status, 403)
        state, _ = self.service.handle('GET', '/api/hr/outreach/browser', {}, None, lambda db: 'manager')
        self.assertTrue(state['calibrationReady'])
        self.assertNotIn('token', state)

    def test_unconfigured_service_has_no_effects(self):
        service = OutreachBrowserService(self.engine, self.portal)
        self.queued()
        service.tick()
        self.assertFalse(service.browser_state()['configured'])
        self.assertEqual(self.browser.sent, [])
        self.assertIsNone(service.thread)

    def test_discovery_continues_pages_with_sends_disabled_and_after_restart(self):
        self.engine.sending_enabled = False
        calls = []
        original_begin = self.browser.begin_discovery
        def begin(source):
            calls.append(dict(source))
            if len(calls) == 2:
                raise BrowserError('bridge_unavailable')
            return original_begin(source)
        def page(operation_id):
            offset = self.browser.source.get('cursor', {}).get('offset', 0)
            record = self.record | {'resumeId': f'resume-{offset}', 'chatId': f'chat-{offset}'}
            return {'status': 'completed', 'result': {'candidates': [record],
                    'complete': offset == 1, 'cursor': None if offset == 1 else {'offset': 1}}}
        self.browser.begin_discovery, self.browser.discovery_status = begin, page
        self.service.begin_discovery(self.campaign['id'], {'kind': 'direct', 'limit': 3})
        with self.assertRaises(BrowserError):
            self.service.tick()
        self.assertEqual(self.service.discovery_state(self.campaign['id'])['status'], 'running')
        restarted = OutreachBrowserService(self.engine, self.portal, bridge=self.browser,
                                            account_id='test-account', worker_enabled=True)
        restarted.tick()
        restarted.tick()
        state = restarted.discovery_state(self.campaign['id'])
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(state['result']['imported'], 2)
        self.assertEqual(calls[-1]['cursor'], {'offset': 1})
        self.assertEqual(self.browser.sent, [])

    def test_busy_browser_is_never_claimed(self):
        self.queued()
        self.browser.status = lambda: {'accountId': 'test-account', 'loggedIn': True,
                                       'selectorsCalibrated': True, 'busy': True}
        self.service.tick()
        self.assertEqual(self.engine.detail(self.campaign['id'])['recipients'][0]['state'], 'queued')
        self.assertEqual(self.browser.sent, [])

    def test_source_filter_query_is_preserved(self):
        campaign = self.engine.create_campaign({'name': 'Applicants', 'mode': 'applicants',
            'accountId': 'test-account', 'vacancyId': '1234', 'template': '{booking_url}', 'limit': 10, 'paceSeconds': 30})
        url = 'https://hh.ru/employer/responses?collection=invited&page=2&vacancyId=1234'
        result, _ = self.service.handle('POST', '/api/hr/outreach/campaigns/' + campaign['id'] + '/discover',
                                       {}, {'vacancyUrl': url}, lambda db: 'manager')
        self.assertEqual(self.browser.source['listUrl'], url)
        self.assertEqual(result['status'], 'queued')


if __name__ == '__main__':
    unittest.main()
