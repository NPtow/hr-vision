import unittest
from domain import fresh, transition, free_slots, public_state, Problem
from feedback import questionnaire, vtt_turns
VTT = '''WEBVTT

00:00:01.000 --> 00:00:05.000
<v>Иван Петров:</v>Как вы вернули ушедшего клиента?

00:00:06.000 --> 00:00:09.000
<v>Анна Миронова:</v>Как нам лучше начать?

00:00:10.000 --> 00:00:15.000
<v Иван Петров>Расскажите о личном результате.</v>

00:00:20.000 --> 00:00:23.000
<v Иван Петров>Вы меня слышите?</v>
'''
class FeedbackTest(unittest.TestCase):
    def setUp(self):
        self.s = fresh()
        self.do('anna','interest',value='accepted')
        self.do('manager','meeting.propose',start=free_slots(self.s,'anna')[0])
        self.m = self.s['candidates']['anna']['meeting']
        self.do('anna','meeting.confirm',meetingId=self.m['id'])
        self.m['room']={'name':'isolated'}
        self.do('manager','meeting.joined',meetingId=self.m['id'])
    def do(self, actor, action, **values):
        self.s = transition(self.s, actor, dict(generation=self.s['generation'],action=action,candidate='anna',**values))
    def finish(self):
        self.do('manager','meeting.transcript',meetingId=self.m['id'],turns=[{'id':'one','text':'Как вы вернули клиента?','seconds':12}])
        self.do('manager','meeting.finish',meetingId=self.m['id'])
        return self.m['questionnaire']
    def test_actual_speaker_both_vtt_formats_and_no_logistics(self):
        q = questionnaire(vtt_turns(VTT,'Иван Петров'),'meeting')
        self.assertEqual(len(q['questions']),2)
        self.assertEqual(q['questions'][0]['question'],'Как вы вернули ушедшего клиента?')
        self.assertEqual(q['questions'][1]['seconds'],10)
        self.assertEqual(q['questions'][0]['source'],'transcript')
    def test_no_speech_never_fabricates_questions(self):
        self.assertIsNone(questionnaire([],'meeting'))
        self.assertIsNone(questionnaire([{'text':'Спасибо за беседу.','seconds':0}],'meeting'))
        self.assertIsNone(questionnaire([{'text':'Как вы возвращали клиентов которые','seconds':0}],'meeting'))
        self.assertEqual(vtt_turns('WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nКак вы работаете?','Иван Петров'),[])
    def test_fragmented_question_and_duplicate(self):
        turns=[{'text':'Как вы','seconds':1},{'text':'вернули клиента?','seconds':3},{'text':'Как вы вернули клиента?','seconds':9}]
        self.assertEqual(len(questionnaire(turns,'m')['questions']),1)
    def test_each_interview_has_own_questions(self):
        a=questionnaire([{'text':'Как вы вернули клиента?','seconds':1}],'m')
        b=questionnaire([{'text':'Как вы планируете продажи?','seconds':1}],'m')
        self.assertNotEqual(a['id'],b['id'])
    def test_capture_manager_only_and_current_meeting(self):
        values=dict(meetingId=self.m['id'],turns=[{'id':'x','text':'Как вы работали?','seconds':1}])
        with self.assertRaises(Problem): self.do('anna','meeting.transcript',**values)
        with self.assertRaises(Problem): self.do('manager','meeting.transcript',**{**values,'meetingId':'stale'})
    def test_live_feedback_and_manual_questions_rejected(self):
        with self.assertRaises(Problem): self.do('manager','feedback.questions',meetingId=self.m['id'],questions='Что вы делали?')
        with self.assertRaises(Problem): self.do('manager','feedback',meetingId=self.m['id'])
    def test_finish_freezes_questions_draft_private(self):
        q=self.finish(); answers={q['questions'][0]['id']:{'rating':'unclear','comment':'Уточнить вклад'}}
        self.do('manager','feedback.draft',meetingId=self.m['id'],questionnaireId=q['id'],answers=answers)
        manager=public_state(self.s,'manager')['candidates']['anna']['meeting']
        candidate=public_state(self.s,'anna')['candidates']['anna']['meeting']
        self.assertEqual(manager['feedbackDraft'],answers)
        self.assertNotIn('questionnaire',candidate); self.assertNotIn('feedbackDraft',candidate); self.assertNotIn('transcriptTurns',manager)
        with self.assertRaises(Problem): self.do('manager','feedback.questions',meetingId=self.m['id'],questions='Другие вопросы?')
    def test_complete_current_questionnaire_required(self):
        q=self.finish()
        for values in [dict(meetingId='old',questionnaireId=q['id'],answers={}),dict(meetingId=self.m['id'],questionnaireId='old',answers={}),dict(meetingId=self.m['id'],questionnaireId=q['id'],answers={})]:
            with self.assertRaises(Problem): self.do('manager','feedback',**values)
        answers={q['questions'][0]['id']:{'rating':'not_asked','comment':''}}
        self.do('manager','feedback',meetingId=self.m['id'],questionnaireId=q['id'],answers=answers)
        self.assertEqual(self.s['candidates']['anna']['feedback']['questions'],q['questions'])
        self.assertNotIn('feedback',public_state(self.s,'anna')['candidates']['anna'])
    def test_legacy_saved_feedback_is_preserved(self):
        self.finish(); prior={'example':'Старый ответ','doubts':'Нет','check':'Рекомендации','at':'2026-10-05'}
        self.s['candidates']['anna']['feedback']=prior.copy()
        self.assertEqual(public_state(self.s,'manager')['candidates']['anna']['feedback'],prior)
if __name__=='__main__': unittest.main()
