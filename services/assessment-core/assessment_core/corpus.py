"""Lossless, deterministic envelopes for already parsed assessment sources.

One supplied source unit is one chunk. This module does not parse documents,
select evidence, infer speakers, trim text, merge units, or impose size limits.
Missing optional metadata is normalized; explicit null roles remain null.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    """Serialize a JSON value deterministically without escaping Unicode.

    Serialization errors deliberately omit input values, which can contain
    private candidate material. NaN and infinity are not valid JSON values.
    """
    try:
        result = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
        result.encode("utf-8")
        return result
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        raise ValueError("value must be valid UTF-8 JSON") from None


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _object(value: Any, path: str, allowed: set[str], required: set[str]) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    if set(value) - allowed:
        raise ValueError(f"{path} contains unsupported fields")
    if required - set(value):
        raise ValueError(f"{path} is missing required fields")
    return value


def _string(value: Any, path: str, *, nonblank: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    if nonblank and not value.strip():
        raise ValueError(f"{path} must not be blank")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError(f"{path} must be valid UTF-8 text") from None
    return value


def _timestamp(value: Any, path: str) -> int | None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f"{path} must be a nonnegative integer or null")
    return value


def build_corpus(value: dict) -> dict:
    """Validate and fingerprint all supplied CV/interview units in input order.

    Identifiers are local to this candidate's corpus; unit IDs must be unique
    within their source. Unknown fields are rejected instead of silently lost.
    Optional role defaults to ``unknown``; other optional metadata defaults to
    null. Every unit, including an empty or whitespace-only unit, is retained,
    provided that the corpus contains at least one non-whitespace text unit.
    """
    value = _object(value, "corpus", {"candidate_id", "sources"}, {"candidate_id", "sources"})
    candidate_id = _string(value["candidate_id"], "candidate_id", nonblank=True)
    if not isinstance(value["sources"], list):
        raise ValueError("sources must be an array")

    sources: list[dict] = []
    chunks: list[dict] = []
    source_ids: set[str] = set()
    has_content = False
    for source_index, source_value in enumerate(value["sources"]):
        source_path = f"sources[{source_index}]"
        source = _object(
            source_value, source_path,
            {"source_id", "version", "kind", "units"},
            {"source_id", "version", "kind", "units"},
        )
        source_id = _string(source["source_id"], f"{source_path}.source_id", nonblank=True)
        if source_id in source_ids:
            raise ValueError(f"{source_path}.source_id duplicates an earlier source")
        source_ids.add(source_id)
        version = _string(source["version"], f"{source_path}.version", nonblank=True)
        kind = source["kind"]
        if not isinstance(kind, str) or kind not in ("cv", "interview"):
            raise ValueError(f"{source_path}.kind must be cv or interview")
        if not isinstance(source["units"], list):
            raise ValueError(f"{source_path}.units must be an array")

        units: list[dict] = []
        unit_ids: set[str] = set()
        for unit_index, unit_value in enumerate(source["units"]):
            unit_path = f"{source_path}.units[{unit_index}]"
            unit = _object(
                unit_value, unit_path,
                {"unit_id", "text", "role", "speaker", "start_ms", "end_ms"},
                {"unit_id", "text"},
            )
            unit_id = _string(unit["unit_id"], f"{unit_path}.unit_id", nonblank=True)
            if unit_id in unit_ids:
                raise ValueError(f"{unit_path}.unit_id duplicates an earlier unit in this source")
            unit_ids.add(unit_id)
            text = _string(unit["text"], f"{unit_path}.text")
            has_content = has_content or bool(text.strip())
            role = unit.get("role", "unknown")
            if role is not None and (
                not isinstance(role, str) or role not in ("candidate", "interviewer", "unknown")
            ):
                raise ValueError(f"{unit_path}.role must be candidate, interviewer, unknown, or null")
            if kind == "cv" and role == "interviewer":
                raise ValueError(f"{unit_path}.role cannot be interviewer for a CV")
            speaker = unit.get("speaker")
            if speaker is not None:
                speaker = _string(speaker, f"{unit_path}.speaker")
            start_ms = _timestamp(unit.get("start_ms"), f"{unit_path}.start_ms")
            end_ms = _timestamp(unit.get("end_ms"), f"{unit_path}.end_ms")
            if start_ms is not None and end_ms is not None and end_ms < start_ms:
                raise ValueError(f"{unit_path}.end_ms must not precede start_ms")

            normalized_unit = {
                "unit_id": unit_id, "text": text, "role": role, "speaker": speaker,
                "start_ms": start_ms, "end_ms": end_ms,
            }
            units.append(normalized_unit)
            chunk = {
                "source_id": source_id, "source_version": version, "kind": kind,
                **normalized_unit,
            }
            chunks.append({"chunk_id": "chunk_" + _sha256(chunk), **chunk})

        normalized_source = {
            "source_id": source_id, "version": version, "kind": kind, "units": units,
        }
        sources.append({
            "source_id": source_id, "version": version, "kind": kind,
            "sha256": _sha256(normalized_source),
        })

    if not has_content:
        raise ValueError("corpus must contain at least one non-whitespace text unit")
    result = {"candidate_id": candidate_id, "sources": sources, "chunks": chunks}
    return {**result, "sha256": _sha256(result)}
