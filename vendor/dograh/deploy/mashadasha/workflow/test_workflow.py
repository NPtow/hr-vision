"""Contract checks for the migration; no model/provider/network calls."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("mashadasha_generate", HERE / "generate.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class WorkflowContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builder = generator.Builder(HERE / "source")
        cls.graph = cls.builder.build()
        cls.nodes = {node["id"]: node for node in cls.graph["nodes"]}
        cls.edges = cls.graph["edges"]

    def test_frozen_sources_match_audited_hashes(self):
        manifest = generator.read_json(HERE / "source" / "manifest.json")
        for item in manifest["files"]:
            self.assertEqual(hashlib.sha256((HERE / "source" / item["file"]).read_bytes()).hexdigest(), item["sha256"])

    def test_all_conversation_nodes_reachable_and_can_finish(self):
        self.assertEqual(len(self.nodes), len(self.graph["nodes"]))
        self.assertEqual([n["id"] for n in self.nodes.values() if n["type"] == "startCall"], ["b1_monologue"])
        self.assertTrue(all(e["source"] in self.nodes and e["target"] in self.nodes for e in self.edges))
        self.assertFalse(any(e["source"] == "end" or e["target"] == "b1_monologue" for e in self.edges))

        def closure(start, reverse=False):
            seen = {start}
            while True:
                more = {e["source"] if reverse else e["target"] for e in self.edges
                        if (e["target"] if reverse else e["source"]) in seen}
                if more <= seen:
                    return seen
                seen |= more

        conversation = set(self.nodes) - {"global"}
        self.assertEqual(closure("b1_monologue"), conversation)
        self.assertEqual(closure("end", reverse=True), conversation)

    def test_llm_transition_tool_names_are_unique_ascii_and_short(self):
        seen = set()
        for edge in self.edges:
            name = edge["data"]["label"]
            self.assertRegex(name, r"^[a-z][a-z0-9_]*$")
            self.assertLessEqual(len(name), 64)
            key = (edge["source"], re.sub(r"[^a-z0-9]", "_", name.lower()))
            self.assertNotIn(key, seen)
            seen.add(key)

    def test_source_case_blitz_order_and_budgets_preserved(self):
        q = self.builder.questions
        self.assertIn(q["2"]["text"], self.nodes["b2_case"]["data"]["prompt"])
        blitz_ids = ["b5_blitz"] + [f"b5_blitz_{i:02d}" for i in range(2, 14)]
        self.assertEqual(len(q["5"]["probes"]), 13)
        for i, (id, probe) in enumerate(zip(blitz_ids, q["5"]["probes"])):
            self.assertIn("Вопрос: " + probe["text"], self.nodes[id]["data"]["prompt"])
            targets = [e["target"] for e in self.edges if e["source"] == id]
            self.assertEqual(targets, [blitz_ids[i + 1] if i < 12 else "end"])
        self.assertEqual({k: v["max_follow_ups"] for k, v in q.items()}, {"1": 1, "2": 6, "3": 8, "4a": 9, "4b": 5, "5": 16})
        for id, info in self.builder.mapping.items():
            if info["source_block"]:
                budget = q[info["source_block"]]["max_follow_ups"]
                self.assertIn(f"не больше {budget} уточняющих", self.nodes[id]["data"]["prompt"])

    def test_role_selection_and_callback_branches_exist(self):
        triples = {(e["source"], e["data"]["label"], e["target"]) for e in self.edges}
        paths = [
            ("b4_setup", "choose_b2b", "b4_b2b"),
            ("b4_setup", "choose_b2c", "b4_b2c"),
            ("b4_next", "specific_callback", "b4_confirm"),
            ("b4_next", "vague_callback", "b4_date"),
            ("b4_next", "goodbye_without_step", "b4_window"),
            ("b4_date", "no_concrete_step", "b4_exit"),
            ("b4_confirm", "callback_confirmed", "b4_exit"),
        ]
        self.assertTrue(set(paths) <= triples)
        self.assertFalse(any(e["target"] == "b4_date" for e in self.edges if e["source"] == "b4_date"))
        self.assertFalse(any(e["target"] == "b4_window" for e in self.edges if e["source"] == "b4_window"))

    def test_client_role_isolated_and_context_is_initial_only(self):
        client_nodes = ["b4_b2b", "b4_b2c", "b4_think", "b4_other", "b4_price", "b4_refuse", "b4_next", "b4_date", "b4_window", "b4_confirm"]
        for id in client_nodes:
            self.assertFalse(self.nodes[id]["data"]["add_global_prompt"])
            self.assertIn(generator.ROLE_BASE, self.nodes[id]["data"]["prompt"])
            self.assertIn(generator.CONTEXT, self.nodes[id]["data"]["prompt"])
        all_prompts = "\n".join(n["data"]["prompt"] for n in self.nodes.values())
        self.assertNotIn("{{gathered_context", all_prompts)
        self.assertNotIn("{{initial_context", all_prompts)

    def test_committed_artifacts_match_generator_and_preserve_history(self):
        with tempfile.TemporaryDirectory() as path:
            out = Path(path)
            generator.generate(HERE / "source", out)
            for name in ["workflow.json", "create-request.json", "update-request.json", "provenance.json", "initial-context.example.json"]:
                self.assertEqual((HERE / name).read_bytes(), (out / name).read_bytes(), name)
            settings = generator.read_json(out / "update-request.json")["workflow_configurations"]
            self.assertFalse(settings["context_compaction_enabled"])
            self.assertEqual(settings["max_call_duration"], 3600)


if __name__ == "__main__":
    unittest.main()
