"""Check the release patch against real source files, without running an hour call.

Set DOGRAH_RELEASE_ROOT=/app inside the unmodified 1.48.0 image. The local default
uses the checkout, whose recording source must match the same release hash.
"""
import ast
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from patch_duration import patch


FILES = (
    "api/schemas/workflow_configurations.py",
    "api/services/call_concurrency/rate_limiter.py",
    "api/services/pipecat/in_memory_buffers.py",
)


class DurationRecordingCapacityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source_root = Path(os.environ.get("DOGRAH_RELEASE_ROOT", Path(__file__).parents[2]))
        for name in FILES:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_root / name, target)

    def test_hour_recording_fits_patched_track_limit(self):
        patch(self.root)
        source = (self.root / FILES[2]).read_text()
        tree = ast.parse(source)
        assignments = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Attribute) and target.attr == "_max_size"
                    for target in node.targets)
        ]
        self.assertEqual(len(assignments), 1)
        capacity = eval(compile(ast.Expression(assignments[0].value), "<capacity>", "eval"),
                        {"__builtins__": {}})
        self.assertEqual(capacity, 128 * 1024 * 1024)
        self.assertGreaterEqual(capacity, 3600 * 16000 * 2)
        self.assertIn("MAX_CALL_DURATION_SECONDS = 3600", (self.root / FILES[0]).read_text())
        self.assertIn("self.stale_call_timeout = 3900", (self.root / FILES[1]).read_text())

    def test_patch_is_idempotent(self):
        patch(self.root)
        first = [(self.root / name).read_bytes() for name in FILES]
        patch(self.root)
        self.assertEqual(first, [(self.root / name).read_bytes() for name in FILES])

    def test_unknown_recording_source_rejects_without_partial_write(self):
        recording = self.root / FILES[2]
        recording.write_text(recording.read_text() + "\n# Unexpected upstream change\n")
        before = [(self.root / name).read_bytes() for name in FILES]
        with self.assertRaisesRegex(RuntimeError, "Unsupported upstream recording code"):
            patch(self.root)
        self.assertEqual(before, [(self.root / name).read_bytes() for name in FILES])


if __name__ == "__main__":
    unittest.main()
