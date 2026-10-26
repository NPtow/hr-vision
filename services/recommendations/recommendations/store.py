"""Service-owned SQLite snapshots and immutable results; no external database."""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import time

from . import __version__
from .engine import canonical_json, digest, recommend


class StoreUnavailable(Exception):
    pass


class ResultStore:
    def __init__(self, path, *, rubric=None):
        self.path = Path(path).expanduser()
        self.rubric = rubric
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS recommendations (
                id TEXT PRIMARY KEY, input TEXT NOT NULL, result TEXT NOT NULL,
                result_sha256 TEXT NOT NULL, service_version TEXT NOT NULL,
                created REAL NOT NULL)""")

    @contextmanager
    def db(self):
        # GET/readiness must never recreate a lost database as an empty file.
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw", uri=True, timeout=1)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def runtime_lock(self):
        fd = os.open(str(self.path) + ".owner.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StoreUnavailable("store_in_use") from None
            yield
        finally:
            os.close(fd)

    def submit(self, value):
        result = recommend(value, rubric=self.rubric)
        operation_id = result["recommendation_id"]
        encoded = canonical_json(value)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT input FROM recommendations WHERE id=?", (operation_id,)).fetchone()
            if row is not None:
                if row["input"] != encoded:
                    raise StoreUnavailable("identity_conflict")
            else:
                db.execute("INSERT INTO recommendations VALUES (?,?,?,?,?,?)",
                           (operation_id, encoded, canonical_json(result), digest(result), __version__, time.time()))
        # Commit precedes the response; GET never writes or re-runs calculation.
        return self.get(operation_id)

    def get(self, operation_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM recommendations WHERE id=?", (operation_id,)).fetchone()
        if row is None:
            raise StoreUnavailable("not_found")
        try:
            value, result = json.loads(row["input"]), json.loads(row["result"])
            if (digest({"service_version": row["service_version"], "input": value}) != operation_id
                    or digest(result) != row["result_sha256"] or result["recommendation_id"] != operation_id
                    or result["service_version"] != row["service_version"]):
                raise StoreUnavailable("integrity_error")
        except (ValueError, TypeError, KeyError, RecursionError):
            raise StoreUnavailable("integrity_error") from None
        return result

    def ready(self):
        """Check both reading and acquiring the write transaction needed by POST."""
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("SELECT id,input,result,result_sha256,service_version,created FROM recommendations LIMIT 1").fetchone()
            db.rollback()
        return True

    def metadata(self):
        with self.db() as db:
            count, latest = db.execute("SELECT COUNT(*),MAX(created) FROM recommendations").fetchone()
        return {"saved_results": count, "latest_created_unix": latest,
                "database_bytes": self.path.stat().st_size}

    def backup(self, destination):
        """SQLite online backup; never copy a live SQLite file with filesystem cp."""
        target = Path(destination).expanduser()
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            with self.db() as source:
                backup_db = sqlite3.connect(str(target))
                try:
                    source.backup(backup_db)
                    if backup_db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise StoreUnavailable("backup_integrity_error")
                finally:
                    backup_db.close()
            return {"status": "completed", "saved_results": self.metadata()["saved_results"]}
        except Exception:
            target.unlink(missing_ok=True)
            raise

    @classmethod
    def restore(cls, source, destination):
        """Restore only into a new path. Caller stops service before switching DB."""
        original = Path(source).expanduser().resolve(strict=True)
        # Read-only source avoids creating a missing source or updating backup.
        source_db = sqlite3.connect(original.as_uri() + "?mode=ro", uri=True)
        try:
            if source_db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise StoreUnavailable("backup_integrity_error")
            source_db.execute("SELECT id,input,result,result_sha256,service_version,created FROM recommendations LIMIT 1")
            target = Path(destination).expanduser()
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            try:
                target_db = sqlite3.connect(str(target))
                try:
                    source_db.backup(target_db)
                finally:
                    target_db.close()
                restored = cls(target)
                with restored.db() as db:
                    operation_ids = [row[0] for row in db.execute("SELECT id FROM recommendations")]
                for operation_id in operation_ids:
                    restored.get(operation_id)
                return {"status": "completed", "saved_results": len(operation_ids)}
            except Exception:
                target.unlink(missing_ok=True)
                raise
        finally:
            source_db.close()
