"""Prospective lifecycle hooks + shared dispatcher with controlled providers."""
import json
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import candidate_notifications as delivery
import recruiter_notifications as recruiter
import meeting_notifications as notices
from domain import iso
import datetime as dt
import test_meeting_reminders as rem
import test_candidate_portal as portal_fixture
import test_employer_acceptance as http_fixture


class LifecycleTest(unittest.TestCase):
    connect = rem.RemindersTest.connect
    change = rem.RemindersTest.change
    meeting = rem.RemindersTest.meeting
    def setUp(self):
        rem.RemindersTest.setUp(self); self.meeting()
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE cp_employer_workflows(application_id TEXT,decision TEXT,after_interest TEXT,after_meeting_id TEXT);
                CREATE TABLE cp_meeting_feedback(meeting_id TEXT,application_id TEXT,submitted TEXT);
                CREATE TABLE cp_offer_versions(application_id TEXT,status TEXT);
                ALTER TABLE cp_applications ADD COLUMN status TEXT DEFAULT 'accepted';
                ALTER TABLE cp_candidates ADD COLUMN searching INTEGER DEFAULT 1;
            ''')
    def queue(self,event='meeting.confirmed',cid='candidate'):
        with self.connect() as db:
            return delivery.enqueue(db,cid,event+':meeting',event,settings=self.config,now=self.tick)
    def rows(self):
        with self.connect() as db:
            db.row_factory=sqlite3.Row
            return [dict(r) for r in db.execute('SELECT * FROM cn_outbox')]
    def process(self,tick=None):
        with patch('employer_notifications.collect'),patch('meeting_reminders.collect'):
            delivery.process_pending(self.connect,self.config,100,self.tick if tick is None else tick,self.transport)
    def test_confirmation_two_people_two_channels_exact_context_once(self):
        self.queue(); self.queue(); self.process(); self.process()
        self.assertEqual(len(self.rows()),4); self.assertEqual(len(self.transport.sent),4)
        self.assertEqual({m[1] for m in self.transport.sent},{'candidate@fixture.test','employer@fixture.test','1001','3001'})
        for channel,person,content,markup in self.transport.sent:
            self.assertIn('МСК',content)
            if channel=='telegram':
                self.assertNotIn('https://',content); button=markup['inline_keyboard'][0][0]
                link=button['web_app']['url'] if person=='3001' else button['url']; query=parse_qs(urlsplit(link).query)
                self.assertEqual(query['meetingId' if person=='3001' else 'meeting'],['meeting'])
                self.assertEqual(query['applicationId' if person=='3001' else 'application'],['application'])
            elif person=='employer@fixture.test': self.assertNotIn('miniapp=',content)
    def test_proposal_only_candidate_actual_confirm_action(self):
        self.change("UPDATE cp_meetings SET status='pending'"); self.queue('meeting.proposed'); self.process()
        self.assertEqual({m[1] for m in self.transport.sent},{'candidate@fixture.test','1001'})
        self.assertEqual(self.transport.sent[-1][3]['inline_keyboard'][0][0]['text'],'Подтвердить время')
    def test_completion_feedback_no_time_based_generation(self):
        self.process(self.start+1900); self.assertEqual(self.rows(),[])
        self.change("UPDATE cp_meetings SET status='completed'"); self.queue('meeting.completed'); self.process(self.start+1900)
        self.assertEqual(len(self.transport.sent),4)
        self.assertTrue(any('хотите ли вы продолжить' in m[2] for m in self.transport.sent))
        self.assertTrue(any('связь по встрече' in m[2] for m in self.transport.sent))

    def test_receiver_blocks_old_dispatcher_for_real_completion_retry_rows(self):
        spec = importlib.util.spec_from_file_location('lifecycle_receiver', Path(__file__).resolve().parents[1] / 'deploy/ci/receiver.py')
        receiver = importlib.util.module_from_spec(spec); spec.loader.exec_module(receiver)
        self.change("UPDATE cp_meetings SET status='completed'")
        self.queue('meeting.completed')
        self.transport.fail_email = delivery.DeliveryFailure('temporary', retryable=True)
        self.process(self.start + 1900)
        before = self.rows()
        retries = [row for row in before if row['status'] == 'pending']
        self.assertEqual({row['candidate_id'] for row in retries}, {'candidate', 'employer:employer'})
        self.assertTrue(all(row['event_type'] == 'meeting.completed' and row['attempts'] == 1 for row in retries))
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            for name in receiver.PRE_MEETING_NOTICES_RUNTIME:
                path = runtime / name; path.parent.mkdir(parents=True, exist_ok=True); path.touch()
            # Each actual pending retry independently blocks legacy activation;
            # neither recipient namespace nor provider backoff bypasses the gate.
            with self.connect() as db:
                for row in retries:
                    db.execute('SAVEPOINT isolate_notice')
                    db.execute('DELETE FROM cn_outbox WHERE id!=?', (row['id'],))
                    with self.assertRaisesRegex(ValueError, 'Lifecycle outbox'):
                        receiver.guard_outbox_runtime(db, runtime)
                    db.execute('ROLLBACK TO isolate_notice'); db.execute('RELEASE isolate_notice')
                (runtime / 'meeting_notifications.py').touch()
                receiver.guard_outbox_runtime(db, runtime)
        self.assertEqual(self.rows(), before)
    def test_wrong_candidate_and_namespace(self):
        self.assertEqual(self.queue(cid='other'),[]); self.queue(); self.change("UPDATE cn_outbox SET candidate_id='other:' || candidate_id"); self.process()
        self.assertEqual(self.transport.sent,[])
    def test_transaction_rollback(self):
        with self.assertRaises(RuntimeError),self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            delivery.enqueue(db,'candidate','meeting.confirmed:meeting','meeting.confirmed',settings=self.config,now=self.tick)
            raise RuntimeError('rollback')
        self.assertEqual(self.rows(),[])
    def test_stale_changes_fail_closed(self):
        cases=["UPDATE cp_meetings SET status='completed'","UPDATE cp_meetings SET revision=3",
            "UPDATE cp_meetings SET manager_id='workspace:wrong'","UPDATE cp_meetings SET start='2039-01-01T00:00:00+00:00'",
            "UPDATE cp_applications SET candidate_id='wrong'","UPDATE cp_applications SET archived_at='closed'",
            "UPDATE em_vacancies SET workspace_id='wrong'","UPDATE em_reviews SET state='declined'", "UPDATE cp_applications SET status='declined'"]
        for sql in cases:
            with self.subTest(sql=sql):
                self.queue()
                tables=('cp_meetings','cp_applications','em_vacancies','em_reviews')
                with self.connect() as db:
                    for t in tables: db.execute('CREATE TABLE saved_'+t+' AS SELECT * FROM '+t)
                    db.execute(sql)
                self.process(); self.assertEqual(self.transport.sent,[])
                with self.connect() as db:
                    for t in tables:
                        db.execute('DELETE FROM '+t); db.execute('INSERT INTO '+t+' SELECT * FROM saved_'+t); db.execute('DROP TABLE saved_'+t)
                    db.execute('DELETE FROM cn_outbox')
    def test_superseded_and_ended_suppressed(self):
        self.queue(); self.change("INSERT INTO cp_meetings SELECT 'new',application_id,start,end,'pending',revision+1,created_at,confirmed_at,manager_id FROM cp_meetings")
        self.process(); self.assertEqual(self.transport.sent,[])
        self.change("DELETE FROM cp_meetings WHERE id='new'"); self.change('DELETE FROM cn_outbox'); self.queue(); self.process(self.start+1800)
        self.assertEqual(self.transport.sent,[])
    def test_feedback_submitted_revalidated(self):
        self.change("UPDATE cp_meetings SET status='completed'"); self.queue('meeting.completed')
        self.change("INSERT INTO cp_meeting_feedback VALUES ('meeting','application','{}')"); self.process()
        self.assertEqual({m[1] for m in self.transport.sent},{'candidate@fixture.test','1001'})
    def test_candidate_after_response_or_offer_suppressed(self):
        for statement in ["INSERT INTO cp_employer_workflows VALUES ('application','review','yes','meeting')","INSERT INTO cp_offer_versions VALUES ('application','sent')"]:
            with self.subTest(statement=statement):
                self.change("UPDATE cp_meetings SET status='completed'"); self.queue('meeting.completed'); self.change(statement); self.process()
                self.assertEqual({m[1] for m in self.transport.sent},{'employer@fixture.test','3001'})
                self.transport.sent.clear(); self.change('DELETE FROM cn_outbox'); self.change('DELETE FROM cp_employer_workflows'); self.change('DELETE FROM cp_offer_versions')
    def test_disconnected_rebound_inactive_independent_channels(self):
        self.queue(); self.change("UPDATE en_bindings SET generation='new'"); self.change('UPDATE cn_recipients SET telegram_chat_id=NULL')
        self.process(); self.assertEqual({m[1] for m in self.transport.sent},{'candidate@fixture.test','employer@fixture.test'})
        self.transport.sent.clear(); self.change('DELETE FROM cn_outbox'); self.queue(); self.change('UPDATE em_accounts SET active=0'); self.process()
        self.assertEqual({m[1] for m in self.transport.sent},{'candidate@fixture.test'})
    def test_unverified_unbound_disabled_never_replay(self):
        self.change('UPDATE cn_recipients SET email_verified=0,telegram_chat_id=NULL'); self.change('DELETE FROM en_bindings')
        self.config=replace(self.config,enabled=False); self.queue(); self.assertEqual({r['status'] for r in self.rows()},{'disabled','unbound'})
        self.config=replace(self.config,enabled=True); self.change("UPDATE cn_recipients SET email_verified=1,telegram_chat_id='1001'")
        for row in self.rows():
            with self.connect() as db: delivery.retry(db,row['id'],self.config,self.tick)
        self.process(); self.assertEqual(self.transport.sent,[])
    def test_email_unverified_independent_telegram_and_employer(self):
        self.change('UPDATE cn_recipients SET email_verified=0'); self.queue(); self.process()
        self.assertEqual({m[1] for m in self.transport.sent},{'1001','employer@fixture.test','3001'})
    def test_retry_backoff_uncertainty_no_manual_replay(self):
        self.queue(); self.transport.fail_email=delivery.DeliveryFailure('temporary',retryable=True); self.process()
        self.transport.fail_email=None; self.process(self.tick+30); self.process(self.tick+60); self.assertEqual(len(self.transport.sent),4)
        self.change('DELETE FROM cn_outbox'); self.transport.sent.clear(); self.queue(); self.transport.fail_email=RuntimeError('uncertain'); self.process()
        self.transport.fail_email=None
        for row in self.rows():
            with self.connect() as db: delivery.retry(db,row['id'],self.config,self.tick+60)
        self.process(self.tick+60); self.assertEqual(len(self.transport.sent),2)

    def test_unbound_channels_do_not_replay_after_linking(self):
        self.change('UPDATE cn_recipients SET telegram_chat_id=NULL'); self.change('DELETE FROM en_bindings'); self.queue()
        self.change("UPDATE cn_recipients SET telegram_chat_id='1001'"); self.change("INSERT INTO en_bindings VALUES ('employer','3001','new',20)")
        self.process(); self.assertEqual({m[1] for m in self.transport.sent},{'candidate@fixture.test','employer@fixture.test'})
    def test_search_stopped_and_expired_claim_are_terminal(self):
        self.queue(); self.change('UPDATE cp_candidates SET searching=0'); self.process(); self.assertEqual(self.transport.sent,[])
        self.change('UPDATE cp_candidates SET searching=1'); self.change('DELETE FROM cn_outbox'); self.queue()
        self.change("UPDATE cn_outbox SET status='sending',lease_until=?,lease_id='lease'",(self.tick-1,)); self.process()
        self.assertEqual({r['last_error'] for r in self.rows()},{'delivery_uncertain'})
    def test_changed_candidate_binding_and_employer_email(self):
        self.queue(); self.change("UPDATE cn_recipients SET telegram_verified_at=11,email='other@fixture.test'")
        self.change("UPDATE em_accounts SET email='other@fixture.test'"); self.change('DELETE FROM en_bindings'); self.process()
        self.assertEqual(self.transport.sent,[])

    def test_real_transport_serializes_mail_and_miniapp_without_network(self):
        self.queue(); emails=[]; telegram=[]
        class SMTP:
            def __init__(self,*args,**kwargs): pass
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def ehlo(self): pass
            def starttls(self,**kwargs): pass
            def send_message(self,message): emails.append(message); return {}
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,_): return b'{"ok":true,"result":{"message_id":123}}'
        def request(req,**kwargs):
            telegram.append(json.loads(req.data)); return Response()
        with patch('candidate_notifications.smtplib.SMTP',SMTP),patch('candidate_notifications.smtplib.SMTP_SSL',SMTP),patch('candidate_notifications.urllib.request.urlopen',side_effect=request),patch('meeting_reminders.collect'),patch('employer_notifications.collect'):
            delivery.process_pending(self.connect,self.config,100,self.tick)
        self.assertEqual({str(m['To']) for m in emails},{'candidate@fixture.test','employer@fixture.test'})
        self.assertTrue(all(m['Message-ID'] and 'МСК' in m.get_content() for m in emails))
        self.assertEqual({str(m['chat_id']) for m in telegram},{'1001','3001'})
        self.assertTrue(all(m['reply_markup']['inline_keyboard'] for m in telegram))
        self.assertEqual({r['status'] for r in self.rows()},{'delivered'})


class PrimaryHookTest(unittest.TestCase):
    setUp=portal_fixture.CandidatePortalTest.setUp
    tearDown=portal_fixture.CandidatePortalTest.tearDown
    connect=portal_fixture.CandidatePortalTest.connect
    payload=portal_fixture.CandidatePortalTest.payload
    call=portal_fixture.CandidatePortalTest.call
    not_manager=staticmethod(portal_fixture.CandidatePortalTest.not_manager)
    register=portal_fixture.CandidatePortalTest.register
    slot=portal_fixture.CandidatePortalTest.slot
    def test_canonical_booking_reuses_candidate_recruiter_events(self):
        self.portal.notifications=delivery
        config=replace(delivery.settings({'HR_ORIGIN':'https://fixture.test'}),enabled=True,smtp_host='smtp.test',smtp_from='hr@fixture.test',telegram_token='fake',telegram_username='FixtureBot')
        with self.connect() as db: delivery.init(db)
        with patch('candidate_notifications.settings',return_value=config):
            person,cookie=self.register(); slot=self.slot()
            with self.connect() as db:
                delivery.set_email(db,person['id'],person['email'],verified=True)
                db.execute("UPDATE cn_recipients SET telegram_chat_id='1001',telegram_verified_at=10")
                rid=db.execute('SELECT recruiter_id FROM cp_recruiter_slot_owners WHERE slot_id=?',(slot['id'],)).fetchone()[0]
                recruiter._recipient(db,rid); db.execute("UPDATE rn_channels SET telegram_chat_id='2001',telegram_verified_at=10"); db.execute('DELETE FROM cn_outbox')
            original_enqueue=notices.enqueue
            def fail_after_enqueue(*args,**kwargs):
                original_enqueue(*args,**kwargs); raise RuntimeError('transaction probe')
            with patch('meeting_notifications.enqueue',side_effect=fail_after_enqueue),self.assertRaises(RuntimeError),self.connect() as db:
                db.execute('BEGIN IMMEDIATE'); self.portal.book(db,person['id'],{'slotId':slot['id']})
            with self.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_interviews').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM rn_outbox').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM cn_outbox').fetchone()[0],0)
            self.call('candidate/book',{'slotId':slot['id']},cookie)
            self.call('candidate/book',{'slotId':slot['id']},cookie)
            fake=rem.FakeTransport()
            with patch('meeting_reminders.collect'),patch('employer_notifications.collect'):
                delivery.process_pending(self.connect,config,100,transport=fake)
            recruiter.process_pending(self.connect,config,100,transport=fake)
            self.assertEqual(len(fake.sent),4)
            with self.connect() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM cn_outbox WHERE event_type='interview.booked'").fetchone()[0],2)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM rn_outbox WHERE event_type='interview.booked'").fetchone()[0],2)


class CanonicalLifecycleTest(unittest.TestCase):
    setUpClass=classmethod(http_fixture.EmployerAcceptanceTest.setUpClass.__func__)
    tearDownClass=classmethod(http_fixture.EmployerAcceptanceTest.tearDownClass.__func__)
    for _name in ('request','ok','employer','vacancy','candidate','batch','accepted','state','action_body','act','propose','complete','feedback'):
        locals()[_name]=getattr(http_fixture.EmployerAcceptanceTest,_name)
    exact=staticmethod(http_fixture.EmployerAcceptanceTest.exact)
    def test_http_propose_confirm_complete_retries(self):
        http_fixture.EmployerAcceptanceTest.setUp(self)
        config=replace(delivery.settings(),enabled=True,smtp_host='smtp.test',smtp_from='hr@fixture.test',telegram_token='fake',telegram_username='FixtureBot')
        with patch('candidate_notifications.settings',return_value=config):
            employer=self.employer(); vacancy=self.vacancy(employer); person,aid=self.accepted(employer,vacancy)
            with self.app.connect() as db:
                delivery.set_email(db,person['id'],person['email'],verified=True)
                db.execute("UPDATE cn_recipients SET telegram_chat_id='1001',telegram_verified_at=10 WHERE candidate_id=?",(person['id'],))
                db.execute('INSERT INTO en_bindings VALUES (?,?,?,?)',(employer['accountId'],'3001','generation',10)); db.execute('DELETE FROM cn_outbox')
            original_enqueue=notices.enqueue
            def fail_after_enqueue(*args,**kwargs):
                original_enqueue(*args,**kwargs); raise RuntimeError('transaction probe')
            portal=self.app.CANDIDATE_PORTAL
            with patch('meeting_notifications.enqueue',side_effect=fail_after_enqueue),self.assertRaises(RuntimeError),self.app.connect() as db:
                portal.propose_meeting(db,{'applicationId':aid,'start':iso(self.tick),'end':iso(self.tick+dt.timedelta(minutes=30))},manager_id='workspace:'+employer['workspace']['id'])
            with self.app.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_meetings WHERE application_id=?',(aid,)).fetchone()[0],0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM cn_outbox').fetchone()[0],0)
            meeting=self.propose(employer,vacancy,aid)
            def process():
                fake=rem.FakeTransport()
                with patch('meeting_reminders.collect'),patch('employer_notifications.collect'):
                    delivery.process_pending(self.app.connect,config,100,transport=fake)
                return fake.sent
            self.assertEqual(len(process()),2)
            with patch('meeting_notifications.enqueue',side_effect=fail_after_enqueue),self.assertRaises(RuntimeError),self.app.connect() as db:
                portal.confirm_meeting(db,person['id'],self.exact(aid,meeting))
            with self.app.connect() as db:
                self.assertEqual(db.execute('SELECT status FROM cp_meetings WHERE id=?',(meeting['id'],)).fetchone()[0],'pending')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM cn_outbox').fetchone()[0],2)
            for _ in range(2): self.ok('candidate/meeting/confirm' ,self.exact(aid,meeting),cookie=person['cookie'])
            self.assertEqual(len(process()),4)
            self.ok('employer/room',self.action_body(employer,vacancy,aid,'meeting.room',meetingId=meeting['id'],revision=meeting['revision'],consent=True),token=employer['token'])
            with patch('meeting_notifications.enqueue',side_effect=fail_after_enqueue),self.assertRaises(RuntimeError):
                portal.complete(person['id'],aid,meeting['id'])
            with self.app.connect() as db:
                self.assertEqual(db.execute('SELECT status FROM cp_meetings WHERE id=?',(meeting['id'],)).fetchone()[0],'live')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM cn_outbox').fetchone()[0],6)
            self.act(employer,vacancy,aid,'meeting.finish',meetingId=meeting['id'],revision=meeting['revision'])
            self.app.CANDIDATE_PORTAL.complete(person['id'],aid,meeting['id'])
            self.assertEqual(len(process()),4); self.assertEqual(process(),[])
            with self.app.connect() as db: counts=dict(db.execute('SELECT event_type,COUNT(*) FROM cn_outbox GROUP BY event_type'))
            self.assertEqual(counts,{'meeting.proposed':2,'meeting.confirmed':4,'meeting.completed':4})

if __name__=='__main__': unittest.main()
