#!/usr/bin/env python3
"""Offline safety checks. Temporary fixtures only; no Keychain or network access."""
import copy
import fcntl
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / ".build/serper-refine/serper-refine"
DATE = "2026-10-11T12:00:00Z"
VERSION = "serper-brand-domain-v1"


def invoke(folder, plan, *args, ok=True):
    process = subprocess.run(
        [str(CLI), "--data-dir", str(folder), "--plan", str(plan), *args],
        text=True, capture_output=True, timeout=10,
    )
    assert (process.returncode == 0) == ok, process.stdout + process.stderr
    return [json.loads(line) for line in process.stdout.splitlines()]


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def fixture_run(entry, run_id=None, refined=False):
    return {
        "version": 1, "id": run_id or entry["previousRunID"], "provider": "serper",
        "request": {"companyID": entry["companyID"], "companyName": entry["companyName"],
                    "query": entry["plannedQuery"] if refined else entry["previousQuery"], "limit": 7},
        "results": [], "tools": [], "rawResponse": '{"organic":[]}', "rawFormat": "json",
        "startedAt": DATE, "completedAt": DATE, "hasMore": False,
    }


def snapshot(folder):
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


entries = [
    {"companyID": company_id, "companyName": name, "groundingHostname": domain,
     "previousRunID": f"source-{company_id}-first", "previousQuery": f'"{name}"',
     "queryVersion": VERSION, "plannedQuery": f"{name} обучение {domain}", "limit": 10, "executed": False}
    for company_id, name, domain in [
        ("brunoyam", "Бруноям", "brunoyam.com"),
        ("karpov", "karpov.courses", "karpov.courses"),
        ("setters", "SETTERS Education", "setters.education"),
    ]
]
base = {"provider": "serper", "queryVersion": VERSION, "status": "prepared_only_no_network",
        "plannedRequestCount": len(entries), "entries": entries}

with tempfile.TemporaryDirectory(prefix="tg-serper-refine-test-") as directory:
    temp = Path(directory)
    folder = temp / "data"
    plan = temp / "plan.json"
    write(plan, base)
    for entry in entries:
        write(folder / "source-runs" / (entry["previousRunID"] + ".json"), fixture_run(entry))
    before = snapshot(temp)
    dry = invoke(folder, plan)[0]
    assert dry["selectedCompanies"] == dry["pendingCompanies"] == 3
    assert dry["requestsThisInvocation"] == 3 and not dry["networkStarted"]
    assert dry["nextCompanyIDs"] == ["brunoyam", "karpov", "setters"]
    assert before == snapshot(temp), "Dry-run wrote files"
    assert invoke(folder, plan, "--max-requests", "1")[0]["requestsThisInvocation"] == 1

    # Existing exact query (including a UI result with limit7) prevents spending again.
    write(folder / "source-runs/source-refined-ui.json", fixture_run(entries[0], "source-refined-ui", refined=True))
    dry = invoke(folder, plan)[0]
    assert dry["successfulAlready"] == 1 and dry["pendingCompanies"] == 2
    assert "brunoyam" not in dry["nextCompanyIDs"]

    # A started request in ANY other Serper journal is skipped despite no response file.
    write(folder / "source-batch-journals/another-version.json", {
        "version": 1, "provider": "serper", "queryVersion": "another-version",
        "attempts": [{"companyID": "karpov", "query": entries[1]["plannedQuery"], "state": "started"}],
    })
    dry = invoke(folder, plan)[0]
    assert dry["uncertainOrFailedSkipped"] == 1 and dry["nextCompanyIDs"] == ["setters"]

    # Own failed attempt is durable, never retried; successful history overrides uncertainty.
    own_journal = folder / f"source-batch-journals/{VERSION}.json"
    own = {
        "version": 1, "provider": "serper", "queryVersion": VERSION, "createdAt": DATE, "updatedAt": DATE,
        "attempts": [{"companyID": "setters", "companyName": "SETTERS Education", "query": entries[2]["plannedQuery"],
                      "previousRunID": entries[2]["previousRunID"], "startedAt": DATE, "completedAt": DATE,
                      "state": "failed", "errorCode": "http_400"}],
    }
    write(own_journal, own)
    before = snapshot(temp)
    dry = invoke(folder, plan)[0]
    assert dry["uncertainOrFailedSkipped"] == 2 and dry["requestsThisInvocation"] == 0
    # Execute with zero eligible requests must return BEFORE Keychain. It creates only lockfile.
    zero = invoke(folder, plan, "--execute")[0]
    assert zero["requestsThisInvocation"] == 0 and zero["event"] == "plan_execute"
    assert all(snapshot(temp)[name] == data for name, data in before.items())
    write(folder / "source-runs/source-refined-setters.json", fixture_run(entries[2], "source-refined-setters", refined=True))
    dry = invoke(folder, plan)[0]
    assert dry["successfulAlready"] == 2 and dry["uncertainOrFailedSkipped"] == 1

    # The same OS lock rejects --execute before any Keychain or network operations.
    with (folder / ".source-batch.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        refused = invoke(folder, plan, "--execute", ok=False)
        assert len(refused) == 1 and "lock" in refused[0]["message"]
        fcntl.flock(lock, fcntl.LOCK_UN)

    # Changing a previously attempted query under the same version cannot silently re-spend.
    changed = copy.deepcopy(base)
    changed["entries"][2]["plannedQuery"] = "SETTERS школа setters.education"
    write(plan, changed)
    assert invoke(folder, plan, ok=False)[0]["event"] == "stopped"
    write(plan, base)

    for field, value in [
        ("companyID", "unknown-company"), ("companyName", "Wrong company"),
        ("groundingHostname", "unrelated.example"), ("previousRunID", "source-not-present"),
        ("previousQuery", "unrelated"), ("queryVersion", "unreviewed-v2"),
        ("limit", 11), ("executed", True), ("plannedQuery", "no grounding hostname"),
        ("plannedQuery", "Бруноям\nобучение brunoyam.com"),
    ]:
        invalid = copy.deepcopy(base)
        invalid["entries"][0][field] = value
        write(plan, invalid)
        assert invoke(folder, plan, ok=False)[0]["event"] == "stopped", field
    invalid = copy.deepcopy(base)
    invalid["entries"].append(invalid["entries"][0])
    invalid["plannedRequestCount"] += 1
    write(plan, invalid)
    assert invoke(folder, plan, ok=False)[0]["event"] == "stopped"
    write(plan, base)

    own_journal.write_text("corrupt", encoding="utf-8")
    assert invoke(folder, plan, ok=False)[0]["event"] == "stopped"
    assert own_journal.read_text() == "corrupt"
    own_journal.unlink()
    corrupt_run = folder / "source-runs/corrupt.json"
    corrupt_run.write_text("corrupt", encoding="utf-8")
    assert invoke(folder, plan, ok=False)[0]["event"] == "stopped"
    assert corrupt_run.read_text() == "corrupt"
    assert invoke(folder, plan, "--max-requests", "410", ok=False)[0]["event"] == "stopped"

print("serper-refine offline checks passed: read-only preview, catalog/history grounding, exact UI dedupe, cross-journal uncertain skip, no failed retries, common OS lock, zero-request credential gate, plan-change refusal, corruption preservation")
