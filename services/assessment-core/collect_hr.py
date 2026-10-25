#!/usr/bin/env python3
"""Operator-only, read-only collector check. Never submits an assessment.

Uses the installed HR CoreClient plus the portal-owned assessment_corpus
adapter. DB path and service credentials come only from trusted configuration.
Default output is an inventory of IDs/counts/hashes/issues, never private text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


def summary(snapshot):
    manifest = snapshot["manifest"]
    return {
        "candidate_id": snapshot["candidate_id"], "status": snapshot["status"],
        "revision": snapshot["revision"], "scope": manifest["scope"],
        "resume": {k: manifest["resume"][k] for k in
                   ("status", "characters", "text_sha256", "version") if k in manifest["resume"]},
        "source_count": manifest["source_count"], "unit_count": manifest["unit_count"],
        "text_characters": manifest["text_characters"], "issues": manifest["issues"],
        "conversations": [{
            "owner_kind": o["owner_kind"], "owner_id": o["owner_id"], "status": o["status"],
            "lifecycle_status": o["lifecycle_status"], "warnings": o["warnings"],
            "recordings": [{k: r[k] for k in ("recording_id", "kind", "transcript_id",
                "content_sha256", "unit_count", "characters") if k in r} for r in o["recordings"]],
        } for o in manifest["conversations"]],
    }


def check_parameter_visibility(snapshot, count=15):
    """Exercise the actual request builder locally with a SYNTHETIC rubric.

    This verifies source routing, not our working scales or candidate quality.
    No model client, queue or run store is constructed.
    """
    from assessment_core.corpus import build_corpus, canonical_json
    from assessment_core.engine import prepare
    if snapshot["status"] != "ready":
        return {"status": "not_run_incomplete_corpus", "model_calls": 0}
    rubric = {"rubric_id": "routing-check-only", "version": "synthetic-v1", "parameters": [
        {"parameter_id": "routing_test_" + str(i), "title": "Проверка передачи данных " + str(i),
         "definition": "Синтетическая проверка маршрутизации; оценивание не запускать.", "levels": [
             {"level_id": "A", "label": "A", "description": "Синтетический вариант A."},
             {"level_id": "B", "label": "B", "description": "Синтетический вариант B."}]} for i in range(count)]}
    plan = prepare(snapshot["corpus"], rubric)
    expected = build_corpus(snapshot["corpus"])
    state = plan["request"]["state"]
    if state != {"sources": expected["sources"], "chunks": expected["chunks"]}:
        raise ValueError("shared_corpus_mismatch")
    texts = [u["text"] for source in snapshot["corpus"]["sources"] for u in source["units"]]
    if texts != [chunk["text"] for chunk in state["chunks"]]:
        raise ValueError("source_text_mismatch")
    if len(plan["request"]["questions"]) != count or not all(
            "state.chunks" in q["instructions"] and set(q) == {"type", "instructions", "criteria"}
            for q in plan["request"]["questions"].values()):
        raise ValueError("parameter_visibility_mismatch")
    return {"status": "verified_without_model_call", "parameters": count,
            "shared_state_sha256": hashlib.sha256(canonical_json(state).encode("utf-8")).hexdigest(),
            "chunk_count": len(state["chunks"]), "request_bytes": len(canonical_json(plan["request"]).encode("utf-8")),
            "model_calls": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-id", action="append", required=True)
    parser.add_argument("--check-parameters", type=int, default=15)
    args = parser.parse_args(argv)
    if not 1 <= args.check_parameters <= 100:
        parser.error("--check-parameters must be between 1 and 100")
    try:
        runtime = Path(os.environ["ASSESSMENT_HR_RUNTIME"])
        if not runtime.is_absolute() or not (runtime / "core_source_client.py").is_file():
            raise ValueError("installed_hr_runtime_required")
        sys.path.insert(0, str(runtime))
        from core_source_client import CoreClient, ServiceHTTP
        # New portal module is separate from the mutable ASR checkout; this CLI
        # resolves its sibling source in the current artifact/repository.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "interface-lab" / "server"))
        from assessment_corpus import CandidateCorpusCollector, SQLiteResumeReader
        core = CoreClient(ServiceHTTP(os.environ["HR_MATERIALS_CORE_URL"],
                                      os.environ["HR_MATERIALS_CORE_KEY"]))
        approved_ids = frozenset(args.candidate_id)
        collector = CandidateCorpusCollector(core, SQLiteResumeReader(os.environ["ASSESSMENT_HR_DATABASE"]),
            authorize=lambda cid: cid in approved_ids)
        # This is an operator CLI, not an HTTP user-authorization substitute.
        results = []
        for cid in dict.fromkeys(args.candidate_id):
            try:
                snapshot = collector.collect(cid)
                result = summary(snapshot)
                result["parameter_visibility"] = check_parameter_visibility(snapshot, args.check_parameters)
            except Exception as exc:
                result = {"candidate_id": cid, "status": "error", **safe_error(exc)}
            results.append(result)
        print(json.dumps({"mode": "read_only", "model_calls": 0, "candidates": results},
                         ensure_ascii=False, indent=2))
        return 0 if all(r["status"] == "ready" for r in results) else 3
    except Exception as exc:
        # Source/DB/HTTP exceptions can contain paths, credentials or content;
        # only explicitly safe collector machine codes may reach stdout.
        print(json.dumps(safe_error(exc)))
        return 2


def safe_error(exc):
    safe = type(exc).__module__ == "assessment_corpus" and type(exc).__name__ == "CollectionError"
    return {"error": str(exc) if safe else "collection_failed", "error_type": type(exc).__name__}


if __name__ == "__main__":
    raise SystemExit(main())
