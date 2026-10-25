"""CV extraction and mandatory onboarding, using private synthetic documents only."""
import base64
import datetime as dt
import io
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from candidate_portal import CandidatePortal, ClosingConnection, dump
from candidate_resume import MAX_BYTES, extract_preferences, parse_upload
from domain import Problem, iso, now
from test_candidate_portal import FakeMedia


TEXT = ('ФИО: Анна Тестовая\nГород: Москва\nЖелаемая должность: Менеджер по работе с клиентами\n'
        'Опыт работы: пять лет занималась сопровождением клиентов, продажами и переговорами. '
        'Готовила коммерческие предложения, работала с базой клиентов, помогала новым сотрудникам. '
        'Образование: высшее. Навыки: переговоры, анализ данных, планирование и деловая переписка.')


def upload(value=TEXT, filename='resume.txt'):
    content = value.encode('utf-8') if isinstance(value, str) else value
    return {'filename': filename, 'contentBase64': base64.b64encode(content).decode('ascii')}


def docx(text=TEXT, raw_xml=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('word/document.xml', raw_xml or (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' +
            ''.join('<w:p><w:r><w:t>' + line + '</w:t></w:r></w:p>' for line in text.splitlines()) +
            '</w:body></w:document>'))
    return stream.getvalue()


def pdf(text=True):
    lines = ['Anna Test', 'Desired salary: 120000-180000 RUB',
             'Experience: Five years working with customers and business development.',
             'Prepared commercial proposals and supported existing client relationships.',
             'Education: University degree in business. Skills: Sales, communication, planning.']
    commands = 'BT /F1 12 Tf 50 780 Td ' + ' '.join(('0 -18 Td ' if index else '') + '(' + line + ') Tj'
                                                for index, line in enumerate(lines)) + ' ET' if text else '0 0 30 30 re f'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length ' + str(len(commands)).encode() + b' >>\nstream\n' + commands.encode() + b'\nendstream']
    result = b'%PDF-1.4\n'
    offsets = [0]
    for index, item in enumerate(objects, 1):
        offsets.append(len(result))
        result += f'{index} 0 obj\n'.encode() + item + b'\nendobj\n'
    xref = len(result)
    result += b'xref\n0 6\n0000000000 65535 f \n' + b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets[1:])
    return result + f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()


class ResumeExtractionTest(unittest.TestCase):
    def problem(self, status, body):
        with self.assertRaises(Problem) as result:
            parse_upload(body)
        self.assertEqual(result.exception.status, status)

    def test_txt_docx_are_actually_extracted(self):
        for body in (upload(), upload(docx(), 'resume.docx'), upload(TEXT.encode('utf-16'), 'resume.txt')):
            result = parse_upload(body)
            self.assertIn('сопровождением клиентов', result['text'])
            self.assertEqual(result['fields']['city'], 'Москва')
            self.assertEqual(result['fields']['name'], 'Анна Тестовая')
            self.assertNotIn('salaryMin', result['fields'])
            self.assertNotIn('latestStartDate', result['fields'])
            self.assertIn('пять лет занималась', result['fields']['about'])
            self.assertNotIn('Образование', result['fields']['about'])

    def test_short_review_fields_are_only_from_explicit_cv_facts(self):
        result = extract_preferences(TEXT + '\nМесто проживания: м. Сокол\nЗанятость: Частичная занятость')
        self.assertEqual(result['residence'], 'м. Сокол')
        self.assertEqual(result['employmentType'], 'part-time')
        self.assertNotIn('employmentType', extract_preferences('Формат работы: удалённо'))
        self.assertNotIn('employmentType', extract_preferences('Занятость: полная или частичная'))
        self.assertNotIn('about', extract_preferences('Город: Москва\nЖелаемая должность: Менеджер'))
        summary = 'Пять лет работаю с корпоративными клиентами, готовлю предложения и сопровождаю договоры.'
        review = extract_preferences('О себе: ' + summary + '\nТип занятости: полная занятость\nРайон: Сокол\nЖелаемая зарплата: 120000–160000 рублей\nВыйду на работу не позднее: 2030-01-01')
        self.assertEqual(review['about'], summary)

    @unittest.skipUnless(shutil.which('pdftotext'), 'pdftotext runtime is required to test actual PDF extraction')
    def test_real_pdf_text_extracted_and_scanned_document_rejected(self):
        result = parse_upload(upload(pdf(), 'resume.pdf'))
        self.assertIn('commercial proposals', result['text'])
        self.assertEqual(result['fields']['salaryMax'], 180000)
        self.problem(422, upload(pdf(False), 'scan.pdf'))

    def test_only_explicit_desired_salary_is_used(self):
        for value in ('Текущая зарплата: 150000 ₽', 'В ООО Компания получала 150000 ₽',
                      'Оклад: 150000 ₽', 'Зарплата на предыдущей работе: 150000 ₽',
                      'Желаемая зарплата: 100000 USD', 'Желаемая зарплата: 500000 ₽ в год',
                      'Desired salary: 20000 RUB weekly', 'Желаемый доход: 5000 рублей в день',
                      'Желаемая зарплата: 5000 ₽ в неделю', 'Желаемый доход: 1000 ₽ в час'):
            self.assertNotIn('salaryMin', extract_preferences(value), value)
        for value in ('Желаемая зарплата: 120 000–180 000 ₽', 'Зарплатные ожидания: от 120 до 180 тыс. руб.',
                      'Desired salary: 120000-180000 RUB', 'Желаемый доход: 120000–180 тыс. руб.'):
            self.assertEqual(extract_preferences(value), {'salaryMin': 120000, 'salaryMax': 180000})
        self.assertEqual(extract_preferences('Желаемая зарплата: 150000 ₽'), {'salaryMin': 150000})
        self.assertEqual(extract_preferences('Желаемая зарплата: до 180000 ₽'), {'salaryMax': 180000})

    def test_latest_start_never_infers_earliest_or_past_dates(self):
        date = (now() + dt.timedelta(days=20)).date().isoformat()
        for value in (f'Available from: {date}', f'Готов выйти: {date}', 'Дата рождения: 1990-05-01', 'Дата выхода: 2020-01-01'):
            self.assertNotIn('latestStartDate', extract_preferences(value))
        for value in (f'Latest start date: {date}', f'Дата выхода на работу: {date}', f'Готов приступить не позднее {date}'):
            self.assertEqual(extract_preferences(value)['latestStartDate'], date)

    def test_unsupported_invalid_empty_and_oversized_uploads_fail(self):
        self.problem(415, upload(filename='resume.exe'))
        self.problem(400, {'filename': 'resume.txt', 'contentBase64': '!not-base64!'})
        self.problem(413, upload(b'x' * (MAX_BYTES + 1)))
        self.problem(422, upload('Short document'))
        self.problem(422, upload(b'not pdf', 'resume.pdf'))
        self.problem(422, upload(b'not docx', 'resume.docx'))
        self.problem(422, upload(docx(raw_xml='<!DOCTYPE a [<!ENTITY boom "CV">]><a>&boom;</a>'), 'resume.docx'))
        self.problem(422, upload(b'\x00' * 200))


class CandidateCVOnboardingTest(unittest.TestCase):
    def setUp(self):
        generation = patch('candidate_resume_model.ResumeModel.generate', side_effect=lambda resume, *_: resume)
        generation.start()
        self.addCleanup(generation.stop)
        self.tmp = tempfile.TemporaryDirectory(prefix='hr-cv-onboarding-')
        self.path = Path(self.tmp.name) / 'private.sqlite3'
        self.lock = threading.RLock()
        self.portal = CandidatePortal(self.connect, self.lock, FakeMedia(), 'https://portal.example')
        self.person, headers = self.call('register', {'email': 'test@example.test',
            'password': 'test-password-123', 'consentReuse': True})
        self.cookie = headers['Set-Cookie'].split(';')[0]
        self.date = (now() + dt.timedelta(days=20)).date().isoformat()

    def tearDown(self):
        self.tmp.cleanup()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def call(self, route, body=None, cookie='', manager=False):
        path = '/api/hr/' + (route if route.startswith('recruiter/') else 'candidate/' + route)
        return self.portal.handle('GET' if body is None else 'POST', path,
            {'Content-Type': 'application/json', 'Cookie': cookie}, body, '127.0.0.1',
            lambda db: 'manager' if manager else 'nobody')

    def problem(self, status, *args, **kwargs):
        with self.assertRaises(Problem) as result:
            self.call(*args, **kwargs)
        self.assertEqual(result.exception.status, status, result.exception.message)

    def slot(self):
        state, _ = self.call('recruiter/slots', {'start': iso(now() + dt.timedelta(minutes=5)),
             'recruiterName': 'Рекрутер'}, manager=True)
        slot = state['slots'][0]
        from test_candidate_portal import assign_fixture_slot
        with self.connect() as db:
            assign_fixture_slot(db, slot)
        return slot

    def test_registration_creates_identity_before_cv_and_resumes_after_login(self):
        self.assertIsNone(self.person['resume'])
        self.assertEqual(self.person['onboarding']['step'], 'resume')
        self.assertFalse(self.person['autoApprove'])
        self.assertEqual(self.person['availability'], [])
        self.portal = CandidatePortal(self.connect, self.lock, FakeMedia(), 'https://portal.example')
        state, _ = self.call('session', cookie=self.cookie)
        self.assertTrue(state['authenticated'])
        self.assertEqual(state['candidate']['id'], self.person['id'])
        self.assertEqual(state['candidate']['onboarding']['step'], 'resume')
        self.call('logout', {}, self.cookie)
        state, _ = self.call('login', {'email': self.person['email'], 'password': 'test-password-123'})
        self.assertEqual(state['onboarding']['step'], 'resume')

    def test_cv_and_missing_fields_gate_new_booking_on_server(self):
        slot = self.slot()
        self.call('profile', {'name': 'User', 'city': 'Москва', 'desiredRole': 'Менеджер',
            'salaryMin': 100000, 'salaryMax': 150000, 'latestStartDate': self.date}, self.cookie)
        self.problem(409, 'book', {'slotId': slot['id']}, self.cookie)
        self.call('profile', {'salaryMin': None, 'salaryMax': None, 'latestStartDate': ''}, self.cookie)
        state, _ = self.call('resume', upload(TEXT + '\nЖелаемая зарплата: 120000 ₽'), self.cookie)
        self.assertEqual(state['onboarding']['missingFields'], ['employmentType', 'preferredCompanies'])
        self.problem(409, 'book', {'slotId': slot['id']}, self.cookie)
        state, _ = self.call('profile', {'salaryMax': None, 'latestStartDate': '', 'employmentType': 'full-time', 'anyCompany': True, 'resumeReviewed': True}, self.cookie)
        self.assertEqual(state['salaryMin'], 120000)
        self.assertEqual(state['onboarding']['step'], 'complete')
        state, _ = self.call('book', {'slotId': slot['id']}, self.cookie)
        self.assertEqual(state['interview']['status'], 'booked')
        self.assertEqual(state['availability'], [])

    def test_full_cv_requires_review_and_sparse_edits_preserve_data(self):
        availability = [{'start': iso(now() + dt.timedelta(days=1)), 'end': iso(now() + dt.timedelta(days=1, hours=2))}]
        self.call('profile', {'availability': availability, 'about': 'Saved older personal text',
            'geographies': ['Москва'], 'autoApprove': True}, self.cookie)
        state, _ = self.call('resume', upload(TEXT + f'\nЖелаемая зарплата: 120000–180000 ₽\nДата выхода: {self.date}'), self.cookie)
        self.assertEqual(state['onboarding'], {'step': 'preferences', 'missingFields': ['employmentType', 'preferredCompanies']})
        self.assertFalse(state['resumeReviewed'])
        self.assertEqual(state['availability'], availability)
        self.assertIn('пять лет занималась', state['about'])
        self.assertTrue(state['autoApprove'])
        state, _ = self.call('profile', {'salaryMax': 200000, 'anyCompany': True, 'resumeReviewed': True, 'about': 'Моё уточнение по опыту',
            'city': 'Химки', 'residence': 'Левобережный район', 'employmentType': 'full-time'}, self.cookie)
        self.assertEqual(state['onboarding']['step'], 'complete')
        self.assertEqual(state['salaryMin'], 120000)
        self.assertEqual(state['city'], 'Химки')
        self.assertEqual(state['about'], 'Моё уточнение по опыту')
        self.assertEqual(state['availability'], availability)
        state, _ = self.call('state', cookie=self.cookie)
        self.assertEqual(state['residence'], 'Левобережный район')
        self.assertEqual(state['employmentType'], 'full-time')
        self.assertTrue(state['resumeReviewed'])

    def test_resume_is_private_and_failed_replacement_keeps_previous_file(self):
        self.call('resume', upload(), self.cookie)
        self.problem(422, 'resume', upload('scan without content'), self.cookie)
        saved, _ = self.call('resume', cookie=self.cookie)
        self.assertEqual(base64.b64decode(saved['contentBase64']).decode(), TEXT)
        self.problem(401, 'resume')
        other, headers = self.call('register', {'email': 'other@example.test', 'password': 'test-password-123', 'consentReuse': True})
        other_cookie = headers['Set-Cookie'].split(';')[0]
        self.problem(404, 'resume', cookie=other_cookie)
        self.problem(400, 'resume', {**upload(), 'candidateId': self.person['id']}, other_cookie)
        for state in (self.call('state', cookie=self.cookie)[0], self.call('recruiter/state', manager=True)[0]):
            self.assertNotIn('contentBase64', dump(state))
            self.assertNotIn('text_content', dump(state))
            self.assertNotIn(TEXT, dump(state))

    def test_minimum_salary_and_company_choices_persist_without_start_date(self):
        self.call('resume', upload(), self.cookie)
        values = {'salaryMin': 120000, 'salaryMax': None, 'employmentType': 'full-time',
                  'preferredCompanies': ['Яндекс', 'Т-Банк'], 'anyCompany': False, 'resumeReviewed': True}
        self.call('profile', values, self.cookie)
        state, _ = self.call('state', cookie=self.cookie)
        self.assertEqual(['Яндекс', 'Т-Банк'], state['preferredCompanies'])
        self.assertIsNone(state['salaryMax'])
        self.assertEqual('', state['latestStartDate'])
        self.assertEqual('complete', state['onboarding']['step'])
        self.problem(400, 'profile', {'anyCompany': True}, self.cookie)
        self.problem(400, 'profile', {'preferredCompanies': []}, self.cookie)
        state, _ = self.call('profile', {'anyCompany': True, 'preferredCompanies': []}, self.cookie)
        self.assertTrue(state['anyCompany'])

    def test_invalid_ranges_dates_and_internal_profile_edits_rejected(self):
        for body in ({'salaryMin': -1}, {'salaryMax': True}, {'salaryMin': 200000, 'salaryMax': 100000},
                     {'latestStartDate': 'yesterday'}, {'latestStartDate': '2020-01-01'}, {'resume': {}},
                     {'onboarding': {'step': 'complete'}}, {'internalProfile': {'summary': 'override'}}):
            self.problem(400, 'profile', body, self.cookie)

    def test_existing_booked_interview_stays_accessible_without_cv(self):
        slot = self.slot()
        with self.connect() as db:
            db.execute('UPDATE cp_recruiter_slots SET candidate_id=? WHERE id=?', (self.person['id'], slot['id']))
            db.execute("INSERT INTO cp_interviews VALUES ('legacy-interview',?,?,'booked',NULL,?,NULL)",
                       (self.person['id'], slot['id'], iso(now())))
        state, _ = self.call('state', cookie=self.cookie)
        self.assertIsNone(state['resume'])
        self.assertEqual(state['onboarding']['step'], 'complete')
        self.assertEqual(state['interview']['id'], 'legacy-interview')
        self.call('book', {'slotId': slot['id']}, self.cookie)  # safe idempotent retry
        self.call('interview/join', {'consent': True}, self.cookie)
        with self.connect() as db:
            old = json.loads(db.execute('SELECT profile FROM cp_candidates WHERE id=?', (self.person['id'],)).fetchone()[0])
            old['latestStartDate'] = '2020-01-01'
            db.execute('UPDATE cp_candidates SET profile=? WHERE id=?', (dump(old), self.person['id']))
        state, _ = self.call('profile', {'city': 'Химки', 'about': 'Уточнение из старого кабинета'}, self.cookie)
        self.assertEqual(state['city'], 'Химки')
        self.assertEqual(state['latestStartDate'], '2020-01-01')
        self.assertEqual(state['onboarding']['step'], 'complete')


if __name__ == '__main__':
    unittest.main()
