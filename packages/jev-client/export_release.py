"""Freeze shared transport and rubric into an independent service release.

Only source bytes are copied. The manifest has no timestamps or local paths,
and an existing file with different content is rejected before any write.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def export_release(release_root: Path, *, rubric_name: str = "sales-v1.json") -> dict:
    """Return the deterministic manifest after freezing shared dependencies."""
    if rubric_name not in {"sales-v1.json", "analytics-baseline.json"}:
        raise ValueError("Unsupported rubric release filename")
    package_root = Path(__file__).resolve().parent
    repository = package_root.parents[1]
    rubric = (repository / "packages/assessment-contracts/rubrics/sales-v1.json").read_bytes()
    candidate_rubric = repository / "services/assessment-core/rubrics/analytics-baseline.json"
    if rubric != candidate_rubric.read_bytes():
        raise ValueError("Shared and candidate rubric catalogs differ")
    files = {"jev_client/__init__.py": (package_root / "jev_client/__init__.py").read_bytes(),
             "rubrics/" + rubric_name: rubric}
    manifest = {"contract_version": "assessment-shared-release-v1",
                "files": {name: hashlib.sha256(data).hexdigest()
                          for name, data in sorted(files.items())}}
    files["shared-components.json"] = (json.dumps(manifest, ensure_ascii=False,
        sort_keys=True, indent=2) + "\n").encode("utf-8")
    destination = Path(release_root).resolve()
    # Preflight the complete set, so a collision never produces a partial copy.
    for name, data in files.items():
        target = destination / name
        if target.is_symlink() or not target.parent.resolve().is_relative_to(destination):
            raise ValueError("Shared release target must stay inside release root")
        if target.exists() and (not target.is_file() or target.read_bytes() != data):
            raise ValueError("Shared release file already exists with different content: " + name)
    for name, data in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(data)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_root", type=Path)
    parser.add_argument("--rubric-name", choices=("sales-v1.json", "analytics-baseline.json"),
                        default="sales-v1.json")
    arguments = parser.parse_args()
    print(json.dumps(export_release(arguments.release_root, rubric_name=arguments.rubric_name),
                     sort_keys=True))


if __name__ == "__main__":
    main()
