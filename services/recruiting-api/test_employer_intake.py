"""Existing employer intake contract, isolated in real temporary SQLite databases."""
import base64
import concurrent.futures
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from candidate_portal import CandidatePortal, ClosingConnection
from domain import Problem, iso, now
import employer_intake as intake
import employer_store as store


class NoMedia:
    configured = False


class EmployerIntakeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='employer-intake-')
        self.path = Path(self.tmp.name) / 'intake.sqlite3'
        CandidatePortal(self.connect, threading.RLock(), NoMedia(), 'https://example.test')
        with self.connect() as db:
            db.execute('CREATE TABLE em_workspaces(id TEXT PRIMARY KEY,name TEXT,kind TEXT,created_at TEXT)')
            db.executemany('INSERT INTO em_workspaces VALUES (?,?,?,?)',
                           [(wid, 'Работодатель ' + wid, 'employer', iso(now())) for wid in ('a', 'b')])
            store.init(db)
            intake.init(db)
            self.va = store.create_vacancy(db, 'a', {'title': 'Менеджер'})['id']
            self.va2 = store.create_vacancy(db, 'a', {'title': 'Вторая вакансия'})['id']
            self.vb = store.create_vacancy(db, 'b', {'title': 'Другой работодатель'})['id']

    def tearDown(self):
        self.tmp.cleanup()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=10000')
        return db

    @staticmethod
    def body(*entries, batch='synthetic-batch-123'):
        return {'batch': batch, 'scope': 'sfera', 'entries': list(entries) or [{'name': 'Тест Кандидат'}]}

    @staticmethod
    def upload(name='Тест резюме.rtf', content=b'{\\rtf1 Synthetic CV}'):
        return {'name': name, 'data': base64.b64encode(content).decode()}

    @staticmethod
    def docx():
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types/>')
            texts = ['ФИО: Анна Тестова', 'Город: Москва', 'Желаемая должность: Менеджер',
                     'Опыт работы: Пять лет развивала продажи и поддерживала отношения с клиентами. '
                     'Проводила переговоры, готовила коммерческие предложения, обучала новых коллег '
                     'и работала в CRM. Руководила командой и организовывала встречи.']
            xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            xml += ''.join('<w:p><w:r><w:t>' + text + '</w:t></w:r></w:p>' for text in texts)
            archive.writestr('word/document.xml', xml + '</w:body></w:document>')
        return buffer.getvalue()

    def assert_problem(self, status, operation, *args, **kwargs):
        with self.assertRaises(Problem) as caught:
            operation(*args, **kwargs)
        self.assertEqual(caught.exception.status, status, caught.exception.message)

    def test_manual_and_link_drafts_persist_without_creating_or_approving_cp_profiles(self):
        body = self.body({'name': 'Первый', 'role': 'Инженер', 'contact': '+70000000000', 'note': 'Заметка'},
                         {'name': 'Второй', 'link': 'https://hh.ru/resume/synthetic'})
        with self.connect() as db:
            added = intake.add(db, 'a', self.va, body)
            self.assertEqual(len(added['candidates']), 2)
        with self.connect() as db:
            people = intake.list_candidates(db, 'a', self.va)['candidates']
            self.assertEqual([p['id'] for p in people], [p['id'] for p in added['candidates']][::-1])
            self.assertEqual(people[0]['link'], 'https://hh.ru/resume/synthetic')
            self.assertEqual(people[1]['contact'], '+70000000000')
            for table in ('cp_candidates', 'cp_consent_log', 'cp_applications', 'cp_interviews'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)
            self.assertEqual(intake.list_candidates(db, 'a', self.va2), {'candidates': []})
            self.assertEqual(intake.list_candidates(db, 'b', self.vb), {'candidates': []})

    def test_existing_ui_dto_exact_shape_and_private_docx_extraction(self):
        body = self.body({'name': 'Исправленное имя', 'file': self.upload('Профиль.docx', self.docx())})
        prepared = intake.prepare(body)
        with self.connect() as db:
            person = intake.add(db, 'a', self.va, body, prepared)['candidates'][0]
            self.assertEqual(set(person), {'id', 'name', 'role', 'contact', 'link', 'note', 'scope', 'files', 'createdAt', 'archived'})
            self.assertEqual(person['name'], 'Исправленное имя')
            self.assertEqual(person['role'], 'Менеджер')
            self.assertEqual(set(person['files'][0]), {'id', 'name', 'size'})
            extracted = db.execute('SELECT text_content,extracted,parse_status FROM em_intake_files').fetchone()
            self.assertIn('ФИО: Анна Тестова', extracted[0])
            self.assertEqual(json.loads(extracted[1])['city'], 'Москва')
            self.assertEqual(extracted[2], 'parsed')
            self.assertNotIn('extracted', person)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_legacy_doc_rtf_retained_without_fabricated_extraction(self):
        files = [('legacy.doc', bytes.fromhex('d0cf11e0a1b11ae1') + b'FAKE-DOC-FIXTURE'),
                 ('legacy.rtf', b'{\\rtf1 Synthetic}')]
        with self.connect() as db:
            people = intake.add(db, 'a', self.va, self.body(*[{'name': name, 'file': self.upload(name, content)}
                                                           for name, content in files]))['candidates']
            for person, (name, content) in zip(people, files):
                saved, headers = intake.file(db, 'a', person['files'][0]['id'])
                self.assertEqual(saved, content)
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))
            self.assertEqual(db.execute('SELECT DISTINCT parse_status FROM em_intake_files').fetchall(), [('unsupported',)])

    def test_same_batch_retry_has_same_ids_and_no_duplicate_files_after_restart(self):
        body = self.body({'name': 'Тест', 'file': self.upload()})
        with self.connect() as db:
            first = intake.add(db, 'a', self.va, body)
        with self.connect() as db:
            with patch.object(intake, 'prepare', side_effect=AssertionError('Must not reparse a retry')):
                self.assertEqual(intake.add(db, 'a', self.va, body), first)
            for table in ('em_intake_candidates', 'em_intake_files', 'em_intake_batches'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 1)
            changed = self.body({'name': 'Другой'})
            self.assert_problem(409, intake.add, db, 'a', self.va, changed)

    def test_batch_namespace_is_per_workspace_and_vacancy(self):
        body = self.body()
        with self.connect() as db:
            p1 = intake.add(db, 'a', self.va, body)['candidates'][0]
            p2 = intake.add(db, 'a', self.va2, body)['candidates'][0]
            p3 = intake.add(db, 'b', self.vb, body)['candidates'][0]
            self.assertEqual(len({p1['id'], p2['id'], p3['id']}), 3)

    def test_ownership_checks_for_add_list_archive_and_download(self):
        with self.connect() as db:
            person = intake.add(db, 'a', self.va, self.body({'name': 'Тест', 'file': self.upload()}))['candidates'][0]
            self.assert_problem(404, intake.add, db, 'b', self.va, self.body())
            self.assert_problem(404, intake.list_candidates, db, 'b', self.va)
            self.assert_problem(404, intake.archive, db, 'b', self.vb, {'id': person['id'], 'archived': True})
            self.assert_problem(404, intake.archive, db, 'a', self.va2, {'id': person['id'], 'archived': True})
            self.assert_problem(404, intake.file, db, 'b', person['files'][0]['id'])
            self.assert_problem(404, intake.file, db, 'missing', person['files'][0]['id'])
            self.assert_problem(404, intake.file, db, 'a', '../anything')

    def test_request_cannot_spoof_vacancy_or_workspace(self):
        with self.connect() as db:
            self.assert_problem(409, intake.add, db, 'a', self.va, {**self.body(), 'vacancyId': self.va2})
            self.assert_problem(400, intake.add, db, 'a', self.va, {**self.body(), 'workspaceId': 'b'})
            self.assert_problem(400, intake.add, db, 'a', self.va, ['wrong'])

    def test_oversize_files_batch_and_invalid_signatures_leave_no_partial_rows(self):
        wrong_files = [('CV.pdf', b'MZ-not-a-pdf'), ('CV.doc', b'not-word'), ('CV.exe', b'MZ'),
                       ('../CV.rtf', b'{\\rtf1 X}'), ('CV\r\n.rtf', b'{\\rtf1 X}')]
        with self.connect() as db:
            for name, content in wrong_files:
                self.assert_problem(415 if name.endswith('.exe') else 400, intake.add, db, 'a', self.va,
                    self.body({'name': 'Тест', 'file': self.upload(name, content)}))
            self.assert_problem(413, intake.add, db, 'a', self.va,
                self.body({'name': 'Большое', 'file': self.upload('big.rtf', b'{\\rtf1 ' + b'x' * intake.MAX_BYTES)}))
            content = b'{\\rtf1 ' + b'x' * (4 * 1024 * 1024)
            self.assert_problem(413, intake.add, db, 'a', self.va,
                self.body({'name': 'Первый', 'file': self.upload('one.rtf', content)},
                          {'name': 'Второй', 'file': self.upload('two.rtf', content)}))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_intake_files').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_intake_candidates').fetchone()[0], 0)

    def test_limits_and_link_scheme_validation(self):
        with self.connect() as db:
            for link in ('javascript:alert(1)', 'http://hh.ru/resume/x', 'https://user:pass@hh.ru/x',
                         'https://hh.ru:bad/x', 'https://hh.ru/\x00x'):
                self.assert_problem(400, intake.add, db, 'a', self.va, self.body({'name': 'Тест', 'link': link}))
            for body in ({**self.body(), 'entries': []}, self.body(*[{'name': 'Тест'}] * 11),
                         self.body({'name': ''}), {**self.body(), 'batch': 'short'},
                         self.body({'name': 'Тест', 'consentReuse': True})):
                self.assert_problem(400, intake.add, db, 'a', self.va, body)

    def test_batch_parse_failure_leaves_no_partial_writes(self):
        body = self.body({'name': 'Первый', 'file': self.upload()},
                         {'name': 'Невалидный DOCX', 'file': self.upload('bad.docx', b'PKnot-a-valid-zip')})
        with self.connect() as db:
            self.assert_problem(422, intake.add, db, 'a', self.va, body)
            for table in ('em_intake_candidates', 'em_intake_files', 'em_intake_batches'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)

    def test_failure_after_first_candidate_write_rolls_back_when_caught(self):
        body = self.body({'name': 'Первый'}, {'name': 'Второй'})
        prepared = intake.prepare(body)
        with self.connect() as db:
            db.execute('''CREATE TRIGGER test_fail_intake BEFORE INSERT ON em_intake_candidates
              WHEN json_extract(NEW.body,'$.name')='Второй' BEGIN SELECT RAISE(ABORT,'synthetic'); END''')
            with self.assertRaises(sqlite3.IntegrityError):
                intake.add(db, 'a', self.va, body, prepared)
            for table in ('em_intake_candidates', 'em_intake_files', 'em_intake_batches'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)

    def test_prepare_before_db_does_not_bypass_closed_vacancy_or_changed_payload(self):
        body = self.body()
        prepared = intake.prepare(body)
        with self.connect() as db:
            self.assert_problem(409, intake.add, db, 'a', self.va, self.body({'name': 'Другой'}), prepared)
            row = db.execute('SELECT body FROM cp_vacancies WHERE id=?', (self.va,)).fetchone()
            vacancy = json.loads(row[0]); vacancy['closedBy'] = 'someone'
            db.execute('UPDATE cp_vacancies SET body=? WHERE id=?', (json.dumps(vacancy), self.va))
            self.assert_problem(409, intake.add, db, 'a', self.va, body, prepared)

    def test_quota_failures_are_atomic(self):
        with self.connect() as db:
            with patch.object(intake, 'MAX_WORKSPACE_CANDIDATES', 0):
                self.assert_problem(409, intake.add, db, 'a', self.va, self.body())
            with patch.object(intake, 'MAX_STORAGE_BYTES', 1):
                self.assert_problem(413, intake.add, db, 'a', self.va,
                    self.body({'name': 'Тест', 'file': self.upload()}))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_intake_batches').fetchone()[0], 0)

    def test_archive_unarchive_idempotence_and_download_keep_history(self):
        with self.connect() as db:
            person = intake.add(db, 'a', self.va, self.body({'name': 'Тест', 'file': self.upload()}))['candidates'][0]
            archived = intake.archive(db, 'a', self.va, {'id': person['id'], 'archived': True})
            self.assertTrue(archived['candidate']['archived'])
            self.assertEqual(intake.archive(db, 'a', self.va, {'id': person['id'], 'archived': True}), archived)
        with self.connect() as db:
            self.assertTrue(intake.list_candidates(db, 'a', self.va)['candidates'][0]['archived'])
            restored = intake.archive(db, 'a', self.va, {'id': person['id'], 'archived': False})
            self.assertFalse(restored['candidate']['archived'])
            store.archive_vacancy(db, 'a', self.va)
            self.assertEqual(intake.file(db, 'a', person['files'][0]['id'])[0], b'{\\rtf1 Synthetic CV}')

    def test_add_does_not_commit_caller_transaction(self):
        db = self.connect()
        try:
            intake.add(db, 'a', self.va, self.body({'name': 'Тест', 'file': self.upload()}))
            self.assertTrue(db.in_transaction)
            db.rollback()
        finally:
            db.close()
        with self.connect() as db:
            for table in ('em_intake_candidates', 'em_intake_files', 'em_intake_batches'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)

    def test_concurrent_duplicate_upload_creates_one_draft(self):
        body = self.body({'name': 'Тест', 'file': self.upload()})
        prepared = intake.prepare(body)
        def add(_):
            with self.connect() as db:
                return intake.add(db, 'a', self.va, body, prepared)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(add, range(4)))
        self.assertTrue(all(result == results[0] for result in results))
        with self.connect() as db:
            for table in ('em_intake_candidates', 'em_intake_files', 'em_intake_batches'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
