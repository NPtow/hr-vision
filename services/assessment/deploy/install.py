"""Root-only installation of a committed, versioned release. Does not restart HR API.

Usage: sudo python3 RELEASE/deploy/install.py RELEASE
The operator copies the verified source archive to /opt/hr-vision-assessment/releases/SHA.
"""
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import sys


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def private(path, text):
    tmp = path.with_suffix('.new')
    with open(tmp, 'w', opener=lambda p, f: os.open(p, f, 0o600)) as stream:
        stream.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


assert os.geteuid() == 0, 'Root required'
release = Path(sys.argv[1]).resolve()
assert release.parent == Path('/opt/hr-vision-assessment/releases')
assert re.fullmatch('[a-f0-9]{40}', release.name), 'Commit SHA required'
assert all((release / name).is_file() for name in ('app.py', 'engine.py', 'prompts/2026-10-25.2.json'))
state = Path('/var/lib/hr-vision-assessment/jobs.sqlite3')
if state.exists():
    with sqlite3.connect('file:' + str(state) + '?mode=ro', uri=True) as db:
        assert not db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone(), 'Assessment jobs active'

config = Path('/etc/hr-vision')
config.mkdir(mode=0o700, exist_ok=True)
envfile = config / 'assessment.env'
if envfile.exists():
    values = dict(line.split('=', 1) for line in envfile.read_text().splitlines() if '=' in line and not line.startswith('#'))
    token = values['ASSESSMENT_SERVICE_TOKEN']
else:
    pid = run('systemctl', 'show', 'hr-vision-api', '-p', 'MainPID', '--value')
    runtime = dict(item.split(b'=', 1) for item in Path('/proc/' + pid + '/environ').read_bytes().split(b'\0') if b'=' in item)
    key = runtime[b'HR_JEV_API_KEY'].decode()
    assert key and not re.search(r'\s', key)
    token = secrets.token_hex(32)
    private(envfile, 'ASSESSMENT_SERVICE_TOKEN=' + token + '\nASSESSMENT_JEV_KEY=' + key
        + '\nASSESSMENT_MODEL=jev-1.14.0\nASSESSMENT_PROXY=socks5h://127.0.0.1:10808\nASSESSMENT_DAILY_BUDGET=6000000\n')
assert re.fullmatch('[a-f0-9]{64}', token)
private(config / 'assessment-bridge.env', 'HR_ASSESSMENT_ENABLED=1\nHR_ASSESSMENT_TOKEN=' + token + '\n')
dropin = Path('/etc/systemd/system/hr-vision-api.service.d')
dropin.mkdir(exist_ok=True)
(dropin / 'assessment.conf').write_text('[Service]\nEnvironmentFile=/etc/hr-vision/assessment-bridge.env\n')
Path('/etc/systemd/system/hr-vision-assessment.service').write_bytes((release / 'deploy/hr-vision-assessment.service').read_bytes())
current = release.parent.parent / 'current'
staged = current.with_name('current.new')
staged.unlink(missing_ok=True)
staged.symlink_to(release)
os.replace(staged, current)
run('systemctl', 'daemon-reload')
run('systemctl', 'enable', '--now', 'hr-vision-assessment')
run('systemctl', 'restart', 'hr-vision-assessment')
print(json.dumps({'release': release.name, 'service': run('systemctl', 'is-active', 'hr-vision-assessment'),
                  'hrApiRestarted': False}))
