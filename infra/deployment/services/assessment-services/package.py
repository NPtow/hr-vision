"""Build a deterministic standalone release from one immutable Git commit."""
import argparse
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

SERVICES = {'candidate': ('assessment-core', 'analytics-baseline.json'),
            'vacancy': ('vacancy-profile', 'sales-v1.json'),
            'recommendations': ('recommendations', 'sales-v1.json')}


def build(repository, ref, service, output):
    name, rubric = SERVICES[service]
    commit = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', '--verify', ref + '^{commit}'], text=True).strip()
    paths = ['services/' + name, 'packages/jev-client', 'packages/assessment-contracts/rubrics',
             'services/assessment-core/rubrics/analytics-baseline.json']
    archive = subprocess.check_output(['git', '-C', str(repository), 'archive', commit, *paths])
    with tempfile.TemporaryDirectory() as temporary:
        checkout = Path(temporary) / 'source'
        checkout.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            for member in source.getmembers():
                if not (member.isfile() or member.isdir()) or member.name.startswith('/') or '..' in Path(member.name).parts:
                    raise ValueError('Only regular committed source files are allowed')
            source.extractall(checkout)
        release = Path(temporary) / commit
        shutil.copytree(checkout / 'services' / name, release,
                        ignore=shutil.ignore_patterns('tests', '__pycache__', '*.pyc'))
        spec = importlib.util.spec_from_file_location('committed_export', checkout / 'packages/jev-client/export_release.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.export_release(release, rubric_name=rubric)
        unit = 'deploy/hr-vision-' + name + '.service'
        if not (release / unit).is_file():
            raise ValueError('Committed service unit is required')
        manifest = {'format': 'assessment-service-release-v1', 'service': service, 'commit': commit,
                    'data_compatibility': name + '-v1',
                    'files': {str(p.relative_to(release)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in sorted(release.rglob('*')) if p.is_file()}}
        (release / 'release.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
        output = Path(output)
        if output.exists():
            raise ValueError('Refusing to overwrite existing artifact')
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('xb') as stream, gzip.GzipFile(filename='', mode='wb', fileobj=stream, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as target:
                for path in [release, *sorted(release.rglob('*'))]:
                    info = target.gettarinfo(str(path), str(path.relative_to(release.parent)))
                    info.uid = info.gid = 0
                    info.uname = info.gname = ''
                    info.mtime = 0
                    info.mode = 0o755 if path.is_dir() else 0o644
                    if path.is_file():
                        with path.open('rb') as content:
                            target.addfile(info, content)
                    else:
                        target.addfile(info)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('service', choices=SERVICES)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    manifest = build(args.repo, args.commit, args.service, args.output)
    print(json.dumps({'service': args.service, 'commit': manifest['commit'],
                      'artifact': str(args.output), 'files': len(manifest['files'])}))


if __name__ == '__main__':
    main()
