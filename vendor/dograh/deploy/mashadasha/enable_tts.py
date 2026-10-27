"""Configure the private, no-key Edge speech adapter after its runtime test."""
import json
import os
from pathlib import Path
import secrets

from configure_from_oasis import read_environment

state = Path("/var/lib/dograh")
env_path = state / ".env.local"
env = read_environment(env_path)
if not env.get("TTS_AUTH_TOKEN"):
    env["TTS_AUTH_TOKEN"] = secrets.token_hex(32)
    with env_path.open("a") as stream:
        stream.write("\nTTS_AUTH_TOKEN=" + env["TTS_AUTH_TOKEN"] + "\n")
credentials_path = state / "bootstrap.json"
credentials = json.loads(credentials_path.read_text())
credentials["providers"]["tts"] = {
    "provider": "openai", "model": "gpt-4o-mini-tts", "voice": "alloy",
    "base_url": "http://tts:8000/v1", "api_key": env["TTS_AUTH_TOKEN"],
}
with credentials_path.open("w") as stream:
    json.dump(credentials, stream, ensure_ascii=False, indent=2)
os.chmod(credentials_path, 0o600)
print("Private Edge TTS configuration prepared; bootstrap and runtime test still required")
