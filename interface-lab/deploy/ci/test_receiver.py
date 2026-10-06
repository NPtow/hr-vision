import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import receiver as r


class ReceiverTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def bundle(self, target='mockups', mutate=None, extra=None):
        sha, run = 'a' * 40, '12.1'
        entries = {'hr-vision-employer-menu--gallery': {}} if target == 'mockups' else {f'hr-vision-product--{name}': {} for name in ['start', 'manager', 'candidate']}
        files = {'ui/index.html': b'<html/>', 'ui/iframe.html': b'<html/>',
                 'ui/index.json': json.dumps({'entries': entries}).encode(),
                 'ui/deployment.json': json.dumps({'target': target, 'branch': r.BRANCHES[target], 'sha': sha, 'run': run}).encode()}
        if target == 'service':
            files.update({'api/' + name: b'# runtime' for name in r.RUNTIME})
        m = {'version': 1, 'target': target, 'branch': r.BRANCHES[target], 'sha': sha, 'run': run, 'release': sha + '-' + run, 'files': {name: r.digest(data) for name, data in files.items()}}
        if mutate:
            mutate(m, files)
        archive = self.root / 'bundle.tgz'
        with tarfile.open(archive, 'w:gz') as tar:
            for name, data in {**files, 'release.json': json.dumps(m).encode()}.items():
                info = tarfile.TarInfo(name); info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            if extra:
                tar.addfile(extra)
        stage = self.root / 'stage'; stage.mkdir(exist_ok=True)
        return archive, stage, m

    def test_valid_mockups_and_service(self):
        for target in r.BRANCHES:
            a, s, m = self.bundle(target)
            self.assertEqual(r.unpack(a, s, target), m)

    def test_rejects_wrong_destination(self):
        a, s, _ = self.bundle()
        with self.assertRaisesRegex(ValueError, 'target'): r.unpack(a, s, 'service')

    def test_rejects_traversal(self):
        a, s, _ = self.bundle(extra=tarfile.TarInfo('../outside'))
        with self.assertRaisesRegex(ValueError, 'Unsafe'): r.unpack(a, s, 'mockups')

    def test_rejects_aliased_paths(self):
        a, s, _ = self.bundle(extra=tarfile.TarInfo('ui/./index.html'))
        with self.assertRaisesRegex(ValueError, 'Unsafe'): r.unpack(a, s, 'mockups')

    def test_rejects_links(self):
        link = tarfile.TarInfo('ui/link'); link.type = tarfile.SYMTYPE; link.linkname = '/etc/passwd'
        a, s, _ = self.bundle(extra=link)
        with self.assertRaisesRegex(ValueError, 'Links'): r.unpack(a, s, 'mockups')

    def test_rejects_tampering(self):
        a, s, _ = self.bundle(mutate=lambda m, f: f.update({'ui/index.html': b'changed'}))
        with self.assertRaisesRegex(ValueError, 'checksum'): r.unpack(a, s, 'mockups')

    def test_rejects_api_in_mockups(self):
        def mutate(m, f):
            f['api/app.py'] = b'pass'; m['files']['api/app.py'] = r.digest(b'pass')
        a, s, _ = self.bundle(mutate=mutate)
        with self.assertRaisesRegex(ValueError, 'API'): r.unpack(a, s, 'mockups')

    def test_rejects_sensitive_public_file(self):
        def mutate(m, f):
            f['ui/.env'] = b'private'; m['files']['ui/.env'] = r.digest(b'private')
        a, s, _ = self.bundle(mutate=mutate)
        with self.assertRaisesRegex(ValueError, 'public'): r.unpack(a, s, 'mockups')

    def test_refuses_old_commit_without_changing_current(self):
        a, s, m = self.bundle(); r.unpack(a, s, 'mockups')
        base = self.root / 'site'; base.mkdir(); (base / 'current').symlink_to('releases/old')
        with patch.dict(r.BASES, mockups=base), patch.object(r, 'current_branch_sha', return_value='b' * 40):
            with self.assertRaisesRegex(ValueError, 'superseded'): r.activate(s, m)
        self.assertEqual(r.pointer(base), 'releases/old')

    def test_branch_lookup_retries_transient_timeout(self):
        response = io.BytesIO(json.dumps({'object': {'sha': 'a' * 40}}).encode())
        with patch.object(r.urllib.request, 'urlopen', side_effect=[TimeoutError(), response]) as request, patch.object(r.time, 'sleep'):
            self.assertEqual(r.current_branch_sha('main'), 'a' * 40)
            self.assertEqual(request.call_count, 2)

    def test_branch_lookup_does_not_retry_missing_branch(self):
        error = r.urllib.error.HTTPError('https://api.github.com', 404, 'Not found', {}, None)
        with patch.object(r.urllib.request, 'urlopen', side_effect=error) as request, patch.object(r.time, 'sleep'):
            with self.assertRaises(r.urllib.error.HTTPError): r.current_branch_sha('main')
            self.assertEqual(request.call_count, 1)

    def test_failed_health_restores_previous_release(self):
        a, s, m = self.bundle(); r.unpack(a, s, 'mockups')
        base = self.root / 'site'; base.mkdir(); (base / 'current').symlink_to('releases/old')
        with patch.dict(r.BASES, mockups=base), patch.object(r, 'current_branch_sha', return_value=m['sha']), patch.object(r, 'check_origin', side_effect=RuntimeError('unhealthy')):
            with self.assertRaisesRegex(RuntimeError, 'unhealthy'): r.activate(s, m)
        self.assertEqual(r.pointer(base), 'releases/old')

    def test_mockups_never_restart_api(self):
        a, s, m = self.bundle(); r.unpack(a, s, 'mockups')
        base = self.root / 'site'
        with patch.dict(r.BASES, mockups=base), patch.object(r, 'current_branch_sha', return_value=m['sha']), patch.object(r, 'check_origin'), patch.object(r, 'restart_api') as restart:
            r.activate(s, m)
            restart.assert_not_called()
        self.assertEqual(r.pointer(base), 'releases/' + m['release'])

    def test_failed_service_health_restores_both_pointers(self):
        a, s, m = self.bundle('service'); r.unpack(a, s, 'service')
        base = self.root / 'site'; base.mkdir(); (base / 'current').symlink_to('releases/old-ui')
        api = self.root / 'api'; api.mkdir(); (api / 'current').symlink_to('releases/old-api')
        with patch.dict(r.BASES, service=base), patch.object(r, 'API', api), patch.object(r, 'current_branch_sha', return_value=m['sha']), patch.object(r, 'guard_and_backup') as backup, patch.object(r, 'restart_api') as restart, patch.object(r, 'check_origin', side_effect=RuntimeError('unhealthy')):
            with self.assertRaisesRegex(RuntimeError, 'unhealthy'): r.activate(s, m)
            backup.assert_called_once_with(m['release'])
            self.assertEqual(restart.call_count, 2)
        self.assertEqual(r.pointer(base), 'releases/old-ui')
        self.assertEqual(r.pointer(api), 'releases/old-api')


if __name__ == '__main__': unittest.main()
