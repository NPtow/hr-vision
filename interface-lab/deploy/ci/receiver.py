#!/usr/bin/env python3
"""Root-owned, fixed-purpose SSH receiver. Never executes uploaded build scripts."""
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

REPO = 'NPtow/hr-vision'
BRANCHES = {'service': 'main', 'mockups': 'codex/mockups'}
BASES = {'service': Path('/srv/hr-vision'), 'mockups': Path('/srv/hr-vision-mockups')}
API = Path('/opt/hr-vision-api')
DATABASE = Path('/var/lib/hr-vision-api/journey.sqlite3')
RUNTIME = {'app.py', 'browser_access.py', 'daily_media.py', 'domain.py', 'dsa_panel.py', 'feedback.py'}
MAX_ARCHIVE = 50 * 1024 * 1024
MAX_CONTENT = 120 * 1024 * 1024


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def unpack(archive, destination, target):
    """Validate every member and digest before writing any payload to disk."""
    with tarfile.open(archive, 'r:gz') as bundle:
        members = bundle.getmembers()
        require(len(members) <= 5000, 'Too many archive entries')
        names = set()
        total = 0
        files = {}
        for member in members:
            name = member.name.rstrip('/')
            path = PurePosixPath(name)
            require(name and str(path) == name and not path.is_absolute() and '..' not in path.parts and '\\' not in name, 'Unsafe archive path')
            require(name not in names, 'Duplicate archive member')
            names.add(name)
            require(member.isdir() or member.isfile(), 'Links and special files are forbidden')
            require(path.parts[0] in {'ui', 'api', 'release.json'}, 'Unknown bundle entry')
            if member.isdir():
                require(path.parts[0] in {'ui', 'api'}, 'Invalid directory')
                continue
            total += member.size
            require(total <= MAX_CONTENT, 'Expanded archive too large')
            files[name] = bundle.extractfile(member).read()
        manifest = json.loads(files.pop('release.json', b'{}'))
        require(manifest.get('version') == 1 and manifest.get('target') == target, 'Wrong deployment target')
        require(manifest.get('branch') == BRANCHES[target], 'Wrong source branch')
        require(re.fullmatch('[a-f0-9]{40}', manifest.get('sha', '')), 'Invalid source commit')
        require(re.fullmatch(r'\d+\.\d+', manifest.get('run', '')), 'Invalid workflow run')
        require(manifest.get('release') == manifest['sha'] + '-' + manifest['run'], 'Invalid release identity')
        require(set(files) == set(manifest.get('files', {})), 'File manifest differs from payload')
        for name, data in files.items():
            require(digest(data) == manifest['files'][name], 'File checksum mismatch: ' + name)
            if name.startswith('api/'):
                require(target == 'service' and name[4:] in RUNTIME, 'Unexpected API file')
            else:
                rel = name.removeprefix('ui/')
                require(name.startswith('ui/') and not any(part.startswith('.') for part in PurePosixPath(rel).parts), 'Unexpected public path')
                require(re.search(r'\.(js|css|svg|woff2?|ttf|png|jpe?g|webp|ico|gif)$', rel)
                        or rel in {'index.html', 'iframe.html', 'index.json', 'project.json', 'deployment.json'}
                        or rel.endswith('.LICENSE.txt'), 'Unexpected public file')
        require({'ui/index.html', 'ui/iframe.html', 'ui/index.json', 'ui/deployment.json'} <= files.keys(), 'Missing UI files')
        if target == 'service':
            require({'api/' + name for name in RUNTIME} <= files.keys(), 'Incomplete API')
        entries = json.loads(files['ui/index.json'])['entries']
        if target == 'service':
            require(set(entries) == {'hr-vision-product--start', 'hr-vision-product--manager', 'hr-vision-product--candidate'}, 'Service contains mockup stories')
        else:
            require('hr-vision-employer-menu--gallery' in entries and not any(key.startswith('hr-vision-product--') for key in entries), 'Mockup/service separation failed')
        public = json.loads(files['ui/deployment.json'])
        require(all(public.get(key) == manifest[key] for key in ('target', 'branch', 'sha', 'run')), 'Public revision differs')
        for name, data in files.items():
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o644)
        (destination / 'release.json').write_text(json.dumps(manifest))
        return manifest


def current_branch_sha(branch):
    request = urllib.request.Request(f'https://api.github.com/repos/{REPO}/git/ref/heads/{branch}', headers={'User-Agent': 'hr-vision-deployer', 'Accept': 'application/vnd.github+json'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.load(response)['object']['sha']
        except urllib.error.HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(attempt + 1)


def pointer(base):
    p = base / 'current'
    require(not p.exists() or p.is_symlink(), 'Current must be a symlink')
    value = os.readlink(p) if p.is_symlink() else None
    require(value is None or re.fullmatch(r'releases/[A-Za-z0-9._-]+', value), 'Unsafe existing pointer')
    return value


def switch(base, name, value):
    temporary = base / ('.' + name + '-' + str(os.getpid()))
    if temporary.is_symlink():
        temporary.unlink()
    if value is None:
        (base / name).unlink(missing_ok=True)
    else:
        temporary.symlink_to(value)
        temporary.replace(base / name)


def api_changed(source):
    return any(not (API / 'current' / name).is_file() or (source / name).read_bytes() != (API / 'current' / name).read_bytes() for name in RUNTIME)


def guard_live_interviews(db):
    states = db.execute('SELECT body FROM scenario WHERE id=1').fetchall()
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='scenario_variants'").fetchone():
        states += db.execute('SELECT body FROM scenario_variants').fetchall()
    for row in states:
        state = json.loads(row[0])
        require(not any(c.get('meeting') and c['meeting']['status'] == 'live' for c in state['candidates'].values()), 'An interview is live. Retry after it finishes; current release is unchanged.')


def guard_and_backup(release):
    with sqlite3.connect(f'file:{DATABASE}?mode=ro', uri=True) as db:
        guard_live_interviews(db)
        folder = Path('/var/backups/hr-vision')
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup = folder / (release + '.sqlite3')
        with sqlite3.connect(backup) as copy:
            db.backup(copy)
        backup.chmod(0o600)


def restart_api():
    subprocess.run(['systemctl', 'restart', 'hr-vision-api'], check=True, timeout=45)


def check_origin(target, sha):
    host = 'hr-vision.158-160-179-53.sslip.io' if target == 'service' else 'hr-vision-lab.158-160-179-53.sslip.io'
    last = None
    for _ in range(10):
        try:
            request = urllib.request.Request('http://127.0.0.1:8381/deployment.json', headers={'Host': host})
            with urllib.request.urlopen(request, timeout=4) as response:
                data = json.load(response)
            require(data['sha'] == sha and data['target'] == target, 'Origin revision mismatch')
            story = 'hr-vision-product--start' if target == 'service' else 'hr-vision-employer-menu--gallery'
            entry = 'http://127.0.0.1:8381/iframe.html?id=' + story + '&viewMode=story'
            with urllib.request.urlopen(urllib.request.Request(entry, headers={'Host': host}), timeout=4) as response:
                require(response.status == 200 and response.url == entry, 'Entry page was redirected to another site')
            if target == 'service':
                with urllib.request.urlopen('http://127.0.0.1:8391/api/hr/health', timeout=4) as response:
                    require(json.load(response).get('ok'), 'API health check failed')
            return
        except Exception as error:
            last = error
            time.sleep(1)
    raise RuntimeError('Origin verification failed') from last


def activate(stage, manifest):
    target, release = manifest['target'], manifest['release']
    base = BASES[target]
    base.mkdir(parents=True, exist_ok=True)
    require(shutil.disk_usage(base).free >= 512 * 1024 * 1024 + MAX_CONTENT, 'Insufficient disk reserve')
    # Do not let an older workflow overwrite a more recent push.
    require(current_branch_sha(manifest['branch']) == manifest['sha'], 'Branch advanced; this release is superseded')
    previous_ui = pointer(base)
    previous_api = pointer(API) if target == 'service' else None
    change_api = target == 'service' and api_changed(stage / 'api')
    if change_api:
        guard_and_backup(release)
    ui_release = base / 'releases' / release
    require(not ui_release.exists(), 'Workflow attempt was already installed; rerun it for a new attempt')
    ui_release.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(stage / 'ui', ui_release)
    if change_api:
        api_release = API / 'releases' / release
        require(not api_release.exists(), 'API release already exists')
        shutil.copytree(stage / 'api', api_release)
    switched_api = False
    try:
        if change_api:
            switch(API, 'current', 'releases/' + release)
            switched_api = True
            restart_api()
        switch(base, 'current', 'releases/' + release)
        check_origin(target, manifest['sha'])
    except Exception:
        switch(base, 'current', previous_ui)
        if switched_api:
            switch(API, 'current', previous_api)
            restart_api()
        raise
    if previous_ui:
        switch(base, 'previous', previous_ui)
    if switched_api and previous_api:
        switch(API, 'previous', previous_api)
    print(json.dumps({'published': target, 'sha': manifest['sha'], 'release': release, 'apiRestarted': change_api}), flush=True)


def main():
    require(os.geteuid() == 0 and len(sys.argv) == 2 and sys.argv[1] in BRANCHES, 'Invalid deploy command')
    target = sys.argv[1]
    os.umask(0o022)
    with tempfile.TemporaryDirectory(prefix='hr-vision-ci-') as temporary:
        temp = Path(temporary)
        archive = temp / 'release.tgz'
        total = 0
        with archive.open('wb') as output:
            while data := sys.stdin.buffer.read(65536):
                total += len(data)
                require(total <= MAX_ARCHIVE, 'Upload too large')
                output.write(data)
        stage = temp / 'stage'
        stage.mkdir()
        manifest = unpack(archive, stage, target)
        base = BASES[target]
        base.mkdir(parents=True, exist_ok=True)
        with (base / '.deploy.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if target == 'service':
                with Path('/run/hr-vision-api-deploy.lock').open('a') as api_lock:
                    fcntl.flock(api_lock, fcntl.LOCK_EX)
                    activate(stage, manifest)
            else:
                activate(stage, manifest)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Deployment failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
