"""Preview/publish identity over the current live bundles without downgrading them."""
from pathlib import Path
import datetime
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parent
OUT = ROOT.parent.parent / 'output' / 'brand-refresh'
OUT.mkdir(parents=True, exist_ok=True)
BASELINE = OUT / 'baseline'
PREVIEW = OUT / 'preview'
REMOTE = '/srv/hr-vision-company-demo'
ROOT_ICONS = {'favicon.ico', 'favicon.svg', 'favicon-32.png', 'favicon-48.png', 'apple-touch-icon.png'}
ICON_PAGES = {'lab.html', 'storybook/index.html', 'storybook/iframe.html'}
SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '-o',
       'ProxyCommand=ssh -o ProxyCommand=none -i ~/.ssh/yandex_ebitrix -W %h:%p nikita@158.160.179.53',
       '-i', str(Path.home() / '.ssh/yandex_ebitrix'), 'nikita@10.130.0.18']

def ssh(command, data=None):
    return subprocess.run(SSH + [command], input=data, check=True, capture_output=True).stdout

def sha(data):
    return hashlib.sha256(data).hexdigest()

def files(folder):
    return {str(p.relative_to(folder)): sha(p.read_bytes()) for p in folder.rglob('*') if p.is_file()}

def is_identity_file(name):
    return (name.startswith('brand/') or Path(name).name in ROOT_ICONS
            or name in ICON_PAGES | {'index.html', 'candidates.html', 'brandbook.html'})

def favicon_html(html, prefix='./brand/'):
    html = re.sub(r'<link\b[^>]*rel="(?:icon|apple-touch-icon|mask-icon)"[^>]*>\s*', '', html)
    links = (f'<link rel="icon" type="image/x-icon" sizes="16x16 32x32 48x48" href="{prefix}md-favicon-v4.ico">\n'
             f'<link rel="icon" type="image/png" sizes="16x16" href="{prefix}md-favicon-v4-16.png">\n'
             f'<link rel="icon" type="image/png" sizes="32x32" href="{prefix}md-favicon-v4-32.png">\n'
             f'<link rel="apple-touch-icon" sizes="180x180" href="{prefix}md-apple-touch-v4.png">\n')
    return html.replace('</head>', links + '</head>')

def brand_html(html, audience):
    html = re.sub(r'<html\b[^>]*>', f'<html lang="ru" data-md-audience="{audience}">', html, count=1)
    html = html.replace('МашаДаша', 'HR Vision')
    html = re.sub(r'<link\b[^>]*rel="(?:icon|apple-touch-icon|mask-icon)"[^>]*>\s*', '', html)
    html = re.sub(r'<(?:script|link)\b[^>]*(?:src|href)="\./brand/[^\"]+"[^>]*>(?:</script>)?\s*', '', html)
    html = re.sub(r'(<meta name="theme-color" content=")[^"]+', r'\g<1>#f7f6f2', html)
    css_version = sha((ROOT / 'public/brand/brand.css').read_bytes())[:12]
    js_version = sha((ROOT / 'public/brand/site-brand.js').read_bytes())[:12]
    html = html.replace('</head>', '<link rel="icon" type="image/x-icon" sizes="16x16 32x32 48x48" href="./brand/md-favicon-v4.ico">\n'
                        '<link rel="icon" type="image/png" sizes="16x16" href="./brand/md-favicon-v4-16.png">\n'
                        '<link rel="icon" type="image/png" sizes="32x32" href="./brand/md-favicon-v4-32.png">\n'
                        '<link rel="apple-touch-icon" sizes="180x180" href="./brand/md-apple-touch-v4.png">\n'
                        f'<link rel="stylesheet" href="./brand/brand.css?v={css_version}">\n</head>')
    return html.replace('</body>', f'<script type="module" src="./brand/site-brand.js?v={js_version}"></script>\n</body>')

def prepare():
    current = ssh(f'readlink {REMOTE}/current').decode().strip()
    snapshot = OUT / 'baseline.json'
    if not snapshot.exists() or json.loads(snapshot.read_text())['release'] != current:
        archive = ssh(f'tar -czf - -C {REMOTE}/current .')
        assert ssh(f'readlink {REMOTE}/current').decode().strip() == current, 'Live release changed; prepare again'
        if BASELINE.exists(): shutil.rmtree(BASELINE)
        BASELINE.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
            for member in tar.getmembers():
                path = Path(member.name)
                assert not path.is_absolute() and '..' not in path.parts
                assert member.isfile() or member.isdir(), f'Unexpected archive entry: {path}'
            # The macOS system Python predates extraction filters. All entries
            # above are checked to be regular files/directories with safe paths.
            tar.extractall(BASELINE)
        snapshot.write_text(json.dumps({'release': current, 'files': files(BASELINE)}, indent=2))
    if PREVIEW.exists(): shutil.rmtree(PREVIEW)
    shutil.copytree(BASELINE, PREVIEW)
    shutil.copytree(ROOT / 'public/brand', PREVIEW / 'brand', dirs_exist_ok=True)
    shutil.copy2(ROOT / 'public/brandbook.html', PREVIEW / 'brandbook.html')
    for name in ROOT_ICONS:
        shutil.copy2(ROOT / 'public' / name, PREVIEW / name)
    for path in BASELINE.rglob('*'):
        if path.is_file() and path.name in ROOT_ICONS:
            shutil.copy2(ROOT / 'public' / path.name, PREVIEW / path.relative_to(BASELINE))
    for name in ICON_PAGES:
        prefix = '../' * len(Path(name).parent.parts) + 'brand/'
        (PREVIEW / name).write_text(favicon_html((BASELINE / name).read_text(), prefix))
    for name, audience in [('index.html', 'dasha'), ('candidates.html', 'masha')]:
        (PREVIEW / name).write_text(brand_html((BASELINE / name).read_text(), audience))
    # Application bundles remain byte-exact; additional HTML changes only icons.
    original = json.loads(snapshot.read_text())['files']
    preserved = {name: digest for name, digest in original.items()
                 if name != 'brand-version.json' and not is_identity_file(name)}
    for name, digest in preserved.items():
        assert sha((PREVIEW / name).read_bytes()) == digest, name
    (OUT / 'prepared.json').write_text(json.dumps({'source': current, 'files': files(PREVIEW), 'preserved': preserved}, indent=2))
    print(json.dumps({'preview': str(PREVIEW), 'source': current, 'preservedFiles': len(preserved)}))

def deploy():
    prepared = json.loads((OUT / 'prepared.json').read_text())
    assert files(PREVIEW) == prepared['files'], 'Preview changed; prepare and verify again'
    source = prepared['source']
    assert ssh(f'readlink {REMOTE}/current').decode().strip() == source, 'Live release changed; prepare and verify again'
    release = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-brand'
    changed = {name: (PREVIEW / name).read_bytes() for name in prepared['files']
               if is_identity_file(name)}
    manifest = {name: sha(data) for name, data in changed.items()}
    changed['brand-version.json'] = (json.dumps({'release': release, 'source': source, 'files': manifest}, indent=2) + '\n').encode()
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as tar:
        for name, data in sorted(changed.items()):
            entry = tarfile.TarInfo(name); entry.size = len(data); entry.mode = 0o644
            entry.mtime = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            tar.addfile(entry, io.BytesIO(data))
    payload = archive.getvalue()
    remote_archive = f'/tmp/mashadasha-{release}.tar.gz'
    ssh(f'cat > {remote_archive}', payload)
    script = r'''
from pathlib import Path
import fcntl, hashlib, json, os, shutil, subprocess, tarfile
base = Path('/srv/hr-vision-company-demo')
with (base / '.deploy.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    current = base / 'current'
    assert os.readlink(current) == SOURCE, 'Concurrent deployment'
    subprocess.run(['systemctl','is-active','--quiet','hr-vision-web.service'],check=True)
    archive = Path(ARCHIVE)
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == DIGEST
    target = base / 'releases' / RELEASE
    shutil.copytree(current.resolve(), target, symlinks=True)
    with tarfile.open(archive) as tar:
        for entry in tar.getmembers():
            path = Path(entry.name)
            assert entry.isfile() and not path.is_absolute() and '..' not in path.parts
            assert path.parts[0] == 'brand' or entry.name in MANIFEST or entry.name == 'brand-version.json'
            assert not (target / path).is_symlink()
        tar.extractall(target, filter='data')
    for name, digest in MANIFEST.items():
        assert hashlib.sha256((target / name).read_bytes()).hexdigest() == digest, name
    for name, digest in PRESERVED.items():
        assert hashlib.sha256((target / name).read_bytes()).hexdigest() == digest, name
    for p in (target / 'brand').rglob('*'):
        p.chmod(0o755 if p.is_dir() else 0o644)
    (target / 'brand').chmod(0o755)
    pending = base / ('current-' + RELEASE)
    pending.symlink_to(target.relative_to(base)); os.replace(pending,current)
    archive.unlink()
    print(json.dumps({'release':RELEASE,'previous':SOURCE,'files':MANIFEST,'preservedFiles':len(PRESERVED)}))
'''
    for key, value in {'SOURCE':source, 'RELEASE':release, 'ARCHIVE':remote_archive, 'DIGEST':sha(payload),
                       'MANIFEST':manifest, 'PRESERVED':prepared['preserved']}.items():
        script = script.replace(key, repr(value))
    receipt = json.loads(ssh('sudo python3 -', script.encode()).decode().strip())
    (OUT / 'deployment.json').write_text(json.dumps(receipt, indent=2))
    print(json.dumps({k:v for k,v in receipt.items() if k != 'files'}))

if __name__ == '__main__':
    action = sys.argv[1] if len(sys.argv)>1 else 'prepare'
    if action == 'prepare': prepare()
    elif action == 'deploy': deploy()
    else: raise SystemExit('Usage: deploy-brand.py prepare|deploy')
