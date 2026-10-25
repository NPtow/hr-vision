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
