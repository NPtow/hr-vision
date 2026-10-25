import ast
from contextlib import ExitStack, contextmanager
import importlib.util
import io
import json
import os
import sqlite3
from pathlib import Path
import subprocess
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

    def bundle(self, target='mockups', mutate=None, extra=None, runtime=None):
        sha, run = 'a' * 40, '12.1'
        entries = {'hr-vision-employer-menu--gallery': {}} if target == 'mockups' else {f'hr-vision-product--{name}': {} for name in ['start', 'manager', 'candidate']}
        files = {'ui/index.html': b'<html/>', 'ui/iframe.html': b'<html/>',
                 'ui/index.json': json.dumps({'entries': entries}).encode(),
                 'ui/deployment.json': json.dumps({'target': target, 'branch': r.BRANCHES[target], 'sha': sha, 'run': run}).encode()}
        if target == 'service':
            files.update({'api/' + name: b'# runtime' for name in (r.RUNTIME if runtime is None else runtime)})
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

    def test_accepts_exact_presentation_only_on_mockups(self):
        def add_presentation(m, f):
            name = 'ui/presentations/hrds-2026-10-07/index.html'
            f[name] = b'<html><title>HRDS</title></html>'
            m['files'][name] = r.digest(f[name])
        a, s, m = self.bundle(mutate=add_presentation)
        self.assertEqual(r.unpack(a, s, 'mockups'), m)
        a, s, _ = self.bundle('service', mutate=add_presentation)
        with self.assertRaisesRegex(ValueError, 'public file'):
            r.unpack(a, s, 'service')

    def test_rejects_other_nested_html(self):
        def add_other(m, f):
            name = 'ui/presentations/unapproved/index.html'
            f[name] = b'<html/>'
            m['files'][name] = r.digest(f[name])
        a, s, _ = self.bundle(mutate=add_other)
        with self.assertRaisesRegex(ValueError, 'public file'):
            r.unpack(a, s, 'mockups')

    def test_accepts_only_exact_zoom_public_html(self):
        for target, name, accepted in (
            ('service', 'call/zoom/client.html', True),
            ('service', 'call/zoom/left.html', True),
            ('mockups', 'call/zoom/client.html', True),
            ('mockups', 'call/zoom/left.html', True),
            ('service', 'call/zoom/admin.html', False),
            ('service', 'prototypes/zoom-meeting-sdk/web/client.html', False),
        ):
            def add_client(manifest, files):
                files['ui/' + name] = b'<html/>'
                manifest['files']['ui/' + name] = r.digest(files['ui/' + name])
            with self.subTest(target=target, name=name):
                archive, stage, manifest = self.bundle(target, mutate=add_client)
                if accepted:
                    self.assertEqual(r.unpack(archive, stage, target), manifest)
                else:
                    with self.assertRaisesRegex(ValueError, 'public file'):
                        r.unpack(archive, stage, target)

    def assert_runtime_imports(self, server, runtime):
        """Check absolute/relative imports and package initializers at any depth."""
        def local_paths(module):
            parts = module.split('.')
            result = []
            for i in range(1, len(parts) + 1):
                prefix = '/'.join(parts[:i])
                package = prefix + '/__init__.py'
                if (server / package).is_file():
                    result.append(package)
            leaf = '/'.join(parts) + '.py'
            if (server / leaf).is_file():
                result.append(leaf)
            return result

        for filename in runtime:
            tree = ast.parse((server / filename).read_text())
            package = '.'.join(Path(filename).parts[:-1])
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ''
                    if node.level:
                        module = importlib.util.resolve_name('.' * node.level + module, package)
                    names = [module] + [module + '.' + alias.name for alias in node.names if alias.name != '*']
                else:
                    continue
                for name in names:
                    for imported in local_paths(name):
                        self.assertIn(imported, runtime,
                                      f'{filename}:{node.lineno} imports unpackaged {imported}')

    def test_runtime_includes_local_imports_even_inside_route_handlers(self):
        server = Path(__file__).resolve().parents[2] / 'server'
        self.assert_runtime_imports(server, r.RUNTIME)

    def test_import_scanner_follows_relative_package_imports(self):
        server = self.root / 'server'
        (server / 'nested' / 'child').mkdir(parents=True)
        sources = {
            'app.py': 'def route():\n    from nested.child.client import Client\n',
            'nested/__init__.py': '',
            'nested/child/__init__.py': '',
            'nested/child/client.py': 'from .. import store\nfrom .helpers import Client\n',
            'nested/child/helpers.py': 'class Client: pass\n',
            'nested/store.py': '',
        }
        for name, code in sources.items():
            (server / name).write_text(code)
        self.assert_runtime_imports(server, sources)
        for missing in ('nested/__init__.py', 'nested/child/helpers.py', 'nested/store.py'):
            with self.subTest(missing=missing), self.assertRaisesRegex(AssertionError, 'imports unpackaged'):
                self.assert_runtime_imports(server, sources.keys() - {missing})

    def test_known_complete_service_layouts_are_accepted(self):
        self.assertEqual(len(r.LEGACY_RUNTIME), 19)
        self.assertEqual(r.BOT_RUNTIME - r.LEGACY_RUNTIME, {'candidate_bot.py'})
        self.assertEqual(r.PRE_IMPORT_RUNTIME - r.BOT_RUNTIME, {'recruiter_access.py', 'recruiter_sources.py', 'recruiter_notifications.py'})
        self.assertEqual(r.PRE_OUTREACH_RUNTIME - r.PRE_IMPORT_RUNTIME, {'vacancy_import.py', 'vacancy_sources.py'})
        self.assertEqual(r.PRE_ACCESS_RUNTIME - r.PRE_OUTREACH_RUNTIME, r.OUTREACH_RUNTIME)
        self.assertEqual(r.PRE_CV_RUNTIME - r.PRE_ACCESS_RUNTIME, {'candidate_access.py'})
        self.assertEqual(r.PRE_POOLED_RUNTIME - r.PRE_CV_RUNTIME, {'structured_model.py', 'candidate_resume_model.py', 'candidate_resume_prompts.py'})
        self.assertEqual(r.PRE_EMPLOYER_TELEGRAM_RUNTIME - r.PRE_POOLED_RUNTIME, {'interview_scheduler.py'})
        self.assertEqual(r.PRE_REMINDERS_RUNTIME - r.PRE_EMPLOYER_TELEGRAM_RUNTIME, {'employer_notifications.py', 'telegram_miniapp.py'})
        self.assertEqual(r.PRE_MEETING_NOTICES_RUNTIME - r.PRE_REMINDERS_RUNTIME, {'meeting_reminders.py'})
        self.assertEqual(r.PRE_MEETING_LAB_RUNTIME - r.PRE_MEETING_NOTICES_RUNTIME, {'meeting_notifications.py'})
        self.assertEqual(r.PRE_RECRUITER_COACH_RUNTIME - r.PRE_MEETING_LAB_RUNTIME, {'meeting_lab.py'})
        self.assertEqual(r.PRE_CALL_CLEANUP_RUNTIME - r.PRE_RECRUITER_COACH_RUNTIME, {'recruiter_coach.py'})
        self.assertEqual(r.PRE_OBSERVABILITY_RUNTIME - r.PRE_CALL_CLEANUP_RUNTIME, {'call_cleanup.py', 'media_runtime.py'})
        self.assertEqual(r.PRE_CANDIDATE_CALENDAR_RUNTIME - r.PRE_OBSERVABILITY_RUNTIME, {'observability.py'})
        self.assertEqual(r.PRE_INTERVIEW_ASSISTANT_RUNTIME - r.PRE_CANDIDATE_CALENDAR_RUNTIME, {'candidate_availability.py'})
        self.assertEqual(r.PRE_MATERIALS_RUNTIME - r.PRE_INTERVIEW_ASSISTANT_RUNTIME, {'interview_assistant.py'})
        self.assertEqual(r.PRE_SERVICES_RUNTIME - r.PRE_MATERIALS_RUNTIME, r.MATERIALS_RUNTIME)
        self.assertEqual(r.SERVICES_ONLY_RUNTIME - r.PRE_SERVICES_RUNTIME, r.SERVICES_RUNTIME)
        self.assertEqual(r.ZOOM_ONLY_RUNTIME - r.PRE_ZOOM_RUNTIME, r.ZOOM_RUNTIME)
        self.assertEqual(r.PRE_ASSESSMENT_RUNTIME, r.SERVICES_ONLY_RUNTIME | r.ZOOM_ONLY_RUNTIME)
        self.assertEqual(r.ASSESSMENT_ONLY_RUNTIME - r.ZOOM_ONLY_RUNTIME, {'interview_assessment.py'})
        self.assertEqual(r.PRE_PUBLIC_PROFILE_RUNTIME - r.PRE_ASSESSMENT_RUNTIME, {'interview_assessment.py'})
        self.assertEqual(r.PRE_SELECTION_RUNTIME - r.PRE_PUBLIC_PROFILE_RUNTIME, {'public_profiles.py'})
        self.assertEqual(r.PRE_JEV_RUNTIME - r.PRE_SELECTION_RUNTIME, {'selection.py'})
        self.assertEqual(r.PRE_ASR_DISPATCH_RUNTIME - r.PRE_JEV_RUNTIME, {'assessment_jobs.py'})
        self.assertEqual(r.PRE_SELECTION_STATUS_RUNTIME - r.PRE_ASR_DISPATCH_RUNTIME, {'materials_asr_dispatch.py'})
        self.assertEqual(r.RUNTIME - r.PRE_SELECTION_STATUS_RUNTIME, {'selection_status.py'})
        self.assertIn(frozenset(r.PRE_SELECTION_STATUS_RUNTIME), r.RUNTIME_LAYOUTS)
        self.assertIn(frozenset(r.PRE_ASR_DISPATCH_RUNTIME), r.RUNTIME_LAYOUTS)
        self.assertIn(frozenset(r.PRE_JEV_RUNTIME), r.RUNTIME_LAYOUTS)
        self.assertIn(frozenset(r.PRE_PUBLIC_PROFILE_RUNTIME), r.RUNTIME_LAYOUTS)
        self.assertEqual(r.ZOOM_RUNTIME, {'call_providers.py', 'zoom_media.py', 'materials_zoom.py', 'zoom_recording_retention.py'})
        self.assertEqual(len(r.MATERIALS_RUNTIME), 9)
        for runtime in (r.LEGACY_RUNTIME, r.BOT_RUNTIME, r.PRE_IMPORT_RUNTIME, r.PRE_OUTREACH_RUNTIME,
                        r.PRE_ACCESS_RUNTIME, r.PRE_CV_RUNTIME, r.PRE_POOLED_RUNTIME, r.PRE_EMPLOYER_TELEGRAM_RUNTIME, r.PRE_REMINDERS_RUNTIME, r.PRE_MEETING_NOTICES_RUNTIME, r.PRE_MEETING_LAB_RUNTIME, r.PRE_RECRUITER_COACH_RUNTIME, r.PRE_CALL_CLEANUP_RUNTIME, r.PRE_OBSERVABILITY_RUNTIME, r.PRE_CANDIDATE_CALENDAR_RUNTIME, r.PRE_INTERVIEW_ASSISTANT_RUNTIME, r.PRE_MATERIALS_RUNTIME, r.PRE_ZOOM_RUNTIME, r.SERVICES_ONLY_RUNTIME, r.ZOOM_ONLY_RUNTIME, r.ASSESSMENT_ONLY_RUNTIME, r.PRE_ASSESSMENT_RUNTIME, r.PRE_ASR_DISPATCH_RUNTIME, r.RUNTIME):
            with self.subTest(count=len(runtime)):
                a, s, m = self.bundle('service', runtime=runtime)
                self.assertEqual(r.unpack(a, s, 'service'), m)

    def test_asr_hook_requires_complete_layout_and_is_an_api_change(self):
        hook = 'materials_asr_dispatch.py'
        # The complete previous runtime stays a valid rollback layout; adding
        # the hook to an older partial history must not widen the allowlist.
        archive, stage, manifest = self.bundle('service', runtime=r.PRE_ASR_DISPATCH_RUNTIME)
        self.assertEqual(r.unpack(archive, stage, 'service'), manifest)
        for partial in (r.PRE_SELECTION_RUNTIME | {hook}, r.LEGACY_RUNTIME | {hook}):
            with self.subTest(count=len(partial)):
                archive, stage, _ = self.bundle('service', runtime=partial)
                with self.assertRaisesRegex(ValueError, 'Incomplete API'):
                    r.unpack(archive, stage, 'service')
        source, api = self.root / 'asr-incoming', self.root / 'asr-installed'
        for folder in (source, api / 'current'):
            for name in r.PRE_ASR_DISPATCH_RUNTIME:
                file = folder / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('# unchanged previous runtime')
        with patch.object(r, 'API', api):
            self.assertFalse(r.api_changed(source))
            (source / hook).write_text('# new opt-in dispatcher')
            self.assertTrue(r.api_changed(source))
            (api / 'current' / hook).write_text('# new opt-in dispatcher')
            self.assertFalse(r.api_changed(source))
            (source / hook).unlink()
            self.assertTrue(r.api_changed(source))

    def test_partial_employer_telegram_layout_is_rejected(self):
        for module in ('employer_notifications.py', 'telegram_miniapp.py'):
            a, staging, _ = self.bundle('service', runtime=r.RUNTIME - {module})
            with self.assertRaises(ValueError):
                r.unpack(a, staging, 'service')

    def test_partial_materials_runtime_is_rejected(self):
        for module in r.MATERIALS_RUNTIME:
            with self.subTest(module=module):
                a, stage, _ = self.bundle('service', runtime=r.RUNTIME - {module})
                with self.assertRaisesRegex(ValueError, 'Incomplete API'):
                    r.unpack(a, stage, 'service')

    def test_partial_zoom_runtime_is_rejected(self):
        for module in r.ZOOM_RUNTIME:
            with self.subTest(module=module):
                archive, stage, _ = self.bundle('service', runtime=r.RUNTIME - {module})
                with self.assertRaisesRegex(ValueError, 'Incomplete API'):
                    r.unpack(archive, stage, 'service')

    def test_partial_recruiter_layout_is_rejected(self):
        for module in r.PRE_ACCESS_RUNTIME - r.BOT_RUNTIME:
            a, s, _ = self.bundle('service', runtime=r.RUNTIME - {module})
            with self.assertRaisesRegex(ValueError, 'Incomplete API'):
                r.unpack(a, s, 'service')

    def test_partial_legacy_layout_is_rejected(self):
        a, s, _ = self.bundle('service', runtime=r.LEGACY_RUNTIME - {'candidate_notifications.py'})
        with self.assertRaisesRegex(ValueError, 'Incomplete API'):
            r.unpack(a, s, 'service')

    def test_partial_import_layout_is_rejected(self):
        for name in ('vacancy_import.py', 'vacancy_sources.py'):
            a, s, _ = self.bundle('service', runtime=r.PRE_OUTREACH_RUNTIME - {name})
            with self.assertRaisesRegex(ValueError, 'Incomplete API'):
                r.unpack(a, s, 'service')

    def test_partial_outreach_layout_is_rejected(self):
        for name in r.OUTREACH_RUNTIME:
            with self.subTest(missing=name):
                a, s, _ = self.bundle('service', runtime=r.RUNTIME - {name})
                with self.assertRaisesRegex(ValueError, 'Incomplete API'):
                    r.unpack(a, s, 'service')
        a, s, _ = self.bundle('service', runtime=r.PRE_OUTREACH_RUNTIME | {'candidate_booking.py'})
        with self.assertRaisesRegex(ValueError, 'Incomplete API'):
            r.unpack(a, s, 'service')

    def test_candidate_access_cannot_ship_without_its_booking_dependencies(self):
        for runtime in (r.PRE_OUTREACH_RUNTIME | {'candidate_access.py'},
                        r.RUNTIME - {'candidate_booking.py'}):
            a, s, _ = self.bundle('service', runtime=runtime)
            with self.assertRaisesRegex(ValueError, 'Incomplete API'):
                r.unpack(a, s, 'service')

    def test_nested_runtime_change_detection_and_rollback(self):
        source, api = self.root / 'nested-incoming', self.root / 'nested-installed'
        for folder in (source, api / 'current'):
            for name in r.RUNTIME:
                path = folder / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('# same')
        with patch.object(r, 'API', api):
            self.assertFalse(r.api_changed(source))
            nested = source / 'hh_browser' / 'bridge.py'
            nested.write_text('# changed bridge')
            self.assertTrue(r.api_changed(source))
            nested.write_text('# same')
            for name in r.RUNTIME - r.PRE_OUTREACH_RUNTIME:
                (source / name).unlink()
            self.assertTrue(r.api_changed(source))
            for name in r.RUNTIME - r.PRE_OUTREACH_RUNTIME:
                (api / 'current' / name).unlink()
            self.assertFalse(r.api_changed(source))

    def test_runtime_change_detection_handles_both_layout_directions(self):
        source, api = self.root / 'incoming-api', self.root / 'installed-api'
        source.mkdir()
        (api / 'current').mkdir(parents=True)
        for name in r.LEGACY_RUNTIME:
            (source / name).write_text('# same')
            (api / 'current' / name).write_text('# same')
        with patch.object(r, 'API', api):
            self.assertFalse(r.api_changed(source))
            (source / 'candidate_bot.py').write_text('# bot')
            self.assertTrue(r.api_changed(source))
            (api / 'current' / 'candidate_bot.py').write_text('# bot')
            self.assertFalse(r.api_changed(source))
            (source / 'candidate_bot.py').unlink()
            self.assertTrue(r.api_changed(source))

    def test_legacy_runtime_still_checks_health_and_rolls_back(self):
        a, s, m = self.bundle('service', runtime=r.LEGACY_RUNTIME)
        r.unpack(a, s, 'service')
        base, api = self.root / 'site', self.root / 'api'
        base.mkdir(); (base / 'current').symlink_to('releases/old-ui')
        api.mkdir(); (api / 'current').symlink_to('releases/old-api')
        self.runtime(api / 'releases' / 'old-api', r.LEGACY_RUNTIME)
        database = self.outbox()
        with patch.dict(r.BASES, service=base), patch.object(r, 'API', api), \
                patch.object(r, 'DATABASE', database), patch.object(r, 'stop_api'), \
                patch.object(r, 'current_branch_sha', return_value=m['sha']), \
                patch.object(r, 'guard_and_backup') as backup, \
                patch.object(r, 'restart_api') as restart, \
                patch.object(r, 'check_origin', side_effect=RuntimeError('unhealthy')) as health, \
                patch.object(r, 'pause_materials', return_value=[]):
            with self.assertRaisesRegex(RuntimeError, 'unhealthy'):
                r.activate(s, m)
            backup.assert_called_once_with(m['release'])
            health.assert_called_once_with('service', m['sha'])
            self.assertEqual(restart.call_count, 2)
        self.assertEqual(r.pointer(base), 'releases/old-ui')
        self.assertEqual(r.pointer(api), 'releases/old-api')

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

    def test_service_requires_new_workflow_runtime(self):
        def mutate(m, f):
            del f['api/employer_workflow.py']
            del m['files']['api/employer_workflow.py']
        a, s, _ = self.bundle('service', mutate=mutate)
        with self.assertRaisesRegex(ValueError, 'Incomplete API'):
            r.unpack(a, s, 'service')

    def test_service_runtime_allowlist_stays_closed(self):
        def mutate(m, f):
            f['api/employer_private_keys.py'] = b'not allowed'
            m['files']['api/employer_private_keys.py'] = r.digest(f['api/employer_private_keys.py'])
        a, s, _ = self.bundle('service', mutate=mutate)
        with self.assertRaisesRegex(ValueError, 'Unexpected API file'):
            r.unpack(a, s, 'service')

    def test_materials_dependencies_config_docs_and_tests_are_not_runtime(self):
        for name in ('requirements-materials.txt', 'requirements-materials.lock', 'requirements-api-extras.lock',
                     'MATERIALS.md', 'test_materials_worker.py', 'materials.env'):
            def mutate(m, files):
                files['api/' + name] = b'not runtime'
                m['files']['api/' + name] = r.digest(files['api/' + name])
            with self.subTest(name=name):
                a, stage, _ = self.bundle('service', mutate=mutate)
                with self.assertRaisesRegex(ValueError, 'Unexpected API file'):
                    r.unpack(a, stage, 'service')

    def test_service_package_allowlist_stays_closed(self):
        def mutate(m, f):
            # The operator patch generator and private browser config are not API runtime.
            f['api/hh_browser/integration.py'] = b'not allowed'
            m['files']['api/hh_browser/integration.py'] = r.digest(b'not allowed')
        a, s, _ = self.bundle('service', mutate=mutate)
        with self.assertRaisesRegex(ValueError, 'Unexpected API file'):
            r.unpack(a, s, 'service')

    def test_actual_packager_and_receiver_agree_on_complete_api(self):
        # Exercise the JS release producer against the Python receiver rather than
        # duplicating either allowlist in a test-only parser.
        fixture = self.root / 'package-input'
        (fixture / 'server').mkdir(parents=True)
        (fixture / 'storybook-static').mkdir()
        for name in r.RUNTIME:
            source = fixture / 'server' / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text('# synthetic runtime fixture\n')
        # Dependency installation/configuration is outside the public release
        # producer. Even adjacent files must not leak into an API archive.
        hook = fixture / 'server' / 'materials_asr_dispatch.py'
        hook.write_text('# bounded, opt-in ASR recording notification fixture\n')
        non_runtime = ('requirements-materials.txt', 'requirements-materials.lock', 'requirements-api-extras.lock',
                       'MATERIALS.md', 'test_materials_worker.py', 'materials.env')
        for name in non_runtime:
            (fixture / 'server' / name).write_text('must not be packaged\n')
        for name in ('index.html', 'iframe.html'):
            (fixture / 'storybook-static' / name).write_text('<html/>')
        zoom_client = fixture / 'storybook-static' / 'call' / 'zoom'
        zoom_client.mkdir(parents=True)
        for name, body in (('client.html', '<html/>'), ('left.html', '<html/>'), ('client.js', '// client'), ('client.css', 'body {}')):
            (zoom_client / name).write_text(body)
        entries = {f'hr-vision-product--{name}': {} for name in ('start', 'manager', 'candidate')}
        (fixture / 'storybook-static' / 'index.json').write_text(json.dumps({'entries': entries}))
        script = Path(__file__).resolve().parents[2] / 'scripts' / 'package-release.mjs'
        environment = {**os.environ, 'HR_BUILD_TARGET': 'service', 'GITHUB_REF_NAME': 'main',
                       'GITHUB_SHA': 'b' * 40, 'GITHUB_RUN_ID': '13', 'GITHUB_RUN_ATTEMPT': '1'}
        subprocess.run(['node', str(script)], cwd=fixture, env=environment, check=True,
                       capture_output=True, text=True, timeout=30)
        stage = self.root / 'producer-stage'
        stage.mkdir()
        manifest = r.unpack(fixture / 'release.tgz', stage, 'service')
        self.assertEqual({p[4:] for p in manifest['files'] if p.startswith('api/')}, r.RUNTIME)
        self.assertEqual((stage / 'api' / hook.name).read_bytes(), hook.read_bytes())
        self.assertEqual(manifest['files']['api/' + hook.name], r.digest(hook.read_bytes()))
        self.assertTrue(all('api/' + name not in manifest['files'] for name in non_runtime))
        self.assertTrue({'ui/call/zoom/client.html', 'ui/call/zoom/left.html', 'ui/call/zoom/client.js', 'ui/call/zoom/client.css'} <= manifest['files'].keys())

        # A new SDK frame does not allow other HTML or PoC screens to ship.
        unexpected = zoom_client / 'admin.html'
        unexpected.write_text('<html/>')
        invalid = subprocess.run(['node', str(script)], cwd=fixture, env=environment,
                                 capture_output=True, text=True, timeout=30)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn('Unexpected public file: call/zoom/admin.html', invalid.stderr)
        unexpected.unlink()

        # The operator lives in manager?workspace=outreach, never a fourth public story.
        entries['hr-vision-product--outreach'] = {}
        (fixture / 'storybook-static' / 'index.json').write_text(json.dumps({'entries': entries}))
        invalid = subprocess.run(['node', str(script)], cwd=fixture, env=environment,
                                 capture_output=True, text=True, timeout=30)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn('exactly start, manager and candidate', invalid.stderr)

        # Shared browser-assets are copied into mockups too; the same two SDK
        # pages may ship, but the release must still contain no API files.
        (fixture / 'storybook-static' / 'index.json').write_text(json.dumps({
            'entries': {'hr-vision-employer-menu--gallery': {}},
        }))
        mockup_environment = {**environment, 'HR_BUILD_TARGET': 'mockups', 'GITHUB_REF_NAME': 'codex/mockups'}
        subprocess.run(['node', str(script)], cwd=fixture, env=mockup_environment, check=True,
                       capture_output=True, text=True, timeout=30)
        mockup_stage = self.root / 'producer-mockup-stage'
        mockup_stage.mkdir()
        mockup_manifest = r.unpack(fixture / 'release.tgz', mockup_stage, 'mockups')
        self.assertTrue({'ui/call/zoom/client.html', 'ui/call/zoom/left.html'} <= mockup_manifest['files'].keys())
        self.assertFalse(any(name.startswith('api/') for name in mockup_manifest['files']))

    def test_rejects_sensitive_public_file(self):
        def mutate(m, f):
            f['ui/.env'] = b'private'; m['files']['ui/.env'] = r.digest(b'private')
        a, s, _ = self.bundle(mutate=mutate)
        with self.assertRaisesRegex(ValueError, 'public'): r.unpack(a, s, 'mockups')

    def test_replay_assets_survive_releases_without_scripts_overwrites_or_extended_ttl(self):
        old, new, later = [self.root / name for name in ('old', 'new', 'later')]
        for directory in (old, new, later):
            (directory / 'assets').mkdir(parents=True)
        for name in ('old.css', 'font.woff2', 'shared.css', 'old.js'):
            (old / 'assets' / name).write_text('old')
        (new / 'assets/shared.css').write_text('new')
        with patch.object(r.time, 'time', return_value=1000):
            r.retain_replay_assets(old, new)
        self.assertEqual((new / 'assets/old.css').stat().st_ino, (old / 'assets/old.css').stat().st_ino)
        self.assertEqual((new / 'assets/shared.css').read_text(), 'new')
        self.assertTrue((new / 'assets/font.woff2').exists())
        self.assertFalse((new / 'assets/old.js').exists())
        with patch.object(r.time, 'time', return_value=1000 + 26 * 3600):
            r.retain_replay_assets(new, later)
        self.assertFalse((later / 'assets/old.css').exists())
        self.assertTrue((later / 'assets/shared.css').exists())
        self.assertTrue((old / 'assets/old.css').exists())

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

    def test_live_call_guard_checks_legacy_and_dsa_scenarios(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE scenario (id INTEGER PRIMARY KEY, body TEXT)')
            db.execute('INSERT INTO scenario VALUES (1, ?)', (json.dumps({'candidates': {}}),))
            r.guard_live_interviews(db)  # Compatible with the pre-migration database.
            db.execute('CREATE TABLE scenario_variants (scope TEXT PRIMARY KEY, body TEXT)')
            db.execute('INSERT INTO scenario_variants VALUES (?,?)', ('dsa', json.dumps({'candidates': {'dsa-1': {'meeting': {'status': 'live'}}}})))
            with self.assertRaisesRegex(ValueError, 'interview is live'):
                r.guard_live_interviews(db)
            db.execute('UPDATE scenario_variants SET body=?', (json.dumps({'candidates': {}}),))
            r.guard_live_interviews(db)

    def test_material_units_drain_and_resume_in_order(self):
        states = iter(('loaded', 'active', 'inactive') * len(r.MATERIALS_UNITS))
        def systemctl(argv, **kwargs):
            if argv[1] in ('show', 'is-active'):
                return subprocess.CompletedProcess(argv, 0, stdout=next(states))
            return subprocess.CompletedProcess(argv, 0)
        with patch.object(r.subprocess, 'run', side_effect=systemctl) as run:
            active = r.pause_materials()
            self.assertEqual(active, list(r.MATERIALS_UNITS))
            r.resume_materials(active)
        commands = [call.args[0][1:] for call in run.call_args_list]
        self.assertEqual([item for item in commands if item[0] == 'stop'],
                         [['stop', unit] for unit in r.MATERIALS_UNITS])
        self.assertEqual([item for item in commands if item[0] == 'start'],
                         [['start', unit] for unit in reversed(r.MATERIALS_UNITS)])

    def test_uninstalled_material_units_do_not_trigger_systemd_mutations(self):
        with patch.object(r.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='not-found')) as run:
            self.assertEqual(r.pause_materials(), [])
            r.resume_materials([])
        self.assertEqual([call.args[0][1] for call in run.call_args_list], ['show'] * len(r.MATERIALS_UNITS))

    def test_failed_service_health_restores_both_pointers(self):
        a, s, m = self.bundle('service'); r.unpack(a, s, 'service')
        base = self.root / 'site'; base.mkdir(); (base / 'current').symlink_to('releases/old-ui')
        api = self.root / 'api'; api.mkdir(); (api / 'current').symlink_to('releases/old-api')
        self.runtime(api / 'releases' / 'old-api', r.PRE_MEETING_NOTICES_RUNTIME)
        database = self.outbox()
        with patch.dict(r.BASES, service=base), patch.object(r, 'API', api), patch.object(r, 'DATABASE', database), patch.object(r, 'stop_api'), patch.object(r, 'current_branch_sha', return_value=m['sha']), patch.object(r, 'guard_and_backup') as backup, patch.object(r, 'restart_api') as restart, patch.object(r, 'check_origin', side_effect=RuntimeError('unhealthy')), patch.object(r, 'pause_materials', return_value=[]):
            with self.assertRaisesRegex(RuntimeError, 'unhealthy'): r.activate(s, m)
            backup.assert_called_once_with(m['release'])
            self.assertEqual(restart.call_count, 2)
        self.assertEqual(r.pointer(base), 'releases/old-ui')
        self.assertEqual(r.pointer(api), 'releases/old-api')

    def runtime(self, folder, names):
        for name in names:
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# installed runtime')
        return folder

    def outbox(self):
        database = self.root / 'journey.sqlite3'
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE cn_outbox (event_type TEXT, payload_json TEXT, status TEXT, candidate_id TEXT)')
        return database

    def insert_notice(self, database, event='meeting.completed', payload=None, status='pending', owner='candidate'):
        with sqlite3.connect(database) as db:
            db.execute('INSERT INTO cn_outbox VALUES (?,?,?,?)',
                       (event, json.dumps({'lifecycle': True}) if payload is None else payload, status, owner))

    @contextmanager
    def service_activation(self, incoming=None, installed=None):
        a, stage, manifest = self.bundle('service', runtime=r.RUNTIME if incoming is None else incoming)
        r.unpack(a, stage, 'service')
        base, api = self.root / 'site', self.root / 'api'
        base.mkdir(); (base / 'current').symlink_to('releases/old-ui')
        api.mkdir(); (api / 'current').symlink_to('releases/old-api')
        self.runtime(api / 'releases' / 'old-api', r.PRE_MEETING_NOTICES_RUNTIME if installed is None else installed)
        database = self.outbox()
        with ExitStack() as stack:
            stack.enter_context(patch.dict(r.BASES, service=base))
            for key, value in [('API', api), ('DATABASE', database)]:
                stack.enter_context(patch.object(r, key, value))
            stack.enter_context(patch.object(r, 'current_branch_sha', return_value=manifest['sha']))
            stack.enter_context(patch.object(r, 'guard_and_backup'))
            stack.enter_context(patch.object(r, 'pause_materials', return_value=[]))
            stop = stack.enter_context(patch.object(r, 'stop_api'))
            restart = stack.enter_context(patch.object(r, 'restart_api'))
            health = stack.enter_context(patch.object(r, 'check_origin'))
            yield stage, manifest, base, api, database, stop, restart, health

    def test_old_calendar_runtime_cannot_erase_saved_rules_or_timezone(self):
        old = self.runtime(self.root / 'old-calendar', r.PRE_CANDIDATE_CALENDAR_RUNTIME)
        current = self.runtime(self.root / 'with-calendar', r.RUNTIME)
        with sqlite3.connect(self.outbox()) as db:
            db.execute('CREATE TABLE cp_candidates (profile TEXT)')
            db.execute('INSERT INTO cp_candidates VALUES (?)', (json.dumps({'availability': []}),))
            r.guard_outbox_runtime(db, old)
            for profile in ({'timezone': 'Europe/Moscow'}, {'availabilitySchedule': {'weekly': [{'id': 'x'}], 'dates': []}},
                            {'availabilitySchedule': {'weekly': [], 'dates': [{'date': '2026-10-21'}]}}):
                db.execute('UPDATE cp_candidates SET profile=?', (json.dumps(profile),))
                with self.assertRaisesRegex(ValueError, 'Candidate calendar preferences'):
                    r.guard_outbox_runtime(db, old)
                r.guard_outbox_runtime(db, current)

    def test_legacy_target_refuses_lifecycle_for_both_roles_and_all_states(self):
        old = self.runtime(self.root / 'old', r.PRE_MEETING_NOTICES_RUNTIME)
        with sqlite3.connect(self.outbox()) as db:
            for event in ('interview.booked', 'meeting.proposed', 'meeting.confirmed', 'meeting.completed'):
                for owner in ('candidate', 'employer:account'):
                    for status in ('pending', 'sending', 'error', 'delivered', 'disabled', 'unbound', 'unverified'):
                        with self.subTest(event=event, owner=owner, status=status):
                            db.execute('DELETE FROM cn_outbox')
                            db.execute('INSERT INTO cn_outbox VALUES (?,?,?,?)',
                                       (event, '{"lifecycle":true}', status, owner))
                            before = db.execute('SELECT * FROM cn_outbox').fetchall()
                            with self.assertRaisesRegex(ValueError, 'compatible forward recovery'):
                                r.guard_outbox_runtime(db, old)
                            self.assertEqual(db.execute('SELECT * FROM cn_outbox').fetchall(), before)

    def test_ordinary_rows_and_historical_schemas_allow_old_runtime(self):
        old = self.runtime(self.root / 'old', r.LEGACY_RUNTIME)
        with sqlite3.connect(':memory:') as db:
            r.guard_outbox_runtime(db, old)
            db.execute('CREATE TABLE cn_outbox (event_type TEXT)')
            db.execute("INSERT INTO cn_outbox VALUES ('meeting.confirmed')")
            r.guard_outbox_runtime(db, old)
            db.execute('ALTER TABLE cn_outbox ADD COLUMN payload_json TEXT')
            for payload in (None, '{}', '{"application_id":"application"}'):
                db.execute('UPDATE cn_outbox SET payload_json=?', (payload,))
                r.guard_outbox_runtime(db, old)
            db.execute("UPDATE cn_outbox SET event_type='meeting.completed'")
            with self.assertRaisesRegex(ValueError, 'Lifecycle outbox'):
                r.guard_outbox_runtime(db, old)

    def test_pre_lab_runtime_still_handles_lifecycle_rows(self):
        prior = self.runtime(self.root / 'prior', r.PRE_MEETING_LAB_RUNTIME)
        with sqlite3.connect(self.outbox()) as db:
            db.execute("INSERT INTO cn_outbox VALUES ('meeting.completed', ?, 'pending', 'candidate')",
                       ('{"lifecycle":true}',))
            r.guard_outbox_runtime(db, prior)

    def test_call_cleanup_schema_requires_worker_even_when_queue_is_empty(self):
        prior = self.runtime(self.root / 'before-cleanup', r.PRE_CALL_CLEANUP_RUNTIME)
        current = self.runtime(self.root / 'with-cleanup', r.RUNTIME)
        with sqlite3.connect(':memory:') as db:
            r.guard_outbox_runtime(db, prior)
            db.execute('CREATE TABLE cp_call_cleanup (closed_at REAL)')
            for state in ('empty', 'pending', 'closed'):
                db.execute('DELETE FROM cp_call_cleanup')
                if state != 'empty':
                    db.execute('INSERT INTO cp_call_cleanup VALUES (?)', (None if state == 'pending' else 123,))
                with self.subTest(state=state), self.assertRaisesRegex(ValueError, 'Call cleanup schema'):
                    r.guard_outbox_runtime(db, prior)
                r.guard_outbox_runtime(db, current)

    def test_materials_sources_block_old_runtime_even_when_terminal_or_disabled(self):
        prior = self.runtime(self.root / 'before-materials', r.PRE_MATERIALS_RUNTIME)
        current = self.runtime(self.root / 'with-materials', r.RUNTIME)
        with sqlite3.connect(':memory:') as db:
            r.guard_outbox_runtime(db, prior)
            db.execute('CREATE TABLE media_sources (id TEXT, state TEXT, metadata TEXT)')
            r.guard_outbox_runtime(db, prior)  # Feature-off empty schema is reversible.
            for state, metadata in (('pending', '{}'), ('ready', '{}'),
                                    ('unsupported', '{"capture_disabled":true}')):
                db.execute('DELETE FROM media_sources')
                db.execute('INSERT INTO media_sources VALUES (?,?,?)', ('source', state, metadata))
                before = db.execute('SELECT * FROM media_sources').fetchall()
                with self.subTest(state=state), self.assertRaisesRegex(ValueError, 'Materials catalog.*forward recovery'):
                    r.guard_outbox_runtime(db, prior)
                self.assertEqual(db.execute('SELECT * FROM media_sources').fetchall(), before)
                r.guard_outbox_runtime(db, current)

    def test_core_source_data_requires_services_runtime(self):
        prior = self.runtime(self.root / 'before-services', r.PRE_SERVICES_RUNTIME)
        current = self.runtime(self.root / 'with-services', r.RUNTIME)
        for table in ('portal_materials_outbox', 'core_material_receipts', 'core_material_channels', 'core_material_asset_bindings'):
            with self.subTest(table=table), sqlite3.connect(':memory:') as db:
                r.guard_outbox_runtime(db, prior)
                db.execute('CREATE TABLE ' + table + ' (id TEXT)')
                r.guard_outbox_runtime(db, prior)
                db.execute('INSERT INTO ' + table + ' VALUES (?)', ('durable',))
                with self.assertRaisesRegex(ValueError, 'Core/source data.*forward recovery'):
                    r.guard_outbox_runtime(db, prior)
                r.guard_outbox_runtime(db, current)

    def test_core_source_view_does_not_bypass_runtime_guard(self):
        prior = self.runtime(self.root / 'before-services', r.PRE_SERVICES_RUNTIME)
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE VIEW portal_materials_outbox AS SELECT 1 WHERE 0')
            with self.assertRaisesRegex(ValueError, 'Cannot verify core/source schema'):
                r.guard_outbox_runtime(db, prior)

    def test_assessment_data_requires_capable_runtime_and_empty_history_stays_compatible(self):
        prior = self.runtime(self.root / 'before-assessments', r.PRE_ASSESSMENT_RUNTIME)
        current = self.runtime(self.root / 'with-assessments', r.RUNTIME)
        with sqlite3.connect(':memory:') as db:
            r.guard_outbox_runtime(db, prior)
            for table in ('ia_assessment_revisions', 'ia_assessment_feedback',
                          'ia_assessment_feedback_events', 'ia_assessment_commands'):
                with self.subTest(table=table):
                    db.execute('CREATE TABLE ' + table + ' (id TEXT)')
                    r.guard_outbox_runtime(db, prior)
                    db.execute('INSERT INTO ' + table + " VALUES ('preserved-assessment-row')")
                    with self.assertRaisesRegex(ValueError, 'assessment data.*interview_assessment'):
                        r.guard_outbox_runtime(db, prior)
                    self.assertEqual(db.execute('SELECT * FROM ' + table).fetchall(), [('preserved-assessment-row',)])
                    r.guard_outbox_runtime(db, current)
                    db.execute('DROP TABLE ' + table)

    def test_jev_queue_requires_runtime_and_cannot_be_hidden_by_view(self):
        prior = self.runtime(self.root / 'before-jev', r.PRE_JEV_RUNTIME)
        current = self.runtime(self.root / 'with-jev', r.RUNTIME)
        with sqlite3.connect(':memory:') as db:
            for table in ('as_bindings', 'as_matches'):
                db.execute('CREATE TABLE ' + table + ' (id TEXT)')
                r.guard_outbox_runtime(db, prior)
                db.execute('INSERT INTO ' + table + " VALUES ('queued')")
                with self.assertRaisesRegex(ValueError, 'Jev queue data'):
                    r.guard_outbox_runtime(db, prior)
                r.guard_outbox_runtime(db, current)
                db.execute('DROP TABLE ' + table)
                db.execute('CREATE VIEW ' + table + ' AS SELECT 1 WHERE 0')
                with self.assertRaisesRegex(ValueError, 'Cannot verify Jev'):
                    r.guard_outbox_runtime(db, prior)
                db.execute('DROP VIEW ' + table)

    def test_assessment_view_does_not_bypass_runtime_guard(self):
        prior = self.runtime(self.root / 'before-assessments', r.PRE_ASSESSMENT_RUNTIME)
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE VIEW ia_assessment_revisions AS SELECT 1 WHERE 0')
            with self.assertRaisesRegex(ValueError, 'Cannot verify interview assessment schema'):
                r.guard_outbox_runtime(db, prior)

    def test_selection_receipts_require_selection_runtime_without_erasing_data(self):
        prior = self.runtime(self.root / 'before-selection', r.PRE_SELECTION_RUNTIME)
        current = self.runtime(self.root / 'with-selection', r.RUNTIME)
        with sqlite3.connect(':memory:') as db:
            for table in ('sl_core_results', 'sl_nominations'):
                db.execute('CREATE TABLE ' + table + ' (id TEXT)')
                r.guard_outbox_runtime(db, prior)
                db.execute('INSERT INTO ' + table + " VALUES ('preserved')")
                with self.assertRaisesRegex(ValueError, 'Selection data requires selection.py'):
                    r.guard_outbox_runtime(db, prior)
                r.guard_outbox_runtime(db, current)
                self.assertEqual(db.execute('SELECT * FROM ' + table).fetchall(), [('preserved',)])
                db.execute('DROP TABLE ' + table)

    def test_explicit_assessment_downgrade_is_refused_without_changing_data_or_queues(self):
        with self.service_activation(incoming=r.PRE_ASSESSMENT_RUNTIME, installed=r.RUNTIME) as (stage, m, base, api, database, stop, restart, health):
            with sqlite3.connect(database) as db:
                db.execute('CREATE TABLE ia_assessment_revisions (revision_id TEXT)')
                db.execute("INSERT INTO ia_assessment_revisions VALUES ('approved-orbit')")
            self.insert_notice(database, event='meeting.confirmed', payload='{}')
            with sqlite3.connect(database) as db:
                queue = db.execute('SELECT * FROM cn_outbox').fetchall()
            started = []
            restart.side_effect = lambda: started.append(r.pointer(api))
            with self.assertRaisesRegex(ValueError, 'assessment data.*interview_assessment'):
                r.activate(stage, m)
            self.assertEqual(started, ['releases/old-api'])
            self.assertEqual(stop.call_count, 2)
            health.assert_not_called()
            self.assertEqual(r.pointer(api), 'releases/old-api')
            self.assertEqual(r.pointer(base), 'releases/old-ui')
            with sqlite3.connect(database) as db:
                self.assertEqual(db.execute('SELECT * FROM ia_assessment_revisions').fetchall(), [('approved-orbit',)])
                self.assertEqual(db.execute('SELECT * FROM cn_outbox').fetchall(), queue)

    def test_last_assessment_commit_after_failed_health_blocks_incapable_fallback(self):
        with self.service_activation(installed=r.PRE_ASSESSMENT_RUNTIME) as (stage, m, base, api, database, stop, restart, health):
            health.side_effect = RuntimeError('unhealthy')
            def stopped():
                if stop.call_count == 2:
                    with sqlite3.connect(database) as db:
                        db.execute('CREATE TABLE ia_assessment_revisions (revision_id TEXT)')
                        db.execute("INSERT INTO ia_assessment_revisions VALUES ('last-assessment-commit')")
            stop.side_effect = stopped
            with self.assertRaisesRegex(RuntimeError, 'rollback refused.*assessment data'):
                r.activate(stage, m)
            restart.assert_called_once()
            self.assertEqual(r.pointer(api), 'releases/' + m['release'])
            with sqlite3.connect(database) as db:
                self.assertEqual(db.execute('SELECT * FROM ia_assessment_revisions').fetchall(), [('last-assessment-commit',)])

    def test_compatible_assessment_retry_and_fallback_preserve_existing_rows(self):
        with self.service_activation(installed=r.RUNTIME) as (stage, m, base, api, database, stop, restart, health):
            with sqlite3.connect(database) as db:
                db.execute('CREATE TABLE ia_assessment_revisions (revision_id TEXT)')
                db.execute("INSERT INTO ia_assessment_revisions VALUES ('approved-orbit')")
            health.side_effect = RuntimeError('unhealthy')
            with self.assertRaisesRegex(RuntimeError, '^unhealthy$'):
                r.activate(stage, m)
            self.assertEqual(stop.call_count, 2)
            self.assertEqual(restart.call_count, 2)
            self.assertEqual(r.pointer(api), 'releases/old-api')
            self.assertEqual(r.pointer(base), 'releases/old-ui')
            # Retry with a fresh release identity, retaining the same immutable DB.
            retried = {**m, 'run': '12.2', 'release': m['sha'] + '-12.2'}
            health.side_effect = None
            r.activate(stage, retried)
            self.assertEqual(r.pointer(api), 'releases/' + retried['release'])
            self.assertEqual(r.pointer(base), 'releases/' + retried['release'])
            with sqlite3.connect(database) as db:
                self.assertEqual(db.execute('SELECT * FROM ia_assessment_revisions').fetchall(), [('approved-orbit',)])

    def test_materials_view_does_not_bypass_old_runtime_guard(self):
        prior = self.runtime(self.root / 'before-materials', r.PRE_MATERIALS_RUNTIME)
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE VIEW media_sources AS SELECT 1 WHERE 0')
            with self.assertRaisesRegex(ValueError, 'Cannot verify materials catalog schema'):
                r.guard_outbox_runtime(db, prior)

    def test_zoom_choices_require_zoom_runtime_before_and_after_room_creation(self):
        prior = self.runtime(self.root / 'before-zoom', r.PRE_ZOOM_RUNTIME)
        current = self.runtime(self.root / 'with-zoom', r.RUNTIME)
        with sqlite3.connect(':memory:') as db:
            r.guard_outbox_runtime(db, prior)
            db.execute('CREATE TABLE cp_call_provider_choices (meeting_id TEXT, provider TEXT, state TEXT)')
            r.guard_outbox_runtime(db, prior)
            db.execute("INSERT INTO cp_call_provider_choices VALUES ('daily-meeting','daily','ready')")
            r.guard_outbox_runtime(db, prior)
            for state in ('selected', 'creating', 'ready'):
                db.execute("DELETE FROM cp_call_provider_choices WHERE provider='zoom'")
                db.execute("INSERT INTO cp_call_provider_choices VALUES ('zoom-meeting','zoom',?)", (state,))
                before = db.execute('SELECT * FROM cp_call_provider_choices').fetchall()
                with self.subTest(state=state), self.assertRaisesRegex(ValueError, 'Zoom meetings.*forward recovery'):
                    r.guard_outbox_runtime(db, prior)
                self.assertEqual(db.execute('SELECT * FROM cp_call_provider_choices').fetchall(), before)
                r.guard_outbox_runtime(db, current)

    def test_zoom_choices_schema_fails_closed_for_old_runtime(self):
        prior = self.runtime(self.root / 'before-zoom', r.PRE_ZOOM_RUNTIME)
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE VIEW cp_call_provider_choices AS SELECT 1 WHERE 0')
            with self.assertRaisesRegex(ValueError, 'Cannot verify call provider choices schema'):
                r.guard_outbox_runtime(db, prior)
            db.execute('DROP VIEW cp_call_provider_choices')
            db.execute('CREATE TABLE cp_call_provider_choices (meeting_id TEXT)')
            with self.assertRaisesRegex(ValueError, 'Cannot verify call provider choices schema'):
                r.guard_outbox_runtime(db, prior)

    def test_explicit_pre_zoom_rollback_restores_current_compatible_runtime(self):
        with self.service_activation(incoming=r.PRE_ZOOM_RUNTIME, installed=r.RUNTIME) as (stage, m, base, api, database, stop, restart, health):
            with sqlite3.connect(database) as db:
                db.execute('CREATE TABLE cp_call_provider_choices (meeting_id TEXT, provider TEXT, state TEXT)')
                db.execute("INSERT INTO cp_call_provider_choices VALUES ('preserved-meeting','zoom','selected')")
            started = []
            restart.side_effect = lambda: started.append(r.pointer(api))
            with self.assertRaisesRegex(ValueError, 'Zoom meetings'):
                r.activate(stage, m)
            self.assertEqual(started, ['releases/old-api'])
            self.assertEqual(stop.call_count, 2)
            health.assert_not_called()
            self.assertEqual(r.pointer(api), 'releases/old-api')
            self.assertEqual(r.pointer(base), 'releases/old-ui')

    def test_last_zoom_choice_after_failed_health_blocks_old_fallback(self):
        with self.service_activation(installed=r.PRE_ZOOM_RUNTIME) as (stage, m, base, api, database, stop, restart, health):
            health.side_effect = RuntimeError('unhealthy')
            def stopped():
                if stop.call_count == 2:
                    with sqlite3.connect(database) as db:
                        db.execute('CREATE TABLE cp_call_provider_choices (meeting_id TEXT, provider TEXT, state TEXT)')
                        db.execute("INSERT INTO cp_call_provider_choices VALUES ('last-commit','zoom','creating')")
            stop.side_effect = stopped
            with self.assertRaisesRegex(RuntimeError, 'rollback refused.*Zoom meetings'):
                r.activate(stage, m)
            restart.assert_called_once()
            self.assertEqual(r.pointer(api), 'releases/' + m['release'])
            with sqlite3.connect(database) as db:
                self.assertEqual(db.execute('SELECT * FROM cp_call_provider_choices').fetchall(), [('last-commit', 'zoom', 'creating')])

    def test_compatible_zoom_fallback_keeps_provider_choices(self):
        with self.service_activation(installed=r.RUNTIME) as (stage, m, base, api, database, stop, restart, health):
            with sqlite3.connect(database) as db:
                db.execute('CREATE TABLE cp_call_provider_choices (meeting_id TEXT, provider TEXT, state TEXT)')
                db.execute("INSERT INTO cp_call_provider_choices VALUES ('preserved-meeting','zoom','ready')")
            health.side_effect = RuntimeError('unhealthy')
            with self.assertRaisesRegex(RuntimeError, '^unhealthy$'):
                r.activate(stage, m)
            self.assertEqual(stop.call_count, 2)
            self.assertEqual(restart.call_count, 2)
            self.assertEqual(r.pointer(api), 'releases/old-api')
            self.assertEqual(r.pointer(base), 'releases/old-ui')
            with sqlite3.connect(database) as db:
                self.assertEqual(db.execute('SELECT * FROM cp_call_provider_choices').fetchall(), [('preserved-meeting', 'zoom', 'ready')])

    def test_explicit_pre_materials_rollback_restores_current_compatible_runtime(self):
        with self.service_activation(incoming=r.PRE_MATERIALS_RUNTIME, installed=r.RUNTIME) as (stage, m, base, api, database, stop, restart, health):
            with sqlite3.connect(database) as db:
                db.execute('CREATE TABLE media_sources (id TEXT)')
                db.execute("INSERT INTO media_sources VALUES ('preserved-source')")
            started = []
            restart.side_effect = lambda: started.append(r.pointer(api))
            with self.assertRaisesRegex(ValueError, 'Materials catalog'):
                r.activate(stage, m)
            self.assertEqual(started, ['releases/old-api'])
            self.assertEqual(stop.call_count, 2)
            health.assert_not_called()
            self.assertEqual(r.pointer(api), 'releases/old-api')
            self.assertEqual(r.pointer(base), 'releases/old-ui')

    def test_last_materials_commit_after_failed_health_blocks_old_fallback(self):
        with self.service_activation(installed=r.PRE_MATERIALS_RUNTIME) as (stage, m, base, api, database, stop, restart, health):
            health.side_effect = RuntimeError('unhealthy')
            def stopped():
                if stop.call_count == 2:
                    with sqlite3.connect(database) as db:
                        db.execute('CREATE TABLE media_sources (id TEXT)')
                        db.execute("INSERT INTO media_sources VALUES ('last-worker-commit')")
            stop.side_effect = stopped
            with self.assertRaisesRegex(RuntimeError, 'rollback refused.*Materials catalog'):
                r.activate(stage, m)
            restart.assert_called_once()
            self.assertEqual(r.pointer(api), 'releases/' + m['release'])
            with sqlite3.connect(database) as db:
                self.assertEqual(db.execute('SELECT id FROM media_sources').fetchall(), [('last-worker-commit',)])

    def test_malformed_outbox_fails_closed_for_old_runtime(self):
        old = self.runtime(self.root / 'old', r.PRE_MEETING_NOTICES_RUNTIME)
        with sqlite3.connect(self.outbox()) as db:
            for payload in ('{', '', 'null', '[]', 'true', '42'):
                with self.subTest(payload=payload):
                    db.execute('DELETE FROM cn_outbox')
                    db.execute('INSERT INTO cn_outbox VALUES (?,?,?,?)', ('meeting.confirmed', payload, 'pending', 'candidate'))
                    with self.assertRaises(ValueError):
                        r.guard_outbox_runtime(db, old)
            db.execute('DROP TABLE cn_outbox')
            db.execute('CREATE VIEW cn_outbox AS SELECT 1')
            with self.assertRaisesRegex(ValueError, 'schema'):
                r.guard_outbox_runtime(db, old)
            db.execute('DROP VIEW cn_outbox')
            db.execute('CREATE TABLE cn_outbox (wrong TEXT)')
            with self.assertRaisesRegex(ValueError, 'event types'):
                r.guard_outbox_runtime(db, old)

    def test_explicit_legacy_activation_is_blocked_and_current_compatible_api_restored(self):
        with self.service_activation(incoming=r.PRE_MEETING_NOTICES_RUNTIME, installed=r.RUNTIME) as (stage, m, base, api, db, stop, restart, health):
            def stopped():
                if stop.call_count == 1:
                    self.insert_notice(db)
            stop.side_effect = stopped
            started = []
            restart.side_effect = lambda: started.append(r.pointer(api))
            with self.assertRaisesRegex(ValueError, 'Lifecycle outbox'):
                r.activate(stage, m)
            self.assertEqual(started, ['releases/old-api'])
            self.assertEqual(stop.call_count, 2)
            health.assert_not_called()
            self.assertEqual(r.pointer(base), 'releases/old-ui')
            self.assertEqual(r.pointer(api), 'releases/old-api')

    def test_explicit_legacy_activation_with_ordinary_rows_still_works(self):
        with self.service_activation(incoming=r.PRE_MEETING_NOTICES_RUNTIME, installed=r.RUNTIME) as (stage, m, base, api, db, stop, restart, health):
            self.insert_notice(db, event='meeting.confirmed', payload='{}')
            r.activate(stage, m)
            self.assertEqual(r.pointer(api), 'releases/' + m['release'])
            stop.assert_called_once(); restart.assert_called_once()

    def test_failed_health_refuses_old_fallback_after_last_new_writer_commit(self):
        with self.service_activation() as (stage, m, base, api, db, stop, restart, health):
            started = []
            restart.side_effect = lambda: started.append(r.pointer(api))
            health.side_effect = RuntimeError('unhealthy')
            # Simulate the last lifecycle transaction committing while stop waits
            # for the new unit to exit, after the health check already failed.
            def stopped():
                if stop.call_count == 2:
                    self.insert_notice(db)
            stop.side_effect = stopped
            with self.assertRaisesRegex(RuntimeError, 'rollback refused.*Lifecycle outbox'):
                r.activate(stage, m)
            self.assertEqual(started, ['releases/' + m['release']])
            self.assertEqual(r.pointer(api), 'releases/' + m['release'])
            self.assertEqual(r.pointer(base), 'releases/' + m['release'])
            with sqlite3.connect(db) as connection:
                self.assertEqual(connection.execute('SELECT * FROM cn_outbox').fetchall(),
                                 [('meeting.completed', '{"lifecycle": true}', 'pending', 'candidate')])

    def test_failed_health_can_restore_compatible_target_with_lifecycle_rows(self):
        with self.service_activation(installed=r.RUNTIME) as (stage, m, base, api, db, stop, restart, health):
            self.insert_notice(db, owner='employer:account')
            health.side_effect = RuntimeError('unhealthy')
            with self.assertRaisesRegex(RuntimeError, 'unhealthy'):
                r.activate(stage, m)
            self.assertEqual(stop.call_count, 2); self.assertEqual(restart.call_count, 2)
            self.assertEqual(r.pointer(api), 'releases/old-api')
            self.assertEqual(r.pointer(base), 'releases/old-ui')

    def test_failed_health_refuses_old_fallback_for_reused_employer_event(self):
        with self.service_activation() as (stage, m, base, api, db, stop, restart, health):
            def failed_health(*args):
                self.insert_notice(db, event='meeting.confirmed', owner='employer:account')
                raise RuntimeError('unhealthy')
            health.side_effect = failed_health
            with self.assertRaisesRegex(RuntimeError, 'rollback refused.*Lifecycle outbox'):
                r.activate(stage, m)
            restart.assert_called_once()
            self.assertEqual(r.pointer(api), 'releases/' + m['release'])

    def test_restart_failure_also_rechecks_fallback_data(self):
        with self.service_activation() as (stage, m, base, api, db, stop, restart, health):
            def failed_start():
                self.insert_notice(db)
                raise subprocess.CalledProcessError(1, 'systemctl restart')
            restart.side_effect = failed_start
            with self.assertRaisesRegex(RuntimeError, 'rollback refused.*Lifecycle outbox'):
                r.activate(stage, m)
            restart.assert_called_once(); health.assert_not_called()
            self.assertEqual(r.pointer(api), 'releases/' + m['release'])
            self.assertEqual(r.pointer(base), 'releases/old-ui')

    def test_absent_or_incomplete_recovery_target_never_starts(self):
        with self.service_activation() as (stage, m, base, api, db, stop, restart, health):
            with self.assertRaisesRegex(ValueError, 'No previous API'):
                r.activate_api(None)
            with self.assertRaisesRegex(ValueError, 'Incomplete recovery API'):
                r.activate_api('releases/missing')
            (api / 'releases/old-api/app.py').unlink()
            with self.assertRaisesRegex(ValueError, 'Incomplete recovery API'):
                r.activate_api('releases/old-api')
            restart.assert_not_called()
            self.assertEqual(r.pointer(api), 'releases/old-api')

    def test_database_failure_during_fallback_never_starts_old_code(self):
        with self.service_activation() as (stage, m, base, api, db, stop, restart, health):
            def failed_health(*args):
                db.unlink()
                raise RuntimeError('unhealthy')
            health.side_effect = failed_health
            with self.assertRaisesRegex(RuntimeError, 'rollback refused.*unable to open database'):
                r.activate(stage, m)
            restart.assert_called_once()
            self.assertEqual(r.pointer(api), 'releases/' + m['release'])
            self.assertFalse(db.exists())

    def test_stop_and_database_failures_never_switch_or_start(self):
        with self.service_activation() as (stage, m, base, api, database, stop, restart, health):
            stop.side_effect = subprocess.TimeoutExpired('systemctl stop', 45)
            with self.assertRaises(subprocess.TimeoutExpired):
                r.activate_api('releases/old-api')
            stop.side_effect = None
            database.unlink()
            with self.assertRaises(sqlite3.OperationalError):
                r.activate_api('releases/old-api')
            self.assertFalse(database.exists())
            database.write_bytes(b'corrupt database')
            with self.assertRaises(sqlite3.DatabaseError):
                r.activate_api('releases/old-api')
            restart.assert_not_called()
            self.assertEqual(r.pointer(api), 'releases/old-api')

    def test_write_reservation_spans_guard_and_switch_and_releases_before_start(self):
        with self.service_activation() as (stage, m, base, api, database, stop, restart, health):
            actual_switch = r.switch
            def reserved_switch(*args):
                with sqlite3.connect(database, timeout=0) as writer:
                    with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                        writer.execute('BEGIN IMMEDIATE')
                actual_switch(*args)
            def can_start():
                with sqlite3.connect(database, timeout=0) as writer:
                    writer.execute('BEGIN IMMEDIATE')
            restart.side_effect = can_start
            with patch.object(r, 'switch', side_effect=reserved_switch):
                r.activate_api('releases/old-api')
            restart.assert_called_once()

    def test_busy_database_fails_closed(self):
        with self.service_activation() as (stage, m, base, api, database, stop, restart, health):
            connect = sqlite3.connect
            with connect(database) as writer:
                writer.execute('BEGIN IMMEDIATE')
                with patch.object(r.sqlite3, 'connect', side_effect=lambda *a, **kw: connect(*a, timeout=0, **kw)):
                    with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                        r.activate_api('releases/old-api')
            restart.assert_not_called()
            self.assertEqual(r.pointer(api), 'releases/old-api')

    def test_stop_uses_checked_synchronous_unit_stop(self):
        with patch.object(r.subprocess, 'run') as run:
            r.stop_api()
            run.assert_called_once_with(['systemctl', 'stop', 'hr-vision-api'], check=True, timeout=45)

    def test_live_call_guard_checks_candidate_portal_interviews_and_meetings(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE scenario (id INTEGER PRIMARY KEY, body TEXT)')
            db.execute('INSERT INTO scenario VALUES (1, ?)', (json.dumps({'candidates': {}}),))
            for table in ('cp_interviews', 'cp_meetings', 'ml_sessions'):
                db.execute(f'CREATE TABLE {table} (status TEXT)')
                db.execute(f'INSERT INTO {table} VALUES (?)', ('booked',))
                r.guard_live_interviews(db)
                db.execute(f'UPDATE {table} SET status=?', ('live',))
                with self.assertRaisesRegex(ValueError, 'interview is live'):
                    r.guard_live_interviews(db)
                db.execute(f'UPDATE {table} SET status=?', ('completed',))
                r.guard_live_interviews(db)


if __name__ == '__main__': unittest.main()
