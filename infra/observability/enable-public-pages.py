#!/usr/bin/env python3
"""Add the shared loader to a copy of the CURRENT branded pages, atomically.

Assets are hardlinked to avoid duplicating the existing site on a small disk.
HTML is replaced with a fresh inode, so previous releases remain unchanged.
"""
from pathlib import Path
import os
import shutil
import time

base = Path('/srv/hr-vision-company-demo')
current = base/'current'
source = current.resolve(strict=True)
assert source.parent == base/'releases'
assert (Path('/srv/hr-vision/current')/'observability.js').is_file(), 'Deploy SDK first'
loader = '<script defer src="/observability.js"></script>'
if loader in (source/'index.html').read_text():
    print('Public pages already instrumented')
    raise SystemExit(0)
for file in source.rglob('*'):
    assert not file.is_symlink(), 'Inspect symlink before copying release'
release = base/'releases'/('observability-'+str(int(time.time())))
shutil.copytree(source,release,copy_function=os.link)
for name in ['index.html','candidates.html']:
    file = release/name
    if not file.is_file():
        continue
    html = file.read_text()
    if loader in html:
        continue
    assert '</head>' in html
    replacement = file.with_suffix('.html.tmp')
    replacement.write_text(html.replace('</head>',loader+'\n</head>',1))
    replacement.chmod(0o644)
    replacement.replace(file)
assert current.resolve() == source, 'Public site changed concurrently'
pending = base/'current-observability'
pending.symlink_to(release.relative_to(base))
previous = base/'previous-observability'
if previous.is_symlink(): previous.unlink()
previous.symlink_to(source.relative_to(base))
pending.replace(current)
print('Public pages enabled:',release.name,'previous:',source.name)
