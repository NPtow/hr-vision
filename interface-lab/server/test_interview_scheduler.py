"""Public booking contracts over real SQLite; no real people or delivery."""
import concurrent.futures
import datetime as dt
import threading
import unittest
from unittest.mock import patch

import candidate_booking
import recruiter_availability
from domain import Problem, iso
import test_candidate_booking as fixture


class PooledBookingTest(unittest.TestCase):
    setUp = fixture.CandidateBookingTest.setUp
    tearDown = fixture.CandidateBookingTest.tearDown
    connect = fixture.CandidateBookingTest.connect
    payload = fixture.CandidateBookingTest.payload
    call = fixture.CandidateBookingTest.call
    not_manager = staticmethod(fixture.CandidateBookingTest.not_manager)
    register = fixture.CandidateBookingTest.register
    slot = fixture.CandidateBookingTest.slot
    event = staticmethod(fixture.CandidateBookingTest.event)
    prepared = fixture.CandidateBookingTest.prepared
    issue = fixture.CandidateBookingTest.issue
    request = fixture.CandidateBookingTest.request
    rows = fixture.CandidateBookingTest.rows

    def public_window(self, token, start):
        state, _ = self.request('state', token)
        return next(slot for slot in state['slots'] if slot['start'] == start)

    def book_window(self, token, window, request='pooled-request'):
        return self.request('book', token, slotId=window['id'], requestId=request)[0]

    def test_both_public_entries_return_one_anonymous_time_for_two_recruiters(self):
        _, _, token = self.issue()
        first, second = self.slot(recruiter='One'), self.slot(recruiter='Two')
        _, cookie = self.register()
        invite, _ = self.request('state', token)
        account, _ = self.call('candidate/slots', cookie=cookie)
        self.assertEqual(1, len(invite['slots']))
        self.assertEqual(invite['slots'], account['slots'])
        window = invite['slots'][0]
        self.assertEqual({'id', 'start', 'end'}, set(window))
        self.assertNotIn(window['id'], (first['id'], second['id']))

    def test_pool_prefers_lower_workload_and_retries_never_reassign(self):
        first = self.slot(recruiter='One')
        second = self.slot(recruiter='Two')
        self.slot(minutes=30, recruiter='One')
        self.slot(minutes=30, recruiter='Two')
        loaded, _, loaded_token = self.issue()
        # Existing operator booking gives One a load of 1/2, Two is still 0/2.
        with self.connect() as db:
            self.portal.book(db, loaded, {'slotId': first['id']}, prepared=True)
        cid, _, token = self.issue()
        window = self.public_window(token, iso(self.tick + dt.timedelta(minutes=30)))
        booked = self.book_window(token, window)
        self.assertEqual('Two', booked['booking']['recruiterName'])
        self.assertEqual(booked, self.book_window(token, window))
        self.assertEqual(booked, self.book_window(token, window, 'new-request'))
        owner = self.rows('SELECT recruiter_id FROM cp_recruiter_slot_owners WHERE slot_id=?', (second['id'],))[0][0]
        self.assertEqual([(owner, 'email'), (owner, 'telegram')], self.rows(
            'SELECT recruiter_id,channel FROM rn_outbox WHERE interview_id=? ORDER BY channel', (booked['booking']['id'],)))
        self.assertEqual(1, len(self.rows('SELECT id FROM cp_interviews WHERE candidate_id=?', (cid,))))
        self.assertEqual(1, len(self.rows('SELECT * FROM cp_booking_attribution')))

    def test_fairness_uses_capacity_not_just_number_of_meetings(self):
        self.tick = self.tick.replace(hour=10, minute=0, second=0) + dt.timedelta(days=1)
        one = [self.slot(minutes=i*30, recruiter='One') for i in range(4)]
        two = [self.slot(minutes=i*30, recruiter='Two') for i in range(8)]
        for slot in (one[0], two[0], two[1]):
            cid = self.prepared()
            with self.connect() as db:
                self.portal.book(db, cid, {'slotId': slot['id']}, prepared=True)
        # Equal utilization 1/4 vs 2/8: older last assignment wins (One).
        _, _, token = self.issue()
        window = self.public_window(token, one[2]['start'])
        self.assertEqual('One', self.book_window(token, window)['booking']['recruiterName'])
        # Now 2/4 vs 2/8: Two gets the next shared time.
        _, _, token2 = self.issue()
        window2 = self.public_window(token2, one[3]['start'])
        self.assertEqual('Two', self.book_window(token2, window2)['booking']['recruiterName'])

    def test_inactive_recruiter_does_not_receive_assignments(self):
        first, second = self.slot(recruiter='One'), self.slot(recruiter='Two')
        _, _, token = self.issue()
        window = self.public_window(token, first['start'])
        owner = self.rows('SELECT recruiter_id FROM cp_recruiter_slot_owners WHERE slot_id=?', (first['id'],))[0][0]
        with self.connect() as db:
            db.execute('UPDATE rc_accounts SET active=0 WHERE id=?', (owner,))
        self.assertEqual('Two', self.book_window(token, window)['booking']['recruiterName'])
        _, _, next_token = self.issue()
        self.assertEqual([], self.request('state', next_token)[0]['slots'])

    def test_two_processes_use_two_owners_but_third_gets_conflict(self):
        first, second = self.slot(recruiter='One'), self.slot(recruiter='Two')
        _, _, one = self.issue()
        _, _, two = self.issue()
        _, _, three = self.issue()
        window = self.public_window(one, first['start'])
        portal2 = type(self.portal)(self.connect, threading.RLock(), self.media, self.portal.origin)
        service2 = candidate_booking.CandidateBooking(portal2, self.event)
        gate = threading.Barrier(2)
        def attempt(item):
            service, token = item
            gate.wait()
            return service.handle('POST', '/api/hr/booking/book', {'Content-Type': 'application/json'},
                {'token': token, 'slotId': window['id'], 'requestId': 'race', 'email': 'candidate@example.test',
                 'consentProfile': True}, '127.0.0.1')[0]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, [(self.service, one), (service2, two)]))
        self.assertEqual({'One', 'Two'}, {item['booking']['recruiterName'] for item in results})
        with self.assertRaises(Problem) as raised:
            self.book_window(three, window)
        self.assertEqual(409, raised.exception.status)
        self.assertEqual(2, len(self.rows('SELECT id FROM cp_interviews')))
        self.assertEqual(4, len(self.rows('SELECT id FROM rn_outbox')))

    def test_authenticated_booking_uses_same_pool_and_is_idempotent(self):
        first, second = self.slot(recruiter='One'), self.slot(recruiter='Two')
        candidate, cookie = self.register()
        pool, _ = self.call('candidate/slots', cookie=cookie)
        booked, _ = self.call('candidate/book', {'slotId': pool['slots'][0]['id']}, cookie)
        again, _ = self.call('candidate/book', {'slotId': pool['slots'][0]['id']}, cookie)
        self.assertEqual(booked['interview']['id'], again['interview']['id'])
        self.assertEqual(1, len(self.rows('SELECT id FROM cp_interviews WHERE candidate_id=?', (candidate['id'],))))
        # The same pooled time remains available through the other recruiter.
        self.assertEqual(pool['slots'], self.call('candidate/slots', cookie=cookie)[0]['slots'])

    def test_closed_date_and_overlap_cannot_be_bypassed_with_a_stale_time(self):
        first = self.slot(recruiter='One')
        self.slot(recruiter='Two')
        _, _, token = self.issue()
        window = self.public_window(token, first['start'])
        owner = self.rows('SELECT recruiter_id FROM cp_recruiter_slot_owners WHERE slot_id=?', (first['id'],))[0][0]
        day = self.tick.astimezone(recruiter_availability.MOSCOW).date().isoformat()
        with self.connect() as db:
            recruiter_availability.set_date_override(db, {'date': day, 'intervals': []}, recruiter={'id': owner})
        self.assertEqual('Two', self.book_window(token, window)['booking']['recruiterName'])
        _, _, another = self.issue()
        with self.assertRaises(Problem) as error:
            self.book_window(another, window)
        self.assertEqual(409, error.exception.status)

    def test_overlapping_booked_slot_excludes_the_owner(self):
        first = self.slot(recruiter='One')
        self.slot(recruiter='Two')
        owner = self.rows('SELECT recruiter_id FROM cp_recruiter_slot_owners WHERE slot_id=?', (first['id'],))[0][0]
        overlap_id = 'historical-overlap'
        # Seed an existing commitment even though the generated free pool would
        # expose only one of these overlapping manual times.
        cid = self.prepared()
        with self.connect() as db:
            db.execute('INSERT INTO cp_recruiter_slots VALUES (?,?,?,?,?)',
                       (overlap_id, iso(self.tick + dt.timedelta(minutes=15)),
                        iso(self.tick + dt.timedelta(minutes=45)), 'One', cid))
            db.execute('INSERT INTO cp_recruiter_slot_owners VALUES (?,?)', (overlap_id, owner))
            db.execute("INSERT INTO cp_interviews VALUES ('old-interview',?,?,'booked',NULL,?,NULL)",
                       (cid, overlap_id, iso(self.tick)))
        _, _, token = self.issue()
        window = self.public_window(token, first['start'])
        self.assertEqual('Two', self.book_window(token, window)['booking']['recruiterName'])

    def test_same_instant_with_different_offsets_is_one_window(self):
        first, second = self.slot(recruiter='One'), self.slot(recruiter='Two')
        with self.connect() as db:
            db.execute('UPDATE cp_recruiter_slots SET start=?,end=? WHERE id=?', (
                self.tick.astimezone(recruiter_availability.MOSCOW).isoformat(),
                (self.tick + dt.timedelta(minutes=30)).astimezone(recruiter_availability.MOSCOW).isoformat(), second['id']))
        _, _, token = self.issue()
        slots = self.request('state', token)[0]['slots']
        self.assertEqual(1, len(slots))
        self.assertEqual(first['start'], slots[0]['start'])

    def test_outbox_failure_rolls_back_reservation_and_all_attribution(self):
        first = self.slot()
        _, _, token = self.issue()
        window = self.public_window(token, first['start'])
        with patch('recruiter_notifications.enqueue_booking', side_effect=RuntimeError('outbox unavailable')):
            with self.assertRaises(RuntimeError):
                self.book_window(token, window)
        self.assertEqual([], self.rows('SELECT id FROM cp_interviews'))
        self.assertEqual([], self.rows('SELECT * FROM cp_booking_attribution'))
        self.assertEqual([], self.rows('SELECT * FROM cp_booking_profile_consents'))
        self.assertEqual([(None,)], self.rows('SELECT candidate_id FROM cp_recruiter_slots'))
        self.assertEqual('booked', self.book_window(token, window)['status'])

    def test_weekly_capacity_extends_beyond_materialized_horizon(self):
        # The horizon ends on Monday, but Two also works the Friday beyond it.
        import interview_scheduler
        fixed = dt.datetime(2030, 1, 8, 6, tzinfo=dt.timezone.utc)
        monday = dt.date(2030, 2, 4)
        owners = []
        with self.connect() as db:
            for name in ('One', 'Two'):
                from test_candidate_portal import assign_fixture_slot
                sid = 'capacity-' + name
                db.execute('INSERT INTO cp_recruiter_slots VALUES (?,?,?,?,NULL)',
                           (sid, '2030-02-04T07:00:00Z', '2030-02-04T07:30:00Z', name))
                assign_fixture_slot(db, {'id': sid, 'recruiterName': name})
                owners.append(db.execute('SELECT recruiter_id FROM cp_recruiter_slot_owners WHERE slot_id=?', (sid,)).fetchone()[0])
        with patch('recruiter_availability.now', return_value=fixed), patch('interview_scheduler.now', return_value=fixed):
            for index, name in enumerate(('One', 'Two')):
                cid = self.prepared()
                with self.connect() as db:
                    db.execute('UPDATE cp_recruiter_slots SET candidate_id=? WHERE id=?', (cid, 'capacity-' + name))
                    db.execute("INSERT INTO cp_interviews VALUES (?,?,?,'booked',NULL,?,NULL)",
                               ('capacity-interview-' + name, cid, 'capacity-' + name, iso(fixed)))
                    recruiter_availability.add_block(db, {'recruiterName': name, 'startDate': '2030-01-08',
                        'weekdays': [0] if index == 0 else [0, 4], 'startTime': '10:00', 'endTime': '11:00',
                        'timezone': 'Europe/Moscow'}, recruiter={'id': owners[index], 'name': name})
            with self.connect() as db:
                key = interview_scheduler.window_id('2030-02-04T07:30:00Z', '2030-02-04T08:00:00Z')
                chosen = interview_scheduler.select_slot(db, key)
                owner = db.execute('SELECT recruiter_id FROM cp_recruiter_slot_owners WHERE slot_id=?', (chosen,)).fetchone()[0]
                self.assertEqual(owners[1], owner)


if __name__ == '__main__':
    unittest.main()
