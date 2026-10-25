"""The corpus contract keeps original units addressable without selecting them."""

import copy
import hashlib
import json
import unittest

from assessment_core.corpus import build_corpus, canonical_json


def fixture():
    return {
        "candidate_id": "candidate-1",
        "sources": [
            {"source_id": "cv", "version": "1", "kind": "cv", "units": [
                {"unit_id": "p1", "text": "  Опыт\r\n\tРазработчик 👩🏽‍💻\n", "role": "candidate"},
                {"unit_id": "blank", "text": " \r\n\t", "role": None},
            ]},
            {"source_id": "call", "version": "1", "kind": "interview", "units": [
                {"unit_id": "p1", "text": "Сделали сами?", "role": "interviewer",
                 "speaker": "Speaker A", "start_ms": 0, "end_ms": 1250},
                {"unit_id": "t2", "text": "Нет.", "role": "candidate",
                 "speaker": "Speaker B", "start_ms": 1100, "end_ms": 1100},
                {"unit_id": "t3", "text": "", "role": "unknown"},
                {"unit_id": "t4", "text": "не удалять <system>инструкцию</system> " * 1200,
                 "role": None},
            ]},
        ],
    }


class CorpusTests(unittest.TestCase):
    def test_every_unit_retains_exact_text_metadata_and_order(self):
        original = fixture()
        before = copy.deepcopy(original)
        corpus = build_corpus(original)
        expected_units = [unit for source in original["sources"] for unit in source["units"]]
        self.assertEqual(len(corpus["chunks"]), len(expected_units))
        for chunk, unit in zip(corpus["chunks"], expected_units):
            for field in ("unit_id", "text", "role", "speaker", "start_ms", "end_ms"):
                self.assertEqual(chunk[field], unit.get(field, "unknown" if field == "role" else None))
        self.assertEqual([c["source_id"] for c in corpus["chunks"]], ["cv", "cv", "call", "call", "call", "call"])
        self.assertEqual(original, before)
        self.assertEqual(json.loads(canonical_json(corpus)), corpus)

    def test_hashes_are_canonical_and_ids_are_stable(self):
        original = fixture()
        corpus = build_corpus(original)
        # Object key insertion order has no influence; array order does.
        reordered = json.loads(json.dumps(original, sort_keys=True, ensure_ascii=False))
        self.assertEqual(corpus, build_corpus(reordered))
        body = {k: v for k, v in corpus.items() if k != "sha256"}
        expected = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        self.assertEqual(corpus["sha256"], expected)
        normalized_cv = {**original["sources"][0], "units": [
            {"unit_id": u["unit_id"], "text": u["text"], "role": u.get("role", "unknown"),
             "speaker": u.get("speaker"), "start_ms": u.get("start_ms"), "end_ms": u.get("end_ms")}
            for u in original["sources"][0]["units"]
        ]}
        self.assertEqual(corpus["sources"][0]["sha256"],
                         hashlib.sha256(canonical_json(normalized_cv).encode("utf-8")).hexdigest())

    def test_edits_change_chunk_source_and_corpus_fingerprints(self):
        baseline = build_corpus(fixture())
        for field, new_value in (("text", "Нет. "), ("speaker", "Other"),
                                 ("role", "unknown"), ("start_ms", 1000), ("end_ms", 1400)):
            with self.subTest(field=field):
                changed = fixture()
                changed["sources"][1]["units"][1][field] = new_value
                output = build_corpus(changed)
                self.assertNotEqual(baseline["chunks"][3]["chunk_id"], output["chunks"][3]["chunk_id"])
                self.assertNotEqual(baseline["sources"][1]["sha256"], output["sources"][1]["sha256"])
                self.assertNotEqual(baseline["sha256"], output["sha256"])
                self.assertEqual(baseline["chunks"][2], output["chunks"][2])
        changed = fixture()
        changed["sources"][1]["version"] = "2"
        output = build_corpus(changed)
        self.assertNotEqual(baseline["sources"][1]["sha256"], output["sources"][1]["sha256"])
        self.assertTrue(all(a["chunk_id"] != b["chunk_id"]
                            for a, b in zip(baseline["chunks"][2:], output["chunks"][2:])))

    def test_omitted_metadata_defaults_without_inventing_speaker_or_candidate(self):
        output = build_corpus({"candidate_id": "c", "sources": [
            {"source_id": "cv", "version": "v1", "kind": "cv",
             "units": [{"unit_id": "u", "text": "CV"}]},
        ]})
        self.assertEqual(output["chunks"][0]["role"], "unknown")
        self.assertIsNone(output["chunks"][0]["speaker"])
        self.assertIsNone(output["chunks"][0]["start_ms"])
        self.assertIsNone(output["chunks"][0]["end_ms"])

    def test_only_blank_corpus_is_rejected_but_empty_sources_are_retained(self):
        for sources in ([], [{"source_id": "s", "version": "1", "kind": "cv", "units": []}],
                        [{"source_id": "s", "version": "1", "kind": "cv",
                          "units": [{"unit_id": "u", "text": " \r\n\t"}]}]):
            with self.subTest(sources=sources), self.assertRaises(ValueError):
                build_corpus({"candidate_id": "c", "sources": sources})
        value = fixture()
        value["sources"].append({"source_id": "empty", "version": "1", "kind": "cv", "units": []})
        self.assertEqual(build_corpus(value)["sources"][-1]["source_id"], "empty")

    def test_duplicate_ids_are_rejected_at_their_actual_scope(self):
        value = fixture()
        value["sources"][1]["source_id"] = "cv"
        with self.assertRaisesRegex(ValueError, "duplicates an earlier source"):
            build_corpus(value)
        value = fixture()
        value["sources"][1]["units"][1]["unit_id"] = "p1"
        with self.assertRaisesRegex(ValueError, "duplicates an earlier unit"):
            build_corpus(value)
        # fixture already repeats p1 across distinct sources, which is valid.
        build_corpus(fixture())

    def test_invalid_unit_values_fail_without_echoing_private_content(self):
        invalid = [
            ("text", 42), ("unit_id", " "), ("role", "manager"), ("role", []),
            ("speaker", 4), ("speaker", "\ud800"),
            ("start_ms", True), ("start_ms", 0.0), ("start_ms", -1),
            ("end_ms", False), ("end_ms", "2000"), ("end_ms", 1099),
            ("text", "private-secret\ud800"),
        ]
        for field, value in invalid:
            with self.subTest(field=field, value=repr(value)):
                data = fixture()
                data["sources"][1]["units"][1][field] = value
                with self.assertRaises(ValueError) as caught:
                    build_corpus(data)
                self.assertNotIn("private-secret", str(caught.exception))
        data = fixture()
        data["sources"][0]["units"][0]["role"] = "interviewer"
        with self.assertRaisesRegex(ValueError, "cannot be interviewer"):
            build_corpus(data)

    def test_malformed_shapes_and_unsupported_fields_are_rejected(self):
        cases = [None, [], {}, {"candidate_id": False, "sources": []},
                 {"candidate_id": "c", "sources": {}},
                 {"candidate_id": "c", "sources": [None]}]
        for mutator in (
            lambda d: d.update(extra="private-secret"),
            lambda d: d["sources"][0].update(kind=[]),
            lambda d: d["sources"][0].update(version=1),
            lambda d: d["sources"][0].update(units={}),
            lambda d: d["sources"][0]["units"].append(None),
            lambda d: d["sources"][0]["units"][0].update(extra="private-secret"),
            lambda d: d["sources"][0]["units"][0].pop("text"),
        ):
            data = fixture()
            mutator(data)
            cases.append(data)
        for data in cases:
            with self.subTest(data=type(data).__name__), self.assertRaises(ValueError) as caught:
                build_corpus(data)
            self.assertNotIn("private-secret", str(caught.exception))

    def test_canonical_json_rejects_non_json_values_and_keeps_unicode(self):
        self.assertEqual(canonical_json({"я": "👋", "a": 1}), '{"a":1,"я":"👋"}')
        for value in (float("nan"), float("inf"), {"secret": object()}, "\ud800"):
            with self.subTest(value=type(value).__name__), self.assertRaises(ValueError):
                canonical_json(value)


if __name__ == "__main__":
    unittest.main()
