"""Controlled clock/provider, independent SQLite per case; no real sends."""
import concurrent.futures
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import candidate_notifications as delivery
import employer_notifications as employer
import meeting_reminders as reminders
import recruiter_notifications as recruiter


def iso(tick):
    return datetime.fromtimestamp(tick, timezone.utc).isoformat()


class FakeTransport:
    def __init__(self):
        self.sent, self.fail_email = [], None
    def send_email(self, recipient, subject, content, message_id):
        if self.fail_email: raise self.fail_email
        self.sent.append(('email',recipient,content,None)); return 'accepted'
    def send_telegram(self, recipient, content, reply_markup=None):
        self.sent.append(('telegram',recipient,content,reply_markup)); return 'accepted'


class RemindersTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'reminders.sqlite'
        self.tick=2000000000; self.start=self.tick+86400
        self.config=replace(delivery.settings({'HR_ORIGIN':'https://fixture.test'}),enabled=True,
            smtp_host='smtp.test',smtp_from='hr@fixture.test',telegram_token='fake',telegram_username='FixtureBot')
        self.transport=FakeTransport()
        for target in ('candidate_notifications.urllib.request.urlopen','candidate_notifications.smtplib.SMTP','candidate_notifications.smtplib.SMTP_SSL'):
            p=patch(target,side_effect=AssertionError('No real provider calls')); p.start(); self.addCleanup(p.stop)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE cp_candidates(id TEXT PRIMARY KEY,email TEXT,profile TEXT);
                CREATE TABLE rc_accounts(id TEXT PRIMARY KEY,email TEXT,active INTEGER);
                CREATE TABLE cp_intake(candidate_id TEXT,source TEXT);
                CREATE TABLE cp_recruiter_slots(id TEXT PRIMARY KEY,start TEXT,end TEXT,candidate_id TEXT);
                CREATE TABLE cp_recruiter_slot_owners(slot_id TEXT,recruiter_id TEXT);
                CREATE TABLE cp_interviews(id TEXT PRIMARY KEY,candidate_id TEXT,slot_id TEXT,status TEXT,created_at TEXT);
                CREATE TABLE cp_applications(id TEXT PRIMARY KEY,candidate_id TEXT,vacancy_id TEXT,archived_at TEXT);
                CREATE TABLE cp_meetings(id TEXT PRIMARY KEY,application_id TEXT,start TEXT,end TEXT,status TEXT,revision INTEGER,created_at TEXT,confirmed_at TEXT,manager_id TEXT);
                CREATE TABLE em_accounts(id TEXT PRIMARY KEY,workspace_id TEXT,email TEXT,active INTEGER,is_team INTEGER);
                CREATE TABLE em_workspaces(id TEXT PRIMARY KEY,kind TEXT);
                CREATE TABLE em_vacancies(vacancy_id TEXT PRIMARY KEY,workspace_id TEXT,archived_at TEXT);
                CREATE TABLE em_reviews(application_id TEXT PRIMARY KEY,workspace_id TEXT,state TEXT);
                CREATE TABLE cp_booking_profile_consents(interview_id TEXT PRIMARY KEY,candidate_id TEXT,invitation_id TEXT,contact_email TEXT);
                CREATE TABLE cp_booking_invitations(id TEXT PRIMARY KEY,candidate_id TEXT,token_hash TEXT,expires INTEGER,revoked_at TEXT);
                INSERT INTO cp_candidates VALUES ('candidate','candidate@fixture.test','{}');
                INSERT INTO rc_accounts VALUES ('recruiter','recruiter@fixture.test',1),('other','other@fixture.test',1);
                INSERT INTO cp_recruiter_slot_owners VALUES ('slot','recruiter');
                INSERT INTO em_accounts VALUES ('employer','workspace','employer@fixture.test',1,0);
                INSERT INTO em_workspaces VALUES ('workspace','employer');
            ''')
            delivery.init(db); recruiter.init(db); employer.init(db)
            db.execute('INSERT INTO cp_recruiter_slots VALUES (?,?,?,?)',('slot',iso(self.start),iso(self.start+1800),'candidate'))
            db.execute('INSERT INTO cp_interviews VALUES (?,?,?,?,?)',('interview','candidate','slot','booked',iso(self.tick-3600)))
            delivery.set_email(db,'candidate','candidate@fixture.test',verified=True,now=self.tick-3600)
            db.execute("UPDATE cn_recipients SET telegram_chat_id='1001',telegram_verified_at=10")
            recruiter._recipient(db,'recruiter',self.tick)
            db.execute("UPDATE rn_channels SET telegram_chat_id='2001',telegram_verified_at=10")
            db.execute("INSERT INTO en_bindings VALUES ('employer','3001','generation',10)")
    def connect(self): return sqlite3.connect(self.path,timeout=10)
    def change(self,sql,args=()):
        with self.connect() as db: db.execute(sql,args)
    def collect(self,tick=None,config=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE'); reminders.collect(db,config or self.config,self.tick if tick is None else tick)
    def process(self,tick=None,config=None):
        config=config or self.config; tick=self.tick if tick is None else tick
        with patch('employer_notifications.collect'):
            delivery.process_pending(self.connect,config,100,tick,self.transport)
        recruiter.process_pending(self.connect,config,100,tick,self.transport)
    def rows(self):
        with self.connect() as db:
            db.row_factory=sqlite3.Row
            return [dict(r) for table in ('cn_outbox','rn_outbox') for r in db.execute('SELECT * FROM '+table+' WHERE event_type=?',(reminders.EVENT,))]
    def meeting(self):
        with self.connect() as db:
            db.execute('DELETE FROM cp_interviews')
            db.execute("INSERT INTO cp_applications VALUES ('application','candidate','vacancy',NULL)")
            db.execute("INSERT INTO em_vacancies VALUES ('vacancy','workspace',NULL)")
            db.execute("INSERT INTO em_reviews VALUES ('application','workspace','selected')")
            db.execute('INSERT INTO cp_meetings VALUES (?,?,?,?,?,?,?,?,?)',('meeting','application',iso(self.start),iso(self.start+1800),'confirmed',2,iso(self.tick-7200),iso(self.tick-3600),'workspace:workspace'))
    def booking_only(self):
        self.change('UPDATE cp_candidates SET email=NULL')
        self.change('UPDATE cn_recipients SET email_verified=0,telegram_chat_id=NULL,telegram_verified_at=NULL')
        self.link='https://fixture.test/iframe.html?id=hr-vision-product--candidate&cp=invite-booking#booking=fixture-secret'
        with self.connect() as db:
            db.execute('INSERT INTO cp_booking_invitations VALUES (?,?,?,?,NULL)',('invite','candidate',hashlib.sha256(b'fixture-secret').hexdigest(),self.start+9000))
            db.execute("INSERT INTO cp_booking_profile_consents VALUES ('interview','candidate','invite','consent@fixture.test')")
            db.execute("INSERT INTO cn_booking_telegram VALUES ('invite','candidate','4001',10)")
            db.execute('''INSERT INTO cn_outbox(id,candidate_id,event_key,event_type,channel,recipient,created_at,status,next_attempt_at,payload_json)
                VALUES ('confirmation','candidate','booking:invite','booking.confirmed','email','consent@fixture.test',0,'delivered',0,?)''',
                (json.dumps({'invitationId':'invite','interviewId':'interview','url':self.link}),))
    def test_both_channels_both_participants_once_across_restart(self):
        self.process(); self.process(self.tick+60)
        self.assertEqual(len(self.rows()),4); self.assertEqual({r['status'] for r in self.rows()},{'delivered'})
        self.assertEqual({(m[0],m[1]) for m in self.transport.sent},{('email','candidate@fixture.test'),('email','recruiter@fixture.test'),('telegram','1001'),('telegram','2001')})
        self.assertEqual(len(self.transport.sent),4)
        for m in self.transport.sent: self.assertIn('МСК',m[2])
    def test_employer_meeting_both_channels_and_safe_links(self):
        self.meeting(); self.process(); self.assertEqual(len(self.transport.sent),4)
        mail=next(x for x in self.transport.sent if x[:2]==('email','employer@fixture.test'))
        tg=next(x for x in self.transport.sent if x[:2]==('telegram','3001'))
        self.assertNotIn('miniapp=',mail[2])
        query=parse_qs(urlsplit(tg[3]['inline_keyboard'][0][0]['web_app']['url']).query)
        self.assertEqual(query['meetingId'],['meeting']); self.assertEqual(query['miniapp'],['employer']); self.assertEqual(query['applicationId'],['application'])
    def test_due_not_due_ten_minute_catchup(self):
        self.collect(self.tick-1); self.assertEqual(self.rows(),[])
        self.collect(self.tick+600); self.assertEqual(len(self.rows()),4)
    def test_downtime_no_stale_24h_at_15m(self):
        self.collect(self.start-900); self.assertEqual(len(self.rows()),4)
        for row in self.rows(): self.assertEqual(json.loads(row.get('payload_json') or row['payload'])['offset'],900)
    def test_outside_window_and_started(self):
        for tick in (self.tick+601,self.start-299,self.start,self.start+1801): self.collect(tick)
        self.assertEqual(self.rows(),[])
    def test_late_creation_and_confirmation(self):
        self.change('UPDATE cp_interviews SET created_at=?',(iso(self.tick+1),)); self.collect(self.tick+30); self.assertEqual(self.rows(),[])
        self.collect(self.start-900); self.assertEqual(len(self.rows()),4)
        self.change('DELETE FROM cn_outbox'); self.change('DELETE FROM rn_outbox'); self.meeting()
        self.change('UPDATE cp_meetings SET confirmed_at=?',(iso(self.tick+1),)); self.collect(self.tick+30); self.assertEqual(self.rows(),[])
    def test_completed_interview(self):
        self.collect(); self.change("UPDATE cp_interviews SET status='completed'"); self.process()
        self.assertEqual(self.transport.sent,[]); self.assertEqual({r['last_error'] for r in self.rows()},{'reminder_no_longer_current'})
    def test_changed_time_blocks_old_rows(self):
        self.collect(); self.change('UPDATE cp_recruiter_slots SET end=?',(iso(self.start+3600),))
        with patch('meeting_reminders.collect'): self.process()
        self.assertEqual(self.transport.sent,[])
    def test_dispatch_after_window(self):
        self.collect(); self.process(self.tick+601)
        self.assertEqual(self.transport.sent,[]); self.assertEqual({r['last_error'] for r in self.rows()},{'reminder_expired'})
    def test_wrong_recruiter_owner(self):
        self.collect(); self.change("UPDATE cp_recruiter_slot_owners SET recruiter_id='other'")
        with patch('meeting_reminders.collect'): self.process()
        self.assertEqual(self.transport.sent,[])
    def test_wrong_employer_workspace_revision(self):
        self.meeting(); self.collect(); self.change("UPDATE cp_meetings SET manager_id='workspace:other',revision=3"); self.process(); self.assertEqual(self.transport.sent,[])
    def test_cancelled_employer_meeting(self):
        self.meeting(); self.collect(); self.change("UPDATE cp_meetings SET status='cancelled'"); self.process(); self.assertEqual(self.transport.sent,[])
    def test_wrong_email_same_chat_rebind(self):
        self.collect(); self.change("UPDATE cn_recipients SET telegram_verified_at=11,email='changed@fixture.test'")
        self.change('UPDATE rn_channels SET telegram_verified_at=11'); self.change("UPDATE rc_accounts SET email='changed@fixture.test' WHERE id='recruiter'")
        self.process(); self.assertEqual(self.transport.sent,[])
    def test_employer_generation_rebind(self):
        self.meeting(); self.collect(); self.change("UPDATE en_bindings SET generation='new'"); self.process()
        self.assertFalse(any(m[:2]==('telegram','3001') for m in self.transport.sent)); self.assertEqual(len(self.transport.sent),3)
    def test_unbound_disabled_no_replay(self):
        self.change('UPDATE cn_recipients SET telegram_chat_id=NULL,telegram_verified_at=NULL')
        self.collect(config=replace(self.config,smtp_host='')); self.assertEqual({r['status'] for r in self.rows()},{'disabled','unbound','pending'})
        self.change("UPDATE cn_recipients SET telegram_chat_id='1001',telegram_verified_at=20"); self.process()
        self.assertEqual([(m[0],m[1]) for m in self.transport.sent],[('telegram','2001')])
    def test_full_account_unverified_email_stays_unverified(self):
        self.change('UPDATE cn_recipients SET email_verified=0'); self.process()
        self.assertFalse(any(m[:2]==('email','candidate@fixture.test') for m in self.transport.sent))
        with self.connect() as db: self.assertEqual(db.execute('SELECT email_verified FROM cn_recipients').fetchone()[0],0)
    def test_booking_saved_email_exact_link_separate_chat_no_grants(self):
        self.booking_only(); self.process()
        self.assertEqual({m[1] for m in self.transport.sent},{'consent@fixture.test','4001','recruiter@fixture.test','2001'})
        self.assertTrue(next(m[2] for m in self.transport.sent if m[1]=='consent@fixture.test').endswith(self.link))
        self.assertEqual(next(m[3] for m in self.transport.sent if m[1]=='4001')['inline_keyboard'][0][0]['url'],self.link)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT email FROM cp_candidates').fetchone()[0],None)
            self.assertEqual(db.execute('SELECT email_verified,telegram_chat_id FROM cn_recipients').fetchone(),(0,None))
    def test_exact_booking_link_preserved_for_explicit_migration_alias_only(self):
        self.booking_only()
        config = replace(self.config, product_origin='https://new.fixture.test', product_aliases=('https://fixture.test',))
        self.process(config=config)
        self.assertEqual(next(m[3] for m in self.transport.sent if m[1]=='4001')['inline_keyboard'][0][0]['url'], self.link)
    def test_booking_foreign_origin_is_not_sent(self):
        self.booking_only()
        self.process(config=replace(self.config, product_origin='https://new.fixture.test'))
        self.assertFalse(any(m[1]=='4001' for m in self.transport.sent))
    def test_booking_revocation(self):
        self.booking_only(); self.collect(); self.change("UPDATE cp_booking_invitations SET revoked_at='revoked'"); self.process()
        self.assertEqual({m[1] for m in self.transport.sent},{'recruiter@fixture.test','2001'})
    def test_booking_wrong_personal_link(self):
        self.booking_only(); self.change("UPDATE cp_booking_invitations SET token_hash='wrong'"); self.process()
        self.assertEqual({m[1] for m in self.transport.sent},{'recruiter@fixture.test','2001'})
    def test_booking_navigation_exact_chat(self):
        self.booking_only()
        with self.connect() as db:
            self.assertFalse(reminders.booking_navigation(db,'1001',self.config,self.tick,'command:1'))
            self.assertTrue(reminders.booking_navigation(db,'4001',self.config,self.tick,'command:2'))
        with patch('meeting_reminders.collect'): self.process()
        self.assertEqual(len(self.transport.sent),1); self.assertIn('Моя запись',self.transport.sent[0][2]); self.assertEqual(self.transport.sent[0][1],'4001')
    def test_known_failure_retry_does_not_repeat_success(self):
        self.transport.fail_email=delivery.DeliveryFailure('temporary',retryable=True); self.process(); self.assertEqual(len(self.transport.sent),2)
        self.transport.fail_email=None; self.process(self.tick+30); self.process(self.tick+60); self.assertEqual(len(self.transport.sent),4)
    def test_uncertain_quarantined_manual_retry_no_replay(self):
        self.transport.fail_email=RuntimeError('unknown'); self.process(); self.transport.fail_email=None; self.process(self.tick+60)
        with self.connect() as db:
            for row in self.rows():
                if row['channel']=='email':
                    if 'recruiter_id' in row: recruiter.retry(db,'recruiter',row['id'],self.config,self.tick+90)
                    else: delivery.retry(db,row['id'],self.config,self.tick+90)
        self.process(self.tick+90); self.assertEqual(len(self.transport.sent),2)
    def test_expired_claim_quarantined(self):
        self.collect()
        for table in ('cn_outbox','rn_outbox'): self.change('UPDATE '+table+" SET status='sending',lease_until=?,lease_id='lease'",(self.tick-1,))
        self.process(); self.assertEqual(self.transport.sent,[]); self.assertEqual({r['last_error'] for r in self.rows()},{'delivery_uncertain'})
    def test_recruiter_disconnect_keeps_other_roles_and_blocks_pending_and_tokens(self):
        self.collect()
        with self.connect() as db:
            token = recruiter._token(db,'recruiter','telegram','',self.tick,600)
            recruiter.disconnect_telegram(db,'recruiter',self.tick)
            self.assertEqual(db.execute('SELECT telegram_chat_id FROM cn_recipients').fetchone()[0],'1001')
            self.assertEqual(db.execute('SELECT chat_id FROM en_bindings').fetchone()[0],'3001')
            self.assertEqual(db.execute('SELECT used_at FROM rn_tokens').fetchone()[0],self.tick)
            old = next(row for row in self.rows() if row.get('recruiter_id') and row['channel']=='telegram')
            self.assertEqual(recruiter.retry(db,'recruiter',old['id'],self.config,self.tick)['status'],'cancelled')
        self.process()
        self.assertFalse(any(m[1]=='2001' for m in self.transport.sent))
    def test_booking_navigation_during_interview_but_not_after_end(self):
        self.booking_only(); self.change("UPDATE cp_interviews SET status='live'")
        with self.connect() as db:
            self.assertTrue(reminders.booking_navigation(db,'4001',self.config,self.start+60,'live:1'))
            self.assertTrue(reminders.booking_navigation(db,'4001',self.config,self.start+1800,'grace:1'))
            self.assertFalse(reminders.booking_navigation(db,'4001',self.config,self.start+1800+5400,'ended:1'))
        with patch('meeting_reminders.collect'): self.process(self.start+1800)
        self.assertEqual(len(self.transport.sent),2)
    def test_parallel_collectors(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool: list(pool.map(lambda _:self.collect(),range(6)))
        self.assertEqual(len(self.rows()),4)

if __name__=='__main__': unittest.main()
