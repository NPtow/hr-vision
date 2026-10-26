"""Isolated committed artifacts, operator locks, SQLite WAL, and real child processes."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ops, package = load('operate'), load('package')


class ProcessHost(ops.Host):
    """Real process starts/stops; no systemd/root/provider actions."""
    def __init__(self, root):
        super().__init__('recommendations', root, simulated=True)
        self.process = None
        self.fail_next_health = False
        self.on_stop = None

    def systemctl(self, action, *args):
        if action == 'stop':
            if self.process:
                self.process.terminate()
                self.process.wait(timeout=5)
                self.process = None
            if self.on_stop:
                callback, self.on_stop = self.on_stop, None
                callback()
        elif action == 'start':
            self.process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                            cwd=self.current, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif action not in ('enable', 'daemon-reload'):
            raise AssertionError(action)

    def health(self, timeout=30):
        self.assert_running()
        if self.fail_next_health:
            self.fail_next_health = False
            raise ValueError('Injected health failure')
        if not self.db.exists():
            self.db.parent.mkdir(parents=True)
            with sqlite3.connect(self.db) as db:
                db.execute('CREATE TABLE results(id TEXT PRIMARY KEY, value TEXT)')
                db.execute("INSERT INTO results VALUES('persisted','profile result')")

    def assert_running(self):
        assert self.process and self.process.poll() is None

    def close(self):
        self.systemctl('stop')


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'config', 'user.email', 'fixture@example.invalid'], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'config', 'user.name', 'fixture'], check=True)
        for name in ('packages/jev-client/export_release.py', 'packages/jev-client/jev_client/__init__.py',
                     'packages/assessment-contracts/rubrics/sales-v1.json',
                     'services/assessment-core/rubrics/analytics-baseline.json'):
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((REPO / name).read_bytes())
        directory = self.repo / 'services/recommendations'
        (directory / 'deploy').mkdir(parents=True)
        (directory / 'recommendations').mkdir()
        (directory / 'recommendations/__init__.py').write_text('VERSION = 1\n')
        (directory / 'deploy/hr-vision-recommendations.service').write_text('[Service]\nDynamicUser=yes\nMemoryMax=256M\nTasksMax=32\n')
        self.first = self.commit()
        self.artifact = self.root / 'first.tar.gz'
        package.build(self.repo, self.first, 'recommendations', self.artifact)
        (directory / 'recommendations/__init__.py').write_text('VERSION = 2\n')
        self.second = self.commit()
        self.second_artifact = self.root / 'second.tar.gz'
        package.build(self.repo, self.second, 'recommendations', self.second_artifact)
        self.host = ProcessHost(self.root / 'host')
        self.addCleanup(self.host.close)

    def commit(self):
        subprocess.run(['git', '-C', str(self.repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'commit', '-qm', 'fixture'], check=True)
        return subprocess.check_output(['git', '-C', str(self.repo), 'rev-parse', 'HEAD'], text=True).strip()

    def test_committed_packaging_reproducible_and_dirty_ignored(self):
        (self.repo / 'services/recommendations/recommendations/__init__.py').write_text('DIRTY_SECRET_NEVER_PACKAGE = 3\n')
        other = self.root / 'same.tar.gz'
        package.build(self.repo, self.first, 'recommendations', other)
        self.assertEqual(self.artifact.read_bytes(), other.read_bytes())
        release = self.host.unpack(other)
        self.assertEqual((release / 'recommendations/__init__.py').read_text(), 'VERSION = 1\n')
        self.assertTrue((release / 'jev_client/__init__.py').exists())
        (release / 'recommendations/__init__.py').write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'inventory/hash'):
            ops.validate_release(release, 'recommendations', ownership=False)

    def test_install_update_failed_health_and_controlled_rollback_keep_data(self):
        self.host.install(self.artifact, 'absent')
        original = self.host.db.read_bytes()
        self.host.fail_next_health = True
        with self.assertRaisesRegex(ValueError, 'previous release/config restored'):
            self.host.install(self.second_artifact, self.first)
        self.assertEqual(self.host.current.resolve().name, self.first)
        self.assertEqual(self.host.db.read_bytes(), original)
        self.host.assert_running()
        self.host.install(self.second_artifact, self.first)
        self.host.rollback(self.first, self.second)
        self.assertEqual(self.host.current.resolve().name, self.first)
        with sqlite3.connect(self.host.db) as db:
            self.assertEqual(db.execute('SELECT value FROM results').fetchone(), ('profile result',))

    def test_occupied_canonical_port_refused_without_touching_owner(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            self.host.spec = dict(self.host.spec, port=listener.getsockname()[1])
            with self.assertRaisesRegex(ValueError, 'port is occupied'):
                self.host.check_port_available()
            self.assertGreaterEqual(listener.fileno(), 0)
            self.assertFalse(self.host.current.exists())
        self.host.check_port_available()

    def test_failed_initial_install_preserves_data_and_can_retry(self):
        health = self.host.health
        def fail_after_creating_database():
            health()
            raise ValueError('Injected initial failure')
        self.host.health = fail_after_creating_database
        with self.assertRaisesRegex(ValueError, 'Initial install failed'):
            self.host.install(self.artifact, 'absent')
        self.assertTrue(self.host.db.exists())
        self.assertFalse(self.host.current.is_symlink())
        self.assertIsNone(self.host.process)
        self.host.health = health
        self.host.install(self.artifact, 'absent')
        self.host.assert_running()
        with sqlite3.connect(self.host.db) as db:
            self.assertEqual(db.execute('SELECT value FROM results').fetchone(), ('profile result',))

    def test_bad_expected_current_and_busy_operator_refuse_before_mutation(self):
        self.host.install(self.artifact, 'absent')
        with self.assertRaisesRegex(ValueError, 'Current release changed'):
            self.host.install(self.second_artifact, '0' * 40)
        contender = ProcessHost(self.host.root)
        with self.host.locked():
            with self.assertRaises(BlockingIOError):
                with contender.locked():
                    pass
        self.assertEqual(self.host.current.resolve().name, self.first)

    def test_active_and_raced_jobs_preserved(self):
        self.host.install(self.artifact, 'absent')
        # Candidate/vacancy use the same update machinery with a runs status gate.
        self.host.spec = dict(self.host.spec, table='runs')
        with sqlite3.connect(self.host.db) as db:
            db.execute('CREATE TABLE runs(status TEXT)')
            db.execute("INSERT INTO runs VALUES('sent')")
        with self.assertRaisesRegex(ValueError, 'Active queued'):
            self.host.install(self.second_artifact, self.first)
        self.host.assert_running()
        with sqlite3.connect(self.host.db) as db:
            db.execute('DELETE FROM runs')
        def race():
            with sqlite3.connect(self.host.db) as db:
                db.execute("INSERT INTO runs VALUES('queued')")
        self.host.on_stop = race
        with self.assertRaisesRegex(ValueError, 'previous release/config restored'):
            self.host.install(self.second_artifact, self.first)
        self.host.assert_running()
        self.assertEqual(self.host.current.resolve().name, self.first)
        with sqlite3.connect(self.host.db) as db:
            self.assertEqual(db.execute('SELECT status FROM runs').fetchone(), ('queued',))
            db.execute('DELETE FROM runs')
        self.host.install(self.second_artifact, self.first)
        self.host.on_stop = race
        with self.assertRaisesRegex(ValueError, 'Rollback failed; starting release restored'):
            self.host.rollback(self.first, self.second)
        self.host.assert_running()
        self.assertEqual(self.host.current.resolve().name, self.second)
        with sqlite3.connect(self.host.db) as db:
            self.assertEqual(db.execute('SELECT status FROM runs').fetchone(), ('queued',))

    def test_actual_recommendations_process_health_update_and_rollback(self):
        # Freeze the current runtime in an isolated Git fixture: no main-repo commit.
        runtime = self.repo / 'services/recommendations/recommendations'
        shutil.rmtree(runtime)
        shutil.copytree(REPO / 'services/recommendations/recommendations', runtime,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        first = self.commit()
        first_artifact = self.root / 'real-first.tar.gz'
        package.build(self.repo, first, 'recommendations', first_artifact)
        (runtime / 'release_marker.py').write_text('MARKER = 2\n')
        second = self.commit()
        second_artifact = self.root / 'real-second.tar.gz'
        package.build(self.repo, second, 'recommendations', second_artifact)
        class RealHost(ProcessHost):
            def systemctl(host, action, *arguments):
                if action != 'start':
                    return super().systemctl(action, *arguments)
                environment = dict(os.environ)
                environment.update(ops.read_env(host.envfile))
                environment['RECOMMENDATIONS_DB'] = str(host.db)
                environment['PYTHONPATH'] = str(host.current)
                environment['PYTHONDONTWRITEBYTECODE'] = '1'
                host.process = subprocess.Popen([sys.executable, '-m', 'recommendations'],
                    cwd=host.current, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            def health(host, timeout=5):
                if host.fail_next_health:
                    host.fail_next_health = False
                    raise ValueError('Injected health failure')
                ops.Host.health(host, timeout=timeout)
        host = RealHost(self.root / 'real-host')
        self.addCleanup(host.close)
        with socket.socket() as available:
            available.bind(('127.0.0.1', 0))
            port = available.getsockname()[1]
        host.spec = dict(host.spec, port=port)
        host.install(first_artifact, 'absent')
        with sqlite3.connect(host.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM recommendations').fetchone(), (0,))
        host.fail_next_health = True
        with self.assertRaisesRegex(ValueError, 'previous release/config restored'):
            host.install(second_artifact, first)
        self.assertEqual(host.current.resolve().name, first)
        host.install(second_artifact, first)
        host.rollback(first, second)
        host.health()
        self.assertEqual(host.current.resolve().name, first)

    def test_live_wal_backup_isolated_restore_and_hash_tamper(self):
        self.host.install(self.artifact, 'absent')
        with sqlite3.connect(self.host.db) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute("INSERT INTO results VALUES('WAL','raw provider response')")
            db.commit()
            backup = self.host.backup()
        destination = self.root / 'isolated/restored.sqlite3'
        ops.restore_isolated(backup, destination)
        with sqlite3.connect(destination) as db:
            self.assertEqual(db.execute("SELECT value FROM results WHERE id='WAL'").fetchone(), ('raw provider response',))
        with self.assertRaisesRegex(ValueError, 'isolated'):
            ops.restore_isolated(backup, Path('/var/lib/should-never-write.sqlite3'))
        backup.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            ops.restore_isolated(backup, self.root / 'other.sqlite3')


if __name__ == '__main__':
    unittest.main()
