"""Verify a host archive before extraction; never extracts or starts services."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile


def verify(path, expected):
    raw = Path(path).read_bytes()
    if len(raw) > 16 * 1024**2 or not re.fullmatch(r"[a-f0-9]{64}", expected) or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("archive digest or size mismatch")
    with tarfile.open(path) as archive:
        members = archive.getmembers()
        if len(members) > 256 or sum(m.size for m in members) > 32 * 1024**2:
            raise ValueError("archive exceeds source limits")
        contents = {}
        for item in members:
            parts = PurePosixPath(item.name)
            if (not item.isfile() or item.name in contents or item.mode != 0o644 or parts.is_absolute() or
                    any(p in (".", "..") for p in parts.parts) or not re.fullmatch(r"[A-Za-z0-9_./-]+", item.name)):
                raise ValueError("unsafe archive member")
            contents[item.name] = archive.extractfile(item).read()
    manifest = json.loads(contents["manifest.json"])
    if manifest.get("schema_version") != 1 or manifest.get("service") not in ("core", "zoom", "normalizer"):
        raise ValueError("unknown source manifest")
    hashes = {item["target"]: item["sha256"] for item in manifest["files"]}
    if len(hashes) != len(manifest["files"]) or set(contents) != set(hashes) | {"manifest.json", "SHA256SUMS"}:
        raise ValueError("archive must match selected-source manifest exactly")
    for name, expected_hash in hashes.items():
        if hashlib.sha256(contents[name]).hexdigest() != expected_hash:
            raise ValueError("source hash mismatch")
    sums = "".join(f"{hashlib.sha256(value).hexdigest()}  {name}\n"
                   for name, value in sorted(contents.items()) if name != "SHA256SUMS")
    if contents["SHA256SUMS"] != sums.encode():
        raise ValueError("checksum manifest mismatch")
    return {"service": manifest["service"], "release": hashlib.sha256(contents["manifest.json"]).hexdigest()[:16]}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify.py ARCHIVE EXPECTED_SHA256")
    try:
        print(json.dumps(verify(sys.argv[1], sys.argv[2]), sort_keys=True))
    except (ValueError, OSError, KeyError, tarfile.TarError) as exc:
        raise SystemExit("Archive rejected: " + str(exc))
