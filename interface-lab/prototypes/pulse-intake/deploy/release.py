"""Build an immutable release on outreach. Does not modify the shared public gateway."""
from pathlib import Path
import hashlib
import json
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parent.parent
SSH = [
    "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
    "-o", "ProxyCommand=ssh -o ProxyCommand=none -o BatchMode=yes -i ~/.ssh/yandex_ebitrix -W %h:%p nikita@158.160.179.53",
    "-i", str(Path.home() / ".ssh/yandex_ebitrix"), "nikita@10.130.0.18",
]


def main():
    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    bundle = output / "release.tar.gz"
    names = ["server.py", "fields.json", "requirements.txt", ".dockerignore", "prompts", "web", "deploy"]
    with tarfile.open(bundle, "w:gz") as tar:
        for name in names:
            tar.add(ROOT / name, arcname=name, filter=lambda item: None if "__pycache__" in item.name else item)
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    release = "20261020-" + digest[:12]
    remote = "/srv/pulse-intake/releases/" + release
    subprocess.run(SSH + ["sudo python3 -"], input=(ROOT / "deploy/provision.py").read_bytes(), check=True)
    subprocess.run(SSH + ["sudo install -d -m 755 " + remote + " && sudo tar -xzf - -C " + remote],
                   input=bundle.read_bytes(), check=True)
    command = (
        "cd " + remote + "/deploy"
        " && sudo env PULSE_RELEASE=" + release + " docker compose config --quiet"
        " && sudo env PULSE_RELEASE=" + release + " docker compose build"
        " && sudo env PULSE_RELEASE=" + release + " docker compose up -d"
        " && sudo ln -sfn " + remote + " /srv/pulse-intake/current"
    )
    subprocess.run(SSH + [command], check=True)
    (output / "release.json").write_text(json.dumps({"release": release, "sha256": digest}, indent=2))
    print("Release:", release)


if __name__ == "__main__":
    main()
