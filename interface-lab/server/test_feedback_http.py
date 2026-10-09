"""Temporary DB and HTTP only. Run on outreach, never the shared scenario."""
import base64, json, os, tempfile, threading, unittest, urllib.request, urllib.error, uuid
from unittest.mock import patch
DATA=tempfile.TemporaryDirectory(prefix='hr-feedback-http-')
os.environ.update(HR_DATA_DIR=DATA.name,HR_TEAM_KEY='isolated-test',HR_VIDEO_ENABLED='0',DAILY_API_KEY='')
import app
from domain import fresh, transition, free_slots
from test_feedback import VTT
class FeedbackHTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        threading.Thread(target=cls.server.serve_forever,daemon=True).start()
        cls.url='http://127.0.0.1:'+str(cls.server.server_port)+'/api/hr/'
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();DATA.cleanup()
    def post(self,path,body,token=None):
        req=urllib.request.Request(self.url+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {})})
        try:
            with urllib.request.urlopen(req,timeout=5) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:return e.code,json.load(e)
    def setUp(self):
        s=fresh()
        def act(actor,action,**kw):transition(s,actor,dict(generation=s['generation'],candidate='anna',action=action,**kw))
        act('anna','interest',value='accepted');act('manager','meeting.propose',start=free_slots(s,'anna')[0])
        m=s['candidates']['anna']['meeting'];act('anna','meeting.confirm',meetingId=m['id'])
        m['room']={'id':'test-room','name':'test-only'}
        act('manager','meeting.joined',meetingId=m['id']);act('manager','meeting.finish',meetingId=m['id'])
        self.identity=dict(candidate='anna',generation=s['generation'],meetingId=m['id'])
        with app.connect() as db:
            app.write(db,s)
            db.execute('DELETE FROM scenario_variants')
        self.manager=self.post('session',{'teamKey':'isolated-test','actor':'manager'})[1]['token']
        self.candidate=self.post('session',{'teamKey':'isolated-test','actor':'anna'})[1]['token']
    def test_transcript_fallback_freezes_and_auth_rejects(self):
        with patch.object(app,'load_transcripts',return_value=[{'id':'vtt','text':VTT}]) as provider:
            status,r=self.post('feedback/questions',self.identity,self.manager)
            self.assertEqual(status,200);self.assertEqual(r['status'],'ready');self.assertEqual(len(r['questionnaire']['questions']),2)
            self.post('feedback/questions',self.identity,self.manager);self.assertEqual(provider.call_count,1)
            self.assertEqual(self.post('feedback/questions',self.identity,self.candidate)[0],403)
            self.assertEqual(self.post('feedback/questions',{**self.identity,'meetingId':'old'},self.manager)[0],409)
    def test_waiting_and_error_never_fabricate_questions(self):
        with patch.object(app,'load_transcripts',return_value=[]):
            r=self.post('feedback/questions',self.identity,self.manager)[1];self.assertEqual(r['status'],'processing');self.assertIsNone(r['questionnaire'])
        with patch.object(app,'load_transcripts',side_effect=RuntimeError('private')):
            r=self.post('feedback/questions',self.identity,self.manager)[1];self.assertEqual(r['status'],'unavailable');self.assertNotIn('private',str(r))
    def test_draft_survives_http_round_trip(self):
        self.post('action',{**self.identity,'action':'feedback.questions','questions':'Как вы вернули клиента?'},self.manager)
        q=self.post('feedback/questions',self.identity,self.manager)[1]['questionnaire']
        answers={q['questions'][0]['id']:{'rating':'clear','comment':'Подтвердил примером'}}
        data={**self.identity,'questionnaireId':q['id'],'answers':answers}
        self.assertEqual(self.post('action',{**data,'action':'feedback.draft'},self.manager)[0],200)
        self.assertEqual(self.post('feedback/questions',self.identity,self.manager)[1]['draft'],answers)
        self.assertEqual(self.post('action',{**data,'action':'feedback'},self.manager)[0],200)

    def access_request(self, path, body=None, cookie='', token='', origin=None):
        headers = {'Cookie': cookie}
        if body is not None: headers['Content-Type'] = 'application/json'
        if token: headers['Authorization'] = 'Bearer ' + token
        if origin: headers['Origin'] = origin
        req = urllib.request.Request(self.url + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r: return r.status, json.load(r), r.headers
        except urllib.error.HTTPError as e: return e.code, json.load(e), e.headers

    def test_invitation_remembers_access_and_can_choose_all_roles(self):
        with app.connect() as db: before = app.read(db)
        self.assertFalse(self.access_request('access')[1]['ready'])
        status, result, headers = self.access_request('access', {'teamKey': 'isolated-test'})
        self.assertEqual(status, 200); self.assertTrue(result['ready'])
        cookie = headers['Set-Cookie'].split(';')[0]
        self.assertTrue(self.access_request('access', cookie=cookie)[1]['ready'])
        for actor in ('manager', 'anna', 'mikhail', 'elena'):
            status, result, _ = self.access_request('session', {'actor': actor}, cookie=cookie)
            self.assertEqual(status, 200); self.assertEqual(result['actor'], actor)
            self.assertEqual(self.access_request('state', token=result['token'])[1]['actor'], actor)
        self.assertEqual(self.access_request('invite', {}, cookie=cookie)[1]['url'], app.ORIGIN + '/iframe.html?id=hr-vision-product--start&viewMode=story#team=isolated-test')
        with app.connect() as db: self.assertEqual(before, app.read(db))

    def test_access_and_invitation_stay_private(self):
        for path, body in (('session', {'actor': 'manager'}), ('invite', {}), ('access', {'teamKey': 'wrong'})):
            self.assertEqual(self.access_request(path, body)[0], 403)
        cookie = self.access_request('access', {}, token=self.manager)[2]['Set-Cookie'].split(';')[0]
        self.assertTrue(self.access_request('access', cookie=cookie)[1]['ready'])
        self.assertEqual(self.access_request('session', {'actor': 'manager'}, cookie=cookie, origin='https://untrusted.example')[0], 403)
        self.assertEqual(self.access_request('state', cookie=cookie)[0], 401)
        self.assertEqual(self.access_request('access', {}, token='expired')[0], 401)

    def test_dsa_panel_is_manager_only_and_does_not_change_hiring_state(self):
        with app.connect() as db: before = app.read(db)
        with patch.object(app.DSA_PANEL, 'load', return_value={'candidates': [{'id': '123'}]}) as source:
            self.assertEqual(self.access_request('dsa-candidates')[0], 401)
            self.assertEqual(self.access_request('dsa-candidates', token=self.candidate)[0], 403)
            status, body, _ = self.access_request('dsa-candidates', token=self.manager)
            self.assertEqual(status, 200); self.assertEqual(body['candidates'][0]['id'], '123')
            self.assertEqual(source.call_count, 1)
        with app.connect() as db: self.assertEqual(before, app.read(db))

    def dsa_accounts(self):
        source = {'role': 'Менеджер', 'candidates': [
            {'id': str(i), 'name': f'Тестовый кандидат {i}', 'role': 'Опыт в продажах',
             'cv': f'Резюме {i}', 'sourceStatus': 'не подходит'} for i in range(1, 7)]}
        with patch.object(app.DSA_PANEL, 'load', return_value=source) as loader:
            code, roster = self.post('accounts', {'scope': 'dsa', 'teamKey': 'isolated-test'})
            self.assertEqual(code, 200); self.assertEqual(len(roster['accounts']), 7)
            tokens = {}
            for actor in ('manager', 'dsa-1', 'dsa-2'):
                code, result = self.post('session', {'scope': 'dsa', 'actor': actor, 'teamKey': 'isolated-test'})
                self.assertEqual(code, 200); tokens[actor] = result['token']
            self.assertEqual(loader.call_count, 1)
        return tokens

    def test_dsa_accounts_are_private_and_do_not_change_source_decisions(self):
        with app.connect() as db: before = app.read(db)
        with patch.object(app.DSA_PANEL, 'load') as loader:
            self.assertEqual(self.post('accounts', {'scope': 'dsa'})[0], 403)
            self.assertEqual(self.post('session', {'scope': '../dsa', 'actor': 'manager', 'teamKey': 'isolated-test'})[0], 400)
            loader.assert_not_called()
        tokens = self.dsa_accounts()
        manager = self.access_request('state', token=tokens['manager'])[1]
        self.assertEqual(manager['scope'], 'dsa'); self.assertEqual(len(manager['candidates']), 6)
        self.assertEqual(manager['candidates']['dsa-1']['decision'], 'review')
        self.assertEqual(manager['candidates']['dsa-1']['sourceProfile']['sourceStatus'], 'не подходит')
        person = self.access_request('state', token=tokens['dsa-1'])[1]
        self.assertEqual(person['actorName'], 'Тестовый кандидат 1')
        self.assertEqual(list(person['candidates']), ['dsa-1'])
        self.assertNotIn('sourceProfile', person['candidates']['dsa-1'])
        self.assertEqual(self.post('session', {'scope': 'sfera', 'actor': 'dsa-1', 'teamKey': 'isolated-test'})[0], 400)
        self.assertEqual(self.post('session', {'scope': 'dsa', 'actor': 'anna', 'teamKey': 'isolated-test'})[0], 400)
        with app.connect() as db: self.assertEqual(before, app.read(db))

    def test_dsa_parallel_meetings_persist_and_confirmation_is_manual(self):
        tokens = self.dsa_accounts()
        with app.connect() as db: before = app.read(db)
        s = self.access_request('state', token=tokens['manager'])[1]
        generation = s['generation']
        def act(actor, action, cid='dsa-1', **values):
            return self.post('action', dict(generation=generation, candidate=cid, action=action, **values), tokens[actor])
        slot = s['candidates']['dsa-1']['freeSlots'][0]
        code, s = act('manager', 'meeting.propose', start=slot, previousMeetingId=None)
        self.assertEqual(code, 200)
        first = s['candidates']['dsa-1']['meeting']
        self.assertEqual(first['status'], 'pending')
        self.assertEqual(act('manager', 'meeting.confirm', meetingId=first['id'])[0], 403)
        self.assertEqual(act('dsa-2', 'meeting.confirm', meetingId=first['id'])[0], 403)
        self.assertNotIn(slot, s['candidates']['dsa-2']['freeSlots'])
        self.assertEqual(act('manager', 'meeting.propose', 'dsa-2', start=slot)[0], 409)
        code, s = act('manager', 'meeting.propose', 'dsa-2', start=s['candidates']['dsa-2']['freeSlots'][0])
        self.assertEqual(code, 200)
        second = s['candidates']['dsa-2']['meeting']
        self.assertEqual(act('dsa-1', 'meeting.confirm', meetingId=first['id'])[0], 200)
        s = self.access_request('state', token=tokens['manager'])[1]
        self.assertEqual(s['candidates']['dsa-1']['meeting']['status'], 'confirmed')
        self.assertEqual(s['candidates']['dsa-2']['meeting'], second)
        self.assertEqual(self.post('action', dict(action='chat', generation=generation, candidate='dsa-1', text='Привет'), self.manager)[0], 409)
        # Reset only this scenario; source profiles and ready accounts remain.
        code, reset = act('manager', 'reset')
        self.assertEqual(code, 200); self.assertEqual(len(reset['candidates']), 6)
        self.assertNotEqual(reset['generation'], generation)
        self.assertEqual(reset['candidates']['dsa-1']['sourceProfile']['cv'], 'Резюме 1')
        self.assertIsNone(reset['candidates']['dsa-2']['meeting'])
        self.assertEqual(act('dsa-1', 'meeting.confirm', meetingId=first['id'])[0], 409)
        with app.connect() as db: self.assertEqual(before, app.read(db))

    def test_dsa_room_feedback_and_offer_use_the_selected_profile(self):
        tokens = self.dsa_accounts()
        with app.connect() as db: before = app.read(db)
        s = self.access_request('state', token=tokens['manager'])[1]
        identity = {'generation': s['generation'], 'candidate': 'dsa-1'}
        def act(actor, action, **values):
            code, result = self.post('action', {**identity, 'action': action, **values}, tokens[actor])
            self.assertEqual(code, 200, result)
            return result
        s = act('manager', 'meeting.propose', start=s['candidates']['dsa-1']['freeSlots'][0])
        identity['meetingId'] = s['candidates']['dsa-1']['meeting']['id']
        self.assertEqual(self.post('room', {**identity, 'consent': True}, tokens['manager'])[0], 409)
        act('dsa-1', 'meeting.confirm')
        with patch.object(app.MEDIA, 'room', return_value={'id': 'test-room', 'name': 'test-room', 'url': 'https://example.test/room'}), patch.object(app, 'provider', return_value={'token': 'isolated-provider-token'}) as provider:
            for actor in ('manager', 'dsa-1'):
                self.assertEqual(self.post('room', {**identity, 'consent': True}, tokens[actor])[0], 200)
            self.assertEqual(provider.call_args.args[1]['properties']['user_name'], 'Тестовый кандидат 1')
            self.assertEqual(self.post('room', {**identity, 'consent': True}, tokens['dsa-2'])[0], 403)
        act('manager', 'meeting.joined'); act('dsa-1', 'meeting.joined')
        with patch.object(app.MEDIA, 'finish'):
            act('manager', 'meeting.finish')
        act('manager', 'feedback.questions', questions='Как вы вернули клиента?')
        code, form = self.post('feedback/questions', identity, tokens['manager'])
        self.assertEqual(code, 200); q = form['questionnaire']
        act('manager', 'feedback', questionnaireId=q['id'], answers={q['questions'][0]['id']: {'rating': 'clear', 'comment': 'Пример'}})
        act('dsa-1', 'after', value='yes')
        act('manager', 'decision', value='pool')
        act('manager', 'offer.save', role='Менеджер', compensation='150000', format='Офис', expectations='Продажи', startDate='2099-01-20')
        act('manager', 'offer.send')
        candidate = act('dsa-1', 'offer.reply', value='accepted', version=1)
        self.assertEqual(candidate['closedBy'], 'dsa-1')
        self.assertNotIn('feedback', candidate['candidates']['dsa-1'])
        with app.connect() as db: self.assertEqual(before, app.read(db))
    def test_candidate_intake_persists_without_candidate_consent_and_is_private(self):
        with app.connect() as db: before = app.read(db)
        payload = {'scope': 'sfera', 'batch': str(uuid.uuid4()), 'entries': [{'name': 'Проверка интерфейса', 'role': 'Менеджер', 'contact': 'test@example.com', 'link': 'https://hh.ru/resume/example', 'note': 'Тест'}]}
        self.assertEqual(self.post('intake', payload, self.candidate)[0], 403)
        self.assertEqual(self.post('intake', payload)[0], 401)
        code, result = self.post('intake', payload, self.manager)
        self.assertEqual(code, 200)
        person = result['candidates'][0]
        listed = self.access_request('intake?scope=sfera', token=self.manager)[1]['candidates']
        self.assertIn(person, listed)
        self.assertFalse(person['archived'])
        self.assertEqual(self.access_request('intake?scope=sfera', token=self.candidate)[0], 403)
        self.assertNotIn(person, self.access_request('intake?scope=dsa', token=self.manager)[1]['candidates'])
        self.assertEqual(self.post('intake', payload, self.manager)[1], result)
        self.assertEqual(self.post('intake', {**payload, 'entries': [{'name': 'Другой'}]}, self.manager)[0], 409)
        self.assertEqual(self.post('intake/archive', {'id': person['id'], 'archived': True}, self.manager)[1]['candidate']['archived'], True)
        self.assertEqual(self.post('intake/archive', {'id': person['id'], 'archived': False}, self.manager)[1]['candidate']['archived'], False)
        with app.connect() as db: self.assertEqual(before, app.read(db))

    def test_resume_download_requires_employer_and_preserves_bytes(self):
        content = b'%PDF-1.4\n% isolated resume test'
        payload = {'scope': 'dsa', 'batch': str(uuid.uuid4()), 'entries': [{'name': 'Резюме', 'file': {'name': 'Test.pdf', 'data': base64.b64encode(content).decode()}}]}
        code, result = self.post('intake', payload, self.manager)
        self.assertEqual(code, 200)
        file = result['candidates'][0]['files'][0]
        self.assertEqual(self.access_request('intake/files/' + file['id'], token=self.candidate)[0], 403)
        req = urllib.request.Request(self.url + 'intake/files/' + file['id'], headers={'Authorization': 'Bearer ' + self.manager})
        with urllib.request.urlopen(req, timeout=5) as response:
            self.assertEqual(response.read(), content)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertIn('attachment', response.headers['Content-Disposition'])
        self.assertNotIn('data', file)

    def test_intake_validation_is_atomic_and_rejects_unsafe_links_and_files(self):
        for bad in [
            {'name': ''}, {'name': 'Тест', 'link': 'javascript:alert(1)'},
            {'name': 'Тест', 'link': 'https://user:secret@example.com'},
            {'name': 'Тест', 'file': {'name': '../test.pdf', 'data': 'AAAA'}},
            {'name': 'Тест', 'file': {'name': 'test.pdf', 'data': base64.b64encode(b'<script>x</script>').decode()}},
        ]:
            with app.connect() as db: before = db.execute('SELECT COUNT(*) FROM intake_candidates').fetchone()[0]
            payload = {'scope': 'sfera', 'batch': str(uuid.uuid4()), 'entries': [{'name': 'Valid first'}, bad]}
            self.assertEqual(self.post('intake', payload, self.manager)[0], 400)
            with app.connect() as db: self.assertEqual(before, db.execute('SELECT COUNT(*) FROM intake_candidates').fetchone()[0])

if __name__=='__main__': unittest.main()
