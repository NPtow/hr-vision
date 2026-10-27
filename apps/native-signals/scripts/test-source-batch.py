#!/usr/bin/env python3
"""Offline CLI regression checks. Uses temporary data; never uses --execute or Keychain."""
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / ".build/source-batch/source-batch"
DATE = "2026-10-11T12:00:00Z"


def run(folder, *arguments, ok=True):
    result = subprocess.run([str(CLI), "--data-dir", str(folder), *arguments], capture_output=True, text=True)
    assert (result.returncode == 0) == ok, result.stdout + result.stderr
    return json.loads(result.stdout)


with tempfile.TemporaryDirectory(prefix="tg-source-batch-test-") as directory:
    folder = Path(directory)
    pilot = run(folder)
    assert pilot["selectedCompanies"] == pilot["pendingCompanies"] == 3
    assert [row["id"] for row in pilot["pilot"]] == ["brunoyam", "karpov", "setters"]
    assert pilot["queryVersion"] == "brand-v2"
    assert pilot["pilot"][1]["query"] == "karpov courses"
    exceptions = {row["id"]: row["query"] for row in pilot["queryExceptions"]}
    assert len(exceptions) >= 13  # catalog may grow; preserve established query exceptions
    assert exceptions["discovery-qa-guru"] == "QA.GURU"
    assert exceptions["discovery-pravo-courses"] == "Pravo.courses"
    assert "discovery-niidpo-ru" not in exceptions  # initials are not domain-like
    assert "discovery-school-lobach-ru" not in exceptions
    yandex = run(folder, "--provider", "yandex")
    assert yandex["queryVersion"] == "quoted-brand-v1" and yandex["queryExceptions"] == []
    assert not pilot["networkStarted"] and list(folder.iterdir()) == []
    full = run(folder, "--scope", "all")
    assert full["catalogCompanies"] == full["selectedCompanies"] >= 479
    assert full["requestsThisInvocation"] == 409  # request cap is independent of catalog size
    assert len(set(full["nextCompanyIDs"])) == 409
    assert not any(item.startswith("demo-") for item in full["nextCompanyIDs"])
    assert run(folder, "--scope", "all", "--max-requests", "3")["requestsThisInvocation"] == 3

    # A successful UI request, even with a different result limit, must not be billed again.
    runs = folder / "source-runs"
    runs.mkdir()
    (runs / "source-ui-fixture.json").write_text(json.dumps({
        "version": 1, "id": "source-ui-fixture", "provider": "serper",
        "request": {"companyID": "brunoyam", "companyName": "Бруноям", "query": '"Бруноям"', "limit": 7},
        "results": [], "tools": [], "rawResponse": '{"organic":[]}', "rawFormat": "json",
        "startedAt": DATE, "completedAt": DATE, "hasMore": False,
    }), encoding="utf-8")
    resumed = run(folder)
    assert resumed["successfulAlready"] == 1 and resumed["pendingCompanies"] == 2
    assert "brunoyam" not in resumed["nextCompanyIDs"]

    # A crash after marking started may have consumed a credit; it must be skipped on resume.
    journals = folder / "source-batch-journals"
    journals.mkdir()
    journal = journals / "serper-brand-v2.json"
    journal.write_text(json.dumps({
        "version": 1, "provider": "serper", "queryVersion": "brand-v2",
        "createdAt": DATE, "updatedAt": DATE,
        "attempts": [{"companyID": "karpov", "companyName": "karpov.courses", "query": 'karpov courses',
                      "startedAt": DATE, "state": "started"}],
    }), encoding="utf-8")
    before = {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    resumed = run(folder)
    assert resumed["successfulAlready"] == resumed["uncertainOrFailedSkipped"] == resumed["pendingCompanies"] == 1
    assert resumed["nextCompanyIDs"] == ["setters"]
    assert resumed["uncertainCompanyIDs"] == ["karpov"]
    assert before == {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}

    # The proven karpov UI query takes precedence even over an uncertain journal attempt.
    (runs / "source-karpov-ui-fixture.json").write_text(json.dumps({
        "version": 1, "id": "source-karpov-ui-fixture", "provider": "serper",
        "request": {"companyID": "karpov", "companyName": "karpov.courses", "query": "karpov courses", "limit": 10},
        "results": [], "tools": [], "rawResponse": '{"organic":[]}', "rawFormat": "json",
        "startedAt": DATE, "completedAt": DATE, "hasMore": False,
    }), encoding="utf-8")
    resumed = run(folder)
    assert resumed["successfulAlready"] == 2 and resumed["uncertainOrFailedSkipped"] == 0
    assert resumed["nextCompanyIDs"] == ["setters"]

    # Explicit cohort has its own immutable ID list + request cap and isolated journal.
    ids_file = folder / "ids.json"
    ids_file.write_text(json.dumps(["discovery-partaonline-ru", "discovery-insperia-ru"]), encoding="utf-8")
    explicit_args = ["--company-ids", str(ids_file), "--batch-id", "regression-cohort", "--request-cap", "3"]
    before = {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    explicit = run(folder, *explicit_args)
    assert explicit["scope"] == "explicit" and explicit["selectedCompanies"] == 2
    assert explicit["queryVersion"] == "company-context-v1"
    assert explicit["nextCompanyIDs"] == ["discovery-partaonline-ru", "discovery-insperia-ru"]
    assert explicit["requestsThisInvocation"] == 2 and explicit["requestCap"] == 3
    assert explicit["nextQueries"][0]["query"] == "PARTA ЕГЭ ОГЭ"
    assert not explicit["networkStarted"]
    assert run(folder, *explicit_args, "--phase", "disambiguation")["requestsThisInvocation"] == 0
    assert before == {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    assert run(folder, "--company-ids", str(ids_file), ok=False)["event"] == "stopped"
    assert run(folder, "--batch-id", "../escape", ok=False)["event"] == "stopped"
    assert run(folder, "--provider", "harvest", ok=False)["event"] == "stopped"

    # A zero primary response qualifies for one domain-only clarification. It is a distinct
    # request, shares the provider budget, and uncertain attempts still cannot be billed twice.
    (runs / "source-parta-fixture.json").write_text(json.dumps({
        "version": 1, "id": "source-parta-fixture", "provider": "serper",
        "request": {"companyID": "discovery-partaonline-ru", "companyName": "PARTA", "query": "PARTA ЕГЭ ОГЭ", "limit": 10},
        "results": [], "tools": [], "rawResponse": '{"organic":[]}', "rawFormat": "json",
        "startedAt": DATE, "completedAt": DATE, "hasMore": False,
    }), encoding="utf-8")
    new_journal = journals / "serper-regression-cohort.json"
    cohort_journal = {
        "version": 2, "provider": "serper", "queryVersion": "company-context-v1",
        "batchID": "regression-cohort", "selectedCompanyIDs": ["discovery-partaonline-ru", "discovery-insperia-ru"],
        "requestCap": 3, "createdAt": DATE, "updatedAt": DATE,
        "attempts": [{"companyID": "discovery-partaonline-ru", "companyName": "PARTA", "query": "PARTA ЕГЭ ОГЭ",
                      "phase": "primary", "startedAt": DATE, "completedAt": DATE, "state": "saved",
                      "runID": "source-parta-fixture", "resultCount": 0}],
    }
    new_journal.write_text(json.dumps(cohort_journal), encoding="utf-8")
    explicit = run(folder, *explicit_args)
    assert explicit["successfulAlready"] == 1 and explicit["requestsThisInvocation"] == 1
    clarification = run(folder, *explicit_args, "--phase", "disambiguation")
    assert clarification["nextQueries"] == [{"companyID": "discovery-partaonline-ru", "query": "site:partaonline.ru"}]
    cohort_journal["attempts"].append({"companyID": "discovery-partaonline-ru", "companyName": "PARTA",
        "query": "site:partaonline.ru", "phase": "disambiguation", "startedAt": DATE, "state": "started"})
    new_journal.write_text(json.dumps(cohort_journal), encoding="utf-8")
    clarification = run(folder, *explicit_args, "--phase", "disambiguation")
    assert clarification["uncertainOrFailedSkipped"] == 1 and clarification["requestsThisInvocation"] == 0
    assert run(folder, *explicit_args, "--request-cap", "4", ok=False)["event"] == "stopped"
    ids_file.write_text(json.dumps(["discovery-insperia-ru", "discovery-partaonline-ru"]), encoding="utf-8")
    assert run(folder, *explicit_args, ok=False)["event"] == "stopped"  # changing cohort under same namespace is rejected
    ids_file.write_text(json.dumps(["discovery-partaonline-ru", "discovery-partaonline-ru"]), encoding="utf-8")
    assert run(folder, *explicit_args, ok=False)["event"] == "stopped"
    ids_file.write_text(json.dumps(["discovery-econ-vsu-ru-bs"]), encoding="utf-8")
    assert run(folder, *explicit_args, ok=False)["event"] == "stopped"  # orphan ID is rejected

    journal.write_text("broken journal", encoding="utf-8")
    assert run(folder, ok=False)["event"] == "stopped"
    assert journal.read_text() == "broken journal"
    journal.unlink()
    (runs / "broken.json").write_text("broken run", encoding="utf-8")
    assert run(folder, ok=False)["event"] == "stopped"
    assert (runs / "broken.json").read_text() == "broken run"
    assert run(folder, "--max-requests", "410", ok=False)["event"] == "stopped"
    assert run(folder, "--provider", "targetHunter", ok=False)["event"] == "stopped"

print("source-batch offline checks passed: catalog growth with independent request cap, legacy policy and journal compatibility, canonical explicit cohort, immutable batch cap/IDs, deterministic clarification, dry-run no writes/network/Keychain, UI dedupe, uncertain resume, corruption preservation, invalid providers/arguments")
