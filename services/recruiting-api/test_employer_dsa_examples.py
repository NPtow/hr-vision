"""Verified-source examples remain private and replacement is narrowly reversible."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from domain import Problem
import dsa_panel as panel
import employer_intake as intake
import test_employer_intake as fixture


spec = importlib.util.spec_from_file_location('employer_dsa_examples',
    Path(__file__).resolve().parent / 'scripts' / 'employer-dsa-examples.py')
operator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operator)
operator.setup(str(Path(__file__).resolve().parent))


def example_payload():
    profiles = []
    for cid in panel.EXAMPLE_IDS:
        transcript = '# Тестовый профиль, сделка ' + cid + ', длительность 00:40\n' + (
            '# формат: [начало-конец] Р (рекрутёр) или К (кандидат): текст\n'
            '[00:00-00:05] Р: Какой у вас опыт?\n'
            '[00:05-00:25] К: Синтетическая точная реплика для теста сохранения исходного текста.\n'
            '[00:25-00:40] Р: Спасибо за ответ.\n')
        profile = panel.prepared_example({
            'id': cid, 'photo': panel.SOURCE + '/faces/' + cid + '.jpg',
            'video': panel.SOURCE + '/api/video/' + cid, 'duration': 40, 'score': 4.0,
            'role': 'Опыт из источника, Москва', 'profile': [{'label': 'Опыт', 'value': 'Текст источника'}],
            'cv': 'Текст настоящего источника CV в синтетической проверке.', 'conclusion': ['Вывод источника'],
            'shortChapters': [{'title': 'Опыт', 'seconds': 5, 'endSeconds': 25,
                               'fragment': 'Синтетическая точная реплика', 'isQuote': True}],
        }, transcript)
        profiles.append({'sourceCandidateId': cid, 'name': 'Тестовый ' + cid,
                         'role': 'Менеджер по работе с клиентами', 'preparedProfile': profile})
    return {'schema': 1, 'kind': 'dsa-panel-examples', 'source': panel.SOURCE,
            'preparedAt': '2026-10-14T14:00:00Z', 'profiles': profiles}


class EmployerDsaExamplesTest(unittest.TestCase):
    setUp = fixture.EmployerIntakeTest.setUp
    tearDown = fixture.EmployerIntakeTest.tearDown
    connect = fixture.EmployerIntakeTest.connect
    assert_problem = fixture.EmployerIntakeTest.assert_problem

    def old_examples(self, db, *, wid='a', vid=None, batch='workbook-fixture-123'):
        vid = vid or self.va
        people = intake.add(db, wid, vid, {'scope': 'sfera', 'batch': batch,
                                          'entries': [{'name': 'Первый пример'}, {'name': 'Второй пример'}]})['candidates']
        for person in people:
            person['preparedProfile'] = {'sourceLabel': 'Подборка от 16.09.2026', 'cvText': 'Резюме',
                'recordings': [{'publicUrl': 'https://disk.yandex.ru/i/old_example_video', 'mime': 'video/mp4'}]}
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (operator.dump(person), person['id']))
        return people

    def plan(self, db, payload=None):
        return operator.make_plan(db, payload or example_payload(), 'a', self.va, ['workbook-fixture-123'])

    def rollback_file(self):
        return Path(self.tmp.name) / 'private' / 'rollback.json'

    def test_prepared_profile_preserves_exact_transcript_quotes_and_has_single_video(self):
        payload = example_payload()
        operator.validate_payload(payload)
        for person in payload['profiles']:
            profile = person['preparedProfile']
            self.assertEqual(len(profile['recordings']), 1)
            self.assertEqual(profile['source']['candidateId'], person['sourceCandidateId'])
            self.assertTrue(profile['source']['example'])
            self.assertEqual(profile['chapters'][0]['fragment'], 'Синтетическая точная реплика')
            self.assertTrue(profile['chapters'][0]['isQuote'])
            self.assertIn('[00:05-00:25] К: Синтетическая точная реплика', profile['transcriptText'])
            self.assertEqual(profile['summary'], 'Опыт из источника, Москва')
            self.assertEqual(profile['transcriptSource']['sha256'],
                             hashlib.sha256(profile['transcriptText'].encode()).hexdigest())

    def test_wrong_transcript_identity_duration_or_nonquote_cannot_be_imported(self):
        for mutate in (
            lambda p: p['preparedProfile'].__setitem__('transcriptText', 'Без исходной расшифровки'),
            lambda p: p['preparedProfile'].__setitem__('transcriptText',
                p['preparedProfile']['transcriptText'].replace('сделка 378713', 'сделка 380179')),
            lambda p: p['preparedProfile'].__setitem__('transcriptText',
                p['preparedProfile']['transcriptText'].replace('00:40', '00:41')),
            lambda p: p['preparedProfile']['chapters'][0].__setitem__('isQuote', False),
            lambda p: p['preparedProfile']['chapters'][0].__setitem__('endSeconds', 41),
            lambda p: p['preparedProfile']['chapters'][0].__setitem__('seconds', True),
            lambda p: p['preparedProfile'].__setitem__('cvText', ''),
            lambda p: p.__setitem__('sourceCandidateId', '380611'),
        ):
            payload = example_payload(); mutate(payload['profiles'][0])
            with self.assertRaises((ValueError, Problem)):
                operator.validate_payload(payload)

    def test_stored_dsa_recording_requires_exact_source_identity_and_never_uses_remote_resolver(self):
        with self.connect() as db:
            old = self.old_examples(db)
            person = old[0]
            person['preparedProfile'] = example_payload()['profiles'][0]['preparedProfile']
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (operator.dump(person), person['id']))
            body = {'id': person['id'], 'index': 0, 'vacancyId': self.va}
            source = intake.recording_source(db, 'a', body)
            with patch('employer_intake.urllib.request.build_opener', side_effect=AssertionError('Must not resolve as Yandex')):
                self.assertEqual(intake.resolve_recording(source), {
                    'url': panel.SOURCE + '/api/video/378713', 'mime': 'video/mp4'})
            self.assert_problem(404, intake.recording_source, db, 'b', body)
            self.assert_problem(409, intake.recording_source, db, 'a', {**body, 'vacancyId': self.va2})
            for url in ('https://evil.test/api/video/378713', panel.SOURCE + '.evil.test/api/video/378713',
                        panel.SOURCE + '/api/video/380179', panel.SOURCE + '/api/video/../378713',
                        panel.SOURCE + '/api/video/378713?next=evil'):
                changed = copy.deepcopy(person)
                changed['preparedProfile']['recordings'][0]['publicUrl'] = url
                db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (operator.dump(changed), person['id']))
                self.assert_problem(422, intake.recording_source, db, 'a', body)
            for mutate in (lambda p: p['source'].__setitem__('example', False),
                           lambda p: p['source'].__setitem__('candidateId', '380179'),
                           lambda p: p['recordings'].append(p['recordings'][0].copy())):
                changed = copy.deepcopy(person); mutate(changed['preparedProfile'])
                db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (operator.dump(changed), person['id']))
                self.assert_problem(422, intake.recording_source, db, 'a', body)

    def test_existing_yandex_comment_fingerprints_remain_unchanged(self):
        recording = {'publicUrl': 'https://disk.yandex.ru/i/legacy_recording', 'mime': 'video/mp4', 'size': 123}
        legacy = {key: recording.get(key) for key in ('publicUrl', 'mime', 'size', 'id', 'sha256', 'checksum', 'version')}
        expected = 'intake-recording:' + hashlib.sha256(intake._dump(legacy).encode()).hexdigest()
        self.assertEqual(intake._recording_fingerprint(recording), expected)

    def test_plan_reads_exact_batch_without_writes_or_exposing_other_workspaces(self):
        with self.connect() as db:
            old = self.old_examples(db)
            self.old_examples(db, wid='b', vid=self.vb)
            self.old_examples(db, vid=self.va2)
        db = operator.connect(self.path, readonly=True)
        try:
            plan = self.plan(db)
            self.assertEqual(db.total_changes, 0)
            self.assertEqual([p['id'] for p in plan['archive']], [p['id'] for p in old])
            self.assertEqual(len(plan['add']), 4)
            self.assertEqual(plan['canonicalApplicationsUntouched'], 0)
            with self.assertRaises(Problem):
                operator.make_plan(db, example_payload(), 'b', self.va, ['workbook-fixture-123'])
        finally:
            db.close()

    def test_apply_retry_restart_and_rollback_keep_all_history_and_other_scopes(self):
        payload = example_payload(); rollback_path = self.rollback_file()
        with self.connect() as db:
            old = self.old_examples(db)
            other = self.old_examples(db, wid='b', vid=self.vb)
            tables = ('cp_candidates', 'cp_applications', 'cp_consent_log', 'cp_interviews', 'cp_meetings')
            before = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in tables}
            projected = intake.list_candidates(db, 'a', self.va)['candidates'][0]
            intake.screening_comment(db, 'a', self.va, {'id': projected['id'], 'vacancyId': self.va,
                'recordingIndex': 0, 'seconds': 12, 'text': 'Существующая заметка',
                'requestId': 'before-replace', 'expectedRevision': projected['reviewRevision']})
            plan = self.plan(db)
            result = operator.apply(db, payload, plan, rollback_path)
            self.assertEqual(len(result['archived']), 2); self.assertEqual(len(result['added']), 4)
            self.assertEqual(rollback_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_intake_review_events').fetchone()[0], 1)
            current = intake.list_candidates(db, 'a', self.va)['candidates']
            self.assertEqual(sum(not p['archived'] for p in current), 4)
            for person in current:
                if person['id'] in result['added']:
                    self.assertTrue(person['preparedProfile']['source']['example'])
        with self.connect() as db:
            self.assertTrue(operator.apply(db, payload, plan, rollback_path)['replayed'])
            restored = operator.rollback(db, operator.read_json(rollback_path))
            self.assertEqual(set(restored['restored']), {p['id'] for p in old})
            current = intake.list_candidates(db, 'a', self.va)['candidates']
            self.assertEqual({p['id'] for p in current if not p['archived']}, {p['id'] for p in old})
            self.assertEqual(len([p for p in current if p['archived']]), 4)
            self.assertEqual(sum(len(p['screeningComments']) for p in current), 1)
            self.assertEqual(before, {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in tables})
            self.assertEqual({p['id'] for p in intake.list_candidates(db, 'b', self.vb)['candidates'] if not p['archived']},
                             {p['id'] for p in other})
            self.assertTrue(operator.rollback(db, operator.read_json(rollback_path))['replayed'])
            with self.assertRaisesRegex(ValueError, 'rolled back'):
                operator.apply(db, payload, plan, rollback_path)

    def test_new_review_or_changed_body_after_plan_blocks_apply_without_a_backup(self):
        with self.connect() as db:
            old = self.old_examples(db); plan = self.plan(db)
            projected = intake.list_candidates(db, 'a', self.va)['candidates'][0]
            intake.screening_comment(db, 'a', self.va, {'id': projected['id'], 'vacancyId': self.va,
                'recordingIndex': 0, 'seconds': 12, 'text': 'Новая заметка',
                'requestId': 'after-plan', 'expectedRevision': projected['reviewRevision']})
            with self.assertRaisesRegex(ValueError, 'changed after'):
                operator.apply(db, example_payload(), plan, self.rollback_file())
            self.assertFalse(self.rollback_file().exists())
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_intake_candidates').fetchone()[0], 2)
            plan = self.plan(db)
            old[0]['note'] = 'Изменено пользователем'
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (operator.dump(old[0]), old[0]['id']))
            with self.assertRaisesRegex(ValueError, 'changed after'):
                operator.apply(db, example_payload(), plan, self.rollback_file())

    def test_apply_failure_is_atomic_and_private_backup_can_retry_unchanged_operation(self):
        with self.connect() as db:
            self.old_examples(db); plan = self.plan(db)
            db.execute('''CREATE TRIGGER fail_example BEFORE INSERT ON em_intake_candidates
              WHEN json_extract(NEW.body,'$.preparedProfile.source.kind')='dsa-panel'
              BEGIN SELECT RAISE(ABORT,'synthetic'); END''')
            with self.assertRaises(sqlite3.IntegrityError):
                operator.apply(db, example_payload(), plan, self.rollback_file())
            self.assertTrue(self.rollback_file().exists())
            self.assertEqual(sum(p['archived'] for p in intake.list_candidates(db, 'a', self.va)['candidates']), 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_store_commands').fetchone()[0], 0)
            db.execute('DROP TRIGGER fail_example')
            self.assertEqual(len(operator.apply(db, example_payload(), plan, self.rollback_file())['added']), 4)

    def test_modified_rollback_file_or_changed_profile_never_overwrites_new_information(self):
        with self.connect() as db:
            self.old_examples(db); plan = self.plan(db)
            result = operator.apply(db, example_payload(), plan, self.rollback_file())
            backup = operator.read_json(self.rollback_file())
            tampered = copy.deepcopy(backup); tampered['old'][0]['body'] = '{}'
            with self.assertRaisesRegex(ValueError, 'does not match'):
                operator.rollback(db, tampered)
            cid = result['added'][0]
            row = db.execute('SELECT body FROM em_intake_candidates WHERE id=?', (cid,)).fetchone()
            person = json.loads(row[0]); person['note'] = 'Изменённый профиль'
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (operator.dump(person), cid))
            with self.assertRaisesRegex(ValueError, 'changed after'):
                operator.rollback(db, backup)
            self.assertEqual(sum(not p['archived'] for p in intake.list_candidates(db, 'a', self.va)['candidates']), 4)

    def test_existing_comment_on_new_example_survives_archive_only_rollback(self):
        with self.connect() as db:
            self.old_examples(db); result = operator.apply(db, example_payload(), self.plan(db), self.rollback_file())
            person = next(p for p in intake.list_candidates(db, 'a', self.va)['candidates'] if p['id'] in result['added'])
            intake.screening_comment(db, 'a', self.va, {'id': person['id'], 'vacancyId': self.va,
                'recordingIndex': 0, 'seconds': 6, 'chapterTitle': 'Опыт', 'text': 'Заметка к примеру',
                'requestId': 'after-import', 'expectedRevision': person['reviewRevision']})
            operator.rollback(db, operator.read_json(self.rollback_file()))
            archived = next(p for p in intake.list_candidates(db, 'a', self.va)['candidates'] if p['id'] == person['id'])
            self.assertTrue(archived['archived'])
            self.assertEqual(archived['screeningComments'][0]['text'], 'Заметка к примеру')


if __name__ == '__main__':
    unittest.main()
