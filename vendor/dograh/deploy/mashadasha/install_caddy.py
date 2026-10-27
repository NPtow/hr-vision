"""Install one additive Caddy import. Run with sudo on dsa or outreach."""
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import sys

role = sys.argv[1]
if role not in {"dsa", "outreach"}:
    raise SystemExit("Usage: install_caddy.py dsa|outreach")
source = Path(__file__).with_name(f"{role}.caddy")
main = Path("/etc/caddy/Caddyfile")
snippet = Path("/etc/caddy/dograh-mashadasha.caddy")
line = f"import {snippet}"
original = main.read_text()
old_snippet = snippet.read_bytes() if snippet.exists() else None
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
shutil.copy2(main, main.with_name(f"Caddyfile.before-dograh-{stamp}"))
try:
    snippet.write_bytes(source.read_bytes())
    snippet.chmod(0o644)
    if line not in original.splitlines():
        with main.open("a") as stream:
            stream.write("\n# Isolated Dograh interview platform\n" + line + "\n")
    subprocess.run(["caddy", "validate", "--config", str(main), "--adapter", "caddyfile"], check=True)
    subprocess.run(["caddy", "reload", "--config", str(main), "--adapter", "caddyfile"], check=True)
except Exception:
    main.write_text(original)
    if old_snippet is None:
        snippet.unlink(missing_ok=True)
    else:
        snippet.write_bytes(old_snippet)
    raise
print(f"Installed and reloaded Dograh routes on {role}")
