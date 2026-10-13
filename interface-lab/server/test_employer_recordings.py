"""Imported materials keep employer isolation and never create candidate consent."""
import io
import json
import unittest
from unittest.mock import patch

from domain import Problem
import employer_intake
import employer_recordings
import test_employer_acceptance as fixture


class ImportedRecordingTest(unittest.TestCase):
    setUpClass = classmethod(fixture.EmployerAcceptanceTest.setUpClass.__func__)
    tearDownClass = classmethod(fixture.EmployerAcceptanceTest.tearDownClass.__func__)
    setUp = fixture.EmployerAcceptanceTest.setUp
    request = fixture.EmployerAcceptanceTest.request
    ok = fixture.EmployerAcceptanceTest.ok
    employer = fixture.EmployerAcceptanceTest.employer
    vacancy = fixture.EmployerAcceptanceTest.vacancy

    def imported(self, employer, public_url='https://disk.yandex.ru/i/fixture_recording'):
        vacancy = self.vacancy(employer)
        with self.app.connect() as db:
            wid = employer['workspace']['id']
            person = employer_intake.add(db, wid, vacancy['id'], {
                'batch': 'import-fixture-123', 'scope': 'sfera', 'entries': [{'name': 'Тестовый профиль'}],
            })['candidates'][0]
            person['preparedProfile'] = {'cvText': 'Резюме из файла', 'evaluationText': 'Оценка из файла',
                'sourceLabel': 'Подборка', 'recordings': [{'publicUrl': public_url, 'name': 'Interview.mp4',
                                                        'mime': 'video/mp4', 'size': 123}]}
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (json.dumps(person), person['id']))
        return person, vacancy

    def test_private_materials_visible_only_to_owner_and_do_not_produce_applications(self):
        owner, other = self.employer(), self.employer()
        with self.app.connect() as db:
            tables = ('cp_candidates', 'cp_applications', 'cp_consent_log', 'cp_interviews', 'cn_outbox')
            before = {t: db.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0] for t in tables}
        person, vacancy = self.imported(owner)
        command = {'id': person['id'], 'index': 0}
        with patch.object(employer_recordings, 'resolve', return_value={'url': 'https://downloader.disk.yandex.ru/file', 'mime': 'video/mp4'}) as resolve:
            self.assertEqual(self.request('employer/intake/recording', command)[0], 401)
            self.assertEqual(self.request('employer/intake/recording', command, token=other['token'])[0], 404)
            resolve.assert_not_called()
            self.assertIn('url', self.ok('employer/intake/recording', command, token=owner['token']))
            resolve.assert_called_once()
        records = self.ok('employer/intake?vacancyId=' + vacancy['id'], token=owner['token'])['candidates']
        self.assertEqual(records[0]['preparedProfile']['cvText'], 'Резюме из файла')
        with self.app.connect() as db:
            self.assertEqual(before, {t: db.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0] for t in tables})

    def test_bad_index_and_untrusted_source_never_reach_remote_resolver(self):
        owner = self.employer()
        person, _ = self.imported(owner, 'https://127.0.0.1/private')
        with patch.object(employer_recordings, 'resolve') as resolve:
            for index, status in ((-1, 400), (True, 400), (100, 400), (1, 404), (0, 422)):
                self.assertEqual(self.request('employer/intake/recording', {'id': person['id'], 'index': index},
                                             token=owner['token'])[0], status)
            resolve.assert_not_called()

    def test_revoked_account_during_resolution_cannot_receive_playback_url(self):
        owner = self.employer()
        person, _ = self.imported(owner)
        def revoke(source):
            with self.app.connect() as db:
                db.execute('UPDATE em_accounts SET active=0 WHERE workspace_id=?', (owner['workspace']['id'],))
            return {'url': 'https://downloader.disk.yandex.ru/file', 'mime': 'video/mp4'}
        with patch.object(employer_recordings, 'resolve', side_effect=revoke):
            self.assertEqual(self.request('employer/intake/recording', {'id': person['id'], 'index': 0},
                                         token=owner['token'])[0], 401)

    def test_fixed_api_and_bounded_trusted_download_location(self):
        source = {'publicUrl': 'https://disk.yandex.ru/i/fixture_recording', 'mime': 'video/mp4'}
        for href, valid in [('https://downloader.disk.yandex.ru/disk/abc', True),
                            ('https://evil.test/video', False), ('http://downloader.disk.yandex.ru/file', False),
                            ('https://downloader.disk.yandex.ru.evil.test/file', False),
                            ('https://user@downloader.disk.yandex.ru/file', False)]:
            with patch('employer_recordings.urllib.request.build_opener') as opener:
                opener.return_value.open.return_value = io.BytesIO(json.dumps({'href': href}).encode())
                if valid:
                    self.assertEqual(employer_recordings.resolve(source)['url'], href)
                else:
                    with self.assertRaises(Problem):
                        employer_recordings.resolve(source)
                url = opener.return_value.open.call_args.args[0].full_url
                self.assertTrue(url.startswith('https://cloud-api.yandex.net/v1/disk/public/resources/download?'))


if __name__ == '__main__':
    unittest.main()
