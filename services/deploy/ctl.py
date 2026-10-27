#!/usr/bin/env python3
"""Validate private configuration and run the bounded Compose service stack.

Never shell-source env files and never print rendered Compose configuration:
it contains credentials. config uses --quiet and readiness uses no token output.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
SERVICE_NAMES = ("core", "source-api", "source-worker", "source-reconcile")
CORE_KEYS = {"CORE_HR_DATABASE", "CORE_PRINCIPALS_JSON", "CORE_DEFAULT_CONNECTIONS_JSON",
             "CORE_S3_BUCKET", "CORE_S3_PREFIX", "CORE_S3_ENDPOINT", "CORE_S3_REGION"}
SOURCE_FIELDS = "api_keys daily_key daily_webhook_hmac connection_id default_storage_policy core_token transcription_url transcription_token enable_asr daily_transcription_model broker_url s3_bucket s3_prefix s3_endpoint s3_region max_attempts settle_attempts max_asr_polls retry_seconds lease_seconds max_transcript_bytes max_body_bytes max_artifacts legacy_materials".split()
AWS_KEYS = {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_DEFAULT_REGION"}
IMAGE_KEYS = {"CORE_IMAGE", "SOURCE_IMAGE", "TRANSCRIPTION_IMAGE", "HR_DB_UID", "HR_DB_GID"}
SOURCE_KEYS = {"SOURCE_DAILY_" + key.upper() for key in SOURCE_FIELDS}


def private_file(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("symlink configuration forbidden")
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o600 or st.st_uid != os.geteuid():
        raise ValueError("configuration files must be owned by caller and mode 0600")
    return path.read_text()


def read_env(path, allowed):
    result = {}
    for line in private_file(path).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in allowed or key in result:
            raise ValueError(f"unsupported or duplicate configuration key in {path.name}")
        if any(c in value for c in ("\x00", "\r", "`", "$")) or value != value.strip():
            raise ValueError(f"unsafe literal value in {path.name}")
        if "REPLACE" in value:
            raise ValueError(f"unconfigured placeholder in {path.name}")
        result[key] = value
    return result


def token(value):
    return isinstance(value, str) and len(value) >= 32 and value.isascii() and not any(c.isspace() for c in value)


def endpoint(value, *, loopback=False):
    if not value:
        return
    parsed = urlsplit(value)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment):
        raise ValueError("invalid service endpoint")
    if loopback and (parsed.hostname != "127.0.0.1" or parsed.scheme != "http"):
        raise ValueError("transcription must use explicit host-loopback HTTP endpoint")
    if not loopback and parsed.scheme != "https":
        raise ValueError("object storage endpoint requires HTTPS")


def validate(config_dir):
    config_dir = Path(config_dir)
    if not config_dir.is_absolute() or ".." in config_dir.parts:
        raise ValueError("configuration directory must be absolute")
    directory_stat = config_dir.stat()
    if directory_stat.st_uid != os.geteuid() or stat.S_IMODE(directory_stat.st_mode) & 0o077:
        raise ValueError("configuration directory must be owned by caller and mode 0700")
    images = read_env(config_dir / "compose.env", IMAGE_KEYS)
    for name, service in (("CORE_IMAGE", "core"), ("SOURCE_IMAGE", "source")):
        if not re.fullmatch(r"md-materials-" + service + r":[0-9][a-zA-Z0-9_.-]{0,63}-[a-f0-9]{12}", images.get(name, "")):
            raise ValueError("image must identify independent service version and selected-source hash")
    for key in ("HR_DB_UID", "HR_DB_GID"):
        if not re.fullmatch(r"[1-9][0-9]{2,8}", images.get(key, "")):
            raise ValueError("explicit nonroot existing database owner and GID required")
    core = read_env(config_dir / "core.env", CORE_KEYS | AWS_KEYS)
    source = read_env(config_dir / "source.env", SOURCE_KEYS | AWS_KEYS)
    if not re.fullmatch(r"/var/lib/hr-vision-api/[a-zA-Z0-9_-]+\.(?:sqlite3|sqlite|db)", core.get("CORE_HR_DATABASE", "")):
        raise ValueError("core database must be an existing HR state database in the fixed mount")
    principals = json.loads(core.get("CORE_PRINCIPALS_JSON", "[]"))
    if not isinstance(principals, list) or len(principals) < 2:
        raise ValueError("configure portal and source core principals")
    portal_tokens, source_tokens = [], []
    seen = set()
    connection = source.get("SOURCE_DAILY_CONNECTION_ID", "daily")
    for principal in principals:
        if not isinstance(principal, dict) or not token(principal.get("token")) or principal["token"] in seen:
            raise ValueError("invalid or duplicate core token")
        seen.add(principal["token"])
        if principal.get("role") == "portal":
            portal_tokens.append(principal["token"])
        elif principal.get("role") == "source" and principal.get("provider") == "daily" and principal.get("connection_id") == connection:
            source_tokens.append(principal["token"])
        else:
            raise ValueError("invalid core principal identity")
    if not portal_tokens or not source_tokens or source.get("SOURCE_DAILY_CORE_TOKEN") not in source_tokens:
        raise ValueError("source/core principal mismatch")
    callers = json.loads(source.get("SOURCE_DAILY_API_KEYS", "{}"))
    if not isinstance(callers, dict) or not callers or any(not token(k) or v != "portal" or k in seen for k, v in callers.items()):
        raise ValueError("source requires a distinct portal bearer token")
    if not source.get("SOURCE_DAILY_DAILY_KEY"):
        raise ValueError("source provider key required")
    broker = urlsplit(source.get("SOURCE_DAILY_BROKER_URL", ""))
    if (broker.scheme not in ("amqp", "amqps") or broker.hostname != "127.0.0.1" or
            broker.path != "/source-daily" or broker.username != "md-source-daily" or not broker.password or
            broker.query or broker.fragment or broker.port not in (5672, 5671, None)):
        raise ValueError("Rabbit must use dedicated loopback user and source-daily vhost")
    for env, prefix in ((core, "CORE_"), (source, "SOURCE_DAILY_")):
        endpoint(env.get(prefix + "S3_ENDPOINT", ""))
    if source.get("SOURCE_DAILY_DEFAULT_STORAGE_POLICY", "remote") not in ("remote", "owned"):
        raise ValueError("invalid retention policy")
    for field in ("S3_BUCKET", "S3_PREFIX", "S3_ENDPOINT", "S3_REGION"):
        if core.get("CORE_" + field, "materials" if field == "S3_PREFIX" else "") != source.get("SOURCE_DAILY_" + field, "materials" if field == "S3_PREFIX" else ""):
            raise ValueError("core/source object store configurations must agree")
    if source.get("SOURCE_DAILY_DEFAULT_STORAGE_POLICY", "remote") == "owned" and not source.get("SOURCE_DAILY_S3_BUCKET"):
        raise ValueError("owned retention requires configured object storage")
    endpoint(source.get("SOURCE_DAILY_TRANSCRIPTION_URL", ""), loopback=True)
    if source.get("SOURCE_DAILY_ENABLE_ASR", "false") not in ("false", "0", "true", "1"):
        raise ValueError("invalid ASR opt-in flag")
    if source.get("SOURCE_DAILY_ENABLE_ASR") in ("true", "1") and not source.get("SOURCE_DAILY_TRANSCRIPTION_URL"):
        raise ValueError("explicit ASR opt-in requires separately configured transcription endpoint")
    return config_dir, images, core


def validate_transcription(config_dir):
    config_dir = Path(config_dir)
    if not config_dir.is_absolute() or ".." in config_dir.parts:
        raise ValueError("configuration directory must be absolute")
    st = config_dir.stat()
    if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) & 0o077:
        raise ValueError("configuration directory must be owned by caller and mode 0700")
    images = read_env(config_dir / "compose.env", IMAGE_KEYS)
    if not re.fullmatch(r"md-transcription:[0-9][a-zA-Z0-9_.-]{0,63}-[a-f0-9]{12}", images.get("TRANSCRIPTION_IMAGE", "")):
        raise ValueError("transcription image must carry independent version and source hash")
    settings = read_env(config_dir / "transcription.env", {"TRANSCRIPTION_API_KEYS"})
    keys = json.loads(settings.get("TRANSCRIPTION_API_KEYS", "{}"))
    if not isinstance(keys, dict) or not keys or any(not isinstance(k, str) or not k or not token(v) for k, v in keys.items()):
        raise ValueError("normalization API requires distinct source keys")
    return config_dir, images, settings


def compose_command(config_dir, stack="materials"):
    project, filename = (("hr-vision-transcription", "transcription.production.yaml") if stack == "transcription"
                         else ("hr-vision-data-services", "compose.yaml"))
    return ["docker", "compose", "--project-name", project, "--env-file", str(config_dir / "compose.env"),
            "--file", str(HERE / filename)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", default="/etc/md-materials")
    parser.add_argument("--stack", choices=("materials", "transcription"), default="materials")
    parser.add_argument("action", choices=("validate", "config", "up", "stop", "status", "logs"))
    parser.add_argument("services", nargs="*")
    args = parser.parse_args()
    try:
        allowed = ("api",) if args.stack == "transcription" else SERVICE_NAMES
        if any(name not in allowed for name in args.services):
            raise ValueError("service outside selected deployment stack")
        config, _, core = (validate_transcription if args.stack == "transcription" else validate)(args.config_dir)
        if args.action == "validate":
            print("Configuration structure, paths, isolated queue and service identities validated; values hidden")
            return
        if args.action == "up":
            selected = args.services or allowed
            state_paths = []
            if args.stack == "transcription":
                state_paths.append(Path("/var/lib/md-transcription"))
            else:
                if "core" in selected:
                    state_paths.append(Path(core["CORE_HR_DATABASE"]))
                if any(name.startswith("source-") for name in selected):
                    state_paths.append(Path("/var/lib/md-source-daily"))
            for path in state_paths:
                if not path.exists() or any(p.is_symlink() for p in (path, *path.parents)):
                    raise ValueError("required persistent state path missing or symlinked")
            if args.stack == "materials" and "core" in selected:
                images = read_env(config / "compose.env", IMAGE_KEYS)
                db_stat = Path(core["CORE_HR_DATABASE"]).stat()
                if (db_stat.st_uid, db_stat.st_gid) != (int(images["HR_DB_UID"]), int(images["HR_DB_GID"])):
                    raise ValueError("core UID/GID must match existing private HR database owner")
        # Compose interpolation uses only this fresh environment and our env-file.
        # Do not inherit hostile COMPOSE_FILE, DOCKER_HOST or shell configuration.
        env = {"PATH": os.defpath + ":/usr/local/bin", "HOME": os.environ.get("HOME", "/root"),
               "MATERIALS_CONFIG_DIR": str(config)}
        command = compose_command(config, args.stack)
        actions = {"config": ["config", "--quiet"], "up": ["up", "--detach", "--wait", "--wait-timeout", "90"],
                   "stop": ["stop", "--timeout", "300"], "status": ["ps"], "logs": ["logs", "--tail", "100"]}
        command += actions[args.action]
        if args.action != "config":
            command += args.services
        # Mutations are serialized independently of source build locks.
        if args.action in ("up", "stop"):
            fd = os.open(config / f".{args.stack}.compose.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                result = subprocess.run(command, env=env, check=False)
        else:
            result = subprocess.run(command, env=env, check=False)
        raise SystemExit(result.returncode)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Configuration rejected: {exc}\n")


if __name__ == "__main__":
    main()
