"""Offline acceptance checks use synthetic bytes and never contact production."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('materials_acceptance', Path(__file__).with_name('materials-acceptance.py'))
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


class MemoryStorage:
    backend = 's3'

    def __init__(self, data):
        self.data = data
        self.key = self.object_key(hashlib.sha256(data).hexdigest())

    def object_key(self, digest):
        return 'materials/sha256/' + digest[:2] + '/' + digest

    def head(self, key):
        return SimpleNamespace(size=len(self.data), sha256=hashlib.sha256(self.data).hexdigest())

    def open(self, key):
        return io.BytesIO(self.data)


class AcceptanceHashTests(unittest.TestCase):
    def test_full_stream_digest_and_size(self):
        payload = b'video' * 300_000
        store = MemoryStorage(payload)
        acceptance.stream_hash(store, store.key, len(payload), hashlib.sha256(payload).hexdigest(), len(payload))
        with self.assertRaisesRegex(acceptance.Stop, 'object_digest_mismatch'):
            acceptance.stream_hash(store, store.key, len(payload), hashlib.sha256(b'other').hexdigest(), len(payload))
        with self.assertRaisesRegex(acceptance.Stop, 'object_size_mismatch'):
            acceptance.stream_hash(store, store.key, len(payload) - 1, hashlib.sha256(payload).hexdigest(), len(payload))

    def test_raw_transcript_must_match_catalog_and_actual_s3_bytes(self):
        payload = b'WEBVTT\n\n00:00:00.000 --> 00:00:01.000\ntext\n'
        store = MemoryStorage(payload)
        digest = hashlib.sha256(payload).hexdigest()
        raw = {'backend': 's3', 'object_key': store.key, 'key': store.key,
               'sha256': digest, 'size': len(payload)}
        transcript = {'id': 'revision', 'asset_id': 'asset', 'raw_ref': json.dumps(raw)}
        catalog = {'locations': [{'asset_id': 'asset', 'backend': 's3', 'state': 'verified',
                                  'object_key': store.key, 'sha256': digest, 'size': len(payload)}]}
        acceptance.verify_raw_text(transcript, catalog, store)
        corrupted = MemoryStorage(b'x' + payload[1:])
        with self.assertRaises(acceptance.Stop):
            acceptance.verify_raw_text(transcript, catalog, corrupted)
        catalog['locations'][0]['size'] += 1
        with self.assertRaisesRegex(acceptance.Stop, 'raw_transcript_reference_mismatch'):
            acceptance.verify_raw_text(transcript, catalog, store)

    def test_owned_access_probe_disables_daily_in_child(self):
        with patch.dict(acceptance.os.environ, {'DAILY_API_KEY': 'synthetic-only'}):
            with patch.object(acceptance.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
                acceptance.verify_offline_text('transcript-id', 'video-id')
        args, kwargs = run.call_args
        self.assertEqual(kwargs['env']['DAILY_API_KEY'], '')
        self.assertEqual(kwargs['env']['ACCEPT_VIDEO'], 'video-id')
        self.assertIn('build_runtime(captures={})', args[0][2])
        self.assertIn('get_asset_access', args[0][2])
        self.assertEqual(kwargs['stdout'], acceptance.subprocess.DEVNULL)


if __name__ == '__main__':
    unittest.main()
