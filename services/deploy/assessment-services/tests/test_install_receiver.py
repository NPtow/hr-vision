import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('install_receiver', Path(__file__).resolve().parents[1] / 'install_receiver.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReceiverInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = self.root / 'receiver.py'
        self.new = self.root / 'reviewed.py'
        self.old.write_bytes(b'VERSION = 1\n')
        self.new.write_bytes(b'VERSION = 2\n')
        self.backups = self.root / 'backups'
        self.old_hash = hashlib.sha256(self.old.read_bytes()).hexdigest()
        self.new_hash = hashlib.sha256(self.new.read_bytes()).hexdigest()

    def install(self):
        return module.install(self.new, self.old, self.backups, self.old_hash,
                              self.new_hash, 'a' * 40, root_owned=False)

    def test_atomic_replacement_retains_private_exact_backup(self):
        receipt = self.install()
        self.assertEqual(self.old.read_bytes(), self.new.read_bytes())
        self.assertEqual(self.old.stat().st_mode & 0o777, 0o644)
        backup = Path(receipt['backup'])
        self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(), self.old_hash)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.backups.stat().st_mode & 0o777, 0o700)
        self.assertFalse(receipt['servicesRestarted'])

    def test_changed_installed_hash_refuses_before_backup_or_replacement(self):
        self.old.write_bytes(b'OTHER = 3\n')
        with self.assertRaisesRegex(ValueError, 'Installed receiver changed'):
            self.install()
        self.assertFalse(self.backups.exists())
        self.assertEqual(self.old.read_bytes(), b'OTHER = 3\n')

    def test_invalid_source_hash_syntax_or_permissions_preserves_installed_bytes(self):
        for content, mode in ((b'X = 4\n', 0o644), (b'def invalid syntax\n', 0o644), (b'VERSION = 2\n', 0o664)):
            with self.subTest(content=content):
                self.new.write_bytes(content)
                self.new.chmod(mode)
                if content.startswith(b'def'):
                    self.new_hash = hashlib.sha256(content).hexdigest()
                with self.assertRaises((ValueError, SyntaxError)):
                    self.install()
                self.assertEqual(hashlib.sha256(self.old.read_bytes()).hexdigest(), self.old_hash)


if __name__ == '__main__':
    unittest.main()
