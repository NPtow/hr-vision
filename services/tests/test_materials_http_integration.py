"""Real HTTP acceptance across actual core + source + transcription processes.

Run with /tmp/md-services-venv/bin/python -m pytest services/tests -q.
Only provider discovery/download and object storage are synthetic. No live
Daily, cloud storage, inference, broker, production database or media is used.
"""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading
import time

import pytest
import requests

from materials_http_harness import NORMALIZER, PORTAL, SOURCE, SOURCE_API, VIDEO, VTT


ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("materials_http_harness.py")
PYTHON = Path("/tmp/md-services-venv/bin/python")


class CoreRelay:
    """Forward real HTTP; optionally lose receipt responses after core commits."""
    def __init__(self, target):
        self.target, self.lose_receipt_response, self.deliveries = target, False, []
        self.session = requests.Session()
        self.session.trust_env = False
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def forward(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                upstream = relay.session.request(self.command, relay.target + self.path, data=body,
                    headers={"Authorization": self.headers.get("Authorization", ""),
                             "Content-Type": self.headers.get("Content-Type", "application/json")}, timeout=5)
                is_receipt = self.command == "POST" and self.path.endswith("/receipts")
                if is_receipt:
                    relay.deliveries.append({"body": json.loads(body), "status": upstream.status_code,
                                             "response": upstream.json()})
                lose = is_receipt and relay.lose_receipt_response
                payload = b'{"detail":"response_lost_fixture"}' if lose else upstream.content
                self.send_response(503 if lose else upstream.status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = forward

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_port)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.session.close()


class Stack:
    def __init__(self, path):
        assert Path(sys.executable).resolve() == PYTHON.resolve(), "Use /tmp/md-services-venv/bin/python"
        self.path, self.processes = path, []
        self.session = requests.Session()
        self.session.trust_env = False
        self.database = path / "hr.sqlite3"
        self.objects = path / "object-storage"
        self.source_private = path / "source-private"
        self.transcription_private = path / "transcription-private"
        self.seed_hr()
        self.core_url, _ = self.start("core", "--database", str(self.database))
        self.transcription_url, _ = self.start("transcription")
        self.relay = CoreRelay(self.core_url)

    def seed_hr(self):
        with sqlite3.connect(self.database) as db:
            db.executescript("""
                CREATE TABLE cp_candidates(id TEXT PRIMARY KEY,email TEXT,password_hash TEXT,profile TEXT,created_at TEXT,updated_at TEXT);
                CREATE TABLE cp_interviews(id TEXT PRIMARY KEY,candidate_id TEXT,status TEXT,room TEXT,created_at TEXT,completed_at TEXT);
                CREATE TABLE cp_applications(id TEXT PRIMARY KEY,candidate_id TEXT,status TEXT);
                CREATE TABLE cp_meetings(id TEXT PRIMARY KEY,application_id TEXT,status TEXT,room TEXT,start TEXT,end TEXT,completed_at TEXT);
            """)
            db.execute("INSERT INTO cp_candidates VALUES (?,?,?,?,?,?)", ("candidate-fixture", "PRIVATE-HR-EMAIL@example.org",
                "PRIVATE-HR-PASSWORD", json.dumps({"name": "PRIVATE-HR-NAME", "about": "PRIVATE-HR-PROFILE"}), "2026-01-01", "2026-01-01"))
            room = {"id": "room-fixture", "name": "hrv-interview-fixture", "captureInstanceId": "capture-fixture",
                    "meetingSessionId": "session-fixture", "provider": "daily", "connectionId": "daily"}
            db.execute("INSERT INTO cp_interviews VALUES (?,?,?,?,?,?)", ("interview-fixture", "candidate-fixture", "completed",
                json.dumps(room), "2026-01-01", "2026-01-01"))

    def start(self, role, *options):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        private = self.source_private if role == "source" else self.transcription_private if role == "transcription" else self.path / "core-private"
        log = (self.path / (role + "-" + str(len(self.processes)) + ".log")).open("w+")
        env = {key: value for key, value in os.environ.items() if not key.startswith(("TRANSCRIPTION_", "SOURCE_DAILY_", "CORE_"))}
        env["PYTHONUNBUFFERED"] = "1"
        env.pop("PYTHONPATH", None)
        process = subprocess.Popen([str(PYTHON), str(HARNESS), role, "--port", str(port),
            "--private", str(private), "--objects", str(self.objects), *options],
            cwd=ROOT, env=env, stdout=log, stderr=log)
        self.processes.append((process, log))
        url = "http://127.0.0.1:" + str(port)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                log.seek(0)
                raise AssertionError(role + " exited: " + log.read())
            try:
                if self.session.get(url + "/health/live", timeout=.2).status_code == 200:
                    return url, process
            except requests.RequestException:
                pass
            time.sleep(.03)
        raise AssertionError(role + " did not become ready")

    def start_source(self, transcript="valid", enable_asr=False):
        options = ["--core-url", self.relay.url, "--transcription-url", self.transcription_url, "--transcript", transcript]
        if enable_asr:
            options.append("--enable-asr")
        self.source_url, self.source_process = self.start("source", *options)

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def close(self):
        for process, log in reversed(self.processes):
            self.stop(process)
            log.close()
        if hasattr(self, "relay"):
            self.relay.close()
        self.session.close()

    def request(self, base, method, path, token, **kwargs):
        return self.session.request(method, base + path, headers={"Authorization": "Bearer " + token}, timeout=6, **kwargs)

    def binding(self, policy="remote"):
        response = self.request(self.core_url, "POST", "/v1/bindings", PORTAL, json={"schema_version": 1,
            "owner_kind": "interview", "owner_id": "interview-fixture", "provider": "daily", "connection_id": "daily",
            "source_ref": {"room_id": "room-fixture", "room_name": "hrv-interview-fixture", "capture_instance_id": "capture-fixture",
                           "session_id": "session-fixture"}, "storage_policy": policy})
        assert response.status_code == 200, response.text
        return response.json()

    def enqueue(self, binding):
        response = self.request(self.source_url, "POST", "/v1/jobs", SOURCE_API,
            json={key: binding[key] for key in ("binding_id", "generation")})
        assert response.status_code == 202, response.text
        return response.json()

    def await_job(self, identity, statuses=("succeeded",), seconds=10):
        deadline = time.monotonic() + seconds
        last = None
        while time.monotonic() < deadline:
            response = self.request(self.source_url, "GET", "/v1/jobs/" + identity, SOURCE_API)
            assert response.status_code == 200, response.text
            last = response.json()
            if last["status"] in statuses:
                return last
            if last["status"] in ("failed", "fenced"):
                raise AssertionError(last)
            time.sleep(.03)
        raise AssertionError("job did not finish: " + repr(last))

    def materials(self):
        response = self.request(self.core_url, "GET", "/v1/owners/interview/interview-fixture/materials", PORTAL)
        assert response.status_code == 200, response.text
        return response.json()

    def access(self, identity):
        response = self.request(self.core_url, "GET", "/v1/assets/" + identity + "/access", PORTAL)
        assert response.status_code == 200, response.text
        return response.json()


@pytest.fixture
def stack(tmp_path):
    value = Stack.__new__(Stack)
    try:
        value.__init__(tmp_path)
        yield value
    finally:
        value.close()


def assert_isolated_source(stack):
    state = json.loads((stack.source_private / "isolation.json").read_text())
    assert state["hr_modules_loaded"] is False
    audits = [json.loads(line) for line in (stack.source_private / "sqlite-audit.jsonl").read_text().splitlines()]
    assert any(not row["allowed"] for row in audits), "the SQLite guard was not exercised"
    assert {row["database"] for row in audits if row["allowed"]} == {str(stack.source_private / "source-jobs.sqlite3")}
    assert not (stack.source_private / "forbidden-probe.sqlite3").exists()
    with sqlite3.connect(stack.source_private / "source-jobs.sqlite3") as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {"source_jobs", "source_receipts", "source_artifacts", "source_webhooks"}
        contents = " ".join(str(row) for table in tables for row in db.execute("SELECT * FROM " + table))
    for forbidden in ("PRIVATE-HR-", "candidate-fixture", "owner_kind", "NEVER-PERSIST", "https://", SOURCE, NORMALIZER):
        assert forbidden not in contents


def test_remote_to_owned_preserves_ids_raw_and_real_normalization(stack):
    stack.start_source()
    binding = stack.binding()
    first = stack.enqueue(binding)
    done = stack.await_job(first["id"])
    assert done["acquisition_complete"] is True
    assert (done["video_status"], done["transcript_status"], done["transcription_status"]) == ("ready", "ready", "not_requested")
    remote = stack.materials()
    ids = {row["kind"]: row["id"] for row in remote["assets"]}
    assert set(ids) == {"video", "transcript"}
    assert stack.access(ids["video"])["storage"] == "remote"
    transcript = remote["transcripts"][0]
    raw = transcript["raw_ref"]
    assert (stack.objects / raw["object_key"]).read_bytes() == VTT
    normalized = transcript["metadata"]["normalized"]
    assert normalized["segments"] == [
        {"start_ms": 500, "end_ms": 2000, "text": "Hello & welcome", "speaker": "Recruiter"},
        {"start_ms": 2500, "end_ms": 3000, "text": "Candidate answer", "speaker": None}]
    assert normalized["warnings"] == ["missing_speakers"]
    assert transcript["metadata"]["origin"] == "daily_provider"
    assert transcript["metadata"]["coverage"] == "not_independently_verified"
    assert stack.enqueue(binding)["id"] == first["id"]
    downloads = [json.loads(line) for line in (stack.source_private / "downloads.jsonl").read_text().splitlines()]
    assert [row["kind"] for row in downloads] == ["transcript"]

    upgraded = stack.binding("owned")
    assert upgraded["binding_id"] == binding["binding_id"] and upgraded["generation"] == binding["generation"] + 1
    second = stack.enqueue(upgraded)
    assert second["id"] != first["id"]
    stack.await_job(second["id"])
    owned = stack.materials()
    assert {row["kind"]: row["id"] for row in owned["assets"]} == ids
    assert [row["id"] for row in owned["transcripts"]] == [transcript["id"]]
    access = stack.access(ids["video"])
    assert access["storage"] == "owned" and access["url"].startswith("https://object-fixture.example/materials/sha256/")
    video = next(row for row in owned["assets"] if row["kind"] == "video")
    location = next(row for row in video["locations"] if row["backend"] == "s3")
    assert location["state"] == "verified" and (stack.objects / location["object_key"]).read_bytes() == VIDEO
    assert location["metadata"]["verification"] == "head_metadata"
    assert all(row["status"] == 200 for row in stack.relay.deliveries)
    assert_isolated_source(stack)


def test_lost_receipt_response_then_source_restart_replays_without_duplicates(stack):
    stack.start_source()
    binding = stack.binding("owned")
    stack.relay.lose_receipt_response = True
    first = stack.enqueue(binding)
    waiting = stack.await_job(first["id"], ("waiting",))
    assert waiting["error_code"] == "upstream_503"
    stack.stop(stack.source_process)
    accepted = stack.materials()
    assert len(accepted["assets"]) == 2 and len(accepted["transcripts"]) == 1
    delivery = copy.deepcopy(stack.relay.deliveries[0])
    assert delivery["status"] == 200, delivery
    with sqlite3.connect(stack.source_private / "source-jobs.sqlite3") as db:
        pending = db.execute("SELECT body,delivered FROM source_receipts").fetchall()
    assert pending == [(json.dumps(delivery["body"], ensure_ascii=False, sort_keys=True, separators=(",", ":")), 0)]
    stack.relay.lose_receipt_response = False
    stack.start_source()
    assert stack.enqueue(binding)["id"] == first["id"]
    stack.await_job(first["id"])
    after = stack.materials()
    assert [row["id"] for row in after["assets"]] == [row["id"] for row in accepted["assets"]]
    assert [row["id"] for row in after["transcripts"]] == [row["id"] for row in accepted["transcripts"]]
    assert len(stack.relay.deliveries) >= 2
    assert all(row == delivery for row in stack.relay.deliveries)
    with sqlite3.connect(stack.database) as db:
        assert db.execute("SELECT count(*) FROM core_material_receipts").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM media_assets").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM transcript_versions").fetchone()[0] == 1
    with sqlite3.connect(stack.source_private / "source-jobs.sqlite3") as db:
        assert db.execute("SELECT delivered FROM source_receipts").fetchall() == [(1,)]
    assert_isolated_source(stack)


def test_actual_unconfigured_asr_keeps_remote_video_and_source_isolation(stack):
    stack.start_source(transcript="absent", enable_asr=True)
    binding = stack.binding()
    done = stack.await_job(stack.enqueue(binding)["id"])
    assert done["acquisition_complete"] is True and done["video_status"] == "ready"
    assert done["transcript_status"] == "not_recorded"
    assert done["transcription_status"] == "awaiting_configuration"
    materials = stack.materials()
    assert len(materials["assets"]) == 1 and materials["transcripts"] == []
    assert stack.access(materials["assets"][0]["id"])["storage"] == "remote"
    ready = stack.request(stack.transcription_url, "GET", "/health/ready", NORMALIZER)
    assert ready.status_code == 200 and ready.json()["normalization"] is True and ready.json()["transcription"] is False
    assert list((stack.transcription_private / "inputs").iterdir()) == []
    assert stack.request(stack.core_url, "GET", "/v1/candidates/candidate-fixture/dossier", SOURCE).status_code == 403
    assert stack.request(stack.core_url, "GET", "/v1/owners/interview/interview-fixture/materials", SOURCE).status_code == 403
    assert_isolated_source(stack)


def test_actual_normalizer_rejects_malformed_vtt_without_empty_success(stack):
    stack.start_source(transcript="malformed")
    done = stack.await_job(stack.enqueue(stack.binding())["id"])
    assert done["acquisition_complete"] is True and done["video_status"] == "ready"
    assert done["transcript_status"] == "error" and done["transcription_status"] == "failed"
    materials = stack.materials()
    assert len(materials["assets"]) == 1 and materials["transcripts"] == []
    assert list(stack.objects.rglob("*.metadata.json")), "malformed raw transcript must remain acquired"
    assert_isolated_source(stack)
