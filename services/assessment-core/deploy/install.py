"""Install an immutable committed core release, without restarting HR API.

The existing analytics credential/loopback egress are reused on the server.
No secret is accepted on the command line or printed. API bridge activation
takes effect with the subsequent guarded HR release.
"""
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import sqlite3
import subprocess
import sys
import time
import urllib.request


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def read_env(path):
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            key, separator, value = line.partition('=')
            if separator and not key.startswith('#'):
                parsed = shlex.split(value)
                if len(parsed) == 1:
                    values[key.strip()] = parsed[0]
    return values


def private(path, values):
    staged = path.with_name(path.name + '.new')
    with open(staged, 'w', opener=lambda p, f: os.open(p, f, 0o600)) as stream:
        for key, value in values.items():
            assert isinstance(value, str) and '\n' not in value and '\r' not in value
            stream.write(key + '=' + value + '\n')
    staged.chmod(0o600)
    os.replace(staged, path)


def main():
    assert os.geteuid() == 0, 'Root required'
    release = Path(sys.argv[1]).resolve()
    assert release.parent == Path('/opt/hr-vision-assessment-core/releases')
    assert re.fullmatch('[a-f0-9]{40}', release.name), 'Commit release required'
    assert all((release / name).is_file() for name in (
        'assessment_core/api.py', 'assessment_core/engine.py',
        'rubrics/analytics-baseline.json', 'deploy/hr-vision-assessment-core.service'))
    current = release.parent.parent / 'current'
    state = subprocess.run(['systemctl', 'is-active', '--quiet', 'hr-vision-assessment-core']).returncode
    if state == 0:
        assert current.resolve() == release, 'Running core has another release; use a separately guarded update'
    dbpath = Path('/var/lib/hr-vision-assessment-core/runs.sqlite3')
    if dbpath.exists():
        with sqlite3.connect('file:' + str(dbpath) + '?mode=ro', uri=True) as db:
            assert not db.execute("SELECT 1 FROM runs WHERE status IN ('queued','sent','response_saved') LIMIT 1").fetchone(), 'Core jobs active'
    config = Path('/etc/hr-vision')
    config.mkdir(mode=0o700, exist_ok=True)
    old = read_env(config / 'assessment.env')
    previous = read_env(config / 'assessment-core.env')
    key = previous.get('ASSESSMENT_CORE_JEV_API_KEY') or old.get('ASSESSMENT_JEV_KEY')
    assert key and not re.search(r'\s', key), 'Existing analytics key required'
    token = previous.get('ASSESSMENT_CORE_SERVICE_TOKEN') or secrets.token_hex(32)
    assert re.fullmatch('[a-f0-9]{64}', token)
    proxy = previous.get('ASSESSMENT_CORE_JEV_PROXY', old.get('ASSESSMENT_PROXY', ''))
    version = re.search(r'curl (\d+)\.(\d+)\.(\d+)', run('/usr/bin/curl', '--version'))
    assert version and tuple(map(int, version.groups())) >= (8, 4, 0), 'curl >= 8.4 required'
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location('core_jev_config', release / 'assessment_core/providers/jev.py')
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.JevConfig(api_key=key, proxy_url=proxy)
    private(config / 'assessment-core.env', {
        'ASSESSMENT_CORE_SERVICE_TOKEN': token, 'ASSESSMENT_CORE_JEV_API_KEY': key,
        'ASSESSMENT_CORE_MODEL': 'jev-1.14.0', 'ASSESSMENT_CORE_PORT': '8097',
        'ASSESSMENT_CORE_DB': str(dbpath), 'ASSESSMENT_CORE_JEV_PROXY': proxy})
    private(config / 'assessment-core-bridge.env', {
        'HR_ASSESSMENT_CORE_ENABLED': '1', 'HR_ASSESSMENT_CORE_URL': 'http://127.0.0.1:8097',
        'HR_ASSESSMENT_CORE_TOKEN': token, 'HR_ASSESSMENT_CORE_MODEL': 'jev-1.14.0',
        'HR_ASSESSMENT_CORE_RUBRIC': str(current / 'rubrics/analytics-baseline.json')})
    dropin = Path('/etc/systemd/system/hr-vision-api.service.d')
    dropin.mkdir(exist_ok=True)
    (dropin / 'full-corpus.conf').write_text('[Service]\nEnvironmentFile=/etc/hr-vision/assessment-core-bridge.env\n')
    Path('/etc/systemd/system/hr-vision-assessment-core.service').write_bytes(
        (release / 'deploy/hr-vision-assessment-core.service').read_bytes())
    staged = current.with_name('current.new')
    staged.unlink(missing_ok=True)
    staged.symlink_to(release)
    os.replace(staged, current)
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', 'hr-vision-assessment-core')
    for _ in range(15):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8097/healthz', timeout=2) as response:
                health = json.load(response)
            assert health['status'] == 'ok' and health['model'] == 'jev-1.14.0'
            print(json.dumps({'release': release.name, 'health': health, 'hrApiRestarted': False}))
            return
        except OSError:
            time.sleep(1)
    raise RuntimeError('Core health check failed')


if __name__ == '__main__':
    main()
