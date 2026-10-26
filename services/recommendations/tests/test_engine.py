from copy import deepcopy
import math
from pathlib import Path
import json
import unittest

from recommendations.engine import UNKNOWN, InvalidInput, default_rubric, digest, recommend
from fixtures import request_fixture, synthetic_rubric, set_distribution, set_importance


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.rubric = synthetic_rubric()
        self.value = request_fixture(self.rubric)

    def run_result(self, value=None):
        return recommend(self.value if value is None else value, rubric=self.rubric)

    def test_formula_applies_tanh_per_level_and_preserves_input(self):
        before = deepcopy(self.value)
        result = self.run_result()
        item = result["items"][0]
        expected = 0.1 * math.tanh(1)
        self.assertAlmostEqual(item["score"], 0.07615941559557649, places=14)
        self.assertAlmostEqual(item["score"], expected, places=14)
        self.assertNotAlmostEqual(item["score"], math.tanh(0.1), places=3)
        p = item["parameters"][0]
        self.assertEqual(p["weight"], 1)
        self.assertEqual(p["required_position"], 3)
        self.assertAlmostEqual(sum(l["contribution"] for l in p["levels"]), expected)
        self.assertEqual(self.value, before)
        self.assertEqual(result, self.run_result(deepcopy(self.value)))
        self.assertEqual(result["pool"], {"size": 1, "candidate_ids": ["candidate-1"]})

    def test_conditional_numeric_mass_excludes_unknown_without_overwriting_raw(self):
        for parameter in self.value["candidate_profiles"][0]["parameters"]:
            set_distribution(parameter, [0, 0.16, 0.4, 0.24, 0], unknown=0.2)
        result = self.run_result()["items"][0]
        self.assertAlmostEqual(result["score"], 0.1 * math.tanh(1))
        p = result["parameters"][0]
        self.assertAlmostEqual(p["numeric_probability_mass"], 0.8)
        self.assertEqual(p["levels"][1]["p_raw"], 0.16)
        self.assertAlmostEqual(p["levels"][1]["q"], 0.2)
        before = self.run_result()
        for parameter in self.value["candidate_profiles"][0]["parameters"]:
            parameter["model_confidence"] = 0
        self.assertEqual(before["items"][0]["score"], self.run_result()["items"][0]["score"])

    def test_weight_and_insufficient_exclusion_change_denominator(self):
        set_importance(self.value["vacancy_profile"], 0, 3)
        set_distribution(self.value["candidate_profiles"][0]["parameters"][0], [0, 0, 0, 1, 0], selected="L4")
        set_distribution(self.value["candidate_profiles"][0]["parameters"][1], [0, 0, 0.2, 0, 0], unknown=0.8, selected=None)
        item = self.run_result()["items"][0]
        self.assertAlmostEqual(item["score"], math.tanh(1))
        self.assertEqual(item["coverage"], {"total_parameters": 2, "included_parameters": 1,
                                           "excluded_parameters": 1, "included_weight": 3,
                                           "excluded_weight": 1, "total_weight": 4})
        self.assertEqual(item["parameters"][1]["exclusion_reason"], UNKNOWN)
        self.assertIsNone(item["parameters"][1]["levels"][2]["q"])
        set_distribution(self.value["candidate_profiles"][0]["parameters"][1], [0, 1, 0, 0, 0], selected="L2")
        self.assertAlmostEqual(self.run_result()["items"][0]["score"], math.tanh(1) / 2)

    def test_all_excluded_is_null_unscored_and_never_fake_zero(self):
        set_importance(self.value["vacancy_profile"], 0, 0, required=None)
        set_distribution(self.value["candidate_profiles"][0]["parameters"][1], [0, 0, 0, 0, 0], unknown=1, selected=None)
        item = self.run_result()["items"][0]
        self.assertEqual(item["status"], "unscored")
        self.assertIsNone(item["score"])
        self.assertIsNone(item["rank"])
        self.assertEqual(item["coverage"]["included_weight"], 0)

    def test_missing_vacancy_skills_require_clarification_instead_of_partial_matching(self):
        for importance, required in ((UNKNOWN, "L3"), (2, None)):
            value = deepcopy(self.value)
            set_importance(value["vacancy_profile"], 1, importance, required=required)
            with self.subTest(importance=importance), self.assertRaisesRegex(InvalidInput, "clarification_required"):
                self.run_result(value)

    def test_manual_revision_preserves_raw_answers_and_uses_corrected_requirement(self):
        self.value["vacancy_profile"]["revision"] = {"author": "hr-1", "parent_profile_id": "a" * 64}
        self.value["vacancy_profile"]["profile_id"] = "d" * 64
        self.value["vacancy_profile"]["parameters"][0]["required_level_id"] = "L2"
        before = deepcopy(self.value)
        result = self.run_result()
        self.assertEqual(result["items"][0]["parameters"][0]["required_position"], 2)
        self.assertEqual(self.value, before)

    def test_fields_require_exact_ids_and_months_unknown_is_visible(self):
        fields = self.value["candidate_fields"][0]
        fields["work_locations"] = ["Moscow"]
        fields["industry_months"] = {"manufacturing": 11, "all_industries": 200}
        item = self.run_result()["items"][0]
        self.assertEqual((item["eligibility"], item["status"], item["rank"]), ("fail", "filtered", None))
        self.assertEqual([c["status"] for c in item["required_checks"]], ["fail", "pass", "fail"])
        fields["work_locations"] = None
        fields["industry_months"] = {"unrelated": 200}
        item = self.run_result()["items"][0]
        self.assertEqual((item["eligibility"], item["status"]), ("unknown", "scored"))
        self.assertTrue(item["requires_human_approval"])
        self.assertEqual(item["required_checks"][2]["reason"], "candidate_unknown")
        for requirement in self.value["vacancy_fields"].values():
            if type(requirement) is dict:
                requirement["required"] = False
        self.assertEqual([c["reason"] for c in self.run_result()["items"][0]["required_checks"]], ["not_required"] * 3)

    def test_unknown_requirement_does_not_turn_true_and_empty_candidate_values_are_known(self):
        self.value["vacancy_fields"]["location"]["required"] = None
        self.value["candidate_fields"][0]["work_locations"] = []
        item = self.run_result()["items"][0]
        self.assertEqual(item["required_checks"][0]["status"], "unknown")
        self.value["vacancy_fields"]["location"]["required"] = True
        self.assertEqual(self.run_result()["items"][0]["required_checks"][0]["status"], "fail")
        self.value["vacancy_fields"]["location"]["allowed_ids"] = None
        self.assertEqual(self.run_result()["items"][0]["required_checks"][0]["reason"], "requirement_unknown")

    def test_ranking_is_stable_including_negative_zero_unknown_and_unscored(self):
        value = request_fixture(self.rubric, count=6)
        # 1/2 tie, 3 negative, 4 all excluded, 5 zero, 6 filtered high score.
        for index, profile in enumerate(value["candidate_profiles"]):
            for p in profile["parameters"]:
                if index == 2:
                    set_distribution(p, [0, 1, 0, 0, 0], selected="L2")
                elif index == 3:
                    set_distribution(p, [0, 0, 0, 0, 0], unknown=1, selected=None)
                elif index == 4:
                    set_distribution(p, [0, 0, 1, 0, 0], selected="L3")
                elif index == 5:
                    set_distribution(p, [0, 0, 0, 0, 1], selected="L5")
        value["candidate_fields"][1]["work_locations"] = None
        value["candidate_fields"][5]["work_locations"] = ["other"]
        value["candidate_profiles"].reverse()
        result = self.run_result(value)["items"]
        self.assertEqual([i["candidate_id"] for i in result], ["candidate-1", "candidate-2", "candidate-5", "candidate-3", "candidate-4", "candidate-6"])
        self.assertEqual([i["rank"] for i in result], [1, 2, 3, 4, None, None])

    def test_strict_probability_and_integer_validation(self):
        mutators = [
            lambda v: v["candidate_profiles"][0]["parameters"][0]["levels"][0].update(probability=True),
            lambda v: v["candidate_profiles"][0]["parameters"][0]["levels"][0].update(probability=float("nan")),
            lambda v: v["candidate_profiles"][0]["parameters"][0]["levels"][0].update(probability=float("inf")),
            lambda v: v["candidate_profiles"][0]["parameters"][0]["levels"][0].update(probability=-0.1),
            lambda v: v["candidate_profiles"][0]["parameters"][0]["levels"][0].update(probability=0.011),
            lambda v: v["candidate_profiles"][0]["parameters"][0].update(selected_level_id="L2"),
            lambda v: v["candidate_profiles"][0]["parameters"][0].update(model_confidence=True),
            lambda v: v["vacancy_profile"]["parameters"][0].update(importance=True),
            lambda v: v["candidate_fields"][0]["industry_months"].update(manufacturing=True),
            lambda v: v["vacancy_fields"]["industry_experience"].update(minimum_months=1.5),
            lambda v: v["candidate_profiles"][0].update(chunk_count=True),
            lambda v: v["policy"].update(k=True),
            lambda v: v["policy"].update(k=float("inf")),
        ]
        for index, mutate in enumerate(mutators):
            value = deepcopy(self.value)
            mutate(value)
            with self.subTest(index=index), self.assertRaises(InvalidInput):
                self.run_result(value)

    def test_incompatible_complete_catalogues_are_rejected_atomically(self):
        mutators = [
            lambda v: v["candidate_profiles"][0].update(contract_version="legacy"),
            lambda v: v["vacancy_profile"].update(contract_version="legacy"),
            lambda v: v["candidate_profiles"][0].update(rubric_sha256="f" * 64),
            lambda v: v["vacancy_profile"].update(rubric_version="other"),
            lambda v: v["vacancy_profile"]["parameters"].pop(),
            lambda v: v["candidate_profiles"][0]["parameters"].append(deepcopy(v["candidate_profiles"][0]["parameters"][0])),
            lambda v: v["candidate_profiles"][0]["parameters"][0]["levels"].reverse(),
            lambda v: v["candidate_profiles"][0]["parameters"][0]["levels"][0].update(description="changed"),
            lambda v: v["vacancy_profile"]["parameters"][0].update(parameter_id="unrecognised"),
        ]
        for index, mutate in enumerate(mutators):
            value = deepcopy(self.value)
            mutate(value)
            with self.subTest(index=index), self.assertRaises(InvalidInput):
                self.run_result(value)

    def test_duplicates_missing_fields_text_leakage_and_policy_are_rejected(self):
        mutators = [
            lambda v: v["candidate_profiles"].append(deepcopy(v["candidate_profiles"][0])),
            lambda v: v["candidate_fields"].append(deepcopy(v["candidate_fields"][0])),
            lambda v: v["candidate_fields"].clear(),
            lambda v: v["candidate_fields"][0].update(candidate_id="absent"),
            lambda v: v["candidate_fields"][0].update(work_locations=["moscow", "moscow"]),
            lambda v: v["candidate_profiles"][0]["sources"][0].update(text="PRIVATE TEXT"),
            lambda v: v["candidate_profiles"][0]["parameters"][0].update(extra="PRIVATE TEXT"),
            lambda v: v["policy"].update(probability_mode="raw_levels"),
            lambda v: v["policy"].update(k=2),
        ]
        for index, mutate in enumerate(mutators):
            value = deepcopy(self.value)
            mutate(value)
            with self.subTest(index=index), self.assertRaises(InvalidInput):
                self.run_result(value)
        value = request_fixture(self.rubric)
        value["candidate_profiles"] *= 1001
        with self.assertRaisesRegex(InvalidInput, "invalid_pool_size"):
            self.run_result(value)

    def test_bundled_rubric_matches_common_contract_release(self):
        common = Path(__file__).resolve().parents[3] / "packages/assessment-contracts/rubrics/sales-v1.json"
        self.assertEqual(default_rubric(), json.loads(common.read_text()))

    def test_provider_rounding_tolerance_accepts_raw_values_without_renormalizing_them(self):
        p = self.value["candidate_profiles"][0]["parameters"][0]
        p["levels"][0]["probability"] = 0.000004
        result = self.run_result()["items"][0]["parameters"][0]
        self.assertEqual(result["levels"][0]["p_raw"], 0.000004)
        self.assertAlmostEqual(result["numeric_probability_mass"], 1.000004)
        self.assertAlmostEqual(sum(l["q"] for l in result["levels"]), 1)

    def test_rounded_choice_totals_are_preserved_and_only_numeric_q_is_normalized(self):
        for probabilities, unknown in (([0, 0.16, 0.4, 0.23, 0], 0.2),
                                       ([0, 0.16, 0.41, 0.24, 0], 0.2)):
            value = deepcopy(self.value)
            p = value["candidate_profiles"][0]["parameters"][0]
            set_distribution(p, probabilities, unknown=unknown)
            raw_level = value["vacancy_profile"]["parameters"][0]["raw_answers"]["level"]
            raw_level["probabilities"] = {level["level_id"]: raw for level, raw in zip(p["levels"], probabilities)}
            raw_level["probabilities"][UNKNOWN] = unknown
            raw_importance = value["vacancy_profile"]["parameters"][0]["raw_answers"]["importance"]
            raw_importance["probabilities"] = {"I0": 0.1, "I1": 0.5, "I2": 0.2, "I3": 0.1,
                                                 UNKNOWN: round(sum(probabilities) + unknown - 0.9, 2)}
            before = deepcopy(value)
            with self.subTest(total=sum(probabilities) + unknown):
                parameter = self.run_result(value)["items"][0]["parameters"][0]
                self.assertEqual([level["p_raw"] for level in parameter["levels"]], probabilities)
                self.assertEqual(parameter["insufficient_data_probability"], unknown)
                self.assertAlmostEqual(sum(level["q"] for level in parameter["levels"]), 1)
                self.assertEqual(value, before)

    def test_rounded_exception_still_rejects_off_grid_gross_all_zero_and_non_argmax(self):
        for probabilities, unknown, chosen in (([0, 0.16, 0.401, 0.23, 0], 0.2, "L3"),
                                               ([0, 0.1, 0.4, 0.1, 0], 0.1, "L3"),
                                               ([0, 0, 0, 0, 0], 0, "L3"),
                                               ([0, 0.16, 0.4, 0.23, 0], 0.2, "L4")):
            value = deepcopy(self.value)
            set_distribution(value["candidate_profiles"][0]["parameters"][0], probabilities, unknown, chosen)
            with self.subTest(probabilities=probabilities, chosen=chosen), self.assertRaises(InvalidInput):
                self.run_result(value)
        # The same rounded allowance never relaxes a vacancy Choice's argmax.
        value = deepcopy(self.value)
        raw_level = value["vacancy_profile"]["parameters"][0]["raw_answers"]["level"]
        raw_level["probabilities"] = {"L1": 0, "L2": 0.16, "L3": 0.23, "L4": 0.4,
                                      "L5": 0, UNKNOWN: 0.2}
        with self.assertRaises(InvalidInput):
            self.run_result(value)

    def test_maximum_pool_is_complete_and_next_candidate_is_rejected(self):
        value = request_fixture(self.rubric, count=1000)
        result = self.run_result(value)
        self.assertEqual(len(result["items"]), 1000)
        self.assertEqual(result["pool"]["size"], 1000)
        self.assertEqual(set(result["pool"]["candidate_ids"]), {p["candidate_id"] for p in value["candidate_profiles"]})
        value["candidate_profiles"].append(deepcopy(value["candidate_profiles"][0]))
        with self.assertRaisesRegex(InvalidInput, "invalid_pool_size"):
            self.run_result(value)


if __name__ == "__main__":
    unittest.main()
