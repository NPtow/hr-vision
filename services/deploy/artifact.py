#!/usr/bin/env python3
"""Allowlisted, hash-checked, deterministic independent service build contexts.

record is a maintainer operation after reviewing source changes. prepare never
updates the source manifest or invokes Docker. Concurrent prepares share a lock.
"""
import argparse
import fcntl
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "services/deploy"
SERVICES = {"core": "data-core", "source": "source-daily"}
COMPAT = ("materials_repository.py", "materials_storage.py", "domain.py", "feedback.py")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_file(root, relative):
    path = PurePosixPath(relative)
    if (path.is_absolute() or not path.parts or path.as_posix() != relative or
            any(p in (".", "..") or not re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in path.parts)):
        raise ValueError("unsafe manifest path")
    target = root.joinpath(*path.parts)
    if any(p.is_symlink() for p in (target, *target.parents)):
        raise ValueError("symlink forbidden")
    if not target.is_file() or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("manifest path must be a regular source file")
    return target


def selected(service):
    if service not in SERVICES:
        raise ValueError("unknown service")
    project = ROOT / "services" / SERVICES[service]
    result = {f"services/deploy/Dockerfile.{service}": "Dockerfile",
              "services/deploy/requirements-runtime.lock": "requirements-runtime.lock",
              f"services/{SERVICES[service]}/pyproject.toml": "pyproject.toml"}
    for path in sorted((project / "src").rglob("*")):
        if path.is_symlink():
            raise ValueError("symlink in service source")
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if path.suffix not in (".py", ".json") or any(p.startswith(".") for p in path.relative_to(project).parts):
            raise ValueError("unreviewed source extension")
        result[path.relative_to(ROOT).as_posix()] = path.relative_to(project).as_posix()
    if service == "core":
        result.update({f"interface-lab/server/{name}": f"compat/{name}" for name in COMPAT})
    return dict(sorted(result.items()))


def manifest(service):
    files = [{"source": source, "target": target, "sha256": digest(safe_file(ROOT, source).read_bytes())}
             for source, target in selected(service).items()]
    return {"schema_version": 1, "service": service, "files": files}


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def check_lock():
    lock = (DEPLOY / "requirements-runtime.lock").read_text()
    for name in SERVICES.values():
        project = tomllib.loads((ROOT / "services" / name / "pyproject.toml").read_text())
        for requirement in project["project"]["dependencies"]:
            if not re.fullmatch(r"[a-zA-Z0-9_.-]+==[a-zA-Z0-9_.+-]+", requirement):
                raise ValueError("direct runtime dependency must have an exact pin")
            if not re.search(r"^" + re.escape(requirement.lower()) + r"(?:\s|$)", lock.lower(), re.M):
                raise ValueError("runtime lock does not match pyproject")


def checked(service):
    check_lock()
    recorded = json.loads((DEPLOY / "manifests" / f"{service}.json").read_bytes())
    actual = manifest(service)
    if recorded != actual:
        raise ValueError("source manifest drift; review changes and run record")
    return actual


def safe_output(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or path in (Path("/"), Path.home()):
        raise ValueError("output must be an explicit safe absolute directory")
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("symlink output forbidden")
    if path.resolve().is_relative_to(ROOT) or ROOT.is_relative_to(path.resolve()):
        raise ValueError("build output must be outside source checkout")
    path.mkdir(parents=True, exist_ok=True, mode=0o750)
    return path


def prepare(service, output):
    output = safe_output(output)
    lockdir = output / ".locks"
    lockdir.mkdir(mode=0o700, exist_ok=True)
    if lockdir.is_symlink():
        raise ValueError("symlink lock directory")
    lockpath = lockdir / f"{service}.lock"
    fd = os.open(lockpath, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = checked(service)
        data = {}
        for item in value["files"]:
            raw = safe_file(ROOT, item["source"]).read_bytes()
            if digest(raw) != item["sha256"]:
                raise ValueError("source changed during build preparation")
            data[item["target"]] = raw
        project = tomllib.loads(data["pyproject.toml"].decode())
        version = project["project"]["version"]
        if not re.fullmatch(r"[0-9][a-zA-Z0-9_.-]{0,63}", version):
            raise ValueError("unsafe independent service version")
        source_hash = digest(encoded(value))
        tag = f"md-materials-{service}:{version}-{source_hash[:12]}"
        filename = tag.replace(":", "-") + ".tar.gz"
        data["source-manifest.json"] = encoded(value)
        data["SHA256SUMS"] = "".join(f"{digest(raw)}  {name}\n" for name, raw in sorted(data.items())).encode()
        archive = io.BytesIO()
        with gzip.GzipFile(fileobj=archive, mode="wb", mtime=0, filename="") as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as tar:
                for name, raw in sorted(data.items()):
                    member = tarfile.TarInfo(name)
                    member.size, member.mode, member.mtime = len(raw), 0o644, 0
                    tar.addfile(member, io.BytesIO(raw))
        raw = archive.getvalue()
        target = output / filename
        if target.is_symlink():
            raise ValueError("symlink archive forbidden")
        if target.exists() and target.read_bytes() != raw:
            raise ValueError("refuse replacing an existing release artifact")
        if not target.exists():
            with tempfile.NamedTemporaryFile(dir=output, delete=False) as tmp:
                tmp.write(raw)
                tmp.flush()
                os.fsync(tmp.fileno())
                temporary = Path(tmp.name)
            try:
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        return {"service": service, "image": tag, "artifact": str(target), "sha256": digest(raw),
                "source_sha256": source_hash}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("record", "check", "prepare"))
    parser.add_argument("service", choices=SERVICES)
    parser.add_argument("--output-root")
    args = parser.parse_args()
    try:
        if args.action == "record":
            check_lock()
            target = DEPLOY / "manifests" / f"{args.service}.json"
            target.write_bytes(encoded(manifest(args.service)))
            print(f"Recorded {args.service} selected source manifest; review diff before building")
        elif args.action == "check":
            checked(args.service)
            print(f"{args.service}: selected source hashes and dependency pins verified")
        else:
            if not args.output_root:
                parser.error("prepare requires --output-root")
            print(json.dumps(prepare(args.service, args.output_root), sort_keys=True))
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Artifact rejected: {exc}\n")


if __name__ == "__main__":
    main()
