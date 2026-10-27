import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

DEPLOY = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, DEPLOY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


artifact, ctl = load("artifact"), load("ctl")


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Path(self.temp.name).resolve()
        self.config.chmod(0o700)
        self.images = {"CORE_IMAGE": "md-materials-core:1.0.0-0123456789ab",
                       "SOURCE_IMAGE": "md-materials-source:0.1.0-0123456789ab", "HR_DB_UID": "997", "HR_DB_GID": "987"}
        self.core = {"CORE_HR_DATABASE": "/var/lib/hr-vision-api/state.sqlite3", "CORE_PRINCIPALS_JSON": json.dumps([
            {"token": "p" * 40, "name": "portal", "role": "portal"},
            {"token": "c" * 40, "name": "daily", "role": "source", "provider": "daily", "connection_id": "daily"}])}
        self.source = {"SOURCE_DAILY_API_KEYS": json.dumps({"s" * 40: "portal"}), "SOURCE_DAILY_CORE_TOKEN": "c" * 40,
                       "SOURCE_DAILY_DAILY_KEY": "test-provider-only", "SOURCE_DAILY_ENABLE_ASR": "false",
                       "SOURCE_DAILY_BROKER_URL": "amqp://md-source-daily:test-only@127.0.0.1:5672/source-daily"}
        self.write()

    def tearDown(self):
        self.temp.cleanup()

    def write(self):
        for name, values in (("compose", self.images), ("core", self.core), ("source", self.source)):
            path = self.config / f"{name}.env"
            path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
            path.chmod(0o600)

    def test_valid_literal_private_configuration(self):
        self.assertEqual(ctl.validate(self.config)[1], self.images)

    def test_unknown_env_and_shell_expansion_rejected(self):
        for line in ("DOCKER_HOST=tcp://outside:2375\n", "CORE_HOST=0.0.0.0\n", "CORE_S3_REGION=$(id)\n"):
            with self.subTest(line=line):
                self.write()
                with (self.config / "core.env").open("a") as output:
                    output.write(line)
                with self.assertRaises(ValueError):
                    ctl.validate(self.config)

    def test_source_cannot_select_hr_database_or_temp_mount(self):
        for key in ("SOURCE_DAILY_DB_PATH", "SOURCE_DAILY_TEMP_ROOT", "SOURCE_DAILY_CORE_URL"):
            with self.subTest(key=key):
                self.write()
                with (self.config / "source.env").open("a") as output:
                    output.write(key + "=/var/lib/hr-vision-api/state.sqlite3\n")
                with self.assertRaises(ValueError):
                    ctl.validate(self.config)

    def test_database_traversal_and_host_paths_rejected(self):
        for value in ("/etc/passwd", "/var/lib/hr-vision-api/../outside.db", "relative.db"):
            self.core["CORE_HR_DATABASE"] = value
            self.write()
            with self.assertRaises(ValueError):
                ctl.validate(self.config)

    def test_shared_or_external_broker_rejected(self):
        for url in ("amqp://guest:guest@127.0.0.1:5672/", "amqp://md-source-daily:x@rabbit:5672/source-daily",
                    "amqp://md-source-daily:x@127.0.0.1:5672/%2f"):
            self.source["SOURCE_DAILY_BROKER_URL"] = url
            self.write()
            with self.assertRaises(ValueError):
                ctl.validate(self.config)

    def test_unsafe_permissions_and_symlinks_rejected(self):
        path = self.config / "core.env"
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            ctl.validate(self.config)
        path.chmod(0o600)
        saved = self.config / "saved.env"
        path.rename(saved)
        path.symlink_to(saved)
        with self.assertRaises(ValueError):
            ctl.validate(self.config)

    def test_unpinned_image_and_cross_service_token_rejected(self):
        self.images["CORE_IMAGE"] = "md-materials-core:latest"
        self.write()
        with self.assertRaises(ValueError):
            ctl.validate(self.config)
        self.images["CORE_IMAGE"] = "md-materials-core:1.0.0-0123456789ab"
        self.source["SOURCE_DAILY_CORE_TOKEN"] = "p" * 40
        self.write()
        with self.assertRaises(ValueError):
            ctl.validate(self.config)

    def test_asr_requires_explicit_endpoint(self):
        self.source["SOURCE_DAILY_ENABLE_ASR"] = "true"
        self.write()
        with self.assertRaises(ValueError):
            ctl.validate(self.config)
        self.source["SOURCE_DAILY_TRANSCRIPTION_URL"] = "http://127.0.0.1:8093"
        self.write()
        ctl.validate(self.config)

    @unittest.skipUnless(shutil.which("docker"), "Docker CLI unavailable; no daemon needed")
    def test_compose_config_and_hardening_without_daemon(self):
        env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "MATERIALS_CONFIG_DIR": str(self.config)}
        result = subprocess.run(ctl.compose_command(self.config) + ["config", "--format", "json"],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, "Compose config validation failed")
        services = json.loads(result.stdout)["services"]
        for name, service in services.items():
            self.assertTrue(service["read_only"])
            self.assertEqual(service["network_mode"], "host")
            self.assertEqual(service["cap_drop"], ["ALL"])
            self.assertGreater(int(service["mem_limit"]), 0)
            self.assertGreater(int(service["pids_limit"]), 0)
            self.assertEqual(service["logging"]["options"]["max-file"], "3")
            mounts = {volume["source"] for volume in service.get("volumes", [])}
            self.assertEqual(mounts, {"/var/lib/hr-vision-api"} if name == "core" else {"/var/lib/md-source-daily"})
            if name != "core":
                self.assertEqual(service["environment"]["SOURCE_DAILY_TEMP_ROOT"], "/state/tmp")
                self.assertNotIn("CORE_HR_DATABASE", service["environment"])
        self.assertIn("127.0.0.1", services["source-api"]["command"])
        self.assertEqual(services["core"]["environment"]["CORE_HOST"], "127.0.0.1")
        self.assertEqual(services["core"]["user"], "997:987")
        self.assertEqual(services["source-worker"]["command"], ["worker", "--concurrency", "1"])

    def test_transcription_rejects_asr_configuration_and_unpinned_image(self):
        self.images["TRANSCRIPTION_IMAGE"] = "md-transcription:0.1.0-0123456789ab"
        self.write()
        path = self.config / "transcription.env"
        path.write_text('TRANSCRIPTION_API_KEYS={"daily-source":"' + "t" * 40 + '"}\n')
        path.chmod(0o600)
        ctl.validate_transcription(self.config)
        with path.open("a") as output:
            output.write("TRANSCRIPTION_ASR_URL=http://unapproved-engine\n")
        with self.assertRaises(ValueError):
            ctl.validate_transcription(self.config)

    @unittest.skipUnless(shutil.which("docker"), "Docker CLI unavailable")
    def test_transcription_standalone_compose_is_normalize_only(self):
        self.images["TRANSCRIPTION_IMAGE"] = "md-transcription:0.1.0-0123456789ab"
        self.write()
        path = self.config / "transcription.env"
        path.write_text('TRANSCRIPTION_API_KEYS={"daily-source":"' + "t" * 40 + '"}\n')
        path.chmod(0o600)
        env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "MATERIALS_CONFIG_DIR": str(self.config)}
        result = subprocess.run(ctl.compose_command(self.config, "transcription") + ["config", "--format", "json"],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, "Transcription Compose configuration failed")
        services = json.loads(result.stdout)["services"]
        self.assertEqual(set(services), {"api"})
        api = services["api"]
        self.assertEqual(api["environment"]["TRANSCRIPTION_ASR_URL"], "")
        self.assertNotIn("build", api)
        self.assertEqual(api["ports"][0]["host_ip"], "127.0.0.1")
        self.assertEqual(int(api["mem_limit"]), 268435456)


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "checkout"
        self.deploy = self.root / "services/deploy"
        (self.deploy / "manifests").mkdir(parents=True)
        for short, long in artifact.SERVICES.items():
            project = self.root / "services" / long
            (project / "src/pkg").mkdir(parents=True)
            (project / "src/pkg/__init__.py").write_text("value = 1\n")
            (project / "pyproject.toml").write_text('[project]\nname="test"\nversion="1.0.0"\ndependencies=["example==1.0.0"]\n')
            (self.deploy / f"Dockerfile.{short}").write_text("FROM scratch\n")
        (self.deploy / "requirements-runtime.lock").write_text("example==1.0.0 --hash=sha256:abc\n")
        self.root_patch = patch.object(artifact, "ROOT", self.root)
        self.deploy_patch = patch.object(artifact, "DEPLOY", self.deploy)
        self.root_patch.start()
        self.deploy_patch.start()
        (self.deploy / "manifests/source.json").write_bytes(artifact.encoded(artifact.manifest("source")))

    def tearDown(self):
        self.root_patch.stop()
        self.deploy_patch.stop()
        self.temp.cleanup()

    def test_deterministic_hash_checked_archive_excludes_env_and_database(self):
        project = self.root / "services/source-daily"
        (project / ".env").write_text("NOT_A_REAL_SECRET=test\n")
        (project / "jobs.sqlite3").write_bytes(b"not shipped")
        first = artifact.prepare("source", str(self.base / "output"))
        second = artifact.prepare("source", str(self.base / "other-output"))
        self.assertEqual(first["sha256"], second["sha256"])
        with tarfile.open(first["artifact"]) as archive:
            names = archive.getnames()
            self.assertIn("SHA256SUMS", names)
            self.assertIn("src/pkg/__init__.py", names)
            self.assertFalse(any(".env" in name or "sqlite" in name for name in names))

    def test_source_drift_and_new_unreviewed_module_rejected(self):
        source = self.root / "services/source-daily/src/pkg/__init__.py"
        source.write_text("value = 2\n")
        with self.assertRaises(ValueError):
            artifact.prepare("source", str(self.base / "output"))
        source.write_text("value = 1\n")
        (source.parent / "new.py").write_text("import os\n")
        with self.assertRaises(ValueError):
            artifact.prepare("source", str(self.base / "output"))

    def test_symlink_and_output_traversal_rejected(self):
        link = self.base / "linked-output"
        link.symlink_to(self.base, target_is_directory=True)
        for output in (str(link), str(self.base / "a/../output"), str(self.root / "output"), "/"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                artifact.prepare("source", output)
        for relative in ("../outside", "/etc/passwd", "services/./outside.py", "services/new\nname.py"):
            with self.assertRaises(ValueError):
                artifact.safe_file(self.root, relative)

    def test_dependency_pin_drift_rejected(self):
        (self.deploy / "requirements-runtime.lock").write_text("example==2.0.0\n")
        with self.assertRaises(ValueError):
            artifact.checked("source")

    def test_core_selected_compatibility_modules_are_explicit(self):
        with patch.object(artifact, "ROOT", DEPLOY.parents[1]):
            selected = artifact.selected("core")
        server_files = [p for p in selected if p.startswith("interface-lab/server/")]
        self.assertEqual(set(server_files), {"interface-lab/server/" + name for name in artifact.COMPAT})
        self.assertFalse(any("daily" in p for p in server_files))


if __name__ == "__main__":
    unittest.main()
