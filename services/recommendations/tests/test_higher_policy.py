"""Open contract tests for versioned fallback and server-owned point totals."""

from copy import deepcopy
import math
import unittest

from recommendations.engine import (HIGHER_POLICY, POLICY, UNKNOWN, InvalidInput,
                                    bounded_number, recommend)
from fixtures import request_fixture, set_distribution, set_importance, synthetic_rubric


class HigherPolicyTests(unittest.TestCase):
    def setUp(self):
        self.rubric = synthetic_rubric()
        self.value = request_fixture(self.rubric)
        self.value["policy"] = dict(HIGHER_POLICY)

    def item(self, value=None, rubric=None):
        return recommend(self.value if value is None else value,
                         rubric=self.rubric if rubric is None else rubric)["items"][0]

    def assert_points(self, item):
        if item["score"] is None:
            self.assertIsNone(item["score_points"])
            self.assertTrue(all(p["score_points_contribution"] is None and p["weight_share"] is None
                                for p in item["parameters"]))
            return
        self.assertAlmostEqual(item["score_points"], 50 * (item["score"] + 1), places=12)
        self.assertAlmostEqual(math.fsum(p["score_points_contribution"] for p in item["parameters"]),
                               item["score_points"], places=12)
        self.assertAlmostEqual(sum(p["weight_share"] for p in item["parameters"]), 1, places=12)
        self.assertTrue(0 <= item["score_points"] <= 100)
        for p in item["parameters"]:
            self.assertTrue(math.isfinite(p["score_points_contribution"]))
            if p["excluded"]:
                self.assertEqual((p["weight_share"], p["score_points_contribution"]), (0, 0))
            else:
                self.assertAlmostEqual(p["weight_share"], p["weight"] / item["coverage"]["included_weight"])
                self.assertAlmostEqual(p["score_points_contribution"], p["weight_share"] * 50 * (p["m"] + 1))

    def test_known_requirements_keep_old_tanh_arithmetic_and_ranking(self):
        value = request_fixture(self.rubric, count=3)
        for profile, level in zip(value["candidate_profiles"], (2, 3, 4)):
            for p in profile["parameters"]:
                set_distribution(p, [int(i == level) for i in range(1, 6)], selected="L" + str(level))
        old = recommend(value, rubric=self.rubric)
        value["policy"] = dict(HIGHER_POLICY)
        new = recommend(value, rubric=self.rubric)
        self.assertNotEqual(old["recommendation_id"], new["recommendation_id"])
        for old_item, new_item in zip(old["items"], new["items"]):
            self.assertEqual(old_item["candidate_id"], new_item["candidate_id"])
            for field in ("score", "score_points", "rank", "coverage", "status", "eligibility"):
                self.assertEqual(old_item[field], new_item[field])
            self.assertEqual(old_item["parameters"], new_item["parameters"])
            self.assertTrue(all(p["utility_mode"] == "required_level_tanh" for p in new_item["parameters"]))
            self.assert_points(new_item)

    def test_unknown_requirement_uses_conditional_signed_linear_without_fake_requirement(self):
        for i in range(2):
            set_importance(self.value["vacancy_profile"], i, i + 1, required=None)
            set_distribution(self.value["candidate_profiles"][0]["parameters"][i],
                             [0, .16, .4, .24, 0], unknown=.2, selected="L3")
        before = deepcopy(self.value)
        item = self.item()
        self.assertAlmostEqual(item["score"], .05)
        self.assertAlmostEqual(item["score_points"], 52.5)
        for p in item["parameters"]:
            self.assertEqual(p["utility_mode"], "higher_level_linear")
            self.assertIsNone(p["required_level_id"])
            self.assertIsNone(p["required_position"])
            self.assertEqual([l["u"] for l in p["levels"]], [-1, -.5, 0, .5, 1])
            self.assertEqual([l["p_raw"] for l in p["levels"]], [0, .16, .4, .24, 0])
            self.assertAlmostEqual(sum(l["q"] for l in p["levels"]), 1)
            self.assertEqual(p["insufficient_data_probability"], .2)
        self.assertEqual(self.value, before)
        self.assert_points(item)

    def test_extremes_midpoint_and_no_comparable_skills_remain_distinct(self):
        set_importance(self.value["vacancy_profile"], 1, 0, required=None)
        set_importance(self.value["vacancy_profile"], 0, 3, required=None)
        p = self.value["candidate_profiles"][0]["parameters"][0]
        for level, score, points in ((1, -1, 0), (3, 0, 50), (5, 1, 100)):
            set_distribution(p, [int(i == level) for i in range(1, 6)], selected="L" + str(level))
            item = self.item()
            with self.subTest(level=level):
                self.assertEqual((item["score"], item["score_points"]), (score, points))
                self.assertEqual(item["status"], "scored")
                self.assert_points(item)
        set_distribution(p, [0] * 5, unknown=1, selected=None)
        item = self.item()
        self.assertEqual((item["score"], item["score_points"], item["rank"], item["status"]),
                         (None, None, None, "unscored"))
        self.assert_points(item)

    def test_signed_linear_fallback_uses_catalogue_size_not_hardcoded_five(self):
        for count in (2, 3, 7, 10):
            rubric = synthetic_rubric()
            for p in rubric["parameters"]:
                p["levels"] = [{"level_id": "L" + str(i), "label": "Level " + str(i),
                                "description": p["parameter_id"] + " level " + str(i)}
                               for i in range(1, count + 1)]
            value = request_fixture(rubric)
            value["policy"] = dict(HIGHER_POLICY)
            for i in range(2):
                # set_importance's raw level choices target the synthetic five
                # levels; replace only that raw answer with this catalogue.
                set_importance(value["vacancy_profile"], i, 1, required=None)
                answer = value["vacancy_profile"]["parameters"][i]["raw_answers"]["level"]
                answer["probabilities"] = {"L" + str(l): 0 for l in range(1, count + 1)}
                answer["probabilities"][UNKNOWN] = 1
                value["candidate_profiles"][0]["parameters"][i]["levels"] = [
                    {**deepcopy(level), "probability": 0} for level in rubric["parameters"][i]["levels"]]
                set_distribution(value["candidate_profiles"][0]["parameters"][i],
                                 [0] * (count - 1) + [1], selected="L" + str(count))
            item = self.item(value, rubric)
            with self.subTest(level_count=count):
                self.assertEqual(item["score_points"], 100)
                self.assertEqual([l["u"] for l in item["parameters"][0]["levels"]],
                                 [2 * (i - 1) / (count - 1) - 1 for i in range(1, count + 1)])
                self.assert_points(item)

    def test_mixed_utilities_weights_and_exclusions_have_additive_points(self):
        set_importance(self.value["vacancy_profile"], 0, 3, required="L3")
        set_importance(self.value["vacancy_profile"], 1, 1, required=None)
        set_distribution(self.value["candidate_profiles"][0]["parameters"][0], [0, 1, 0, 0, 0], selected="L2")
        set_distribution(self.value["candidate_profiles"][0]["parameters"][1], [0, 0, 0, 0, 1], selected="L5")
        item = self.item()
        self.assertAlmostEqual(item["score"], (3 * math.tanh(-1) + 1) / 4)
        self.assertEqual([p["utility_mode"] for p in item["parameters"]],
                         ["required_level_tanh", "higher_level_linear"])
        self.assertEqual([p["weight_share"] for p in item["parameters"]], [.75, .25])
        self.assertAlmostEqual(item["parameters"][0]["weighted_contribution"], 3 * math.tanh(-1))
        self.assert_points(item)
        set_importance(self.value["vacancy_profile"], 0, 0, required=None)
        item = self.item()
        self.assertEqual(item["score_points"], 100)
        self.assertEqual(item["coverage"]["included_weight"], 1)
        self.assertIsNone(item["parameters"][0]["utility_mode"])
        self.assert_points(item)

    def test_unknown_importance_rejects_in_both_policies_and_old_policy_stays_strict(self):
        for policy in (POLICY, HIGHER_POLICY):
            value = deepcopy(self.value)
            value["policy"] = dict(policy)
            set_importance(value["vacancy_profile"], 0, UNKNOWN, required=None)
            with self.subTest(policy=policy), self.assertRaisesRegex(InvalidInput, "clarification_required"):
                self.item(value)
        self.value["policy"] = dict(POLICY)
        set_importance(self.value["vacancy_profile"], 0, 3, required=None)
        with self.assertRaisesRegex(InvalidInput, "clarification_required"):
            self.item()

    def test_malformed_new_policies_and_client_numeric_fields_reject(self):
        for change in ({"version": "tanh-higher-v3"}, {"k": 2}, {"k": True},
                       {"k": float("nan")}, {"probability_mode": "raw_levels"}, {"utility_mode": "higher_level_linear"}):
            value = deepcopy(self.value)
            value["policy"].update(change)
            with self.subTest(change=change), self.assertRaises(InvalidInput):
                self.item(value)
        for field in ("score_points", "weight_share", "score_points_contribution", "utility_mode"):
            value = deepcopy(self.value)
            value["candidate_profiles"][0]["parameters"][0][field] = 100
            with self.subTest(client_field=field), self.assertRaises(InvalidInput):
                self.item(value)

    def test_derived_bounds_reject_nonfinite_or_materially_outside_interval(self):
        for value in (True, float("nan"), float("inf"), -1.01, 1.01):
            with self.subTest(value=value), self.assertRaises(InvalidInput):
                bounded_number(value, -1, 1)
        self.assertEqual(bounded_number(1 + 2e-16, -1, 1), 1)
        self.assertEqual(bounded_number(-1 - 2e-16, -1, 1), -1)


if __name__ == "__main__":
    unittest.main()
