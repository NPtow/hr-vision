#!/usr/bin/env python3
"""Offline recovery regression checks; only attachment executes, never network/Keychain."""
import datetime
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / ".build/targethunter-pilot/targethunter-pilot"
SCHEMA = Path.home() / "Library/Application Support/TG Signals/source-runs/source-81c10dd2-2cb0-4f15-b706-ae631162fb99.json"
MODULE = ROOT / "output/targethunter-discovery/vk_modules_list-eaeb0e97-e9fd-48c6-9b59-85a6eeae276b.json"
PROJECT = "6acaf3c667052cdb2f0e9e2f"
CUSTOM = "TG Signals · vk-company-pilot-v1 · brunoyam"
TASK = "1234567890abcdef12345678"


def stamp(offset=0):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=offset)).strftime("%Y-%m-%dT%H:%M:%SZ")


with tempfile.TemporaryDirectory(prefix="targethunter-recovery-test-") as directory:
    folder = Path(directory)
    previous = {
        "version": 1, "planID": "vk-company-pilot-v1", "projectID": PROJECT, "taskModule": "vk/2",
        "entries": [{"companyID": "brunoyam", "companyName": "Бруноям", "query": "Бруноям", "customName": CUSTOM,
                     "startedAt": stamp(-300), "state": "failed", "createResponse": "original-error.json"}],
    }
    journal = folder / "pilot-journal.json"
    journal.write_text(json.dumps(previous), encoding="utf-8")
    (folder / "original-error.json").write_text(json.dumps({"result": {"content": [{"type": "text", "text": json.dumps({
        "status": "msg", "type": "error", "description": "Для запуска задачи, необходимо обновить подключение с ВКонтакте."
    })}]}}), encoding="utf-8")
    receipt = folder / "vk_tasks_list-fixture.json"

    def save_check(tasks, *, date=None, project=PROJECT, status="done"):
        receipt.write_text(json.dumps({"version": 1, "provider": "targetHunter", "operation": "vk_tasks_list",
                                     "arguments": {"project_id": project}, "retrievedAt": date or stamp(),
                                     "result": {"content": [{"type": "text", "text": json.dumps({"status": status, "tasks": tasks})}]}}), encoding="utf-8")

    def run(*extra, ok=True):
        args = [str(CLI), "--schema-run", str(SCHEMA), "--module-run", str(MODULE), "--output-dir", str(folder), "--recover-from", str(receipt), *extra]
        result = subprocess.run(args, capture_output=True, text=True)
        assert (result.returncode == 0) == ok, result.stdout + result.stderr
        return [json.loads(line) for line in result.stdout.splitlines()]

    save_check([])
    before = {p: p.read_bytes() for p in folder.iterdir()}
    plan = run()[0]
    assert plan["action"] == "create_one_after_explicit_absence" and plan["maximumNewTasks"] == 1
    assert before == {p: p.read_bytes() for p in folder.iterdir()}
    save_check([], project="wrong-project")
    run(ok=False)
    save_check([], date=stamp(-1800))
    run(ok=False)
    save_check([], status="msg")
    run(ok=False)
    save_check([{"id": TASK}])  # Unknown naming format cannot prove absence.
    run(ok=False)
    save_check([{"id": TASK, "custom_name": CUSTOM}, {"id": "abcdef1234567890abcdef12", "custom_name": CUSTOM}])
    run(ok=False)

    # An existing task is attached locally without constructing a network client or loading a key.
    save_check([{"id": TASK, "custom_name": CUSTOM, "status": "SUCCESS"}])
    plan = run()[0]
    assert plan["action"] == "attach_existing_task" and plan["maximumNewTasks"] == plan["maximumHTTPRequests"] == 0
    output = run("--execute")
    assert output[-1]["event"] == "existing_task_attached"
    updated = json.loads(journal.read_text())
    entry = updated["entries"][0]
    assert entry["state"] == "created" and entry["taskID"] == TASK and entry["recoveryCount"] == 1
    assert len(entry["recoveryHistory"]) == 1
    assert json.loads((folder / entry["recoveryHistory"][0]).read_text()) == previous
    assert entry["createResponse"] == "original-error.json"
    run(ok=False)  # The same recovery is not accepted twice.

    # One more attempt is possible only after a NEW explicit working-VK confirmation.
    second_previous = json.loads(json.dumps(updated))
    second_entry = second_previous["entries"][0]
    second_entry["state"] = "failed"
    second_entry.pop("taskID")
    second_entry["startedAt"] = stamp(-30)
    journal.write_text(json.dumps(second_previous), encoding="utf-8")
    save_check([])
    run(ok=False)  # Default --recover-from still blocks count=1.
    plan = run("--confirm-vk-reconnected")[0]
    assert plan["maximumNewTasks"] == 1 and plan["recoveryNumber"] == 2
    assert plan["explicitVKWorkingConfirmation"] is True
    save_check([], date=stamp(-1800))
    run("--confirm-vk-reconnected", ok=False)  # New confirmation never bypasses freshness.
    save_check([{"id": TASK, "custom_name": CUSTOM, "status": "SUCCESS"}])
    output = run("--confirm-vk-reconnected", "--execute")  # Still only local attachment, zero API.
    twice = json.loads(journal.read_text())["entries"][0]
    assert twice["recoveryCount"] == 2 and len(twice["recoveryHistory"]) == 2
    assert "Подключение работает" in twice["recoveryReason"]
    assert json.loads((folder / twice["recoveryHistory"][-1]).read_text()) == second_previous
    twice_failed = json.loads(journal.read_text())
    twice_failed["entries"][0]["state"] = "failed"
    twice_failed["entries"][0].pop("taskID")
    journal.write_text(json.dumps(twice_failed), encoding="utf-8")
    run(ok=False)
    run("--confirm-vk-reconnected", ok=False)  # Count=2 cannot be retried, even with the flag.

with tempfile.TemporaryDirectory(prefix="targethunter-create-reconcile-test-") as directory:
    folder = Path(directory)
    receipt = folder / "vk_task_create-fixture.json"
    journal = folder / "pilot-journal.json"
    previous = {
        "version": 1, "planID": "vk-company-pilot-v1", "projectID": PROJECT, "taskModule": "vk/2",
        "entries": [{"companyID": "brunoyam", "companyName": "Бруноям", "query": "Бруноям", "customName": CUSTOM,
                     "startedAt": stamp(-300), "state": "failed", "createResponse": receipt.name,
                     "failure": "recovery outcome requires review; no automatic retry", "recoveryCount": 2,
                     "recoveryHistory": ["older-snapshot-1.json", "older-snapshot-2.json"]}],
    }
    journal.write_text(json.dumps(previous), encoding="utf-8")
    arguments = {"project_id": PROJECT, "task_module": "vk/2", "custom_name": CUSTOM,
                 "params": {"input-type-task": "none_none_array_query", "input-from-task": "hand",
                            "none_none_array_query-task": "Бруноям", "exact": True, "where_find": ["name"]}}
    success = {"status": "msg", "type": "success", "name": "Задача создана!",
               "task": {"id": TASK, "name": CUSTOM, "project_id": PROJECT, "module": "vk/2", "status": 0}}

    def save_create(body, *, args=arguments, date=None, is_error=False):
        receipt.write_text(json.dumps({"version": 1, "provider": "targetHunter", "operation": "vk_task_create",
                                      "arguments": args, "retrievedAt": date or stamp(),
                                      "result": {"isError": is_error, "content": [{"type": "text", "text": json.dumps(body)}]}}), encoding="utf-8")

    def reconcile(*extra, ok=True):
        result = subprocess.run([str(CLI), "--schema-run", str(SCHEMA), "--module-run", str(MODULE),
                                 "--output-dir", str(folder), "--reconcile-create", str(receipt), *extra], capture_output=True, text=True)
        assert (result.returncode == 0) == ok, result.stdout + result.stderr
        return [json.loads(line) for line in result.stdout.splitlines()]

    save_create(success)
    before = {p: p.read_bytes() for p in folder.iterdir()}
    plan = reconcile()[0]
    assert plan["taskID"] == TASK and plan["maximumNewTasks"] == plan["maximumHTTPRequests"] == 0
    assert before == {p: p.read_bytes() for p in folder.iterdir()}
    save_create(dict(success, type="error"))
    reconcile(ok=False)  # A real msg/error must not adopt even if an ID is present.
    save_create(dict(success, type="warning"))
    reconcile(ok=False)
    save_create(success, is_error=True)
    reconcile(ok=False)
    for field, wrong in [("project_id", "wrong-project"), ("module", "vk/4"), ("name", "Other company"), ("id", "invalid")]:
        save_create(dict(success, task=dict(success["task"], **{field: wrong})))
        reconcile(ok=False)
    save_create(dict(success, task=dict(success["task"], task_id="abcdef1234567890abcdef12")))
    reconcile(ok=False)  # Ambiguous task IDs cannot be adopted.
    save_create(success, args=dict(arguments, project_id="wrong-project"))
    reconcile(ok=False)
    save_create(success, date=stamp(-600))
    reconcile(ok=False)
    save_create({"status": True, "data": {"id": TASK}})
    assert reconcile()[0]["taskID"] == TASK  # Preserve the previously supported explicit success envelope.
    save_create(success)
    output = reconcile("--execute")  # Purely local reconciliation: no credential load or HTTP.
    assert output[-1]["event"] == "created_task_reconciled"
    entry = json.loads(journal.read_text())["entries"][0]
    assert entry["taskID"] == TASK and entry["state"] == "created" and "failure" not in entry
    assert entry["recoveryCount"] == 2 and entry["recoveryHistory"] == previous["entries"][0]["recoveryHistory"]
    assert entry["reconciledCreateResponse"] == receipt.name and len(entry["reconciliationHistory"]) == 1
    assert json.loads((folder / entry["reconciliationHistory"][0]).read_text()) == previous
    reconcile(ok=False)  # Already attached: cannot reconcile or create again.

    # Explicit result reads are limited to the known journal task and never run in dry-run.
    base = [str(CLI), "--schema-run", str(SCHEMA), "--module-run", str(MODULE), "--output-dir", str(folder)]
    before = {p: p.read_bytes() for p in folder.iterdir()}
    result = subprocess.run([*base, "--result-once", TASK], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    plan = json.loads(result.stdout)
    assert plan["event"] == "result_dry_run" and plan["maximumNewTasks"] == 0 and plan["maximumHTTPRequests"] == 3
    assert before == {p: p.read_bytes() for p in folder.iterdir()}
    for extra in [["--result-once", "abcdef1234567890abcdef12"], ["--result-once", TASK, "--poll-once"],
                  ["--result-once", TASK, "--reconcile-create", str(receipt)]]:
        assert subprocess.run([*base, *extra], capture_output=True, text=True).returncode != 0
    updated = json.loads(journal.read_text())
    updated["entries"][0]["resultResponse"] = "already-retrieved.json"
    updated["entries"][0].update(state="result_received", receivedResultCount=5, reportedResultCount=15,
                                 reportedCountResponse="vk_tasks_list-fixture.json", resultExtent="unknown; received count differs from task count")
    journal.write_text(json.dumps(updated), encoding="utf-8")
    assert subprocess.run([*base, "--result-once", TASK], capture_output=True, text=True).returncode != 0
    poll = subprocess.run([*base, "--poll-once"], capture_output=True, text=True)
    assert poll.returncode == 0, poll.stdout + poll.stderr
    plan = json.loads(poll.stdout)
    assert plan["maximumNewTasks"] == 0 and plan["maximumHTTPRequests"] == 3  # Handshake + list; no result re-fetch.
    assert json.loads(journal.read_text())["entries"][0]["receivedResultCount"] == 5

print("TargetHunter offline checks passed: recovery guards, fresh complete receipt requirements, immutable histories, msg/success task adoption, msg/error rejection, exact request/metadata validation and zero-network reconciliation; no API or credential reads.")
