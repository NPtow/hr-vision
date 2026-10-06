import unittest
from domain import fresh, transition, public_state, free_slots, Problem


class JourneyTest(unittest.TestCase):
    def setUp(self):
        self.s = fresh()

    def do(self, actor, action, cid='anna', **values):
        self.s = transition(self.s, actor, dict(generation=self.s['generation'], action=action, candidate=cid, **values))

    def accepted(self, cid='anna'):
        self.do(cid, 'interest', cid, value='accepted')

    def meeting(self, cid='anna'):
        self.accepted(cid)
        slot = free_slots(self.s, cid)[0]
        self.do('manager', 'meeting.propose', cid, start=slot)
        return self.s['candidates'][cid]['meeting']['id']

    def reviewed(self, cid='anna'):
        mid = self.meeting(cid)
        self.do(cid, 'meeting.confirm', cid, meetingId=mid)
        self.do('manager', 'meeting.skip', cid, meetingId=mid)
        self.do('manager', 'feedback.questions', cid, meetingId=mid, questions='Как вы возвращали ушедшего клиента?')
        q = self.s['candidates'][cid]['meeting']['questionnaire']
        self.do('manager', 'feedback', cid, meetingId=mid, questionnaireId=q['id'], answers={v['id']: {'rating': 'clear', 'comment': 'Пример'} for v in q['questions']})
        self.do(cid, 'after', cid, value='yes')
        self.do('manager', 'decision', cid, value='pool')
        self.do('manager', 'offer.save', cid, role='Менеджер', compensation='150000', format='Гибрид', expectations='Результат', startDate='2027-01-20')

    def test_hidden_until_candidate_accepts(self):
        self.assertEqual(public_state(self.s, 'manager')['candidates'], {})
        self.accepted()
        self.assertEqual(list(public_state(self.s, 'manager')['candidates']), ['anna'])

    def test_candidate_cannot_mutate_another_candidate(self):
        with self.assertRaises(Problem): self.do('anna','interest','mikhail',value='accepted')

    def test_manager_cannot_accept_for_candidate(self):
        self.accepted()
        with self.assertRaises(Problem): self.do('manager','interest',value='accepted')

    def test_auto_does_not_confirm_meeting(self):
        self.do('anna','auto',enabled=True)
        self.do('manager','meeting.propose',start=free_slots(self.s,'anna')[0])
        self.assertEqual(self.s['candidates']['anna']['meeting']['status'],'pending')

    def test_reserved_slot_excluded_and_manager_collision_prevented(self):
        mid = self.meeting()
        slot = self.s['candidates']['anna']['meeting']['start']
        self.accepted('mikhail')
        self.assertNotIn(slot, free_slots(self.s, 'anna'))
        self.assertNotIn(slot, free_slots(self.s, 'mikhail'))
        with self.assertRaises(Problem): self.do('manager','meeting.propose','mikhail',start=slot)
        self.do('manager','meeting.cancel',meetingId=mid)
        self.assertIn(slot,free_slots(self.s,'mikhail'))

    def test_other_meeting_survives_confirmation_cancel(self):
        first = self.meeting()
        second = self.meeting('mikhail')
        self.do('anna','meeting.confirm',meetingId=first)
        self.do('mikhail','meeting.cancel','mikhail',meetingId=second)
        self.assertEqual(self.s['candidates']['anna']['meeting']['status'],'confirmed')

    def test_old_confirmation_cannot_confirm_rescheduled_meeting(self):
        old = self.meeting()
        self.do('manager','meeting.propose',start=free_slots(self.s,'anna')[0])
        with self.assertRaises(Problem): self.do('anna','meeting.confirm',meetingId=old)

    def test_busy_time_not_available(self):
        self.assertNotIn(self.s['candidates']['anna']['busy'][0],free_slots(self.s,'anna'))

    def test_feedback_cannot_precede_meeting(self):
        self.accepted()
        with self.assertRaises(Problem): self.do('manager','feedback',example='A',doubts='B',check='C')

    def test_feedback_private_to_manager(self):
        self.reviewed()
        self.assertNotIn('feedback',public_state(self.s,'anna')['candidates']['anna'])
        self.assertIn('feedback',public_state(self.s,'manager')['candidates']['anna'])

    def test_draft_offer_hidden_from_candidate(self):
        self.reviewed()
        self.assertIsNone(public_state(self.s,'anna')['candidates']['anna']['offer'])

    def test_offers_sequential_and_acceptance_closes_hiring(self):
        self.reviewed(); self.reviewed('mikhail')
        self.do('manager','offer.send')
        with self.assertRaises(Problem): self.do('manager','offer.send','mikhail')
        self.do('anna','offer.reply',value='declined',version=1)
        self.do('manager','offer.send','mikhail')
        self.do('mikhail','offer.reply','mikhail',value='accepted',version=1)
        self.assertEqual(self.s['closedBy'],'mikhail')
        with self.assertRaises(Problem): self.do('elena','interest','elena',value='accepted')

    def test_cannot_accept_stale_terms(self):
        self.reviewed(); self.do('manager','offer.send')
        self.do('manager','offer.save',role='Менеджер',compensation='180000',format='Гибрид',expectations='Результат',startDate='2027-01-20')
        with self.assertRaises(Problem): self.do('anna','offer.reply',value='accepted',version=1)

    def test_reset_invalidates_stale_commands(self):
        old = self.s['generation']; self.do('manager','reset')
        with self.assertRaises(Problem): transition(self.s,'anna',dict(action='interest',candidate='anna',value='accepted',generation=old))

    def test_chat_is_not_shared_between_candidates(self):
        self.accepted(); self.accepted('mikhail')
        self.do('manager','chat',text='Условия для Анны')
        other = public_state(self.s,'mikhail')
        self.assertEqual(list(other['candidates']),['mikhail'])
        self.assertEqual(other['candidates']['mikhail']['messages'],[])

if __name__ == '__main__': unittest.main()
