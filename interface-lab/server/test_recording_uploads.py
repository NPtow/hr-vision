import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from domain import Problem
from recording_uploads import RecordingUploads
import test_employer_intake as fixtures
import employer_intake


class UploadProductTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.EmployerIntakeTest()
        self.fixture.setUp()
        self.connect = self.fixture.connect
        with self.connect() as db:
            db.execute('CREATE TABLE em_accounts(id TEXT PRIMARY KEY)')
            db.execute("INSERT INTO em_accounts VALUES ('account')")
            person = employer_intake.add(db, 'a', self.fixture.va, self.fixture.body())['candidates'][0]
        self.candidate = person['id']
        self.scope = {'source':'intake','id':self.candidate,'vacancyId':self.fixture.va}
        self.service = RecordingUploads(SimpleNamespace(connect=self.connect))
        self.auth = patch('recording_uploads.employer_access.authenticate', return_value={'workspaceId':'a','accountId':'account'})
        self.auth.start()
        self.addCleanup(self.auth.stop)
        self.calls = []
        self.core = SimpleNamespace(bind=lambda body: {'binding_id':'binding','generation':1}, materials=lambda *args: {'assets': []})
        def send(method,path,raw,headers):
            self.calls.append((method,path,json.loads(raw),headers))
            return {'id':'source-intent','status':'uploading','parts':[{'index':0,'status':'pending'}]}
        self.source = SimpleNamespace(_send=send,call=lambda *args: {'id':'source-intent','status':'registered','parts':[]})
        self.clients = patch.object(self.service,'_clients',return_value=(self.core,self.source))
        self.clients.start()
        self.addCleanup(self.clients.stop)

    def tearDown(self):
        self.fixture.tearDown()

    def body(self):
        return {**self.scope,'requestId':'stable-product-request-123',
                'files':[{'name':'part.wav','size':12,'content_type':'audio/wav'}]}

    def test_begin_recovers_same_intent_and_finalize_enqueues_product_assessment(self):
        first = self.service.begin({},self.body())
        again = self.service.begin({},self.body())
        self.assertEqual(first['id'],again['id'])
        self.assertEqual(self.calls[0],self.calls[1])
        result = self.service.command({},first['id'],'finalize',{})
        self.assertEqual(result['id'],first['id'])
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM hr_recording_uploads').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT kind,owner_id FROM as_bindings').fetchone(),('intake',self.candidate))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_interviews').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_candidates').fetchone()[0],0)

    def test_conflict_and_workspace_or_archive_checks_precede_service_calls(self):
        first = self.service.begin({},self.body())
        changed = self.body(); changed['files'][0]['size'] = 13
        with self.assertRaises(Problem): self.service.begin({},changed)
        with patch('recording_uploads.employer_access.authenticate',return_value={'workspaceId':'b','accountId':'account'}):
            with self.assertRaises(Problem): self.service.command({},first['id'],'status',{})
        with self.connect() as db:
            row = db.execute('SELECT body FROM em_intake_candidates WHERE id=?',(self.candidate,)).fetchone()
            person = json.loads(row[0]); person['archived'] = True
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?',(json.dumps(person),self.candidate))
        with self.assertRaises(Problem): self.service.stream_part({},first['id'],0,io.BytesIO(b'not read'),12)
        self.assertEqual(len(self.calls),1)

    def test_limits_and_missing_credentials_are_not_an_upload(self):
        body = self.body(); body['files'] *= 11
        with self.assertRaises(Problem): self.service.begin({},body)
        self.assertEqual(self.calls,[])


if __name__ == '__main__': unittest.main()
