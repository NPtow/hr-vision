"""Upgrade one assessment service while the installed guarded receiver owns HR quiescence."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time

# Importing the installed receiver must never create runtime cache files.
sys.dont_write_bytecode = True

SHA = re.compile('[a-f0-9]{40}')


class GuardFailure(ValueError):
    pass


class CandidateRecoveryUnavailable(GuardFailure):
    """Candidate readiness could not be restored; HR writers must stay paused."""


def require(condition, message):
    if not condition:
        raise GuardFailure(message)


@contextmanager
def deployment_locks():
    """Exact receiver lock order; do not wait behind an active deploy."""
    handles = []
    try:
        for path in (Path('/srv/hr-vision/.deploy.lock'), Path('/run/hr-vision-api-deploy.lock')):
            require(not path.is_symlink(), 'Unsafe deployment lock')
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            handle = os.fdopen(descriptor, 'a')
            handles.append(handle)
            require(os.fstat(descriptor).st_uid == 0, 'Deployment lock is not root owned')
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        for handle in reversed(handles):
            handle.close()


def installed_receiver(path, expected_digest):
    require(path.is_absolute() and path.is_file() and not path.is_symlink(), 'Installed receiver file required')
    require(path.stat().st_uid == 0 and path.stat().st_mode & 0o022 == 0, 'Receiver must be root owned and protected')
    require(re.fullmatch('[a-f0-9]{64}', expected_digest) and
            hashlib.sha256(path.read_bytes()).hexdigest() == expected_digest, 'Reviewed receiver SHA256 mismatch')
    spec = importlib.util.spec_from_file_location('reviewed_installed_receiver', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(module.BASES['service'] == Path('/srv/hr-vision') and module.API == Path('/opt/hr-vision-api'),
            'Installed receiver roots differ from reviewed host layout')
    return module


def safe_exception(error):
    """Bounded machine metadata only; never messages, arbitrary argv or payloads."""
    result, seen = [], set()
    while error is not None and id(error) not in seen and len(result) < 4:
        seen.add(id(error))
        name = type(error).__name__
        item = {'type': name if re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,79}', name) else 'UnknownException'}
        for attribute in ('returncode', 'errno', 'sqlite_errorcode', 'code'):
            value = getattr(error, attribute, None)
            if isinstance(value, int) and not isinstance(value, bool):
                item[attribute] = value
        if isinstance(error, subprocess.CalledProcessError):
            argv = error.cmd
            if isinstance(argv, (list, tuple)) and len(argv) >= 3 and Path(str(argv[0])).name == 'systemctl':
                if argv[1] in ('start', 'stop', 'restart', 'is-active') and argv[2] in ('hr-vision-api', 'hr-vision-api.service'):
                    item['systemctl_action'] = argv[1]
                    item['systemctl_unit'] = 'hr-vision-api'
        result.append(item)
        error = error.__cause__ or error.__context__
    return result


class PrivateJournal:
    """Root-private atomic state, retained after failure for operator inspection."""
    def __init__(self, directory=Path('/var/lib/hr-vision-operations'), *, require_root=True):
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        require(not directory.is_symlink() and directory.stat().st_mode & 0o077 == 0,
                'Operation journal directory must be private')
        require(not require_root or directory.stat().st_uid == 0, 'Journal directory must be root owned')
        self.path = directory / ('assessment-upgrade-' + secrets.token_hex(12) + '.json')
        self.document = {'format': 'assessment-guarded-operation-v1', 'events': []}

    def __call__(self, phase, status, *, error=None, details=None):
        require(re.fullmatch('[a-z_]{1,64}', phase) and status in ('started', 'passed', 'failed'), 'Invalid journal phase')
        event = {'phase': phase, 'status': status, 'at_unix': time.time()}
        if error is not None:
            event['exception_chain'] = safe_exception(error)
        if details:
            # Only generated/deployment identifiers are accepted, never source texts.
            event['details'] = {key: value for key, value in details.items()
                                if key in ('existing_ui_sha', 'expected_candidate_commit', 'api_pointer', 'ui_pointer', 'paused_units', 'service', 'expected_service_commit')}
        self.document['events'].append(event)
        require(len(self.document['events']) <= 128, 'Operation journal event limit exceeded')
        descriptor, temporary = tempfile.mkstemp(prefix='.operation-', dir=self.path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, 'w') as output:
                json.dump(self.document, output, sort_keys=True)
                output.write('\n')
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)


def inspect_journal(path, *, require_root=True):
    require(path.is_file() and not path.is_symlink() and path.stat().st_mode & 0o077 == 0,
            'Private operation journal required')
    require(not require_root or path.stat().st_uid == 0, 'Operation journal must be root owned')
    require(path.stat().st_size <= 131072, 'Operation journal exceeds bounded size')
    document = json.loads(path.read_text())
    require(document.get('format') == 'assessment-guarded-operation-v1' and
            isinstance(document.get('events'), list) and len(document['events']) <= 128, 'Invalid operation journal')
    return document


def installed_operator(path):
    require(path.is_absolute() and path.is_file() and not path.is_symlink(), 'Installed operator file required')
    require(path.stat().st_uid == 0 and path.stat().st_mode & 0o022 == 0,
            'Operator must be root owned and protected')
    spec = importlib.util.spec_from_file_location('reviewed_assessment_operator', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(module.SERVICES['candidate']['name'] == 'assessment-core', 'Unexpected candidate service identity')
    return module


def validate_original_api(receiver, previous_api):
    """Read-only check of the exact installed receiver's recovery inventory.

    Fail before quiescence if restoring the original runtime would be refused.
    Every file participates, including generated bytecode; never repair or
    filter the source tree here. Durable-state checks still run at restore.
    """
    require(type(previous_api) is str and re.fullmatch(r'releases/[A-Za-z0-9._-]+', previous_api),
            'Unsafe original API pointer')
    releases = receiver.API / 'releases'
    source = receiver.API / previous_api
    require(releases.is_dir() and not releases.is_symlink()
            and source.is_dir() and not source.is_symlink(), 'Unsafe original API runtime')
    entries = list(source.rglob('*'))
    require(all(not path.is_symlink() and (path.is_file() or path.is_dir()) for path in entries),
            'Unsafe original API runtime')
    runtime = frozenset(path.relative_to(source).as_posix() for path in entries if path.is_file())
    require(runtime in receiver.RUNTIME_LAYOUTS, 'Incomplete original API runtime')
    require(receiver.pointer(receiver.API) == previous_api, 'Original API pointer changed')


def recover_candidate(operate, installed_commit, run_id, body_sha256, *, run, step):
    """Under deployment locks and HR pause; offline reconciliation of existing bytes."""
    operator = installed_operator(operate)
    host = operator.Host('candidate')
    with host.locked():
        def validate_runtime():
            release = host.previous(installed_commit)
            # The installer validates root ownership and the complete immutable
            # inventory. Reuse it before executing a module from the new release.
            host.validate_known(release)
            require((release / 'release.json').is_file(), 'Manifested candidate release required')
            require(host.db.is_file() and not host.db.is_symlink(), 'Existing candidate database required')
            # Do not open a WAL database as root and leave root-owned WAL/SHM
            # sidecars for DynamicUser. Inspect only the fixed SQLite header;
            # never change journal mode as part of reconciliation.
            with host.db.open('rb') as source:
                header = source.read(20)
            require(header[:16] == b'SQLite format 3\0' and header[18:20] == b'\x01\x01',
                    'Offline root recovery requires SQLite rollback journaling')
            owner_lock = Path(str(host.db) + '.owner.lock')
            require(owner_lock.is_file() and not owner_lock.is_symlink(), 'Existing candidate runtime lock required')
            require(owner_lock.stat().st_uid == host.db.stat().st_uid
                    and owner_lock.stat().st_mode & 0o077 == 0,
                    'Candidate runtime lock ownership differs from database')
            host.quiet()
            host.health()
            return release
        try:
            release = step('candidate_recovery_validate', validate_runtime)
            step('candidate_recovery_stop', lambda: host.systemctl('stop', host.unit))
            step('candidate_recovery_quiet', host.quiet)
            def reconcile():
                completed = run([sys.executable, '-B', '-s', '-m', 'assessment_core', 'recover-rejection', run_id,
                                 '--db', str(host.db), '--expected-body-sha256', body_sha256],
                                cwd=str(release), env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(release),
                                    'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1'},
                                check=True, capture_output=True, text=True, timeout=120)
                require(len(completed.stdout) <= 16384, 'Candidate recovery summary exceeds limit')
                summary = json.loads(completed.stdout)
                fields = {'run_id', 'status', 'provider_posts', 'body_sha256', 'plan_sha256',
                          'response_sha256', 'audit_phase'}
                require(type(summary) is dict and set(summary) == fields
                        and summary['run_id'] == run_id and summary['status'] == 'completed'
                        and type(summary['provider_posts']) is int and summary['provider_posts'] == 0
                        and summary['body_sha256'] == body_sha256 and summary['audit_phase'] == 'completed'
                        and all(type(summary[key]) is str and re.fullmatch('[a-f0-9]{64}', summary[key])
                                for key in ('plan_sha256', 'response_sha256')),
                        'Candidate recovery summary does not confirm the requested reconciliation')
                return summary
            return step('candidate_recovery_reconcile', reconcile)
        finally:
            # Even a failed CLI/stop must return the installed candidate to its
            # normal owner before the outer wrapper can restore HR admission.
            try:
                step('candidate_recovery_start', lambda: host.systemctl('start', host.unit))
                step('candidate_recovery_health', host.health)
            except BaseException as error:
                raise CandidateRecoveryUnavailable(
                    'Candidate recovery readiness failed; HR and material writers remain paused') from error


def execute(receiver, operate, artifact, expected_current, *, service='candidate', legacy=False,
            recover_rejection=None, expected_body_sha256=None, run=subprocess.run, journal=None):
    """Call under deployment_locks(); injectable subprocess/receiver for isolated tests."""
    record = journal or (lambda *arguments, **keywords: None)
    def step(phase, action):
        record(phase, 'started')
        try:
            result = action()
        except BaseException as error:
            record(phase, 'failed', error=error)
            raise
        record(phase, 'passed')
        return result
    require(service in ('candidate', 'vacancy', 'recommendations'), 'Unknown assessment service')
    require(not legacy or service == 'candidate', 'Legacy candidate flag is valid only for candidate service')
    require((recover_rejection is None) == (expected_body_sha256 is None), 'Recovery run and body hash are required together')
    if recover_rejection is not None:
        require(service == 'candidate', 'Rejection recovery is valid only for candidate service')
        require(type(recover_rejection) is str and re.fullmatch('[a-f0-9]{64}', recover_rejection)
                and type(expected_body_sha256) is str and re.fullmatch('[a-f0-9]{64}', expected_body_sha256),
                'Recovery run and body hash must be 64 lowercase hex digits')
    require(SHA.fullmatch(expected_current), 'Observed full service commit required')
    def command(*arguments):
        completed = run([sys.executable, '-B', str(operate), service, *arguments],
                        check=True, capture_output=True, text=True, timeout=420)
        return json.loads(completed.stdout)
    def preflight():
        inspected = command('inspect', *(['--legacy-candidate'] if legacy else ['--validate-current']))
        require(inspected.get('current') == expected_current, 'Service current changed')
        require(inspected.get('database', {}).get('active') == 0, 'Service jobs active; finish them before guarded upgrade')
    step(service + '_inspect', preflight)
    previous_api = step('capture_api_pointer', lambda: receiver.pointer(receiver.API))
    previous_ui = step('capture_ui_pointer', lambda: receiver.pointer(receiver.BASES['service']))
    require(previous_api and previous_ui, 'Installed API and UI pointers required')
    step('validate_original_api', lambda: validate_original_api(receiver, previous_api))
    def deployment_identity():
        deployment = json.loads((receiver.BASES['service'] / previous_ui / 'deployment.json').read_text())
        require(deployment.get('target') == 'service' and SHA.fullmatch(deployment.get('sha', '')),
                'Existing UI deployment identity invalid')
        return deployment['sha']
    deployment_sha = step('read_existing_deployment', deployment_identity)
    record('deployment_identity', 'passed', details={'existing_ui_sha': deployment_sha,
        'expected_candidate_commit': expected_current if service == 'candidate' else None,
        'expected_service_commit': expected_current, 'service': service, 'api_pointer': previous_api, 'ui_pointer': previous_ui})
    tag = 'assessment-upgrade-' + secrets.token_hex(12)
    step('live_guard_before_stop', lambda: receiver.guard_and_backup(tag + '-before'))
    paused = step('pause_materials', receiver.pause_materials)
    require(isinstance(paused, (list, tuple)) and len(paused) <= 32 and
            all(isinstance(unit, str) and re.fullmatch('[a-z0-9][a-z0-9@_.-]{1,127}', unit) for unit in paused),
            'Receiver returned invalid paused unit identities')
    # This durable event must precede stop_api, including on later recovery failure.
    record('materials_pause_state', 'passed', details={'paused_units': list(paused)})
    stopped_attempted = False
    safe_to_resume = False
    selected_service_healthy = True
    try:
        stopped_attempted = True
        step('stop_api', receiver.stop_api)
        step('live_guard_stopped', lambda: receiver.guard_and_backup(tag + '-stopped'))
        arguments = ['install', str(artifact), '--expected-current', expected_current, '--consumers-quiesced']
        if legacy:
            arguments.append('--legacy-candidate')
        result = step(service + '_install', lambda: command(*arguments))
        response = {service: result, 'service': service, 'existingUiSha': deployment_sha,
                    'hrApiPointer': previous_api, 'guardedBackups': [tag + '-before', tag + '-stopped']}
        if recover_rejection is not None:
            require(type(result.get('current')) is str and SHA.fullmatch(result['current']),
                    'Installed full candidate commit required')
            try:
                response['rejectionRecovery'] = recover_candidate(operate, result['current'], recover_rejection,
                    expected_body_sha256, run=run, step=step)
            except CandidateRecoveryUnavailable:
                selected_service_healthy = False
                raise
        return response
    finally:
        if stopped_attempted and selected_service_healthy:
            try:
                step('restore_api', lambda: receiver.activate_api(previous_api))
                step('verify_origin', lambda: receiver.check_origin('service', deployment_sha))
                safe_to_resume = True
            except BaseException as recovery:
                try:
                    step('stop_api_after_recovery_failure', receiver.stop_api)
                except BaseException:
                    pass
                raise GuardFailure('Guarded API recovery failed; material writers remain paused; inspect private operation journal') from recovery
        if safe_to_resume:
            step('resume_materials', lambda: receiver.resume_materials(paused))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifact', type=Path, nargs='?')
    parser.add_argument('--inspect-journal', type=Path)
    parser.add_argument('--expected-current')
    parser.add_argument('--service', choices=('candidate', 'vacancy', 'recommendations'), default='candidate')
    parser.add_argument('--legacy-candidate', action='store_true')
    parser.add_argument('--recover-rejection', help='Candidate run ID for offline retained-response recovery after install')
    parser.add_argument('--expected-body-sha256', help='Required exact retained-body hash with --recover-rejection')
    parser.add_argument('--receiver', type=Path)
    parser.add_argument('--receiver-sha256', help='SHA256 of reviewed installed receiver, not a secret')
    args = parser.parse_args()
    require(os.geteuid() == 0, 'Root required on target host')
    if args.inspect_journal:
        print(json.dumps(inspect_journal(args.inspect_journal), sort_keys=True))
        return
    require(args.artifact and args.expected_current and args.receiver and args.receiver_sha256, 'Upgrade arguments required')
    operate = Path(__file__).resolve().with_name('operate.py')
    require(operate.stat().st_uid == 0 and operate.stat().st_mode & 0o022 == 0, 'Reviewed root-owned operate.py required')
    require(args.artifact.is_file(), 'Artifact required')
    journal = PrivateJournal()
    print(json.dumps({'operationJournal': str(journal.path)}), flush=True)
    try:
        journal('load_receiver', 'started')
        receiver = installed_receiver(args.receiver, args.receiver_sha256)
        journal('load_receiver', 'passed')
        journal('acquire_deployment_locks', 'started')
        with deployment_locks():
            journal('acquire_deployment_locks', 'passed')
            result = execute(receiver, operate, args.artifact.resolve(), args.expected_current,
                             service=args.service, legacy=args.legacy_candidate,
                             recover_rejection=args.recover_rejection, expected_body_sha256=args.expected_body_sha256,
                             journal=journal)
        journal('operation', 'passed')
        result['operationJournal'] = str(journal.path)
        print(json.dumps(result, sort_keys=True))
    except BaseException as error:
        journal('operation', 'failed', error=error)
        raise GuardFailure('Guarded service upgrade failed; inspect private operation journal: ' + str(journal.path)) from error


if __name__ == '__main__':
    try:
        main()
    except GuardFailure as error:
        raise SystemExit(str(error)) from None
    except BaseException:
        raise SystemExit('Guarded service upgrade failed; inspect API/material/core unit states before retry') from None
