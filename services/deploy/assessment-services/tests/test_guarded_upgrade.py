"""Pure orchestration checks: no root changes, systemd, SSH or provider calls."""
import importlib.util
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

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
        self.api_source = self.api / 'releases/existing'
        self.api_source.mkdir(parents=True)
        self.api_runtime = frozenset({'app.py', 'domain.py'})
        for name in self.api_runtime:
            (self.api_source / name).write_text('# synthetic immutable API runtime\n')
        self.receiver = SimpleNamespace(API=self.api, BASES={'service': self.base}, RUNTIME_LAYOUTS={self.api_runtime},
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

    def test_extra_generated_pyc_aborts_before_backup_pause_stop_or_install_without_deleting_it(self):
        cache = self.api_source / '__pycache__'
        cache.mkdir()
        generated = cache / 'app.cpython-310.pyc'
        generated.write_bytes(b'synthetic generated Python bytecode')
        with self.assertRaisesRegex(module.GuardFailure, 'Incomplete original API runtime'):
            self.execute()
        self.assertEqual(self.events, ['inspect'])
        self.assertEqual(generated.read_bytes(), b'synthetic generated Python bytecode')

    def test_missing_original_runtime_file_aborts_before_backup_pause_stop_or_install(self):
        (self.api_source / 'domain.py').unlink()
        with self.assertRaisesRegex(module.GuardFailure, 'Incomplete original API runtime'):
            self.execute()
        self.assertEqual(self.events, ['inspect'])
        self.assertFalse((self.api_source / 'domain.py').exists())

    def test_original_runtime_symlink_is_refused_without_touching_its_target(self):
        source = self.api_source / 'domain.py'
        source.unlink()
        outside = self.root / 'external.py'
        outside.write_text('# external source\n')
        source.symlink_to(outside)
        with self.assertRaisesRegex(module.GuardFailure, 'Unsafe original API runtime'):
            self.execute()
        self.assertEqual(self.events, ['inspect'])
        self.assertTrue(source.is_symlink())
        self.assertEqual(outside.read_text(), '# external source\n')

    def test_original_api_pointer_change_aborts_before_backup_pause_stop_or_install(self):
        reads = [0]
        def pointer(base):
            if base == self.api:
                reads[0] += 1
                return 'releases/existing' if reads[0] == 1 else 'releases/changed'
            return 'releases/existing'
        self.receiver.pointer = pointer
        with self.assertRaisesRegex(module.GuardFailure, 'Original API pointer changed'):
            self.execute()
        self.assertEqual(self.events, ['inspect'])

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

    def test_vacancy_update_keeps_hr_paused_until_selected_service_health_return(self):
        self.check_selected_service_upgrade('vacancy')

    def test_recommendations_update_keeps_hr_paused_until_selected_service_health_return(self):
        self.check_selected_service_upgrade('recommendations')

    def check_selected_service_upgrade(self, service):
        calls = []
        def selected_run(argv, **kwargs):
            calls.append(argv)
            self.assertEqual(argv[1], '-B')
            self.assertEqual(argv[3], service)
            self.assertNotIn('--legacy-candidate', argv)
            action = argv[4]
            self.events.append(action)
            if action == 'inspect':
                self.assertIn('--validate-current', argv)
                result = {'current': 'b' * 40, 'database': {'active': 0}}
            else:
                self.assertEqual(action, 'install')
                self.assertEqual(self.events[-3:], ['stop', ('guard_backup', 'stopped'), 'install'])
                self.assertIn('--consumers-quiesced', argv)
                self.assertEqual(argv[argv.index('--expected-current') + 1], 'b' * 40)
                result = {'current': 'c' * 40}
            return SimpleNamespace(stdout=json.dumps(result))
        journal = module.PrivateJournal(self.root / 'selected-journal', require_root=False)
        result = module.execute(self.receiver, Path('/reviewed/operate.py'), Path('/artifact.tar.gz'),
                                'b' * 40, service=service, run=selected_run, journal=journal)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result[service]['current'], 'c' * 40)
        self.assertEqual(self.events[-3:], [('activate', 'releases/existing'),
            ('origin', 'service', 'a' * 40), ('resume', ['materials-worker'])])
        records = module.inspect_journal(journal.path, require_root=False)['events']
        self.assertTrue(any(event['phase'] == service + '_install' and event['status'] == 'passed' for event in records))
        with self.assertRaisesRegex(module.GuardFailure, 'only for candidate'):
            module.execute(self.receiver, Path('/reviewed/operate.py'), Path('/artifact.tar.gz'),
                           'b' * 40, service=service, legacy=True, run=selected_run)
        self.assertEqual(len(calls), 2)  # Invalid legacy flag refused before any subprocess/pause.

    def recovery_host(self, *, health_failure=False, quiet_failure=False, missing_lock=False, wal=False):
        release = self.root / 'candidate-release'
        release.mkdir(exist_ok=True)
        (release / 'release.json').write_text('{}')
        database = self.root / 'candidate.sqlite3'
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE synthetic_fixture (id TEXT)')
            if wal:
                db.execute('PRAGMA journal_mode=WAL')
        db.close()
        self.recovery_database_bytes = database.read_bytes()
        database.chmod(0o600)
        lock = Path(str(database) + '.owner.lock')
        if not missing_lock:
            lock.write_bytes(b'')
            lock.chmod(0o600)
        counts = {'health': 0, 'quiet': 0}
        @contextmanager
        def locked():
            self.events.append('candidate_lock')
            try:
                yield
            finally:
                self.events.append('candidate_unlock')
        def previous(commit):
            self.events.append(('candidate_previous', commit))
            return release
        def quiet():
            counts['quiet'] += 1
            self.events.append('candidate_quiet')
            if quiet_failure and counts['quiet'] == 2:
                raise module.GuardFailure('active work raced after candidate stop')
        def health():
            counts['health'] += 1
            self.events.append('candidate_health')
            if health_failure and counts['health'] >= 2:
                raise module.GuardFailure('candidate unavailable')
        host = SimpleNamespace(unit='hr-vision-assessment-core', db=database, locked=locked,
            previous=previous, validate_known=lambda path: self.events.append(('candidate_validate', path)),
            quiet=quiet, health=health,
            systemctl=lambda action, unit: self.events.append(('candidate_systemctl', action, unit)))
        self.recovery_release = release
        self.recovery_database = database
        return host

    def execute_recovery(self, *, cli_failure=False, health_failure=False, quiet_failure=False,
                         invalid_summary=False, missing_lock=False, wal=False, journal=None):
        host = self.recovery_host(health_failure=health_failure, quiet_failure=quiet_failure,
                                  missing_lock=missing_lock, wal=wal)
        before = host.db.stat()
        def fake_run(argv, **kwargs):
            if len(argv) > 4 and argv[3] == '-m':
                self.events.append('reconcile')
                self.assertEqual(argv, [module.sys.executable, '-B', '-s', '-m', 'assessment_core',
                    'recover-rejection', 'd' * 64, '--db', str(host.db), '--expected-body-sha256', 'e' * 64])
                self.assertEqual(kwargs['cwd'], str(self.recovery_release))
                self.assertEqual(kwargs['env'], {'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(self.recovery_release),
                    'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1'})
                self.assertTrue(kwargs['check'])
                self.assertTrue(kwargs['capture_output'])
                self.assertEqual(kwargs['timeout'], 120)
                self.assertNotIn('shell', kwargs)
                if cli_failure:
                    raise subprocess.CalledProcessError(2, argv, output='PRIVATE RESULT', stderr='PRIVATE ERROR')
                result = {'run_id': 'd' * 64, 'status': 'completed', 'provider_posts': 0,
                    'body_sha256': 'e' * 64, 'plan_sha256': 'f' * 64, 'response_sha256': 'a' * 64,
                    'audit_phase': 'completed'}
                if invalid_summary:
                    result['candidate_corpus'] = 'PRIVATE MATERIAL'
                return SimpleNamespace(stdout=json.dumps(result))
            return self.fake_run(argv, **kwargs)
        def constructor(service):
            self.assertEqual(service, 'candidate')
            return host
        try:
            with patch.object(module, 'installed_operator', return_value=SimpleNamespace(Host=constructor)):
                return module.execute(self.receiver, Path('/reviewed/operate.py'), Path('/artifact.tar.gz'),
                    'b' * 40, legacy=True, recover_rejection='d' * 64, expected_body_sha256='e' * 64,
                    run=fake_run, journal=journal)
        finally:
            after = host.db.stat()
            self.assertEqual((before.st_uid, before.st_gid, before.st_mode, before.st_ino),
                             (after.st_uid, after.st_gid, after.st_mode, after.st_ino))
            self.assertEqual(host.db.read_bytes(), self.recovery_database_bytes)

    def test_candidate_recovery_runs_installed_cli_under_pause_and_returns_to_dynamic_owner_before_hr(self):
        result = self.execute_recovery()
        self.assertEqual(result['rejectionRecovery']['provider_posts'], 0)
        self.assertEqual(self.events, ['inspect', ('guard_backup', 'before'), 'pause', 'stop',
            ('guard_backup', 'stopped'), 'install', 'candidate_lock', ('candidate_previous', 'c' * 40),
            ('candidate_validate', self.recovery_release), 'candidate_quiet', 'candidate_health',
            ('candidate_systemctl', 'stop', 'hr-vision-assessment-core'), 'candidate_quiet', 'reconcile',
            ('candidate_systemctl', 'start', 'hr-vision-assessment-core'), 'candidate_health', 'candidate_unlock',
            ('activate', 'releases/existing'), ('origin', 'service', 'a' * 40), ('resume', ['materials-worker'])])

    def test_candidate_recovery_cli_failure_restores_candidate_and_hr_but_returns_failure_with_private_audit(self):
        journal = module.PrivateJournal(self.root / 'recovery-journal', require_root=False)
        with self.assertRaises(subprocess.CalledProcessError):
            self.execute_recovery(cli_failure=True, journal=journal)
        self.assertEqual(self.events[-5:], ['candidate_health', 'candidate_unlock',
            ('activate', 'releases/existing'), ('origin', 'service', 'a' * 40), ('resume', ['materials-worker'])])
        records = module.inspect_journal(journal.path, require_root=False)['events']
        self.assertTrue(any(event['phase'] == 'candidate_recovery_reconcile' and event['status'] == 'failed'
                            for event in records))
        self.assertTrue(any(event['phase'] == 'candidate_recovery_health' and event['status'] == 'passed'
                            for event in records))
        self.assertNotIn('PRIVATE', journal.path.read_text())
        self.assertEqual(self.events.count('reconcile'), 1)

    def test_candidate_recovery_health_failure_keeps_hr_and_materials_paused(self):
        with self.assertRaisesRegex(module.CandidateRecoveryUnavailable, 'writers remain paused'):
            self.execute_recovery(health_failure=True)
        self.assertIn(('candidate_systemctl', 'start', 'hr-vision-assessment-core'), self.events)
        self.assertFalse(any(isinstance(event, tuple) and event[0] in ('activate', 'resume') for event in self.events))

    def test_candidate_recovery_rechecks_quiet_after_stop_and_resumes_after_race_refusal(self):
        with self.assertRaisesRegex(module.GuardFailure, 'raced after'):
            self.execute_recovery(quiet_failure=True)
        self.assertNotIn('reconcile', self.events)
        self.assertIn(('candidate_systemctl', 'start', 'hr-vision-assessment-core'), self.events)
        self.assertEqual(self.events[-1], ('resume', ['materials-worker']))

    def test_candidate_recovery_refuses_missing_runtime_lock_without_creating_root_owned_replacement(self):
        with self.assertRaisesRegex(module.GuardFailure, 'runtime lock required'):
            self.execute_recovery(missing_lock=True)
        self.assertNotIn('reconcile', self.events)
        self.assertFalse(Path(str(self.recovery_database) + '.owner.lock').exists())
        self.assertEqual(self.events[-1], ('resume', ['materials-worker']))

    def test_candidate_recovery_refuses_unexpected_summary_then_safely_restores_hr(self):
        with self.assertRaisesRegex(module.GuardFailure, 'summary does not confirm'):
            self.execute_recovery(invalid_summary=True)
        self.assertEqual(self.events[-1], ('resume', ['materials-worker']))

    def test_root_recovery_refuses_wal_without_creating_sidecars_or_changing_mode(self):
        with self.assertRaisesRegex(module.GuardFailure, 'rollback journaling'):
            self.execute_recovery(wal=True)
        self.assertNotIn('reconcile', self.events)
        self.assertFalse(Path(str(self.recovery_database) + '-wal').exists())
        self.assertFalse(Path(str(self.recovery_database) + '-shm').exists())
        self.assertEqual(self.recovery_database.read_bytes()[18:20], b'\x02\x02')
        self.assertEqual(self.events[-1], ('resume', ['materials-worker']))

    def test_recovery_pair_hash_and_service_validation_precede_any_subprocess_or_pause(self):
        invalid = [dict(recover_rejection='d' * 64), dict(expected_body_sha256='e' * 64),
            dict(recover_rejection='wrong', expected_body_sha256='e' * 64),
            dict(recover_rejection='d' * 64, expected_body_sha256='wrong'),
            dict(service='vacancy', recover_rejection='d' * 64, expected_body_sha256='e' * 64),
            dict(service='recommendations', recover_rejection='d' * 64, expected_body_sha256='e' * 64)]
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(module.GuardFailure):
                module.execute(self.receiver, Path('/reviewed/operate.py'), Path('/artifact.tar.gz'),
                               'b' * 40, run=self.fake_run, **options)
        self.assertEqual(self.events, [])


if __name__ == '__main__':
    unittest.main()
