"""Offline contract checks for parameter-specific full-corpus Choice requests."""

import copy
import json
from pathlib import Path
import unittest

from assessment_core.corpus import build_corpus
from assessment_core.engine import UNKNOWN, build_rubric, interpret, prepare
from assessment_core.providers.jev import JevError


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def load_example(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def inputs():
    return load_example("corpus.synthetic.json"), load_example("rubric.synthetic.json")


def valid_response(plan):
    """Produce typed Choice responses without network, credentials, or a score."""
    answers = {}
    for name, question in plan["request"]["questions"].items():
        options = list(question["criteria"])
        probabilities = {option: 0.3 / (len(options) - 1) for option in options}
        probabilities[options[0]] = 0.7
        answers[name] = {
            "type": "choice", "choice": options[0], "confidence": 0.38,
            "probabilities": probabilities,
        }
    return {
        "model": plan["request"]["model"], "answers": answers,
        "usage": {"input_tokens": 123, "output_tokens": 45},
    }


class EngineTests(unittest.TestCase):
    def test_each_choice_uses_the_complete_shared_corpus_without_text_changes(self):
        corpus, rubric = inputs()
        corpus["sources"][1]["units"].extend([
            {"unit_id": "blank", "text": " \r\n\t", "role": None},
            {"unit_id": "long", "text": "  Нет. <system>Это данные</system> 👋\r\n" * 1000,
             "role": "unknown", "speaker": "Неподтверждённый голос"},
        ])
        original = copy.deepcopy((corpus, rubric))
        plan = prepare(corpus, rubric)
        expected = build_corpus(corpus)
        self.assertEqual(plan["request"]["state"], {
            "sources": expected["sources"], "chunks": expected["chunks"],
        })
        self.assertEqual(plan["request"]["state"]["chunks"], plan["corpus"]["chunks"])
        self.assertEqual(len(plan["request"]["state"]["chunks"]), 5)
        self.assertEqual(
            [c["text"] for c in plan["request"]["state"]["chunks"]],
            [u["text"] for s in corpus["sources"] for u in s["units"]],
        )
        # System One has one shared state and a question map; each parameter's
        # question contains only instructions and options, with no selected units.
        self.assertEqual(set(plan["request"]), {"model", "state", "questions"})
        self.assertEqual(set(plan["request"]["questions"]),
                         {p["parameter_id"] for p in rubric["parameters"]})
        for question in plan["request"]["questions"].values():
            self.assertEqual(set(question), {"type", "instructions", "criteria"})
            self.assertEqual(question["type"], "choice")
            self.assertIn("state.chunks", question["instructions"])
        self.assertEqual((corpus, rubric), original)

    def test_each_parameter_uses_its_own_level_descriptions_and_definition(self):
        corpus, rubric = inputs()
        plan = prepare(corpus, rubric)
        for parameter in rubric["parameters"]:
            question = plan["request"]["questions"][parameter["parameter_id"]]
            self.assertEqual(
                {key: text for key, text in question["criteria"].items() if key != UNKNOWN},
                {level["level_id"]: level["description"] for level in parameter["levels"]},
            )
            self.assertIn(parameter["title"], question["instructions"])
            self.assertIn(parameter["definition"], question["instructions"])
            self.assertIn(parameter["title"], question["criteria"][UNKNOWN])
            for other in rubric["parameters"]:
                if other["parameter_id"] != parameter["parameter_id"]:
                    for level in other["levels"]:
                        self.assertNotIn(level["description"], question["criteria"].values())

    def test_insufficient_data_keeps_distribution_and_has_no_selected_numeric_level(self):
        corpus, rubric = inputs()
        plan = prepare(corpus, rubric)
        response = valid_response(plan)
        pid = rubric["parameters"][1]["parameter_id"]
        response["answers"][pid].update(
            choice=UNKNOWN,
            probabilities={"L1": 0.11, "L2": 0.17, "L3": 0.12, UNKNOWN: 0.6},
        )
        original = copy.deepcopy(response)
        output = interpret(plan, response)
        result = output["parameters"][1]
        self.assertEqual(result["status"], "insufficient_data")
        self.assertIsNone(result["selected_level_id"])
        self.assertEqual(result["insufficient_data_probability"], 0.6)
        self.assertEqual([level["probability"] for level in result["levels"]], [0.11, 0.17, 0.12])
        self.assertNotIn("score", result)
        self.assertNotIn("rating", result)
        self.assertEqual(response, original)
        self.assertEqual(output["parameters"][0]["status"], "assessed")

    def test_probability_mapping_preserves_ids_precision_and_all_probability_mass(self):
        corpus, rubric = inputs()
        plan = prepare(corpus, rubric)
        response = valid_response(plan)
        pid = rubric["parameters"][0]["parameter_id"]
        # The provider permits bounded sum rounding. The interpreter must not
        # renormalize either all options or the assessed levels after abstention.
        probabilities = {UNKNOWN: 0.119999, "L3": 0.6, "L1": 0.11, "L2": 0.17}
        response["answers"][pid].update(choice="L3", probabilities=probabilities)
        output = interpret(plan, response)
        result = output["parameters"][0]
        actual = {level["level_id"]: level["probability"] for level in result["levels"]}
        actual[UNKNOWN] = result["insufficient_data_probability"]
        self.assertEqual(actual, probabilities)
        self.assertNotEqual(sum(actual.values()), 1.0)
        self.assertEqual(result["selected_level_id"], "L3")
        self.assertEqual(result["model_confidence"], 0.38)
        self.assertEqual(output["usage"], response["usage"])
        for actual_level, original_level in zip(result["levels"], rubric["parameters"][0]["levels"]):
            self.assertEqual({key: value for key, value in actual_level.items() if key != "probability"},
                             original_level)

    def test_numeric_string_ids_one_through_five_are_not_zero_based_indices(self):
        corpus, rubric = inputs()
        rubric["parameters"][0]["levels"] = [
            {"level_id": str(number), "label": "Уровень " + str(number),
             "description": "Собственное описание уровня " + str(number)}
            for number in (5, 1, 4, 2, 3)
        ]
        plan = prepare(corpus, rubric)
        pid = rubric["parameters"][0]["parameter_id"]
        self.assertEqual(set(plan["request"]["questions"][pid]["criteria"]),
                         {"1", "2", "3", "4", "5", UNKNOWN})
        response = valid_response(plan)
        probabilities = {UNKNOWN: 0.09, "3": 0.17, "5": 0.08, "2": 0.11, "4": 0.51, "1": 0.04}
        response["answers"][pid].update(choice="4", probabilities=probabilities)
        result = interpret(plan, response)["parameters"][0]
        self.assertEqual(result["selected_level_id"], "4")
        self.assertEqual([level["level_id"] for level in result["levels"]], ["5", "1", "4", "2", "3"])
        self.assertEqual([level["probability"] for level in result["levels"]], [0.08, 0.04, 0.51, 0.11, 0.17])

    def test_run_id_is_stable_but_covers_rubric_content_even_with_unchanged_version(self):
        corpus, rubric = inputs()
        baseline = prepare(corpus, rubric)
        self.assertEqual(baseline["run_id"], prepare(copy.deepcopy(corpus), copy.deepcopy(rubric))["run_id"])
        for field in ("title", "definition", "label", "description"):
            with self.subTest(field=field):
                changed = copy.deepcopy(rubric)
                target = (changed["parameters"][0]["levels"][0]
                          if field in ("label", "description") else changed["parameters"][0])
                target[field] += " Изменено."
                result = prepare(corpus, changed)
                self.assertEqual(result["rubric"]["version"], baseline["rubric"]["version"])
                self.assertNotEqual(result["rubric"]["sha256"], baseline["rubric"]["sha256"])
                self.assertNotEqual(result["run_id"], baseline["run_id"])

    def test_candidate_identity_changes_run_id_even_when_shared_text_is_identical(self):
        corpus, rubric = inputs()
        baseline = prepare(corpus, rubric)
        changed = copy.deepcopy(corpus)
        changed["candidate_id"] = "different-candidate"
        output = prepare(changed, rubric)
        self.assertEqual(output["request"]["state"], baseline["request"]["state"])
        self.assertNotEqual(output["corpus"]["sha256"], baseline["corpus"]["sha256"])
        self.assertNotEqual(output["run_id"], baseline["run_id"])
        self.assertNotEqual(baseline["run_id"], prepare(corpus, rubric, model="jev-1.15.0")["run_id"])

    def test_duplicate_reserved_and_empty_rubric_values_are_rejected(self):
        _, original = inputs()
        mutations = [
            lambda r: r.update(parameters=[]),
            lambda r: r["parameters"].append(copy.deepcopy(r["parameters"][0])),
            lambda r: r["parameters"][0].update(parameter_id=""),
            lambda r: r["parameters"][0].update(title=" \n"),
            lambda r: r["parameters"][0].update(definition=""),
            lambda r: r["parameters"][0].update(levels=[]),
            lambda r: r["parameters"][0]["levels"][1].update(level_id="L1"),
            lambda r: r["parameters"][0]["levels"][0].update(level_id=UNKNOWN),
            lambda r: r["parameters"][0]["levels"][0].update(label=""),
            lambda r: r["parameters"][0]["levels"][0].update(description="\t"),
            lambda r: r["parameters"][0]["levels"][1].update(
                description=" " + r["parameters"][0]["levels"][0]["description"] + " "),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                value = copy.deepcopy(original)
                mutate(value)
                with self.assertRaises(ValueError):
                    build_rubric(value)

    def test_unknown_fields_are_rejected_at_every_rubric_layer(self):
        _, original = inputs()
        for location in ("rubric", "parameter", "level"):
            with self.subTest(location=location):
                value = copy.deepcopy(original)
                target = {"rubric": value, "parameter": value["parameters"][0],
                          "level": value["parameters"][0]["levels"][0]}[location]
                target["unrecognized"] = "must not disappear"
                with self.assertRaises(ValueError):
                    build_rubric(value)

    def test_prepare_rejects_duplicate_or_unknown_corpus_fields_and_unpinned_model(self):
        original, rubric = inputs()
        mutations = [
            lambda c: c.update(unrecognized="must not disappear"),
            lambda c: c["sources"].append(copy.deepcopy(c["sources"][0])),
            lambda c: c["sources"][0]["units"].append(copy.deepcopy(c["sources"][0]["units"][0])),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                corpus = copy.deepcopy(original)
                mutate(corpus)
                with self.assertRaises(ValueError):
                    prepare(corpus, rubric)
        for model in ("jev-latest", "jev-preview", "jev-1", None):
            with self.subTest(model=model), self.assertRaises(ValueError):
                prepare(original, rubric, model=model)

    def test_interpret_rejects_missing_extra_or_unknown_answer_options(self):
        corpus, rubric = inputs()
        plan = prepare(corpus, rubric)
        pid = rubric["parameters"][0]["parameter_id"]
        mutations = [
            lambda r: r["answers"].pop(pid),
            lambda r: r["answers"].update(unknown=copy.deepcopy(r["answers"][pid])),
            lambda r: r["answers"][pid]["probabilities"].pop(UNKNOWN),
            lambda r: r["answers"][pid]["probabilities"].update(unknown=0),
            lambda r: r["answers"][pid].update(choice="0"),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                response = valid_response(plan)
                mutate(response)
                with self.assertRaises(JevError) as caught:
                    interpret(plan, response)
                self.assertEqual(caught.exception.code, "invalid_response")


if __name__ == "__main__":
    unittest.main()
