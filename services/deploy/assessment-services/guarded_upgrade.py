"""Upgrade candidate core while the installed guarded receiver owns HR quiescence."""
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

SHA = re.compile('[a-f0-9]{40}')


class GuardFailure(ValueError):
    pass


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


def execute(receiver, operate, artifact, expected_current, *, legacy=False, run=subprocess.run):
    """Call under deployment_locks(); injectable subprocess/receiver for isolated tests."""
    require(SHA.fullmatch(expected_current), 'Observed full candidate commit required')
    def command(*arguments):
        completed = run([sys.executable, str(operate), 'candidate', *arguments],
                        check=True, capture_output=True, text=True, timeout=420)
        return json.loads(completed.stdout)
    # Before any pause: a busy candidate core never causes HR downtime.
    inspected = command('inspect')
    require(inspected.get('current') == expected_current, 'Candidate current changed')
    require(inspected.get('database', {}).get('active') == 0, 'Candidate jobs active; finish them before guarded upgrade')
    previous_api = receiver.pointer(receiver.API)
    previous_ui = receiver.pointer(receiver.BASES['service'])
    require(previous_api and previous_ui, 'Installed API and UI pointers required')
    deployment = json.loads((receiver.BASES['service'] / previous_ui / 'deployment.json').read_text())
    require(deployment.get('target') == 'service' and SHA.fullmatch(deployment.get('sha', '')),
            'Existing UI deployment identity invalid')
    deployment_sha = deployment['sha']
    tag = 'assessment-upgrade-' + secrets.token_hex(12)
    receiver.guard_and_backup(tag + '-before')  # Includes the live meeting guard.
    paused = receiver.pause_materials()
    stopped_attempted = False
    safe_to_resume = False
    try:
        stopped_attempted = True
        receiver.stop_api()
        # Recheck durable live state after all API writers have stopped.
        receiver.guard_and_backup(tag + '-stopped')
        arguments = ['install', str(artifact), '--expected-current', expected_current, '--consumers-quiesced']
        if legacy:
            arguments.append('--legacy-candidate')
        result = command(*arguments)
        return {'candidate': result, 'existingUiSha': deployment_sha,
                'hrApiPointer': previous_api, 'guardedBackups': [tag + '-before', tag + '-stopped']}
    finally:
        if stopped_attempted:
            try:
                receiver.activate_api(previous_api)  # Rechecks stopped DB/runtime compatibility.
                receiver.check_origin('service', deployment_sha)
                safe_to_resume = True
            except BaseException:
                # Origin failure may occur after activate_api started the unit.
                # Stop through the receiver and retain the material writer pause.
                try:
                    receiver.stop_api()
                except BaseException:
                    pass
                raise GuardFailure('Guarded API recovery failed; material writers remain paused; inspect unit state') from None
        if safe_to_resume:
            receiver.resume_materials(paused)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifact', type=Path)
    parser.add_argument('--expected-current', required=True)
    parser.add_argument('--legacy-candidate', action='store_true')
    parser.add_argument('--receiver', type=Path, required=True)
    parser.add_argument('--receiver-sha256', required=True, help='SHA256 of reviewed installed receiver, not a secret')
    args = parser.parse_args()
    require(os.geteuid() == 0, 'Root required on target host')
    operate = Path(__file__).resolve().with_name('operate.py')
    require(operate.stat().st_uid == 0 and operate.stat().st_mode & 0o022 == 0, 'Reviewed root-owned operate.py required')
    require(args.artifact.is_file(), 'Artifact required')
    receiver = installed_receiver(args.receiver, args.receiver_sha256)
    with deployment_locks():
        result = execute(receiver, operate, args.artifact.resolve(), args.expected_current, legacy=args.legacy_candidate)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except GuardFailure as error:
        raise SystemExit(str(error)) from None
    except BaseException:
        raise SystemExit('Guarded candidate upgrade failed; inspect API/material/core unit states before retry') from None
