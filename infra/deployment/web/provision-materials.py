#!/usr/bin/env python3
"""Prepare the private materials broker/runtime on outreach, without enabling materials.

Run as root from a complete reviewed checkout. The script never changes the live API
pointer or API unit, and never starts the materials worker or scheduler.
"""
import argparse
import base64
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import unquote, urlsplit


IMAGE = ('rabbitmq:4.3.6-alpine@sha256:'
         '6931816af1a943f471c038b278835d36692d55dec769232739296bb91b7de0f5')
CONTAINER = 'hr-vision-materials-rabbitmq'
VOLUME = 'hr-vision-materials-rabbitmq'
RABBIT_DIR = Path('/etc/hr-vision/materials-rabbitmq')
ENV_FILE = Path('/etc/hr-vision/materials.env')
API_ENV = Path('/etc/hr-vision/api.env')
VENV_ROOT = Path('/opt/hr-vision-materials')
VENV = VENV_ROOT / 'venv'
UNIT_DIR = Path('/etc/systemd/system')
UNITS = ('hr-vision-materials-worker.service', 'hr-vision-materials-scheduler.service')
ACCEPTANCE_UNIT = 'hr-vision-materials-acceptance@.service'
PORT = 5672
MEMORY_BYTES = 384 * 1024 * 1024
BROKER_CONFIG = '''# Private single-purpose broker. Only the host loopback port is published.
listeners.tcp.default = 5672
vm_memory_high_watermark.absolute = 192MiB
disk_free_limit.absolute = 512MiB
definitions.import_backend = local_filesystem
definitions.local.path = /etc/rabbitmq/materials/definitions.json
definitions.skip_if_unchanged = true
log.console = true
log.file = false
'''


def run(argv, **kwargs):
    try:
        return subprocess.run(argv, check=True, text=True, capture_output=True, **kwargs)
    except subprocess.CalledProcessError as error:
        # Never repeat argv or full tool output: either may include credentials.
        detail = (error.stderr or error.stdout or '').strip().splitlines()[-8:]
        excerpt = '\n'.join(detail)[-1200:]
        excerpt = re.sub(r'(?i)(://[^:/\s]+:)[^@\s]+@', r'\1[redacted]@', excerpt)
        excerpt = re.sub(r'(?i)\b(password|secret|token|key)\s*[=:]\s*\S+',
                         r'\1=[redacted]', excerpt)
        raise RuntimeError(f'{Path(argv[0]).name} exited {error.returncode}: {excerpt or "no stderr"}') from None


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def private_write(path, data, mode, uid=0, gid=0):
    """Create once by atomic link; existing private files must match exactly."""
    if path.exists() or path.is_symlink():
        require(path.is_file() and not path.is_symlink(), f'Unsafe existing path: {path}')
        require(path.read_bytes() == data, f'Existing file differs; review manually: {path}')
        require((path.stat().st_mode & 0o777) == mode, f'Wrong mode: {path}')
        require(path.stat().st_uid == uid and path.stat().st_gid == gid, f'Wrong owner: {path}')
        return
    fd, temporary = tempfile.mkstemp(prefix='.materials-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, uid, gid)
        os.chmod(temporary, mode)
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def env_value(path, key):
    require(path.is_file() and not path.is_symlink(), f'Missing or unsafe environment file: {path}')
    found = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, value = line.split('=', 1)
        if name.strip() == key:
            found.append(value.strip().strip('"\''))
    require(len(found) <= 1, f'Duplicate {key} in {path}')
    return found[0] if found else None


def broker_password():
    if not ENV_FILE.exists():
        return secrets.token_urlsafe(36), False
    parsed = urlsplit(env_value(ENV_FILE, 'CELERY_BROKER_URL') or '')
    require(parsed.scheme == 'amqp' and parsed.hostname == '127.0.0.1' and
            parsed.port == PORT and parsed.username == 'hr_materials' and
            unquote(parsed.path) == '//materials' and parsed.password,
            'Existing materials broker URL differs; review without rotating credentials')
    require(env_value(ENV_FILE, 'HR_MATERIALS_ENABLED') == '0' and
            env_value(ENV_FILE, 'HR_MATERIALS_BACKFILL_ENABLED') == '0',
            'Existing materials environment is enabled; provisioning must not alter it')
    return parsed.password, True


def password_hash(password, salt):
    return base64.b64encode(salt + hashlib.sha256(salt + password.encode()).digest()).decode()


def definitions(password, existing=None):
    if existing:
        body = json.loads(existing)
        require(len(body.get('users', [])) == 1 and body['users'][0]['name'] == 'hr_materials',
                'Unexpected broker definitions users')
        stored = base64.b64decode(body['users'][0]['password_hash'], validate=True)
        require(len(stored) == 36 and body['users'][0].get('hashing_algorithm') ==
                'rabbit_password_hashing_sha256' and
                secrets.compare_digest(stored, base64.b64decode(password_hash(password, stored[:4]))),
                'Broker definitions and environment credentials differ')
        return existing
    user = {'name': 'hr_materials', 'password_hash': password_hash(password, os.urandom(4)),
            'hashing_algorithm': 'rabbit_password_hashing_sha256', 'tags': ''}
    body = {'users': [user], 'vhosts': [{'name': '/materials'}],
            'permissions': [{'user': 'hr_materials', 'vhost': '/materials',
                             'configure': '.*', 'write': '.*', 'read': '.*'}],
            'queues': [{'name': 'materials', 'vhost': '/materials', 'durable': True,
                        'auto_delete': False, 'arguments': {}}]}
    return (json.dumps(body, sort_keys=True, separators=(',', ':')) + '\n').encode()


def python_runtime(source, python):
    version = run([python, '-c', 'import platform,sys; print(sys.version_info[:2],platform.machine())']).stdout.strip()
    require(version == '(3, 12) x86_64', f'Expected Linux x86_64 CPython 3.12, got {version}')
    locks = [source / 'services' / 'recruiting-api' / name for name in
             ('requirements-materials.lock', 'requirements-api-extras.lock')]
    for path in locks:
        require(path.is_file(), f'Missing lock: {path}')
    digest = hashlib.sha256(b''.join(path.read_bytes() for path in locks)).hexdigest() + '\n'
    marker = VENV / '.materials-locks.sha256'
    if VENV.exists():
        require(marker.is_file() and marker.read_text() == digest,
                'Existing venv lock differs; use a reviewed replacement procedure')
        run([str(VENV / 'bin/python'), '-m', 'pip', 'check'])
        return
    VENV_ROOT.mkdir(mode=0o755, parents=True, exist_ok=True)
    require(not VENV_ROOT.is_symlink(), 'Unsafe Python runtime directory')
    with tempfile.TemporaryDirectory(prefix='.venv-', dir=VENV_ROOT) as temporary:
        path = Path(temporary) / 'venv'
        run([python, '-m', 'venv', str(path)])
        run([str(path / 'bin/python'), '-m', 'pip', 'install', '--no-cache-dir', '--require-hashes',
             '--only-binary=:all:', '-r', str(locks[0]), '-r', str(locks[1])])
        run([str(path / 'bin/python'), '-m', 'pip', 'check'])
        (path / '.materials-locks.sha256').write_text(digest)
        os.chmod(path / '.materials-locks.sha256', 0o644)
        path.rename(VENV)


def broker(group_id, password):
    RABBIT_DIR.mkdir(mode=0o750, parents=True, exist_ok=True)
    require(RABBIT_DIR.is_dir() and not RABBIT_DIR.is_symlink(), 'Unsafe broker config directory')
    os.chown(RABBIT_DIR, 0, group_id)
    os.chmod(RABBIT_DIR, 0o750)
    config = RABBIT_DIR / 'rabbitmq.conf'
    private_write(config, BROKER_CONFIG.encode(), 0o640, gid=group_id)
    definition_file = RABBIT_DIR / 'definitions.json'
    old = definition_file.read_bytes() if definition_file.exists() else None
    private_write(definition_file, definitions(password, old), 0o640, gid=group_id)
    run(['docker', 'image', 'inspect', IMAGE])
    existing = subprocess.run(['docker', 'inspect', CONTAINER], capture_output=True, text=True)
    volume = subprocess.run(['docker', 'volume', 'inspect', VOLUME], capture_output=True, text=True)
    if existing.returncode != 0 and volume.returncode == 0:
        mountpoint = Path(json.loads(volume.stdout)[0]['Mountpoint'])
        require(not any(mountpoint.iterdir()), 'Existing broker volume contains data without its container')
    run(['docker', 'volume', 'create', VOLUME])
    if existing.returncode == 0:
        info = json.loads(existing.stdout)[0]
        binds = info['HostConfig']['Binds'] or []
        ports = info['HostConfig']['PortBindings'].get('5672/tcp') or []
        mounts = info['Mounts']
        require(info['Config']['Image'] == IMAGE and info['HostConfig']['Memory'] == MEMORY_BYTES and
                info['HostConfig']['MemorySwap'] == MEMORY_BYTES and
                info['HostConfig']['RestartPolicy']['Name'] == 'unless-stopped' and
                info['HostConfig']['LogConfig']['Type'] == 'local' and
                info['HostConfig']['LogConfig']['Config'].get('max-size') == '10m' and
                info['HostConfig']['LogConfig']['Config'].get('max-file') == '3' and
                ports == [{'HostIp': '127.0.0.1', 'HostPort': str(PORT)}] and
                any(m.get('Name') == VOLUME and m.get('Destination') == '/var/lib/rabbitmq' for m in mounts) and
                f'{RABBIT_DIR}:/etc/rabbitmq/materials:ro' in binds and
                f'{config}:/etc/rabbitmq/rabbitmq.conf:ro' in binds,
                'Existing broker container differs; do not replace it automatically')
        if not info['State']['Running']:
            run(['docker', 'start', CONTAINER])
    else:
        require(existing.returncode == 1, 'Cannot inspect Docker container')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', PORT))
        run(['docker', 'run', '-d', '--name', CONTAINER,
             '--restart', 'unless-stopped', '--memory', '384m', '--memory-swap', '384m',
             '--publish', '127.0.0.1:5672:5672', '--volume', f'{VOLUME}:/var/lib/rabbitmq',
             '--volume', f'{RABBIT_DIR}:/etc/rabbitmq/materials:ro',
             '--volume', f'{config}:/etc/rabbitmq/rabbitmq.conf:ro',
             '--log-driver', 'local', '--log-opt', 'max-size=10m', '--log-opt', 'max-file=3', IMAGE])
    # ping only confirms the Erlang node; the RabbitMQ application may still be booting.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        for check in ('ping', 'check_running', 'check_local_alarms'):
            try:
                probe = subprocess.run(['docker', 'exec', CONTAINER, 'rabbitmq-diagnostics',
                                        '-q', check], capture_output=True, text=True, timeout=10)
            except subprocess.TimeoutExpired:
                break
            if probe.returncode != 0:
                break
        else:
            break
        time.sleep(2)
    else:
        raise RuntimeError('RabbitMQ did not become healthy within 60 seconds')
    queues = run(['docker', 'exec', CONTAINER, 'rabbitmqctl', '-q', 'list_queues',
                  '-p', '/materials', 'name', 'durable'], timeout=30).stdout.splitlines()
    require(any(line.split() == ['materials', 'true'] for line in queues),
            'Dedicated materials queue is absent or not durable')
    url = f'amqp://hr_materials:{password}@127.0.0.1:5672/%2Fmaterials'
    run([str(VENV / 'bin/python'), '-c',
         'import os; from kombu import Connection; '
         'connection=Connection(os.environ["HR_MATERIALS_CHECK_URL"], connect_timeout=3); '
         'connection.connect(); connection.release()'],
        env={**os.environ, 'HR_MATERIALS_CHECK_URL': url}, timeout=15)


def install_units(source):
    for name in UNITS:
        template = source / 'infra' / 'deployment' / 'web' / name
        require(template.is_file(), f'Missing unit template: {name}')
        private_write(UNIT_DIR / name, template.read_bytes(), 0o644)
    acceptance = source / 'infra' / 'deployment' / 'web' / 'materials-acceptance.py'
    require(acceptance.is_file(), 'Missing single-interview acceptance script')
    helper_dir = Path('/usr/local/libexec/hr-vision-materials')
    helper_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    require(not helper_dir.is_symlink(), 'Unsafe acceptance helper directory')
    private_write(helper_dir / 'acceptance.py', acceptance.read_bytes(), 0o644)
    private_write(UNIT_DIR / ACCEPTANCE_UNIT,
                  (source / 'infra' / 'deployment' / 'web' / ACCEPTANCE_UNIT).read_bytes(), 0o644)
    run(['systemctl', 'daemon-reload'])
    run(['systemd-analyze', 'verify', *(str(UNIT_DIR / name) for name in (*UNITS, ACCEPTANCE_UNIT))])
    for name in UNITS:
        state = subprocess.run(['systemctl', 'is-active', name], capture_output=True, text=True).stdout.strip()
        require(state in ('inactive', 'failed'), f'{name} must remain stopped until S3 acceptance')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', default='python3.12', help='Installed Linux x86_64 CPython 3.12')
    args = parser.parse_args()
    require(os.geteuid() == 0 and sys.platform == 'linux', 'Run as root on the Linux outreach host')
    source = Path(__file__).resolve().parents[3]
    require(API_ENV.is_file(), 'Existing /etc/hr-vision/api.env is required')
    root = env_value(API_ENV, 'HR_DATA_DIR') or '/var/lib/hr-vision-api'
    require(root.startswith('/') and '$' not in root, 'Review HR_DATA_DIR before provisioning')
    database = Path(root) / 'journey.sqlite3'
    require(database.is_file(), f'Existing API database missing: {database}')
    pwd.getpwnam('hr-vision')
    gid = grp.getgrnam('hr-vision').gr_gid
    require(shutil.disk_usage('/').free >= 1024 * 1024 * 1024,
            'Less than 1 GiB free; preserve broker disk alarm reserve')
    password, existed = broker_password()
    if existed:
        require(env_value(ENV_FILE, 'HR_MATERIALS_DB_PATH') == str(database),
                'Existing materials database path differs from the API database')
    if not existed:
        require(not (RABBIT_DIR / 'definitions.json').exists(),
                'Definitions exist without the matching private environment; manual recovery required')
        ENV_FILE.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        content = ('# Disabled until private S3 probe and API release acceptance.\n'
                   'HR_MATERIALS_ENABLED=0\nHR_MATERIALS_BACKFILL_ENABLED=0\n'
                   f'HR_MATERIALS_DB_PATH={database}\nHR_MATERIALS_VIDEO_POLICY=remote\n'
                   f'CELERY_BROKER_URL=amqp://hr_materials:{password}@127.0.0.1:5672/%2Fmaterials\n')
        private_write(ENV_FILE, content.encode(), 0o640, gid=gid)
    python_runtime(source, args.python)
    rabbit_gid = int(run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'id', IMAGE,
                          '-g', 'rabbitmq']).stdout.strip())
    require(rabbit_gid >= 1, 'Unexpected RabbitMQ image account')
    broker(rabbit_gid, password)
    install_units(source)
    print('Prepared broker, hash-locked Python runtime and stopped systemd units. Materials remain disabled.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'Provisioning stopped: {error}', file=sys.stderr)
        sys.exit(1)
