"""Independent candidate-review acceptance through the real local HTTP handler.

Only accounts/CVs in a TemporaryDirectory are used. The video provider and
prepared-recording resolver are synthetic; no production writes or delivery.
Run: python3 -m unittest -v test_employer_candidate_review_acceptance
Browser: python3 test_employer_candidate_review_acceptance.py --serve-fixture
  /tmp/candidate-review.json --video-fixture /absolute/path/to/synthetic.mp4
The JSON contains an ephemeral local session token and must not be committed.
"""
import argparse
import base64
import json
from pathlib import Path
import signal
import threading
import unittest
import uuid
from unittest.mock import patch

from domain import iso, now
import employer_intake
import employer_projection
import test_employer_acceptance as fixture
from test_candidate_media import MaterialMedia
import test_employer_stage_acceptance as stages_fixture


class ReviewMedia(MaterialMedia):
    """The external media boundary, never a replacement for application APIs."""
    def __init__(self):
        super().__init__()
        self.sources = {}

    def materials(self, room, known=None):
        source = self.sources.get(room['id'])
        if source is None:
            return super().materials(room, known)
        rid, tid = room['id'] + '-video', room['id'] + '-transcript'
        transcript = '\n\n'.join(['WEBVTT', *[
            f'{index + 1}\n00:00:{start:02}.000 --> 00:00:{end:02}.000\n<v {source}>ONLY_{source}_{label}</v>'
            for index, (start, end, label) in enumerate(((0, 10, 'PROFILE'), (12, 25, 'EXPERIENCE'), (40, 55, 'RESULT')))
        ]])
        return {'recordings': [{'id': rid, 'url': 'https://media.example/' + rid + '.mp4', 'duration': 90}],
                'transcripts': [{'id': tid, 'text': transcript}],
                'video': {'status': 'ready', 'ids': [rid]},
                'transcription': {'status': 'ready', 'ids': [tid]}, 'checkedAt': iso(now())}


class EmployerCandidateReviewAcceptanceTest(unittest.TestCase):
    setUp = fixture.EmployerAcceptanceTest.setUp
    request = fixture.EmployerAcceptanceTest.request
    ok = fixture.EmployerAcceptanceTest.ok
    employer = fixture.EmployerAcceptanceTest.employer
    vacancy = fixture.EmployerAcceptanceTest.vacancy
    state = fixture.EmployerAcceptanceTest.state
    candidate = fixture.EmployerAcceptanceTest.candidate
    batch = fixture.EmployerAcceptanceTest.batch
    accepted = fixture.EmployerAcceptanceTest.accepted
    action_body = fixture.EmployerAcceptanceTest.action_body
    act = fixture.EmployerAcceptanceTest.act
    propose = fixture.EmployerAcceptanceTest.propose
    exact = staticmethod(fixture.EmployerAcceptanceTest.exact)
    person_state = stages_fixture.EmployerStageAcceptanceTest.person_state
    completed_meeting = stages_fixture.EmployerStageAcceptanceTest.completed_meeting
    stage_action = stages_fixture.EmployerStageAcceptanceTest.stage_action
    create_test = stages_fixture.EmployerStageAcceptanceTest.create_test

    @classmethod
    def setUpClass(cls):
        fixture.EmployerAcceptanceTest.setUpClass.__func__(cls)
        cls.video_path = None
        class LocalVideoHandler(cls.app.Handler):
            def do_GET(self):
                if self.path != '/api/hr/_qa/video' or cls.video_path is None:
                    return super().do_GET()
                content = cls.video_path.read_bytes()
                start, end = 0, len(content) - 1
                range_header = self.headers.get('Range', '')
                if range_header.startswith('bytes='):
                    bounds = range_header[6:].split('-', 1)
                    start = int(bounds[0] or 0)
                    end = min(end, int(bounds[1])) if bounds[1] else end
                if not 0 <= start <= end < len(content):
                    self.send_error(416)
                    return
                self.send_response(206 if range_header else 200)
                self.send_header('Content-Type', 'video/mp4')
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Length', str(end - start + 1))
                if range_header:
                    self.send_header('Content-Range', f'bytes {start}-{end}/{len(content)}')
                self.end_headers()
                self.wfile.write(content[start:end + 1])
        cls.server.RequestHandlerClass = LocalVideoHandler
        cls.media = ReviewMedia()
        cls.app.MEDIA = cls.media
        cls.app.CANDIDATE_PORTAL.media = cls.media
        cls.app.EMPLOYER_ROUTES.media = cls.media
        cls.resolver = patch.object(employer_intake, 'resolve_recording', return_value={
            'url': 'https://media.example/prepared-local-only.mp4', 'mime': 'video/mp4'})
        cls.resolver.start()

    @classmethod
    def tearDownClass(cls):
        cls.resolver.stop()
        fixture.EmployerAcceptanceTest.tearDownClass.__func__(cls)

    def review_person(self, employer, vacancy, name, source):
        person, aid = self.accepted(employer, vacancy, self.candidate(name))
        room = self.media.room(person['initialId'])
        self.media.sources[room['id']] = source
        with self.app.connect() as db:
            db.execute('UPDATE cp_interviews SET room=? WHERE id=?', (json.dumps(room), person['initialId']))
        command = self.action_body(employer, vacancy, aid, 'read')
        materials = self.ok('employer/screening/materials', command, token=employer['token'])
        self.assertEqual(materials['video']['status'], 'ready')
        return person, aid, materials['recordings'][0]['id']

    def comment_command(self, employer, vacancy, person, aid, recording_id, **changes):
        current = self.person_state(employer, vacancy, aid)
        return self.action_body(employer, vacancy, aid, 'screening.comment.add', **{
            'interviewId': person['initialId'], 'recordingId': recording_id, 'seconds': 12,
            'chapterTitle': 'Синтетический таймкод', 'text': 'ONLY_OWNER_COMMENT',
            'expectedRevision': current['workflowRevision'], **changes})

    def before_video(self, employer, vacancy):
        person, aid = self.accepted(employer, vacancy, self.candidate('Галина До появления видео'))
        room = self.media.room(person['initialId'])
        self.media.sources[room['id']] = 'LATE_VIDEO'
        with self.app.connect() as db:
            db.execute('UPDATE cp_interviews SET room=? WHERE id=?', (json.dumps(room), person['initialId']))
        self.ok('employer/action', self.comment_command(employer, vacancy, person, aid, None,
                seconds=20, text='BEFORE_VIDEO_COMMENT'), token=employer['token'])
        return person, aid

    def intake_state(self, employer, vacancy, intake_id):
        people = self.ok('employer/intake?vacancyId=' + vacancy['id'], token=employer['token'])['candidates']
        return next(person for person in people if person['id'] == intake_id)

    def intake_comment(self, employer, vacancy, person, **changes):
        current = self.intake_state(employer, vacancy, person['id'])
        return {'vacancyId': vacancy['id'], 'id': person['id'], 'recordingIndex': 0,
                'seconds': 12, 'text': 'ONLY_PREPARED_COMMENT',
                'requestId': uuid.uuid4().hex, 'expectedRevision': current['reviewRevision'], **changes}

    def imported(self, employer, vacancy, name='Вера Импортированная'):
        with self.app.connect() as db:
            person = employer_intake.add(db, employer['workspace']['id'], vacancy['id'], {
                'batch': 'review-intake-' + uuid.uuid4().hex, 'scope': 'sfera',
                'entries': [{'name': name, 'role': 'Менеджер', 'note': 'ONLY_PREPARED_NOTE'}],
            })['candidates'][0]
            person['preparedProfile'] = {
                'cvText': 'ONLY_PREPARED_CV. Синтетический профиль из импортированного файла.',
                'evaluationText': 'ONLY_PREPARED_EVALUATION. Отдельная оценка рекрутера.',
                'sourceLabel': 'Локальная подготовленная подборка',
                'recordings': [{'publicUrl': 'https://disk.yandex.ru/i/review_fixture',
                    'name': 'synthetic-interview.mp4', 'mime': 'video/mp4', 'size': 1024}],
            }
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?',
                       (json.dumps(person, ensure_ascii=False), person['id']))
        return person

    def test_comments_on_each_timestamp_persist_and_do_not_create_stage_feedback(self):
        employer = self.employer('Review persistence')
        vacancy = self.vacancy(employer)
        person, aid, recording = self.review_person(employer, vacancy, 'Анна Для проверки', 'ALPHA')
        before = self.person_state(employer, vacancy, aid)
        for index, seconds in enumerate((0, 12, 40)):
            command = self.comment_command(employer, vacancy, person, aid, recording)
            command.update(seconds=seconds, text=f'ONLY_COMMENT_{index}')
            self.ok('employer/action', command, token=employer['token'])
        current = self.person_state(employer, vacancy, aid)
        comments = current['screeningComments']
        self.assertEqual(len({item['id'] for item in comments}), 3)
        self.assertEqual([(item['seconds'], item['text']) for item in comments],
                         [(0, 'ONLY_COMMENT_0'), (12, 'ONLY_COMMENT_1'), (40, 'ONLY_COMMENT_2')])
        self.assertTrue(all(item['interviewId'] == person['initialId'] and item['recordingId'] == recording for item in comments))
        self.assertEqual(current['stages'], before['stages'])
        self.assertIsNone(current['feedback'])
        with self.app.connect() as db:
            restored = employer_projection.build_state(db,
                {'workspaceId': employer['workspace']['id'], 'name': 'QA', 'team': False},
                self.app.CANDIDATE_PORTAL, self.media, vacancy['id'])['candidates'][aid]
            self.assertEqual(restored['screeningComments'], comments)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_meeting_feedback WHERE application_id=?', (aid,)).fetchone()[0], 0)

    def test_comment_before_video_remains_scoped_when_the_first_recording_arrives(self):
        employer = self.employer('Review before video')
        vacancy = self.vacancy(employer)
        person, aid = self.before_video(employer, vacancy)
        initial = self.person_state(employer, vacancy, aid)['screeningComments']
        self.assertEqual(len(initial), 1)
        self.assertIsNone(initial[0]['recordingId'])
        source = self.ok('employer/screening/materials', self.action_body(employer, vacancy, aid, 'read'), token=employer['token'])
        self.assertEqual(len(source['recordings']), 1)
        self.assertEqual(self.person_state(employer, vacancy, aid)['screeningComments'], initial)
        self.assertEqual(self.request('employer/action', self.comment_command(employer, vacancy, person, aid, None), token=employer['token'])[0], 400)
        self.ok('employer/action', self.comment_command(employer, vacancy, person, aid, source['recordings'][0]['id'],
                text='EXACT_NEW_VIDEO_COMMENT'), token=employer['token'])
        self.assertEqual(len(self.person_state(employer, vacancy, aid)['screeningComments']), 2)

    def test_retry_is_one_comment_and_foreign_context_cannot_write_or_read_it(self):
        employer, outsider = self.employer('Review owner'), self.employer('Review outsider')
        vacancy, other_vacancy = self.vacancy(employer), self.vacancy(employer, 'Другая вакансия')
        person, aid, recording = self.review_person(employer, vacancy, 'Анна Для проверки', 'ALPHA')
        other_person, other_aid, other_recording = self.review_person(employer, vacancy, 'Борис Другой', 'BETA')
        _, same_person_aid = self.accepted(employer, other_vacancy, person)
        command = self.comment_command(employer, vacancy, person, aid, recording)
        first = self.ok('employer/action', command, token=employer['token'])['candidates'][aid]
        again = self.ok('employer/action', command, token=employer['token'])['candidates'][aid]
        self.assertEqual(first['screeningComments'], again['screeningComments'])
        self.assertEqual(len(again['screeningComments']), 1)
        self.assertEqual(self.request('employer/action', {**command, 'text': 'CHANGED_REPLAY'}, token=employer['token'])[0], 409)
        cases = [
            (employer, vacancy, other_aid, person, recording),
            (employer, vacancy, aid, other_person, other_recording),
            (employer, vacancy, aid, person, other_recording),
            (employer, other_vacancy, aid, person, recording),
            (outsider, vacancy, aid, person, recording),
        ]
        for actor, target, target_aid, source, rid in cases:
            body = {**command, 'requestId': uuid.uuid4().hex, 'applicationId': target_aid,
                    'candidate': target_aid, 'vacancyId': target['id'],
                    'interviewId': source['initialId'], 'recordingId': rid, 'text': 'FOREIGN_WRITE'}
            if actor is employer:
                target_state = self.state(actor, target)
                body['generation'] = target_state['generation']
                body['expectedRevision'] = target_state['candidates'].get(target_aid, again)['workflowRevision']
            status, _, _ = self.request('employer/action', body, token=actor['token'])
            self.assertIn(status, (400, 404, 409))
        self.assertEqual(self.person_state(employer, vacancy, aid)['screeningComments'], again['screeningComments'])
        self.assertEqual(self.person_state(employer, vacancy, other_aid)['screeningComments'], [])
        self.assertEqual(self.person_state(employer, other_vacancy, same_person_aid)['screeningComments'], [])
        self.assertEqual(self.request('employer/state?vacancyId=' + vacancy['id'], token=outsider['token'])[0], 404)
        # Shared interview, different application: both may annotate it, privately.
        separate = self.comment_command(employer, other_vacancy, person, same_person_aid, recording)
        separate['text'] = 'ONLY_OTHER_VACANCY_COMMENT'
        self.ok('employer/action', separate, token=employer['token'])
        self.assertNotIn('ONLY_OTHER_VACANCY_COMMENT', json.dumps(self.person_state(employer, vacancy, aid)['screeningComments']))

    def test_prepared_review_is_private_durable_and_does_not_invent_onboarding(self):
        employer, outsider = self.employer('Prepared owner'), self.employer('Prepared outsider')
        vacancy, other_vacancy = self.vacancy(employer), self.vacancy(employer, 'Другая подготовленная')
        person, other = self.imported(employer, vacancy), self.imported(employer, other_vacancy)
        tables = ('cp_candidates', 'cp_applications', 'cp_consent_log', 'cp_interviews', 'cp_meeting_feedback', 'cn_outbox')
        with self.app.connect() as db:
            before = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in tables}
        command = self.intake_comment(employer, vacancy, person)
        self.ok('employer/intake/comment', command, token=employer['token'])
        self.ok('employer/intake/comment', command, token=employer['token'])
        self.assertEqual(len(self.intake_state(employer, vacancy, person['id'])['screeningComments']), 1)
        self.assertEqual(self.request('employer/intake/comment', {**command, 'text': 'CHANGED_REPLAY'}, token=employer['token'])[0], 409)
        for index, seconds in enumerate((0, 40)):
            self.ok('employer/intake/comment', self.intake_comment(employer, vacancy, person,
                    seconds=seconds, text=f'ONLY_PREPARED_{index}'), token=employer['token'])
        current = self.intake_state(employer, vacancy, person['id'])
        self.assertEqual(len(current['screeningComments']), 3)
        self.assertTrue(all(item['recordingIndex'] == 0 for item in current['screeningComments']))
        self.assertEqual(self.intake_state(employer, other_vacancy, other['id'])['screeningComments'], [])
        for actor, target in ((outsider, vacancy), (employer, other_vacancy)):
            foreign = {**command, 'vacancyId': target['id'], 'requestId': uuid.uuid4().hex,
                       'expectedRevision': current['reviewRevision']}
            self.assertIn(self.request('employer/intake/comment', foreign, token=actor['token'])[0], (404, 409))
        answers = {'reason': 'ONLY_REJECTION_REASON', 'missing': 'ONLY_REJECTION_MISSING', 'improve': 'ONLY_REJECTION_IMPROVE'}
        rejection = {'vacancyId': vacancy['id'], 'id': person['id'], 'rejectionAnswers': answers,
                     'requestId': uuid.uuid4().hex, 'expectedRevision': current['reviewRevision']}
        self.ok('employer/intake/review', rejection, token=employer['token'])
        self.ok('employer/intake/review', rejection, token=employer['token'])
        rejected = self.intake_state(employer, vacancy, person['id'])
        self.assertTrue(rejected['archived'])
        for key, value in answers.items():
            self.assertEqual(rejected['reviewRejection'][key], value)
        self.assertEqual(rejected['screeningComments'], current['screeningComments'])
        with self.app.connect() as db:
            restored = next(item for item in employer_intake.list_candidates(db, employer['workspace']['id'], vacancy['id'])['candidates'] if item['id'] == person['id'])
            self.assertEqual(restored['screeningComments'], current['screeningComments'])
            self.assertEqual(restored['reviewRejection'], rejected['reviewRejection'])
            self.assertEqual(before, {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in tables})

    def test_rejection_answers_are_private_and_do_not_replace_meeting_feedback(self):
        employer = self.employer('Review rejection')
        vacancy = self.vacancy(employer)
        person, aid, recording = self.review_person(employer, vacancy, 'Анна На рассмотрении', 'REJECT')
        self.ok('employer/action', self.comment_command(employer, vacancy, person, aid, recording), token=employer['token'])
        current = self.person_state(employer, vacancy, aid)
        answers = {'reason': 'PRIVATE_REJECT_REASON', 'missing': 'PRIVATE_REJECT_MISSING', 'improve': 'PRIVATE_REJECT_IMPROVE'}
        body = self.action_body(employer, vacancy, aid, 'review.reject',
                                expectedRevision=current['workflowRevision'], rejectionAnswers=answers)
        self.ok('employer/action', body, token=employer['token'])
        self.ok('employer/action', body, token=employer['token'])
        self.assertNotIn(aid, self.state(employer, vacancy)['candidates'])
        with self.app.connect() as db:
            row = db.execute('SELECT answers,created_at FROM cp_review_rejections WHERE application_id=?', (aid,)).fetchone()
            self.assertEqual(json.loads(row[0]), answers)
            self.assertTrue(row[1])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_meeting_feedback WHERE application_id=?', (aid,)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_screening_comments WHERE application_id=?', (aid,)).fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_meetings WHERE application_id=?', (aid,)).fetchone()[0], 0)
        candidate_state = json.dumps(self.ok('candidate/state', cookie=person['cookie']), ensure_ascii=False)
        self.assertNotIn('PRIVATE_REJECT_', candidate_state)
        self.assertNotIn('ONLY_OWNER_COMMENT', candidate_state)

    def test_replacing_prepared_recording_does_not_attach_old_notes_to_new_video(self):
        employer = self.employer('Prepared replacement')
        vacancy = self.vacancy(employer)
        person = self.imported(employer, vacancy)
        old = self.intake_comment(employer, vacancy, person)
        self.ok('employer/intake/comment', old, token=employer['token'])
        original = self.intake_state(employer, vacancy, person['id'])
        stale = self.intake_comment(employer, vacancy, person, text='STALE_VIDEO_NOTE')
        with self.app.connect() as db:
            raw = json.loads(db.execute('SELECT body FROM em_intake_candidates WHERE id=?', (person['id'],)).fetchone()[0])
            raw['preparedProfile']['recordings'][0]['publicUrl'] = 'https://disk.yandex.ru/i/replaced_fixture'
            db.execute('UPDATE em_intake_candidates SET body=? WHERE id=?', (json.dumps(raw), person['id']))
        replaced = self.intake_state(employer, vacancy, person['id'])
        self.assertNotEqual(replaced['reviewRevision'], original['reviewRevision'])
        self.assertEqual(replaced['screeningComments'], [])
        self.assertEqual(self.request('employer/intake/comment', stale, token=employer['token'])[0], 409)
        self.ok('employer/intake/comment', self.intake_comment(employer, vacancy, person,
                text='NEW_VIDEO_NOTE'), token=employer['token'])
        latest = self.intake_state(employer, vacancy, person['id'])
        self.assertEqual([comment['text'] for comment in latest['screeningComments']], ['NEW_VIDEO_NOTE'])
        self.assertNotEqual(latest['screeningComments'][0]['recordingId'], original['screeningComments'][0]['recordingId'])

    def test_original_cv_download_uses_exact_identity_and_owner(self):
        employer, outsider = self.employer('CV owner'), self.employer('CV outsider')
        vacancy = self.vacancy(employer)
        person, aid, _ = self.review_person(employer, vacancy, 'Анна Исходное резюме', 'CV')
        other, other_aid, _ = self.review_person(employer, vacancy, 'Борис Чужое резюме', 'OTHER_CV')
        body = self.action_body(employer, vacancy, aid, 'read')
        result = self.ok('employer/screening/resume', body, token=employer['token'])
        self.assertEqual(result['filename'], 'acceptance.txt')
        decoded = base64.b64decode(result['contentBase64']).decode()
        self.assertIn('Анна Исходное резюме', decoded)
        self.assertNotIn('Борис Чужое резюме', decoded)
        self.assertEqual(self.request('employer/screening/resume', body, token=outsider['token'])[0], 404)
        self.assertIn(self.request('employer/screening/resume', {**body, 'candidate': other_aid}, token=employer['token'])[0], (400, 404, 409))

    def test_invalid_rejection_rolls_back_pending_meeting_and_notification_events(self):
        employer = self.employer('Reject transaction')
        vacancy = self.vacancy(employer)
        person, aid, _ = self.review_person(employer, vacancy, 'Анна Со встречей', 'PENDING')
        meeting = self.propose(employer, vacancy, aid)
        current = self.person_state(employer, vacancy, aid)
        tables = ('cp_workflow_messages', 'cn_outbox', 'cp_review_rejections')
        with self.app.connect() as db:
            counts = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in tables}
        command = self.action_body(employer, vacancy, aid, 'review.reject',
                    expectedRevision=current['workflowRevision'], rejectionAnswers={'reason': ''})
        self.assertEqual(self.request('employer/action', command, token=employer['token'])[0], 400)
        after = self.person_state(employer, vacancy, aid)
        self.assertEqual(after['meeting'], current['meeting'])
        self.assertEqual(after['workflowRevision'], current['workflowRevision'])
        with self.app.connect() as db:
            self.assertEqual(counts, {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in tables})
        answers = {'reason': 'EXACT_PENDING_REASON', 'missing': 'Уточнение', 'improve': 'Другой опыт'}
        self.ok('employer/action', {**command, 'requestId': uuid.uuid4().hex, 'rejectionAnswers': answers}, token=employer['token'])
        with self.app.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM cp_meetings WHERE id=?', (meeting['id'],)).fetchone()[0], 'cancelled')
            self.assertEqual(json.loads(db.execute('SELECT answers FROM cp_review_rejections WHERE application_id=?', (aid,)).fetchone()[0]), answers)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cp_meeting_feedback WHERE application_id=?', (aid,)).fetchone()[0], 0)

    def seed_browser(self):
        employer = self.employer('Review browser')
        vacancy = self.vacancy(employer, 'QA · Подбор кандидатов')
        other_vacancy = self.vacancy(employer, 'QA · Другая вакансия')
        alpha, alpha_aid, alpha_recording = self.review_person(employer, vacancy, 'Алёна Проверочная', 'ALPHA')
        beta, beta_aid, beta_recording = self.review_person(employer, vacancy, 'Борис Проверочный', 'BETA')
        _, other_aid = self.accepted(employer, other_vacancy, alpha)
        late, late_aid = self.before_video(employer, other_vacancy)
        prepared = self.imported(employer, vacancy)
        selected, selected_aid = self.accepted(employer, vacancy, self.candidate('Сергей Выбранный'))
        first = self.completed_meeting(employer, vacancy, selected, selected_aid)
        test = self.create_test(employer, vacancy, selected_aid, 'ONLY_SELECTED_STAGE_TEST')
        return {'localOnly': True, 'fixture': 'real-http-temp-sqlite-fake-video',
                'apiBase': self.url, 'token': employer['token'], 'vacancy': vacancy,
                'otherVacancy': other_vacancy, 'otherApplicationId': other_aid,
                'beforeVideo': {'applicationId': late_aid, 'interviewId': late['initialId']},
                'alpha': {'name': 'Алёна Проверочная', 'applicationId': alpha_aid, 'candidateId': alpha['id'],
                          'interviewId': alpha['initialId'], 'recordingId': alpha_recording},
                'beta': {'name': 'Борис Проверочный', 'applicationId': beta_aid, 'candidateId': beta['id'],
                         'interviewId': beta['initialId'], 'recordingId': beta_recording},
                'prepared': {'id': prepared['id'], 'name': prepared['name']},
                'selected': {'name': 'Сергей Выбранный', 'applicationId': selected_aid,
                             'meetingId': first['id'], 'testStageId': test['id']}}


def serve_fixture(destination, video_path=None):
    cls = EmployerCandidateReviewAcceptanceTest
    cls.setUpClass()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        case = cls()
        case.setUp()
        result = case.seed_browser()
        if video_path:
            path = Path(video_path).resolve(strict=True)
            cls.video_path = path
            result['videoPath'] = str(path)
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        target.chmod(0o600)
        print('Local candidate-review fixture ready: ' + str(target), flush=True)
        stop.wait()
    finally:
        Path(destination).unlink(missing_ok=True)
        cls.tearDownClass()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve-fixture')
    parser.add_argument('--video-fixture')
    args, remaining = parser.parse_known_args()
    if args.serve_fixture:
        serve_fixture(args.serve_fixture, args.video_fixture)
    else:
        unittest.main(argv=[__file__, *remaining])
