"""Additive catalogue adapter with transaction-scoped receipt publication.

Legacy catalogue IDs and immutable transcript versions remain authoritative.
The bundled legacy repository supplies schema and connection primitives only;
no provider adapter, worker, queue, or portal runtime is imported here.
"""
import base64
import math
import hashlib
import json
import secrets

from materials_repository import MaterialsRepository, RepositoryError, _json, _reference, _require


PROFILE_FIELDS = frozenset((
    "name", "city", "desiredRole", "about", "telegram", "geographies", "availability",
    "consentReuse", "autoApprove", "desiredSalary", "workMode", "salaryMin", "salaryMax",
    "latestStartDate", "residence", "employmentType", "resumeReviewed", "timezone",
    "availabilitySchedule",
))


def encode(value):
    result = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    _require(len(result.encode()) <= 24 * 1024 * 1024, "payload_too_large", 413)
    return result


def fingerprint(value):
    # Internal transcript fingerprints contain both the revision segments and
    # normalized projections. That structure can exceed the wire limit without
    # any individual request/column exceeding it. Hash incrementally instead of
    # applying an unrelated aggregate body bound to this internal structure.
    digest = hashlib.sha256()
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    for chunk in encoder.iterencode(value):
        digest.update(chunk.encode())
    return digest.hexdigest()


def portal(principal, candidate_id):
    _require(principal.role == "portal", "portal_required", 403)
    _require(principal.candidate_ids is None or candidate_id in principal.candidate_ids, "owner_forbidden", 403)


def authorize_owner(principal, owner):
    if owner.get("owner_kind") == "intake":
        _require(principal.role == "portal", "portal_required", 403)
        _require(principal.candidate_ids is None, "owner_forbidden", 403)
        _require(principal.workspace_ids is None or owner["workspace_id"] in principal.workspace_ids,
                 "owner_forbidden", 403)
    else:
        _require(principal.workspace_ids is None, "owner_forbidden", 403)
        portal(principal, owner["candidate_id"])


class OwnerBridge:
    """Reads trusted HR owner/room rows; performs no provider calls."""
    def __init__(self, default_connections):
        self.default_connections = default_connections.copy()

    def owner(self, db, kind, identity, *, completed=False):
        _require(kind in ("interview", "meeting", "intake"), "invalid_owner", 400)
        if kind == "intake":
            _require(any(r[2] == "em_intake_candidates" and r[3] == "intake_id"
                         for r in db.execute("PRAGMA foreign_key_list(media_sources)")),
                     "intake_schema_migration_required", 503)
            row = db.execute("""SELECT i.*,v.archived_at FROM em_intake_candidates i
                JOIN em_vacancies v ON v.vacancy_id=i.vacancy_id AND v.workspace_id=i.workspace_id
                WHERE i.id=?""", (identity,)).fetchone()
            _require(row is not None, "owner_not_found", 404)
            owner = dict(row)
            _require(not json.loads(owner["body"]).get("archived") and not owner["archived_at"], "owner_archived", 410)
            return {**owner, "owner_kind": "intake", "status": "completed"}
        if kind == "interview":
            row = db.execute("SELECT * FROM cp_interviews WHERE id=?", (identity,)).fetchone()
        else:
            row = db.execute("""SELECT m.*,a.candidate_id FROM cp_meetings m
                JOIN cp_applications a ON a.id=m.application_id WHERE m.id=?""", (identity,)).fetchone()
        _require(row is not None, "owner_not_found", 404)
        owner = dict(row)
        _require(db.execute("SELECT id FROM cp_candidates WHERE id=?", (owner["candidate_id"],)).fetchone()
                 is not None, "candidate_not_found", 404)
        if completed:
            _require(owner["status"] == "completed", "owner_not_completed")
        return owner

    def trusted_ref(self, owner, kind, identity, provider, connection_id, *, db=None, source_ref=None):
        if provider == "upload" and source_ref and str(source_ref.get("capture_instance_id", "")).startswith("product-upload:"):
            _require(db is not None, "upload_registration_required", 403)
            capture = source_ref["capture_instance_id"]
            row = db.execute("SELECT * FROM hr_recording_uploads WHERE capture_identity=?", (capture,)).fetchone()
            _require(row is not None and not row["revoked_at"] and
                     (row["owner_kind"], row["owner_id"], row["connection_id"]) == (kind, identity, connection_id),
                     "upload_registration_required", 403)
            return {"room_id": row["id"], "room_name": row["id"], "capture_instance_id": capture}
        _require(kind != "intake", "upload_registration_required", 403)
        try:
            room = json.loads(owner["room"]) if isinstance(owner["room"], str) else owner["room"]
        except (ValueError, TypeError):
            raise RepositoryError("owner_room_unavailable") from None
        _require(isinstance(room, dict) and room.get("id") and room.get("name"), "owner_room_unavailable")
        _require(not room.get("captureNeverStarted"), "capture_not_started")
        stored_provider = room.get("provider", "daily")
        stored_connection = room.get("connectionId", self.default_connections.get(stored_provider))
        _require((provider, connection_id) == (stored_provider, stored_connection), "source_identity_conflict")
        if provider == "zoom":
            from materials_capture import owner_source_ref
            try:
                return owner_source_ref(kind, identity, room, provider, connection_id)
            except ValueError:
                raise RepositoryError("owner_room_unavailable") from None
        capture = room.get("captureInstanceId") or hashlib.sha256(json.dumps(
            [kind, identity, provider, connection_id, room["id"]], separators=(",", ":")).encode()).hexdigest()
        result = {"room_id": room["id"], "room_name": room["name"], "capture_instance_id": capture}
        session = room.get("meetingSessionId") or room.get("session_id") or room.get("sessionId") or room.get("mtgSessionId")
        if session:
            result["session_id"] = session
        if provider == "daily" and room["name"] == "hrv-" + identity:
            result["exclusive_room_owner"] = {"kind": kind, "id": identity}
        _reference(result)
        return result


class CoreRepository(MaterialsRepository):
    def __init__(self, connect, *, default_connections=None, clock=None):
        super().__init__(connect, clock=clock)
        self.bridge = OwnerBridge(default_connections or {"daily": "daily"})

    def init(self, db=None):
        if db is None:
            with self._db(write=True) as connection:
                self.init(connection)
            return
        # Refuse accidentally pointing core at a fresh empty database. Portal owns
        # business schema and migrations; core only adds catalogue tables.
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        _require({"cp_candidates", "cp_interviews", "cp_meetings", "cp_applications"} <= tables,
                 "hr_owner_schema_required", 503)
        from .schema import ensure_sources_v2
        ensure_sources_v2(db)
        super().init(db)
        db.execute("""CREATE TABLE IF NOT EXISTS core_material_receipts (
            binding_id TEXT NOT NULL REFERENCES media_sources(id), generation INTEGER NOT NULL,
            receipt_id TEXT NOT NULL, body_digest TEXT NOT NULL, response TEXT NOT NULL,
            created_at REAL NOT NULL, PRIMARY KEY(binding_id,generation,receipt_id))""")
        db.execute("""CREATE TABLE IF NOT EXISTS core_material_channels (
            binding_id TEXT PRIMARY KEY REFERENCES media_sources(id), generation INTEGER NOT NULL,
            video_status TEXT NOT NULL, transcript_status TEXT NOT NULL,
            acquisition_complete INTEGER NOT NULL, transcription_status TEXT NOT NULL,
            updated_at REAL NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS core_material_asset_bindings (
            asset_id TEXT PRIMARY KEY REFERENCES media_assets(id),
            publishing_binding_id TEXT NOT NULL REFERENCES media_sources(id))""")

        db.execute("""CREATE TABLE IF NOT EXISTS core_transcript_selections (
            binding_id TEXT NOT NULL REFERENCES media_sources(id), generation INTEGER NOT NULL,
            recording_id TEXT NOT NULL REFERENCES media_assets(id), request_id TEXT NOT NULL,
            expected_revision INTEGER NOT NULL, revision INTEGER NOT NULL,
            status TEXT NOT NULL, transcript_id TEXT REFERENCES transcript_versions(id),
            error_code TEXT, updated_at REAL NOT NULL,
            PRIMARY KEY(binding_id,generation,recording_id))""")

        db.execute("""CREATE TABLE IF NOT EXISTS core_transcript_selection_requests (
            binding_id TEXT NOT NULL REFERENCES media_sources(id), generation INTEGER NOT NULL,
            recording_id TEXT NOT NULL REFERENCES media_assets(id), request_id TEXT NOT NULL,
            expected_revision INTEGER NOT NULL, revision INTEGER NOT NULL,
            PRIMARY KEY(binding_id,generation,recording_id,request_id))""")

    @staticmethod
    def canonical_ref(source):
        ref = dict(source["source_ref"])
        if "capture_id" in ref:
            capture = ref.pop("capture_id")
            _require("capture_instance_id" not in ref or ref["capture_instance_id"] == capture,
                     "source_identity_conflict")
            ref["capture_instance_id"] = capture
        for key in ("provider", "connection_id"):
            if key in ref:
                _require(ref.pop(key) == source[key], "source_identity_conflict")
        if source["provider"] == "zoom" and "meeting_number" not in ref:
            ref["meeting_number"] = ref.get("room_id")
        return ref

    @classmethod
    def binding(cls, source):
        return {"binding_id": source["id"], **{key: source[key] for key in
                ("id", "generation", "owner_kind", "owner_id", "provider", "connection_id", "storage_policy", "state")},
                "source_ref": cls.canonical_ref(source),
                "revoked": bool(source["metadata"].get("capture_disabled"))}

    def _family_active(self, db, source):
        current, seen = source, set()
        while True:
            if current["id"] in seen or current["metadata"].get("capture_disabled"):
                return False
            seen.add(current["id"])
            if not current["parent_source_id"]:
                return True
            parent = self._one(db, "media_sources", current["parent_source_id"])
            if (parent["owner_kind"], parent["owner_id"], parent["provider"], parent["connection_id"]) != (
                    source["owner_kind"], source["owner_id"], source["provider"], source["connection_id"]):
                return False
            current = parent

    def _authorize(self, db, source, principal, *, active=False):
        owner = self.bridge.owner(db, source["owner_kind"], source["owner_id"], completed=active)
        if principal.role == "source":
            _require((source["provider"], source["connection_id"]) ==
                     (principal.provider, principal.connection_id), "source_forbidden", 403)
        else:
            authorize_owner(principal, owner)
        if active:
            _require(self._family_active(db, source), "binding_revoked", 410)
            trusted = self.bridge.trusted_ref(owner, source["owner_kind"], source["owner_id"],
                                               source["provider"], source["connection_id"], db=db, source_ref=self.canonical_ref(source))
            # exclusive_room_owner is a derived server attestation, not a
            # persisted identity requirement in pre-split session children.
            # Every room/capture/session identity must still match HR storage.
            ref = self.canonical_ref(source)
            _require(all(ref.get(key) == value for key, value in trusted.items()
                         if key != "exclusive_room_owner") and
                     ("exclusive_room_owner" not in ref or
                      ref["exclusive_room_owner"] == trusted.get("exclusive_room_owner")),
                     "owner_source_changed")
        return owner

    def create_binding(self, principal, body):
        _require(principal.role == "portal", "portal_required", 403)
        _reference(body.source_ref)
        with self._db(write=True) as db:
            owner = self.bridge.owner(db, body.owner_kind, body.owner_id, completed=True)
            authorize_owner(principal, owner)
            trusted = self.bridge.trusted_ref(owner, body.owner_kind, body.owner_id, body.provider, body.connection_id, db=db, source_ref=body.source_ref)
            required = {"room_id", "room_name", "capture_instance_id"}
            _require(required <= body.source_ref.keys() and all(trusted.get(k) == v for k, v in body.source_ref.items()),
                     "source_identity_conflict")
            # Match legacy capture-key precedence so existing catalogue IDs stay stable.
            key = "session_id" if trusted.get("session_id") else "capture_instance_id"
            capture = key + ":" + trusted[key]
            row = db.execute("SELECT * FROM media_sources WHERE provider=? AND connection_id=? AND capture_identity=?",
                             (body.provider, body.connection_id, capture)).fetchone()
            if row:
                source = self._decode(row)
                _require((source["owner_kind"], source["owner_id"]) == (body.owner_kind, body.owner_id),
                         "source_identity_conflict")
                _require(all(trusted.get(k) == v for k, v in self.canonical_ref(source).items()), "source_identity_conflict")
                _require(self._family_active(db, source), "binding_revoked", 410)
                if body.storage_policy == "owned" or source["storage_policy"] == "owned":
                    # Legacy session workers must not retain an old remote
                    # generation after the owner upgrades their capture root.
                    family = [r[0] for r in db.execute("""WITH RECURSIVE family(id) AS (
                        SELECT id FROM media_sources WHERE id=? UNION
                        SELECT s.id FROM media_sources s JOIN family f ON s.parent_source_id=f.id
                        WHERE s.provider=? AND s.connection_id=? AND s.interview_id IS ? AND s.meeting_id IS ? AND s.intake_id IS ?)
                        SELECT s.id FROM media_sources s JOIN family f ON f.id=s.id WHERE s.storage_policy<>'owned'""",
                        (source["id"], source["provider"], source["connection_id"], source["interview_id"], source["meeting_id"], source.get("intake_id")))]
                    for member in family:
                        self._invalidate(db, member)
                        db.execute("UPDATE media_sources SET storage_policy='owned' WHERE id=?", (member,))
                db.execute("UPDATE media_sources SET source_ref=? WHERE id=?", (_reference(trusted), source["id"]))
                return self.binding(self._one(db, "media_sources", source["id"]))
            identity, stamp = secrets.token_hex(16), self.clock()
            db.execute("""INSERT INTO media_sources
                (id,interview_id,meeting_id,intake_id,provider,connection_id,capture_identity,source_ref,storage_policy,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (identity, body.owner_id if body.owner_kind == "interview" else None,
                body.owner_id if body.owner_kind == "meeting" else None, body.owner_id if body.owner_kind == "intake" else None, body.provider, body.connection_id, capture,
                _reference(trusted), body.storage_policy, stamp, stamp))
            return self.binding(self._one(db, "media_sources", identity))

    def catalogue_assets(self, principal, created_since, cursor=None, limit=50):
        _require(math.isfinite(created_since) and created_since >= 0 and 1 <= limit <= 100,
                 "invalid_page", 400)
        after = (created_since, "")
        if cursor:
            try:
                value = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                _require(isinstance(value, list) and len(value) == 3 and value[0] == created_since
                         and type(value[1]) in (int, float) and math.isfinite(value[1]) and value[1] >= created_since
                         and isinstance(value[2], str) and len(value[2]) <= 500, "invalid_cursor", 400)
                after = (value[1], value[2])
            except (ValueError, TypeError, UnicodeError):
                raise RepositoryError("invalid_cursor", 400) from None
        with self._db() as db:
            rows = db.execute("""SELECT a.* FROM media_assets a
                WHERE a.kind IN ('video','audio') AND (a.created_at,a.id)>(?,?)
                ORDER BY a.created_at,a.id LIMIT ?""", (*after, limit)).fetchall()
            items = []
            for row in rows:
                asset = self._decode(row)
                source = self._one(db, "media_sources", asset["source_id"])
                try:
                    self._authorize(db, source, principal, active=True)
                    _require(asset["state"] == "ready" and source["storage_policy"] == "owned", "not_ready")
                    _require(db.execute("""SELECT 1 FROM asset_locations WHERE asset_id=? AND backend='s3'
                        AND state='verified' AND sha256 IS NOT NULL AND size>0""", (asset["id"],)).fetchone(), "not_ready")
                except RepositoryError:
                    continue
                items.append({"asset_id": asset["id"], "binding_id": source["id"], "generation": source["generation"],
                    "created_at": asset["created_at"], "kind": asset["kind"], "state": asset["state"],
                    **{k: source[k] for k in ("owner_kind", "owner_id", "provider", "connection_id", "storage_policy")}})
            next_cursor = base64.urlsafe_b64encode(json.dumps([created_since, rows[-1]["created_at"], rows[-1]["id"]]).encode()).decode() if len(rows) == limit else None
            return {"items": items, "next_cursor": next_cursor}

    def read_binding(self, principal, identity):
        with self._db() as db:
            source = self._one(db, "media_sources", identity)
            self._authorize(db, source, principal, active=principal.role == "source")
            return {**self.binding(source), "revoked": not self._family_active(db, source)}

    def _receipt_check(self, db, principal, identity, body, digest):
        _require(principal.role == "source", "source_required", 403)
        source = self._one(db, "media_sources", identity)
        self._authorize(db, source, principal, active=True)
        _require(body.generation == source["generation"], "stale_generation")
        prior = db.execute("""SELECT body_digest,response FROM core_material_receipts
            WHERE binding_id=? AND generation=? AND receipt_id=?""", (identity, body.generation, body.receipt_id)).fetchone()
        if prior:
            _require(prior["body_digest"] == digest, "receipt_conflict")
            return source, json.loads(prior["response"])
        return source, None

    def prepare_receipt(self, principal, identity, body):
        digest = fingerprint(body.model_dump(mode="json", exclude_unset=True))
        with self._db() as db:
            source, prior = self._receipt_check(db, principal, identity, body, digest)
            return source, prior, digest

    def publish_receipt(self, principal, identity, body, digest, verification):
        with self._db(write=True) as db:
            source, prior = self._receipt_check(db, principal, identity, body, digest)
            if prior is not None:
                return prior
            stamp, asset_ids, transcript_ids = self.clock(), [], []
            family = {r[0] for r in db.execute("""WITH RECURSIVE family(id) AS (
                SELECT id FROM media_sources WHERE id=? UNION
                SELECT s.id FROM media_sources s JOIN family f ON s.parent_source_id=f.id
                WHERE s.provider=? AND s.connection_id=? AND s.interview_id IS ? AND s.meeting_id IS ? AND s.intake_id IS ?
                AND COALESCE(json_extract(s.metadata,'$.capture_disabled'),0)<>1)
                SELECT id FROM family""", (identity, source["provider"], source["connection_id"],
                                         source["interview_id"], source["meeting_id"], source.get("intake_id")))}
            for item in body.assets:
                # A proven child of this binding may already hold a historical
                # asset. Update it in place without changing its source/IDs.
                matches = db.execute("""SELECT a.*,s.interview_id,s.meeting_id FROM media_assets a
                    JOIN media_sources s ON s.id=a.source_id
                    WHERE s.provider=? AND s.connection_id=? AND a.kind=? AND a.provider_asset_id=?""",
                    (source["provider"], source["connection_id"], item.kind, item.external_id)).fetchall()
                _require(len(matches) <= 1 and all(row["source_id"] in family for row in matches), "external_asset_conflict")
                row = matches[0] if matches else None
                asset_id = row["id"] if row else secrets.token_hex(16)
                metadata = item.metadata.model_dump(exclude_none=True)
                if metadata.get("session_id") and source["source_ref"].get("session_id"):
                    _require(metadata["session_id"] == source["source_ref"].get("session_id"), "asset_session_conflict")
                state = item.state
                if row:
                    if row["state"] == "ready" and state == "processing":
                        state = "ready"
                    metadata = {**json.loads(row["metadata"]), **metadata}
                    db.execute("UPDATE media_assets SET state=?,metadata=?,updated_at=? WHERE id=?",
                               (state, _reference(metadata), stamp, asset_id))
                else:
                    db.execute("INSERT INTO media_assets VALUES (?,?,?,?,?,?,?,?)",
                               (asset_id, identity, item.kind, item.external_id, state, _reference(metadata), stamp, stamp))
                asset_ids.append(asset_id)
                db.execute("""INSERT INTO core_material_asset_bindings VALUES (?,?)
                    ON CONFLICT(asset_id) DO UPDATE SET publishing_binding_id=excluded.publishing_binding_id""",
                    (asset_id, identity))
                if source["storage_policy"] == "owned" and item.kind in ("video", "audio") and item.state == "ready":
                    _require(any(location.backend == "s3" for location in item.locations), "owned_location_required")
                for location in item.locations:
                    if location.backend == "provider":
                        _require(location.object_key == item.external_id, "provider_location_identity_conflict")
                    self._location(db, asset_id, location, verification, stamp)
                if item.transcript:
                    t = item.transcript
                    raw = t.raw_ref.model_dump(exclude_none=True)
                    self._verified(raw, verification)
                    segments = [s.model_dump() for s in t.normalized.segments]
                    meta = {**t.provenance.model_dump(exclude_none=True), "normalized": t.normalized.model_dump()}
                    content = fingerprint({"raw_ref": raw, "segments": segments, "metadata": meta})
                    old = db.execute("SELECT id FROM transcript_versions WHERE asset_id=? AND content_sha256=?",
                                     (asset_id, content)).fetchone()
                    if old:
                        transcript_id = old[0]
                    else:
                        transcript_id = secrets.token_hex(16)
                        version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM transcript_versions WHERE asset_id=?",
                                             (asset_id,)).fetchone()[0]
                        db.execute("INSERT INTO transcript_versions VALUES (?,?,?,?,?,?,?,?)", (transcript_id, asset_id,
                            version, _reference(raw), encode(segments), encode(meta), content, stamp))
                    transcript_ids.append(transcript_id)
            statuses = {k: getattr(body, k) for k in ("video_status", "transcript_status", "acquisition_complete", "transcription_status")}
            db.execute("""INSERT INTO core_material_channels VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(binding_id) DO UPDATE SET generation=excluded.generation,
                video_status=excluded.video_status,transcript_status=excluded.transcript_status,
                acquisition_complete=excluded.acquisition_complete,transcription_status=excluded.transcription_status,
                updated_at=excluded.updated_at""", (identity, body.generation, body.video_status, body.transcript_status,
                    int(body.acquisition_complete), body.transcription_status, stamp))
            result = {"binding_id": identity, "generation": body.generation, "receipt_id": body.receipt_id,
                      "asset_ids": asset_ids, "transcript_ids": transcript_ids, **statuses}
            db.execute("INSERT INTO core_material_receipts VALUES (?,?,?,?,?,?)", (identity, body.generation,
                       body.receipt_id, digest, _json(result), stamp))
            return result

    @staticmethod
    def _verified(location, verification):
        item = verification.get(location["object_key"])
        _require(item is not None and item.sha256 == location["sha256"] and item.size == location["size"],
                 "object_not_verified")
        return item

    def _location(self, db, asset_id, location, verification, stamp):
        metadata, state = {}, location.state
        if location.backend == "s3":
            checked = self._verified(location.model_dump(), verification)
            metadata = {"storage_connection_id": verification["_connection_id"], "content_type": checked.content_type,
                        "verification": "head_metadata"}
            state = "verified"
        elif location.content_type:
            metadata["content_type"] = location.content_type
        row = db.execute("SELECT * FROM asset_locations WHERE asset_id=? AND backend=? AND object_key=?",
                         (asset_id, location.backend, location.object_key)).fetchone()
        if row:
            _require(not row["sha256"] or not location.sha256 or row["sha256"] == location.sha256,
                     "location_content_conflict")
            db.execute("UPDATE asset_locations SET state=?,size=?,sha256=?,metadata=?,updated_at=? WHERE id=?",
                       (state, location.size, location.sha256, _reference(metadata), stamp, row["id"]))
        else:
            db.execute("INSERT INTO asset_locations VALUES (?,?,?,?,?,?,?,?,?,?)", (secrets.token_hex(16), asset_id,
                       location.backend, location.object_key, state, location.size, location.sha256,
                       _reference(metadata), stamp, stamp))

    def _selection_family(self, db, source):
        rows = db.execute("""WITH RECURSIVE family(id) AS (
            SELECT id FROM media_sources WHERE id=? UNION
            SELECT s.id FROM media_sources s JOIN family f ON s.parent_source_id=f.id
            WHERE s.provider=? AND s.connection_id=? AND s.interview_id IS ? AND s.meeting_id IS ? AND s.intake_id IS ?)
            SELECT s.* FROM media_sources s JOIN family f ON s.id=f.id""",
            (source["id"], source["provider"], source["connection_id"], source["interview_id"], source["meeting_id"], source.get("intake_id")))
        return {row["id"] for row in rows if self._binding_current(db, self._decode(row))}

    def _binding_current(self, db, source):
        try:
            if not self._family_active(db, source): return False
            owner = self.bridge.owner(db, source["owner_kind"], source["owner_id"], completed=True)
            trusted = self.bridge.trusted_ref(owner, source["owner_kind"], source["owner_id"], source["provider"], source["connection_id"], db=db, source_ref=self.canonical_ref(source))
            ref = self.canonical_ref(source)
            return all(ref.get(key) == value for key, value in trusted.items() if key != "exclusive_room_owner") and (
                "exclusive_room_owner" not in ref or ref["exclusive_room_owner"] == trusted.get("exclusive_room_owner"))
        except RepositoryError:
            return False

    def _selection_recording(self, db, source, recording_id):
        row = db.execute("SELECT * FROM media_assets WHERE id=?", (recording_id,)).fetchone()
        _require(row is not None, "recording_not_found", 404)
        _require(row["kind"] in ("video", "audio"), "recording_kind_required")
        _require(row["source_id"] in self._selection_family(db, source), "recording_binding_conflict")
        _require(row["state"] == "ready", "recording_not_ready")
        return self._decode(row)

    def _selected_transcript(self, db, source, recording, transcript_id):
        row = db.execute("""SELECT t.*,a.source_id,a.kind,a.state FROM transcript_versions t
            JOIN media_assets a ON a.id=t.asset_id WHERE t.id=?""", (transcript_id,)).fetchone()
        _require(row is not None, "transcript_not_found", 404)
        family = self._selection_family(db, source)
        _require(row["source_id"] in family and row["kind"] == "transcript" and row["state"] == "ready",
                 "transcript_binding_conflict")
        # Only a Core-confirmed immutable version published to this binding and
        # generation can become primary. Collector-local rows alone are insufficient.
        attested = any(transcript_id in json.loads(receipt["response"]).get("transcript_ids", []) for receipt in db.execute(
            "SELECT response FROM core_material_receipts WHERE binding_id=? AND generation=?",
            (source["id"], source["generation"])))
        _require(attested, "transcript_not_confirmed")
        transcript = self._decode(row)
        metadata = transcript["metadata"]
        _require(metadata.get("processing_status") == "completed" and bool(transcript["segments"]), "transcript_not_ready")
        input_asset, input_sha = metadata.get("input_asset_id"), metadata.get("input_sha256")
        if input_asset is not None or input_sha is not None:
            _require(input_asset == recording["id"] and isinstance(input_sha, str), "transcript_recording_conflict")
            known = {location[0] for location in db.execute(
                "SELECT sha256 FROM asset_locations WHERE asset_id=? AND sha256 IS NOT NULL", (recording["id"],))}
            _require(not known or input_sha in known, "transcript_input_sha256_conflict")
        else:
            # Compatibility for immutable receipts predating input provenance.
            # Never infer an input recording from provider/model/external ID text.
            recordings = [asset["id"] for asset in db.execute("SELECT id,source_id FROM media_assets WHERE kind IN ('video','audio')")
                          if asset["source_id"] in family]
            _require(recordings == [recording["id"]], "ambiguous_transcript_recording")
        return {key: transcript[key] for key in ("id", "asset_id", "version", "raw_ref", "segments", "metadata", "content_sha256", "created_at")}

    @staticmethod
    def _selection_dto(row):
        return {key: row[key] for key in ("binding_id", "generation", "recording_id", "request_id", "revision", "status", "transcript_id", "error_code", "updated_at")}

    def select_transcript(self, principal, binding_id, recording_id, body):
        _require(principal.role == "source", "source_required", 403)
        with self._db(write=True) as db:
            source = self._one(db, "media_sources", binding_id)
            self._authorize(db, source, principal, active=True)
            _require(body.generation == source["generation"], "stale_generation")
            recording = self._selection_recording(db, source, recording_id)
            current = db.execute("""SELECT * FROM core_transcript_selections
                WHERE binding_id=? AND generation=? AND recording_id=?""", (binding_id, body.generation, recording_id)).fetchone()
            if current and current["request_id"] == body.request_id:
                _require(body.expected_revision == current["expected_revision"], "selection_revision_conflict")
                # Pending replay is an acknowledgement, never a terminal-state reset.
                if body.status == "pending":
                    if current["status"] == "ready": self._selected_transcript(db, source, recording, current["transcript_id"])
                    return self._selection_dto(current)
                if current["status"] != "pending":
                    _require((body.status, body.transcript_id, body.error_code) ==
                             (current["status"], current["transcript_id"], current["error_code"]), "selection_terminal_conflict")
                    if body.status == "ready": self._selected_transcript(db, source, recording, body.transcript_id)
                    return self._selection_dto(current)
                if body.status == "ready": self._selected_transcript(db, source, recording, body.transcript_id)
                db.execute("""UPDATE core_transcript_selections SET status=?,transcript_id=?,error_code=?,updated_at=?
                    WHERE binding_id=? AND generation=? AND recording_id=?""", (body.status, body.transcript_id,
                    body.error_code, self.clock(), binding_id, body.generation, recording_id))
            else:
                _require(body.status == "pending", "selection_pending_required")
                seen = db.execute("""SELECT 1 FROM core_transcript_selection_requests
                    WHERE binding_id=? AND generation=? AND recording_id=? AND request_id=?""",
                    (binding_id, body.generation, recording_id, body.request_id)).fetchone()
                _require(seen is None, "selection_request_superseded")
                revision = current["revision"] if current else 0
                _require(body.expected_revision == revision, "selection_revision_conflict")
                db.execute("INSERT INTO core_transcript_selection_requests VALUES (?,?,?,?,?,?)",
                    (binding_id, body.generation, recording_id, body.request_id, body.expected_revision, revision + 1))
                db.execute("""INSERT INTO core_transcript_selections
                    (binding_id,generation,recording_id,request_id,expected_revision,revision,status,transcript_id,error_code,updated_at)
                    VALUES (?,?,?,?,?,?,'pending',NULL,NULL,?)
                    ON CONFLICT(binding_id,generation,recording_id) DO UPDATE SET request_id=excluded.request_id,
                    expected_revision=excluded.expected_revision,revision=excluded.revision,status='pending',
                    transcript_id=NULL,error_code=NULL,updated_at=excluded.updated_at""",
                    (binding_id, body.generation, recording_id, body.request_id, body.expected_revision, revision + 1, self.clock()))
            return self._selection_dto(db.execute("""SELECT * FROM core_transcript_selections
                WHERE binding_id=? AND generation=? AND recording_id=?""", (binding_id, body.generation, recording_id)).fetchone())

    def _selection_view(self, db, kind, identity, visible_assets):
        visible = {asset["id"] for asset in visible_assets if asset["kind"] in ("audio", "video")}
        selections, transcripts, required = [], {}, set()
        stale = invalid = False
        rows = db.execute(f"""SELECT x.* FROM core_transcript_selections x JOIN media_sources s ON s.id=x.binding_id
            WHERE s.{kind}_id=? ORDER BY x.recording_id,x.updated_at,x.binding_id""", (identity,))
        for row in rows:
            if row["recording_id"] not in visible: continue
            source = self._one(db, "media_sources", row["binding_id"])
            required.add(row["recording_id"])
            if not self._binding_current(db, source):
                invalid = True
                continue
            if row["generation"] != source["generation"]:
                stale = True
                continue
            try:
                _require(row["status"] in ("pending", "ready", "failed", "uncertain"), "selection_state_invalid")
                recording = self._selection_recording(db, source, row["recording_id"])
                chosen = self._selected_transcript(db, source, recording, row["transcript_id"]) if row["status"] == "ready" else None
            except RepositoryError:
                invalid = True
                continue
            if any(item["recording_id"] == row["recording_id"] for item in selections):
                invalid = True
            selections.append(self._selection_dto(row))
            if chosen: transcripts[chosen["id"]] = chosen
        # Old generation history is normal once a new current selection exists.
        current_ids = {item["recording_id"] for item in selections}
        stale = bool(required - current_ids)
        return selections, list(transcripts.values()), sorted(required), stale, invalid

    def _selection_revisions(self, db, kind, identity, visible_assets):
        # CAS recovery metadata is independent from whether the selected target
        # remains usable. It exposes no stale transcript IDs or content.
        visible = {asset["id"] for asset in visible_assets if asset["kind"] in ("audio", "video")}
        revisions = []
        for row in db.execute(f"""SELECT x.* FROM core_transcript_selections x JOIN media_sources s ON s.id=x.binding_id
                WHERE s.{kind}_id=? ORDER BY x.binding_id,x.recording_id""", (identity,)):
            if row["recording_id"] not in visible: continue
            source = self._one(db, "media_sources", row["binding_id"])
            if row["generation"] != source["generation"] or not self._binding_current(db, source): continue
            try: self._selection_recording(db, source, row["recording_id"])
            except RepositoryError: continue
            revisions.append({key: row[key] for key in ("binding_id", "generation", "recording_id", "revision")})
        return revisions

    def read_primary_transcripts(self, principal, kind, identity):
        with self._db() as db:
            db.execute("BEGIN")
            owner = self.bridge.owner(db, kind, identity)
            authorize_owner(principal, owner)
            sources = [self._decode(row) for row in db.execute(f"SELECT * FROM media_sources WHERE {kind}_id=?", (identity,))]
            if sources:
                _require(any(self._family_active(db, source) for source in sources), "binding_revoked", 410)
            visible = [self._decode(asset) for source in sources if self._family_active(db, source) for asset in db.execute(
                "SELECT * FROM media_assets WHERE source_id=? AND kind IN ('video','audio')", (source["id"],))]
            selections, transcripts, required, stale, invalid = self._selection_view(db, kind, identity, visible)
            if invalid or any(item["status"] in ("failed", "uncertain") for item in selections): status = "error"
            elif stale or any(item["status"] == "pending" for item in selections): status = "processing"
            elif selections: status = "ready"
            else: status = "not_requested"
            return {"owner_kind": kind, "owner_id": identity, "status": status,
                    "selections": selections, "transcripts": [] if invalid or stale else transcripts,
                    "transcript_selection_required_recording_ids": required,
                    "transcript_selection_revisions": self._selection_revisions(db, kind, identity, visible)}

    def _materials(self, db, kind, identity):
        sources = [self._decode(r) for r in db.execute(
            f"SELECT * FROM media_sources WHERE {kind}_id=? ORDER BY created_at,id", (identity,))]
        assets, transcripts, channels = [], [], []
        active = [source for source in sources if self._family_active(db, source)]
        for source in active:
            # Revoked metadata remains listed for history, while content/access
            # stays unavailable through the same capture_disabled boundary.
            for row in db.execute("SELECT * FROM media_assets WHERE source_id=? ORDER BY created_at,id", (source["id"],)):
                asset = self._decode(row)
                asset["external_id"] = asset["provider_asset_id"]
                asset["locations"] = [self._decode(r) for r in db.execute(
                    "SELECT * FROM asset_locations WHERE asset_id=? ORDER BY created_at,id", (asset["id"],))]
                assets.append(asset)
                transcripts.extend(self._decode(r) for r in db.execute(
                    "SELECT * FROM transcript_versions WHERE asset_id=? ORDER BY version,id", (asset["id"],)))
        for source in active:
            channel = db.execute("SELECT * FROM core_material_channels WHERE binding_id=? AND generation=?",
                                 (source["id"], source["generation"])).fetchone()
            if channel:
                item = dict(channel)
                item["acquisition_complete"] = bool(item["acquisition_complete"])
                channels.append(item)
            else:
                channels.append(self._collector_channels(db, source, active, assets, transcripts))
        selections, _, required, _, _ = self._selection_view(db, kind, identity, assets)
        return {"owner_kind": kind, "owner_id": identity, "sources": [
                    {**self.binding(s), "revoked": not self._family_active(db, s)} for s in sources],
                "assets": assets, "transcripts": transcripts, "channels": channels,
                "transcript_selections": selections,
                "transcript_selection_required_recording_ids": required,
                "transcript_selection_revisions": self._selection_revisions(db, kind, identity, assets)}

    def _collector_channels(self, db, source, active, assets, transcripts):
        # Reuse the worker's durable facts. No provider calls and no ASR or
        # complete-speech claims follow from an imported file being present.
        family = {source["id"]}
        for _ in active:
            added = {s["id"] for s in active if s["parent_source_id"] in family}
            if added <= family:
                break
            family.update(added)
        members = [s for s in active if s["id"] in family]
        policies = {s["id"]: s["storage_policy"] for s in members}
        related = [a for a in assets if a["source_id"] in family]
        kinds = {a["id"]: a["kind"] for a in related}
        normalized = {t["asset_id"] for t in transcripts if t["segments"]}
        latest = {}
        for member in members:
            for row in db.execute("SELECT * FROM media_jobs WHERE source_id=? AND generation=? ORDER BY created_at,id",
                                  (member["id"], member["generation"])):
                job = self._decode(row)
                latest[(member["id"], job["operation"], job["payload"].get("asset_id"))] = job

        def channel(kind):
            material = [a for a in related if a["kind"] == kind]
            ready = (any(a["id"] in normalized for a in material) if kind == "transcript" else any(
                a["state"] == "ready" and any(location["state"] == "verified" or
                    policies[a["source_id"]] == "remote" and location["backend"] == "provider" and location["state"] == "available"
                    for location in a["locations"]) for a in material))
            if ready:
                return "ready"
            jobs = [j for j in latest.values() if j["operation"] == "discover" or
                    kinds.get(j["payload"].get("asset_id")) == kind]
            evidence = [s["metadata"].get("discovery", {}).get(kind, {}) for s in members]
            if any(j["status"] in ("pending", "retry", "running") for j in jobs):
                return "processing"
            if any(j["status"] == "failed" for j in jobs) or any(
                    e.get("status") in ("error", "failed", "blocked") for e in evidence) or any(
                    a["state"] == "error" for a in material) or source["state"] == "error":
                return "error"
            if source["state"] == "unsupported" or any(a["state"] == "unsupported" for a in material) or any(
                    e.get("status") == "unsupported" for e in evidence):
                return "unsupported"
            if source["state"] == "not_recorded" or any(
                    e.get("status") in ("absent", "not_recorded") and e.get("complete") is True for e in evidence):
                return "not_recorded"
            return "error" if source["state"] in ("ready", "partial", "missing") else "processing"

        asr = source["metadata"].get("asr", {})
        transcription = asr.get("state", "not_requested")
        if transcription == "awaiting_provider" and asr.get("reason") == "no_asr_provider_selected":
            transcription = "awaiting_configuration"
        if transcription not in ("not_requested", "awaiting_configuration", "queued", "running", "succeeded", "failed"):
            transcription = "not_requested"
        return {"binding_id": source["id"], "generation": source["generation"],
                "video_status": channel("video"), "transcript_status": channel("transcript"),
                "acquisition_complete": source["metadata"].get("discovery_complete") is True and not any(
                    j["status"] in ("pending", "retry", "running") for j in latest.values()),
                "transcription_status": transcription, "collector_state": source["state"]}

    def read_materials(self, principal, kind, identity):
        with self._db() as db:
            db.execute("BEGIN")
            owner = self.bridge.owner(db, kind, identity)
            authorize_owner(principal, owner)
            return self._materials(db, kind, identity)

    def collection_owner(self, principal, kind, identity):
        with self._db() as db:
            db.execute("BEGIN")
            owner = self.bridge.owner(db, kind, identity, completed=True)
            authorize_owner(principal, owner)
            try:
                room = json.loads(owner["room"]) if isinstance(owner["room"], str) else owner["room"]
            except (ValueError, TypeError):
                raise RepositoryError("owner_room_unavailable") from None
            _require(isinstance(room, dict), "owner_room_unavailable")
            provider = room.get("provider", "daily")
            connection = room.get("connectionId", self.bridge.default_connections.get(provider))
            ref = self.bridge.trusted_ref(owner, kind, identity, provider, connection)
            # The collector receives only the HR-owned room. A source already
            # revoked for this exact capture cannot be revived by collect.
            key = "session_id" if ref.get("session_id") else "capture_instance_id"
            existing = db.execute("SELECT * FROM media_sources WHERE provider=? AND connection_id=? AND capture_identity=?",
                                  (provider, connection, key + ":" + ref[key])).fetchone()
            if existing:
                self._authorize(db, self._decode(existing), principal, active=True)
            return {"id": identity, "room": room}

    def access_context(self, principal, asset_id):
        with self._db() as db:
            db.execute("BEGIN")
            asset = self._one(db, "media_assets", asset_id)
            source = self._one(db, "media_sources", asset["source_id"])
            _require(principal.role == "portal", "portal_required", 403)
            self._authorize(db, source, principal, active=True)
            publication = db.execute("SELECT publishing_binding_id FROM core_material_asset_bindings WHERE asset_id=?",
                                     (asset_id,)).fetchone()
            if publication and publication[0] != source["id"]:
                publisher = self._one(db, "media_sources", publication[0])
                _require((publisher["owner_kind"], publisher["owner_id"], publisher["provider"], publisher["connection_id"]) ==
                         (source["owner_kind"], source["owner_id"], source["provider"], source["connection_id"]),
                         "external_asset_conflict")
                ancestors = {r[0] for r in db.execute("""WITH RECURSIVE ancestors(id,parent_source_id) AS (
                    SELECT id,parent_source_id FROM media_sources WHERE id=? UNION
                    SELECT s.id,s.parent_source_id FROM media_sources s JOIN ancestors a ON s.id=a.parent_source_id)
                    SELECT id FROM ancestors""", (source["id"],))}
                _require(publisher["id"] in ancestors, "external_asset_conflict")
                self._authorize(db, publisher, principal, active=True)
                source = publisher
            _require(asset["state"] == "ready", "asset_not_ready")
            locations = [self._decode(r) for r in db.execute(
                "SELECT * FROM asset_locations WHERE asset_id=? ORDER BY created_at,id", (asset_id,))]
            return asset, source, locations

    def dossier(self, principal, candidate_id):
        portal(principal, candidate_id)
        with self._db() as db:
            db.execute("BEGIN")
            person = db.execute("SELECT id,email,profile,created_at,updated_at FROM cp_candidates WHERE id=?",
                                (candidate_id,)).fetchone()
            _require(person is not None, "candidate_not_found", 404)
            profile = json.loads(person["profile"])
            result = {"candidate_id": candidate_id, "email": person["email"],
                      "profile": {key: value for key, value in profile.items() if key in PROFILE_FIELDS},
                      "created_at": person["created_at"], "updated_at": person["updated_at"],
                      "resume": None, "interviews": [], "meetings": [], "consents": [],
                      "channel_linkage": {"email": {"linked": bool(person["email"]), "verified": None},
                                          "telegram": {"linked": None, "verified": None}}}
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "cp_resumes" in tables:
                resume = db.execute("SELECT filename,mime,uploaded_at FROM cp_resumes WHERE candidate_id=?", (candidate_id,)).fetchone()
                result["resume"] = dict(resume) if resume else None
            for kind, query in (("interview", "SELECT id,status,created_at,completed_at FROM cp_interviews WHERE candidate_id=?"),
                ("meeting", """SELECT m.id,m.status,m.application_id,m.start,m.end,m.completed_at FROM cp_meetings m
                    JOIN cp_applications a ON a.id=m.application_id WHERE a.candidate_id=?""")):
                for row in db.execute(query + " ORDER BY " + ("id" if kind == "interview" else "m.id"), (candidate_id,)):
                    item = dict(row)
                    item["materials"] = self._materials(db, kind, row["id"])
                    result["interviews" if kind == "interview" else "meetings"].append(item)
            if "cp_consent_log" in tables:
                result["consents"].extend(dict(r) for r in db.execute(
                    "SELECT id,created_at,reuse,auto_approve,version FROM cp_consent_log WHERE candidate_id=? ORDER BY created_at,id",
                    (candidate_id,)))
            if "cp_media_consents" in tables:
                ids = [x["id"] for k in ("interviews", "meetings") for x in result[k]]
                for identity in ids:
                    result["consents"].extend(dict(r) for r in db.execute(
                        "SELECT meeting_id,actor_id,consented_at,version FROM cp_media_consents WHERE meeting_id=? ORDER BY actor_id", (identity,)))
            if "cp_recording_control" in tables:
                for interview in result["interviews"]:
                    control = db.execute("SELECT interview_id,consent_received_at,consent_source FROM cp_recording_control WHERE interview_id=?",
                                         (interview["id"],)).fetchone()
                    if control and control["consent_received_at"]:
                        result["consents"].append(dict(control))
            if "cn_recipients" in tables:
                recipient = db.execute("SELECT email,email_verified,telegram_chat_id,telegram_verified_at FROM cn_recipients WHERE candidate_id=?",
                                       (candidate_id,)).fetchone()
                if recipient:
                    result["channel_linkage"] = {"email": {"linked": bool(person["email"]),
                        "verified": bool(person["email"] and recipient["email"] == person["email"] and recipient["email_verified"])},
                        "telegram": {"linked": bool(person["email"] and recipient["telegram_chat_id"] and recipient["telegram_verified_at"] is not None),
                                     "verified": bool(person["email"] and recipient["telegram_chat_id"] and recipient["telegram_verified_at"] is not None)}}
            return result
