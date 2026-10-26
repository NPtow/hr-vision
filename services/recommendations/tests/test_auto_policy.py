"""Open v3 regression cases: unknown importance is an explicit zero weight."""

from copy import deepcopy
import math
import unittest

from recommendations.engine import AUTO_POLICY, HIGHER_POLICY, POLICY, UNKNOWN, InvalidInput, recommend
from fixtures import request_fixture, set_distribution, set_importance, synthetic_rubric

V3 = {"version": "tanh-higher-zero-v3", "k": 1, "probability_mode": "conditional_levels"}


class AutoPolicyTests(unittest.TestCase):
    def setUp(self):
        self.rubric = synthetic_rubric()
        self.value = request_fixture(self.rubric)
        self.value["policy"] = dict(V3)

    def item(self, value=None):
        return recommend(self.value if value is None else value, rubric=self.rubric)["items"][0]

    def test_exported_policy_constant_is_the_explicit_v3_contract(self):
        self.assertEqual(AUTO_POLICY, V3)

    def test_unknown_importance_is_effective_zero_and_raw_snapshot_is_unchanged(self):
        set_importance(self.value["vacancy_profile"], 0, UNKNOWN, required=None)
        set_importance(self.value["vacancy_profile"], 1, 3, required=None)
        set_distribution(self.value["candidate_profiles"][0]["parameters"][1], [0, 0, 0, 1, 0], selected="L4")
        before = deepcopy(self.value)
        item = self.item()
        excluded, included = item["parameters"]
        self.assertEqual((excluded["weight"], excluded["source_importance"], excluded["exclusion_reason"]),
                         (0, UNKNOWN, "unknown_importance"))
        self.assertTrue(excluded["excluded"])
        self.assertEqual((excluded["weight_share"], excluded["score_points_contribution"]), (0, 0))
        self.assertEqual(item["coverage"], {"total_parameters": 2, "included_parameters": 1,
                                           "excluded_parameters": 1, "included_weight": 3,
                                           "excluded_weight": 0, "total_weight": 3})
        self.assertEqual((included["weight"], included["weight_share"], included["utility_mode"]),
                         (3, 1, "higher_level_linear"))
        self.assertIsNone(included["required_level_id"])
        self.assertIsNone(included["required_position"])
        self.assertEqual((item["score"], item["score_points"]), (.5, 75))
        self.assertEqual(self.value, before)

    def test_explicit_zero_and_unknown_importance_have_distinct_reasons(self):
        set_importance(self.value["vacancy_profile"], 0, UNKNOWN, required="L3")
        set_importance(self.value["vacancy_profile"], 1, 0, required=None)
        item = self.item()
        self.assertEqual([p["exclusion_reason"] for p in item["parameters"]], ["unknown_importance", "zero_importance"])
        self.assertEqual([p["source_importance"] for p in item["parameters"]], [UNKNOWN, 0])
        self.assertEqual((item["score"], item["score_points"], item["status"], item["rank"]),
                         (None, None, "unscored", None))
        self.assertEqual(item["coverage"]["total_weight"], 0)
        self.assertTrue(all(p["score_points_contribution"] is None for p in item["parameters"]))

    def test_all_unknown_importance_is_successful_unscored_result(self):
        for index in range(2):
            set_importance(self.value["vacancy_profile"], index, UNKNOWN, required=None)
        item = self.item()
        self.assertEqual(item["status"], "unscored")
        self.assertIsNone(item["score_points"])
        self.assertEqual(item["coverage"]["included_parameters"], 0)
        self.assertTrue(all(p["exclusion_reason"] == "unknown_importance" for p in item["parameters"]))

    def test_known_target_preserves_tanh_and_no_target_retains_direct_level_points(self):
        set_importance(self.value["vacancy_profile"], 0, 3, required="L3")
        set_importance(self.value["vacancy_profile"], 1, 1, required=None)
        set_distribution(self.value["candidate_profiles"][0]["parameters"][0], [0, 1, 0, 0, 0], selected="L2")
        set_distribution(self.value["candidate_profiles"][0]["parameters"][1], [0, 0, 0, 0, 1], selected="L5")
        item = self.item()
        self.assertAlmostEqual(item["score"], (3 * math.tanh(-1) + 1) / 4)
        self.assertEqual([p["utility_mode"] for p in item["parameters"]], ["required_level_tanh", "higher_level_linear"])
        self.assertAlmostEqual(sum(p["score_points_contribution"] for p in item["parameters"]), item["score_points"])
        self.assertEqual(item["parameters"][1]["score_points_contribution"], 25)

    def test_no_target_uses_all_five_direct_level_points(self):
        set_importance(self.value["vacancy_profile"], 0, UNKNOWN, required=None)
        set_importance(self.value["vacancy_profile"], 1, 2, required=None)
        for level, points in enumerate((0, 25, 50, 75, 100), 1):
            set_distribution(self.value["candidate_profiles"][0]["parameters"][1],
                             [int(i == level) for i in range(1, 6)], selected="L" + str(level))
            with self.subTest(level=level):
                self.assertEqual(self.item()["score_points"], points)

    def test_unknown_candidate_still_excluded_and_cannot_turn_unknown_weight_into_score(self):
        set_importance(self.value["vacancy_profile"], 0, UNKNOWN, required=None)
        set_distribution(self.value["candidate_profiles"][0]["parameters"][1], [0] * 5, unknown=1, selected=None)
        item = self.item()
        self.assertEqual([p["exclusion_reason"] for p in item["parameters"]], ["unknown_importance", UNKNOWN])
        self.assertIsNone(item["score"])
        self.assertEqual(item["coverage"]["excluded_weight"], 1)

    def test_legacy_unknown_importance_and_unknown_target_behavior_is_unchanged(self):
        for policy in (POLICY, HIGHER_POLICY):
            value = deepcopy(self.value)
            value["policy"] = dict(policy)
            set_importance(value["vacancy_profile"], 0, UNKNOWN, required=None)
            with self.subTest(policy=policy), self.assertRaisesRegex(InvalidInput, "clarification_required"):
                self.item(value)
        value = deepcopy(self.value)
        value["policy"] = dict(POLICY)
        set_importance(value["vacancy_profile"], 0, 1, required=None)
        with self.assertRaisesRegex(InvalidInput, "clarification_required"):
            self.item(value)

    def test_unknown_weight_does_not_bypass_raw_answer_or_status_validation(self):
        set_importance(self.value["vacancy_profile"], 0, UNKNOWN, required=None)
        for mutation in (lambda p: p.update(status="irrelevant"),
                         lambda p: p["raw_answers"]["importance"]["probabilities"].update(insufficient_data=0),
                         lambda p: p.update(importance=True)):
            value = deepcopy(self.value)
            mutation(value["vacancy_profile"]["parameters"][0])
            with self.assertRaises(InvalidInput):
                self.item(value)


if __name__ == "__main__":
    unittest.main()
