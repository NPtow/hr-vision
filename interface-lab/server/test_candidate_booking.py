"""Scoped HH booking with isolated SQLite, fake providers and synthetic people."""
import concurrent.futures
import datetime as dt
import hashlib
import json
import sqlite3
import threading
import unittest
from urllib.parse import parse_qs, urlsplit

import candidate_booking as booking
import candidate_invites as invites
from candidate_portal import profile_value
from domain import Problem, iso, now
import test_candidate_portal as fixture


class CandidateBookingTest(unittest.TestCase):
    tearDown = fixture.CandidatePortalTest.tearDown
    connect = fixture.CandidatePortalTest.connect
    payload = fixture.CandidatePortalTest.payload
    call = fixture.CandidatePortalTest.call
    not_manager = staticmethod(fixture.CandidatePortalTest.not_manager)
    register = fixture.CandidatePortalTest.register
    slot = fixture.CandidatePortalTest.slot

    def setUp(self):
        fixture.CandidatePortalTest.setUp(self)
        self.service = booking.CandidateBooking(self.portal, self.event)
        with self.connect() as db:
            db.execute('''CREATE TABLE booking_test_events (key TEXT PRIMARY KEY,kind TEXT,
                campaign_id TEXT,recipient_id TEXT,invitation_id TEXT,interview_id TEXT)''')

    @staticmethod
    def event(db, event_type, recipient_id, *, campaign_id, invitation_id, interview_id, event_key):
        db.execute('INSERT OR IGNORE INTO booking_test_events VALUES (?,?,?,?,?,?)',
                   (event_key, event_type, campaign_id, recipient_id, invitation_id, interview_id))

    def prepared(self, *, source='hh', cv=True):
        self.count += 1
        profile = profile_value({'name': 'Тестовый кандидат ' + str(self.count), 'city': 'Москва',
                                 'about': 'Private CV detail', 'consentReuse': False})
        resume = {'filename': 'resume.txt', 'mime': 'text/plain', 'content': b'synthetic CV',
                  'text': 'synthetic CV text', 'fields': {}} if cv else None
        with self.connect() as db:
            return invites.create_prepared(db, profile, source=source,
                                           external_key='hh-test:' + str(self.count), cv=resume)

    def issue(self, cid=None, campaign='campaign-1', recipient=None, **kwargs):
        cid = cid or self.prepared()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            result = self.service.issue(db, cid, campaign, recipient or 'recipient-' + cid, **kwargs)
        return cid, result, parse_qs(urlsplit(result['url']).fragment)['booking'][0]

    def request(self, action, token, **fields):
        return self.service.handle('POST', booking.PREFIX + action,
            {'Content-Type': 'application/json'}, {'token': token, **fields}, '127.0.0.1')

    def expect(self, status, action, token, **fields):
        with self.assertRaises(Problem) as failure:
            self.request(action, token, **fields)
        self.assertEqual(status, failure.exception.status)

    def rows(self, query, args=()):
        with self.connect() as db:
            return db.execute(query, args).fetchall()

    def book(self, token, slot, request='request-1'):
        return self.request('book', token, slotId=slot['id'], requestId=request)[0]

    def test_issue_same_link_survives_new_service_and_has_no_query_token(self):
        cid, first, token = self.issue(recipient='recipient-a')
        self.service = booking.CandidateBooking(self.portal, self.event)
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            second = self.service.issue(db, cid, 'campaign-1', 'recipient-a')
            self.assertTrue(self.service.current(db, first['invitationId'], cid))
            self.assertFalse(self.service.current(db, first['invitationId'], 'different-person'))
            self.assertEqual(hashlib.sha256(token.encode()).hexdigest(),
                             db.execute('SELECT token_hash FROM cp_booking_invitations').fetchone()[0])
        self.assertEqual(first, second)
        self.assertNotIn(token, urlsplit(first['url']).query)
        self.assertIn('cp=invite-booking', first['url'])
        self.assertEqual(1, len(self.rows('SELECT id FROM cp_booking_invitations')))

    def test_issue_requires_prepared_hh_cv_not_an_arbitrary_person(self):
        for cid in (self.prepared(cv=False), self.prepared(source='manual'), 'missing-candidate'):
            with self.assertRaises(Problem) as failure, self.connect() as db:
                self.service.issue(db, cid, 'campaign', 'recipient-' + cid)
            self.assertEqual(409, failure.exception.status)
        self.assertEqual([], self.rows('SELECT id FROM cp_booking_invitations'))

    def test_issue_identity_conflict_expiry_revocation_and_booked_suppression(self):
        cid, issued, token = self.issue(recipient='recipient-a')
        other = self.prepared()
        with self.assertRaises(Problem) as failure, self.connect() as db:
            self.service.issue(db, other, 'campaign-1', 'recipient-a')
        self.assertEqual(409, failure.exception.status)
        self.book(token, self.slot())
        with self.connect() as db:
            self.assertFalse(self.service.current(db, issued['invitationId']))
        with self.assertRaises(Problem) as failure, self.connect() as db:
            self.service.issue(db, cid, 'another-campaign', 'another-recipient')
        self.assertEqual(409, failure.exception.status)
        with self.connect() as db:
            self.service.revoke(db, issued['invitationId'])
        with self.assertRaises(Problem) as failure, self.connect() as db:
            self.service.issue(db, cid, 'campaign-1', 'recipient-a')
        self.assertEqual(410, failure.exception.status)

    def test_issuer_and_init_never_commit_callers_transaction(self):
        cid = self.prepared()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            booking.init(db)
            result = self.service.issue(db, cid, 'campaign', 'recipient')
            db.rollback()
        self.assertEqual([], self.rows('SELECT id FROM cp_booking_invitations'))
        token = parse_qs(urlsplit(result['url']).fragment)['booking'][0]
        self.expect(404, 'state', token)

    def test_state_is_minimal_and_shows_owned_active_half_hour_pool_only(self):
        cid, _, token = self.issue()
        first = self.slot(recruiter='Первый')
        second = self.slot(recruiter='Второй')
        disabled = self.slot(minutes=30, recruiter='Неактивный')
        with self.connect() as db:
            db.execute('UPDATE rc_accounts SET active=0 WHERE id IN '
                       '(SELECT recruiter_id FROM cp_recruiter_slot_owners WHERE slot_id=?)', (disabled['id'],))
            db.execute('INSERT INTO cp_recruiter_slots VALUES (?,?,?,?,NULL)',
                       ('legacy-slot', first['start'], first['end'], 'Без владельца'))
            db.execute('INSERT INTO cp_recruiter_slots VALUES (?,?,?,?,NULL)',
                       ('past-slot', iso(now() - dt.timedelta(hours=1)), iso(now() - dt.timedelta(minutes=30)), 'Первый'))
        state, headers = self.request('state', token)
        self.assertEqual({'status', 'candidateName', 'timezone', 'slots', 'booking'}, set(state))
        self.assertEqual('available', state['status'])
        self.assertEqual({first['id'], second['id']}, {slot['id'] for slot in state['slots']})
        self.assertNotIn('Set-Cookie', headers)
        self.assertEqual('no-store', headers['Cache-Control'])
        self.assertNotIn('Private CV detail', json.dumps(state))
        self.assertNotIn(cid, json.dumps(state))
        self.assertEqual([], self.rows('SELECT * FROM cp_sessions'))

    def test_page_loaded_is_one_event_on_repeated_state_refresh(self):
        _, issued, token = self.issue()
        for _ in range(3):
            self.request('state', token)
        rows = self.rows('SELECT kind,invitation_id,interview_id FROM booking_test_events')
        self.assertEqual([('booking.page_loaded', issued['invitationId'], None)], rows)

    def test_public_shape_origin_method_and_invalid_tokens_fail_closed(self):
        _, _, token = self.issue()
        for path, method, headers, body, status in [
            ('state', 'GET', {}, None, 405),
            ('state?token=' + token, 'POST', {'Content-Type': 'application/json'}, {'token': token}, 400),
            ('state', 'POST', {'Content-Type': 'text/plain'}, {'token': token}, 415),
            ('state', 'POST', {'Content-Type': 'application/json', 'Origin': 'https://other.example'}, {'token': token}, 403),
            ('state', 'POST', {'Content-Type': 'application/json', 'Sec-Fetch-Site': 'cross-site'}, {'token': token}, 403),
            ('resume', 'POST', {'Content-Type': 'application/json'}, {'token': token}, 404),
        ]:
            with self.assertRaises(Problem) as failure:
                self.service.handle(method, booking.PREFIX + path, headers, body, '127.0.0.1')
            self.assertEqual(status, failure.exception.status)
        self.expect(404, 'state', 'not-a-token')
        self.expect(404, 'state', 'a' * 43)
        self.expect(400, 'state', token, candidateId='other-person')
        self.expect(400, 'book', token, slotId='x', requestId='')

    def test_expiry_revocation_reject_state_book_call(self):
        _, issued, token = self.issue()
        slot = self.slot()
        with self.connect() as db:
            db.execute('UPDATE cp_booking_invitations SET expires=0 WHERE id=?', (issued['invitationId'],))
        self.expect(410, 'state', token)
        self.expect(410, 'book', token, slotId=slot['id'], requestId='request')
        self.expect(410, 'call', token)
        _, issued2, token2 = self.issue()
        with self.connect() as db:
            self.service.revoke(db, issued2['invitationId'])
            self.assertFalse(self.service.is_sendable(db, issued2['invitationId']))
        self.expect(410, 'state', token2)

    def test_booking_transaction_attributes_and_notifies_exact_slot_owner(self):
        cid, issued, token = self.issue()
        slot = self.slot(recruiter='Первый')
        other = self.slot(recruiter='Второй')
        owner = self.rows('SELECT recruiter_id FROM cp_recruiter_slot_owners WHERE slot_id=?', (slot['id'],))[0][0]
        state = self.book(token, slot)
        self.assertEqual('booked', state['status'])
        self.assertEqual([], state['slots'])
        self.assertEqual('Первый', state['booking']['recruiterName'])
        self.assertNotIn('room', state['booking'])
        self.assertEqual([(issued['invitationId'], 'campaign-1', 'recipient-' + cid)],
            self.rows('SELECT invitation_id,campaign_id,recipient_id FROM cp_booking_attribution'))
        self.assertEqual([(owner, 'email'), (owner, 'telegram')],
                         self.rows('SELECT recruiter_id,channel FROM rn_outbox ORDER BY channel'))
        self.assertEqual([(None,)], self.rows('SELECT candidate_id FROM cp_recruiter_slots WHERE id=?', (other['id'],)))
        self.assertEqual([('booking.created', state['booking']['id'])],
                         self.rows('SELECT kind,interview_id FROM booking_test_events'))
        self.assertEqual([(None, None)], self.rows('SELECT email,password_hash FROM cp_candidates WHERE id=?', (cid,)))
        self.assertEqual([], self.rows('SELECT * FROM cp_consent_log'))
        self.assertEqual([], self.rows('SELECT * FROM cp_media_consents'))

    def test_retries_keep_one_booking_one_attribution_one_notification_per_channel(self):
        _, _, token = self.issue()
        slot = self.slot()
        first = self.book(token, slot)
        self.assertEqual(first, self.book(token, slot))
        self.assertEqual(first, self.book(token, slot, 'request-2'))
        self.assertEqual(1, len(self.rows('SELECT id FROM cp_interviews')))
        self.assertEqual(1, len(self.rows('SELECT interview_id FROM cp_booking_attribution')))
        self.assertEqual(1, len(self.rows('SELECT * FROM booking_test_events')))
        self.assertEqual(2, len(self.rows('SELECT id FROM rn_outbox')))
        self.assertEqual(2, len(self.rows('SELECT request_id FROM cp_booking_requests')))

    def test_same_request_cannot_change_slot_and_person_cannot_book_twice(self):
        _, _, token = self.issue()
        first, second = self.slot(), self.slot(minutes=30)
        self.book(token, first)
        self.expect(409, 'book', token, slotId=second['id'], requestId='request-1')
        self.expect(409, 'book', token, slotId=second['id'], requestId='request-2')
        self.assertEqual([(None,)], self.rows('SELECT candidate_id FROM cp_recruiter_slots WHERE id=?', (second['id'],)))

    def test_two_candidates_race_for_slot_across_separate_portal_locks(self):
        _, _, one = self.issue()
        _, _, two = self.issue()
        slot = self.slot()
        # A second process would not share the Python lock: DB transaction must protect it.
        second_portal = type(self.portal)(self.connect, threading.RLock(), self.media,
                                         self.portal.origin, fixture.FakeNotifications)
        second_service = booking.CandidateBooking(second_portal, self.event)
        gate = threading.Barrier(2)
        def attempt(args):
            service, token = args
            gate.wait()
            try:
                service.handle('POST', booking.PREFIX + 'book', {'Content-Type': 'application/json'},
                               {'token': token, 'slotId': slot['id'], 'requestId': 'concurrent'}, '127.0.0.1')
                return 200
            except Problem as error:
                return error.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, [(self.service, one), (second_service, two)]))
        self.assertEqual([200, 409], sorted(outcomes))
        self.assertEqual(1, len(self.rows('SELECT id FROM cp_interviews')))
        self.assertEqual(1, len(self.rows('SELECT interview_id FROM cp_booking_attribution')))
        self.assertEqual(2, len(self.rows('SELECT id FROM rn_outbox')))

    def test_one_person_two_campaign_links_do_not_double_attribute(self):
        cid, first, one = self.issue()
        _, _, two = self.issue(cid, campaign='campaign-2', recipient='recipient-2')
        slot = self.slot()
        self.book(one, slot)
        self.book(two, slot)
        self.assertEqual([(first['invitationId'],)], self.rows('SELECT invitation_id FROM cp_booking_attribution'))
        self.assertEqual(1, len(self.rows('SELECT * FROM booking_test_events')))

    def test_existing_recruiter_booking_is_not_attributed_to_later_click(self):
        cid, _, token = self.issue()
        slot = self.slot()
        self.call('recruiter/interview/book', {'candidateId': cid, 'slotId': slot['id']}, manager=True)
        state = self.book(token, slot)
        self.assertEqual('booked', state['status'])
        self.assertEqual([], self.rows('SELECT * FROM cp_booking_attribution'))
        self.assertEqual([], self.rows('SELECT * FROM booking_test_events'))

    def test_event_failure_rolls_back_booking_notifications_and_attribution(self):
        _, _, token = self.issue()
        slot = self.slot()
        def fail(*args, **kwargs):
            raise RuntimeError('synthetic event failure')
        self.service.event_sink = fail
        with self.assertRaises(RuntimeError):
            self.book(token, slot)
        for table in ('cp_interviews', 'cp_booking_attribution', 'cp_booking_requests', 'rn_outbox'):
            self.assertEqual([], self.rows('SELECT * FROM ' + table))
        self.assertEqual([(None,)], self.rows('SELECT candidate_id FROM cp_recruiter_slots WHERE id=?', (slot['id'],)))

    def test_disabled_unowned_or_non_half_hour_slot_cannot_be_booked(self):
        _, _, token = self.issue()
        disabled = self.slot(recruiter='Неактивный')
        unowned = self.slot(minutes=30, recruiter='Без владельца')
        long = self.slot(minutes=60, recruiter='Длинный слот')
        self.request('state', token)
        with self.connect() as db:
            db.execute('UPDATE rc_accounts SET active=0 WHERE id IN (SELECT recruiter_id '
                       'FROM cp_recruiter_slot_owners WHERE slot_id=?)', (disabled['id'],))
            db.execute('DELETE FROM cp_recruiter_slot_owners WHERE slot_id=?', (unowned['id'],))
            db.execute('UPDATE cp_recruiter_slots SET end=? WHERE id=?',
                       (iso(self.tick + dt.timedelta(minutes=120)), long['id']))
        for slot in (disabled, unowned, long):
            self.expect(409, 'book', token, slotId=slot['id'], requestId=slot['id'])
        self.assertEqual([], self.rows('SELECT id FROM cp_interviews'))

    def test_removed_cv_stops_send_and_booking_without_fabricating_resume(self):
        cid, issued, token = self.issue()
        slot = self.slot()
        with self.connect() as db:
            db.execute('DELETE FROM cp_resumes WHERE candidate_id=?', (cid,))
            self.assertFalse(self.service.current(db, issued['invitationId']))
        self.expect(409, 'state', token)
        self.expect(409, 'book', token, slotId=slot['id'], requestId='request')

    def test_booking_token_does_not_grant_call_or_claim_or_session_before_booking(self):
        _, _, token = self.issue()
        self.expect(409, 'call', token)
        for scope in ('call', 'claim'):
            with self.assertRaises(Problem) as failure, self.connect() as db:
                invites.resolve(db, token, scope)
            self.assertEqual(410, failure.exception.status)
        with self.assertRaises(Problem) as failure:
            self.call('candidate/profile', {'about': 'Forbidden'}, Authorization='Bearer ' + token)
        self.assertEqual(401, failure.exception.status)
        self.assertEqual([], self.rows('SELECT * FROM cp_invitations'))

    def test_call_is_separate_scoped_stable_guest_url_without_media_operation(self):
        cid, _, token = self.issue()
        state = self.book(token, self.slot())
        result, headers = self.request('call', token)
        again, _ = self.request('call', token)
        self.assertEqual(result, again)
        self.assertIn('cp=guest', result['url'])
        guest = parse_qs(urlsplit(result['url']).fragment)['invite'][0]
        self.assertNotEqual(token, guest)
        with self.connect() as db:
            capability = invites.resolve(db, guest, 'call')
        self.assertEqual(cid, capability['candidateId'])
        self.assertEqual(state['booking']['id'], capability['interviewId'])
        self.assertNotIn('Set-Cookie', headers)
        self.assertEqual([], self.media.tokens)
        self.assertEqual([], self.media.rooms)
        self.assertEqual(1, len(self.rows('SELECT * FROM cp_invitations')))
        self.assertEqual([], self.rows('SELECT * FROM cp_recording_control'))

    def test_revoked_guest_is_not_rotated_by_public_booking_link(self):
        cid, _, token = self.issue()
        self.book(token, self.slot())
        self.request('call', token)
        with self.connect() as db:
            invites.revoke(db, cid, 'call')
        self.expect(410, 'call', token)
        self.assertEqual(1, len(self.rows('SELECT * FROM cp_invitations')))

    def test_manual_guest_link_is_preserved_instead_of_silently_revoked(self):
        cid, _, token = self.issue()
        self.book(token, self.slot())
        with self.connect() as db:
            manual = invites.issue(db, cid, 'call', self.portal.origin)
        self.expect(409, 'call', token)
        self.assertEqual([(None,)], self.rows('SELECT revoked_at FROM cp_invitations'))
        self.portal.invitation_lookup = lambda db, person, scope: manual
        response, _ = self.request('call', token)
        self.assertEqual(manual['url'], response['url'])
        self.assertEqual(1, len(self.rows('SELECT * FROM cp_invitations')))

    def test_completed_interview_cannot_open_call_or_start_new_booking(self):
        _, _, token = self.issue()
        slot = self.slot()
        self.book(token, slot)
        with self.connect() as db:
            db.execute("UPDATE cp_interviews SET status='completed'")
        self.assertFalse(self.request('state', token)[0]['booking']['canOpenCall'])
        self.expect(409, 'call', token)

    def test_recruiter_ordinary_copy_reuses_booking_link_and_explicit_replace_updates_it(self):
        cid, _, token = self.issue()
        self.book(token, self.slot())
        original, _ = self.request('call', token)
        copied, _ = self.call('recruiter/invitations', {'candidateId': cid, 'scope': 'call'}, manager=True)
        self.assertEqual(original['url'], copied['url'])
        self.assertEqual(1, len(self.rows('SELECT * FROM cp_invitations')))
        replaced, _ = self.call('recruiter/invitations', {'candidateId': cid, 'scope': 'call', 'replace': True}, manager=True)
        self.assertNotEqual(original['url'], replaced['url'])
        current, _ = self.request('call', token)
        self.assertEqual(replaced['url'], current['url'])
        old_token = parse_qs(urlsplit(original['url']).fragment)['invite'][0]
        with self.assertRaises(Problem), self.connect() as db:
            invites.resolve(db, old_token, 'call')

    def test_recruiter_creates_first_and_booking_page_uses_exact_same_guest_link(self):
        cid, _, token = self.issue()
        self.book(token, self.slot())
        first, _ = self.call('recruiter/invitations', {'candidateId': cid, 'scope': 'call'}, manager=True)
        recovered, _ = self.request('call', token)
        self.assertEqual(first['url'], recovered['url'])
        self.assertEqual(1, len(self.rows('SELECT * FROM cp_invitations')))

    def test_booked_link_lives_through_future_interview_without_rotating_token(self):
        _, issued, token = self.issue(ttl_seconds=60)
        slot = self.slot(minutes=60 * 24 * 14)
        self.book(token, slot)
        expected = int(dt.datetime.fromisoformat(slot['end'].replace('Z', '+00:00')).timestamp()) + 90 * 60
        self.assertEqual([(expected,)], self.rows('SELECT expires FROM cp_booking_invitations WHERE id=?',
                                                 (issued['invitationId'],)))
        self.assertEqual('booked', self.request('state', token)[0]['status'])


if __name__ == '__main__':
    unittest.main()
