"""Install this independent process; the existing guarded release switches HR API."""
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import sys
import time
import urllib.request


def run(*args):
    return subprocess.check_output(args, text=True, timeout=30).strip()


def private(path, values):
    raw = ''.join(k + '=' + v + '\n' for k, v in values.items())
    assert all('\n' not in v and '\r' not in v for v in values.values())
    tmp = path.with_suffix('.new')
    with open(tmp, 'w', opener=lambda p, f: os.open(p, f, 0o600)) as f:
        f.write(raw)
    os.chmod(tmp, 0o600); os.replace(tmp, path)


def main():
    assert os.geteuid() == 0
    release = Path(sys.argv[1]).resolve()
    assert release.parent == Path('/opt/hr-vision-employer-review/releases')
    assert all((release / f).is_file() for f in ('service.py', 'quotes.py', 'deploy/hr-vision-employer-review.service'))
    current = release.parent.parent / 'current'
    active = subprocess.run(['systemctl', 'is-active', '--quiet', 'hr-vision-employer-review']).returncode == 0
    assert not active or current.resolve() == release, 'Running service has another release; stop only after draining jobs'
    state = Path('/var/lib/hr-vision-employer-review/jobs.sqlite3')
    if state.exists():
        with sqlite3.connect('file:' + str(state) + '?mode=ro', uri=True) as db:
            assert not db.execute("SELECT 1 FROM preparations WHERE status IN ('queued','processing') LIMIT 1").fetchone(), 'Jobs active'
    pid = run('systemctl', 'show', 'hr-vision-api', '-p', 'MainPID', '--value')
    env = dict(x.split('=', 1) for x in Path('/proc/' + pid + '/environ').read_bytes().decode().split('\0') if '=' in x)
    base = env.get('HR_CV_LLM_BASE_URL', env.get('HR_LLM_BASE_URL', ''))
    key = env.get('HR_CV_LLM_API_KEY', env.get('HR_LLM_API_KEY', ''))
    assert base and key, 'Existing CPA configuration required'
    config = Path('/etc/hr-vision'); config.mkdir(mode=0o700, exist_ok=True)
    old = config / 'employer-review.env'
    previous = dict(line.split('=', 1) for line in old.read_text().splitlines() if '=' in line) if old.exists() else {}
    token = previous.get('REVIEW_TOKEN') or secrets.token_hex(32)
    assert re.fullmatch('[a-f0-9]{64}', token)
    values = {'REVIEW_TOKEN': token, 'REVIEW_DB': str(state), 'REVIEW_PORT': '8098',
        'REVIEW_LLM_BASE_URL': base, 'REVIEW_LLM_KEY': key, 'REVIEW_LLM_MODEL': 'gemini-3.8-flash-high'}
    sys.path.insert(0, str(release))
    from quotes import Model
    os.environ.update(values); Model()  # Validate URL/config without a model call.
    private(old, values)
    private(config / 'employer-review-bridge.env', {'HR_EMPLOYER_REVIEW_ENABLED': '1',
        'HR_EMPLOYER_REVIEW_URL': 'http://127.0.0.1:8098', 'HR_EMPLOYER_REVIEW_TOKEN': token})
    dropin = Path('/etc/systemd/system/hr-vision-api.service.d'); dropin.mkdir(exist_ok=True)
    (dropin / 'employer-review.conf').write_text('[Service]\nEnvironmentFile=/etc/hr-vision/employer-review-bridge.env\n')
    Path('/etc/systemd/system/hr-vision-employer-review.service').write_bytes((release / 'deploy/hr-vision-employer-review.service').read_bytes())
    staged = current.with_name('current.new'); staged.unlink(missing_ok=True); staged.symlink_to(release); os.replace(staged, current)
    run('systemctl', 'daemon-reload'); run('systemctl', 'enable', '--now', 'hr-vision-employer-review')
    for _ in range(15):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8098/healthz', timeout=2) as response: health = json.load(response)
            assert health['status'] == 'ok'
            print(json.dumps({'release': release.name, 'health': health, 'hrApiRestarted': False}))
            return
        except OSError: time.sleep(1)
    raise RuntimeError('Preparation service health failed')


if __name__ == '__main__':
    main()
