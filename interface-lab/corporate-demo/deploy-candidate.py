"""Publish only candidates.html and its assets, preserving the live employer release."""
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlsplit
import datetime
import hashlib
import io
import json
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parent
DIST = ROOT / 'dist'
OUTPUT = ROOT.parent.parent / 'output' / 'candidate-site'
OUTPUT.mkdir(parents=True, exist_ok=True)
SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '-o',
       'ProxyCommand=ssh -o ProxyCommand=none -i ~/.ssh/yandex_ebitrix -W %h:%p nikita@158.160.179.53',
       '-i', str(Path.home() / '.ssh/yandex_ebitrix'), 'nikita@10.130.0.18']


class Resources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        url = attrs.get('src') if tag == 'script' else attrs.get('href') if tag == 'link' else None
        # Shared identity assets are published separately by deploy-brand.py.
        if url and url.startswith('./') and not url.startswith('./brand/'):
            self.urls.append(url)


html = (DIST / 'candidates.html').read_text()
resources = Resources()
resources.feed(html)
queue = [(DIST, url) for url in resources.urls]
assets = {}
seen = set()
while queue:
    parent, url = queue.pop()
    file = (parent / urlsplit(url).path).resolve()
    assert file.is_relative_to(DIST.resolve()) and file.is_file(), f'Missing build resource: {url}'
    if file in seen:
        continue
    seen.add(file)
    name = 'candidate-assets/' + file.name
    data = file.read_bytes()
    assert name not in assets or assets[name] == data, f'Conflicting asset basename: {name}'
    assets[name] = data
    if file.suffix == '.js':
        refs = re.findall(r'''["'](\.[^"']+\.(?:js|css|ttf|woff2?|png|svg|ico)(?:\?[^"']*)?)["']''', data.decode())
        refs += re.findall(r'''new URL\(\s*["'`]([^"'`]+)["'`]\s*,\s*import\.meta\.url''', data.decode())
    elif file.suffix == '.css':
        refs = [value.strip(' \"\'') for value in re.findall(r'url\(([^)]+)\)', data.decode())]
        refs = [value for value in refs if not value.startswith('data:')]
    else:
        refs = []
    for ref in refs:
        # Vite emits these dependencies as siblings; keep their relative imports intact.
        assert '/' not in urlsplit(ref).path.removeprefix('./'), f'Nested asset requires explicit relocation: {ref}'
        queue.append((file.parent, ref))

for url in resources.urls:
    parsed = urlsplit(url)
    replacement = './candidate-assets/' + Path(parsed.path).name
    if parsed.query:
        replacement += '?' + parsed.query
    html = html.replace('"' + url + '"', '"' + replacement + '"')
assets['candidates.html'] = html.encode()
release = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-candidate'
manifest = {name: hashlib.sha256(data).hexdigest() for name, data in assets.items()}
assets['candidate-version.json'] = (json.dumps({'release': release, 'files': manifest}, indent=2) + '\n').encode()
archive = io.BytesIO()
with tarfile.open(fileobj=archive, mode='w:gz') as tar:
    for name, data in sorted(assets.items()):
        entry = tarfile.TarInfo(name)
        entry.size = len(data)
        entry.mode = 0o644
        # Caddy derives ETags from mtime and size; zero dates can retain stale HTML.
        entry.mtime = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        tar.addfile(entry, io.BytesIO(data))
payload = archive.getvalue()
digest = hashlib.sha256(payload).hexdigest()
remote_archive = f'/tmp/hr-vision-{release}.tar.gz'
subprocess.run(SSH + [f'cat > {remote_archive}'], input=payload, check=True)

remote = r'''
from pathlib import Path
import fcntl, hashlib, json, os, shutil, subprocess, tarfile
base = Path('/srv/hr-vision-company-demo')
release = RELEASE
archive = Path(ARCHIVE)
assert hashlib.sha256(archive.read_bytes()).hexdigest() == DIGEST
current = base / 'current'
with (base / '.deploy.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    assert current.is_symlink(), 'Existing company release is required'
    previous = os.readlink(current)
    source = current.resolve()
    target = base / 'releases' / release
    subprocess.run(['systemctl', 'is-active', '--quiet', 'hr-vision-web.service'], check=True)
    before = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in source.rglob('*') if p.is_file()
              and not p.relative_to(source).parts[0] in ('candidate-assets', 'candidates.html', 'candidate-version.json')}
    shutil.copytree(source, target, symlinks=True)
    with tarfile.open(archive) as tar:
        for entry in tar.getmembers():
            path = Path(entry.name)
            assert entry.isfile() and not path.is_absolute() and '..' not in path.parts
            assert path.parts[0] in ('candidate-assets', 'candidates.html', 'candidate-version.json')
            assert not (target / path).is_symlink()
        assert not (target / 'candidate-assets').is_symlink()
        tar.extractall(target)
    for name, expected in MANIFEST.items():
        assert hashlib.sha256((target / name).read_bytes()).hexdigest() == expected, name
        (target / name).chmod(0o644)
    (target / 'candidate-assets').chmod(0o755)
    for name, expected in before.items():
        assert hashlib.sha256((target / name).read_bytes()).hexdigest() == expected, 'Changed existing file: ' + name
    assert os.readlink(current) == previous, 'Current release changed concurrently'
    pending = base / ('current-' + release)
    pending.symlink_to(target.relative_to(base))
    os.replace(pending, current)
    archive.unlink()
    print(json.dumps({'release': release, 'previous': previous, 'archiveSha256': DIGEST,
                      'preservedFiles': len(before), 'root': str(current), 'files': MANIFEST}))
'''
remote = remote.replace('RELEASE', repr(release)).replace('ARCHIVE', repr(remote_archive)).replace('DIGEST', repr(digest)).replace('MANIFEST', repr(manifest))
result = subprocess.run(SSH + ['sudo python3 -'], input=remote.encode(), capture_output=True)
if result.returncode:
    raise RuntimeError(result.stderr.decode())
receipt = json.loads(result.stdout.decode().strip().splitlines()[-1])
receipt['url'] = 'https://hr-vision-lab.158-160-179-53.sslip.io/demos/company-v1/candidates.html'
(OUTPUT / 'candidate-deployment.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps({key: value for key, value in receipt.items() if key != 'files'}, ensure_ascii=False))
