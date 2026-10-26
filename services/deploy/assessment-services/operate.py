"""Independent, root-only local host operations. No SSH and no HR API restart."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import socket
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

SERVICES = {
    'candidate': {'name': 'assessment-core', 'prefix': 'ASSESSMENT_CORE', 'port': 8097,
                  'rubric': 'analytics-baseline.json', 'table': 'runs'},
    'vacancy': {'name': 'vacancy-profile', 'prefix': 'VACANCY_PROFILE', 'port': 8108,
                'rubric': 'sales-v1.json', 'table': 'runs'},
    'recommendations': {'name': 'recommendations', 'prefix': 'RECOMMENDATIONS', 'port': 8099,
                        'rubric': 'sales-v1.json', 'table': None}}
SHA = re.compile(r'[a-f0-9]{40}')
ACTIVE = ('queued', 'sent', 'response_saved')


class OperationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise OperationError(message)


def atomic(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_env(path):
    require(path.is_file() and not path.is_symlink(), 'Private environment file required')
    require(path.stat().st_mode & 0o077 == 0, 'Environment file must be mode 0600')
    values = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, separator, value = line.partition('=')
        require(separator and re.fullmatch(r'[A-Z][A-Z0-9_]*', key), 'Invalid environment key')
        parsed = shlex.split(value)
        require(len(parsed) == 1, 'Invalid environment value')
        values[key] = parsed[0]
    return values


def private_env(path, values):
    for key, value in values.items():
        require(isinstance(value, str) and not re.search(r'[\r\n\x00]', value), 'Invalid environment value')
    # systemd EnvironmentFile quoting: dollar expansion is never used here.
    data = ''.join(key + '=' + json.dumps(value) + '\n' for key, value in sorted(values.items()))
    atomic(path, data.encode())


def validate_release(release, service, *, ownership=True):
    require(release.is_dir() and not release.is_symlink() and SHA.fullmatch(release.name), 'Immutable commit directory required')
    manifest_path = release / 'release.json'
    require(manifest_path.is_file() and not manifest_path.is_symlink(), 'Release manifest required')
    manifest = json.loads(manifest_path.read_text())
    require(manifest.get('format') == 'assessment-service-release-v1' and
            manifest.get('service') == service and manifest.get('commit') == release.name,
            'Manifest identity mismatch')
    require(manifest.get('data_compatibility') == SERVICES[service]['name'] + '-v1', 'Unsupported data compatibility')
    actual = {}
    for path in [release, *release.rglob('*')]:
        require(not path.is_symlink(), 'Release symlinks are forbidden')
        require(path.is_file() or path.is_dir(), 'Unsupported release entry')
        if ownership:
            require(path.stat().st_uid == 0 and path.stat().st_mode & 0o022 == 0,
                    'Release must be root owned and not writable by group/other')
        if path.is_file() and path != manifest_path:
            actual[str(path.relative_to(release))] = hashlib.sha256(path.read_bytes()).hexdigest()
    require(actual == manifest.get('files'), 'Release file inventory/hash mismatch')
    spec = SERVICES[service]
    required = ['jev_client/__init__.py', 'shared-components.json',
                'rubrics/' + spec['rubric'], 'deploy/hr-vision-' + spec['name'] + '.service']
    require(all(name in actual for name in required), 'Standalone dependencies/unit absent')
    shared = json.loads((release / 'shared-components.json').read_text())
    require(shared.get('contract_version') == 'assessment-shared-release-v1' and shared.get('files') and
            all(actual.get(name) == digest for name, digest in shared['files'].items()),
            'Shared component manifest mismatch')
    return manifest


def sqlite_backup(source, destination):
    require(source.is_file() and not source.is_symlink(), 'SQLite source required')
    require(not destination.exists(), 'Backup target already exists')
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5) as src:
            with sqlite3.connect(destination, timeout=5) as target:
                deadline = time.monotonic() + 60
                def progress(status, remaining, total):
                    require(time.monotonic() < deadline, 'SQLite backup deadline exceeded')
                src.backup(target, pages=128, sleep=0.05, progress=progress)
                require(target.execute('PRAGMA integrity_check').fetchone() == ('ok',), 'SQLite integrity check failed')
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def db_state(path, table):
    if not path.exists():
        return {'exists': False, 'active': 0, 'schema': None}
    require(path.is_file() and not path.is_symlink(), 'Unexpected database path')
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5) as db:
        require(db.execute('PRAGMA quick_check').fetchone() == ('ok',), 'Database integrity failure')
        schema = list(db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"))
        active = db.execute("SELECT COUNT(*) FROM " + table + " WHERE status IN (?,?,?)", ACTIVE).fetchone()[0] if table else 0
    return {'exists': True, 'active': active,
            'schema': hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()}


class Host:
    def __init__(self, service, root=Path('/'), *, simulated=False):
        self.service, self.spec, self.root = service, SERVICES[service], Path(root).resolve()
        self.simulated = simulated
        require(self.simulated or self.root == Path('/'), 'Alternate root only allowed in explicit simulation')
        self.unit = 'hr-vision-' + self.spec['name']
        self.base = self.root / 'opt' / self.unit
        self.releases = self.base / 'releases'
        self.current = self.base / 'current'
        self.db = self.root / 'var/lib' / self.unit / ('results.sqlite3' if service == 'recommendations' else 'runs.sqlite3')
        self.config = self.root / 'etc/hr-vision'
        self.envfile = self.config / (self.spec['name'] + '.env')
        self.unitfile = self.root / 'etc/systemd/system' / (self.unit + '.service')
        self.backups = self.root / 'var/backups' / self.unit
        self.state = self.base / 'operation.json'

    @contextmanager
    def locked(self):
        directory = self.root / 'run/lock'
        directory.mkdir(parents=True, exist_ok=True)
        handles = []
        try:
            for suffix in ('deploy', 'operation'):
                path = directory / (self.unit + '.' + suffix + '.lock')
                require(not path.is_symlink(), 'Lock symlink refused')
                handle = path.open('a')
                handles.append(handle)
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            for handle in reversed(handles):
                handle.close()

    def systemctl(self, *arguments):
        require(not self.simulated, 'Simulation must supply its own process backend')
        # Never return/log command output: service-manager failures may contain environment.
        result = subprocess.run(['systemctl', *arguments], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        require(result.returncode == 0, 'systemctl operation failed: ' + arguments[0])

    def previous(self, expected):
        if expected == 'absent':
            require(not self.current.exists() and not self.current.is_symlink(), 'Expected uninstalled service')
            require(not self.unitfile.exists(), 'Absent installation has existing unit; inspect first')
            if self.db.exists():
                require(self.state.is_file() and json.loads(self.state.read_text()).get('status') == 'initial_install_failed',
                        'Absent installation has untracked data; inspect first')
            return None
        require(SHA.fullmatch(expected), 'Expected-current must be full commit or absent')
        require(self.current.is_symlink(), 'Current must be a release symlink')
        previous = self.current.resolve(strict=True)
        require(previous.parent == self.releases and previous.name == expected, 'Current release changed or escaped expected root')
        return previous

    def legacy_inventory(self, release):
        require(self.service == 'candidate' and not (release / 'release.json').exists(), 'Legacy mode only for unmanifested candidate')
        require((release / 'assessment_core/api.py').is_file(), 'Legacy candidate runtime missing')
        files = {}
        for path in [release, *release.rglob('*')]:
            require(not path.is_symlink() and (path.is_file() or path.is_dir()), 'Unsafe legacy release entry')
            if not self.simulated:
                require(path.stat().st_uid == 0 and path.stat().st_mode & 0o022 == 0, 'Unsafe legacy ownership/mode')
            if path.is_file():
                files[str(path.relative_to(release))] = hashlib.sha256(path.read_bytes()).hexdigest()
        return files

    def validate_known(self, release):
        if (release / 'release.json').exists():
            return validate_release(release, self.service, ownership=not self.simulated)
        attestation = self.base / 'legacy' / (release.name + '.json')
        require(attestation.is_file(), 'Unknown legacy release; capture during first controlled upgrade')
        require(json.loads(attestation.read_text()) == self.legacy_inventory(release), 'Legacy release differs from captured inventory')

    def quiet(self):
        state = db_state(self.db, self.spec['table'])
        require(not state['active'], 'Active queued/sent/response_saved jobs: operation refused')
        return state

    def switch(self, release):
        if release is None:
            self.current.unlink(missing_ok=True)
            return
        staged = self.base / ('current.' + secrets.token_hex(8))
        try:
            staged.symlink_to(release)
            os.replace(staged, self.current)
        finally:
            staged.unlink(missing_ok=True)

    def unpack(self, artifact):
        self.releases.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.incoming-', dir=self.releases) as temporary:
            staging = Path(temporary)
            with tarfile.open(artifact, 'r:gz') as archive:
                members = archive.getmembers()
                require(len(members) <= 10000 and sum(m.size for m in members) <= 50_000_000, 'Artifact too large')
                for member in members:
                    parts = Path(member.name).parts
                    require(parts and SHA.fullmatch(parts[0]) and '..' not in parts and
                            not member.name.startswith('/') and (member.isfile() or member.isdir()), 'Unsafe archive member')
                require(len({Path(m.name).parts[0] for m in members}) == 1, 'Exactly one release required')
                # Every member was checked; links/devices/absolute/parent paths are forbidden.
                archive.extractall(staging)
            release = staging / Path(members[0].name).parts[0]
            for path in [release, *release.rglob('*')]:
                path.chmod(0o755 if path.is_dir() else 0o644)
            manifest = validate_release(release, self.service, ownership=not self.simulated)
            destination = self.releases / release.name
            if destination.exists():
                require(validate_release(destination, self.service, ownership=not self.simulated) == manifest,
                        'Existing release differs')
            else:
                os.rename(release, destination)
            return destination

    def check_port_available(self):
        # Binding is only a reservation probe; it never signals the existing owner.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(('127.0.0.1', self.spec['port']))
            except OSError:
                raise OperationError('Canonical service port is occupied; owner was not changed') from None

    def configure(self):
        self.config.mkdir(parents=True, exist_ok=True, mode=0o700)
        require(not self.config.is_symlink(), 'Configuration directory symlink refused')
        existing = read_env(self.envfile) if self.envfile.exists() else {}
        token = existing.get(self.spec['prefix'] + '_SERVICE_TOKEN') or secrets.token_hex(32)
        require(re.fullmatch('[a-f0-9]{64}', token), 'Service token must be 64 lowercase hex digits')
        prefix = self.spec['prefix']
        values = {prefix + '_SERVICE_TOKEN': token, prefix + '_PORT': str(self.spec['port']),
                  prefix + '_DB': '/var/lib/' + self.unit + '/' + self.db.name}
        if self.service != 'recommendations':
            source = read_env(self.config / 'assessment-core.env')
            key, proxy = source.get('ASSESSMENT_CORE_JEV_API_KEY', ''), source.get('ASSESSMENT_CORE_JEV_PROXY', '')
            require(key and not re.search(r'\s', key), 'Existing candidate JEV credential required')
            parsed = urllib.parse.urlsplit(proxy)
            require(parsed.scheme in ('socks5', 'socks5h') and parsed.hostname in ('127.0.0.1', '::1') and
                    parsed.port and not parsed.username and not parsed.password and parsed.path in ('', '/'),
                    'Explicit authenticated-free loopback SOCKS proxy required')
            values.update({prefix + '_JEV_API_KEY': key, prefix + '_JEV_PROXY': proxy, prefix + '_MODEL': 'jev-1.14.0'})
        private_env(self.envfile, values)
        rubric = '/opt/' + self.unit + '/current/rubrics/' + self.spec['rubric']
        bridge_prefix = 'HR_' + prefix
        bridge = {bridge_prefix + '_URL': 'http://127.0.0.1:' + str(self.spec['port']),
                  bridge_prefix + '_TOKEN': token, bridge_prefix + '_RUBRIC': rubric,
                  bridge_prefix + '_CATALOG': rubric, bridge_prefix + '_MODEL': 'jev-1.14.0' if self.service != 'recommendations' else 'none'}
        if self.service == 'candidate':
            bridge['HR_ASSESSMENT_CORE_ENABLED'] = '1'
        bridgefile = self.config / (self.spec['name'] + '-bridge.env')
        private_env(bridgefile, bridge)
        dropin = self.root / 'etc/systemd/system/hr-vision-api.service.d' / ('assessment-' + self.spec['name'] + '.conf')
        atomic(dropin, ('[Service]\nEnvironmentFile=/etc/hr-vision/' + bridgefile.name + '\n').encode(), 0o644)

    def health(self, timeout=30):
        token = read_env(self.envfile)[self.spec['prefix'] + '_SERVICE_TOKEN']
        deadline = time.monotonic() + timeout
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while time.monotonic() < deadline:
            try:
                legacy = not (self.current.resolve() / 'release.json').exists()
                if legacy:
                    self.validate_known(self.current.resolve())
                endpoints = (('/healthz', 'ok'),) if legacy else (('/healthz', 'ok'), ('/readyz', 'ready'))
                for endpoint, status in endpoints:
                    request = urllib.request.Request('http://127.0.0.1:' + str(self.spec['port']) + endpoint,
                                                     headers={'Authorization': 'Bearer ' + token})
                    with opener.open(request, timeout=min(2, max(0.1, deadline - time.monotonic()))) as response:
                        payload = json.loads(response.read(65537))
                    require(payload.get('status') == status, 'Health/readiness status mismatch')
                if legacy:
                    self.quiet()  # Older immutable candidate has no /readyz.
                return
            except (OSError, ValueError):
                time.sleep(min(0.25, max(0, deadline - time.monotonic())))
        raise OperationError('Bounded health/readiness failed')

    def inspect_operation(self, operation_id):
        require(re.fullmatch('[a-f0-9]{64}', operation_id), 'Operation ID must be 64 lowercase hex digits')
        require(self.db.is_file(), 'Service database does not exist')
        with sqlite3.connect(self.db.as_uri() + '?mode=ro', uri=True, timeout=5) as db:
            if self.spec['table']:
                row = db.execute('SELECT status,created,updated,response IS NOT NULL,result IS NOT NULL FROM runs WHERE id=?', (operation_id,)).fetchone()
                require(row is not None, 'Operation not found')
                return dict(zip(('status', 'created', 'updated', 'rawResponseSaved', 'resultSaved'), row))
            row = db.execute('SELECT created,service_version FROM recommendations WHERE id=?', (operation_id,)).fetchone()
            require(row is not None, 'Operation not found')
            return {'status': 'completed', 'created': row[0], 'serviceVersion': row[1]}

    def backup(self):
        self.backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = self.backups / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + secrets.token_hex(4) + '.sqlite3')
        sqlite_backup(self.db, target)
        release_id = self.current.resolve().name if self.current.is_symlink() else json.loads(self.state.read_text())['failed_release']
        metadata = {'service': self.service, 'release': release_id,
                    'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                    'state': db_state(target, self.spec['table'])}
        atomic(target.with_suffix('.json'), (json.dumps(metadata, sort_keys=True) + '\n').encode())
        return target

    def install(self, artifact, expected, *, legacy_runtime=False):
        with self.locked():
            previous = self.previous(expected)
            if previous:
                if legacy_runtime:
                    inventory = self.legacy_inventory(previous)
                    attestation = self.base / 'legacy' / (previous.name + '.json')
                    if attestation.exists():
                        require(json.loads(attestation.read_text()) == inventory, 'Legacy release inventory changed')
                    else:
                        atomic(attestation, json.dumps(inventory, sort_keys=True).encode())
                else:
                    self.validate_known(previous)
            release = self.unpack(artifact)
            require(release != previous, 'Already current; use inspect')
            before = self.quiet()
            if not previous and not self.simulated:
                self.check_port_available()
            # Credential validation precedes stopping any process; snapshot config for rollback.
            paths = [self.envfile, self.config / (self.spec['name'] + '-bridge.env'),
                     self.unitfile, self.root / 'etc/systemd/system/hr-vision-api.service.d' / ('assessment-' + self.spec['name'] + '.conf')]
            saved = {p: (p.read_bytes(), p.stat().st_mode & 0o777) if p.exists() else None for p in paths}
            stopped = switched = False
            backup = None
            try:
                self.configure()
                if previous:
                    self.systemctl('stop', self.unit)
                    stopped = True
                    self.quiet()  # Catch admission between preflight and process stop.
                if not self.simulated:
                    self.check_port_available()
                if before['exists']:
                    backup = self.backup()
                atomic(self.unitfile, (release / ('deploy/' + self.unit + '.service')).read_bytes(), 0o644)
                self.switch(release)
                switched = True
                self.systemctl('daemon-reload')
                self.systemctl('start', self.unit)
                stopped = False
                self.health()
                after = self.quiet()
                require(not before['exists'] or before['schema'] == after['schema'], 'Schema migration is not permitted by this updater')
                self.systemctl('enable', self.unit)
                atomic(self.state, json.dumps({'current': release.name, 'previous': previous.name if previous else None,
                                              'backup': str(backup) if backup else None, 'status': 'healthy'}).encode())
                return {'service': self.service, 'current': release.name, 'previous': previous.name if previous else None,
                        'backup': str(backup) if backup else None, 'hrApiRestarted': False}
            except BaseException:
                if switched:
                    # Preserve all new accepted work. Never overwrite live data from a snapshot.
                    self.quiet()
                    self.systemctl('stop', self.unit)
                    stopped = True
                    self.quiet()
                    state = db_state(self.db, self.spec['table'])
                    require(not previous or not before['exists'] or state['schema'] == before['schema'],
                            'Rollback blocked by incompatible schema; stopped service/data preserved for operator')
                    self.switch(previous)
                for path, value in saved.items():
                    if value is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic(path, *value)
                self.systemctl('daemon-reload')
                if previous and stopped:
                    self.systemctl('start', self.unit)
                    self.health()
                if not previous:
                    atomic(self.state, json.dumps({'status': 'initial_install_failed', 'failed_release': release.name}).encode())
                raise OperationError('Update failed; previous release/config restored and checked' if previous else
                                 'Initial install failed; service stopped and configuration restored') from None

    def rollback(self, target, expected):
        with self.locked():
            previous = self.previous(expected)
            release = self.releases / target
            require(SHA.fullmatch(target), 'Full rollback commit required')
            self.validate_known(release)
            self.validate_known(previous)
            before = self.quiet()
            self.systemctl('stop', self.unit)
            switched = False
            try:
                self.quiet()
                if not self.simulated:
                    self.check_port_available()
                backup = self.backup() if before['exists'] else None
                oldunit = self.unitfile.read_bytes()
                self.switch(release)
                switched = True
                atomic(self.unitfile, (release / ('deploy/' + self.unit + '.service')).read_bytes(), 0o644)
                self.systemctl('daemon-reload')
                self.systemctl('start', self.unit)
                self.health()
                require(db_state(self.db, self.spec['table'])['schema'] == before['schema'], 'Rollback schema changed')
                return {'current': target, 'previous': expected, 'backup': str(backup)}
            except BaseException:
                if switched:
                    self.quiet()
                    self.systemctl('stop', self.unit)
                    self.quiet()
                    require(db_state(self.db, self.spec['table'])['schema'] == before['schema'],
                            'Rollback return blocked by incompatible schema; stopped service/data preserved')
                    self.switch(previous)
                if 'oldunit' in locals():
                    atomic(self.unitfile, oldunit, 0o644)
                self.systemctl('daemon-reload')
                self.systemctl('start', self.unit)
                self.health()
                raise OperationError('Rollback failed; starting release restored') from None


def restore_isolated(source, destination, expected_service=None):
    require(destination.is_absolute(), 'Absolute isolated destination required')
    resolved = destination.resolve()
    require(not any(resolved == Path(p).resolve() or Path(p).resolve() in resolved.parents for p in ('/var/lib', '/opt', '/etc')),
            'Restore destination must be isolated from production state')
    metadata_path = source.with_suffix('.json')
    require(metadata_path.is_file(), 'Backup metadata required')
    metadata = json.loads(metadata_path.read_text())
    require(expected_service is None or metadata.get('service') == expected_service, 'Backup service mismatch')
    require(hashlib.sha256(source.read_bytes()).hexdigest() == metadata['sha256'], 'Backup hash mismatch')
    sqlite_backup(source, destination)
    require(db_state(destination, SERVICES[metadata['service']]['table']) == metadata['state'], 'Restored state mismatch')
    return {'restored': str(destination), 'service': metadata['service'], 'release': metadata['release']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('service', choices=SERVICES)
    commands = parser.add_subparsers(dest='command', required=True)
    inspect = commands.add_parser('inspect')
    inspect.add_argument('--id')
    inspect.add_argument('--probe', action='store_true')
    commands.add_parser('backup')
    install = commands.add_parser('install')
    install.add_argument('artifact', type=Path)
    install.add_argument('--expected-current', required=True)
    install.add_argument('--legacy-candidate', action='store_true')
    install.add_argument('--consumers-quiesced', action='store_true', required=True,
                         help='Operator confirms admission disabled in consumers for the full operation')
    rollback = commands.add_parser('rollback')
    rollback.add_argument('commit')
    rollback.add_argument('--expected-current', required=True)
    rollback.add_argument('--consumers-quiesced', action='store_true', required=True)
    restore = commands.add_parser('restore-isolated')
    restore.add_argument('backup', type=Path)
    restore.add_argument('destination', type=Path)
    args = parser.parse_args()
    require(os.geteuid() == 0, 'Root required on the target host')
    host = Host(args.service)
    if args.command == 'install':
        result = host.install(args.artifact, args.expected_current, legacy_runtime=args.legacy_candidate)
    elif args.command == 'rollback':
        result = host.rollback(args.commit, args.expected_current)
    elif args.command == 'backup':
        with host.locked():
            result = {'backup': str(host.backup())}
    elif args.command == 'restore-isolated':
        result = restore_isolated(args.backup.resolve(), args.destination, args.service)
    else:
        with host.locked():
            result = {'service': args.service, 'current': host.current.resolve().name if host.current.is_symlink() else None,
                      'database': db_state(host.db, host.spec['table']),
                      'privateEnvironmentPresent': host.envfile.is_file()}
            if host.current.is_symlink() and (host.current.resolve() / 'release.json').exists():
                validate_release(host.current.resolve(), args.service)
                result['manifestVerified'] = True
            if args.id:
                result['operation'] = host.inspect_operation(args.id)
            if args.probe:
                host.health()
                result['healthReady'] = True
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except OperationError as error:
        raise SystemExit(str(error)) from None
    except (ValueError, OSError, sqlite3.Error, json.JSONDecodeError):
        # No exception body from parsing/config/provider is ever logged.
        raise SystemExit('Operation refused or failed; inspect service state and bounded local operation tests') from None
