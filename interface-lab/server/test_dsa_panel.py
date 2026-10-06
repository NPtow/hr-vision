import copy
import io
import json
import unittest
from unittest.mock import patch
from dsa_panel import DsaPanel, normalize, SOURCE
from domain import Problem

FIXTURE = {'вакансия': {'компания': 'Тестовый источник', 'название': 'Менеджер'}, 'кандидаты': [
    {'id': '123', 'имя': 'Пример', 'лицо': '/faces/123.jpg', 'видео': {'есть': True, 'url': 'private-source-url'},
     'нарезка': {'куски': [{'начало': 12, 'конец': 20, 'таймкод': '00:12', 'метка': 'Опыт', 'цитата': 'Прямая речь'}]},
     'главы': [{'начало': 0, 'конец': 30, 'таймкод': '00:00', 'название': 'Введение', 'о_чём': 'Описание автора'}],
     'профиль': [{'метка': 'Опыт', 'значение': 'Текст источника'}], 'заключение': ['Мнение рекрутера'],
     'статус': 'не подходит', 'внутренний_ключ': 'private', 'оценка': 4.0}]}


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
