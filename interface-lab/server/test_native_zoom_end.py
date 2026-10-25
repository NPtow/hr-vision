"""Native Zoom leave/end boundaries using product routes and real SQLite."""
import concurrent.futures
import json
import unittest
from unittest.mock import patch
import call_cleanup
from domain import Problem
import test_call_providers as fixture

class NativeZoomEndTest(unittest.TestCase):
    setUp = fixture.CallProvidersTest.setUp
    tearDown = fixture.CallProvidersTest.tearDown
    connect = fixture.CallProvidersTest.connect
    payload = fixture.CallProvidersTest.payload
    call = fixture.CallProvidersTest.call
    not_manager = staticmethod(fixture.CallProvidersTest.not_manager)
    register = fixture.CallProvidersTest.register
    slot = fixture.CallProvidersTest.slot
    select = fixture.CallProvidersTest.select
    join = fixture.CallProvidersTest.join

    def started(self, pin=False):
        self.select(); self.join(True)
        if pin: self.call('recruiter/interview/connected', self.identity, manager=True)
    def reconcile(self):
        return self.call('recruiter/interview/reconcile', self.identity, manager=True)[0]
    def saved(self):
        with self.connect() as db: return self.portal.interview(db, self.person['id'])
    def ended(self, session=None):
        return patch.object(self.zoom, 'get_state', return_value={'status':'ended','providerSessionId':session or self.zoom.session})

    def test_host_leave_never_ends_live_waiting_or_unknown_meeting(self):
        self.started(); before = self.saved()
        for state in ('started', 'waiting', 'unknown'):
            with self.subTest(state=state), patch.object(self.zoom, 'get_state', return_value={'status':state,'providerSessionId':self.zoom.session}):
                self.assertEqual(self.reconcile(), {'completed':False,'status':'live'})
                self.assertEqual(self.saved(), before)
        self.assertFalse(self.zoom.finished); self.assertFalse(self.media.finished)

    def test_native_end_saves_exact_session_and_replays_materials_registration(self):
        self.started(); captured=[]
        class Catalog:
            def register_owner(self, kind, meeting): captured.append((kind,meeting))
        self.portal.materials_catalog=Catalog()
        with self.ended('actual-ended-instance') as read:
            self.assertEqual(self.reconcile(), {'completed':True,'status':'completed'})
            self.assertEqual(self.reconcile(), {'completed':True,'status':'completed'})
            self.assertEqual(read.call_count,1)
        self.assertFalse(self.zoom.finished); self.assertFalse(self.media.finished)
        self.assertEqual(len(captured),2); self.assertEqual(captured[0][0],'interview')
        saved=self.saved(); self.assertEqual(saved['status'],'completed')
        self.assertTrue(saved['room']['sessionPinned'])
        self.assertEqual(saved['room']['meetingSessionId'],'actual-ended-instance')
        self.assertEqual(captured[0][1]['room'],saved['room'])

    def test_candidate_and_guest_cannot_reconcile(self):
        self.started()
        with patch.object(self.zoom,'get_state',side_effect=AssertionError('Unauthorized provider call')):
            for path,cookie in (('candidate/interview/reconcile',self.cookie),('candidate/guest/reconcile',self.cookie),('recruiter/interview/reconcile',self.cookie),('recruiter/interview/reconcile','')):
                with self.subTest(path=path),self.assertRaises(Problem): self.call(path,self.identity,cookie)
        self.assertEqual(self.saved()['status'],'live')

    def test_different_ended_uuid_cannot_complete_pinned_room(self):
        self.started(pin=True); before=self.saved()
        with self.ended('another-instance'):
            with self.assertRaises(Problem) as caught: self.reconcile()
        self.assertEqual(caught.exception.status,409); self.assertEqual(self.saved(),before); self.assertFalse(self.zoom.finished)

    def test_invalid_or_missing_ended_uuid_does_not_complete(self):
        self.started()
        for session in (None,'','a b',123):
            with self.subTest(session=session),patch.object(self.zoom,'get_state',return_value={'status':'ended','providerSessionId':session}):
                with self.assertRaises(Problem): self.reconcile()
                self.assertEqual(self.saved()['status'],'live')
        self.assertFalse(self.zoom.finished)

    def test_provider_error_is_not_completion(self):
        self.started()
        for status in (404,503):
            with patch.object(self.zoom,'get_state',side_effect=Problem('Provider unavailable',status)):
                with self.assertRaises(Problem): self.reconcile()
                self.assertEqual(self.saved()['status'],'live')
        self.assertFalse(self.zoom.finished)

    def test_read_does_not_hold_sqlite_writer_and_rechecks_access(self):
        self.started(); revoked=False
        def access(db):
            if revoked: raise Problem('Revoked',403)
        def read(room):
            nonlocal revoked
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE'); db.execute('UPDATE cp_interviews SET status=status WHERE id=?',(self.mid,))
            revoked=True
            return {'status':'ended','providerSessionId':self.zoom.session}
        with patch.object(self.zoom,'get_state',side_effect=read):
            with self.assertRaises(Problem) as caught: self.portal.reconcile_zoom_end(self.person['id'],meeting_id=self.mid,access_check=access)
        self.assertEqual(caught.exception.status,403); self.assertEqual(self.saved()['status'],'live'); self.assertFalse(self.zoom.finished)

    def test_replaced_room_during_provider_read_cannot_complete(self):
        self.started()
        def read(room):
            with self.connect() as db: db.execute('UPDATE cp_interviews SET room=? WHERE id=?',(json.dumps({**room,'meetingSessionId':'replacement'}),self.mid))
            return {'status':'ended','providerSessionId':self.zoom.session}
        with patch.object(self.zoom,'get_state',side_effect=read):
            with self.assertRaises(Problem): self.reconcile()
        self.assertEqual(self.saved()['status'],'live'); self.assertFalse(self.zoom.finished)

    def test_changed_provider_binding_is_not_overwritten(self):
        self.started(); room=self.saved()['room']
        with self.connect() as db: db.execute('UPDATE cp_call_provider_choices SET room=? WHERE meeting_id=?',(json.dumps({**room,'meetingSessionId':'different-binding'}),self.mid))
        with self.ended():
            with self.assertRaises(Problem): self.reconcile()
        self.assertEqual(self.saved()['status'],'live'); self.assertFalse(self.zoom.finished)

    def test_concurrent_native_ends_share_one_business_transition(self):
        self.started()
        with self.ended() as read:
            with concurrent.futures.ThreadPoolExecutor(2) as pool: results=list(pool.map(lambda _:self.reconcile(),range(2)))
        self.assertTrue(all(item['completed'] for item in results)); self.assertEqual(read.call_count,1); self.assertFalse(self.zoom.finished)

    def test_daily_is_not_reconciled_or_ended(self):
        self.join(True)
        with self.assertRaises(Problem) as caught: self.reconcile()
        self.assertEqual(caught.exception.status,409); self.assertFalse(self.media.finished)

    def test_background_recovery_after_closed_browser_has_same_boundary(self):
        self.started()
        with patch.object(self.zoom,'get_state',return_value={'status':'started','providerSessionId':self.zoom.session}) as read:
            first=call_cleanup.process_native_zoom_ends(self.portal); second=call_cleanup.process_native_zoom_ends(self.portal)
        self.assertFalse(first[0]['completed']); self.assertEqual(second,[]); self.assertEqual(read.call_count,1)
        with self.connect() as db: db.execute('UPDATE cp_zoom_lifecycle_checks SET next_attempt=0')
        with self.ended(): result=call_cleanup.process_native_zoom_ends(self.portal)
        self.assertTrue(result[0]['completed']); self.assertEqual(self.saved()['status'],'completed'); self.assertFalse(self.zoom.finished)

    def test_background_errors_are_retryable_and_never_send_end(self):
        self.started()
        with patch.object(self.zoom,'get_state',side_effect=Problem('Temporary',503)): result=call_cleanup.process_native_zoom_ends(self.portal)
        self.assertTrue(result[0]['retry']); self.assertEqual(self.saved()['status'],'live'); self.assertFalse(self.zoom.finished)

    def test_background_poll_limit_is_bounded(self):
        for limit in (0,21,True):
            with self.assertRaises(ValueError): call_cleanup.process_native_zoom_ends(self.portal,limit=limit)

    def attach_catalog(self):
        from materials_runtime import build_runtime
        from materials_service import MaterialsService
        from materials_storage import FileSystemStorage
        runtime = build_runtime(connect=self.connect, environ={'HR_MATERIALS_ENABLED':'1'}, captures={},
            storage=FileSystemStorage(self.tmp.name + '/materials'), publish=lambda _job: None)
        self.portal.materials_catalog = MaterialsService(runtime)
        return runtime.repository

    def test_completed_replay_repairs_crash_before_catalog_without_duplicate_source(self):
        self.started()
        # Business commit survived while catalogue registration did not run.
        with self.ended(): self.reconcile()
        repository = self.attach_catalog()
        self.assertEqual(repository.list_sources('interview', self.mid), [])
        with patch.object(self.zoom,'get_state',side_effect=AssertionError('Completed meeting needs no provider read')):
            self.reconcile(); self.reconcile()
        sources = repository.list_sources('interview', self.mid)
        self.assertEqual(len(sources),1)
        self.assertEqual(sources[0]['source_ref']['session_id'], self.zoom.session)
        self.assertFalse(self.zoom.finished)

    def test_background_repairs_completed_catalog_gap_without_opt_in_backfill(self):
        self.started()
        with self.ended(): self.reconcile()
        repository = self.attach_catalog()
        with patch.object(self.zoom,'get_state',side_effect=AssertionError('No provider call needed')):
            result = call_cleanup.process_native_zoom_ends(self.portal)
            again = call_cleanup.process_native_zoom_ends(self.portal)
        self.assertTrue(result[0]['completed']); self.assertEqual(again,[])
        self.assertEqual(len(repository.list_sources('interview',self.mid)),1)

if __name__=='__main__': unittest.main()
