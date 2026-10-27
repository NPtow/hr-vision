import copy
import concurrent.futures
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading

import pytest
from fastapi.testclient import TestClient
from materials_repository import MaterialsRepository, RepositoryError
from materials_capture import Source, owner_source_ref
from materials_storage import S3Storage, StoredObject, StorageError

from hr_materials_core.app import BodyLimit, create_app
from hr_materials_core.auth import Principal, Tokens
from hr_materials_core.repository import CoreRepository
from hr_materials_core.schemas import Receipt
from hr_materials_core.service import CoreService
from hr_materials_core.settings import Settings


PORTAL = "p" * 40
SCOPED = "o" * 40
SOURCE = "s" * 40
OTHER = "x" * 40


class Store:
    backend = "s3"
    connection_id = "s3:test-store"

    def __init__(self):
        self.objects, self.head_calls, self.on_head, self.on_sign = {}, 0, None, None

    def object_key(self, digest):
        return "materials/sha256/" + digest[:2] + "/" + digest

    def add(self, data=b"recording", content_type="video/mp4"):
        digest = hashlib.sha256(data).hexdigest()
        obj = StoredObject(self.object_key(digest), digest, len(data), content_type, "s3")
        self.objects[obj.key] = obj
        return {"backend": "s3", "object_key": obj.key, "size": obj.size,
                "sha256": obj.sha256, "content_type": content_type}

    def head(self, key):
        self.head_calls += 1
        if self.on_head:
            self.on_head()
        if key not in self.objects:
            raise StorageError("not found")
        return self.objects[key]

    def signed_url(self, key, expires_seconds):
        if self.on_sign:
            self.on_sign()
        return "https://private.example/signed?expires=" + str(expires_seconds)


@pytest.fixture
def env(tmp_path):
    path = tmp_path / "hr.sqlite"
    connect = lambda: sqlite3.connect(path)
    with connect() as db:
        db.executescript("""
          CREATE TABLE em_intake_candidates(id TEXT PRIMARY KEY,workspace_id TEXT,vacancy_id TEXT,body TEXT);
          CREATE TABLE em_vacancies(vacancy_id TEXT,workspace_id TEXT,archived_at TEXT);
          CREATE TABLE cp_candidates(id TEXT PRIMARY KEY,email TEXT,password_hash TEXT,profile TEXT,created_at TEXT,updated_at TEXT);
          CREATE TABLE cp_interviews(id TEXT PRIMARY KEY,candidate_id TEXT,status TEXT,room TEXT,created_at TEXT,completed_at TEXT);
          CREATE TABLE cp_applications(id TEXT PRIMARY KEY,candidate_id TEXT,status TEXT);
          CREATE TABLE cp_meetings(id TEXT PRIMARY KEY,application_id TEXT,status TEXT,room TEXT,start TEXT,end TEXT,completed_at TEXT);
          CREATE TABLE cp_resumes(candidate_id TEXT,filename TEXT,mime TEXT,content BLOB,text_content TEXT,extracted TEXT,uploaded_at TEXT);
          CREATE TABLE cp_consent_log(id TEXT,candidate_id TEXT,created_at TEXT,reuse INT,auto_approve INT,version TEXT);
          CREATE TABLE cp_media_consents(meeting_id TEXT,actor_id TEXT,consented_at TEXT,version TEXT);
          CREATE TABLE cn_recipients(candidate_id TEXT,email TEXT,email_verified INT,telegram_chat_id TEXT,telegram_verified_at INT);
        """)
        for index in (1, 2):
            cid, iid = f"c{index}", f"i{index}"
            db.execute("INSERT INTO cp_candidates VALUES (?,?,?,?,?,?)", (cid, cid + "@test.example", "password-MUST-NOT-LEAK",
                       json.dumps({"name": cid, "consentReuse": True, "password": "profile-secret"}), "2026-01-01", "2026-01-01"))
            room = {"id": "r" + str(index), "name": "hrv-" + iid, "captureInstanceId": "cap" + str(index),
                    "provider": "daily", "connectionId": "daily", "url": "https://provider.example/room"}
            db.execute("INSERT INTO cp_interviews VALUES (?,?,?,?,?,?)", (iid, cid, "completed", json.dumps(room), "2026-01-01", "2026-01-01"))
        db.execute("INSERT INTO cp_applications VALUES ('a1','c1','accepted')")
        meeting_room = json.dumps({"id": "rm1", "name": "hrv-m1", "captureInstanceId": "capm1"})
        db.execute("INSERT INTO cp_meetings VALUES ('m1','a1','completed',?,'start','end','2026-01-01')", (meeting_room,))
        db.execute("INSERT INTO cp_resumes VALUES ('c1','cv.pdf','application/pdf',X'01','private text','{}','2026-01-01')")
        db.execute("INSERT INTO cp_consent_log VALUES ('consent','c1','2026-01-01',1,0,'v1')")
        db.execute("INSERT INTO cp_media_consents VALUES ('i1','c1','2026-01-01','recording-v1')")
        db.execute("INSERT INTO cn_recipients VALUES ('c1','c1@test.example',1,'private-chat-id',123)")
    repo = CoreRepository(connect)
    repo.init()
    store = Store()
    service = CoreService(repo, {("daily", "daily"): store})
    principals = {PORTAL: Principal("portal", "portal"), SCOPED: Principal("candidate-one", "portal", candidate_ids=frozenset({"c1"})),
                  SOURCE: Principal("daily", "source", "daily", "daily"), OTHER: Principal("other", "source", "daily", "other")}
    tokens = Tokens(principals)
    with TestClient(create_app(service=service, tokens=tokens)) as client:
        yield client, repo, store, service, principals, connect


def auth(token=PORTAL):
    return {"Authorization": "Bearer " + token}


def binding(client, *, owner="i1", policy="remote", extra_ref=None, token=PORTAL):
    kind = "meeting" if owner.startswith("m") else "interview"
    source_ref = {"room_id": "rm1" if kind == "meeting" else "r" + owner[1:], "room_name": "hrv-" + owner,
                  "capture_instance_id": "cap" + (owner if kind == "meeting" else owner[1:])}
    source_ref.update(extra_ref or {})
    return client.post("/v1/bindings", headers=auth(token), json={"schema_version": 1, "owner_kind": kind,
                       "owner_id": owner, "provider": "daily", "connection_id": "daily", "source_ref": source_ref,
                       "storage_policy": policy})


def receipt(*assets, **extra):
    return {"schema_version": 1, "generation": 1, "receipt_id": "receipt-one", "assets": list(assets),
            "video_status": "ready", "transcript_status": "not_recorded", "acquisition_complete": True,
            "transcription_status": "awaiting_configuration", **extra}


def video(external_id="video-one", location=None):
    return {"external_id": external_id, "kind": "video", "state": "ready", "metadata": {"duration": 3.0},
            "locations": [location or {"backend": "provider", "object_key": external_id, "state": "available"}]}


def transcript(store, text="Actual speech"):
    raw = store.add(text.encode(), "text/vtt")
    return {"external_id": "transcript-one", "kind": "transcript", "state": "ready", "locations": [],
            "transcript": {"raw_ref": raw, "provenance": {"origin": "daily_provider", "processing_status": "completed",
                "coverage": "not_independently_verified"}, "normalized": {"schema_version": 1, "format": "json",
                "segments": [{"text": text, "start_ms": None, "end_ms": None, "speaker": None}],
                "full_text": text, "text": text, "vtt": "", "warnings": ["missing_timestamps", "missing_speakers"]}}}


def post(client, identity, data, token=SOURCE):
    return client.post(f"/v1/bindings/{identity}/receipts", headers=auth(token), json=data)


def test_auth_scopes_and_owner_trust(env):
    client, repo, *_ = env
    assert client.get("/health/ready").status_code == 200
    assert client.get("/v1/candidates/c1/dossier").status_code == 401
    assert binding(client, token=SOURCE).status_code == 403
    assert binding(client, owner="i2", token=SCOPED).status_code == 403
    assert binding(client, extra_ref={"room_id": "r2"}).status_code == 409
    assert binding(client, extra_ref={"candidate_id": "c2"}).status_code == 409
    assert binding(client, extra_ref={"exclusive_room_owner": {"kind": "interview", "id": "i2"}}).status_code == 409
    assert binding(client, extra_ref={"url": "https://unsafe.example"}).status_code == 400
    response = binding(client)
    assert response.status_code == 200
    one = response.json()
    assert one["source_ref"]["exclusive_room_owner"] == {"kind": "interview", "id": "i1"}
    assert "url" not in one["source_ref"]
    assert binding(client).json()["binding_id"] == one["binding_id"]
    assert client.get("/v1/bindings/" + one["binding_id"], headers=auth(OTHER)).status_code == 403
    assert post(client, one["binding_id"], receipt(video()), OTHER).status_code == 403
    assert post(client, one["binding_id"], receipt(video()), PORTAL).status_code == 403
    assert client.get("/v1/owners/interview/i1/materials", headers=auth(SOURCE)).status_code == 403


def test_owner_must_be_completed_and_room_identity_still_matches(env):
    client, repo, store, service, principals, connect = env
    with connect() as db:
        db.execute("UPDATE cp_interviews SET status='live' WHERE id='i1'")
    assert binding(client).status_code == 409
    with connect() as db:
        db.execute("UPDATE cp_interviews SET status='completed' WHERE id='i1'")
    identity = binding(client).json()["binding_id"]
    with connect() as db:
        db.execute("UPDATE cp_interviews SET room=? WHERE id='i1'", (json.dumps({"id": "different", "name": "hrv-i1"}),))
    assert post(client, identity, receipt(video())).status_code == 409


def test_exact_replay_conflict_and_independent_channels(env):
    client, repo, store, *_ = env
    identity = binding(client).json()["binding_id"]
    data = receipt(video())
    first = post(client, identity, data)
    assert first.status_code == 200, first.text
    assert post(client, identity, data).json() == first.json()
    changed = copy.deepcopy(data)
    changed["transcription_status"] = "failed"
    assert post(client, identity, changed).status_code == 409
    result = client.get("/v1/owners/interview/i1/materials", headers=auth()).json()
    assert len(result["assets"]) == 1
    assert result["channels"][0]["video_status"] == "ready"
    assert result["channels"][0]["transcript_status"] == "not_recorded"
    assert result["channels"][0]["transcription_status"] == "awaiting_configuration"
    access = client.get("/v1/assets/" + first.json()["asset_ids"][0] + "/access", headers=auth()).json()
    assert access["storage"] == "remote" and access["binding_id"] == identity
    assert access["external_id"] == "video-one" and "url" not in access


def test_owned_verification_and_replay_does_not_require_another_head(env):
    client, repo, store, *_ = env
    identity = binding(client, policy="owned").json()["binding_id"]
    assert post(client, identity, receipt(video())).status_code == 409
    location = {**store.add(), "state": "verified"}
    data = receipt(video(location=location))
    first = post(client, identity, data)
    assert first.status_code == 200, first.text
    assert store.head_calls == 1
    assert post(client, identity, data).json() == first.json()
    assert store.head_calls == 1
    aid = first.json()["asset_ids"][0]
    result = client.get(f"/v1/assets/{aid}/access", headers=auth()).json()
    assert result["storage"] == "owned" and "https://private.example" in result["url"]
    metadata = repo.list_locations(aid)[0]["metadata"]
    assert metadata["verification"] == "head_metadata"
    assert metadata["storage_connection_id"] == store.connection_id


@pytest.mark.parametrize("mutation", ["size", "sha", "prefix", "content_type"])
def test_owned_untrusted_or_mismatched_object_rejected(env, mutation):
    client, repo, store, *_ = env
    identity = binding(client, policy="owned").json()["binding_id"]
    location = {**store.add(), "state": "verified"}
    if mutation == "size": location["size"] += 1
    if mutation == "sha": location["sha256"] = "b" * 64
    if mutation == "prefix": location["object_key"] = "elsewhere/" + location["object_key"]
    if mutation == "content_type": location["content_type"] = "text/plain"
    assert post(client, identity, receipt(video(location=location))).status_code in (400, 409)
    assert repo.list_assets(identity) == []


def test_external_asset_collision_rolls_back_entire_receipt(env):
    client, repo, *_ = env
    one = binding(client).json()["binding_id"]
    two = binding(client, owner="i2").json()["binding_id"]
    assert post(client, one, receipt(video("shared"))).status_code == 200
    collision = receipt(video("new"), video("shared"))
    assert post(client, two, collision).status_code == 409
    assert repo.list_assets(two) == []
    with repo._db() as db:
        assert db.execute("SELECT count(*) FROM core_material_receipts WHERE binding_id=?", (two,)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM core_material_channels WHERE binding_id=?", (two,)).fetchone()[0] == 0


def test_receipt_insert_failure_rolls_back_assets_locations_versions_and_channels(env):
    client, repo, store, service, principals, connect = env
    identity = binding(client).json()["binding_id"]
    with connect() as db:
        db.execute("""CREATE TRIGGER force_receipt_failure BEFORE INSERT ON core_material_receipts
                    BEGIN SELECT RAISE(ABORT,'forced receipt failure'); END""")
    data = Receipt.model_validate(receipt(video(), transcript(store), transcript_status="ready", transcription_status="succeeded"))
    with pytest.raises(sqlite3.IntegrityError):
        service.receive(principals[SOURCE], identity, data)
    with connect() as db:
        for table in ("media_assets", "asset_locations", "transcript_versions", "core_material_channels", "core_material_receipts", "core_material_asset_bindings"):
            assert db.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0


def test_generation_policy_upgrade_revocation_and_inflight_head(env):
    client, repo, store, *_ = env
    identity = binding(client).json()["binding_id"]
    assert post(client, identity, receipt(video())).status_code == 200
    upgraded = binding(client, policy="owned").json()
    assert upgraded["generation"] == 2
    assert binding(client).json()["storage_policy"] == "owned"
    assert post(client, identity, receipt(video())).status_code == 409
    assert post(client, identity, receipt(video(), receipt_id="old", generation=1)).status_code == 409
    location = {**store.add(), "state": "verified"}
    store.on_head = lambda: repo.suspend_source(identity)
    response = post(client, identity, receipt(video(location=location), generation=2, receipt_id="raced"))
    assert response.status_code in (409, 410)
    assert len(repo.list_assets(identity)) == 1
    assert repo.list_locations(repo.list_assets(identity)[0]["id"])[0]["backend"] == "provider"
    assert post(client, identity, receipt(video(), generation=3)).status_code == 410
    assert binding(client).status_code == 410


def test_signing_rechecks_revocation_and_cross_owner_access(env):
    client, repo, store, *_ = env
    identity = binding(client, owner="i2", policy="owned").json()["binding_id"]
    location = {**store.add(), "state": "verified"}
    aid = post(client, identity, receipt(video(location=location))).json()["asset_ids"][0]
    assert client.get(f"/v1/assets/{aid}/access", headers=auth(SCOPED)).status_code == 403
    store.on_sign = lambda: repo.suspend_source(identity)
    assert client.get(f"/v1/assets/{aid}/access", headers=auth()).status_code == 410


def test_historical_ids_and_transcript_versions_retained_additively(env):
    client, repo, store, service, principals, connect = env
    legacy = MaterialsRepository(connect)
    source = legacy.ensure_source("interview", "i1", "daily", "daily", {
        "room_id": "r1", "room_name": "hrv-i1", "capture_instance_id": "cap1"})
    asset = legacy.upsert_asset(source["id"], "transcript", "transcript-one", "ready")
    old = legacy.save_transcript(asset["id"], {"backend": "s3", "object_key": "old/private.vtt"}, [{"text": "Historical"}])
    repo.init()
    identity = binding(client).json()["binding_id"]
    assert identity == source["id"]
    result = post(client, identity, receipt(transcript(store), video_status="not_recorded", transcript_status="ready"))
    assert result.status_code == 200, result.text
    assert result.json()["asset_ids"] == [asset["id"]]
    materials = client.get("/v1/owners/interview/i1/materials", headers=auth()).json()
    assert [x["version"] for x in materials["transcripts"]] == [1, 2]
    assert materials["transcripts"][0]["id"] == old["id"]
    assert repo.get_transcript(old["id"])["segments"][0]["text"] == "Historical"
    with connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE transcript_versions SET segments='[]' WHERE id=?", (old["id"],))


def test_historical_session_child_can_publish_through_proven_binding_family(env):
    client, repo, store, service, principals, connect = env
    legacy = MaterialsRepository(connect)
    root = legacy.ensure_source("interview", "i1", "daily", "daily", {
        "room_id": "r1", "room_name": "hrv-i1", "capture_instance_id": "cap1"})
    child = legacy.ensure_source("interview", "i1", "daily", "daily",
        asdict(Source("daily", "daily", "r1", "hrv-i1", "session-historical", "cap1")))
    with connect() as db:
        db.execute("UPDATE media_sources SET parent_source_id=? WHERE id=?", (root["id"], child["id"]))
    old = legacy.upsert_asset(child["id"], "video", "old-recording", "ready")
    old_text = legacy.upsert_asset(child["id"], "transcript", "transcript-one", "ready")
    identity = binding(client).json()["binding_id"]
    assert identity == root["id"]
    response = post(client, identity, receipt(video("old-recording"), transcript(store), transcript_status="ready"))
    assert response.status_code == 200, response.text
    assert response.json()["asset_ids"] == [old["id"], old_text["id"]]
    assert repo.get_asset(old["id"])["source_id"] == child["id"]
    access = client.get("/v1/assets/" + old["id"] + "/access", headers=auth()).json()
    assert access["binding_id"] == root["id"]
    materials = client.get("/v1/owners/interview/i1/materials", headers=auth()).json()
    assert len(materials["sources"]) == 2 and {a["id"] for a in materials["assets"]} == {old["id"], old_text["id"]}
    child_binding = client.get("/v1/bindings/" + child["id"], headers=auth(SOURCE)).json()
    assert child_binding["source_ref"]["capture_instance_id"] == "cap1"
    assert "provider" not in child_binding["source_ref"] and "capture_id" not in child_binding["source_ref"]
    # A same-owner but unrelated session is not proof of a binding family.
    with connect() as db:
        db.execute("UPDATE media_sources SET parent_source_id=NULL WHERE id=?", (child["id"],))
    assert post(client, identity, receipt(video("old-recording"), receipt_id="unrelated")).status_code == 409
    with connect() as db:
        db.execute("UPDATE media_sources SET parent_source_id=? WHERE id=?", (root["id"], child["id"]))
    repo.suspend_source(identity)
    revoked = client.get("/v1/owners/interview/i1/materials", headers=auth()).json()
    assert revoked["assets"] == [] and revoked["transcripts"] == []
    assert all(source["revoked"] for source in revoked["sources"])
    dossier = client.get("/v1/candidates/c1/dossier", headers=auth())
    assert "Actual speech" not in dossier.text
    assert client.get("/v1/assets/" + old["id"] + "/access", headers=auth()).status_code == 410
    assert client.get("/v1/bindings/" + child["id"], headers=auth(SOURCE)).status_code == 410


def test_concurrent_receipt_retry_returns_one_atomic_result(env):
    client, repo, store, service, principals, connect = env
    identity = binding(client).json()["binding_id"]
    barrier = threading.Barrier(2)
    body = Receipt.model_validate(receipt(video()))
    def publish(_):
        barrier.wait()
        return service.receive(principals[SOURCE], identity, body)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, range(2)))
    assert results[0] == results[1]
    with connect() as db:
        assert db.execute("SELECT count(*) FROM core_material_receipts").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM media_assets").fetchone()[0] == 1


def attach_collector(env):
    from materials_runtime import MaterialsRuntime
    from materials_service import MaterialsService
    _, repo, store, service, _, connect = env
    runtime = MaterialsRuntime(connect, repo, {}, store, publish=lambda _: None)
    service.collector = MaterialsService(runtime).register_owner
    return runtime


def test_collect_uses_existing_durable_collector_scopes_and_idempotency(env):
    client, repo, store, service, principals, connect = env
    runtime = attach_collector(env)
    collect = service.collector
    calls = []
    def unlocked(kind, meeting, policy):
        # Proves there is no held reader/writer transaction around collector
        # composition, which performs its own existing SQLite writes.
        with repo._db(write=True):
            pass
        calls.append((kind, copy.deepcopy(meeting), policy))
        return collect(kind, meeting, policy)
    service.collector = unlocked
    endpoint = "/v1/owners/interview/i1/collect"
    assert client.post(endpoint).status_code == 401
    assert client.post(endpoint, headers=auth(SOURCE)).status_code == 403
    assert client.post("/v1/owners/interview/i2/collect", headers=auth(SCOPED)).status_code == 403
    for payload in ({"room": {}}, {"candidate_id": "c2"}, {"source_ref": {}}, {"storage_policy": "anything"}):
        assert client.post(endpoint, headers=auth(), json=payload).status_code == 422
    assert calls == []
    first = client.post(endpoint, headers=auth(SCOPED))
    again = client.post(endpoint, headers=auth(SCOPED), json={})
    assert first.status_code == again.status_code == 202
    assert first.json() == again.json()
    identity = first.json()["binding_id"]
    assert len(repo.list_jobs(identity)) == 1
    assert len(repo.list_sources("interview", "i1")) == 1
    assert calls[0][1]["room"]["id"] == "r1" and calls[0][1]["id"] == "i1"
    assert calls[0][2] is None
    with connect() as db:
        db.execute("UPDATE cp_interviews SET status='live' WHERE id='i1'")
    assert client.post(endpoint, headers=auth()).status_code == 409
    assert len(calls) == 2


def test_collect_rechecks_owner_after_scheduling_and_failure_does_not_break_reads(env):
    client, repo, store, service, principals, connect = env
    endpoint = "/v1/owners/interview/i1/collect"
    assert client.post(endpoint, headers=auth()).status_code == 503
    attach_collector(env)
    collect = service.collector
    def changed_owner(kind, meeting, policy):
        source = collect(kind, meeting, policy)
        with connect() as db:
            db.execute("UPDATE cp_interviews SET status='live' WHERE id='i1'")
        return source
    service.collector = changed_owner
    assert client.post(endpoint, headers=auth()).status_code == 409
    assert client.get("/v1/owners/interview/i1/materials", headers=auth()).status_code == 200
    assert client.get("/v1/candidates/c1/dossier", headers=auth()).status_code == 200
    assert client.get("/health/live").status_code == 200


def zoom_owner(env):
    client, repo, store, service, principals, connect = env
    room = {"provider": "zoom", "id": "12345678901", "name": "hrv-i1", "meetingNumber": "12345678901",
            "meetingSessionId": "exact-session-uuid", "connectionId": "zoom-account"}
    with connect() as db:
        db.execute("UPDATE cp_interviews SET room=? WHERE id='i1'", (json.dumps(room),))
    service.storage_registry[("zoom", "zoom-account")] = store
    return room


def test_zoom_collect_preserves_existing_asset_access_transcript_and_worker_channels(env):
    client, repo, store, service, principals, connect = env
    room = zoom_owner(env)
    attach_collector(env)
    source = repo.ensure_source("interview", "i1", "zoom", "zoom-account",
        owner_source_ref("interview", "i1", room, "zoom", "zoom-account"), storage_policy="owned")
    owned = store.add()
    video_asset = repo.upsert_asset(source["id"], "video", "zoom-file", "ready")
    repo.add_location(video_asset["id"], "s3", owned["object_key"], "verified", owned["size"], owned["sha256"],
                      {"storage_connection_id": store.connection_id})
    text_asset = repo.upsert_asset(source["id"], "transcript", "zoom-vtt", "ready")
    normalized = transcript(store)["transcript"]["normalized"]
    revision = repo.save_transcript(text_asset["id"], {"backend": "s3", "object_key": "private-legacy-vtt"},
        normalized["segments"], {"origin": "provider_import", "normalized": normalized})
    endpoint = "/v1/owners/interview/i1/collect"
    assert client.post(endpoint, headers=auth(), json={"storage_policy": "remote"}).status_code == 400
    response = client.post(endpoint, headers=auth(), json={"storage_policy": "owned"})
    assert response.status_code == 202, response.text
    assert response.json()["binding_id"] == source["id"]
    assert client.post(endpoint, headers=auth()).json()["binding_id"] == source["id"]
    with connect() as db:
        db.execute("UPDATE media_jobs SET status='succeeded' WHERE source_id=?", (source["id"],))
    repo.set_source_state(source["id"], "ready", {"discovery_complete": True, "discovery": {
        "video": {"status": "ready", "complete": True}, "transcript": {"status": "ready", "complete": True}},
        "asr": {"state": "not_requested", "reason": "provider_transcript_import"}})
    data = client.get("/v1/owners/interview/i1/materials", headers=auth()).json()
    channel = data["channels"][0]
    assert channel["video_status"] == channel["transcript_status"] == "ready"
    assert channel["acquisition_complete"] is True and channel["transcription_status"] == "not_requested"
    assert data["transcripts"][0]["id"] == revision["id"]
    assert data["transcripts"][0]["metadata"]["normalized"]["full_text"] == "Actual speech"
    access = client.get("/v1/assets/" + video_asset["id"] + "/access", headers=auth())
    assert access.status_code == 200 and access.json()["storage"] == "owned"
    # The optional meeting_number projection is derived from the saved number
    # for real legacy Source dataclass references, without replacing source IDs.
    legacy = asdict(Source("zoom", "zoom-account", "12345678901", "hrv-i1", "exact-session-uuid",
        owner_source_ref("interview", "i1", room, "zoom", "zoom-account")["capture_instance_id"]))
    with connect() as db:
        db.execute("UPDATE media_sources SET source_ref=? WHERE id=?", (json.dumps(legacy), source["id"]))
    assert client.get("/v1/assets/" + video_asset["id"] + "/access", headers=auth()).status_code == 200


def test_existing_worker_channel_facts_are_independent_and_do_not_infer_asr(env):
    client, repo, store, service, principals, connect = env
    source = binding(client).json()["binding_id"]
    asset = repo.upsert_asset(source, "video", "ready-video", "ready")
    repo.add_location(asset["id"], "provider", "ready-video", "available")
    repo.set_source_state(source, "awaiting_provider", {"discovery_complete": True, "discovery": {
        "video": {"status": "ready", "complete": True}, "transcript": {"status": "absent", "complete": True}},
        "asr": {"state": "awaiting_provider", "reason": "no_asr_provider_selected"}})
    def channel():
        return client.get("/v1/owners/interview/i1/materials", headers=auth()).json()["channels"][0]
    result = channel()
    assert result["video_status"] == "ready" and result["transcript_status"] == "not_recorded"
    assert result["acquisition_complete"] is True and result["transcription_status"] == "awaiting_configuration"
    job = repo.enqueue_job(source, "discover")
    assert channel()["transcript_status"] == "processing" and channel()["acquisition_complete"] is False
    with connect() as db:
        db.execute("UPDATE media_jobs SET status='failed' WHERE id=?", (job["id"],))
    assert channel()["transcript_status"] == "error" and channel()["video_status"] == "ready"
    repo.update_source_policy(source, "owned")
    assert channel()["video_status"] != "ready"  # Remote reference cannot satisfy owned retention.


def test_collect_rejects_zoom_without_exact_saved_session_and_revoked_capture(env):
    client, repo, store, service, principals, connect = env
    room = zoom_owner(env)
    attach_collector(env)
    with connect() as db:
        db.execute("UPDATE cp_interviews SET room=? WHERE id='i1'", (json.dumps({**room, "meetingSessionId": ""}),))
    endpoint = "/v1/owners/interview/i1/collect"
    assert client.post(endpoint, headers=auth()).status_code == 409
    assert repo.list_sources("interview", "i1") == []
    with connect() as db:
        db.execute("UPDATE cp_interviews SET room=? WHERE id='i1'", (json.dumps(room),))
    accepted = client.post(endpoint, headers=auth())
    assert accepted.status_code == 202, accepted.text
    repo.suspend_source(accepted.json()["binding_id"])
    assert client.post(endpoint, headers=auth()).status_code == 410


def test_collector_composition_uses_existing_runtime_without_source_principals(env, monkeypatch):
    client, repo, store, service, principals, connect = env
    from materials_runtime import MaterialsRuntime
    import materials_runtime
    room = zoom_owner(env)
    runtime = MaterialsRuntime(connect, repo, {}, store, publish=lambda _: None)
    arguments = {}
    def build(**kwargs):
        arguments.update(kwargs)
        return runtime
    monkeypatch.setattr(materials_runtime, "build_runtime", build)
    with repo._db() as db:
        database_path = db.execute("PRAGMA database_list").fetchone()[2]
    settings = Settings(database_path=database_path, principals={PORTAL: principals[PORTAL]},
                        default_connections={"daily": "daily", "zoom": "zoom-account"}, collector_enabled=True)
    with TestClient(create_app(settings)) as composed:
        assert composed.app.state.service.storage_registry[("zoom", "zoom-account")] is store
        response = composed.post("/v1/owners/interview/i1/collect", headers=auth(), json={"storage_policy": "owned"})
        assert response.status_code == 202, response.text
    assert arguments["connect"].__self__ is settings
    assert arguments["environ"]["HR_MATERIALS_ENABLED"] == "1"
    assert arguments["publish"]("durable-job") is None


def test_terminal_worker_state_without_usable_material_is_not_forever_processing(env):
    client, repo, *_ = env
    identity = binding(client).json()["binding_id"]
    repo.set_source_state(identity, "ready", {"discovery_complete": True, "discovery": {
        "video": {"status": "ready", "complete": True}, "transcript": {"status": "ready", "complete": True}}})
    channel = client.get("/v1/owners/interview/i1/materials", headers=auth()).json()["channels"][0]
    assert channel["video_status"] == channel["transcript_status"] == "error"
    assert channel["transcription_status"] == "not_requested"


def test_owned_upgrade_atomically_fences_legacy_child_remote_generation(env):
    client, repo, store, service, principals, connect = env
    root = binding(client).json()["binding_id"]
    child = repo.ensure_source("interview", "i1", "daily", "daily",
        asdict(Source("daily", "daily", "r1", "hrv-i1", "session-historical", "cap1")))
    with connect() as db:
        db.execute("UPDATE media_sources SET parent_source_id=? WHERE id=?", (root, child["id"]))
    upgrade = binding(client, policy="owned").json()
    assert upgrade["generation"] == 2
    child_binding = client.get("/v1/bindings/" + child["id"], headers=auth(SOURCE)).json()
    assert child_binding["storage_policy"] == "owned" and child_binding["generation"] == 2
    assert post(client, child["id"], receipt(video())).status_code == 409
    assert post(client, child["id"], receipt(video(), generation=2)).status_code == 409
    location = {**store.add(), "state": "verified"}
    published = post(client, child["id"], receipt(video(location=location), generation=2))
    assert published.status_code == 200, published.text
    aid = published.json()["asset_ids"][0]
    assert client.get(f"/v1/assets/{aid}/access", headers=auth()).json()["storage"] == "owned"
    assert binding(client).json()["generation"] == 2


def test_dossier_allowlisted_profile_resume_consents_and_channel_summary(env):
    client, *_ = env
    assert binding(client, owner="m1").status_code == 200
    response = client.get("/v1/candidates/c1/dossier", headers=auth(SCOPED))
    assert response.status_code == 200
    result = response.json()
    assert result["profile"]["name"] == "c1" and result["resume"]["filename"] == "cv.pdf"
    assert result["interviews"][0]["id"] == "i1" and result["meetings"][0]["id"] == "m1"
    assert len(result["consents"]) == 2
    assert result["channel_linkage"] == {"email": {"linked": True, "verified": True}, "telegram": {"linked": True, "verified": True}}
    for secret in ("password", "profile-secret", "private-chat-id", "private text", "provider.example", "token_hash"):
        assert secret not in response.text
    assert client.get("/v1/candidates/c2/dossier", headers=auth(SCOPED)).status_code == 403
    assert client.get("/v1/candidates/c1/dossier", headers=auth(SOURCE)).status_code == 403


def test_schema_rejects_credentials_extra_fields_invalid_versions_and_time(env):
    client, repo, store, *_ = env
    identity = binding(client).json()["binding_id"]
    for patch in ({"schema_version": True}, {"generation": True}, {"generation": "1"}, {"password": "hidden-credential"}):
        response = post(client, identity, receipt(video(), **patch))
        assert response.status_code == 422
        assert "hidden-credential" not in response.text
    item = transcript(store)
    item["transcript"]["normalized"]["segments"][0].update(start_ms=100, end_ms=50)
    assert post(client, identity, receipt(item)).status_code == 422
    item["transcript"]["normalized"]["segments"][0].update(start_ms=None, end_ms=50)
    assert post(client, identity, receipt(item)).status_code == 422
    item = transcript(store, " ")
    assert post(client, identity, receipt(item)).status_code == 422
    unsafe = video(location={"backend": "provider", "object_key": "https://secret.example/?token=hidden", "state": "available"})
    result = post(client, identity, receipt(unsafe))
    assert result.status_code == 422 and "hidden" not in result.text
    mismatch = video(location={"backend": "provider", "object_key": "other-artifact", "state": "available"})
    assert post(client, identity, receipt(mismatch)).status_code == 409
    assert repo.list_assets(identity) == []


def test_transcript_greater_than_legacy_2mib_limit_is_accepted(env):
    client, repo, store, *_ = env
    identity = binding(client).json()["binding_id"]
    item = transcript(store)
    text = "speech " * 14000  # Each segment stays below the per-segment bound.
    segments = [{"text": text, "start_ms": None, "end_ms": None, "speaker": None} for _ in range(10)]
    full_text = "\n".join(s["text"] for s in segments)
    normalized = item["transcript"]["normalized"]
    normalized.update(segments=segments, text=full_text, full_text=full_text)
    data = receipt(item, video_status="not_recorded", transcript_status="ready")
    assert len(json.dumps(data).encode()) > 2 * 1024 * 1024
    response = post(client, identity, data)
    assert response.status_code == 200, response.text
    assert repo.get_transcript(response.json()["transcript_ids"][0])["metadata"]["normalized"]["full_text"] == full_text


def test_twenty_mib_normalizer_result_fits_receipt_and_internal_fingerprint(env):
    client, repo, store, *_ = env
    identity = binding(client).json()["binding_id"]
    item = transcript(store)
    text = "x" * 100000
    rows = [{"text": text, "start_ms": None, "end_ms": None, "speaker": None} for _ in range(65)]
    full = "\n".join(row["text"] for row in rows)
    item["transcript"]["normalized"].update(segments=rows, full_text=full, text=full)
    data = receipt(item, transcript_status="ready")
    assert len(json.dumps(item["transcript"]["normalized"]).encode()) < 20 * 1024 * 1024
    assert len(json.dumps(data).encode()) < 24 * 1024 * 1024
    response = post(client, identity, data)
    assert response.status_code == 200, response.text
    assert repo.get_transcript(response.json()["transcript_ids"][0])["metadata"]["normalized"]["full_text"] == full


def test_actual_s3_head_validates_metadata_key_size_and_checksum():
    digest = hashlib.sha256(b"ok").hexdigest()
    class Client:
        response = {"ContentLength": 2, "Metadata": {"Sha256": digest}, "ContentType": "text/plain"}
        def head_object(self, **kwargs): return self.response
    client = Client()
    storage = S3Storage("private-bucket", client=client)
    assert storage.head(storage.object_key(digest)).sha256 == digest
    client.response = {**client.response, "Metadata": {"Sha256": "b" * 64}}
    with pytest.raises(StorageError): storage.head(storage.object_key(digest))
    with pytest.raises(StorageError): storage.head("other-prefix/" + storage.object_key(digest))


def test_settings_fail_closed_and_do_not_create_empty_hr_database(tmp_path):
    unknown = tmp_path / "unknown.sqlite"
    configured = json.dumps([{"token": PORTAL, "name": "portal", "role": "portal", "candidate_ids": ["c1"]}])
    with pytest.raises(ValueError):
        Settings.from_env({"CORE_HR_DATABASE": str(unknown), "CORE_PRINCIPALS_JSON": configured})
    assert not unknown.exists()
    unknown.touch()
    settings = Settings.from_env({"CORE_HR_DATABASE": str(unknown), "CORE_PRINCIPALS_JSON": configured})
    assert settings.principals[PORTAL].candidate_ids == frozenset({"c1"})
    with pytest.raises(RepositoryError) as failure:
        CoreRepository(settings.connect).init()
    assert failure.value.code == "hr_owner_schema_required"
    with settings.connect() as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
    with pytest.raises(ValueError):
        Settings.from_env({"CORE_HR_DATABASE": str(unknown), "CORE_PRINCIPALS_JSON": json.dumps([
            {"token": "weak", "name": "portal", "role": "portal"}])})


def test_imports_work_with_only_explicit_pure_donors(tmp_path):
    root = Path(__file__).resolve().parents[3]
    vendor = tmp_path / "selected-pure-modules"
    vendor.mkdir()
    for name in ("materials_repository.py", "materials_storage.py", "domain.py", "feedback.py"):
        shutil.copyfile(root / "services" / "recruiting-api" / name, vendor / name)
    code = """
import sys
from hr_materials_core.app import create_app
from hr_materials_core.settings import Settings
for forbidden in ('candidate_portal','daily_media','materials_capture','materials_runtime','materials_worker','celery'):
    assert forbidden not in sys.modules, forbidden
"""
    process = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env={**os.environ,
        "PYTHONPATH": os.pathsep.join((str(root / "services" / "data-core" / "src"), str(vendor)))},
        capture_output=True, text=True, timeout=20)
    assert process.returncode == 0, process.stderr


@pytest.mark.anyio
async def test_body_limit_before_decoding_chunked_json():
    called, sent = [], []
    async def downstream(scope, receive, send): called.append(True)
    middleware = BodyLimit(downstream, limit=5)
    messages = iter([{"type": "http.request", "body": b"1234", "more_body": True},
                     {"type": "http.request", "body": b"56", "more_body": False}])
    async def receive(): return next(messages)
    async def send(message): sent.append(message)
    await middleware({"type": "http", "method": "POST"}, receive, send)
    assert not called and sent[0]["status"] == 413
