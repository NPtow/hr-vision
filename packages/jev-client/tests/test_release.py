"""Release isolation and historical candidate import compatibility, offline."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1]
REPOSITORY = PACKAGE.parents[1]
CORE = REPOSITORY / "services/assessment-core"
spec = importlib.util.spec_from_file_location("jev_release_export", PACKAGE / "export_release.py")
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class SharedReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="jev-independent-release-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def invoke(self, code, *, pythonpath=None):
        environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                       "PYTHONPATH": str(pythonpath or self.root),
                       "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run([sys.executable, "-c", code], cwd=self.root,
                                env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_deterministic_independent_release_and_verified_hashes(self):
        manifest = exporter.export_release(self.root)
        before = {str(path.relative_to(self.root)): path.read_bytes()
                  for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(manifest, exporter.export_release(self.root))
        self.assertEqual(before, {str(path.relative_to(self.root)): path.read_bytes()
                                 for path in self.root.rglob("*") if path.is_file()})
        for name, digest in manifest["files"].items():
            self.assertEqual(hashlib.sha256((self.root / name).read_bytes()).hexdigest(), digest)
        self.assertEqual(json.loads((self.root / "shared-components.json").read_text()), manifest)
        self.invoke("from jev_client import JevClient, JevConfig, JevError, validate_response; "
                    "assert JevClient(JevConfig(api_key='offline')).config.model == 'jev-1.14.0'")

    def test_candidate_legacy_alias_and_installer_path_import_outside_checkout(self):
        shutil.copytree(CORE / "assessment_core", self.root / "assessment_core",
                        ignore=shutil.ignore_patterns("__pycache__"))
        exporter.export_release(self.root, rubric_name="analytics-baseline.json")
        self.invoke('''
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
import sys
from unittest.mock import patch
import assessment_core.providers.jev as old
import jev_client as shared
assert old is shared
assert old.JevError is shared.JevError
with patch('assessment_core.providers.jev.ENDPOINT', 'patched-for-both'):
    assert shared.ENDPOINT == 'patched-for-both'
spec = spec_from_file_location('core_jev_config', Path('assessment_core/providers/jev.py'))
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
assert module.JevConfig is shared.JevConfig
assert module.JevConfig(api_key='offline').model == 'jev-1.14.0'
''')
        # Match deploy/install.py: only its own deploy directory is importable.
        (self.root / "deploy").mkdir()
        self.invoke('''
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
import sys
sys.path = [p for p in sys.path if p not in ('', str(Path.cwd()))]
spec = spec_from_file_location('core_jev_config', Path('assessment_core/providers/jev.py'))
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
assert module.JevConfig(api_key='offline').model == 'jev-1.14.0'
''', pythonpath=self.root / "deploy")

    def test_original_transport_suite_passes_in_clean_export(self):
        shutil.copytree(CORE / "assessment_core", self.root / "assessment_core",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (self.root / "tests").mkdir()
        shutil.copyfile(CORE / "tests/test_jev.py", self.root / "tests/test_jev.py")
        exporter.export_release(self.root, rubric_name="analytics-baseline.json")
        self.invoke("import unittest; suite = unittest.defaultTestLoader.discover('tests'); "
                    "result = unittest.TextTestRunner().run(suite); "
                    "assert result.testsRun >= 28; assert result.wasSuccessful()")

    def test_conflicting_release_is_rejected_before_any_copy(self):
        (self.root / "rubrics").mkdir()
        conflict = self.root / "rubrics/sales-v1.json"
        conflict.write_text("previous-version")
        with self.assertRaisesRegex(ValueError, "different content"):
            exporter.export_release(self.root)
        self.assertFalse((self.root / "jev_client").exists())
        self.assertFalse((self.root / "shared-components.json").exists())
        self.assertEqual(conflict.read_text(), "previous-version")

    def test_release_symlink_cannot_write_outside_release(self):
        with tempfile.TemporaryDirectory(prefix="jev-outside-") as outside:
            (self.root / "jev_client").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "inside release"):
                exporter.export_release(self.root)
            self.assertFalse((Path(outside) / "__init__.py").exists())


if __name__ == "__main__":
    unittest.main()
