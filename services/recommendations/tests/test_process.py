"""Real independent processes, HTTP, SQLite, restart and operator commands."""

import http.client
import json
import os
from pathlib import Path
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from recommendations.engine import canonical_json, default_rubric
from fixtures import request_fixture


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.service_root = Path(__file__).resolve().parents[1]
        self.db = self.root / "results.sqlite3"
        self.token = "c3" * 32
        self.log = tempfile.TemporaryFile(mode="w+b")
        self.addCleanup(self.log.close)
        self.processes = []
        self.addCleanup(self.stop_all)
        self.start()

    def environment(self, db=None, port=None):
        env = dict(os.environ)
        env.update(PYTHONPATH=str(self.service_root), RECOMMENDATIONS_SERVICE_TOKEN=self.token,
                   RECOMMENDATIONS_DB=str(db or self.db), RECOMMENDATIONS_PORT=str(port or self.port))
        env.pop("RECOMMENDATIONS_HOST", None)
        return env

    def start(self, db=None):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        process = subprocess.Popen([sys.executable, "-m", "recommendations"], cwd=self.service_root,
                                   env=self.environment(db), stdout=self.log, stderr=self.log)
        self.processes.append(process)
        self.process = process
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail("service process failed to start")
            try:
                if self.http("GET", "/readyz")[0] == 200:
                    return process
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.01)
        self.fail("service readiness deadline")

    def stop_all(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)

    def stop(self, *, crash=False):
        self.process.send_signal(signal.SIGKILL if crash else signal.SIGINT)
        self.process.wait(timeout=5)

    def http(self, method, path, value=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        body = None if value is None else canonical_json(value).encode()
        conn.request(method, path, body=body, headers={"Authorization": "Bearer " + self.token,
                                                     "Content-Type": "application/json"})
        response = conn.getresponse()
        result = response.status, json.loads(response.read())
        conn.close()
        return result

    def command(self, *args):
        return subprocess.run([sys.executable, "-m", "recommendations", *map(str, args)],
                              cwd=self.service_root, env=self.environment(), timeout=5,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_crash_restart_immutable_get_and_database_ownership(self):
        status, result = self.http("POST", "/v1/recommendations", request_fixture(default_rubric()))
        self.assertEqual(status, 200)
        operation_id = result["recommendation_id"]
        # A second process with the same DB and another free port cannot own it.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            second_port = probe.getsockname()[1]
        second = subprocess.run([sys.executable, "-m", "recommendations"], cwd=self.service_root,
                                env=self.environment(port=second_port), timeout=5,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(second.returncode, 2)
        self.assertEqual(json.loads(second.stdout), {"error": "service_configuration_error"})
        self.stop(crash=True)
        self.start()
        self.assertEqual(self.http("GET", "/v1/recommendations/" + operation_id), (200, result))
        self.assertEqual(self.http("POST", "/v1/recommendations", request_fixture(default_rubric())), (200, result))
        self.assertEqual(self.http("GET", "/v1/operator")[1]["storage"]["saved_results"], 1)

    def test_backup_restore_commands_and_switch_to_restored_database(self):
        status, result = self.http("POST", "/v1/recommendations", request_fixture(default_rubric()))
        self.assertEqual(status, 200)
        backup = self.root / "backup.sqlite3"
        restored = self.root / "restore.sqlite3"
        created = self.command("backup", backup)
        self.assertEqual(created.returncode, 0)
        self.assertEqual(json.loads(created.stdout), {"status": "completed", "saved_results": 1})
        restored_command = self.command("restore", backup, restored)
        self.assertEqual(restored_command.returncode, 0)
        self.stop()
        self.start(restored)
        self.assertEqual(self.http("GET", "/v1/recommendations/" + result["recommendation_id"]), (200, result))

    def test_real_http_hundred_profile_load_latency_and_no_private_payload_logs(self):
        value = request_fixture(default_rubric(), count=100)
        started = time.monotonic()
        status, result = self.http("POST", "/v1/recommendations", value)
        post_seconds = time.monotonic() - started
        self.assertEqual(status, 200)
        self.assertEqual(len(result["items"]), 100)
        self.assertEqual(result["pool"]["size"], 100)
        self.assertLess(post_seconds, 2, "100-profile POST local acceptance threshold")
        started = time.monotonic()
        self.assertEqual(self.http("GET", "/v1/recommendations/" + result["recommendation_id"]), (200, result))
        get_seconds = time.monotonic() - started
        self.assertLess(get_seconds, 0.5, "saved GET local acceptance threshold")
        self.log.flush()
        self.log.seek(0)
        output = self.log.read().decode()
        self.assertNotIn(self.token, output)
        self.assertNotIn("work_locations", output)
        self.assertNotIn("Деловой диалог", output)
        records = [json.loads(line) for line in output.splitlines()]
        self.assertTrue(any(record.get("operation_id") == result["recommendation_id"] for record in records))
        print(canonical_json({"local_hundred_profile_post_seconds": post_seconds,
                              "saved_get_seconds": get_seconds, "request_bytes": len(canonical_json(value).encode())}))

    def test_configuration_rejects_public_binding_before_creating_storage(self):
        unused = self.root / "not-created.sqlite3"
        env = self.environment(unused)
        env["RECOMMENDATIONS_HOST"] = "0.0.0.0"
        failed = subprocess.run([sys.executable, "-m", "recommendations"], cwd=self.service_root, env=env,
                                timeout=5, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(failed.returncode, 2)
        self.assertFalse(unused.exists())


class PackagedImportTests(unittest.TestCase):
    def test_exported_package_uses_frozen_sibling_without_monorepo_or_extra_pythonpath(self):
        service_root = Path(__file__).resolve().parents[1]
        repository = service_root.parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            release = Path(temporary) / "release"
            release.mkdir()
            for source, name in ((service_root / "recommendations", "recommendations"),
                                 (service_root / "rubrics", "rubrics"),
                                 (repository / "packages/jev-client/jev_client", "jev_client")):
                shutil.copytree(source, release / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            env = dict(os.environ, PYTHONPATH=str(release))
            script = (
                "from pathlib import Path; import jev_client; "
                "from recommendations.engine import distribution, default_rubric; "
                "distribution([.49,.50]); assert default_rubric()['parameters']; "
                "assert Path(jev_client.__file__).resolve().parent == Path('jev_client').resolve()"
            )
            result = subprocess.run([sys.executable, "-c", script], cwd=release, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr.decode())


if __name__ == "__main__":
    unittest.main()
