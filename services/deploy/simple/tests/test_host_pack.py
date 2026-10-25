import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pack, run, verify = load("package"), load("run"), load("verify")


class HostPackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "checkout"
        self.control = self.root / "services/deploy/simple"
        (self.control / "manifests").mkdir(parents=True)
        (self.control / "run.py").write_text("# isolated runner\n")
        (self.control.parent / "requirements-runtime.lock").write_text("requests==2.34.2\n")
        zoom = self.root / "services/zoom-api"
        zoom.mkdir()
        (zoom / "app.py").write_text("import domain\n")
        server = self.root / "interface-lab/server"
        server.mkdir(parents=True)
        (server / "domain.py").write_text("from feedback import value\n")
        (server / "feedback.py").write_text("value=1\n")
        self.patches = [patch.object(pack, "ROOT", self.root), patch.object(pack, "HERE", self.control)]
        for item in self.patches:
            item.start()
        (self.control / "manifests/zoom.json").write_bytes(pack.encode(pack.manifest("zoom")))

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def test_exact_recursive_closure_excludes_portal_and_secrets(self):
        (self.root / "interface-lab/server/app.py").write_text("secret_portal=True\n")
        (self.root / "services/zoom-api/.env").write_text("PRIVATE=do-not-ship\n")
        result = pack.prepare("zoom", str(self.base / "output"))
        verified = verify.verify(result["archive"], result["sha256"])
        self.assertEqual(verified["release"], result["release"])
        with tarfile.open(result["archive"]) as archive:
            names = set(archive.getnames())
        self.assertIn("compat/feedback.py", names)
        self.assertIn("src/zoom_api.py", names)
        self.assertNotIn("compat/app.py", names)
        self.assertFalse(any(".env" in name or "sqlite" in name for name in names))

    def test_portal_dependency_is_rejected_even_when_lazy(self):
        (self.root / "interface-lab/server/domain.py").write_text("def f():\n import app\n")
        with self.assertRaises(ValueError):
            pack.manifest("zoom")

    def test_deterministic_archive_and_tampering_rejection(self):
        first = pack.prepare("zoom", str(self.base / "one"))
        second = pack.prepare("zoom", str(self.base / "two"))
        self.assertEqual(first["sha256"], second["sha256"])
        path = Path(first["archive"])
        path.write_bytes(path.read_bytes() + b"tampered")
        with self.assertRaises(ValueError):
            verify.verify(path, first["sha256"])

    def test_donor_drift_and_unsafe_paths_rejected(self):
        (self.root / "interface-lab/server/feedback.py").write_text("value=2\n")
        with self.assertRaises(ValueError):
            pack.prepare("zoom", str(self.base / "output"))
        for relative in ("../outside.py", "/etc/passwd", "services/a/../x.py", "services/evil\nname.py"):
            with self.assertRaises(ValueError):
                pack.safe_source(relative)
        link = self.base / "linked"
        link.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(ValueError):
            pack.prepare("zoom", str(link))

    def test_unsafe_archive_member_rejected_after_valid_outer_hash(self):
        path = self.base / "unsafe.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            item = tarfile.TarInfo("../escape.py")
            item.mode, item.size = 0o644, 3
            archive.addfile(item, io.BytesIO(b"bad"))
        with self.assertRaises(ValueError):
            verify.verify(path, hashlib.sha256(path.read_bytes()).hexdigest())


class IsolationTests(unittest.TestCase):
    def test_zoom_cannot_receive_database_aws_broker_and_cannot_change_spool(self):
        env = run.environment("zoom", {"ZOOM_API_TOKEN": "z" * 40, "CORE_HR_DATABASE": "private",
            "AWS_SECRET_ACCESS_KEY": "private", "CELERY_BROKER_URL": "private", "ZOOM_API_TEMP_ROOT": "/var/lib/hr-vision-api"})
        self.assertNotIn("CORE_HR_DATABASE", env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertNotIn("CELERY_BROKER_URL", env)
        self.assertEqual(env["ZOOM_API_TEMP_ROOT"], "/var/cache/hr-vision-zoom")

    def test_core_filters_oauth_and_broker_forces_loopback_and_zoom_scope(self):
        env = run.environment("core", {"CORE_HR_DATABASE": "/var/lib/hr-vision-api/state.sqlite3",
            "ZOOM_S2S_CLIENT_SECRET": "private", "ZOOM_MEETING_SDK_CLIENT_SECRET": "private",
            "CELERY_BROKER_URL": "private", "CORE_HOST": "0.0.0.0", "CORE_PORT": "9999"})
        self.assertNotIn("ZOOM_S2S_CLIENT_SECRET", env)
        self.assertNotIn("CELERY_BROKER_URL", env)
        self.assertEqual((env["CORE_HOST"], env["CORE_PORT"]), ("127.0.0.1", "8093"))
        with self.assertRaises(ValueError):
            run.environment("core", {"CORE_HR_DATABASE": "/var/lib/hr-vision-api/state.sqlite3",
                                      "HR_CORE_COLLECTOR_ENABLED": "1", "HR_MATERIALS_PROVIDERS": "daily"})

    def test_normalizer_cannot_start_asr_or_use_broker(self):
        env = run.environment("normalizer", {"TRANSCRIPTION_ASR_URL": "https://unapproved.example",
            "TRANSCRIPTION_ASR_API_KEY": "private", "TRANSCRIPTION_BROKER_URL": "amqp://private"})
        self.assertEqual(env["TRANSCRIPTION_ASR_URL"], "")
        self.assertEqual(env["TRANSCRIPTION_ASR_API_KEY"], "")
        self.assertEqual(env["TRANSCRIPTION_BROKER_URL"], "")


if __name__ == "__main__":
    unittest.main()
