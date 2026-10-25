"""Daily's independent source coexists with the exact legacy Zoom lifecycle."""
import json
import unittest
from unittest.mock import Mock, patch

from core_source_client import CoreMaterialsCatalog, ProviderMaterialsCatalog
from core_source_outbox import CompletionOutbox
from domain import Problem
import test_call_providers as zoom_fixtures
from test_core_source_integration import FakeCore, FakeSource


class ZoomServicesCoexistenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = zoom_fixtures.CallProvidersTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.portal, self.connect = self.fixture.portal, self.fixture.connect
        self.source, self.core = FakeSource(), FakeCore()
        self.portal.media = self.source
        self.outbox = CompletionOutbox(self.connect, self.core, self.source)
        self.portal.materials_outbox = self.outbox
        self.zoom_catalog = Mock()
        self.zoom_catalog.legacy_materials.return_value = {'status': 'ready', 'recordings': [], 'transcripts': []}
        self.daily_catalog = CoreMaterialsCatalog(self.core, self.source, self.outbox)
        self.portal.materials_catalog = ProviderMaterialsCatalog(self.daily_catalog, self.zoom_catalog)

    def outbox_rows(self):
        with self.connect() as db:
            return db.execute('SELECT payload FROM portal_materials_outbox').fetchall()

    def saved(self):
        with self.connect() as db:
            return self.portal.interview(db, self.fixture.person['id'])

    def complete(self):
        return self.fixture.call('recruiter/interview/complete', self.fixture.identity, manager=True)

    def test_daily_join_and_completion_use_typed_source_and_durable_outbox(self):
        credentials = self.fixture.join(True)
        self.assertEqual(credentials['provider'], 'daily')
        self.assertEqual(len(self.source.tokens), 1)
        self.assertFalse(hasattr(self.source, 'request'))
        self.complete()
        self.complete()
        rows = self.outbox_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0][0])['provider'], 'daily')
        self.assertEqual(self.core.bindings, [])
        self.assertEqual(self.outbox.dispatch_once()['state'], 'delivered')
        self.assertEqual(len(self.source.jobs), 1)
        self.zoom_catalog.register_owner.assert_not_called()
        self.assertFalse(self.fixture.zoom.created)

    def test_zoom_explicit_end_and_materials_never_reach_daily_source(self):
        self.fixture.select()
        with patch.object(self.source, 'token', side_effect=AssertionError('Zoom cannot request Daily token')):
            self.assertEqual(self.fixture.join(True)['provider'], 'zoom')
        with patch.object(self.source, 'start_capture', side_effect=AssertionError('Zoom cannot start Daily capture')):
            self.portal.start_interview(self.fixture.person['id'], True)
        with patch.object(self.source, 'finish', side_effect=AssertionError('Zoom cannot finish Daily call')):
            self.complete()
            self.complete()
        result, _ = self.fixture.call('recruiter/interview/materials',
            {**self.fixture.identity, 'provider': 'daily'}, manager=True)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(self.outbox_rows(), [])
        self.assertEqual(self.outbox.dispatch_once(), {'claimed': False})
        self.assertEqual(self.core.bindings, [])
        self.assertEqual(self.source.jobs, [])
        self.assertEqual(self.source.tokens, [])
        self.assertEqual(self.zoom_catalog.legacy_materials.call_args.args[1]['room']['provider'], 'zoom')
        self.assertTrue(self.saved()['room']['sessionPinned'])
        self.assertEqual(len(self.fixture.zoom.finished), 1)

    def test_native_zoom_end_and_completed_replay_keep_exact_instance_and_no_daily_intent(self):
        self.fixture.select()
        self.fixture.join(True)
        with patch.object(self.fixture.zoom, 'get_state', return_value={
                'status': 'ended', 'providerSessionId': 'actual-ended-session'}) as read:
            first, _ = self.fixture.call('recruiter/interview/reconcile', self.fixture.identity, manager=True)
            second, _ = self.fixture.call('recruiter/interview/reconcile', self.fixture.identity, manager=True)
        self.assertTrue(first['completed'])
        self.assertTrue(second['completed'])
        self.assertEqual(read.call_count, 1)
        self.assertEqual(self.zoom_catalog.register_owner.call_count, 2)
        self.assertEqual(self.saved()['room']['meetingSessionId'], 'actual-ended-session')
        self.assertEqual(self.outbox_rows(), [])
        self.assertEqual(self.core.bindings, [])
        self.assertFalse(self.fixture.zoom.finished)

    def test_missing_zoom_collector_and_unknown_provider_never_fall_back(self):
        daily = Mock()
        router = ProviderMaterialsCatalog(daily)
        for provider, status in (('zoom', 503), ('another-provider', 409)):
            meeting = {'id': 'synthetic-owner', 'room': {'provider': provider}}
            with self.subTest(provider=provider), self.assertRaises(Problem) as caught:
                router.register_owner('interview', meeting)
            self.assertEqual(caught.exception.status, status)
            with self.assertRaises(Problem):
                router.legacy_materials('interview', meeting)
        daily.register_owner.assert_not_called()
        daily.legacy_materials.assert_not_called()

    def test_daily_catalog_and_outbox_reject_zoom_even_if_accidentally_called_directly(self):
        self.fixture.select()
        self.fixture.join(True)
        meeting = self.saved()
        with self.connect() as db:
            self.assertIsNone(self.outbox.record(db, 'interview', meeting['id'], meeting['room']))
        for operation in (self.daily_catalog.register_owner, self.daily_catalog.legacy_materials):
            with self.assertRaises(Problem) as caught:
                operation('interview', meeting)
            self.assertEqual(caught.exception.status, 503)
        self.assertEqual(self.outbox_rows(), [])
        self.assertEqual(self.core.bindings, [])

    def test_owned_zoom_playback_survives_absent_provider_adapter_in_services_mode(self):
        from materials_runtime import build_runtime
        from materials_service import MaterialsService
        self.fixture.select()
        self.fixture.join(True)
        self.complete()
        storage = Mock(connection_id='synthetic-private-storage')
        storage.signed_url.return_value = 'https://owned.example.test/temporary-access'
        runtime = build_runtime(connect=self.connect, captures={}, storage=storage,
            publish=lambda _job: None, environ={'HR_MATERIALS_ENABLED': '1',
                'HR_MATERIALS_SERVICES_ENABLED': '1', 'HR_DATA_DIR': self.fixture.tmp.name})
        legacy = MaterialsService(runtime)
        meeting = self.saved()
        source = legacy.register_owner('interview', meeting)
        asset = runtime.repository.upsert_asset(source['id'], 'video', 'synthetic-zoom-video', state='ready')
        runtime.repository.add_location(asset['id'], 's3', 'synthetic-owned-key', state='verified',
            size=24, sha256='a' * 64, metadata={'storage_connection_id': storage.connection_id})
        self.portal.materials_catalog = ProviderMaterialsCatalog(self.daily_catalog, legacy)
        with patch.object(self.source, 'materials', side_effect=AssertionError('No Daily fallback')):
            result, _ = self.fixture.call('recruiter/interview/materials', self.fixture.identity, manager=True)
        self.assertEqual(result['recordings'][0]['storage'], 'owned')
        self.assertEqual(result['recordings'][0]['url'], storage.signed_url.return_value)
        self.assertEqual(result['video']['status'], 'ready')
        self.assertEqual(runtime.captures, {})
        self.assertEqual(self.outbox_rows(), [])


if __name__ == '__main__':
    unittest.main()
