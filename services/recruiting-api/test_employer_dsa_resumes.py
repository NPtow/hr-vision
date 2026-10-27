"""Original resume attachment is scoped, byte-preserving and safely reversible."""
import base64
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from domain import Problem
import employer_intake as intake
import test_employer_intake as fixture
from test_employer_dsa_examples import example_payload


spec = importlib.util.spec_from_file_location('employer_dsa_resumes',
    Path(__file__).resolve().parent / 'scripts' / 'employer-dsa-resumes.py')
operator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operator)
operator.setup(str(Path(__file__).resolve().parent))


class EmployerDsaResumesTest(unittest.TestCase):
    tearDown = fixture.EmployerIntakeTest.tearDown
    connect = fixture.EmployerIntakeTest.connect
    assert_problem = fixture.EmployerIntakeTest.assert_problem

    def setUp(self):
        fixture.EmployerIntakeTest.setUp(self)
        self.batch = 'dsa-examples-' + '1' * 24
        self.contents = {cid: ('%PDF-1.4\nSynthetic original ' + cid + '\n%%EOF').encode()
                         for cid in operator.ORIGINALS}
        manifest = {cid: (len(data), operator.sha(data)) for cid, data in self.contents.items()}
        patcher = patch.object(operator, 'ORIGINALS', manifest)
        patcher.start(); self.addCleanup(patcher.stop)
        self.source_dir = Path(self.tmp.name) / 'originals'
        self.source_dir.mkdir()
        for cid, data in self.contents.items():
            (self.source_dir / (cid + '.pdf')).write_bytes(data)
        self.import_plan = {'workspaceId': 'a', 'vacancyId': self.va, 'operationId': self.batch, 'add': []}
        with self.connect() as db:
            db.execute('INSERT INTO em_intake_batches VALUES (?,?,?,?,?)',
                       ('a', self.va, self.batch, 'synthetic', '2026-10-14T00:00:00Z'))
            for profile in example_payload()['profiles']:
                cid = profile['sourceCandidateId']
                iid = operator.sha((self.batch + ':' + cid).encode())[:24]
                body = {'id': iid, 'name': profile['name'], 'files': [], 'archived': False, 'scope': 'sfera',
                        'preparedProfile': profile['preparedProfile'], 'note': 'Исходная заметка'}
                db.execute('INSERT INTO em_intake_candidates VALUES (?,?,?,?,?)',
                           (iid, 'a', self.va, self.batch, operator.dump(body)))
                self.import_plan['add'].append({'sourceCandidateId': cid, 'id': iid, 'name': profile['name']})
        self.payload = operator.prepare(self.source_dir, self.import_plan)
        self.backup_path = Path(self.tmp.name) / 'private' / 'rollback.json'

    def bodies(self, db):
        return dict(db.execute('SELECT id,body FROM em_intake_candidates ORDER BY id'))

    def test_prepare_preserves_bytes_and_uses_profile_display_name(self):
        for entry, original in zip(self.payload['files'], self.import_plan['add']):
            self.assertEqual(base64.b64decode(entry['data']), self.contents[entry['sourceCandidateId']])
            self.assertEqual(entry['name'], 'Резюме — ' + original['name'] + '.pdf')
            self.assertEqual(Path(entry['sourcePath']).name, entry['sourceCandidateId'] + '.pdf')
        for mutate in (
            lambda p: p['files'][0].__setitem__('data', base64.b64encode(b'%PDF-other').decode()),
            lambda p: p['files'][0].__setitem__('sha256', '0' * 64),
            lambda p: p['files'][0].__setitem__('sourceCandidateId', '380611'),
            lambda p: p['files'][0].__setitem__('intakeId', p['files'][1]['intakeId']),
            lambda p: p['files'].pop(),
            lambda p: p['files'][0].__setitem__('name', '../outside.pdf'),
        ):
            payload = copy.deepcopy(self.payload); mutate(payload)
            with self.assertRaises(ValueError):
                operator.validate_payload(payload)

    def test_plan_is_read_only_and_checks_workspace_vacancy_identity_and_batch(self):
        db = operator.connect(self.path, readonly=True)
        try:
            plan = operator.make_plan(db, self.payload)
            self.assertEqual(len(plan['attachments']), 4)
            self.assertEqual(db.total_changes, 0)
            for field, value in (('workspaceId', 'b'), ('vacancyId', self.va2)):
                payload = {**self.payload, field: value}
                with self.assertRaises((ValueError, Problem)):
                    operator.make_plan(db, payload)
        finally:
            db.close()
        with self.connect() as db:
            iid = self.payload['files'][0]['intakeId']
            original = self.bodies(db)[iid]
            for mutate in (
                lambda b: b['preparedProfile']['source'].__setitem__('example', False),
                lambda b: b['preparedProfile']['source'].__setitem__('candidateId', '380179'),
                lambda b: b.__setitem__('archived', True),
                lambda b: b.__setitem__('name', 'Другой человек'),
            ):
                body = json.loads(original); mutate(body)
                db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (operator.dump(body), iid))
                with self.assertRaises(ValueError):
                    operator.make_plan(db, self.payload)
            db.execute('INSERT INTO em_intake_batches VALUES (?,?,?,?,?)',
                       ('a', self.va, 'other-batch', 'synthetic', '2026-10-14T00:00:00Z'))
            db.execute('UPDATE em_intake_candidates SET body=?,batch=? WHERE id=?', (original, 'other-batch', iid))
            with self.assertRaises(ValueError):
                operator.make_plan(db, self.payload)

    def test_apply_retry_restart_download_and_rollback_preserve_all_other_data(self):
        with self.connect() as db:
            # Keep an unrelated real intake/file and an existing file on a target.
            intake.add(db, 'b', self.vb, {'scope': 'sfera', 'batch': 'other-original',
                                        'entries': [{'name': 'Чужой профиль'}]})
            iid = self.payload['files'][0]['intakeId']
            body = json.loads(self.bodies(db)[iid])
            body['files'] = [{'id': 'f' * 24, 'name': 'existing.txt', 'size': 8}]
            db.execute('INSERT INTO em_intake_files VALUES (?,?,?,?,?,?,?,?)',
                       ('f' * 24, iid, 'existing.txt', 'text/plain', b'existing', 'existing', '{}', 'parsed'))
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (operator.dump(body), iid))
            projected = next(p for p in intake.list_candidates(db, 'a', self.va)['candidates'] if p['id'] == iid)
            intake.screening_comment(db, 'a', self.va, {'id': iid, 'vacancyId': self.va, 'recordingIndex': 0,
                'seconds': 5, 'text': 'Существующий комментарий', 'requestId': 'before-pdf',
                'expectedRevision': projected['reviewRevision']})
            before = self.bodies(db)
            tables = ('cp_candidates', 'cp_applications', 'cp_consent_log', 'cp_interviews', 'cp_meetings',
                      'em_intake_review_events', 'em_intake_batches')
            snapshots = {table: db.execute('SELECT * FROM ' + table).fetchall() for table in tables}
            plan = operator.make_plan(db, self.payload)
            result = operator.apply(db, self.payload, plan, self.backup_path)
            self.assertEqual(len(result['attached']), 4)
            self.assertEqual(self.backup_path.stat().st_mode & 0o777, 0o600)
            for entry in self.payload['files']:
                current = json.loads(self.bodies(db)[entry['intakeId']])
                expected = json.loads(before[entry['intakeId']])
                added = current['files'].pop()
                self.assertEqual(current, expected)
                self.assertEqual(added, {'id': operator.file_id(entry), 'name': entry['name'], 'size': entry['size']})
                content, _ = intake.file(db, 'a', added['id'])
                self.assertEqual(content, self.contents[entry['sourceCandidateId']])
                self.assert_problem(404, intake.file, db, 'b', added['id'])
            self.assertEqual(snapshots, {table: db.execute('SELECT * FROM ' + table).fetchall() for table in tables})
        with self.connect() as db:
            self.assertTrue(operator.apply(db, self.payload, plan, self.backup_path)['replayed'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_intake_files').fetchone()[0], 5)
            restored = operator.rollback(db, operator.read_json(self.backup_path))
            self.assertEqual(restored['detached'], result['attached'])
            self.assertEqual(self.bodies(db), before)
            self.assertEqual(db.execute('SELECT id,content FROM em_intake_files').fetchall(), [('f' * 24, b'existing')])
            self.assertEqual(snapshots, {table: db.execute('SELECT * FROM ' + table).fetchall() for table in tables})
            self.assertTrue(operator.rollback(db, operator.read_json(self.backup_path))['replayed'])
            with self.assertRaisesRegex(ValueError, 'rolled back'):
                operator.apply(db, self.payload, plan, self.backup_path)

    def test_stale_plan_or_conflicting_file_never_overwrites_or_partially_attaches(self):
        with self.connect() as db:
            plan = operator.make_plan(db, self.payload)
            iid = self.payload['files'][0]['intakeId']; original = self.bodies(db)[iid]
            body = json.loads(original); body['note'] = 'Новая информация'
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (operator.dump(body), iid))
            with self.assertRaisesRegex(ValueError, 'changed after'):
                operator.apply(db, self.payload, plan, self.backup_path)
            self.assertFalse(self.backup_path.exists())
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_intake_files').fetchone()[0], 0)
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (original, iid))
            db.execute('INSERT INTO em_intake_files VALUES (?,?,?,?,?,?,?,?)',
                       (plan['attachments'][0]['fileId'], iid, 'other.pdf', 'application/pdf', b'other', '', '{}', 'unsupported'))
            with self.assertRaisesRegex(ValueError, 'already exists'):
                operator.apply(db, self.payload, plan, self.backup_path)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_intake_files').fetchone()[0], 1)

    def test_atomic_failure_leaves_private_backup_for_safe_retry(self):
        with self.connect() as db:
            before = self.bodies(db); plan = operator.make_plan(db, self.payload)
            # Fail at the second PDF, after the first insert and body update.
            fid = plan['attachments'][1]['fileId']
            db.execute("CREATE TRIGGER fail_pdf BEFORE INSERT ON em_intake_files WHEN NEW.id='" + fid +
                       "' BEGIN SELECT RAISE(ABORT,'synthetic'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                operator.apply(db, self.payload, plan, self.backup_path)
            self.assertTrue(self.backup_path.exists())
            self.assertEqual(self.bodies(db), before)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_intake_files').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_store_commands WHERE kind=?',
                                        ('intake.resume.attach',)).fetchone()[0], 0)
            db.execute('DROP TRIGGER fail_pdf')
            self.assertEqual(len(operator.apply(db, self.payload, plan, self.backup_path)['attached']), 4)

    def test_rollback_refuses_tampered_backup_profile_or_file(self):
        with self.connect() as db:
            operator.apply(db, self.payload, operator.make_plan(db, self.payload), self.backup_path)
            backup = operator.read_json(self.backup_path)
            tampered = copy.deepcopy(backup); tampered['changes'][0]['beforeBody'] = '{}'
            with self.assertRaisesRegex(ValueError, 'does not match'):
                operator.rollback(db, tampered)
            iid = backup['changes'][0]['id']; original = self.bodies(db)[iid]
            body = json.loads(original); body['note'] = 'Правка после привязки'
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (operator.dump(body), iid))
            with self.assertRaisesRegex(ValueError, 'Profile changed'):
                operator.rollback(db, backup)
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (original, iid))
            db.execute('UPDATE em_intake_files SET content=? WHERE id=?', (b'modified', backup['changes'][0]['fileId']))
            with self.assertRaisesRegex(ValueError, 'file changed'):
                operator.rollback(db, backup)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM em_intake_files').fetchone()[0], 4)


if __name__ == '__main__':
    unittest.main()
