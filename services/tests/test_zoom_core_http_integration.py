"""Real HTTP acceptance for core collect -> existing worker -> private Zoom API.

Only the provider inside zoom-api and owned object storage are fixtures. Three
separate uvicorn TCP servers use the real apps, typed client/content transport,
collector/worker and standalone HTTP normalizer. No cloud, ASR or broker calls.
"""
from contextlib import contextmanager, ExitStack
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import sqlite3
import sys
import threading
import time

import requests
import uvicorn


ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT / "interface-lab/server", ROOT / "services/data-core/src", ROOT / "services/transcription/src"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from core_source_client import ServiceHTTP
from hr_materials_core.app import create_app as core_app
from hr_materials_core.auth import Principal, Tokens
from hr_materials_core.repository import CoreRepository
from hr_materials_core.service import CoreService
from materials_http import DownloadResult
from materials_runtime import MaterialsRuntime
from materials_service import MaterialsService
from materials_storage import StoredObject
from materials_zoom import ZoomCapture
from md_transcription.api import create_app as transcription_app
from md_transcription.settings import Settings as TranscriptionSettings
from md_transcription.worker import Runtime as TranscriptionRuntime
from zoom_api_client import ZoomAPI, ZoomContent


PORTAL_TOKEN = "zoom-core-http-fixture-portal-" + "p" * 32
ZOOM_TOKEN = "zoom-private-http-fixture-" + "z" * 32
TRANSCRIPTION_TOKEN = "zoom-normalizer-http-fixture-" + "n" * 32
PROVIDER_TOKEN = "PROVIDER_SECRET_MUST_STAY_INSIDE_ZOOM_API"
SDK_SECRET = "SDK_SECRET_MUST_STAY_INSIDE_ZOOM_API"
VIDEO = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 20
VTT = b"WEBVTT\n\n00:00:00.500 --> 00:00:02.000\n<v Recruiter>Hello &amp; welcome</v>\n\n00:00:02.500 --> 00:00:03.000\nCandidate answer\n"
ROOM = {"provider": "zoom", "id": "12345678901", "name": "hrv-zoom-interview",
        "meetingNumber": "12345678901", "meetingSessionId": "exact-zoom-session",
        "connectionId": "zoom-account"}


@contextmanager
def real_http_app(app):
    """Own a bound socket so server startup does not race ephemeral-port reuse."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=listener.getsockname()[1],
                                           log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started, "real HTTP app failed to start"
        yield "http://127.0.0.1:" + str(listener.getsockname()[1])
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive(), "HTTP app did not shut down"


class OwnedObjects:
    """Complete owned-storage fixture, including actual raw-byte reads."""
    backend = "s3"
    connection_id = "s3:zoom-http-fixture"

    def __init__(self):
        self.objects, self.metadata = {}, {}

    def key_for_sha256(self, digest):
        return "materials/sha256/" + digest[:2] + "/" + digest

    object_key = key_for_sha256

    def put_file(self, path, *, expected_size, expected_sha256, content_type):
        data = Path(path).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        assert len(data) == expected_size and digest == expected_sha256
        key = self.key_for_sha256(digest)
        assert key not in self.objects or self.objects[key] == data
        self.objects[key] = data
        self.metadata[key] = StoredObject(key, digest, len(data), content_type, self.backend)
        return self.metadata[key]

    def head(self, key):
        return self.metadata[key]

    def open(self, key):
        return io.BytesIO(self.objects[key])

    def read_bytes(self, key, max_bytes):
        assert len(self.objects[key]) <= max_bytes
        return self.objects[key]

    def signed_url(self, key, expires_seconds):
        return "https://owned-fixture.example/" + key + "?expires=" + str(expires_seconds)


class ZoomProvider:
    configured = capture_configured = selection_enabled = True
    connection_id = "zoom-account"
    _hosts, _default_host = {}, None

    def __init__(self):
        self.resolved, self.recording_calls = [], []

    def recordings(self, room):
        assert room == ROOM
        self.recording_calls.append(dict(room))
        return {"id": ROOM["id"], "uuid": ROOM["meetingSessionId"], "recording_count": 2,
                "download_token": PROVIDER_TOKEN, "sdk_secret": SDK_SECRET,
                "recording_files": [
                    {"id": identity, "meeting_id": ROOM["meetingSessionId"], "file_type": kind,
                     "file_extension": suffix, "recording_type": recording_type, "status": "completed",
                     "file_size": len(body), "download_url": "https://fixture.zoom.us/private/" + identity,
                     "play_url": "https://fixture.zoom.us/private/play", "sdk_secret": SDK_SECRET}
                    for identity, kind, suffix, recording_type, body in (
                        ("zoom-video", "MP4", "MP4", "shared_screen_with_speaker_view", VIDEO),
                        ("zoom-transcript", "TRANSCRIPT", "VTT", "audio_transcript", VTT))]}

    def resolve_download(self, row):
        self.resolved.append(row["id"])
        return {"url": row["download_url"], "headers": {"Authorization": "Bearer " + PROVIDER_TOKEN}}

    def credentials(self, *_):
        raise AssertionError("collect must not request Meeting SDK credentials")


def test_zoom_collect_existing_worker_private_content_and_http_normalizer(tmp_path, monkeypatch):
    database = tmp_path / "synthetic-hr.sqlite3"
    connect = lambda: sqlite3.connect(database, timeout=10)
    with connect() as db:
        db.executescript("""
            CREATE TABLE cp_candidates(id TEXT PRIMARY KEY,email TEXT,password_hash TEXT,profile TEXT,created_at TEXT,updated_at TEXT);
            CREATE TABLE cp_interviews(id TEXT PRIMARY KEY,candidate_id TEXT,status TEXT,room TEXT,created_at TEXT,completed_at TEXT);
            CREATE TABLE cp_applications(id TEXT PRIMARY KEY,candidate_id TEXT,status TEXT);
            CREATE TABLE cp_meetings(id TEXT PRIMARY KEY,application_id TEXT,status TEXT,room TEXT,start TEXT,end TEXT,completed_at TEXT);
        """)
        db.execute("INSERT INTO cp_candidates VALUES (?,?,?,?,?,?)", ("zoom-candidate", "fixture@example.org", "private-password",
            json.dumps({"name": "Synthetic candidate"}), "2026-01-01", "2026-01-01"))
        db.execute("INSERT INTO cp_interviews VALUES (?,?,?,?,?,?)", ("zoom-interview", "zoom-candidate", "completed",
            json.dumps(ROOM), "2026-01-01", "2026-01-01"))
    repository = CoreRepository(connect, default_connections={"zoom": "zoom-account"})
    repository.init()
    objects, provider, downloads, normalization_paths = OwnedObjects(), ZoomProvider(), [], []

    def provider_download(source, destination, *, max_bytes, expected_size, timeout_seconds):
        assert source.headers == {"Authorization": "Bearer " + PROVIDER_TOKEN}
        assert source.url.startswith("https://fixture.zoom.us/private/")
        identity = source.url.rsplit("/", 1)[1]
        body = VIDEO if identity == "zoom-video" else VTT
        assert len(body) == expected_size and len(body) <= max_bytes
        Path(destination).write_bytes(body)
        downloads.append(identity)
        return DownloadResult(Path(destination), len(body), hashlib.sha256(body).hexdigest(),
                              "video/mp4" if identity == "zoom-video" else "text/vtt")

    spec = importlib.util.spec_from_file_location("private_zoom_http_acceptance_app", ROOT / "services/zoom-api/app.py")
    zoom_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(zoom_module)
    zoom_app = zoom_module.create_app(provider, token=ZOOM_TOKEN, downloader=provider_download, temp_root=str(tmp_path))

    class ForbiddenASR:
        calls = 0
        def transcribe(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("ASR must not run for provider transcript import")

    backend = ForbiddenASR()
    settings = TranscriptionSettings(data_dir=tmp_path / "normalizer-private", api_keys={"collector": TRANSCRIPTION_TOKEN})
    transcription_runtime = TranscriptionRuntime(settings, backend=backend)
    normalizer_app = transcription_app(settings, transcription_runtime)

    @normalizer_app.middleware("http")
    async def record_normalizer_paths(request, call_next):
        normalization_paths.append(request.url.path)
        return await call_next(request)

    with ExitStack() as stack:
        zoom_url = stack.enter_context(real_http_app(zoom_app))
        normalizer_url = stack.enter_context(real_http_app(normalizer_app))
        monkeypatch.setenv("HR_TRANSCRIPTION_URL", normalizer_url)
        monkeypatch.setenv("HR_TRANSCRIPTION_TOKEN", TRANSCRIPTION_TOKEN)
        # Use the actual typed client and default worker downloader/normalizer.
        # ZoomCapture resolves ZoomContent; the worker must POST to private API.
        zoom_client = ZoomAPI(ServiceHTTP(zoom_url, ZOOM_TOKEN))
        capture = ZoomCapture(zoom_client, "zoom-account")
        runtime = MaterialsRuntime(connect, repository, {("zoom", "zoom-account"): capture}, objects,
            publish=lambda _: None, provider_allowlist=("zoom",), temp_root=str(tmp_path))
        service = CoreService(repository, {("zoom", "zoom-account"): objects},
                              collector=MaterialsService(runtime).register_owner)
        core = core_app(service=service, tokens=Tokens({PORTAL_TOKEN: Principal("portal", "portal")}))
        core_url = stack.enter_context(real_http_app(core))
        session = stack.enter_context(requests.Session())
        session.trust_env = False
        payloads = []

        def request(method, path, **kwargs):
            response = session.request(method, core_url + path, headers={"Authorization": "Bearer " + PORTAL_TOKEN},
                                       timeout=5, **kwargs)
            payloads.append(response.text)
            return response

        endpoint = "/v1/owners/interview/zoom-interview/collect"
        first = request("POST", endpoint)
        replay = request("POST", endpoint, json={})
        assert first.status_code == replay.status_code == 202, (first.text, replay.text)
        assert first.json() == replay.json()
        identity = first.json()["binding_id"]
        assert len(repository.list_sources("interview", "zoom-interview")) == 1
        assert len(repository.list_jobs(identity)) == 1
        assert provider.recording_calls == [] and downloads == [] and normalization_paths == [], "collect must schedule without provider I/O"
        # Prove the adapter/client path selects the real private byte transport.
        discovered = capture.discover({**repository.get_source(identity)["source_ref"],
                                       "provider": "zoom", "connection_id": "zoom-account"})
        assert isinstance(capture.resolve(discovered.artifacts[0]), ZoomContent)

        outcomes = []
        for _ in range(10):
            result = runtime.worker.run_once()
            if not result["claimed"]:
                break
            outcomes.append(result)
        assert len(outcomes) == 3 and all(row["state"] == "completed" for row in outcomes), outcomes
        assert sorted(downloads) == ["zoom-transcript", "zoom-video"]
        assert sorted(provider.resolved) == sorted(downloads)
        response = request("GET", "/v1/owners/interview/zoom-interview/materials")
        assert response.status_code == 200, response.text
        materials = response.json()
        assert len(materials["assets"]) == 2 and len(materials["transcripts"]) == 1
        channel = materials["channels"][0]
        assert channel["video_status"] == channel["transcript_status"] == "ready"
        assert channel["acquisition_complete"] is True and channel["transcription_status"] == "not_requested"
        transcript = materials["transcripts"][0]
        assert objects.read_bytes(transcript["raw_ref"]["object_key"], len(VTT)) == VTT
        normalized = transcript["metadata"]["normalized"]
        assert normalized["segments"] == [
            {"start_ms": 500, "end_ms": 2000, "text": "Hello & welcome", "speaker": "Recruiter"},
            {"start_ms": 2500, "end_ms": 3000, "text": "Candidate answer", "speaker": None}]
        assert normalized["full_text"] == "Hello & welcome\nCandidate answer"
        assert normalized["warnings"] == ["missing_speakers"]
        assert transcript["metadata"]["origin"] == "provider_import" and transcript["metadata"]["source_provider"] == "zoom"
        for asset in materials["assets"]:
            access = request("GET", "/v1/assets/" + asset["id"] + "/access")
            assert access.status_code == 200, access.text
            assert access.json()["storage"] == "owned"
            assert access.json()["url"].startswith("https://owned-fixture.example/materials/sha256/")
            location = next(row for row in asset["locations"] if row["state"] == "verified")
            assert objects.read_bytes(location["object_key"], 10000) == (VIDEO if asset["kind"] == "video" else VTT)

        # Recollect coalesces with the existing durable discover job and cannot
        # duplicate imported material, raw storage or transcript versions.
        assert request("POST", endpoint).json()["binding_id"] == identity
        assert runtime.worker.run_once() == {"claimed": False}
        assert len(repository.list_jobs(identity)) == 3
        assert len(objects.objects) == 2 and len(repository.list_transcripts(identity)) == 1
        assert normalization_paths == ["/v1/normalize"] and backend.calls == 0
        with transcription_runtime.repository.engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT count(*) FROM jobs").scalar_one() == 0
        assert list((settings.data_dir / "inputs").iterdir()) == []
        catalogue = json.dumps(repository.list_sources("interview", "zoom-interview") +
                               repository.list_assets(identity) + repository.list_transcripts(identity))
        for secret in (PROVIDER_TOKEN, SDK_SECRET, ZOOM_TOKEN, TRANSCRIPTION_TOKEN, "https://fixture.zoom.us/", "download_url", "play_url"):
            assert secret not in "\n".join(payloads) and secret not in catalogue
