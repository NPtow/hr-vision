"""Private SQLite call ledger, adapted from the existing analytics service.

Persist the complete plan BEFORE one external call; persist the raw response
BEFORE deriving a presentation. Replaying saved results never calls a model.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import time

from .corpus import canonical_json
from .engine import digest, interpret, validate_plan
from .providers.jev import JevError


class RunUnavailable(Exception):
    pass


class RunStore:
    def __init__(self, path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, status TEXT NOT NULL, plan TEXT NOT NULL,
                response TEXT, response_sha256 TEXT, result TEXT, error TEXT,
                created REAL NOT NULL, updated REAL NOT NULL)""")

    @contextmanager
    def db(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, run_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise RunUnavailable("run_not_found")
        return {key: json.loads(row[key]) if key in ("plan", "response", "result", "error")
                and row[key] is not None else row[key] for key in row.keys()}

    @contextmanager
    def runtime_lock(self):
        """One service/CLI owner per database, including across different ports."""
        fd = os.open(str(self.path) + ".owner.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RunUnavailable("store_in_use") from None
            yield
        finally:
            os.close(fd)

    def submit(self, plan):
        """Queue a frozen plan once; repeated submissions reuse its existing row."""
        validate_plan(plan)
        encoded = canonical_json(plan)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT plan FROM runs WHERE id=?", (plan["run_id"],)).fetchone()
            if row is not None:
                if row["plan"] != encoded:
                    raise RunUnavailable("run_identity_conflict")
            else:
                pending = db.execute("SELECT COUNT(*) FROM runs WHERE status IN ('queued','sent')").fetchone()[0]
                if pending >= 100:
                    raise RunUnavailable("queue_full")
                now = time.time()
                db.execute("INSERT INTO runs VALUES (?, 'queued', ?, NULL, NULL, NULL, NULL, ?, ?)",
                           (plan["run_id"], encoded, now, now))
        return self.get(plan["run_id"])

    def next_queued(self):
        with self.db() as db:
            row = db.execute("SELECT id,plan FROM runs WHERE status='queued' ORDER BY created,id LIMIT 1").fetchone()
        if row is None:
            return None
        try:
            plan = json.loads(row["plan"])
            validate_plan(plan)
            if plan["run_id"] != row["id"]:
                raise ValueError("saved run identity mismatch")
        except (ValueError, TypeError, KeyError):
            with self.db() as db:
                db.execute("UPDATE runs SET status='failed',error=?,updated=? WHERE id=? AND status='queued'",
                           (canonical_json({"code": "invalid_saved_plan"}), time.time(), row["id"]))
            return None
        return plan

    def recover(self):
        """Startup-only recovery, with exclusive service ownership; no network I/O."""
        with self.db() as db:
            db.execute("UPDATE runs SET status='outcome_unknown',error=?,updated=? WHERE status='sent'",
                       (canonical_json({"code": "interrupted_provider_call"}), time.time()))
            saved = [row[0] for row in db.execute("SELECT id FROM runs WHERE status='response_saved'")]
        for run_id in saved:
            try:
                self.replay(run_id)
            except (ValueError, RunUnavailable, JevError):
                with self.db() as db:
                    db.execute("UPDATE runs SET status='failed',error=?,updated=? WHERE id=? AND status='response_saved'",
                               (canonical_json({"code": "invalid_saved_response"}), time.time(), run_id))

    def execute(self, plan, client):
        # A content identity includes candidate, source versions/content, exact
        # rubric, prompt and model. A repeated call cannot silently rescore it.
        validate_plan(plan)
        expected = digest({key: value for key, value in plan.items() if key != "run_id"})
        if plan.get("run_id") != expected:
            raise ValueError("plan identity mismatch")
        now = time.time()
        reuse = False
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT status,plan FROM runs WHERE id=?", (expected,)).fetchone()
            if old is not None:
                if old["plan"] != canonical_json(plan):
                    raise ValueError("run identity conflict")
                if old["status"] in ("completed", "response_saved"):
                    reuse = True
                elif old["status"] == "queued":
                    db.execute("UPDATE runs SET status='sent',updated=? WHERE id=? AND status='queued'", (now, expected))
                else:
                    raise RunUnavailable("run_" + old["status"] + "; automatic resubmission is disabled")
            else:
                db.execute("INSERT INTO runs VALUES (?, 'sent', ?, NULL, NULL, NULL, NULL, ?, ?)",
                           (expected, canonical_json(plan), now, now))
        if reuse:
            return self.replay(expected)
        try:
            response = client.evaluate(plan["request"])
        except JevError as error:
            with self.db() as db:
                db.execute("UPDATE runs SET status=?, error=?, updated=? WHERE id=? AND status='sent'",
                           ("outcome_unknown" if error.outcome_unknown else "failed",
                            canonical_json({"code": error.code, "status": error.status}), time.time(), expected))
            raise
        except Exception:
            with self.db() as db:
                db.execute("UPDATE runs SET status='outcome_unknown',error=?,updated=? WHERE id=? AND status='sent'",
                           (canonical_json({"code": "unexpected_provider_failure"}), time.time(), expected))
            raise RunUnavailable("unexpected_provider_failure; automatic resubmission is disabled") from None
        with self.db() as db:
            db.execute("UPDATE runs SET status='response_saved',response=?,response_sha256=?,updated=? WHERE id=? AND status='sent'",
                       (canonical_json(response), digest(response), time.time(), expected))
        return self.replay(expected)

    def replay(self, run_id):
        """Rebuild from a saved provider response; never evaluate or retry."""
        row = self.get(run_id)
        if row["status"] not in ("completed", "response_saved") or row["response"] is None:
            raise RunUnavailable("no_saved_response")
        plan = row["plan"]
        if plan.get("run_id") != run_id or digest({key: value for key, value in plan.items() if key != "run_id"}) != run_id:
            raise RunUnavailable("saved_plan_hash_mismatch")
        if digest(row["response"]) != row["response_sha256"]:
            raise RunUnavailable("saved_response_hash_mismatch")
        result = interpret(plan, row["response"])
        if row["status"] == "completed":
            if canonical_json(result) != canonical_json(row["result"]):
                raise RunUnavailable("saved_result_mismatch")
            return result
        with self.db() as db:
            db.execute("UPDATE runs SET status='completed', result=?, updated=? WHERE id=? AND status IN ('response_saved','completed')",
                       (canonical_json(result), time.time(), run_id))
        return result
