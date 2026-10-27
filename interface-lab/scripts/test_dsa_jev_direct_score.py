import copy
import json
import tempfile
import unittest
from pathlib import Path
from dsa_jev_direct_score import score_view, render, build_request


class DirectScoreTests(unittest.TestCase):
    def answer(self):
        return {'score': .63, 'confidence': .34, 'probabilities': {'0': .62, '1': .24, '2': .06, '3': .08, '4': 0}}

    def fixture(self):
        criterion = {'id': 'A08', 'level': None, 'state': 'insufficient', 'evidence': [
            {'id': 'source-1', 'text': 'Professional evidence', 'seconds': 12, 'turnIds': [2, 3]}
        ]}
        response = {'model': 'jev-1.14.0', 'answers': {'A08-score': self.answer(), 'A08-material': {'noul': .8}}}
        return criterion, response

    def test_retains_actual_score_despite_rounded_probability_buckets(self):
        criterion, response = self.fixture()
        result = score_view(self.answer(), response, criterion)
        self.assertEqual(result['value'], 1.63)
        self.assertEqual(result['confidence'], .34)
        self.assertEqual(result['evidenceIds'], ['source-1'])
        self.assertEqual(criterion['level'], None)

    def test_rejects_corrupt_distribution(self):
        criterion, response = self.fixture()
        for change in [{'score': float('nan')}, {'confidence': 1.2}, {'score': 3.9}, {'probabilities': {'0': .6, '1': .6, '2': 0, '3': 0, '4': 0}}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                score_view({**self.answer(), **change}, response, criterion)

    def test_missing_core_scale_cannot_silently_become_handoff_scale(self):
        criterion, _ = self.fixture()
        criterion['label'] = 'Professional skill'
        candidate = {'assessment': {'candidate': {'name': 'Test Person'}, 'criteria': [criterion]}, 'turns': []}
        with self.assertRaisesRegex(ValueError, 'No explicit scale for skill A08'):
            build_request(candidate, 'Professional vacancy', {})

    def test_merge_is_source_bound_and_preserves_old_policy(self):
        criterion, response = self.fixture()
        empty = {'id': 'A03', 'level': None, 'state': 'insufficient', 'evidence': []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'jev').mkdir()
            payload = {'candidates': [{'id': 'test', 'assessment': {'criteria': [criterion, empty]},
                'provenance': {}, 'turns': [{'id': 2, 'seconds': 12, 'endSeconds': 15}, {'id': 3, 'seconds': 15, 'endSeconds': 22}]}]}
            (root / 'assessment.json').write_text(json.dumps(payload))
            (root / 'jev/direct-score-test-response.json').write_text(json.dumps(response))
            explanation = {'candidateId': 'test', 'criterionId': 'A08', 'evidenceId': 'source-1', 'explanation': 'Observed approach.', 'feedbackQuestion': 'Was the next step clear?'}
            (root / 'reviewed-explanations.json').write_text(json.dumps({'items': [explanation]}))
            self.assertEqual(render(root), {'candidates': 1, 'scores': 1, 'explanations': True})
            saved = json.loads((root / 'assessment.json').read_text())['candidates'][0]['assessment']['criteria']
            self.assertIsNone(saved[0]['level'])
            self.assertEqual(saved[0]['state'], 'insufficient')
            self.assertEqual(saved[0]['evidence'][0]['endSeconds'], 22)
            self.assertEqual(saved[0]['evidence'][0]['feedbackQuestion'], explanation['feedbackQuestion'])
            self.assertNotIn('modelEstimate', saved[1])
            # A binding to another excerpt must fail before replacing the payload.
            before = (root / 'assessment.json').read_bytes()
            wrong = copy.deepcopy(explanation); wrong['evidenceId'] = 'different-source'
            (root / 'reviewed-explanations.json').write_text(json.dumps({'items': [wrong]}))
            with self.assertRaises(ValueError):
                render(root)
            self.assertEqual((root / 'assessment.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
