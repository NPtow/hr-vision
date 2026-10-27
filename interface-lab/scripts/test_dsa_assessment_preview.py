"""Synthetic contract tests; no network and no real candidate data."""
import importlib.util
from pathlib import Path
import unittest
import copy

SPEC = importlib.util.spec_from_file_location("preview", Path(__file__).with_name("dsa_assessment_preview.py"))
preview = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preview)


def make_turn(index, text="Asked the customer to confirm the next step.", speaker="candidate"):
    return {"id": f"dsa-test-u{index:04d}", "index": index, "sourceId": f"source-{index}", "text": text, "speaker": speaker, "startSec": index * 2, "endSec": index * 2 + 1, "playbackStartSec": index * 2, "playbackEndSec": index * 2 + 1}


def example_unit(ep, index, cls="narrated"):
    return {"episodeId": ep, "sourceId": f"dsa-test-u{index:04d}", "turns": [make_turn(index)], "classAnswer": {}, "sourceAnswer": {"type": "choice", "choice": f"dsa-test-u{index:04d}"}}


CRITERIA = {"A01": {"name": "Диалог"}}
ANCHORS = {"A01": {"anchor_status": "draft", "levels": {str(i): {"actions": [{"action_id": f"a{i}", "text_ru": f"Action {i}"}]} for i in range(1, 6)}, "concern_observables": [{"action_id": "concern", "text_ru": "Explicit concerning action"}]}}


def answers(ep, probs, action=4, support=.95, relevance=.95, concern=.01):
    a = {f"{ep}-class": {"choice": max(probs, key=probs.get), "probabilities": probs, "confidence": .2}, f"{ep}-relevance": {"noul": relevance}}
    a.update({f"{ep}|a{i}": {"noul": support if i == action else .01} for i in range(1, 6)})
    a[f"{ep}|concern"] = {"noul": concern}
    return a


class PolicyContract(unittest.TestCase):
    def test_uncertain_observed_argmax_cannot_obtain_level_four(self):
        a = answers("e1", {"observed": .36, "narrated": .33, "partial_narrative": .26, "generic": .05})
        result = preview.criterion_result("test", "A01", [example_unit("e1", 1)], {"model": "synthetic", "answers": a}, CRITERIA, ANCHORS)
        self.assertEqual(result["level"], 2)
        self.assertEqual(result["methodDetails"]["units"][0]["rawLevel"], 4)

    def test_generic_quote_is_readable_but_cannot_set_level(self):
        a = answers("e1", {"generic": .99, "narrated": .01}, relevance=.6)
        result = preview.criterion_result("test", "A01", [example_unit("e1", 1)], {"model": "synthetic", "answers": a}, CRITERIA, ANCHORS)
        self.assertIsNone(result["level"])
        self.assertEqual(len(result["evidence"]), 1)
        self.assertEqual(result["evidence"][0]["text"], make_turn(1)["text"])

    def test_missing_action_is_not_level_one(self):
        a = answers("e1", {"narrated": .99, "generic": .01}, support=.2)
        result = preview.criterion_result("test", "A01", [example_unit("e1", 1)], {"model": "synthetic", "answers": a}, CRITERIA, ANCHORS)
        self.assertIsNone(result["level"])

    def test_explicit_concern_and_positive_episode_are_not_averaged(self):
        a = answers("e1", {"observed": .99, "generic": .01}, concern=.95)
        a.update(answers("e2", {"observed": .99, "generic": .01}))
        result = preview.criterion_result("test", "A01", [example_unit("e1", 1), example_unit("e2", 10)], {"model": "synthetic", "answers": a}, CRITERIA, ANCHORS)
        self.assertEqual(result["state"], "conflict")
        self.assertIsNone(result["level"])
        self.assertEqual(len(result["evidence"]), 2)

    def test_no_anchors_means_quotes_only(self):
        a = answers("e1", {"narrated": .99, "generic": .01})
        result = preview.criterion_result("test", "A01", [example_unit("e1", 1)], {"model": "synthetic", "answers": a}, CRITERIA, {})
        self.assertIsNone(result["level"])
        self.assertEqual(len(result["evidence"]), 1)

    def test_episodes_cover_all_turns_and_do_not_quote_recruiter(self):
        turns = [make_turn(1), make_turn(2), make_turn(3, "Here is my suggestion", "recruiter"), make_turn(31), make_turn(32)]
        episodes = preview.make_episodes(turns, "Synthetic Example")
        self.assertEqual([t["index"] for e in episodes for t in e["turns"]], [t["index"] for t in turns])
        for e in episodes:
            for source in e["sources"].values():
                self.assertTrue(all(t["speaker"] == "candidate" for t in source))
                self.assertNotIn(3, [t["index"] for t in source])

    def test_contact_and_personal_sentences_removed_without_losing_business(self):
        text = "У меня ребенок и нужен садик. Я договорился о поставке. test@example.com"
        result = preview.redact(text, [])
        self.assertNotIn("ребенок", result)
        self.assertNotIn("example.com", result)
        self.assertIn("Я договорился о поставке.", result)

    def test_health_reference_masked_without_masking_equipment_temperature(self):
        result = preview.redact("У меня немного температура сейчас повышена. Рабочая температура оборудования — 80 градусов.", [])
        self.assertNotIn("температура сейчас повышена", result)
        self.assertIn("температура оборудования", result)


class SourceReviewContract(unittest.TestCase):
    def setUp(self):
        self.turns = [make_turn(1, "First professional sentence."), make_turn(2, "Continuation from the same candidate."), make_turn(3, "Final professional sentence.")]
        evidence = preview.make_evidence("test", "A01", {"sourceId": self.turns[0]["id"], "turns": self.turns[:1]})
        self.payload = {"candidates": [{"id": "test", "provenance": {}, "assessment": {"criteria": [{"id": "A01", "level": None, "methodDetails": {"units": [{"sourceId": evidence["sourceId"], "turnIds": [1], "actionProbabilities": {"action": .7}}]}, "evidence": [evidence]}]}}]}
        self.operation = {"candidateId": "test", "sourceId": evidence["sourceId"], "skills": ["A01"], "reason": "Readability review", "turnRange": [1, 3]}

    def test_expansion_is_verbatim_and_keeps_evaluation_scope(self):
        original = copy.deepcopy(self.payload)
        result, audit = preview.apply_review_overrides(self.payload, {"test": self.turns}, {"schemaVersion": 1, "contexts": [self.operation]})
        criterion = result["candidates"][0]["assessment"]["criteria"][0]
        evidence = criterion["evidence"][0]
        self.assertEqual(evidence["text"], " ".join(t["text"] for t in self.turns))
        self.assertEqual(evidence["turnIds"], [1, 2, 3])
        self.assertEqual(evidence["evaluationTurnIds"], [1])
        self.assertTrue(evidence["reviewContext"])
        self.assertEqual(criterion["methodDetails"], original["candidates"][0]["assessment"]["criteria"][0]["methodDetails"])
        self.assertEqual(self.payload, original)
        self.assertEqual(audit["operations"][0]["originalEvidence"]["turnIds"], [1])

    def test_trim_resolves_source_id_and_time_from_first_remaining_turn(self):
        self.operation.update({"turnRange": [2, 3], "replacementSourceId": self.turns[1]["id"]})
        result, _ = preview.apply_review_overrides(self.payload, {"test": self.turns}, {"schemaVersion": 1, "contexts": [self.operation]})
        evidence = result["candidates"][0]["assessment"]["criteria"][0]["evidence"][0]
        self.assertEqual(evidence["sourceId"], self.turns[1]["id"])
        self.assertEqual(evidence["seconds"], self.turns[1]["playbackStartSec"])
        self.assertEqual(evidence["evaluationSourceId"], self.turns[0]["id"])

    def test_missing_binding_is_a_hard_failure(self):
        self.operation["sourceId"] = "dsa-test-u9999"
        with self.assertRaises(ValueError):
            preview.apply_review_overrides(self.payload, {"test": self.turns}, {"schemaVersion": 1, "contexts": [self.operation]})

    def test_recruiter_or_other_candidate_cannot_be_added(self):
        for change in ({"speaker": "recruiter"}, {"id": "dsa-other-u0002"}):
            turns = copy.deepcopy(self.turns)
            turns[1].update(change)
            with self.assertRaises(ValueError):
                preview.apply_review_overrides(self.payload, {"test": turns}, {"schemaVersion": 1, "contexts": [self.operation]})

    def test_exclusion_keeps_original_quote_in_audit_not_view(self):
        operation = {k: v for k, v in self.operation.items() if k != "turnRange"}
        result, audit = preview.apply_review_overrides(self.payload, {"test": self.turns}, {"schemaVersion": 1, "exclusions": [operation]})
        criterion = result["candidates"][0]["assessment"]["criteria"][0]
        self.assertEqual(criterion["evidence"], [])
        self.assertEqual(audit["operations"][0]["originalEvidence"]["text"], self.turns[0]["text"])
        self.assertEqual(len(criterion["methodDetails"]["units"]), 1)

    def test_reading_review_cannot_remove_scored_evidence(self):
        self.payload["candidates"][0]["assessment"]["criteria"][0]["level"] = 3
        with self.assertRaises(ValueError):
            preview.apply_review_overrides(self.payload, {"test": self.turns}, {"schemaVersion": 1, "exclusions": [self.operation]})


if __name__ == "__main__":
    unittest.main()
