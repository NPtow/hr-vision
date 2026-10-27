"""Owned imports use Core reads; portal viewing never schedules a capture."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from core_source_client import CoreMaterialsCatalog, ProviderMaterialsCatalog
from domain import Problem, require
import test_candidate_portal as fixtures


def imported_data():
    def version(identity, asset_id, words):
        return {'id': identity, 'asset_id': asset_id, 'version': 1, 'content_sha256': 'a' * 64,
            'segments': [{'text': words, 'start_ms': 0, 'end_ms': 1000, 'speaker': 'speaker_0'}],
            'metadata': {'origin': 'configured-asr', 'model_version': 'configured-model',
                'normalized': {'full_text': words, 'vtt': '', 'warnings': []}}}
    return {'sources': [{'id': 'upload-binding', 'binding_id': 'upload-binding', 'provider': 'upload',
                        'connection_id': 'upload', 'generation': 1, 'revoked': False}],
        'assets': [
            {'id': 'uploaded-video', 'source_id': 'upload-binding', 'kind': 'video', 'state': 'ready', 'metadata': {'duration': 1}},
            {'id': 'capture-text', 'source_id': 'upload-binding', 'kind': 'transcript', 'state': 'ready', 'external_id': 'capture'},
            {'id': 'selected-text', 'source_id': 'upload-binding', 'kind': 'transcript', 'state': 'ready', 'external_id': 'asr:configured'}],
        'transcripts': [version('capture-version', 'capture-text', 'Unselected original'),
                        version('selected-version', 'selected-text', 'Explicit selected words')],
        'channels': [{'binding_id': 'upload-binding', 'video_status': 'ready', 'transcript_status': 'ready'}],
        'transcript_selection_required_recording_ids': ['uploaded-video'],
        'transcript_selections': [{'binding_id': 'upload-binding', 'generation': 1, 'recording_id': 'uploaded-video',
            'request_id': 'source:job', 'revision': 1, 'status': 'ready', 'transcript_id': 'selected-version', 'error_code': None}]}


class UploadedCatalogTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CandidatePortalTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.portal, self.connect = self.fixture.portal, self.fixture.connect
        self.person, self.cookie = self.fixture.completed_person()
        self.room = {'id': 'upload-capture', 'name': 'owned-upload', 'provider': 'upload',
                     'connectionId': 'upload', 'captureInstanceId': 'upload-instance'}
        with self.connect() as db:
            self.owner_id = db.execute('SELECT id FROM cp_interviews WHERE candidate_id=?', (self.person['id'],)).fetchone()[0]
            db.execute('UPDATE cp_interviews SET room=? WHERE id=?', (json.dumps(self.room), self.owner_id))
            db.execute('DELETE FROM cp_call_provider_choices WHERE meeting_id=?', (self.owner_id,))
        self.core = Mock()
        self.core.materials.return_value = imported_data()
        self.core.access.return_value = {'storage': 'owned', 'url': 'https://private.example/owned-temporary', 'expires': 123}
        self.core.collect.side_effect = AssertionError('An imported recording cannot schedule collection')
        self.daily, self.zoom = Mock(), Mock()
        self.upload = CoreMaterialsCatalog(self.core, connect=self.connect, collect=False)
        self.router = ProviderMaterialsCatalog(self.daily, self.zoom, self.upload)
        self.portal.materials_catalog = self.router

    def test_completed_upload_renders_exact_selected_core_version_without_collector(self):
        with patch.object(self.fixture.media, 'materials', side_effect=AssertionError('No capture provider fallback')):
            result = self.portal.materials(self.person['id'])
        self.assertEqual(result['video']['status'], 'ready')
        self.assertEqual(result['recordings'][0]['id'], 'uploaded-video')
        self.assertEqual(result['recordings'][0]['storage'], 'owned')
        self.assertEqual([row['id'] for row in result['transcripts']], ['selected-version'])
        self.assertEqual(result['transcripts'][0]['fullText'], 'Explicit selected words')
        self.assertEqual(result['transcripts'][0]['selectionRevision'], 1)
        self.assertEqual(result['transcripts'][0]['segments'][0]['speaker'], 'speaker_0')
        self.assertEqual([row['id'] for row in result['transcriptVariants']], ['capture-version'])
        self.core.collect.assert_not_called()
        self.core.bind.assert_not_called()
        self.core.access.assert_called_once_with('uploaded-video')
        for call in self.core.materials.call_args_list:
            self.assertEqual(call.args, ('interview', self.owner_id))
        self.daily.register_owner.assert_not_called()
        self.daily.legacy_materials.assert_not_called()
        self.zoom.register_owner.assert_not_called()
        self.zoom.legacy_materials.assert_not_called()

    def test_actual_recruiter_materials_route_and_both_state_serializations_without_call_choice(self):
        with self.connect() as db:
            self.assertIsNone(self.portal.calls.choice(db, self.owner_id))
        def no_daily(): raise AssertionError('Imported DTOs must not resolve Daily')
        with patch.object(self.portal.calls, '_daily', no_daily):
            materials, _ = self.fixture.call('recruiter/interview/materials',
                {'candidateId': self.person['id'], 'interviewId': self.owner_id}, manager=True)
            candidate, _ = self.fixture.call('candidate/state', cookie=self.cookie)
            recruiter, _ = self.fixture.call('recruiter/state', manager=True)
        self.assertEqual(materials['transcripts'][0]['id'], 'selected-version')
        for state in (candidate['interview'], recruiter['candidates'][0]['interview']):
            self.assertIsNone(state['callProvider'])
            self.assertTrue(state['callProviderLocked'])
            self.assertFalse(state['videoConfigured'])
            self.assertEqual(state['callProviderOptions'], [])
            self.assertNotIn('room', state)
        self.core.collect.assert_not_called()
        with self.connect() as db:
            self.assertIsNone(self.portal.calls.choice(db, self.owner_id))

    def test_completed_upload_ignores_stale_daily_choice_without_mutating_it(self):
        with self.connect() as db:
            db.execute("INSERT INTO cp_call_provider_choices VALUES (?,'daily',NULL,'recruiter','ready',NULL,'old-choice')", (self.owner_id,))
            previous = self.portal.calls.choice(db, self.owner_id)
        with patch.object(self.portal.calls, '_daily', side_effect=AssertionError('No Daily descriptor fallback')):
            candidate, _ = self.fixture.call('candidate/state', cookie=self.cookie)
            self.assertIsNone(candidate['interview']['callProvider'])
            self.portal.materials(self.person['id'])
        with self.connect() as db:
            self.assertEqual(self.portal.calls.choice(db, self.owner_id), previous)
            with self.assertRaises(Problem): self.portal.calls.prepare_default(db, self.owner_id, self.room, 'recruiter')
            with self.assertRaises(Problem): self.portal.calls.select(db, self.owner_id, 'daily', 'recruiter', self.room)
            self.assertEqual(self.portal.calls.choice(db, self.owner_id), previous)

    def test_import_is_not_a_selectable_or_callable_provider_even_if_status_is_tampered(self):
        for operation in (lambda: self.portal.calls.for_room(self.room),
                          lambda: self.portal.calls.finish(self.room),
                          lambda: self.portal.calls.materials(self.room)):
            with self.assertRaises(Problem) as raised: operation()
            self.assertEqual(raised.exception.status, 409)
        with self.connect() as db:
            for provider, status in (('upload', 400), ('daily', 409), ('zoom', 409)):
                with self.assertRaises(Problem) as raised:
                    self.portal.calls.select(db, self.owner_id, provider, 'recruiter', self.room)
                self.assertEqual(raised.exception.status, status)
            with self.assertRaises(Problem): self.portal.calls.prepare_default(db, self.owner_id, self.room, 'recruiter')
            self.assertIsNone(self.portal.calls.choice(db, self.owner_id))
        for path in ('recruiter/interview/join', 'recruiter/interview/start'):
            with self.assertRaises(Problem) as raised:
                self.fixture.call(path, {'candidateId': self.person['id'], 'consent': True, 'oralConsent': True}, manager=True)
            self.assertEqual(raised.exception.status, 409)
        with self.connect() as db:
            db.execute("UPDATE cp_interviews SET status='booked' WHERE id=?", (self.owner_id,))
        for operation in (lambda: self.portal.materials(self.person['id']),
                          lambda: self.fixture.call('candidate/state', cookie=self.cookie)):
            with self.assertRaises(Problem) as raised: operation()
            self.assertEqual(raised.exception.status, 409)
        self.core.collect.assert_not_called()
        self.assertEqual(self.core.mock_calls, [])

    def test_read_only_registration_validates_without_any_service_io(self):
        meeting = {'id': self.owner_id, 'room': self.room}
        self.assertIsNone(self.upload.register_owner('interview', meeting))
        self.assertIsNone(self.upload.register_owner('interview', meeting, 'owned'))
        self.assertEqual(self.core.mock_calls, [])
        for kind, value, policy in [('candidate', meeting, None), ('interview', {'room': self.room}, None),
                                    ('interview', meeting, 'remote')]:
            with self.assertRaises(Problem): self.upload.register_owner(kind, value, policy)
        self.assertEqual(self.core.mock_calls, [])
        with self.assertRaises(Problem):
            CoreMaterialsCatalog(self.core, outbox=Mock(), collect=False)

    def test_access_callback_denies_before_read_and_catches_owner_change_during_access(self):
        def denied(db): raise Problem('owner_forbidden', 403)
        with self.assertRaises(Problem) as raised:
            self.portal.materials(self.person['id'], access_check=denied)
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(self.core.mock_calls, [])
        checks = []
        def check(db):
            checks.append(True)
            require(db.execute('SELECT id FROM cp_candidates WHERE id=?', (self.person['id'],)).fetchone(), 'owner_missing', 403)
        def changed(_asset):
            with self.connect() as db:
                db.execute('UPDATE cp_interviews SET room=? WHERE id=?',
                           (json.dumps({**self.room, 'captureInstanceId': 'different-upload'}), self.owner_id))
            return {'storage': 'owned', 'url': 'https://private.example/stale-access'}
        self.core.access.side_effect = changed
        with self.assertRaises(Problem) as raised:
            self.portal.materials(self.person['id'], access_check=check)
        self.assertEqual(raised.exception.status, 409)
        self.assertGreaterEqual(len(checks), 3)
        self.core.collect.assert_not_called()

    def test_wrong_meeting_and_pending_upload_cannot_read_capture_fallback(self):
        with self.assertRaises(Problem) as raised:
            self.portal.materials(self.person['id'], meeting_id='another-owner')
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(self.core.mock_calls, [])
        data = imported_data()
        data['transcript_selections'][0].update(status='pending', transcript_id=None)
        self.core.materials.return_value = data
        result = self.portal.materials(self.person['id'])
        self.assertEqual(result['transcripts'], [])
        self.assertEqual(result['transcription']['status'], 'processing')
        self.assertEqual({row['id'] for row in result['transcriptVariants']}, {'selected-version', 'capture-version'})

    def test_explicit_router_rejects_unsupported_or_unconfigured_provider(self):
        for provider, status in [('arbitrary-url', 409), ('upload', 503)]:
            router = ProviderMaterialsCatalog(self.daily, self.zoom)
            with self.assertRaises(Problem) as raised:
                router.register_owner('interview', {'id': 'owner', 'room': {'provider': provider}})
            self.assertEqual(raised.exception.status, status)
        for provider, expected in [('daily', self.daily), ('zoom', self.zoom), ('upload', self.upload)]:
            self.assertIs(self.router._catalog({'room': {'provider': provider}}), expected)
        self.daily.register_owner.assert_not_called()
        self.zoom.register_owner.assert_not_called()


class UploadedEmployerProjectionTests(unittest.TestCase):
    def test_actual_employer_http_state_keeps_completed_upload_noncallable_and_private(self):
        import test_employer_call_providers as helpers
        cls = helpers.EmployerCallProvidersTest
        with patch.dict(os.environ, {'HR_CORE_CATALOG_ENABLED': '0', 'HR_MATERIALS_SERVICES_ENABLED': '0'}):
            cls.setUpClass()
            self.addCleanup(cls.doClassCleanups)
            self.addCleanup(cls.tearDownClass)
            fixture = cls()
            self.addCleanup(fixture.doCleanups)
            fixture.setUp()
            room = {'provider': 'upload', 'connectionId': 'upload', 'id': 'private-upload-id',
                'name': 'private-upload-name', 'captureInstanceId': 'private-upload-instance'}
            with fixture.app.connect() as db:
                db.execute("UPDATE cp_meetings SET status='completed',room=?,completed_at=? WHERE id=?",
                           (json.dumps(room), fixture.meeting['end'], fixture.meeting['id']))
                db.execute('DELETE FROM cp_call_provider_choices WHERE meeting_id=?', (fixture.meeting['id'],))
            before = len(fixture.media.tokens), len(fixture.media.rooms), len(fixture.zoom.created)
            with patch.object(fixture.zoom, 'host_for', side_effect=AssertionError('Upload has no Zoom host mapping')):
                status, state, _ = fixture.request('employer/state?vacancyId=' + fixture.job['id'], token=fixture.owner['token'])
            self.assertEqual(status, 200, state)
            meeting = state['candidates'][fixture.aid]['meeting']
            self.assertEqual(meeting['status'], 'completed')
            self.assertIsNone(meeting['callProvider'])
            self.assertEqual(meeting['callProviderOptions'], [])
            self.assertFalse(meeting['videoConfigured'])
            self.assertTrue(meeting['callProviderLocked'])
            self.assertNotIn('room', meeting)
            for secret in room.values():
                if secret != 'upload': self.assertNotIn(secret, json.dumps(state))
            self.assertEqual((len(fixture.media.tokens), len(fixture.media.rooms), len(fixture.zoom.created)), before)
            foreign = fixture.employer('other-upload-workspace')
            status, _, _ = fixture.request('employer/state?vacancyId=' + fixture.job['id'], token=foreign['token'])
            self.assertEqual(status, 404)


class UploadedStartupTests(unittest.TestCase):
    def test_core_catalog_flag_wires_upload_to_read_only_core_without_network(self):
        code = '''import json,urllib.request
urllib.request.OpenerDirector.open=lambda *a,**k: (_ for _ in ()).throw(AssertionError('No startup service I/O'))
import app
router=app.CANDIDATE_PORTAL.materials_catalog
upload=router._catalog({'room':{'provider':'upload'}})
assert upload.register_owner('interview',{'id':'fixture-owner','room':{'provider':'upload'}}) is None
print(json.dumps({'router':type(router).__name__,'upload':type(upload).__name__,
    'collect':upload.collect_enabled,'outbox':upload.outbox is not None,
    'core_shared':upload.core is app.CORE_CLIENT,'zoom_collect':router.zoom.collect_enabled}))
'''
        for services_mode in ('0', '1'):
            with self.subTest(services_mode=services_mode), tempfile.TemporaryDirectory(prefix='uploaded-catalog-startup-') as root:
                env = {key: value for key, value in os.environ.items() if not key.startswith(('HR_', 'DAILY_', 'ZOOM_'))}
                env.update(HR_DATA_DIR=root, HR_TEAM_KEY='synthetic-team-key', HR_CORE_CATALOG_ENABLED='1',
                    HR_MATERIALS_SERVICES_ENABLED=services_mode, HR_MATERIALS_ENABLED='0', HR_VIDEO_ENABLED='0',
                    HR_MATERIALS_CORE_URL='http://127.0.0.1:8180', HR_MATERIALS_CORE_KEY='synthetic-core-key',
                    HR_MATERIALS_SOURCE_URL='http://127.0.0.1:8181', HR_MATERIALS_SOURCE_KEY='synthetic-source-key')
                result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).parent,
                    env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {'router': 'ProviderMaterialsCatalog', 'upload': 'CoreMaterialsCatalog',
                    'collect': False, 'outbox': False, 'core_shared': True, 'zoom_collect': True})


if __name__ == '__main__': unittest.main()
