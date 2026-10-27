"""Offline collector contract tests over synthetic Core DTOs and a temporary DB."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import quote


SERVER = Path(__file__).resolve().parents[3] / "services" / "recruiting-api"
sys.path.insert(0, str(SERVER))

from assessment_corpus import CandidateCorpusCollector, CollectionError, SQLiteResumeReader
from assessment_core.engine import prepare


def content_hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def conversation(kind, owner_id, parts):
    binding_id = owner_id + "-binding"
    materials = {
        "owner_kind": kind, "owner_id": owner_id,
        "sources": [{"id": binding_id, "binding_id": binding_id, "generation": 1,
                     "owner_kind": kind, "owner_id": owner_id, "revoked": False,
                     "provider": "synthetic", "connection_id": "synthetic-connection",
                     "source_ref": {"session": owner_id}, "updated_at": 10}],
        "channels": [{"binding_id": binding_id, "generation": 1, "acquisition_complete": True,
                      "video_status": "ready", "transcript_status": "ready",
                      "transcription_status": "completed", "updated_at": 10}],
        "assets": [], "transcript_selections": [], "transcript_selection_required_recording_ids": [],
    }
    primary = {"owner_kind": kind, "owner_id": owner_id, "status": "ready", "selections": [],
               "transcripts": [], "transcript_selection_required_recording_ids": []}
    for number, segments in enumerate(parts):
        recording_id = owner_id + "-recording-" + str(number)
        transcript_id = owner_id + "-transcript-" + str(number)
        transcript_asset_id = transcript_id + "-asset"
        selection = {"binding_id": binding_id, "generation": 1, "recording_id": recording_id,
                     "request_id": "synthetic-asr-" + recording_id, "revision": 2,
                     "status": "ready", "transcript_id": transcript_id, "error_code": None, "updated_at": 10}
        raw_ref = {"backend": "synthetic", "object_key": "PRIVATE-RAW-LOCATION/" + transcript_id,
                   "sha256": "b" * 64, "size": 100}
        text = "\n".join(segment["text"] for segment in segments)
        metadata = {"processing_status": "completed", "coverage": "full", "input_asset_id": recording_id,
                    "origin": "synthetic", "normalized": {"schema_version": 1,
                    "format": "canonical_json", "segments": deepcopy(segments), "text": text,
                    "full_text": text, "vtt": "NOT THE SOURCE TO PARSE", "warnings": []}}
        transcript = {"id": transcript_id, "asset_id": transcript_asset_id, "version": 1,
                      "raw_ref": raw_ref, "segments": deepcopy(segments), "metadata": metadata,
                      "content_sha256": content_hash({"raw_ref": raw_ref, "segments": segments, "metadata": metadata}),
                      "created_at": 10}
        materials["assets"].extend([
            {"id": recording_id, "source_id": binding_id, "kind": "video", "state": "ready", "updated_at": 10},
            {"id": transcript_asset_id, "source_id": binding_id, "kind": "transcript", "state": "ready", "updated_at": 10},
        ])
        materials["transcript_selections"].append(deepcopy(selection))
        materials["transcript_selection_required_recording_ids"].append(recording_id)
        primary["selections"].append(deepcopy(selection))
        primary["transcripts"].append(transcript)
        primary["transcript_selection_required_recording_ids"].append(recording_id)
    return {"id": owner_id, "status": "completed", "updated_at": 10, "materials": materials}, primary


def fixture():
    candidate_id = "candidate-α /1"
    repeated = {"text": "  Повтор 👩🏽‍💻 e\u0301\t\r\n", "speaker": "speaker_0", "start_ms": 0, "end_ms": 1200}
    first, p1 = conversation("interview", "interview-1", [
        [deepcopy(repeated), {"text": " \t\r\n", "speaker": "speaker_1", "start_ms": 1100, "end_ms": 1100},
         deepcopy(repeated), {"text": "", "speaker": None, "start_ms": None, "end_ms": None}],
        [{"text": "Вторая часть.\n", "speaker": "Unmapped Person", "start_ms": 100, "end_ms": 1500}],
    ])
    second, p2 = conversation("interview", "interview-2", [
        [{"text": " Другое интервью\u00a0", "start_ms": 0, "end_ms": 0}],
    ])
    meeting, pm = conversation("meeting", "employer-meeting", [
        [{"text": "  Разговор с работодателем.  ", "speaker": "speaker_99", "start_ms": 10, "end_ms": 20}],
    ])
    cv = {"candidate_id": candidate_id, "filename": "synthetic.pdf", "mime": "application/pdf",
          "uploaded_at": "2026-01-02T03:04:05Z", "text_content": " \tCV\r\nРазработчик 👩🏽‍💻\n\u00a0",
          "original_sha256": hashlib.sha256(b"SYNTHETIC ORIGINAL CV BYTES").hexdigest()}
    dossier = {"candidate_id": candidate_id, "resume": {k: cv[k] for k in ("filename", "mime", "uploaded_at")},
               "profile": {"summary": "FORBIDDEN PROFILE SUMMARY SUBSTITUTE", "skills": ["INFERRED SKILL"]},
               "interviews": [second, first], "meetings": [meeting], "updated_at": 10}
    return SimpleNamespace(candidate_id=candidate_id, dossier=dossier, cv=cv,
                           primaries={("interview", first["id"]): p1, ("interview", second["id"]): p2,
                                      ("meeting", meeting["id"]): pm})


def collector_for(data, *, authorize=None):
    core = SimpleNamespace(http=SimpleNamespace(call=Mock(return_value=None)), primary_transcripts=Mock())
    core.http.call.side_effect = lambda _method, _path: deepcopy(data.dossier)
    core.primary_transcripts.side_effect = lambda kind, owner_id: deepcopy(data.primaries[(kind, owner_id)])
    reader = Mock(side_effect=lambda _candidate_id: deepcopy(data.cv))
    authorization = authorize if authorize is not None else Mock(return_value=None)
    return CandidateCorpusCollector(core, reader, authorize=authorization), core, reader, authorization


def synthetic_rubric():
    return {"rubric_id": "offline-test-only", "version": "synthetic-v1", "parameters": [
        {"parameter_id": "parameter-" + str(index), "title": "Параметр " + str(index),
         "definition": "Синтетическая проверка полноты корпуса, не рабочая рубрика.", "levels": [
             {"level_id": "one", "label": "Один", "description": "Синтетическое описание один"},
             {"level_id": "two", "label": "Два", "description": "Синтетическое описание два"},
         ]} for index in range(15)
    ]}


class CollectorTests(unittest.TestCase):
    def test_live_conversation_blocks_corpus_even_with_all_materials_and_primary_ready(self):
        data = fixture()
        owner = next(item for item in data.dossier["interviews"] if item["id"] == "interview-1")
        owner["status"] = "live"
        self.assertEqual(data.primaries[("interview", "interview-1")]["status"], "ready")
        self.assertTrue(all(channel["acquisition_complete"] for channel in owner["materials"]["channels"]))
        self.assertTrue(all(selection["status"] == "ready" for selection in owner["materials"]["transcript_selections"]))
        collector, _, _, _ = collector_for(data)
        snapshot = collector.collect(data.candidate_id)
        self.assertEqual(snapshot["status"], "blocked")
        self.assertIsNone(snapshot["corpus"])
        self.assertIn({"code": "conversation_not_completed", "owner_kind": "interview", "owner_id": "interview-1"},
                      snapshot["manifest"]["issues"])

    def test_recording_parts_preserve_upstream_order_instead_of_sorting_opaque_ids(self):
        data = fixture()
        owner = next(item for item in data.dossier["interviews"] if item["id"] == "interview-1")
        materials = owner["materials"]
        primary = data.primaries[("interview", "interview-1")]
        identities = {"interview-1-recording-0": "z-part", "interview-1-recording-1": "a-part"}
        for asset in materials["assets"]:
            if asset["id"] in identities:
                asset["id"] = identities[asset["id"]]
        for selection in primary["selections"]:
            selection["recording_id"] = identities[selection["recording_id"]]
        for transcript in primary["transcripts"]:
            transcript["metadata"]["input_asset_id"] = identities[transcript["metadata"]["input_asset_id"]]
            transcript["content_sha256"] = content_hash({key: transcript[key] for key in ("raw_ref", "segments", "metadata")})
        primary["transcript_selection_required_recording_ids"] = ["z-part", "a-part"]
        materials["transcript_selections"] = deepcopy(primary["selections"])
        materials["transcript_selection_required_recording_ids"] = ["z-part", "a-part"]
        collector, _, _, _ = collector_for(data)
        snapshot = collector.collect(data.candidate_id)
        self.assertEqual(snapshot["status"], "ready")
        parts = [source for source in snapshot["corpus"]["sources"]
                 if source["source_id"].startswith("interview:interview-1:recording:")]
        self.assertEqual([source["source_id"].rsplit(":", 1)[1] for source in parts], ["z-part", "a-part"])
        self.assertEqual([unit["text"] for source in parts for unit in source["units"]],
                         [segment["text"] for transcript in primary["transcripts"] for segment in transcript["segments"]])

    def test_all_cv_interviews_parts_and_employer_meeting_are_preserved_exactly(self):
        data = fixture()
        original = deepcopy(data)
        collector, core, reader, authorize = collector_for(data)
        snapshot = collector.collect(data.candidate_id)
        self.assertEqual(snapshot["status"], "ready")
        sources = snapshot["corpus"]["sources"]
        self.assertEqual(len(sources), 5)  # one CV + four independently selected recording parts
        self.assertEqual(sources[0]["units"], [{"unit_id": "stored-text", "text": data.cv["text_content"], "role": "unknown"}])
        for source in sources[1:]:
            kind, rest = source["source_id"].split(":", 1)
            owner_id, recording_id = rest.split(":recording:")
            primary = data.primaries[(kind, owner_id)]
            selection = next(s for s in primary["selections"] if s["recording_id"] == recording_id)
            transcript = next(t for t in primary["transcripts"] if t["id"] == selection["transcript_id"])
            self.assertEqual(source["kind"], "interview")
            self.assertEqual(source["units"], [{"unit_id": "segment:" + str(i), "role": "unknown", **segment}
                                               for i, segment in enumerate(transcript["segments"])])
        multipart = next(s for s in sources if s["source_id"].endswith("interview-1-recording-0"))
        self.assertEqual(multipart["units"][0]["text"], multipart["units"][2]["text"])
        self.assertNotEqual(multipart["units"][0]["unit_id"], multipart["units"][2]["unit_id"])
        self.assertEqual([u["text"] for u in multipart["units"]][1::2], [" \t\r\n", ""])
        self.assertEqual(snapshot["manifest"]["unit_count"], 8)
        self.assertEqual(snapshot["manifest"]["source_count"], 5)
        self.assertEqual(snapshot["manifest"]["scope"], ["interviews", "meetings"])
        self.assertEqual(data.dossier, original.dossier)
        self.assertEqual(data.cv, original.cv)
        self.assertEqual(data.primaries, original.primaries)
        expected_path = "/v1/candidates/" + quote(data.candidate_id, safe="") + "/dossier"
        self.assertEqual(core.http.call.call_args_list, [unittest.mock.call("GET", expected_path)] * 2)
        self.assertEqual(core.primary_transcripts.call_count, 6)
        self.assertEqual(reader.call_count, 2)
        self.assertGreaterEqual(authorize.call_count, 3)

    def test_all_fifteen_questions_share_the_entire_unmodified_collected_state(self):
        data = fixture()
        collector, _, _, _ = collector_for(data)
        snapshot = collector.collect(data.candidate_id)
        with patch("assessment_core.providers.jev.JevClient.evaluate", side_effect=AssertionError("No model calls")) as model:
            plan = prepare(snapshot["corpus"], synthetic_rubric())
        model.assert_not_called()
        request = plan["request"]
        self.assertEqual(len(request["questions"]), 15)
        self.assertEqual(len(request["state"]["sources"]), 5)
        self.assertEqual(len(request["state"]["chunks"]), 8)
        expected_texts = [unit["text"] for source in snapshot["corpus"]["sources"] for unit in source["units"]]
        self.assertEqual([chunk["text"] for chunk in request["state"]["chunks"]], expected_texts)
        for question in request["questions"].values():
            self.assertEqual(set(question), {"type", "instructions", "criteria"})
            self.assertEqual(question["type"], "choice")
            self.assertEqual(set(question["criteria"]), {"one", "two", "insufficient_data"})
        serialized = json.dumps(request, ensure_ascii=False)
        self.assertNotIn("FORBIDDEN PROFILE SUMMARY", serialized)
        self.assertNotIn("INFERRED SKILL", serialized)
        self.assertNotIn("PRIVATE-RAW-LOCATION", serialized)
        self.assertNotIn("NOT THE SOURCE TO PARSE", serialized)

    def test_missing_or_pending_part_blocks_entire_corpus_without_fallback(self):
        for condition in ("missing", "processing", "unavailable"):
            data = fixture()
            primary = data.primaries[("interview", "interview-1")]
            owner = next(o for o in data.dossier["interviews"] if o["id"] == "interview-1")
            missing_transcript = primary["selections"][1]["transcript_id"]
            primary["transcripts"] = [t for t in primary["transcripts"] if t["id"] != missing_transcript]
            if condition == "missing":
                primary["selections"].pop()
            elif condition == "processing":
                primary["status"] = "processing"
                primary["selections"][1].update(status="processing", transcript_id=None)
            owner["materials"]["transcript_selections"] = deepcopy(primary["selections"])
            collector, _, _, _ = collector_for(data)
            with self.subTest(condition=condition):
                snapshot = collector.collect(data.candidate_id)
                self.assertEqual(snapshot["status"], "blocked")
                self.assertIsNone(snapshot["corpus"])
                codes = {issue["code"] for issue in snapshot["manifest"]["issues"]}
                expected = {"missing": "primary_missing_recording", "processing": "recording_transcript_not_ready",
                            "unavailable": "selected_transcript_unavailable"}[condition]
                self.assertIn(expected, codes)
                self.assertEqual(snapshot["manifest"]["source_count"], 4)

    def test_cv_revision_race_is_rejected_without_using_smaller_or_newer_corpus(self):
        data = fixture()
        collector, _, reader, _ = collector_for(data)
        newer = deepcopy(data.cv)
        newer["text_content"] += " revised text"
        reader.side_effect = [deepcopy(data.cv), newer]
        with self.assertRaisesRegex(CollectionError, "sources_changed_during_collection"):
            collector.collect(data.candidate_id)
        collector, _, reader, _ = collector_for(data)
        newer["uploaded_at"] = "2026-01-03T00:00:00Z"
        reader.side_effect = [newer]
        with self.assertRaisesRegex(CollectionError, "resume_changed_during_collection"):
            collector.collect(data.candidate_id)

    def test_selection_revision_race_between_reads_is_rejected(self):
        data = fixture()
        collector, core, _, _ = collector_for(data)
        calls = 0

        def dossier_read(_method, _path):
            nonlocal calls
            calls += 1
            if calls == 2:
                primary = data.primaries[("interview", "interview-1")]
                primary["selections"][0]["revision"] += 1
                owner = next(o for o in data.dossier["interviews"] if o["id"] == "interview-1")
                owner["materials"]["transcript_selections"] = deepcopy(primary["selections"])
            return deepcopy(data.dossier)

        core.http.call.side_effect = dossier_read
        with self.assertRaisesRegex(CollectionError, "sources_changed_during_collection"):
            collector.collect(data.candidate_id)
        data = fixture()
        data.primaries[("interview", "interview-1")]["selections"][0]["revision"] += 1
        collector, _, _, _ = collector_for(data)
        with self.assertRaisesRegex(CollectionError, "primary_changed_during_collection"):
            collector.collect(data.candidate_id)

    def test_cross_candidate_denial_occurs_before_any_cv_or_core_read(self):
        data = fixture()
        collector, core, reader, authorize = collector_for(data, authorize=Mock(return_value=False))
        with self.assertRaisesRegex(CollectionError, "candidate_access_denied"):
            collector.collect("another-candidate")
        core.http.call.assert_not_called()
        core.primary_transcripts.assert_not_called()
        reader.assert_not_called()
        authorize.assert_called_once_with("another-candidate")
        collector, core, reader, _ = collector_for(data, authorize=Mock(side_effect=PermissionError("denied")))
        with self.assertRaises(PermissionError):
            collector.collect(data.candidate_id)
        core.http.call.assert_not_called()
        reader.assert_not_called()

    def test_dossier_or_cv_candidate_mismatch_is_rejected(self):
        for target in ("dossier", "cv"):
            data = fixture()
            getattr(data, target)["candidate_id"] = "another-candidate"
            collector, _, reader, _ = collector_for(data)
            with self.subTest(target=target), self.assertRaisesRegex(CollectionError, "candidate.*mismatch"):
                collector.collect(data.candidate_id)
            if target == "dossier":
                reader.assert_not_called()

    def test_revoked_binding_blocks_corpus_and_never_reads_its_cached_primary(self):
        data = fixture()
        revoked_owner = next(o for o in data.dossier["interviews"] if o["id"] == "interview-1")
        revoked_owner["materials"]["sources"][0]["revoked"] = True
        collector, core, _, _ = collector_for(data)
        snapshot = collector.collect(data.candidate_id)
        self.assertIsNone(snapshot["corpus"])
        self.assertEqual(snapshot["status"], "blocked")
        self.assertIn("binding_revoked", {i["code"] for i in snapshot["manifest"]["issues"]})
        self.assertNotIn(unittest.mock.call("interview", "interview-1"), core.primary_transcripts.call_args_list)

    def test_authorization_revocation_between_passes_stops_further_reads(self):
        data = fixture()
        collector, core, reader, _ = collector_for(data, authorize=Mock(side_effect=[None, False]))
        with self.assertRaisesRegex(CollectionError, "candidate_access_denied"):
            collector.collect(data.candidate_id)
        self.assertEqual(core.http.call.call_count, 1)
        self.assertEqual(reader.call_count, 1)

    def test_authorization_revoked_during_final_cv_read_is_checked_before_return(self):
        data = fixture()
        allowed = True
        authorize = Mock(side_effect=lambda _candidate_id: None if allowed else False)
        collector, core, reader, _ = collector_for(data, authorize=authorize)
        reads = 0

        def read_resume(_candidate_id):
            nonlocal reads, allowed
            reads += 1
            if reads == 2:
                allowed = False
            return deepcopy(data.cv)

        reader.side_effect = read_resume
        with self.assertRaisesRegex(CollectionError, "candidate_access_denied"):
            collector.collect(data.candidate_id)
        self.assertEqual(core.http.call.call_count, 2)
        self.assertEqual(reader.call_count, 2)
        self.assertGreaterEqual(authorize.call_count, 3)

    def test_unverified_actual_asr_coverage_warns_and_declared_partial_blocks(self):
        for coverage in ("not_independently_verified", "partial", "truncated", "incomplete"):
            data = fixture()
            transcript = data.primaries[("interview", "interview-1")]["transcripts"][0]
            transcript["metadata"]["coverage"] = coverage
            transcript["content_sha256"] = content_hash({key: transcript[key] for key in ("raw_ref", "segments", "metadata")})
            collector, _, _, _ = collector_for(data)
            with self.subTest(coverage=coverage):
                snapshot = collector.collect(data.candidate_id)
                if coverage == "not_independently_verified":
                    self.assertEqual(snapshot["status"], "ready")
                    self.assertIsNotNone(snapshot["corpus"])
                    warnings = [warning for conversation in snapshot["manifest"]["conversations"]
                                for warning in conversation["warnings"]]
                    self.assertTrue(warnings)
                    self.assertIn("speech_coverage_unverified", {warning["code"] for warning in warnings})
                    self.assertFalse(snapshot["manifest"]["issues"])
                    self.assertIn(coverage, json.dumps(snapshot["manifest"], ensure_ascii=False))
                else:
                    self.assertEqual(snapshot["status"], "blocked")
                    self.assertIsNone(snapshot["corpus"])
                    self.assertIn("transcript_coverage_not_full", {i["code"] for i in snapshot["manifest"]["issues"]})

    def test_revalidate_detects_cv_selection_or_revocation_change(self):
        for mutation in ("cv", "selection", "revocation"):
            data = fixture()
            collector, _, _, _ = collector_for(data)
            snapshot = collector.collect(data.candidate_id)
            if mutation == "cv":
                data.cv["original_sha256"] = "f" * 64
            elif mutation == "selection":
                primary = data.primaries[("interview", "interview-1")]
                primary["selections"][0]["revision"] += 1
                owner = next(o for o in data.dossier["interviews"] if o["id"] == "interview-1")
                owner["materials"]["transcript_selections"] = deepcopy(primary["selections"])
            else:
                data.dossier["meetings"][0]["materials"]["sources"][0]["revoked"] = True
            with self.subTest(mutation=mutation), self.assertRaisesRegex(CollectionError, "sources_changed_since_collection"):
                collector.revalidate(snapshot)

    def test_updated_at_polling_and_profile_noise_does_not_change_revision(self):
        data = fixture()
        collector, _, _, _ = collector_for(data)
        snapshot = collector.collect(data.candidate_id)
        data.dossier["updated_at"] = 999
        data.dossier["profile"]["summary"] = "A CHANGED PROFILE SUMMARY"
        for group in ("interviews", "meetings"):
            for owner in data.dossier[group]:
                owner["updated_at"] = 999
                for collection in ("sources", "assets", "channels", "transcript_selections"):
                    for value in owner["materials"][collection]:
                        value["updated_at"] = 999
        for primary in data.primaries.values():
            for selection in primary["selections"]:
                selection["updated_at"] = 999
            for transcript in primary["transcripts"]:
                transcript["created_at"] = 999
        current = collector.revalidate(snapshot)
        self.assertEqual(current["revision"], snapshot["revision"])
        self.assertEqual(current["corpus"], snapshot["corpus"])

    def test_no_resume_uses_transcripts_and_never_profile_summary(self):
        data = fixture()
        data.cv = None
        data.dossier["resume"] = None
        collector, _, _, _ = collector_for(data)
        snapshot = collector.collect(data.candidate_id)
        self.assertEqual(snapshot["status"], "ready")
        self.assertEqual(snapshot["manifest"]["resume"], {"status": "absent"})
        self.assertEqual(len(snapshot["corpus"]["sources"]), 4)
        self.assertNotIn("PROFILE SUMMARY", json.dumps(snapshot["corpus"]))
        data.dossier["interviews"] = []
        data.dossier["meetings"] = []
        empty = collector.collect(data.candidate_id)
        self.assertEqual(empty["status"], "blocked")
        self.assertIsNone(empty["corpus"])
        self.assertIn({"code": "no_source_text"}, empty["manifest"]["issues"])


class SQLiteResumeReaderTests(unittest.TestCase):
    def test_fixed_readonly_database_returns_exact_text_hash_without_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "HR candidate ?# db.sqlite3"
            text, raw = " \tStored CV\r\nЮникод 👋 e\u0301\n", b"SYNTHETIC ORIGINAL BYTES"
            with sqlite3.connect(str(path)) as db:
                db.execute("CREATE TABLE cp_resumes (candidate_id TEXT,filename TEXT,mime TEXT,text_content TEXT,content BLOB,uploaded_at TEXT)")
                db.execute("INSERT INTO cp_resumes VALUES (?,?,?,?,?,?)", ("candidate-1", "cv.pdf", "application/pdf", text, raw, "v1"))
            before = path.read_bytes()
            reader = SQLiteResumeReader(path)
            real_connect = sqlite3.connect

            def readonly_connect(*args, **kwargs):
                connection = real_connect(*args, **kwargs)
                with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                    connection.execute("CREATE TABLE forbidden_write (value TEXT)")
                return connection

            with patch("assessment_corpus.sqlite3.connect", side_effect=readonly_connect) as connect:
                result = reader("candidate-1")
                self.assertIsNone(reader("candidate-1' OR 1=1 --"))
            self.assertEqual(connect.call_args.args[0], path.as_uri() + "?mode=ro")
            self.assertTrue(connect.call_args.kwargs["uri"])
            self.assertEqual(result, {"candidate_id": "candidate-1", "filename": "cv.pdf", "mime": "application/pdf",
                                      "text_content": text, "uploaded_at": "v1",
                                      "original_sha256": hashlib.sha256(raw).hexdigest()})
            self.assertNotIn("content", result)
            self.assertEqual(path.read_bytes(), before)

    def test_relative_or_missing_database_path_is_rejected_without_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            absent = Path(directory) / "does-not-exist.sqlite3"
            for path in (Path("relative.sqlite3"), absent, Path(directory)):
                with self.subTest(path=str(path)), self.assertRaisesRegex(CollectionError, "existing_hr_database_required"):
                    SQLiteResumeReader(path)
            self.assertFalse(absent.exists())


if __name__ == "__main__":
    unittest.main()
