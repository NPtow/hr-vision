#!/usr/bin/env python3
"""Package existing build; no remote operations or secret files included."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

p = argparse.ArgumentParser()
p.add_argument('--release', required=True, help='source commit plus build identity')
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', a.release):
    p.error('invalid release identity')
root = Path(__file__).resolve().parent.parent
server_names = ['app.py', 'adapter.py', 'adapter_core.py', 'adapter_reader.py', 'adapter_errors.py', 'VENDORED.md']
# Explicit reviewed server safelist; never package arbitrary local Python/env files.
files = [root / 'server' / name for name in server_names]
files += [root / 'deploy/candidate-profile-preview.service']
if any(not path.is_file() for path in files):
    p.error('missing required server, provenance or service file')
dist = root / 'web/dist'
if not (dist / 'index.html').is_file():
    p.error('build web/dist before packaging')
files += sorted(x for x in dist.rglob('*') if x.is_file())
manifest = {'release': a.release, 'files': {}}
for path in files:
    if path.is_symlink() or '.env' in path.name or path.suffix == '.map':
        p.error('unexpected symlink, environment file or sourcemap: ' + str(path))
    manifest['files'][str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
a.output.parent.mkdir(parents=True, exist_ok=True)
# Exclusive output: never overwrite a previous release archive.
with a.output.open('xb') as out, tarfile.open(fileobj=out, mode='w:gz') as archive:
    for path in files:
        archive.add(path, arcname=str(path.relative_to(root)), recursive=False)
    data = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    entry = tarfile.TarInfo('manifest.json')
    entry.size = len(data)
    entry.mode = 0o644
    archive.addfile(entry, io.BytesIO(data))
print(json.dumps({'archive': str(a.output), 'sha256': hashlib.sha256(a.output.read_bytes()).hexdigest(), 'release': a.release}))
