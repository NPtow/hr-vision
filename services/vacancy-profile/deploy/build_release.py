"""Build a self-contained vacancy service directory; never installs or starts it."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil


def build(destination):
    service = Path(__file__).resolve().parents[1]
    repository = service.parents[1]
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('release destination must not exist')
    destination.mkdir(parents=True, mode=0o755)
    try:
        for name in ('vacancy_profile', 'deploy'):
            shutil.copytree(service / name, destination / name,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for name in ('README.md', 'API.md'):
            shutil.copyfile(service / name, destination / name)
        spec = importlib.util.spec_from_file_location('freeze_shared', repository / 'packages/jev-client/export_release.py')
        exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(exporter)
        exporter.export_release(destination)
        files = {str(path.relative_to(destination)): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in sorted(destination.rglob('*')) if path.is_file()}
        manifest = {'contract_version': 'vacancy-release-v1', 'files': files}
        (destination / 'release-manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
        return manifest
    except Exception:
        shutil.rmtree(destination)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    print(json.dumps(build(parser.parse_args().destination), sort_keys=True))
