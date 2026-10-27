"""Create deployment secrets once, on the target machine, without printing them."""
import os
from pathlib import Path
import secrets

state = Path("/var/lib/dograh")
state.mkdir(mode=0o750, parents=True, exist_ok=True)
target = state / ".env.local"
if target.exists():
    print("Existing deployment environment preserved")
else:
    example = Path(__file__).with_name("env.example").read_text()
    lines = []
    for line in example.splitlines():
        if "=replace_with_random" in line:
            key = line.split("=", 1)[0]
            line = f"{key}={secrets.token_hex(64 if key == 'OSS_JWT_SECRET' else 32)}"
        lines.append(line)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write("\n".join(lines) + "\n")
    print("Deployment environment created (0600)")
