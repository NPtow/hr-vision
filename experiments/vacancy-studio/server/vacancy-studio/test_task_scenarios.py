"""Scenario presentation checks. No provider calls or production state."""
import copy
import json
from pathlib import Path
import unittest
from jsonschema import Draft202012Validator, ValidationError
from task_scenarios import SCHEMA, prompt_guides, validate_scenarios, task_catalog

class TaskScenarioTests(unittest.TestCase):
    def scenarios(self):
        return {
            'L2':'Собирать условия покупки и передавать решение руководителю.',
            'L3':'Самостоятельно выяснять условия покупки и обосновывать продолжение.',
            'L4':'Перепроверять противоречивые условия и менять решение после уточнения.',
            'L5':'Разбирать зависимые ограничения и несколько центров принятия решения.',
        }
    def test_complete_mapping(self):
        s=self.scenarios();Draft202012Validator(SCHEMA).validate(s)
        validate_scenarios({'task_scenarios':s})
    def test_missing_option_rejected(self):
        s=self.scenarios();del s['L4']
        with self.assertRaises(ValidationError):Draft202012Validator(SCHEMA).validate(s)
    def test_l1_is_not_silently_reinterpreted_as_a_junior_requirement(self):
        s=self.scenarios();s['L1']='Работать с поддержкой руководителя.'
        with self.assertRaises(ValidationError):Draft202012Validator(SCHEMA).validate(s)
    def test_duplicate_options_cannot_choose_different_levels(self):
        s=self.scenarios();s['L4']=s['L3'].upper()
        with self.assertRaises(ValueError):validate_scenarios({'task_scenarios':s})
    def test_numeric_and_rank_labels_not_employer_options(self):
        for text in ('Уровень 5: вести все сложные продажи.','Senior: вести все сложные продажи.','Вести продажи на 5/5.'):
            s=self.scenarios();s['L5']=text
            with self.subTest(text=text),self.assertRaises(ValueError):validate_scenarios({'task_scenarios':s})
    def test_legacy_proposal_retains_meaning(self):
        p={'parameter_id':'A03','level':1,'quote':'Сохраняем исходную цитату.'}
        before=copy.deepcopy(p);validate_scenarios(p);self.assertEqual(p,before)
    def test_guide_bound_to_all_actual_anchors(self):
        rubric=json.loads((Path(__file__).resolve().parents[2]/'src/scenes/vacancy-research/sales-rubric.json').read_text())
        guides=prompt_guides(rubric)
        self.assertEqual(len(guides),80);self.assertEqual(sum(x['selectable'] for x in guides),64)
        by_key={(x['parameter_id'],x['level_id']):x for x in guides}
        for p in rubric['parameters']:
            for l in p['levels']:
                guide=by_key[p['parameter_id'],l['level_id']]
                self.assertEqual(guide['anchor'],l['description'])
                self.assertEqual(guide['rubric_version'],rubric['version'])
                self.assertIn(l['description'],guide['prompt'])
    def test_work_templates_do_not_change_assessment_anchors(self):
        rubric=json.loads((Path(__file__).resolve().parents[2]/'src/scenes/vacancy-research/sales-rubric.json').read_text())
        before=copy.deepcopy(rubric);catalog=task_catalog(rubric)
        self.assertEqual(rubric,before)
        count=0
        for p,original in zip(catalog['parameters'],rubric['parameters']):
            for l,old in zip(p['levels'],original['levels']):
                self.assertEqual(l['description'],old['description'])
                if l['level_id']=='L1':self.assertNotIn('work_task_template',l)
                else:self.assertGreater(len(l['work_task_template']),12);count+=1
        self.assertEqual(count,64)

if __name__=='__main__':unittest.main()
