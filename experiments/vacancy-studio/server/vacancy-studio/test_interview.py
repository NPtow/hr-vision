"""Regression tests for question purpose, evidence, durable answers and bounded coverage."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server as studio
import interview


class InterviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path, studio.STATE_PATH = studio.STATE_PATH, Path(self.tmp.name)/'db.sqlite3'
        self.state = studio.fresh()
        self.sid = self.state['id']

    def tearDown(self):
        studio.STATE_PATH = self.old_path
        self.tmp.cleanup()

    def fact(self, key='experience', value='3 года', manual=False):
        return {'id':'fact-'+key, 'kind':'fact', 'key':key, 'value':value, 'status':'accepted',
                'automatic':True, 'manual':manual, 'hard_filter':False, 'source_id':'m1',
                'quote':'Нужен опыт от трёх лет.', 'reason':'Опыт из описания', 'original':{}}

    def topic(self, pid='A08'):
        return {'parameter_id':pid, 'source_id':'m1', 'quote':'Сам ведёт сделки в CRM.', 'reason':'Ведение воронки'}

    def init_question(self, topics=('A08',)):
        interview.interview(self.state)['topics'] = {pid:self.topic(pid) for pid in topics}
        interview.append_reply(self.state,'first','Разобрали задачи.',[],studio.PARAMETERS)
        studio.save(self.state)
        return studio.public(self.state)

    def choose(self, state, index=None, defer=False, job='structured-123456'):
        q=state['messages'][-1]
        text='Пока не могу уточнить это требование.' if defer else q['options'][index]
        body={'revision':state['revision'], 'message':text, 'message_id':job, 'sourceIds':[],
              'reply_to':q['id'], 'answer':{'defer':True} if defer else {'option_index':index}}
        return studio.start_turn(self.sid,body),body

    def test_questions_have_specific_skill_and_rubric_task_choices(self):
        state=self.init_question()
        q=state['messages'][-1]
        self.assertEqual(q['question_target']['key'],'A08')
        self.assertEqual(q['options'],interview.TEMPLATES['parameters']['A08'])
        self.assertIn('будущий сотрудник',q['question'])
        self.assertNotIn('скорость',q['question'])
        self.assertNotIn('option_effects',q)

    def test_catalog_scope_excludes_unrelated_management_skills(self):
        self.init_question(('A08','A07'))
        self.assertEqual(set(self.state['interview']['topics']),{'A08','A07'})
        self.assertEqual(interview.next_question(self.state,studio.PARAMETERS)['question_target']['key'],'A07')

    def test_accepted_pending_and_dismissed_skills_are_not_interviewed(self):
        for status in ('accepted','pending','dismissed'):
            with self.subTest(status=status):
                s=copy.deepcopy(self.state)
                interview.interview(s)['topics']={'A08':self.topic()}
                s['proposals']=[{'id':'s','kind':'skill','parameter_id':'A08','status':status}]
                self.assertEqual(interview.next_question(s,studio.PARAMETERS)['question_target']['kind'],'condition')

    def test_task_choice_creates_pending_skill_without_model_and_survives_reload(self):
        state=self.init_question()
        with patch.object(studio.POOL,'submit') as submit, patch.object(studio,'model_call') as model:
            new,body=self.choose(state,1)
            self.assertFalse(submit.called);self.assertFalse(model.called)
        p=new['proposals'][0]
        self.assertEqual((p['parameter_id'],p['level'],p['status']),('A08',3,'pending'))
        self.assertEqual(p['quote'],body['message']);self.assertEqual(p['source_id'],body['message_id'])
        self.assertFalse(p['importance_confirmed'])
        self.assertEqual(studio.public(studio.load(self.sid)),new)
        repeated=studio.start_turn(self.sid,body)
        self.assertEqual(repeated,new)
        self.assertEqual(len(repeated['proposals']),1)
        self.assertEqual(new['messages'][-2]['in_reply_to']['question_target']['key'],'A08')

    def test_answer_is_bound_to_server_issued_option_not_client_effect(self):
        state=self.init_question()
        with self.assertRaises(studio.Problem):
            studio.start_turn(self.sid,{'revision':0,'message':'Подменённый ответ','message_id':'invalid-choice-123','sourceIds':[],
                                       'reply_to':state['active_question_id'],'answer':{'option_index':1}})
        self.assertEqual(studio.load(self.sid)['proposals'],[])

    def test_stale_question_cannot_modify_different_skill(self):
        state=self.init_question(('A08','A07'))
        new,body=self.choose(state,1)
        with self.assertRaises(studio.Problem) as error:
            studio.start_turn(self.sid,{**body,'revision':new['revision'],'message_id':'stale-question-123'})
        self.assertEqual(error.exception.code,'STALE_QUESTION')
        self.assertEqual(len(studio.load(self.sid)['proposals']),1)

    def test_manual_accept_invalidates_question_choice(self):
        state=self.init_question()
        current=studio.load(self.sid)
        current['proposals'].append({'id':'manual','kind':'skill','parameter_id':'A08','status':'accepted'})
        studio.save(current)
        with self.assertRaises(studio.Problem):self.choose(state,1)

    def test_manual_condition_edit_advances_question_without_model(self):
        self.state['proposals']=[self.fact(),{'id':'s','kind':'skill','parameter_id':'A07','status':'accepted'}]
        state=self.init_question(())
        self.assertEqual(state['messages'][-1]['question_target']['key'],'experience')
        with patch.object(studio,'model_call') as model:
            changed=studio.mutate(self.sid,{'revision':0,'action':'edit','id':'fact-experience','value':'5 лет','hard_filter':True})
        self.assertEqual(changed['messages'][-1]['question_target']['key'],'industry_experience')
        self.assertFalse(model.called)

    def test_upgrade_does_not_touch_running_turn(self):
        self.state.pop('interview_version')
        self.state['pending']={'status':'running'}
        studio.save(self.state)
        self.assertEqual(studio.load(self.sid),self.state)

    def test_missing_experience_has_value_question_known_experience_has_obligation_question(self):
        self.state['proposals']=[{'id':'s','kind':'skill','parameter_id':'A07','status':'accepted'}]
        q=interview.next_question(self.state,studio.PARAMETERS)
        self.assertEqual((q['question_target']['key'],q['question_target']['decision']),('experience','value'))
        self.state['proposals'].append(self.fact())
        q=interview.next_question(self.state,studio.PARAMETERS)
        self.assertEqual(q['question_target']['decision'],'requiredness')
        self.assertIn('3 года',q['question'])

    def test_required_and_preferred_are_explicit_decisions_not_missing_boolean(self):
        for index in (0,1):
            with self.subTest(index=index):
                self.state=studio.fresh();self.sid=self.state['id']
                self.state['proposals']=[self.fact(),{'id':'s','kind':'skill','parameter_id':'A07','status':'accepted'}]
                state=self.init_question(())
                new,body=self.choose(state,index)
                fact=new['proposals'][0]
                self.assertEqual(fact['hard_filter'],index==0)
                self.assertEqual(fact['requirement_status'],'required' if index==0 else 'preferred')
                self.assertEqual(fact['requirement_evidence']['source_id'],body['message_id'])
                self.assertTrue(fact['manual'])
                self.assertEqual(new['messages'][-1]['question_target']['key'],'industry_experience')

    def test_no_experience_requirement_is_stored_without_fabricated_years(self):
        self.state['proposals']=[{'id':'s','kind':'skill','parameter_id':'A07','status':'accepted'}]
        state=self.init_question(())
        new,_=self.choose(state,0)
        f=new['vacancy_context']['experience']
        self.assertEqual(f['value'],'Коммерческий опыт не обязателен — готовы обучать.')
        self.assertFalse(f['hard_filter'])
        self.assertEqual(new['messages'][-1]['question_target']['key'],'industry_experience')

    def test_unknown_is_not_false_and_questionnaire_terminates(self):
        state=self.init_question(('A08','A07'))
        keys=[]
        with patch.object(studio.POOL,'submit') as submit:
            for n in range(15):
                q=state['messages'][-1]
                if not q.get('question'):break
                keys.append(interview.question_key(q['question_target']))
                state,_=self.choose(state,defer=True,job=f'question-skip-{n:04d}')
        self.assertEqual(len(keys),7)
        self.assertEqual(len(set(keys)),len(keys))
        self.assertEqual(state['proposals'],[])
        self.assertTrue(state['messages'][-1]['interview_complete'])
        self.assertEqual(len(state['interview_progress']['unresolved']),7)
        self.assertFalse(submit.called)

    def test_old_draft_upgrade_preserves_history_and_decisions_and_is_idempotent(self):
        self.state.pop('interview_version')
        self.state['messages']=[{'id':'legacy','role':'assistant','content':'Старый ответ','question':'Как вы оцениваете скорость?', 'options':[]}]
        self.state['proposals']=[self.fact(manual=True)]
        studio.save(self.state)
        first=studio.load(self.sid)
        self.assertEqual(first['messages'][0],self.state['messages'][0])
        self.assertEqual(first['proposals'],self.state['proposals'])
        self.assertEqual(first['revision'],1)
        self.assertEqual(len(first['messages']),2)
        self.assertIn('question_target',first['messages'][-1])
        self.assertEqual(studio.load(self.sid),first)

    def test_short_free_answer_retains_question_even_after_last_eight_messages(self):
        state=self.init_question()
        with patch.object(studio.POOL,'submit'):
            result=studio.start_turn(self.sid,{'revision':0,'message':'Самостоятельно','message_id':'free-answer-12345','sourceIds':[], 'reply_to':state['active_question_id']})
        studio.LIMIT.release()
        s=studio.load(self.sid)
        s['messages'] += [{'id':f'x{i}','role':'assistant','content':'Другой пункт'} for i in range(9)]
        source=next(x for x in studio.corpus(s) if x['id']=='free-answer-12345')
        self.assertEqual(source['answer_to']['question_target']['key'],'A08')
        self.assertEqual(source['text'],'Самостоятельно')

    def test_topic_without_literal_evidence_is_rejected(self):
        result={'extraction':{'title':'SDR','summary':'','facts':[],'skills':[]},
                'planning':{'skill_topics':[self.topic()], 'conditions':[]},'reply':{'message':'Разобрали.'}}
        with self.assertRaises(ValueError):studio.validate_composition(result,[{'id':'m1','text':'Ищем SDR.'}])

    def test_model_cannot_return_unbound_questions(self):
        result={'extraction':{'title':'SDR','summary':'','facts':[],'skills':[]},
                'planning':{'skill_topics':[], 'conditions':[]},'reply':{'message':'Разобрали.','question':'Как оцениваете скорость?'}}
        with self.assertRaises(studio.ValidationError):studio.validate_composition(result,[])
        result['reply']={'message':'Как вы оцениваете скорость?'}
        with self.assertRaises(ValueError):studio.validate_composition(result,[])

    def test_role_quote_is_not_skill_evidence_even_when_literally_present(self):
        result={'extraction':{'title':'SDR','summary':'','facts':[],'skills':[]},
                'planning':{'skill_topics':[{**self.topic(),'quote':'Ищем SDR.'}], 'conditions':[]},'reply':{'message':'Разобрали.'}}
        with self.assertRaises(ValueError) as error:
            studio.validate_composition(result,[{'id':'m1','text':'Ищем SDR.'}])
        self.assertEqual(studio.validation_failure(error.exception)['code'],'ROLE_WITHOUT_TASK')

    def test_condition_schema_is_empty_until_question_and_scoped_to_asked_key(self):
        result={'extraction':{'title':'SDR','summary':'','facts':[],'skills':[]},
                'planning':{'skill_topics':[], 'conditions':[{'key':'experience','status':'unknown','source_id':'m','quote':'3 года','reason':'Стаж'}]},'reply':{'message':'Разобрали.'}}
        with self.assertRaises(studio.ValidationError):studio.Draft202012Validator(studio.composition_schema(self.state)).validate(result)
        self.state['messages']=[{'id':'m','role':'user','content':'3 года','in_reply_to':{'question_target':{'kind':'condition','key':'experience'}}}]
        studio.Draft202012Validator(studio.composition_schema(self.state)).validate(result)
        result['planning']['conditions'][0]['key']='location'
        with self.assertRaises(studio.ValidationError):studio.Draft202012Validator(studio.composition_schema(self.state)).validate(result)

    def test_requiredness_requires_explicit_evidence_not_plain_years_or_unknown(self):
        for quote,status in [('Три года','required'),('Не знаю','preferred'),('Опыт не обязателен','required')]:
            with self.subTest(quote=quote):
                result={'extraction':{'title':'SDR','summary':'','facts':[],'skills':[]},
                        'planning':{'skill_topics':[], 'conditions':[{'key':'experience','status':status,'source_id':'m','quote':quote,'reason':'Опыт'}]},'reply':{'message':'Разобрали.'}}
                with self.assertRaises(ValueError):studio.validate_composition(result,[{'id':'m','text':quote}])

    def test_free_text_obligation_only_applies_to_asked_condition_and_own_source(self):
        self.state['proposals']=[self.fact()]
        decision={'key':'experience','status':'required','source_id':'m2','quote':'Обязательно три года.','reason':'Уточнение'}
        planning={'skill_topics':[],'conditions':[decision]}
        message={'id':'m2','in_reply_to':{'question_target':{'kind':'condition','key':'experience'}}}
        interview.record_planning(self.state,planning,{**message,'id':'other'})
        self.assertFalse(self.state['proposals'][0]['hard_filter'])
        interview.record_planning(self.state,planning,message)
        self.assertTrue(self.state['proposals'][0]['hard_filter'])
        self.assertEqual(self.state['proposals'][0]['requirement_evidence'],decision)


if __name__=='__main__':unittest.main()
