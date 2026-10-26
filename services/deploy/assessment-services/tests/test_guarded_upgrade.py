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
        self.assertEqual(argv[1], '-B')
        self.assertEqual(argv[3], 'candidate')
        action = argv[4]
        self.events.append(action)
        if action == 'inspect':
            self.assertIn('--legacy-candidate', argv)
            result = {'current': 'b' * 40, 'database': {'active': self.active}}
        else:
            self.assertIn('--consumers-quiesced', argv)
            self.assertIn('--legacy-candidate', argv)
            if self.install_fails:
                raise subprocess.CalledProcessError(1, argv)
            result = {'current': 'c' * 40}
        return SimpleNamespace(stdout=json.dumps(result))

    def execute(self, journal=None):
        return module.execute(self.receiver, Path('/reviewed/operate.py'), Path('/artifact.tar.gz'),
                              'b' * 40, legacy=True, run=self.fake_run, journal=journal)

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

    def test_unsafe_legacy_inventory_refuses_before_stopping_hr(self):
        def unsafe_legacy(argv, **kwargs):
            self.assertIn('--legacy-candidate', argv)
            self.events.append('inspect')
            raise subprocess.CalledProcessError(1, argv)
        with self.assertRaises(subprocess.CalledProcessError):
            module.execute(self.receiver, Path('/reviewed/operate.py'), Path('/artifact.tar.gz'),
                           'b' * 40, legacy=True, run=unsafe_legacy)
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

    def test_private_phase_journal_distinguishes_install_and_origin_failure(self):
        journal = module.PrivateJournal(self.root / 'private-journal', require_root=False)
        self.origin_fails = True
        with self.assertRaises(module.GuardFailure):
            self.execute(journal)
        document = module.inspect_journal(journal.path, require_root=False)
        phases = {(event['phase'], event['status']) for event in document['events']}
        self.assertIn(('candidate_install', 'passed'), phases)
        self.assertIn(('restore_api', 'passed'), phases)
        self.assertIn(('verify_origin', 'failed'), phases)
        self.assertNotIn(('resume_materials', 'started'), phases)
        self.assertEqual(journal.path.stat().st_mode & 0o777, 0o600)
        identity = next(event['details'] for event in document['events'] if event['phase'] == 'deployment_identity')
        self.assertEqual(identity['existing_ui_sha'], 'a' * 40)
        paused = next(event['details'] for event in document['events'] if event['phase'] == 'materials_pause_state')
        self.assertEqual(paused['paused_units'], ['materials-worker'])
        phases_in_order = [event['phase'] for event in document['events']]
        self.assertLess(phases_in_order.index('materials_pause_state'), phases_in_order.index('stop_api'))

    def test_journal_exception_metadata_never_contains_message_output_or_argv(self):
        journal = module.PrivateJournal(self.root / 'private-journal', require_root=False)
        try:
            try:
                raise subprocess.CalledProcessError(42, ['python', 'SECRET_ARG'], output='SECRET_STDOUT', stderr='SECRET_STDERR')
            except subprocess.CalledProcessError as original:
                raise ValueError('SECRET_MESSAGE') from original
        except ValueError as error:
            journal('candidate_install', 'failed', error=error)
        encoded = journal.path.read_text()
        self.assertNotIn('SECRET', encoded)
        chain = module.inspect_journal(journal.path, require_root=False)['events'][0]['exception_chain']
        self.assertEqual(chain, [{'type': 'ValueError'}, {'type': 'CalledProcessError', 'returncode': 42}])
        journal.path.chmod(0o644)
        with self.assertRaisesRegex(module.GuardFailure, 'Private operation journal'):
            module.inspect_journal(journal.path, require_root=False)


if __name__ == '__main__':
    unittest.main()
