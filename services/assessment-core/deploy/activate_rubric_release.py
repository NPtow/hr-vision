"""Activate an immutable rubric-only release without restarting any service."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys


def runtime_files(release):
    files = list((release / 'assessment_core').rglob('*.py'))
    # Shared transport is part of the runtime, including its addition/removal.
    files.extend((release / 'jev_client').rglob('*.py'))
    files.append(release / 'deploy/hr-vision-assessment-core.service')
    return {str(p.relative_to(release)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files}


def check_release(current, release, dbpath):
    if runtime_files(current) != runtime_files(release):
        raise ValueError('Runtime changed: a rubric-only activation cannot restart or upgrade code')
    old = json.loads((current / 'rubrics/analytics-baseline.json').read_text())
    new = json.loads((release / 'rubrics/analytics-baseline.json').read_text())
    if old['rubric_id'] != new['rubric_id'] or old['version'] == new['version']:
        raise ValueError('Same rubric ID and a new version are required')
    with sqlite3.connect('file:' + str(dbpath) + '?mode=ro', uri=True) as db:
        if db.execute("SELECT 1 FROM runs WHERE status IN ('queued','sent','response_saved') LIMIT 1").fetchone():
            raise ValueError('Assessment jobs are active')
    return new


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('release', type=Path)
    parser.add_argument('--expected-current', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Root required')
    root = Path('/opt/hr-vision-assessment-core')
    release = args.release.resolve(strict=True)
    if release.parent != root / 'releases' or not re.fullmatch('[a-f0-9]{40}', release.name):
        raise SystemExit('Immutable commit release required')
    current = root / 'current'
    with open('/run/lock/hr-vision-assessment-core.deploy.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = current.resolve(strict=True)
        if previous.name != args.expected_current:
            raise SystemExit('Current release changed; recheck before activating')
        rubric = check_release(previous, release, Path('/var/lib/hr-vision-assessment-core/runs.sqlite3'))
        sys.path.insert(0, str(release))
        from assessment_core.engine import build_rubric
        checked = build_rubric(rubric)
        staged = root / ('current.rubric.' + str(os.getpid()))
        try:
            staged.symlink_to(release)
            if current.resolve(strict=True) != previous:
                raise SystemExit('Current release changed during validation')
            os.replace(staged, current)
        finally:
            staged.unlink(missing_ok=True)
        print(json.dumps({'previous': previous.name, 'release': release.name,
                          'rubricVersion': checked['version'], 'rubricHash': checked['sha256'],
                          'servicesRestarted': False}))


if __name__ == '__main__':
    main()
