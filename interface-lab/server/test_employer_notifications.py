"""Real HTTP + temporary SQLite, no real providers. Also a local UI fixture."""
import argparse
import dataclasses
import hashlib
import hmac
import json
from pathlib import Path
import signal
import threading
import time
import unittest
from urllib.parse import urlencode, urlsplit, parse_qs
from unittest.mock import patch
import candidate_notifications as delivery
import employer_notifications as notices
import telegram_miniapp
from domain import Problem
import test_employer_acceptance as fixture

DUMMY_TOKEN = 'test-only-telegram-token'
def signed(user=123456, date=None, **extra):
    fields = {'auth_date': str(int(time.time()) if date is None else date), 'user': json.dumps({'id': user, 'first_name': 'Fixture'}), **extra}
    secret = hmac.new(b'WebAppData', DUMMY_TOKEN.encode(), hashlib.sha256).digest()
    fields['hash'] = hmac.new(secret, '\n'.join(f'{k}={v}' for k, v in sorted(fields.items())).encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)

class MiniAppSignatureTest(unittest.TestCase):
    def test_valid_and_invalid_boundaries(self):
        self.assertEqual(telegram_miniapp.verify(signed(), DUMMY_TOKEN), '123456')
        bad = [signed()+'&auth_date=1', signed().replace('hash=', 'hash=0'), signed(date=int(time.time())-301), signed(date=int(time.time())+31), signed(user=True), signed(user=-1), signed(user=2**52), signed(user='123456'), signed(chat_type='group'), 'a'*16385, 'user=%zz', signed()+'&hash='+'a'*64]
        for index, raw in enumerate(bad):
            with self.subTest(index=index), self.assertRaises(Problem): telegram_miniapp.verify(raw, DUMMY_TOKEN)

    def test_transport_network_boundary_and_button_validation(self):
        config = delivery.Settings(enabled=True, telegram_token=DUMMY_TOKEN, telegram_username='FixtureBot', product_origin='https://mashadasha.tech')
        markup = notices.button(config, 'owned-vacancy')
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, _): return b'{"ok":true,"result":{"message_id":1}}'
        with patch('candidate_notifications.urllib.request.urlopen', return_value=Response()) as network:
            self.assertEqual(delivery.Transport(config).send_telegram('123456', 'Подборка', markup), '1')
            payload = json.loads(network.call_args.args[0].data)
            self.assertEqual(payload['reply_markup'], markup)
            self.assertEqual(set(payload), {'text', 'reply_markup', 'chat_id', 'link_preview_options'})
        for url in ['http://mashadasha.tech/iframe.html','https://evil.test/iframe.html','https://mashadasha.tech.evil.test/iframe.html','https://user@mashadasha.tech/iframe.html','https://mashadasha.tech/iframe.html#secret','https://mashadasha.tech/elsewhere']:
            with self.subTest(url=url), self.assertRaises(Problem):
                delivery.Transport(config).send_telegram('123456','x',{'inline_keyboard':[[{'text':'x','web_app':{'url':url}}]]})
        with self.assertRaises(Problem):
            delivery._reply_markup({'inline_keyboard':[[{'text':'x','url':'https://example.org','web_app':{'url':notices.app_url(config)}}]]})
        ordinary={'inline_keyboard':[[{'text':'x','url':'https://example.org'},{'text':'y','callback_data':'cb:test'}]]}
        self.assertEqual(delivery._reply_markup(ordinary), ordinary)

class EmployerNotificationsTest(unittest.TestCase):
    setUpClass = classmethod(fixture.EmployerAcceptanceTest.setUpClass.__func__)
    tearDownClass = classmethod(fixture.EmployerAcceptanceTest.tearDownClass.__func__)
    request = fixture.EmployerAcceptanceTest.request
    ok = fixture.EmployerAcceptanceTest.ok
    employer = fixture.EmployerAcceptanceTest.employer
    vacancy = fixture.EmployerAcceptanceTest.vacancy
    candidate = fixture.EmployerAcceptanceTest.candidate
    batch = fixture.EmployerAcceptanceTest.batch
    accepted = fixture.EmployerAcceptanceTest.accepted
    state = fixture.EmployerAcceptanceTest.state
    def setUp(self):
        fixture.EmployerAcceptanceTest.setUp(self)
        self.config=delivery.Settings(enabled=True,telegram_token=DUMMY_TOKEN,telegram_username='FixtureBot',telegram_polling=True,product_origin=self.app.ORIGIN)
        self.settings_patch=patch('candidate_notifications.settings',return_value=self.config)
        self.settings_patch.start(); self.addCleanup(self.settings_patch.stop)
        with self.app.connect() as db:
            for table in ('en_bindings','en_tokens','en_seen','en_sessions','cn_outbox','cn_telegram_updates'): db.execute('DELETE FROM '+table)
        self.counter=0
    def update(self,content,chat=123456):
        self.counter+=1
        value={'update_id':self.counter,'message':{'chat':{'id':chat,'type':'private'},'from':{'id':chat},'text':content}}
        with self.app.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return delivery._consume_telegram_update(db,value,int(time.time()),self.config)
    def bind(self,employer,chat=123456):
        link=self.ok('employer/telegram/link',{},token=employer['token'])
        command='/start '+parse_qs(urlsplit(link['url']).query)['start'][0]
        self.assertEqual(self.update(command,chat)['status'],'linked'); return command
    def exchange(self,raw=None,**kwargs):
        return self.request('employer/telegram/session',{'initData':signed() if raw is None else raw},headers={'Origin':self.app.ORIGIN},**kwargs)
    def queued(self):
        with self.app.connect() as db:
            return [dict(zip(['id','status','payload','error'],r)) for r in db.execute("SELECT id,status,payload_json,last_error FROM cn_outbox WHERE event_key LIKE 'employer-shortlist:%'")]
    def deliver(self):
        class Fake:
            def __init__(self): self.calls=[]
            def send_telegram(self,recipient,text,reply_markup=None): self.calls.append((recipient,text,reply_markup)); return '1'
        fake=Fake(); delivery.process_pending(self.app.connect,settings=self.config,transport=fake,limit=100); return fake.calls
    def test_binding_auth_scope_expiry_one_use_disconnect_rebind(self):
        employer,outsider=self.employer(),self.employer('Other')
        self.assertEqual(self.request('employer/telegram/link',{})[0],401)
        team,_=self.app.EMPLOYER_ROUTES._session({}, {},True)
        self.assertEqual(self.request('employer/telegram/link',{},token=team['token'])[0],403)
        self.assertEqual(self.request('employer/telegram/link',{'accountId':outsider['accountId']},token=employer['token'])[0],400)
        self.assertEqual(self.update(self.bind(employer))['status'],'expired')
        self.assertEqual(self.update('/start em_wrong')['status'],'wrong_link')
        self.assertEqual(self.update('/start candidate_unknown')['status'],'ignored')
        link=self.ok('employer/telegram/link',{},token=outsider['token'])
        command='/start '+parse_qs(urlsplit(link['url']).query)['start'][0]
        self.assertEqual(self.update(command)['status'],'chat_already_linked')
        with self.app.connect() as db: db.execute('UPDATE en_tokens SET expires_at=0')
        self.assertEqual(self.update(command,567890)['status'],'expired')
        self.ok('employer/telegram/disconnect',{},token=outsider['token'])
        self.assertTrue(self.ok('employer/telegram',token=employer['token'])['linked'])
        self.bind(employer,567890)
        self.assertEqual(self.exchange()[0],403); self.assertEqual(self.exchange(signed(567890))[0],200)
        self.ok('employer/telegram/disconnect',{},token=employer['token'])
        self.assertEqual(self.exchange(signed(567890))[0],403)
    def test_exchange_identity_cookie_ownership_origin_revocation(self):
        employer,outsider=self.employer(),self.employer('Other')
        self.vacancy(employer); other=self.vacancy(outsider); self.bind(employer)
        status,result,headers=self.exchange(cookie=outsider['cookie'],token=outsider['token'])
        self.assertEqual(status,200,result); self.assertEqual(result['state']['workspaceId'],employer['workspace']['id'])
        self.assertIn('Secure; HttpOnly; SameSite=Lax',headers['Set-Cookie'])
        self.assertEqual(self.request('employer/state?vacancyId='+other['id'],token=result['token'])[0],404)
        self.assertEqual(self.request('employer/telegram/session',{'initData':signed()})[0],403)
        self.assertEqual(self.request('employer/telegram/session',{'initData':signed()},headers={'Origin':'https://evil.test'})[0],403)
        self.assertEqual(self.exchange(signed(user=999999),cookie=outsider['cookie'])[0],403)
        self.ok('employer/telegram/disconnect',{},token=employer['token'])
        self.assertEqual(self.request('employer/session?personal=1',token=result['token'])[0],401)
        self.assertEqual(self.request('employer/session?personal=1',token=employer['token'])[0],200)
        self.bind(employer)
        with self.app.connect() as db: db.execute('UPDATE em_accounts SET active=0 WHERE id=?',(employer['accountId'],))
        self.assertEqual(self.exchange()[0],403)
    def test_baseline_later_approval_aggregation_replay(self):
        employer=self.employer(); vacancy=self.vacancy(employer); self.accepted(employer,vacancy)
        hidden=self.candidate('Hidden'); aid=self.batch(employer,vacancy,[hidden])['applicationIds'][0]
        self.bind(employer); self.assertEqual(self.queued(),[]); self.deliver(); self.assertEqual(self.queued(),[])
        self.ok('candidate/application/decision',{'applicationId':aid,'decision':'accepted'},cookie=hidden['cookie'])
        self.accepted(employer,vacancy,self.candidate('Later'))
        rows=self.queued(); self.assertEqual(len(rows),1); self.assertEqual(len(json.loads(rows[0]['payload'])['applications']),2)
        calls=self.deliver(); self.assertEqual(len(calls),1); self.assertIn('Доступно новых кандидатов: 2',calls[0][1]); self.assertNotIn('Hidden',calls[0][1])
        self.assertEqual(parse_qs(urlsplit(calls[0][2]['inline_keyboard'][0][0]['web_app']['url']).query)['vacancyId'],[vacancy['id']])
        self.batch(employer,vacancy,[hidden]); self.assertEqual(self.deliver(),[]); self.assertEqual(len(self.queued()),1)
    def test_stale_consent_partial_count_disabled_history(self):
        employer=self.employer(); vacancy=self.vacancy(employer); self.bind(employer); self.deliver()
        a,_=self.accepted(employer,vacancy); self.accepted(employer,vacancy)
        self.ok('candidate/profile',{'consentReuse':False,'autoApprove':False},cookie=a['cookie'])
        calls=self.deliver(); self.assertEqual(len(calls),1); self.assertIn('Доступно новых кандидатов: 1',calls[0][1])
        with patch('candidate_notifications.settings',return_value=dataclasses.replace(self.config,enabled=False)): self.accepted(employer,vacancy)
        self.assertEqual(self.deliver(),[])
    def test_stale_binding_stopped_candidate_no_send(self):
        employer=self.employer(); vacancy=self.vacancy(employer); self.bind(employer); self.deliver(); self.accepted(employer,vacancy)
        self.ok('employer/telegram/disconnect',{},token=employer['token']); self.assertEqual(self.deliver(),[])
        self.assertEqual(self.queued()[0]['error'],'employer_binding_changed')
        self.bind(employer); self.deliver(); person,_=self.accepted(employer,vacancy)
        with self.app.connect() as db: db.execute('UPDATE cp_candidates SET searching=0 WHERE id=?',(person['id'],))
        self.assertEqual(self.deliver(),[]); self.assertEqual(self.queued()[-1]['error'],'employer_shortlist_unavailable')
    def test_publication_of_preapproved_batch_and_exact_replay(self):
        employer = self.employer()
        vacancy = self.vacancy(employer)
        self.bind(employer)
        self.deliver()
        people = [self.candidate('Первый'), self.candidate('Второй')]
        import employer_store
        payload = {'source': 'recruiter', 'version': 'test', 'externalBatchId': 'exact-replay',
                   'candidates': [{'candidateId': person['id'], 'rank': rank, 'reason': 'Test'}
                                  for rank, person in enumerate(people)]}
        with self.app.connect() as db:
            for person in people:
                self.app.CANDIDATE_PORTAL.propose_application(db, {'candidateId': person['id'], 'vacancyId': vacancy['id']})
                aid = db.execute('SELECT id FROM cp_applications WHERE candidate_id=? AND vacancy_id=?',
                                 (person['id'], vacancy['id'])).fetchone()[0]
                self.app.CANDIDATE_PORTAL.decide_application(db, person['id'], {'applicationId': aid, 'decision': 'accepted'})
            first = employer_store.import_batch(db, employer['workspace']['id'], vacancy['id'], payload, self.app.CANDIDATE_PORTAL)
            replay = employer_store.import_batch(db, employer['workspace']['id'], vacancy['id'], payload, self.app.CANDIDATE_PORTAL)
            self.assertEqual(first, replay)
        self.assertEqual(len(self.queued()), 1)
        calls = self.deliver()
        self.assertEqual(len(calls), 1)
        self.assertIn('Доступно новых кандидатов: 2', calls[0][1])
        self.assertEqual(self.deliver(), [])

    def seed_browser(self):
        employer=self.employer('Telegram QA'); vacancy=self.vacancy(employer,'QA · Подборка в Telegram')
        _,aid=self.accepted(employer,vacancy,self.candidate('Алёна Проверочная'))
        hidden=self.candidate('Скрытый до согласования'); self.batch(employer,vacancy,[hidden]); self.bind(employer)
        return {'apiBase':self.url,'token':employer['token'],'cookie':employer['cookie'],'vacancy':vacancy,'applicationId':aid,'candidateName':'Алёна Проверочная','hiddenCandidateName':'Скрытый до согласования','initData':signed(),'miniappUrl':notices.app_url(self.config,vacancy['id']),'localOnly':True,'fixture':'real-http-temp-sqlite-fake-provider'}

def serve_fixture(destination):
    cls=EmployerNotificationsTest; cls.setUpClass(); stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig,lambda *_:stop.set())
    case=cls()
    try:
        case.setUp(); target=Path(destination); target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(case.seed_browser(),ensure_ascii=False,indent=2)); target.chmod(0o600)
        print('Local employer Telegram fixture ready: '+str(target),flush=True); stop.wait()
    finally:
        case.doCleanups(); Path(destination).unlink(missing_ok=True); cls.tearDownClass()
if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--serve-fixture'); args,rest=parser.parse_known_args()
    if args.serve_fixture: serve_fixture(args.serve_fixture)
    else: unittest.main(argv=[__file__,*rest])
