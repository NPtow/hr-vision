"""Real SQLite protocol; synthetic CVs/capabilities only, no provider requests."""
import concurrent.futures
import json
import unittest
from domain import Problem
from candidate_booking import CandidateBooking
from urllib.parse import urlsplit, parse_qs
from hh_outreach import HHOutreach, digest
from hh_routes import HHRecruiterRoutes
import test_candidate_portal as fixture

class OutreachTest(unittest.TestCase):
    connect = fixture.CandidatePortalTest.connect
    call = fixture.CandidatePortalTest.call
    not_manager = staticmethod(fixture.CandidatePortalTest.not_manager)
    slot = fixture.CandidatePortalTest.slot

    def setUp(self):
        fixture.CandidatePortalTest.setUp(self)
        self.time = 2000000000.0
        self.hh = HHRecruiterRoutes(self.connect, self.portal)
        with self.connect() as db:
            db.execute('CREATE TABLE fake_booking(id TEXT PRIMARY KEY,cid TEXT,valid INTEGER)')
        self.engine = self.new_engine()

    def tearDown(self):
        fixture.CandidatePortalTest.tearDown(self)

    def new_engine(self, enabled=True):
        return HHOutreach(self.connect, issue_booking=self.issue, booking_current=self.current,
            prepare_candidate=self.hh.prepare_candidate, clock=lambda: self.time, sending_enabled=enabled)

    @staticmethod
    def issue(db, cid, campaign_id, recipient_id):
        iid = 'booking_' + recipient_id
        db.execute('INSERT INTO fake_booking VALUES (?,?,1)', (iid, cid))
        return {'invitationId': iid, 'url': 'https://portal.example/booking#booking=' + recipient_id, 'expiresAt': '2034-01-01T00:00:00Z'}

    @staticmethod
    def current(db, iid):
        return bool(db.execute('''SELECT 1 FROM fake_booking b WHERE b.id=? AND b.valid=1
          AND NOT EXISTS(SELECT 1 FROM cp_interviews i WHERE i.candidate_id=b.cid)''', (iid,)).fetchone())

    def campaign(self, **changes):
        return self.engine.create_campaign({'name': 'Synthetic campaign', 'accountId': 'account1',
            'mode': 'direct', 'template': 'Здравствуйте, {name}! Выберите время: {booking_url}',
            'limit': 3, 'paceSeconds': 60, **changes})['id']

    @staticmethod
    def record(n=1, **changes):
        return {'accountId': 'account1', 'resumeId': 'resume' + str(n), 'chatId': 'chat' + str(n),
            'resumeUrl': 'https://hh.ru/resume/resume' + str(n),
            'chatUrl': 'https://hh.ru/employer/chat?chatId=chat' + str(n),
            'name': 'Synthetic Person', 'profile': {'name': 'Synthetic Person', 'city': 'Москва'},
            'cvAvailable': True, 'cvText': 'Synthetic Person\nОпыт работы: тестовая роль', **changes}

    def ready(self, count=1, **changes):
        cid = self.campaign(**changes)
        self.engine.import_recipients(cid, [self.record(n) for n in range(1, count + 1)])
        self.engine.queue(cid)
        self.engine.control(cid, 'resume')
        return cid

    def evidence(self, job, **changes):
        return {key: job[key] for key in ('accountId', 'resumeId', 'chatId', 'messageHash')} | {
            'messageId': 'observed123', 'persisted': True, 'observedAt': self.time, **changes}

    def test_import_stable_identity_preserves_candidate_edits(self):
        cid = self.campaign(mode='applicants', vacancyId='vac1')
        raw = self.record(responseId='response1', vacancyId='vac1')
        self.assertEqual({'imported': 1, 'repeated': 0}, self.engine.import_recipients(cid, [raw]))
        person = self.engine.detail(cid)['recipients'][0]['candidateId']
        with self.connect() as db:
            db.execute('UPDATE cp_candidates SET profile=? WHERE id=?', (json.dumps({'name': 'Edited'}), person))
        self.assertEqual({'imported': 0, 'repeated': 1}, self.engine.import_recipients(cid, [raw]))
        with self.connect() as db:
            self.assertEqual(1, db.execute('SELECT COUNT(*) FROM cp_candidates').fetchone()[0])
            row = db.execute('SELECT email,password_hash,profile FROM cp_candidates WHERE id=?', (person,)).fetchone()
            self.assertEqual((None, None), row[:2])
            self.assertEqual('Edited', json.loads(row[2])['name'])
        with self.assertRaises(Problem):
            self.engine.import_recipients(cid, [self.record(2, responseId='response2', vacancyId='other')])

    def test_identical_names_not_identity_missing_cv_later_import(self):
        cid = self.campaign()
        self.engine.import_recipients(cid, [self.record(cvAvailable=False, cvText=''), self.record(2)])
        people = self.engine.detail(cid)['recipients']
        self.assertNotEqual(people[0]['candidateId'], people[1]['candidateId'])
        self.assertEqual('cv_missing', self.engine.queue(cid)['skipped'][0]['reason'])
        self.engine.import_recipients(cid, [self.record()])
        self.assertEqual(1, len(self.engine.queue(cid)['queued']))

    def test_rejects_identity_changes_consent_cross_account_and_bad_urls(self):
        cid = self.campaign()
        self.engine.import_recipients(cid, [self.record()])
        for raw in [self.record(chatId='other'), self.record(2, accountId='other'),
            self.record(2, profile={'name': 'Person', 'consentReuse': True}),
            self.record(2, chatUrl='https://hh.ru.evil.example/chat'),
            self.record(2, chatUrl='https://x:secret@hh.ru/chat'), self.record(2, resumeUrl='file:///etc/passwd')]:
            with self.subTest(raw=raw), self.assertRaises(Problem):
                self.engine.import_recipients(cid, [raw])
        self.assertEqual(1, len(self.engine.detail(cid)['recipients']))

    def test_template_limits_and_draft(self):
        for template in ('no link', '{booking_url.__class__}', '{booking_url!r}', '{booking_url}{booking_url}', '{bad} {booking_url}'):
            with self.subTest(template=template), self.assertRaises(Problem):
                self.campaign(template=template)
        for changes in ({'limit': True}, {'limit': 0}, {'paceSeconds': 1}, {'mode': 'all'}):
            with self.subTest(changes=changes), self.assertRaises(Problem):
                self.campaign(**changes)
        self.assertEqual('draft', self.engine.campaign(self.campaign())['status'])

    def test_queue_immutable_snapshot_limit_cross_campaign_dedupe_public_redaction(self):
        cid = self.campaign(limit=1)
        self.engine.import_recipients(cid, [self.record(), self.record(2)])
        result = self.engine.queue(cid)
        self.assertEqual(1, len(result['queued']))
        self.assertEqual('limit_reached', result['skipped'][0]['reason'])
        self.assertEqual([], self.engine.queue(cid)['queued'])
        other = self.campaign()
        self.engine.import_recipients(other, [self.record()])
        self.assertEqual('duplicate', self.engine.queue(other)['skipped'][0]['reason'])
        with self.connect() as db:
            before = db.execute('SELECT message,booking_url,message_hash FROM ho_outbox').fetchone()
        self.engine.control(cid, 'resume')
        job = self.engine.claim('account1', 'worker')
        self.assertEqual(before, (job['message'], job['bookingUrl'], job['messageHash']))
        self.assertEqual(digest(job['message']), job['messageHash'])
        public = json.dumps(self.engine.detail(cid))
        self.assertNotIn(job['bookingUrl'], public)
        self.assertNotIn(job['claimToken'], public)

    def test_one_durable_account_lease_under_concurrency(self):
        self.ready(count=3)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda n: self.new_engine().claim('account1', 'worker' + str(n)), range(8)))
        self.assertEqual(1, sum(item is not None for item in results))
        self.assertIsNone(self.engine.claim('account1', 'worker9'))

    def test_disabled_pause_race_and_invalid_booking_prevent_start(self):
        cid = self.ready()
        self.assertIsNone(self.new_engine(enabled=False).claim('account1', 'worker'))
        self.engine.control(cid, 'pause')
        self.assertIsNone(self.engine.claim('account1', 'worker'))
        self.engine.control(cid, 'resume')
        job = self.engine.claim('account1', 'worker')
        self.engine.control(cid, 'pause')
        with self.assertRaises(Problem):
            self.engine.start(job['jobId'], job['claimToken'])
        self.engine.control(cid, 'resume')
        with self.connect() as db:
            db.execute('UPDATE fake_booking SET valid=0')
        with self.assertRaises(Problem):
            self.engine.start(job['jobId'], job['claimToken'])
        with self.connect() as db:
            self.assertIsNone(db.execute('SELECT started FROM ho_attempts').fetchone()[0])

    def test_crash_before_click_reclaimed_after_started_unknown_never_blind_retry(self):
        cid = self.ready(count=2)
        job = self.engine.claim('account1', 'worker')
        self.time += 301
        again = self.new_engine().claim('account1', 'restarted')
        self.assertEqual(job['jobId'], again['jobId'])
        self.assertNotEqual(job['claimToken'], again['claimToken'])
        with self.assertRaises(Problem):
            self.engine.start(job['jobId'], job['claimToken'])
        self.engine.start(again['jobId'], again['claimToken'])
        with self.assertRaises(Problem):
            self.engine.start(again['jobId'], again['claimToken'])
        self.time += 301
        self.assertEqual({'unknown': 1}, self.new_engine(enabled=False).recover_account('account1'))
        self.assertIsNone(self.new_engine().claim('account1', 'restarted'))
        self.assertEqual(1, self.engine.detail(cid)['stats']['unknown'])
        self.engine.unblock_account('account1')
        self.assertNotEqual(again['jobId'], self.engine.claim('account1', 'worker')['jobId'])
        with self.assertRaises(Problem):
            self.engine.retry_blocked(again['jobId'])
        self.engine.reconcile(again['jobId'], self.evidence(again))
        self.assertEqual(1, self.engine.detail(cid)['stats']['sentObserved'])
        self.assertEqual(0, self.engine.detail(cid)['stats']['unknown'])

    def test_evidence_requires_exact_chat_identity_message_persisted_proof(self):
        cid = self.ready(count=2)
        job = self.engine.claim('account1', 'worker')
        with self.assertRaises(Problem):
            self.engine.result(job['jobId'], job['claimToken'], 'sent_observed', evidence=self.evidence(job))
        self.engine.start(job['jobId'], job['claimToken'])
        for changes in ({key: 'wrong'} for key in ('accountId', 'resumeId', 'chatId', 'messageHash')):
            with self.subTest(changes=changes), self.assertRaises(Problem):
                self.engine.result(job['jobId'], job['claimToken'], 'sent_observed', evidence=self.evidence(job, **changes))
        for changes in ({'persisted': False}, {'messageId': None}, {'observedAt': float('nan')}):
            with self.subTest(changes=changes), self.assertRaises(Problem):
                self.engine.result(job['jobId'], job['claimToken'], 'sent_observed', evidence=self.evidence(job, **changes))
        self.engine.result(job['jobId'], job['claimToken'], 'sent_observed', evidence=self.evidence(job))
        self.assertEqual(1, self.engine.detail(cid)['stats']['sentObserved'])
        self.assertIsNone(self.engine.claim('account1', 'worker'))
        self.time += 60
        self.assertIsNotNone(self.engine.claim('account1', 'worker'))

    def test_pre_start_block_retry_after_start_block_means_unknown(self):
        self.ready(count=2)
        job = self.engine.claim('account1', 'worker')
        self.engine.result(job['jobId'], job['claimToken'], 'blocked', reason='logged_out')
        self.assertIsNone(self.engine.claim('account1', 'worker'))
        self.engine.retry_blocked(job['jobId'])
        self.engine.unblock_account('account1')
        job = self.engine.claim('account1', 'worker')
        self.engine.start(job['jobId'], job['claimToken'])
        self.assertEqual('unknown', self.engine.result(job['jobId'], job['claimToken'], 'blocked', reason='challenge')['state'])
        with self.assertRaises(Problem):
            self.engine.retry_blocked(job['jobId'])

    def test_unique_attribution_completed_not_live_and_separate_media(self):
        cid = self.ready()
        recipient = self.engine.detail(cid)['recipients'][0]
        rid, person = recipient['id'], recipient['candidateId']
        with self.engine.db(write=True) as db:
            self.engine.event_sink(db, 'booking.page_loaded', rid, event_key='page:first')
            self.assertFalse(self.engine.event_sink(db, 'booking.page_loaded', rid, event_key='page:first'))
            db.execute("INSERT INTO cp_recruiter_slots VALUES ('slot','2034-01-01','2034-01-02','Synthetic',?)", (person,))
            db.execute("INSERT INTO cp_interviews VALUES ('interview',?,'slot','live',NULL,'2034-01-01',NULL)", (person,))
            self.engine.event_sink(db, 'booking.created', rid, interview_id='interview', event_key='booking:interview')
            db.execute("UPDATE cp_interviews SET room=? WHERE id='interview'", (json.dumps({'id': 'room1', 'name': 'room-one', 'url': 'https://test.daily.co/room-one'}),))
            db.execute("INSERT INTO cp_media_state VALUES ('interview','interview',?,?, '2034-01-01')", (json.dumps({'id': 'room1', 'name': 'room-one'}), json.dumps({'status': 'ready', 'video': {'status': 'processing', 'ids': []}, 'transcription': {'status': 'ready', 'ids': ['transcript1']}}),))
        stats = self.engine.detail(cid)['stats']
        self.assertEqual((1, 1, 0, 0, 1), (stats['pageLoaded'], stats['booked'], stats['completed'], stats['videoReady'], stats['transcriptReady']))
        with self.connect() as db:
            db.execute("UPDATE cp_interviews SET status='completed' WHERE id='interview'")
        self.assertEqual(1, self.engine.detail(cid)['stats']['completed'])
        with self.connect() as db:
            db.execute("UPDATE cp_interviews SET room=? WHERE id='interview'", (json.dumps({'id': 'room2', 'name': 'new-room'}),))
        self.assertEqual(0, self.engine.detail(cid)['stats']['transcriptReady'])

    def test_real_booking_is_attributed_once_and_blocks_pending_send(self):
        booking = CandidateBooking(self.portal)
        self.engine = HHOutreach(self.connect, issue_booking=booking.issue, booking_current=booking.current,
            prepare_candidate=self.hh.prepare_candidate, clock=lambda: self.time, sending_enabled=True)
        booking.event_sink = self.engine.event_sink
        slot = self.slot()
        cid = self.ready()
        job = self.engine.claim('account1', 'worker')
        token = parse_qs(urlsplit(job['bookingUrl']).fragment)['booking'][0]
        headers = {'Content-Type': 'application/json'}
        for _ in range(2):
            booking.handle('POST', '/api/hr/booking/state', headers, {'token': token}, '127.0.0.1')
        for _ in range(2):
            result, _ = booking.handle('POST', '/api/hr/booking/book', headers,
                {'token': token, 'slotId': slot['id'], 'requestId': 'request1',
                 'email': 'booking@example.test', 'consentProfile': True}, '127.0.0.1')
            self.assertEqual('booked', result['status'])
        stats = self.engine.detail(cid)['stats']
        self.assertEqual((1, 1), (stats['pageLoaded'], stats['booked']))
        with self.connect() as db:
            self.assertEqual(1, db.execute('SELECT COUNT(*) FROM cp_booking_attribution').fetchone()[0])
            self.assertEqual(1, db.execute('SELECT COUNT(*) FROM cp_interviews').fetchone()[0])
        with self.assertRaises(Problem):
            self.engine.start(job['jobId'], job['claimToken'])

    def test_cancel_before_start_releases_guard_campaign_stays_cancelled(self):
        cid = self.ready()
        self.engine.control(cid, 'cancel')
        other = self.campaign()
        self.engine.import_recipients(other, [self.record()])
        self.assertEqual(1, len(self.engine.queue(other)['queued']))
        with self.assertRaises(Problem):
            self.engine.control(cid, 'resume')

if __name__ == '__main__':
    unittest.main()
