"""Real HR SQLite + service API doubles, no live models or delivery."""
import copy
import json
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import test_selection as fixture
import selection
import selection_facts as facts
import selection_status
import assessment_core_bridge as candidate
import vacancy_profile_bridge as vacancy
import recommendations_bridge as matching
from domain import Problem


class IntegrationTest(unittest.TestCase):
    def setUp(self):
        self.f=fixture.SelectionTest();self.f.setUp()
        self.connect,self.lock,self.portal=self.f.connect,self.f.portal.lock,self.f.portal
        self.vid=self.f.vacancy['id'];self.cid=self.f.person(consent=False);self.pending=self.f.person()
        self.rubric={'rubric_id':'sales','version':'test-v1','parameters':[{'parameter_id':'A01'}]}
        self.posts=[];self.denied=set();self.saved=None;self.profile=None
        self.vp=vacancy.Bridge(self.connect,self.lock,self.auth_v,self,self.rubric,'jev-1.14.0')
        self.rb=matching.Bridge(self.connect,self.lock,self.auth_v,self.auth_c,self.vp,SimpleNamespace(read=self.read_candidate),self)
        with self.connect() as db:
            candidate.init(db)
            for cid in (self.cid,self.pending):
                db.execute('INSERT INTO ac_bindings (id,candidate_id,source_revision,rubric_hash,model,manifest,run_id,status,created,updated,request_format) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    ('binding-'+cid,cid,'a'*64,self.vp.rubric_hash,self.vp.model,'{}',('c' if cid==self.cid else 'd')*64,'completed',time.time(),time.time(),candidate.REQUEST_FORMAT))
            self.evidence(db,self.cid)
    def tearDown(self):self.f.tearDown()
    def auth_v(self,vid):
        if vid in self.denied:raise Problem('denied',403)
        with self.connect() as db:selection.vacancy(db,vid)
    def auth_c(self,cid):
        if cid in self.denied:raise Problem('denied',403)
        with self.connect() as db:selection.person(db,cid)
    @staticmethod
    def evidence(db,cid,kind='interview',parts=True):
        return selection_status.save_evidence(db,cid,{'revision':'a'*64,'manifest':{'conversations':[{
            'owner_kind':kind,'lifecycle_status':'completed','status':'ready','primary_status':'ready',
            'recordings':[{'kind':'video','selection':{'status':'ready'},'transcript_id':'t1'},
            {'kind':'audio','selection':{'status':'ready' if parts else 'pending'},'transcript_id':'t2' if parts else None}]}]}})
    def read_candidate(self,cid):
        self.auth_c(cid)
        return {'state':'ready','coreRun':{'assessment':{'candidate_id':cid,'run_id':'c'*64}}} if cid==self.cid else {'state':'queued'}
    def call(self,method,path,body=None):
        if method=='POST':self.posts.append((path,copy.deepcopy(body)))
        if path=='/v1/vacancy-profiles':
            snap=body['vacancy'];self.profile={'contract_version':'vacancy-level-importance-v1','profile_id':'b'*64,'run_id':'b'*64,
                'vacancy_id':self.vid,'source_revision':snap['revision'],'vacancy_sha256':selection.digest(snap['data']),
                'rubric_id':'sales','rubric_version':'test-v1','rubric_sha256':self.vp.rubric_hash,'importance_version':'importance-0-3-v1',
                'model':'jev-1.14.0','parameters':[{'parameter_id':'A01','status':'ready'}]}
        if path.startswith('/v1/vacancy-profiles'):
            if path.endswith('/revisions'):
                self.profile={**self.profile,'profile_id':'e'*64,'revision':{'author':body['author'],'parent_profile_id':body['expected_parent_id']}}
                return {'profile':copy.deepcopy(self.profile)}
            if '/profiles/' in path:return {'profile':copy.deepcopy(self.profile)}
            base={**self.profile,'profile_id':'b'*64};base.pop('revision',None)
            return {'status':'completed','run_id':'b'*64,'profile':base,'error':None,'vacancy_revision':self.profile['source_revision'],
                    'rubric_version':'test-v1','model':'jev-1.14.0'}
        if path=='/v1/recommendations':
            vp=body['vacancy_profile'];self.saved={'contract_version':'recommendations-v1','recommendation_id':selection.digest(body),'vacancy_id':self.vid,
                'vacancy_profile_id':vp['profile_id'],'rubric_id':vp['rubric_id'],'rubric_version':vp['rubric_version'],'rubric_sha256':vp['rubric_sha256'],
                'policy':matching.POLICY,'items':[{'candidate_id':self.cid,'eligibility':'unknown','status':'scored','rank':1,'score':0.2,'required_checks':[],'parameters':[],'coverage':{}}]}
        return copy.deepcopy(self.saved)
    def start(self):
        with self.connect() as db:rev=selection.vacancy(db,self.vid)['contentHash']
        self.vp.start(self.vid,rev)
        return self.rb.start(self.vid,rev,[self.pending,self.cid])
    def test_pool_get_no_posts_no_outbox_and_stale_facts(self):
        value=self.start();self.assertEqual(value['pendingCandidates'],[{'candidateId':self.pending,'state':'queued'}]);sent=len(self.posts)
        self.assertEqual(self.rb.read(self.vid)['state'],'ready');self.assertEqual(len(self.posts),sent)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cn_outbox').fetchone()[0],0)
            current=facts.read(db,'candidate',self.cid);self.assertIsNone(current['fields']['work_locations'])
            facts.update(db,'candidate',self.cid,{'revision':current['revision'],'fields':{**current['fields'],'work_locations':['work-mode:remote']}})
        self.assertEqual(self.rb.read(self.vid)['state'],'stale')
    def test_authorization_identity_atomic_duplicate_pool(self):
        self.start();self.denied.add(self.cid)
        with self.assertRaises(Problem) as error:self.rb.read(self.vid)
        self.assertEqual(error.exception.status,403);self.denied.clear()
        with self.assertRaises(Problem):self.rb.validate({**self.saved,'vacancy_id':'other'},self.posts[-1][1])
        with self.assertRaises(Problem):self.rb.start(self.vid,'wrong',[self.cid,self.cid])
    def test_manual_profile_revision_stales_recommendation(self):
        value=self.start();revised=self.vp.revise(self.vid,{'revision':value['revision'],'profileId':'b'*64,'changes':[{'parameter_id':'A01','required_level_id':'L3','importance':2}]})
        self.assertEqual(revised['profile']['profile_id'],'e'*64);self.assertEqual(self.vp.read(self.vid)['profile']['profile_id'],'e'*64)
        self.assertEqual(self.rb.read(self.vid)['state'],'stale')
    def test_unknown_exact_approval_retained_without_legacy_or_fake_pass(self):
        value=self.start();body={'vacancyId':self.vid,'candidateIds':[self.cid],'requestId':'publish','recommendationId':value['recommendationId'],'revision':value['revision']}
        with self.connect() as db:
            with self.assertRaises(Problem):selection.nominate_recommendation(db,self.portal,body,value)
            with self.assertRaises(Problem):selection.nominate_recommendation(db,self.portal,{**body,'unknownApprovals':[self.cid,self.pending]},value)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cn_outbox').fetchone()[0],0)
            receipt=selection.nominate_recommendation(db,self.portal,{**body,'unknownApprovals':[self.cid]},value)
            self.assertEqual(receipt['waitingProfile'],[self.cid])
            approval=json.loads(db.execute('SELECT unknown_approval FROM sl_recommendation_nominations').fetchone()[0]);self.assertEqual(approval['recommendationId'],value['recommendationId'])
            self.assertIsNone(facts.read(db,'candidate',self.cid)['fields']['industry_months'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sl_core_results').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_applications').fetchone()[0],0)
            current=facts.read(db,'vacancy',self.vid);facts.update(db,'vacancy',self.vid,{'revision':current['revision'],'fields':{**facts.VACANCY,'employment':{'required':False,'allowed_ids':None}}})
            selection.reconcile_nominations(db,self.portal,self.cid)
            self.assertEqual(db.execute('SELECT state FROM sl_recommendation_nominations').fetchone()[0],'blocked')
    def test_hr_filter_both_all_parts_not_employer(self):
        with self.connect() as db:
            self.assertEqual(self.evidence(db,self.cid,kind='meeting')['state'],'missing')
            self.assertEqual(self.evidence(db,self.cid,parts=False)['state'],'incomplete')
            self.assertEqual(self.evidence(db,self.cid)['state'],'ready')
    def test_optimistic_edits_and_owned_catalogue(self):
        with self.connect() as db:
            current=facts.read(db,'candidate',self.cid);self.assertIn('options',current)
            with self.assertRaises(Problem):facts.update(db,'candidate',self.cid,{'revision':current['revision'],'fields':{**facts.CANDIDATE,'work_locations':['made-up']}})
            valid={**facts.CANDIDATE,'industry_months':{'logistics':12},'employment_types':['full-time']}
            edited=facts.update(db,'candidate',self.cid,{'revision':current['revision'],'fields':valid});self.assertNotEqual(edited['revision'],current['revision'])
            with self.assertRaises(Problem):facts.update(db,'candidate',self.cid,{'revision':current['revision'],'fields':valid})
    def test_directory_pages_complete_without_network(self):
        with self.connect() as db,patch('assessment_core_bridge.enabled',return_value=True):
            first=selection.directory(db,self.vid,limit=1);second=selection.directory(db,self.vid,limit=1,offset=first['nextOffset'])
            self.assertEqual(first['count'],2);self.assertNotEqual(first['candidates'][0]['id'],second['candidates'][0]['id']);self.assertIsNone(second['nextOffset'])

    def test_consent_continuation_revalidates_outside_lock_and_publishes(self):
        value=self.start()
        body={'vacancyId':self.vid,'candidateIds':[self.cid],'requestId':'consent-worker',
              'recommendationId':value['recommendationId'],'revision':value['revision'],'unknownApprovals':[self.cid]}
        with self.connect() as db:
            selection.nominate_recommendation(db,self.portal,body,value)
            profile=json.loads(db.execute('SELECT profile FROM cp_candidates WHERE id=?',(self.cid,)).fetchone()[0])
            db.execute('UPDATE cp_candidates SET profile=? WHERE id=?',(json.dumps({**profile,'consentReuse':True}),self.cid))
            selection.reconcile_nominations(db,self.portal,self.cid)
            self.assertEqual(db.execute('SELECT state FROM sl_recommendation_nominations').fetchone()[0],'pending_verification')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_applications').fetchone()[0],0)
        original=self.rb.read
        def checked(vid):
            self.assertFalse(self.lock._is_owned())
            return original(vid)
        self.rb.read=checked
        worker=matching.NominationWorker(self.connect,self.lock,self.portal,bridge_factory=lambda *args:self.rb)
        self.assertEqual(worker.run_once(),1)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM sl_recommendation_nominations').fetchone()[0],'proposed')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_applications').fetchone()[0],1)
            self.assertIsNone(facts.read(db,'candidate',self.cid)['fields']['industry_months'])
        self.assertEqual(worker.run_once(),0)

    def test_continuation_source_changed_without_local_binding_mutation_blocks(self):
        value=self.start()
        body={'vacancyId':self.vid,'candidateIds':[self.cid],'requestId':'source-changed',
              'recommendationId':value['recommendationId'],'revision':value['revision'],'unknownApprovals':[self.cid]}
        with self.connect() as db:
            selection.nominate_recommendation(db,self.portal,body,value)
            profile=json.loads(db.execute('SELECT profile FROM cp_candidates WHERE id=?',(self.cid,)).fetchone()[0])
            db.execute('UPDATE cp_candidates SET profile=? WHERE id=?',(json.dumps({**profile,'consentReuse':True}),self.cid))
            selection.reconcile_nominations(db,self.portal,self.cid)
        self.rb.candidate=SimpleNamespace(read=lambda cid:{'state':'stale'})
        worker=matching.NominationWorker(self.connect,self.lock,self.portal,bridge_factory=lambda *args:self.rb)
        self.assertEqual(worker.run_once(),1)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM sl_recommendation_nominations').fetchone()[0],'blocked')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_applications').fetchone()[0],0)

    def test_bounded_safe_get_retry_exhaustion_and_explicit_retry(self):
        value=self.start()
        body={'vacancyId':self.vid,'candidateIds':[self.cid],'requestId':'retry-budget',
              'recommendationId':value['recommendationId'],'revision':value['revision'],'unknownApprovals':[self.cid]}
        with self.connect() as db:
            selection.nominate_recommendation(db,self.portal,body,value)
            profile=json.loads(db.execute('SELECT profile FROM cp_candidates WHERE id=?',(self.cid,)).fetchone()[0])
            db.execute('UPDATE cp_candidates SET profile=? WHERE id=?',(json.dumps({**profile,'consentReuse':True}),self.cid))
            selection.reconcile_nominations(db,self.portal,self.cid)
        clock=[time.time()]
        broken=SimpleNamespace(read=lambda vid: (_ for _ in ()).throw(Problem('unavailable',503)))
        worker=matching.NominationWorker(self.connect,self.lock,self.portal,bridge_factory=lambda *args:broken,clock=lambda:clock[0])
        for attempt in range(matching.MAX_VERIFICATION_ATTEMPTS):
            self.assertEqual(worker.run_once(),1)
            if attempt == 0:self.assertEqual(worker.run_once(),0)
            clock[0]+=301
        self.assertEqual(worker.run_once(),0)
        with self.connect() as db:
            nid,state,attempts=db.execute('SELECT id,state,attempts FROM sl_recommendation_nominations').fetchone()
            self.assertEqual(state,'verification_failed');self.assertEqual(attempts,matching.MAX_VERIFICATION_ATTEMPTS)
            with self.assertRaises(Problem):
                selection.retry_recommendation_nomination(db,nid,{'revision':'stale','recommendationId':body['recommendationId']},value)
            selection.retry_recommendation_nomination(db,nid,body,value)
        worker.bridge_factory=lambda *args:self.rb
        self.assertEqual(worker.run_once(),1)
        with self.connect() as db:self.assertEqual(db.execute('SELECT state FROM sl_recommendation_nominations').fetchone()[0],'proposed')

    def test_new_human_approval_recovers_blocked_with_audit(self):
        first=self.start()
        oldbody={'vacancyId':self.vid,'candidateIds':[self.cid],'requestId':'old-approval','recommendationId':first['recommendationId'],
                 'revision':first['revision'],'unknownApprovals':[self.cid]}
        with self.connect() as db:
            selection.nominate_recommendation(db,self.portal,oldbody,first)
            db.execute("UPDATE sl_recommendation_nominations SET state='blocked',error='recommendation_stale'")
        self.vp.revise(self.vid,{'revision':first['revision'],'profileId':'b'*64,'changes':[{'parameter_id':'A01','required_level_id':'L3','importance':2}]})
        fresh=self.rb.start(self.vid,first['revision'],[self.cid,self.pending])
        self.assertNotEqual(fresh['recommendationId'],first['recommendationId'])
        body={**oldbody,'requestId':'fresh-approval','recommendationId':fresh['recommendationId']}
        with self.connect() as db:
            selection.nominate_recommendation(db,self.portal,body,fresh)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sl_recommendation_nomination_history').fetchone()[0],1)
            current=db.execute('SELECT recommendation_id,unknown_approval FROM sl_recommendation_nominations').fetchone()
            self.assertEqual(current[0],fresh['recommendationId'])
            self.assertEqual(json.loads(current[1])['recommendationId'],fresh['recommendationId'])
