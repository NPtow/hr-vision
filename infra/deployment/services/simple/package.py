#!/usr/bin/env python3
"""record/check/prepare selected host sources; no install, build or deployment."""
import argparse
import ast
import fcntl
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
SERVICES = ("core", "zoom", "normalizer")
DONORS = {"core": ("materials_repository", "materials_storage", "materials_runtime", "materials_service", "domain", "feedback"),
          "zoom": ("zoom_media", "domain", "feedback", "media_runtime", "materials_http", "materials_capture"),
          "normalizer": ()}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def safe_source(relative):
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", relative) or any(x in (".", "..", "") for x in relative.split("/")):
        raise ValueError("unsafe selected path")
    path = ROOT / relative
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError("source must be a regular non-symlink file")
    return path


def selected(service):
    result = {"infra/deployment/services/simple/run.py": "run.py"}
    if service == "zoom":
        result["services/zoom-api/app.py"] = "src/zoom_api.py"
    else:
        project = "data-core" if service == "core" else "transcription"
        for path in sorted((ROOT / "services" / project / "src").rglob("*.py")):
            if "__pycache__" not in path.parts:
                result[path.relative_to(ROOT).as_posix()] = "src/" + path.relative_to(ROOT / "services" / project / "src").as_posix()
        result[f"services/{project}/pyproject.toml"] = "pyproject.toml"
    lock = "services/transcription/requirements.lock" if service == "normalizer" else "infra/deployment/services/requirements-runtime.lock"
    result[lock] = "runtime.lock"
    # Follow static sibling imports, including lazy imports; record/check makes
    # the resulting exact closure reviewable. Never vendor the portal app.
    pending = list(DONORS[service])
    if service == "core":
        for relative in result:
            if relative.startswith("services/data-core/src/"):
                pending.extend(local_imports(safe_source(relative)))
    visited = set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        if name == "app":
            raise ValueError("portal app.py cannot be a service donor")
        path = ROOT / "services/recruiting-api" / (name + ".py")
        if not path.is_file():
            continue
        visited.add(name)
        result[f"services/recruiting-api/{name}.py"] = f"compat/{name}.py"
        pending.extend(local_imports(path))
    return result


def local_imports(path):
    names = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            names.append(node.module.split(".")[0])
    return names


def manifest(service):
    return {"schema_version": 1, "service": service, "files": [
        {"source": source, "target": target, "sha256": sha(safe_source(source).read_bytes())}
        for source, target in sorted(selected(service).items())]}


def checked(service):
    recorded = json.loads((HERE / "manifests" / (service + ".json")).read_bytes())
    if recorded != manifest(service):
        raise ValueError("selected-source drift: review changes, then record again")
    return recorded


def prepare(service, output_root):
    output = Path(output_root)
    if (not output.is_absolute() or ".." in output.parts or output == Path("/") or
            output.resolve().is_relative_to(ROOT) or ROOT.is_relative_to(output.resolve()) or
            any(p.is_symlink() for p in (output, *output.parents))):
        raise ValueError("output must be an absolute non-symlink directory outside checkout")
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(output / ("." + service + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = checked(service)
        contents = {}
        for item in value["files"]:
            raw = safe_source(item["source"]).read_bytes()
            if sha(raw) != item["sha256"]:
                raise ValueError("source changed while packaging")
            contents[item["target"]] = raw
        contents["manifest.json"] = encode(value)
        contents["SHA256SUMS"] = "".join(f"{sha(raw)}  {name}\n" for name, raw in sorted(contents.items())).encode()
        sink = io.BytesIO()
        with gzip.GzipFile(fileobj=sink, mode="wb", mtime=0, filename="") as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for name, raw in sorted(contents.items()):
                    item = tarfile.TarInfo(name)
                    item.size, item.mode, item.mtime = len(raw), 0o644, 0
                    archive.addfile(item, io.BytesIO(raw))
        raw = sink.getvalue()
        release = sha(encode(value))[:16]
        archive_path = output / f"{service}-{release}.tar.gz"
        if archive_path.is_symlink() or archive_path.exists() and archive_path.read_bytes() != raw:
            raise ValueError("refuse replacing existing artifact")
        with tempfile.NamedTemporaryFile(dir=output, delete=False) as temporary:
            temporary.write(raw)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.replace(archive_path)
        return {"service": service, "release": release, "archive": str(archive_path), "sha256": sha(raw)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("record", "check", "prepare"))
    parser.add_argument("service", choices=SERVICES)
    parser.add_argument("--output-root")
    args = parser.parse_args()
    try:
        if args.action == "record":
            (HERE / "manifests").mkdir(exist_ok=True)
            (HERE / "manifests" / (args.service + ".json")).write_bytes(encode(manifest(args.service)))
            print("Selected source hashes recorded; review manifest diff")
        elif args.action == "check":
            checked(args.service)
            print(args.service + ": selected sources verified")
        else:
            if not args.output_root:
                parser.error("prepare needs --output-root")
            print(json.dumps(prepare(args.service, args.output_root), sort_keys=True))
    except (ValueError, OSError) as exc:
        parser.exit(2, "Rejected: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
