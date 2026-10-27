"""Read-only HR adapter: existing Core dossier/primary reads + stored CV text.

This module belongs to the trusted HR boundary, not the model service. It does
not acquire media, choose ASR versions, parse CVs, call models, or write a DB.
The caller MUST supply its normal candidate authorization check. Core service
credentials alone do not authorize a browser user to access another candidate.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote


VERSION = "hr-candidate-corpus-v1"


class CollectionError(ValueError):
    """Safe machine code only; never echo source text or credentials."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _require(condition, code):
    if not condition:
        raise CollectionError(code)


def _id(value):
    _require(isinstance(value, str) and 0 < len(value) <= 500 and
             not any(ord(c) < 32 or ord(c) == 127 for c in value), "invalid_source_identity")
    return value


def _indexed(items, key):
    _require(isinstance(items, list), "invalid_source_collection")
    result = {}
    for item in items:
        _require(isinstance(item, dict), "invalid_source_record")
        identity = _id(item.get(key))
        _require(identity not in result, "duplicate_source_identity")
        result[identity] = item
    return result


def _selection(value):
    return {k: value.get(k) for k in (
        "binding_id", "generation", "recording_id", "request_id", "revision",
        "status", "transcript_id", "error_code")}


class SQLiteResumeReader:
    """Fixed, operator-configured HR DB. Use only AFTER candidate authorization.

    No caller-supplied filename or SQL; no migrations, extraction or write mode.
    Original bytes are hashed locally and never returned to the model service.
    """
    def __init__(self, database_path):
        self.path = Path(database_path)
        _require(self.path.is_absolute() and self.path.is_file(), "existing_hr_database_required")

    def __call__(self, candidate_id):
        _id(candidate_id)
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=10)
        try:
            db.execute("PRAGMA query_only=ON")
            db.row_factory = sqlite3.Row
            row = db.execute("""SELECT candidate_id,filename,mime,text_content,content,uploaded_at
                FROM cp_resumes WHERE candidate_id=?""", (candidate_id,)).fetchone()
            if row is None:
                return None
            item = dict(row)
            raw = item.pop("content")
            _require(isinstance(raw, bytes), "invalid_resume_bytes")
            item["original_sha256"] = hashlib.sha256(raw).hexdigest()
            return item
        finally:
            db.close()


class CandidateCorpusCollector:
    """Adapter over an EXISTING CoreClient and a portal-owned CV reader.

    collect() performs two complete read passes and refuses changed inputs.
    The result contains corpus only when ready; missing parts cannot accidentally
    be submitted as a smaller successful corpus. Inspect manifest for blockers.
    Call revalidate() again immediately before dispatch and before publication.
    """
    def __init__(self, core, read_resume, *, authorize):
        _require(callable(read_resume) and callable(authorize), "authorization_and_resume_reader_required")
        self.core, self.read_resume, self.authorize = core, read_resume, authorize

    def collect(self, candidate_id):
        _id(candidate_id)
        first = self._read(candidate_id)
        second = self._read(candidate_id)
        _require(first["revision"] == second["revision"], "sources_changed_during_collection")
        _require(self.authorize(candidate_id) is not False, "candidate_access_denied")
        return second

    def revalidate(self, snapshot):
        current = self.collect(snapshot["candidate_id"])
        _require(current["revision"] == snapshot["revision"], "sources_changed_since_collection")
        _require(current["status"] == "ready", "corpus_not_ready")
        return current

    def _read(self, candidate_id):
        # An exception or explicit False denies access. Existing portal checks
        # commonly return None on success; never catch authorization failures.
        _require(self.authorize(candidate_id) is not False, "candidate_access_denied")
        dossier = self.core.http.call("GET", "/v1/candidates/" + quote(candidate_id, safe="") + "/dossier")
        _require(isinstance(dossier, dict) and dossier.get("candidate_id") == candidate_id,
                 "candidate_identity_mismatch")
        cv = self.read_resume(candidate_id)
        sources, issues = [], []
        manifest = {"contract_version": VERSION, "candidate_id": candidate_id,
                    "scope": ["interviews", "meetings"], "resume": None, "conversations": []}
        self._resume(candidate_id, dossier.get("resume"), cv, sources, issues, manifest)
        for group, kind in (("interviews", "interview"), ("meetings", "meeting")):
            owners = _indexed(dossier.get(group), "id")
            for owner_id in owners:
                owner = owners[owner_id]
                inventory = self._conversation(kind, owner_id, owner, sources, issues)
                manifest["conversations"].append(inventory)
        manifest["issues"] = issues
        manifest["source_count"] = len(sources)
        manifest["unit_count"] = sum(len(s["units"]) for s in sources)
        manifest["text_characters"] = sum(len(u["text"]) for s in sources for u in s["units"])
        if not any(u["text"].strip() for s in sources for u in s["units"]):
            issues.append({"code": "no_source_text"})
        body = {"candidate_id": candidate_id, "sources": sources}
        # Include exact text and provenance in identity even for a blocked read;
        # blocked results expose only counts and integrity metadata, no corpus.
        revision = _hash({"manifest": manifest, "corpus": body})
        return {"candidate_id": candidate_id, "status": "blocked" if issues else "ready",
                "revision": revision, "corpus": None if issues else body, "manifest": manifest}

    @staticmethod
    def _resume(candidate_id, metadata, cv, sources, issues, manifest):
        _require((metadata is None) == (cv is None), "resume_changed_during_collection")
        if cv is None:
            manifest["resume"] = {"status": "absent"}
            return
        _require(isinstance(metadata, dict) and isinstance(cv, dict) and
                 cv.get("candidate_id") == candidate_id, "resume_candidate_mismatch")
        _require(all(metadata.get(k) == cv.get(k) for k in ("filename", "mime", "uploaded_at")),
                 "resume_changed_during_collection")
        text = cv.get("text_content")
        _require(isinstance(text, str) and isinstance(cv.get("original_sha256"), str), "invalid_resume_text")
        provenance = {k: cv[k] for k in ("filename", "mime", "uploaded_at", "original_sha256")}
        provenance["text_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        version = _hash(provenance)
        manifest["resume"] = {"status": "ready" if text.strip() else "text_unavailable",
                              **provenance, "version": version, "characters": len(text)}
        if not text.strip():
            issues.append({"code": "resume_text_unavailable"})
        sources.append({"source_id": "cv:" + candidate_id, "version": version, "kind": "cv",
                        "units": [{"unit_id": "stored-text", "text": text, "role": "unknown"}]})

    def _conversation(self, kind, owner_id, owner, sources, issues):
        materials = owner.get("materials")
        _require(isinstance(materials, dict) and materials.get("owner_kind") == kind and
                 materials.get("owner_id") == owner_id, "materials_owner_mismatch")
        bindings = _indexed(materials.get("sources"), "id")
        assets = _indexed(materials.get("assets"), "id")
        channels = _indexed(materials.get("channels"), "binding_id")
        entry = {"owner_kind": kind, "owner_id": owner_id, "lifecycle_status": owner.get("status"),
                 "bindings": [], "recordings": [], "warnings": [], "status": "ready"}

        def block(code, **details):
            entry["status"] = "blocked"
            issues.append({"code": code, "owner_kind": kind, "owner_id": owner_id, **details})

        for binding_id, binding in bindings.items():
            _require(binding.get("owner_kind") == kind and binding.get("owner_id") == owner_id and
                     binding.get("binding_id") == binding_id, "binding_owner_mismatch")
            _require(type(binding.get("generation")) is int and binding["generation"] > 0 and
                     type(binding.get("revoked")) is bool, "invalid_binding_version")
            channel = channels.get(binding_id)
            facts = {k: binding.get(k) for k in ("id", "generation", "revoked", "provider", "connection_id", "source_ref")}
            facts["channel"] = ({k: channel.get(k) for k in (
                "generation", "acquisition_complete", "video_status", "transcript_status",
                "transcription_status")} if channel else None)
            entry["bindings"].append(facts)
            if binding["revoked"]:
                block("binding_revoked", binding_id=binding_id)
            elif not channel or channel.get("generation") != binding["generation"] or channel.get("acquisition_complete") is not True:
                block("acquisition_incomplete", binding_id=binding_id)
        _require(set(channels) <= set(bindings), "channel_binding_mismatch")
        for asset in assets.values():
            _require(asset.get("source_id") in bindings, "asset_binding_mismatch")
        recordings = {aid: a for aid, a in assets.items() if a.get("kind") in ("audio", "video")}
        # All stored conversation owners are accounted for, including employer
        # interviews. A not-yet-held appointment contains no interview text yet.
        if not bindings and not assets and owner.get("status") in (
                "booked", "scheduled", "confirmed", "pending", "cancelled", "declined"):
            entry["status"] = "not_conducted"
            return entry
        if not bindings:
            block("materials_not_discovered")
        if not recordings:
            confirmed_absent = bool(bindings) and all(
                not b["revoked"] and channels.get(bid, {}).get("acquisition_complete") is True and
                channels.get(bid, {}).get("video_status") == "not_recorded" and
                channels.get(bid, {}).get("transcript_status") == "not_recorded"
                for bid, b in bindings.items())
            if confirmed_absent:
                # Honest absence, distinct from a provider/API/ASR failure.
                entry["status"] = "not_recorded" if entry["status"] == "ready" else entry["status"]
            else:
                block("recording_inventory_unavailable")
            return entry
        if any(b["revoked"] for b in bindings.values()):
            return entry  # Never read revoked text, even from a local cache.
        if owner.get("status") != "completed":
            block("conversation_not_completed")
        primary = self.core.primary_transcripts(kind, owner_id)
        _require(isinstance(primary, dict) and primary.get("owner_kind") == kind and
                 primary.get("owner_id") == owner_id, "primary_owner_mismatch")
        entry["primary_status"] = primary.get("status")
        selected = _indexed(primary.get("selections"), "recording_id")
        transcripts = _indexed(primary.get("transcripts"), "id")
        _require(set(selected) <= set(recordings), "selection_recording_mismatch")
        required = primary.get("transcript_selection_required_recording_ids")
        _require(isinstance(required, list) and set(required) <= set(recordings), "required_recording_mismatch")
        # Dossier and primary are separate HTTP reads. Both must describe exactly
        # the same selection, not merely a provider's generic ready flag.
        in_dossier = _indexed(materials.get("transcript_selections"), "recording_id")
        _require({k: _selection(v) for k, v in selected.items()} ==
                 {k: _selection(v) for k, v in in_dossier.items()} and
                 sorted(required) == sorted(materials.get("transcript_selection_required_recording_ids", [])),
                 "primary_changed_during_collection")
        if primary.get("status") != "ready":
            block("primary_not_ready", primary_status=primary.get("status"))
        used = set()
        # Preserve the collector's inventory order. Opaque asset IDs carry no
        # chronology and must not reorder recording parts with reset timecodes.
        for recording_id, recording in recordings.items():
            selection = selected.get(recording_id)
            detail = {"recording_id": recording_id, "kind": recording["kind"],
                      "source_id": recording["source_id"], "selection": _selection(selection) if selection else None}
            entry["recordings"].append(detail)
            if not selection:
                block("primary_missing_recording", recording_id=recording_id)
                continue
            binding = bindings.get(selection.get("binding_id"))
            _require(binding is not None and selection.get("generation") == binding["generation"],
                     "selection_binding_mismatch")
            _require(type(selection.get("revision")) is int and selection["revision"] > 0,
                     "invalid_selection_revision")
            if selection.get("status") != "ready":
                block("recording_transcript_not_ready", recording_id=recording_id,
                      selection_status=selection.get("status"))
                continue
            transcript_id = selection.get("transcript_id")
            transcript = transcripts.get(transcript_id)
            if transcript is None:
                block("selected_transcript_unavailable", recording_id=recording_id)
                continue
            _require(transcript_id not in used, "transcript_reused_across_recordings")
            used.add(transcript_id)
            asset = assets.get(transcript.get("asset_id"))
            _require(asset is not None and asset.get("kind") == "transcript" and
                     asset.get("state") == "ready", "transcript_asset_mismatch")
            segments, meta = transcript.get("segments"), transcript.get("metadata")
            _require(isinstance(segments, list) and segments and isinstance(meta, dict), "invalid_transcript")
            _require(meta.get("processing_status") == "completed", "transcript_not_completed")
            _require(meta.get("input_asset_id") in (None, recording_id), "transcript_input_mismatch")
            # This is Core's immutable version hash, not our normalized corpus
            # hash. Keep both identities rather than comparing unlike hashes.
            expected_hash = _hash({"raw_ref": transcript.get("raw_ref"), "segments": segments, "metadata": meta})
            _require(expected_hash == transcript.get("content_sha256"), "transcript_content_hash_mismatch")
            units = []
            for index, segment in enumerate(segments):
                _require(isinstance(segment, dict) and set(segment) <= {"text", "speaker", "start_ms", "end_ms"} and
                         isinstance(segment.get("text"), str), "invalid_transcript_segment")
                unit = {"unit_id": "segment:" + str(index), "text": segment["text"], "role": "unknown"}
                for field in ("speaker", "start_ms", "end_ms"):
                    if field in segment:
                        unit[field] = segment[field]
                units.append(unit)
            normalized = meta.get("normalized")
            _require(isinstance(normalized, dict) and normalized.get("segments") == segments and
                     normalized.get("text") == normalized.get("full_text") == "\n".join(u["text"] for u in units),
                     "normalized_transcript_mismatch")
            # The ASR worker normally declares not_independently_verified:
            # completion does not certify recognition of every spoken word.
            # Preserve that uncertainty, without treating it as proven truncation.
            coverage = meta.get("coverage")
            if coverage in ("partial", "truncated", "incomplete"):
                block("transcript_coverage_not_full", recording_id=recording_id)
            elif coverage != "full":
                entry["warnings"].append({"code": "speech_coverage_unverified",
                                           "recording_id": recording_id, "coverage": coverage})
            provenance = {"selection": _selection(selection), "transcript_id": transcript_id,
                          "content_sha256": expected_hash, "raw_ref": transcript.get("raw_ref"),
                          "metadata": {k: v for k, v in meta.items() if k != "normalized"}}
            detail.update({"transcript_id": transcript_id, "content_sha256": expected_hash,
                           "unit_count": len(units), "characters": sum(len(u["text"]) for u in units),
                           "provenance": provenance})
            sources.append({"source_id": kind + ":" + owner_id + ":recording:" + recording_id,
                            "version": _hash(provenance), "kind": "interview", "units": units})
        _require(set(transcripts) <= used, "unexpected_primary_transcript")
        return entry
