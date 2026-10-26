"""A rubric release must not silently activate new runtime or interrupt a job."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('rubric_release',
    Path(__file__).resolve().parents[1] / 'deploy/activate_rubric_release.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RubricReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old, self.new = self.root / 'old', self.root / 'new'
        for release, version in [(self.old, '1'), (self.new, '2')]:
            for folder in ['assessment_core', 'deploy', 'rubrics']:
                (release / folder).mkdir(parents=True)
            (release / 'assessment_core/engine.py').write_text('RUNTIME = 1\n')
            (release / 'deploy/hr-vision-assessment-core.service').write_text('[Service]\n')
            (release / 'rubrics/analytics-baseline.json').write_text(json.dumps({'rubric_id': 'test', 'version': version}))
        self.db = self.root / 'runs.sqlite3'
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE runs(status TEXT)')

    def test_unchanged_runtime_and_new_rubric_are_allowed(self):
        self.assertEqual(module.check_release(self.old, self.new, self.db)['version'], '2')

    def test_runtime_change_is_rejected(self):
        (self.new / 'assessment_core/engine.py').write_text('RUNTIME = 2\n')
        with self.assertRaisesRegex(ValueError, 'Runtime changed'):
            module.check_release(self.old, self.new, self.db)

    def test_active_job_is_rejected(self):
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT INTO runs VALUES ('sent')")
        with self.assertRaisesRegex(ValueError, 'jobs are active'):
            module.check_release(self.old, self.new, self.db)

    def test_same_version_is_rejected(self):
        path = 'rubrics/analytics-baseline.json'
        (self.new / path).write_bytes((self.old / path).read_bytes())
        with self.assertRaisesRegex(ValueError, 'new version'):
            module.check_release(self.old, self.new, self.db)
