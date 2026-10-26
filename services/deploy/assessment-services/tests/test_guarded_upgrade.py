"""Pure orchestration checks: no root changes, systemd, SSH or provider calls."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location('guarded_upgrade', Path(__file__).resolve().parents[1] / 'guarded_upgrade.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class GuardedUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.events = []
        self.active = 0
        self.install_fails = self.activation_fails = self.origin_fails = self.stopped_guard_fails = False
        self.api = self.root / 'api'
        self.base = self.root / 'ui'
        deployment = self.base / 'releases/existing/deployment.json'
        deployment.parent.mkdir(parents=True)
        deployment.write_text(json.dumps({'sha': 'a' * 40, 'target': 'service'}))
        self.receiver = SimpleNamespace(API=self.api, BASES={'service': self.base},
            pointer=lambda base: 'releases/existing', guard_and_backup=self.backup,
            pause_materials=self.pause, stop_api=lambda: self.events.append('stop'),
            activate_api=self.activate, check_origin=self.origin,
            resume_materials=lambda active: self.events.append(('resume', active)))

    def backup(self, tag):
        phase = 'stopped' if tag.endswith('-stopped') else 'before'
        self.events.append(('guard_backup', phase))
        if phase == 'stopped' and self.stopped_guard_fails:
            raise ValueError('live transition raced before stop')

    def pause(self):
        self.events.append('pause')
        return ['materials-worker']

    def activate(self, pointer):
        self.events.append(('activate', pointer))
        if self.activation_fails:
            raise ValueError('incompatible durable state')

    def origin(self, target, sha):
        self.events.append(('origin', target, sha))
        if self.origin_fails:
            raise ValueError('origin health failure')

    def fake_run(self, argv, **kwargs):
        self.assertEqual(argv[2], 'candidate')
        action = argv[3]
        self.events.append(action)
        if action == 'inspect':
            result = {'current': 'b' * 40, 'database': {'active': self.active}}
        else:
            self.assertIn('--consumers-quiesced', argv)
            self.assertIn('--legacy-candidate', argv)
            if self.install_fails:
                raise subprocess.CalledProcessError(1, argv)
            result = {'current': 'c' * 40}
        return SimpleNamespace(stdout=json.dumps(result))

    def execute(self):
        return module.execute(self.receiver, Path('/reviewed/operate.py'), Path('/artifact.tar.gz'),
                              'b' * 40, legacy=True, run=self.fake_run)

    def test_success_holds_pause_through_install_and_origin_verification(self):
        result = self.execute()
        self.assertEqual(result['existingUiSha'], 'a' * 40)
        self.assertEqual(self.events, ['inspect', ('guard_backup', 'before'), 'pause', 'stop',
            ('guard_backup', 'stopped'), 'install', ('activate', 'releases/existing'),
            ('origin', 'service', 'a' * 40), ('resume', ['materials-worker'])])

    def test_busy_core_refuses_before_hr_pause(self):
        self.active = 1
        with self.assertRaisesRegex(module.GuardFailure, 'jobs active'):
            self.execute()
        self.assertEqual(self.events, ['inspect'])

    def test_install_failure_recovers_original_api_and_resumes_materials(self):
        self.install_fails = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.execute()
        self.assertEqual(self.events[-3:], [('activate', 'releases/existing'),
            ('origin', 'service', 'a' * 40), ('resume', ['materials-worker'])])

    def test_stopped_live_guard_failure_prevents_install_but_recovers_api(self):
        self.stopped_guard_fails = True
        with self.assertRaisesRegex(ValueError, 'live transition'):
            self.execute()
        self.assertNotIn('install', self.events)
        self.assertEqual(self.events[-1], ('resume', ['materials-worker']))

    def test_unsafe_activation_or_origin_never_resumes_writers(self):
        for failure in ('activation_fails', 'origin_fails'):
            with self.subTest(failure=failure):
                self.events.clear()
                self.activation_fails = self.origin_fails = False
                setattr(self, failure, True)
                with self.assertRaisesRegex(module.GuardFailure, 'writers remain paused'):
                    self.execute()
                self.assertFalse(any(isinstance(item, tuple) and item[0] == 'resume' for item in self.events))
                self.assertEqual(self.events[-1], 'stop')


if __name__ == '__main__':
    unittest.main()
