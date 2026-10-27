"""Process launcher for HTTP acceptance; fixtures replace provider/storage only.

Not a deployable service. Each role imports its real app. The source process has
no HR repository import path and a SQLite audit guard permits only its job DB.
"""
import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import threading
import time


ROOT = Path(__file__).resolve().parents[2]
PORTAL = "portal-http-fixture-" + "p" * 32
SOURCE = "source-core-http-fixture-" + "s" * 32
SOURCE_API = "source-portal-http-fixture-" + "a" * 32
NORMALIZER = "normalizer-http-fixture-" + "n" * 32
VIDEO = b"synthetic-video-fixture-no-real-media"
VTT = b"WEBVTT\n\n00:00:00.500 --> 00:00:02.000\n<v Recruiter>Hello &amp; welcome</v>\n\n00:00:02.500 --> 00:00:03.000\nCandidate answer\n"


@dataclass(frozen=True)
class Object:
    key: str
    sha256: str
    size: int
    content_type: str
    backend: str = "s3"

    def as_dict(self):
        value = asdict(self)
        value["object_key"] = value.pop("key")
        return value


class LocalObjects:
    """Filesystem fake for the configured object-storage boundary, shared by roles."""
    backend = "s3"
    connection_id = "s3:http-acceptance-fixture"

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def object_key(self, digest):
        return "materials/sha256/" + digest[:2] + "/" + digest

    def put_file(self, path, *, expected_sha256, expected_size, content_type):
        data = Path(path).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        assert digest == expected_sha256 and len(data) == expected_size
        obj = Object(self.object_key(digest), digest, len(data), content_type)
        target = self.root / obj.key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.with_suffix(".metadata.json").write_text(json.dumps(asdict(obj)))
        return obj

    def head(self, key):
        return Object(**json.loads((self.root / key).with_suffix(".metadata.json").read_text()))

    def read_bytes(self, key, max_bytes):
        data = (self.root / key).read_bytes()
        assert len(data) <= max_bytes
        return data

    def signed_url(self, key, expires_seconds):
        return "https://object-fixture.example/" + key + "?expires=" + str(expires_seconds)


def source_app(args):
    sys.path.insert(0, str(ROOT / "services/source-daily/src"))
    from source_daily.api import create_app
    from source_daily.clients import CoreClient, TranscriptionClient
    from source_daily.config import Settings
    from source_daily.pipeline import Pipeline
    from source_daily.store import Store
    from source_daily._vendor.materials_capture import Artifact, Discovery, HttpSource, Source
    from source_daily._vendor.materials_http import DownloadResult

    private = Path(args.private).resolve()
    private.mkdir(parents=True, exist_ok=True)
    db_path = private / "source-jobs.sqlite3"
    audit_path = private / "sqlite-audit.jsonl"

    def audit(event, values):
        if event == "sqlite3.connect":
            actual = Path(values[0]).resolve()
            allowed = actual == db_path
            with audit_path.open("a") as stream:
                stream.write(json.dumps({"database": str(actual), "allowed": allowed}) + "\n")
            if not allowed:
                raise PermissionError("source SQLite access outside its private database")
    sys.addaudithook(audit)
    # Prove the guard rejects arbitrary database opens before running real jobs.
    try:
        sqlite3.connect(private / "forbidden-probe.sqlite3")
    except PermissionError:
        pass
    else:
        raise AssertionError("source SQLite guard did not reject the probe")

    class Capture:
        def discover(self, ref):
            assert ref["room_id"] == "room-fixture" and ref["session_id"] == "session-fixture"
            assert ref["capture_instance_id"] == "capture-fixture"
            source = Source.from_ref(ref)
            assets = [Artifact(source, "recording-fixture", "video", "ready", {"duration": 3})]
            if args.transcript != "absent":
                assets.append(Artifact(source, "transcript-fixture", "transcript", "ready"))
            return Discovery(assets, {"video": {"status": "ready", "complete": True},
                "transcript": {"status": "absent" if args.transcript == "absent" else "ready", "complete": True}}, True)

        def resolve(self, artifact):
            assert artifact.source.session_id == "session-fixture"
            return HttpSource("https://provider-fixture.example/" + artifact.provider_asset_id +
                              "?temporary=NEVER-PERSIST", expires_at=4102444800)

    def fetch(source, path, *, max_bytes, timeout_seconds):
        # Local bytes stand in for the provider download/SSRF boundary. Service
        # HTTP protocols, normalizer, canonical receipt and databases stay real.
        transcript = "transcript-fixture" in source.url
        data = (b"WEBVTT\n\nbroken cue" if args.transcript == "malformed" else VTT) if transcript else VIDEO
        assert len(data) <= max_bytes
        Path(path).write_bytes(data)
        with (private / "downloads.jsonl").open("a") as stream:
            stream.write(json.dumps({"kind": "transcript" if transcript else "video", "sha256": hashlib.sha256(data).hexdigest()}) + "\n")
        return DownloadResult(path, len(data), hashlib.sha256(data).hexdigest(), "text/vtt" if transcript else "video/mp4")

    settings = Settings(db_path=str(db_path), temp_root=str(private / "tmp"),
        api_keys={SOURCE_API: "portal"}, daily_key="fixture-provider-key-only",
        core_url=args.core_url, core_token=SOURCE, transcription_url=args.transcription_url,
        transcription_token=NORMALIZER, enable_asr=args.enable_asr,
        settle_attempts=1, max_attempts=10, retry_seconds=1)
    store = Store(settings.db_path)
    pipeline = Pipeline(settings, store, CoreClient(settings.core_url, SOURCE), Capture(),
        TranscriptionClient(settings.transcription_url, NORMALIZER), LocalObjects(args.objects), downloader=fetch)

    class Media:
        configured = True

    assert not any(name == "materials_repository" or name.startswith("hr_materials_core") for name in sys.modules)
    (private / "isolation.json").write_text(json.dumps({"hr_modules_loaded": False, "database": str(db_path)}))

    def worker():
        while True:
            for identity in store.due():
                pipeline.run(identity)
            time.sleep(.03)
    threading.Thread(target=worker, daemon=True).start()
    return create_app(settings, pipeline, Media())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=("core", "source", "transcription"))
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--objects")
    parser.add_argument("--private", required=True)
    parser.add_argument("--database")
    parser.add_argument("--core-url")
    parser.add_argument("--transcription-url")
    parser.add_argument("--transcript", choices=("valid", "absent", "malformed"), default="valid")
    parser.add_argument("--enable-asr", action="store_true")
    args = parser.parse_args()
    if args.role == "core":
        sys.path[:0] = [str(ROOT / "services/data-core/src"), str(ROOT / "interface-lab/server")]
        from hr_materials_core.app import create_app
        from hr_materials_core.auth import Principal, Tokens
        from hr_materials_core.repository import CoreRepository
        from hr_materials_core.service import CoreService
        repository = CoreRepository(lambda: sqlite3.connect(args.database))
        app = create_app(service=CoreService(repository, {("daily", "daily"): LocalObjects(args.objects)}),
            tokens=Tokens({PORTAL: Principal("portal", "portal"), SOURCE: Principal("daily", "source", "daily", "daily")}))
    elif args.role == "transcription":
        sys.path.insert(0, str(ROOT / "services/transcription/src"))
        from md_transcription.api import create_app
        from md_transcription.settings import Settings
        # No inference endpoint/model selected. Normalization remains available.
        app = create_app(Settings(data_dir=Path(args.private), api_keys={"daily": NORMALIZER}))
    else:
        app = source_app(args)
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="error", access_log=False)


if __name__ == "__main__":
    main()
