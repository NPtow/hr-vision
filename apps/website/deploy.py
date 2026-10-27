"""Publish this demo only; never replaces the product or Storybook release."""
from pathlib import Path
import datetime
import hashlib
import io
import json
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parent
DIST = ROOT / 'dist'
assert (DIST / 'index.html').is_file(), 'Build the demo first'
SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '-o',
       'ProxyCommand=ssh -o ProxyCommand=none -i ~/.ssh/yandex_ebitrix -W %h:%p nikita@158.160.179.53',
       '-i', str(Path.home() / '.ssh/yandex_ebitrix'), 'nikita@10.130.0.18']
release = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
archive = io.BytesIO()
with tarfile.open(fileobj=archive, mode='w:gz') as tar:
    for path in sorted(DIST.rglob('*')):
        assert not path.is_symlink(), 'Build may not contain symbolic links'
        if path.is_file():
            tar.add(path, arcname=str(path.relative_to(DIST)), recursive=False)
payload = archive.getvalue()
digest = hashlib.sha256(payload).hexdigest()
remote_archive = f'/tmp/hr-vision-company-{release}.tar.gz'
subprocess.run(SSH + [f'cat > {remote_archive}'], input=payload, check=True)
remote = r'''
from pathlib import Path
import hashlib, json, os, subprocess, tarfile
release = RELEASE
archive = Path(ARCHIVE)
assert hashlib.sha256(archive.read_bytes()).hexdigest() == DIGEST
base = Path('/srv/hr-vision-company-demo')
target = base / 'releases' / release
target.mkdir(parents=True, exist_ok=False)
with tarfile.open(archive) as tar:
    for entry in tar.getmembers():
        assert entry.isfile() and not Path(entry.name).is_absolute() and '..' not in Path(entry.name).parts
    tar.extractall(target)
for path in target.rglob('*'):
    path.chmod(0o755 if path.is_dir() else 0o644)
target.chmod(0o755)
config = Path('/etc/hr-vision/Caddyfile')
original = config.read_text()
config_sha = hashlib.sha256(config.read_bytes()).hexdigest()
route = '\t\tredir /demos/company-v1 /demos/company-v1/ 308\n\t\thandle_path /demos/company-v1/* {\n\t\t\troot * /srv/hr-vision-company-demo/current\n\t\t\tencode zstd gzip\n\t\t\theader Cache-Control "no-cache"\n\t\t\theader X-Robots-Tag "noindex, nofollow"\n\t\t\tfile_server\n\t\t}\n'.replace('\\t', '\t').replace('\\n', '\n')
anchor = '\thandle @mockups {\n'.replace('\\t', '\t').replace('\\n', '\n')
if '/demos/company-v1/' in original:
    assert route in original, 'Existing demo route differs; inspect before changing it'
    updated = original
else:
    assert original.count(anchor) == 1
    updated = original.replace(anchor, anchor + route, 1)
    assert updated.replace(route, '', 1) == original
temporary = config.with_name('Caddyfile.company-' + release)
temporary.write_text(updated)
subprocess.run(['caddy', 'validate', '--config', str(temporary), '--adapter', 'caddyfile'], check=True)
current = base / 'current'
previous = os.readlink(current) if current.is_symlink() else None
pending = base / ('current-' + release)
pending.symlink_to(target.relative_to(base))
assert hashlib.sha256(config.read_bytes()).hexdigest() == config_sha, 'Config changed concurrently'
os.replace(pending, current)
if updated != original:
    backup = config.with_name('Caddyfile.before-company-' + release)
    backup.write_text(original)
    os.replace(temporary, config)
    try:
        subprocess.run(['systemctl', 'reload', 'hr-vision-web.service'], check=True)
    except Exception:
        config.write_text(original)
        subprocess.run(['systemctl', 'reload', 'hr-vision-web.service'], check=True)
        if previous:
            pending.symlink_to(previous)
            os.replace(pending, current)
        raise
else:
    temporary.unlink()
subprocess.run(['systemctl', 'is-active', '--quiet', 'hr-vision-web.service'], check=True)
archive.unlink()
print(json.dumps({'release': release, 'previous': previous, 'archiveSha256': DIGEST,
                  'routeAdded': updated != original, 'root': str(current)}, ensure_ascii=False))
'''
remote = remote.replace('RELEASE', repr(release)).replace('ARCHIVE', repr(remote_archive)).replace('DIGEST', repr(digest))
result = subprocess.run(SSH + ['sudo python3 -'], input=remote.encode(), capture_output=True, check=True)
print(result.stdout.decode().strip())
receipt = json.loads(result.stdout.decode().strip().splitlines()[-1])
receipt['url'] = 'https://hr-vision-lab.158-160-179-53.sslip.io/demos/company-v1/'
(ROOT / 'deployment.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
