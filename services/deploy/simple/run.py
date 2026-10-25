"""Host service entrypoint. Preserve only role-specific environment settings."""
import os
from pathlib import Path
import re
import sys

PORTS = {"core": 8093, "normalizer": 8094, "zoom": 8095}
ZOOM_KEYS = {"ZOOM_API_TOKEN", "ZOOM_API_TEMP_ROOT", "HR_ZOOM_ENABLED", "HR_ZOOM_CONNECTION_ID",
             "HR_ZOOM_HOST_MAP_JSON", "HR_ZOOM_DEFAULT_HOST_ID", "ZOOM_MEETING_SDK_CLIENT_ID",
             "ZOOM_MEETING_SDK_CLIENT_SECRET", "ZOOM_S2S_ACCOUNT_ID", "ZOOM_S2S_CLIENT_ID", "ZOOM_S2S_CLIENT_SECRET"}
CORE_KEYS = {"CORE_HR_DATABASE", "CORE_PRINCIPALS_JSON", "CORE_DEFAULT_CONNECTIONS_JSON",
             "CORE_S3_BUCKET", "CORE_S3_PREFIX", "CORE_S3_ENDPOINT", "CORE_S3_REGION",
             "HR_MATERIALS_STORAGE_BACKEND", "HR_MATERIALS_STORAGE_ROOT", "HR_MATERIALS_S3_BUCKET",
             "HR_MATERIALS_S3_PREFIX", "HR_MATERIALS_S3_ENDPOINT", "HR_MATERIALS_S3_REGION",
             "HR_CORE_COLLECTOR_ENABLED", "HR_MATERIALS_PROVIDERS", "HR_ZOOM_API_URL", "HR_ZOOM_API_TOKEN",
             "HR_ZOOM_ENABLED", "HR_ZOOM_CONNECTION_ID", "HR_TRANSCRIPTION_URL", "HR_TRANSCRIPTION_TOKEN",
             "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_DEFAULT_REGION"}
NORMALIZER_KEYS = {"TRANSCRIPTION_API_KEYS", "TRANSCRIPTION_DATA_DIR", "TRANSCRIPTION_MAX_INPUT_BYTES",
                   "TRANSCRIPTION_MAX_RESULT_BYTES"}


def environment(service, incoming):
    allowed = {"core": CORE_KEYS, "zoom": ZOOM_KEYS, "normalizer": NORMALIZER_KEYS}[service]
    result = {key: value for key, value in incoming.items() if key in allowed}
    result.update(PATH="/usr/local/bin:/usr/bin:/bin", LANG="C.UTF-8", PYTHONDONTWRITEBYTECODE="1",
                  PYTHONUNBUFFERED="1", AWS_EC2_METADATA_DISABLED="true")
    if service == "core":
        result.update(CORE_HOST="127.0.0.1", CORE_PORT="8093")
        if result.get("HR_CORE_COLLECTOR_ENABLED") == "1":
            if result.get("HR_MATERIALS_PROVIDERS") != "zoom" or result.get("HR_ZOOM_API_URL") != "http://127.0.0.1:8095":
                raise ValueError("core collector must use scoped Zoom HTTP adapter")
        db = result.get("CORE_HR_DATABASE", "")
        if not re.fullmatch(r"/var/lib/hr-vision-api/[a-zA-Z0-9_-]+\.(sqlite3|sqlite|db)", db):
            raise ValueError("core requires existing private HR SQLite path")
    elif service == "zoom":
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", result.get("ZOOM_API_TOKEN", "")):
            raise ValueError("Zoom requires a separate URL-safe service token")
        result["ZOOM_API_TEMP_ROOT"] = "/var/cache/hr-vision-zoom"
    else:
        result.update(TRANSCRIPTION_DATA_DIR="/var/lib/hr-vision-transcription",
                      TRANSCRIPTION_BROKER_URL="", TRANSCRIPTION_ASR_URL="", TRANSCRIPTION_ASR_MODEL="",
                      TRANSCRIPTION_ASR_API_KEY="")
    return result


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in PORTS:
        raise SystemExit("expected core, zoom or normalizer")
    service = sys.argv[1]
    root = Path(__file__).resolve().parent
    configured = environment(service, os.environ)
    os.environ.clear()
    os.environ.update(configured)
    os.umask(0o077)
    sys.path[:0] = [str(root / "src"), str(root / "compat")]
    if service == "core":
        from hr_materials_core.__main__ import main as start
        start()
    elif service == "zoom":
        import uvicorn
        from zoom_api import create_app
        # One process and the API's single download semaphore bound disk usage.
        uvicorn.run(create_app(), host="127.0.0.1", port=8095, workers=1,
                    access_log=False, timeout_graceful_shutdown=45)
    else:
        from md_transcription.__main__ import main as start
        sys.argv = ["md-transcription", "api", "--host", "127.0.0.1", "--port", "8094"]
        start()


if __name__ == "__main__":
    main()
