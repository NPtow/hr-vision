"""Regression: the shipped catalogue must not reuse one generic level scale."""
import json
from pathlib import Path
import unittest

from assessment_core.engine import UNKNOWN, prepare


ROOT = Path(__file__).resolve().parents[1]


class WorkingRubricTests(unittest.TestCase):
    def test_working_questions_have_distinct_behavioural_anchors(self):
        rubric = json.loads((ROOT / 'rubrics/analytics-baseline.json').read_text())
        corpus = json.loads((ROOT / 'examples/corpus.synthetic.json').read_text())
        plan = prepare(corpus, rubric)
        expected = {'A01', 'A03', 'A04', 'A05', 'A06.1', 'A06.2', 'A07', 'A08',
                    'A10', 'A11.1', 'M01', 'M02', 'M03', 'M04', 'M05', 'NEW-01'}
        self.assertEqual(set(plan['request']['questions']), expected)
        self.assertNotEqual(rubric['version'], '2026-10-25.3-full-corpus-test.1')
        seen = {}
        for parameter in rubric['parameters']:
            pid = parameter['parameter_id']
            question = plan['request']['questions'][pid]
            self.assertEqual(set(question['criteria']), {'L1', 'L2', 'L3', 'L4', 'L5', UNKNOWN})
            for level in parameter['levels']:
                description = level['description'].strip()
                self.assertNotIn(description, seen,
                    f"{pid} reuses a behavioural anchor from {seen.get(description)}")
                seen[description] = pid
                self.assertEqual(question['criteria'][level['level_id']], description)
        self.assertEqual(len(seen), 80)


if __name__ == '__main__':
    unittest.main()
