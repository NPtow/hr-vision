"""Provider compatibility checks at the outgoing HTTP boundary."""
import copy
import json
import unittest
from unittest.mock import patch

from candidate_resume_model import schemas
from structured_model import ModelConfig, provider, validate


class StructuredTransportTest(unittest.TestCase):
    def test_gemini_nullable_enum_keeps_nullability_and_local_validation(self):
        schema = schemas([{'chunkId': 'cv_test', 'text': 'Synthetic CV'}])
        original = copy.deepcopy(schema)
        payload = {'model': 'gemini-3-flash', 'response_format': {
            'type': 'json_schema', 'json_schema': {
                'name': 'candidate_cv_extract', 'strict': True, 'schema': schema}}}
        with patch('structured_model.urllib.request.build_opener') as build:
            opener = build.return_value
            opener.open.return_value.__enter__.return_value.read.return_value = b'{"choices":[]}'
            provider(ModelConfig('https://cpa.example/v1', 'gemini-3-flash', 'test-key'),
                     payload, 'test-request', 45)
            request = opener.open.call_args.args[0]
            sent = json.loads(request.data)['response_format']['json_schema']['schema']
        employment = sent['properties']['profile']['properties']['employmentType']
        self.assertNotIn(None, employment['enum'])
        self.assertIn('null', employment['type'])
        self.assertEqual(schema, original)
        self.assertEqual(json.loads(request.data)['model'], 'gemini-3-flash')
        local = schema['properties']['profile']['properties']['employmentType']
        self.assertIsNone(validate(None, local))
        self.assertEqual(validate('full-time', local), 'full-time')
        with self.assertRaises(ValueError):
            validate('invented-employment', local)

    def test_other_provider_schema_is_unchanged(self):
        schema = schemas([{'chunkId': 'cv_test', 'text': 'Synthetic CV'}])
        payload = {'model': 'test-model', 'response_format': {
            'type': 'json_schema', 'json_schema': {
                'name': 'candidate_cv_extract', 'strict': True, 'schema': schema}}}
        with patch('structured_model.urllib.request.build_opener') as build:
            opener = build.return_value
            opener.open.return_value.__enter__.return_value.read.return_value = b'{"choices":[]}'
            provider(ModelConfig('https://cpa.example/v1', 'test-model', 'test-key'),
                     payload, 'test-request', 45)
            sent = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(sent, payload)
