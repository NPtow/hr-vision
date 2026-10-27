"""Source/graph contracts; no service, model or network calls."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("career_generate", HERE / "generate.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class CareerWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builder = generator.Builder(HERE / "source")
        cls.graph = cls.builder.build()
        cls.nodes = {n["id"]: n for n in cls.graph["nodes"]}
        cls.edges = cls.graph["edges"]

    def test_sources_match_frozen_hashes_and_exact_five_blocks(self):
        manifest = generator.read_json(HERE / "source" / "manifest.json")
        self.assertEqual(manifest["agent_id"], "57f7a18a-ea1e-4b8f-829b-e340e1605d46")
        for item in manifest["files"]:
            self.assertEqual(hashlib.sha256((HERE / "source" / item["file"]).read_bytes()).hexdigest(), item["sha256"])
        self.assertEqual(list(self.builder.q), ["1", "2", "3", "4", "5"])
        self.assertEqual({k: q["max_follow_ups"] for k, q in self.builder.q.items()}, {"1": 3, "2": 3, "3": 2, "4": 2, "5": 5})

    def test_graph_reachable_and_every_route_can_finish(self):
        self.assertEqual(len(self.nodes), len(self.graph["nodes"]))
        self.assertEqual([n["id"] for n in self.nodes.values() if n["type"] == "startCall"], ["career_welcome"])
        self.assertTrue(all(e["source"] in self.nodes and e["target"] in self.nodes for e in self.edges))
        def closure(start, reverse=False):
            seen = {start}
            while True:
                more = {e["source"] if reverse else e["target"] for e in self.edges if (e["target"] if reverse else e["source"]) in seen}
                if more <= seen:
                    return seen
                seen |= more
        conversation = set(self.nodes) - {"career_global"}
        self.assertEqual(closure("career_welcome"), conversation)
        self.assertEqual(closure("career_end", True), conversation)

    def test_company_and_one_or_two_experience_branches(self):
        triples = {(e["source"], e["data"]["label"], e["target"]) for e in self.edges}
        for triple in [
            ("career_company", "named_company_reason_missing", "career_company_why"),
            ("career_company", "environment_without_brand", "career_company_criterion"),
            ("career_company", "company_preferences_complete", "career_experience_select"),
            ("career_company", "criterion_already_clear", "career_company_avoid"),
            ("career_direction", "role_preferences_already_complete", "career_market"),
            ("career_tasks", "avoid_preferences_already_known", "career_market"),
            ("career_depth1", "one_focus_only", "career_reflection"),
            ("career_depth1", "two_focuses_selected", "career_case2"),
            ("career_depth2", "two_cases_done", "career_reflection")]:
            self.assertIn(triple, triples)
        self.assertFalse(any(e["target"] == "career_case1" for e in self.edges if e["source"] in {"career_case2", "career_depth2", "career_reflection"}))

    def test_fresh_answer_contract_and_unique_tool_names(self):
        names = set()
        for n in self.nodes.values():
            if n["type"] in {"startCall", "agentNode"}:
                self.assertIn(generator.ROUTING, n["data"]["prompt"])
                self.assertNotIn("немедленного перехода", n["data"]["prompt"])
        for edge in self.edges:
            name = edge["data"]["label"]
            self.assertRegex(name, r"^[a-z][a-z0-9_]*$")
            self.assertLessEqual(len(name), 64)
            self.assertNotIn((edge["source"], name), names)
            names.add((edge["source"], name))
            self.assertIn("после нового ответа", edge["data"]["condition"])

    def test_source_prompts_and_no_invented_scoring(self):
        for node_id, mapping in self.builder.mapping.items():
            if mapping["source_block"]:
                q = self.builder.q[mapping["source_block"]]
                self.assertIn(q["followup_instruction"], self.nodes[node_id]["data"]["prompt"])
        self.assertIn(self.builder.q["4"]["text"], self.nodes["career_company"]["data"]["prompt"])
        self.assertIn(self.builder.q["5"]["text"], self.nodes["career_experience_select"]["data"]["prompt"])
        self.assertFalse(any(n["data"].get("extraction_enabled") for n in self.nodes.values()))
        prompts = "\n".join(n["data"]["prompt"] for n in self.nodes.values())
        self.assertNotIn("{{gathered_context", prompts)
        self.assertNotIn("я буду искать подходящие вакансии по всему интернету", prompts.lower())

    def test_deterministic_outputs_and_original_duration(self):
        with tempfile.TemporaryDirectory() as path:
            output = Path(path)
            generator.generate(HERE / "source", output)
            for name in ["workflow.json", "create-request.json", "update-request.json", "provenance.json", "initial-context.example.json"]:
                self.assertEqual((HERE / name).read_bytes(), (output / name).read_bytes(), name)
        settings = generator.read_json(HERE / "update-request.json")["workflow_configurations"]
        self.assertEqual(settings["max_call_duration"], 1800)
        self.assertFalse(settings["context_compaction_enabled"])
        provenance = generator.read_json(HERE / "provenance.json")
        self.assertEqual(provenance["runtime_requirements"]["passthrough_node_ids"], [])


if __name__ == "__main__":
    unittest.main()
