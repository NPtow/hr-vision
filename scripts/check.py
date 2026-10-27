#!/usr/bin/env python3
"""Run existing isolated checks from the repository's canonical component paths."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PREFIXES = ('HR_', 'ASSESSMENT_', 'VACANCY_PROFILE_', 'RECOMMENDATIONS_',
                    'DAILY_', 'ZOOM_', 'JEV_', 'TYPESAFE_')


def run(command, directory='.', paths=(), extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(PRIVATE_PREFIXES)}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=os.pathsep.join(str(ROOT / p) for p in paths))
    env.update(extra or {})
    print(f'[{directory}] ' + ' '.join(command), flush=True)
    subprocess.run(command, cwd=ROOT / directory, env=env, check=True)


def unittest(directory, paths=(), pattern='test*.py'):
    run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', directory, '-p', pattern, '-q'], paths=paths)


def web():
    run(['npm', 'run', 'typecheck'], 'apps/web')
    run(['node', 'scripts/transcript-check.mjs'], 'apps/web')
    files = ['recording-transcript.test.ts', 'observability-privacy.test.ts',
             'candidate-player-model.test.ts', 'interview-answer-model.test.ts',
             'candidate-review-queue.test.ts', 'employer-entry.test.ts',
             'employer-agenda.test.ts', 'candidate-workspace.test.ts',
             'candidate-search-model.test.ts', 'candidate-schedule.test.ts',
             'candidate-call-connection.test.ts', 'candidate-access.test.ts',
             'candidate-navigation.test.ts', 'pooled-booking.test.ts',
             'telegram-employer.test.ts', 'selection-directory.test.mjs',
             'selection-automation.test.mjs', 'distribution-data.test.mjs']
    run(['node', '--test', *['scripts/' + name for name in files],
         'src/scenes/interview-assessment/fragment-feedback.test.mjs'], 'apps/web')
    run(['npm', 'run', 'build'], 'apps/web', extra={'HR_BUILD_TARGET': 'service'})


def api():
    run([sys.executable, '-B', '-m', 'unittest', 'discover', '-q'], 'services/recruiting-api')
    run([sys.executable, '-B', '-m', 'unittest', '-q', 'test_receiver'], 'infra/deployment/web/ci')


def services():
    shared = ('services/recruiting-api', 'services/data-core/src', 'services/transcription/src',
              'services/transcription-source/src', 'tests/integration')
    for folder, paths in [
        ('packages/jev-client/tests', ('packages/jev-client',)),
        ('services/assessment/tests', ()),
        ('services/employer-review', ()),
        ('services/assessment-core/tests', ('services/assessment-core',)),
        ('services/vacancy-profile/tests', ('packages/jev-client', 'services/vacancy-profile')),
        ('services/recommendations/tests', ('services/recommendations',)),
        ('infra/deployment/services/assessment-services/tests', ()),
        ('tests/integration/assessment-flow', ('services/assessment-core', 'packages/jev-client',
          'services/vacancy-profile', 'services/recommendations', 'services/recruiting-api')),
        ('services/transcription/tests', ('services/transcription/src',)),
    ]:
        unittest(folder, paths)
    run([sys.executable, '-B', '-m', 'pytest', '-q', 'services/data-core/tests',
         'services/zoom-api/test_zoom_api.py', 'infra/deployment/services/simple/tests',
         'tests/integration/test_zoom_core_http_integration.py'], paths=shared)
    run([sys.executable, '-B', '-m', 'pytest', '-q', 'services/transcription-source/tests',
         'infra/deployment/services/transcription/tests',
         'tests/integration/test_transcription_pipeline_http.py'], paths=shared)


def site():
    run(['npm', 'test'], 'apps/website')
    run(['npm', 'run', 'build'], 'apps/website')


def preview():
    unittest('apps/candidate-preview/tests')
    run(['node', '--test', 'tests/frontend.test.mjs'], 'apps/candidate-preview')
    run(['npm', 'run', 'build'], 'apps/candidate-preview/web')


def native():
    run(['npm', 'test'], 'apps/native-signals/board-web')
    run(['npm', 'run', 'build'], 'apps/native-signals/board-web')
    run(['npm', 'test'], 'apps/native-signals/rpa/telemetr')
    run(['swift', 'test'], 'apps/native-signals')


if __name__ == '__main__':
    groups = {'web': web, 'api': api, 'services': services, 'site': site, 'preview': preview, 'native': native}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('group', choices=groups, help='Dependencies must already be installed for this group.')
    args = parser.parse_args()
    try:
        groups[args.group]()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
