"""Local entry point. Source loading/auth belong to the HR adapter, not this CLI."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

from .engine import prepare
from .providers.jev import JevClient, JevConfig, JevError
from .runstore import RunStore, RunUnavailable


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("non-finite JSON number")


def read(path):
    with Path(path).open(encoding="utf-8") as source:
        return json.load(source, object_pairs_hook=_unique, parse_constant=_invalid_constant)


def write(value, path=None):
    raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if path:
        fd = os.open(Path(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            target.write(raw)
    else:
        print(raw, end="")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Full-corpus candidate assessment — local development entry point")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run"):
        command = commands.add_parser(name)
        command.add_argument("--corpus", required=True)
        command.add_argument("--rubric", required=True)
        command.add_argument("--model", default="jev-1.14.0")
        command.add_argument("--output")
    replay = commands.add_parser("replay")
    replay.add_argument("run_id")
    replay.add_argument("--output")
    recover = commands.add_parser("recover-rejection", help="Offline recovery from retained provider bytes; no POST")
    recover.add_argument("run_id")
    recover.add_argument("--expected-body-sha256", required=True)
    recover.add_argument("--output", help="Private summary JSON; never exports candidate material")
    commands.choices["plan"].add_argument("--request-only", action="store_true")
    default_db = str(Path.home() / ".local/share/hr-vision-assessment-core/runs.sqlite3")
    for name in ("run", "replay", "recover-rejection"):
        commands.choices[name].add_argument("--db", default=default_db)
    args = parser.parse_args(argv)
    try:
        if args.command == "recover-rejection":
            if not Path(args.db).is_file():
                raise ValueError("existing database required")
            store = RunStore(args.db)
            store.recover_rejection(args.run_id, args.expected_body_sha256)
            audit = store.recovery_audit(args.run_id)
            result = {"run_id": args.run_id, "status": "completed", "provider_posts": 0,
                      "body_sha256": audit["body_sha256"], "plan_sha256": audit["plan_sha256"],
                      "response_sha256": audit["response_sha256"], "audit_phase": audit["phase"]}
        elif args.command == "replay":
            result = RunStore(args.db).replay(args.run_id)
        else:
            plan = prepare(read(args.corpus), read(args.rubric), model=args.model)
            if args.command == "plan":
                result = plan["request"] if args.request_only else plan
            else:
                client = JevClient(JevConfig.from_environment(model=args.model))
                store = RunStore(args.db)
                with store.runtime_lock():
                    result = store.execute(plan, client)
        write(result, args.output)
        return 0
    except JevError as error:
        print(json.dumps({"error": error.code, "http_status": error.status,
                          "outcome_unknown": error.outcome_unknown}), file=sys.stderr)
        return 2
    except (ValueError, TypeError, KeyError, sqlite3.Error, RunUnavailable):
        # Validation exceptions can originate in Python's JSON reader. Do not
        # echo its source excerpt, which can include candidate material.
        print('{"error":"invalid_input_or_unavailable_run"}', file=sys.stderr)
        return 2
    except OSError:
        print('{"error":"local_storage_error"}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
