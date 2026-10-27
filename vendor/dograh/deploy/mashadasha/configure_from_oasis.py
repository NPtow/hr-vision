#!/usr/bin/env python3
"""Prepare the private bootstrap input on outreach; never print credentials.

Run with sudo on the host. Mount the resulting file read-only in the API
container at /run/dograh-bootstrap.json. No Oasis setting is modified.
"""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import stat
import sys
import tempfile
from urllib.parse import urlsplit


def read_environment(path: Path) -> dict[str, str]:
    """Parse assignment values as data, without shell expansion or execution."""
    result = {}
    for line in path.read_text().splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not match:
            continue
        name, raw = match.groups()
        values = shlex.split(raw, comments=True, posix=True)
        if len(values) > 1:
            raise ValueError("Environment contains an unquoted multi-word value")
        result[name] = values[0] if values else ""
    return result


def read_private_json(path: Path) -> dict:
    if path.is_symlink():
        raise ValueError("Private input must not be a symbolic link")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("Private input must be a regular file with mode 600")
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("Private input must contain a JSON object")
    return data


def prepare(source: Path, output: Path) -> dict:
    env = read_environment(source)
    required = (
        "OPENAI_COMPATIBLE_LLM_URL",
        "OPENAI_COMPATIBLE_LLM_API_KEY",
        "DEEPGRAM_API_KEY",
    )
    if not all(env.get(key) for key in required):
        raise ValueError("Oasis LLM or Deepgram configuration is incomplete")
    url = urlsplit(env[required[0]])
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
        raise ValueError("LLM URL must be an HTTP(S) endpoint without embedded credentials")

    previous = read_private_json(output) if output.exists() or output.is_symlink() else {}
    # The initial private file used .local, which EmailStr rejects. Correct
    # that pre-account-creation value while retaining the generated password.
    if previous.get("email", "nikita@dograh.example.com") not in {
        "nikita@dograh.example.com", "nikita@dograh.local"
    }:
        raise ValueError("Existing bootstrap file belongs to a different account")
    password = previous.get("password") or secrets.token_urlsafe(32)
    if not isinstance(password, str) or len(password.encode()) < 16 or len(password.encode()) > 72:
        raise ValueError("Bootstrap password must be between 16 and 72 UTF-8 bytes")

    providers = dict(previous.get("providers", {}))
    providers["llm"] = {
        "provider": "openai",
        "model": "gpt-6.1-sol",
        "base_url": env["OPENAI_COMPATIBLE_LLM_URL"].rstrip("/"),
        "api_key": env["OPENAI_COMPATIBLE_LLM_API_KEY"],
    }
    providers["stt"] = {
        "provider": "deepgram",
        "model": "nova-3-general",
        "language": "ru",
        "base_url": "https://api.deepgram.com",
        "api_key": env["DEEPGRAM_API_KEY"],
    }
    # Do not invent TTS credentials. A later owner-supplied provider may be
    # placed in this private JSON and is preserved on subsequent runs.
    if env.get("ELEVENLABS_API_KEY") and not providers.get("tts"):
        providers["tts"] = {
            "provider": "elevenlabs",
            "model": "eleven_flash_v2_5",
            "voice": "21m00Tcm4TlvDq8ikWAM",
            "speed": 1.0,
            "base_url": "https://api.elevenlabs.io",
            "api_key": env["ELEVENLABS_API_KEY"],
        }
    if providers.get("tts") is not None and not providers["tts"].get("api_key"):
        raise ValueError("Configured TTS provider has no API key")
    data = {
        **previous,
        "version": 1,
        "email": "nikita@dograh.example.com",
        "password": password,
        "providers": providers,
    }

    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stat.S_IMODE(output.parent.stat().st_mode) & 0o077:
        raise ValueError("Bootstrap directory must be private (mode 700)")
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".bootstrap-", dir=output.parent)
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return {"credentials_file": str(output), "email": data["email"],
            "password_preserved": bool(previous.get("password")),
            "llm_configured": True, "stt_configured": True,
            "tts_configured": bool(providers.get("tts")), "runtime_verified": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("/etc/oasis/env"))
    parser.add_argument("--output", type=Path, default=Path("/var/lib/dograh/bootstrap.json"))
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.source, args.output)))
        return 0
    except Exception as exc:
        # Exceptions from parsers or clients may contain input values.
        print(f"Private bootstrap preparation failed ({type(exc).__name__}); no credentials printed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
