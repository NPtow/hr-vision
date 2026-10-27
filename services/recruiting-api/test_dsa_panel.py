import copy
import hashlib
import io
import json
import unittest
from unittest.mock import patch
from dsa_panel import DsaPanel, normalize, imported_materials, SOURCE
from domain import Problem

FIXTURE = {'вакансия': {'компания': 'Тестовый источник', 'название': 'Менеджер'}, 'кандидаты': [
    {'id': '123', 'имя': 'Пример', 'лицо': '/faces/123.jpg', 'видео': {'есть': True, 'url': 'private-source-url'},
     'нарезка': {'куски': [{'начало': 12, 'конец': 20, 'таймкод': '00:12', 'метка': 'Опыт', 'цитата': 'Прямая речь'}]},
     'главы': [{'начало': 0, 'конец': 30, 'таймкод': '00:00', 'название': 'Введение', 'о_чём': 'Описание автора'}],
     'профиль': [{'метка': 'Опыт', 'значение': 'Текст источника'}], 'заключение': ['Мнение рекрутера'],
     'статус': 'не подходит', 'внутренний_ключ': 'private', 'оценка': 4.0}]}


def imported_fixture(cid='candidate', iid='interview'):
    transcript = 'WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v Кандидат>Проверю сроки.</v>\n'
    return {'version': 1, 'candidateId': cid, 'interviewId': iid, 'preparedProfile': {
        'source': {'kind': 'dsa-panel', 'candidateId': '123', 'example': True},
        'recordings': [{'source': 'dsa-panel', 'sourceCandidateId': '123',
                        'publicUrl': SOURCE + '/api/video/123', 'mime': 'video/mp4', 'duration': 30}],
        'transcriptText': transcript, 'transcriptSource': {'candidateId': '123',
            'sha256': hashlib.sha256(transcript.encode()).hexdigest()}}}


class ImportedInterviewTest(unittest.TestCase):
    def test_keeps_exact_video_and_transcript_without_network_or_provider_room(self):
        saved = imported_fixture()
        with patch('dsa_panel.urllib.request.urlopen', side_effect=AssertionError('network')):
            result = imported_materials(saved, 'candidate', 'interview')
        self.assertEqual(result['recordings'][0]['url'], SOURCE + '/api/video/123')
        self.assertEqual(result['transcripts'][0]['text'], saved['preparedProfile']['transcriptText'])
        self.assertEqual(result['video']['status'], 'ready')
        self.assertEqual(result['recordings'][0]['id'], imported_materials(saved, 'candidate', 'interview')['recordings'][0]['id'])

    def test_keeps_authored_chapters_separate_from_transcript_cues(self):
        saved = imported_fixture()
        chapter = {'title': 'Опыт', 'seconds': 0, 'endSeconds': 20,
                   'detail': 'Исходное описание', 'fragment': '', 'source': '', 'isQuote': False}
        saved['preparedProfile']['chapters'] = [chapter,
            {**chapter, 'seconds': float('nan')}, {**chapter, 'endSeconds': 35},
            {**chapter, 'kind': 'transcript'}, {**chapter, 'seconds': -1}]
        result = imported_materials(saved, 'candidate', 'interview')
        self.assertEqual(result['chapters'], [chapter])
        self.assertEqual(result['transcripts'][0]['text'], saved['preparedProfile']['transcriptText'])

    def test_rejects_wrong_owner_interview_source_url_or_modified_transcript(self):
        for field, value in [('candidateId', 'other'), ('interviewId', 'other')]:
            saved = imported_fixture(); saved[field] = value
            with self.subTest(field=field), self.assertRaises(Problem):
                imported_materials(saved, 'candidate', 'interview')
        for field, value in [('publicUrl', 'https://other.example/video/123'),
                             ('sourceCandidateId', '456'), ('duration', float('nan'))]:
            saved = imported_fixture(); saved['preparedProfile']['recordings'][0][field] = value
            with self.subTest(field=field), self.assertRaises(Problem):
                imported_materials(saved, 'candidate', 'interview')
        saved = imported_fixture(); saved['preparedProfile']['transcriptText'] += 'changed'
        with self.assertRaises(Problem): imported_materials(saved, 'candidate', 'interview')


class DsaPanelTest(unittest.TestCase):
    def test_preserves_evidence_and_original_decision_without_fabricating_state(self):
        data = copy.deepcopy(FIXTURE)
        p = normalize(data)['candidates'][0]
        self.assertEqual(data, FIXTURE)
        self.assertEqual(p['shortChapters'][0]['fragment'], 'Прямая речь')
        self.assertEqual(p['shortChapters'][0]['endSeconds'], 20)
        self.assertTrue(p['shortChapters'][0]['isQuote'])
        self.assertFalse(p['fullChapters'][0]['isQuote'])
        self.assertEqual(p['sourceStatus'], 'не подходит')
        self.assertNotIn('interest', p); self.assertNotIn('meeting', p)
        self.assertNotIn('private', json.dumps(p))
        self.assertEqual(p['video'], SOURCE + '/api/video/123')

    def test_source_endpoint_cannot_be_injected(self):
        data = copy.deepcopy(FIXTURE)
        data['кандидаты'][0]['id'] = '../../other'
        with self.assertRaises(ValueError): normalize(data)

    def test_reads_only_visible_candidates_and_caches_without_writing_to_source(self):
        client = DsaPanel()
        with patch('dsa_panel.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(FIXTURE).encode())) as opened:
            self.assertEqual(len(client.load()['candidates']), 1)
            client.load()
            self.assertEqual(opened.call_count, 1)
            self.assertEqual(opened.call_args[0][0].get_method(), 'GET')

    def test_outage_does_not_return_stale_or_fake_profiles(self):
        client = DsaPanel(); client.cached = normalize(FIXTURE); client.expires = 0
        with patch('dsa_panel.urllib.request.urlopen', side_effect=RuntimeError('private internal detail')):
            with self.assertRaises(Problem) as error: client.load()
            self.assertEqual(error.exception.status, 502)
            self.assertNotIn('private', error.exception.message)
