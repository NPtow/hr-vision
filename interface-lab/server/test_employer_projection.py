"""Read-only screen adapter checks composed with the canonical store fixture."""
import datetime as dt
import json
import unittest

from domain import iso, now
import employer_projection as projection
import employer_store as store
import employer_workflow as workflow
import test_employer_store as fixture


class EmployerProjectionTest(unittest.TestCase):
    # Reuse real CandidatePortal/store schema setup, without inheriting and
    # rerunning the store's own 22 mutation/ownership test methods.
    setUp = fixture.EmployerStoreTest.setUp
    tearDown = fixture.EmployerStoreTest.tearDown
    connect = fixture.EmployerStoreTest.connect
    person = fixture.EmployerStoreTest.person
    batch = staticmethod(fixture.EmployerStoreTest.batch)
    import_people = fixture.EmployerStoreTest.import_people
    accepted = fixture.EmployerStoreTest.accepted

    def state(self, workspace='a', vacancy_id=None):
        with self.connect() as db:
            return projection.build_state(db, {'workspaceId': workspace, 'name': 'Проверяющий', 'team': False},
                                          self.portal, self.portal.media, vacancy_id)

    def meeting(self, db, aid, mid, start, status='completed', workspace='a', revision=1):
        db.execute('''INSERT INTO cp_meetings
          (id,application_id,start,end,status,room,confirmed_at,completed_at,manager_id,revision,created_at)
          VALUES (?,?,?,?,?,NULL,?,?,?,?,?)''',
          (mid, aid, iso(start), iso(start + dt.timedelta(minutes=30)), status,
           iso(start), iso(now()) if status == 'completed' else None,
           'workspace:' + workspace, revision, iso(now())))

    def other_application(self, db, cid, workspace='a', label='Вторая'):
        vacancy = store.create_vacancy(db, workspace, {'title': label, 'city': 'Москва', 'remote': False})
        batch = store.import_batch(db, workspace, vacancy['id'], self.batch([cid], external=label), self.portal)
        aid = batch['applicationIds'][0]
        self.portal.decide_application(db, cid, {'applicationId': aid, 'decision': 'accepted'})
        return aid, vacancy['id']

    def pool(self, db, aid, mid):
        result = workflow.manager_action(db, aid, 'feedback.questions',
            {'meetingId': mid, 'questions': 'Как вы измеряли результат своей работы?'}, manager_id='reviewer')
        q = result['state']['meeting']['questionnaire']
        workflow.manager_action(db, aid, 'feedback.submit', {'meetingId': mid, 'questionnaireId': q['id'],
            'answers': {item['id']: {'rating': 'clear'} for item in q['questions']}}, manager_id='reviewer')
        workflow.manager_action(db, aid, 'decision', {'value': 'pool'}, manager_id='reviewer')

    def test_real_cv_internal_summary_and_profile_map_without_demo_people(self):
        cid = self.person()
        with self.connect() as db:
            body = json.loads(db.execute('SELECT profile FROM cp_candidates WHERE id=?', (cid,)).fetchone()[0])
            body.update(name='Алёна Тестовая', about='Редактируемая справка кандидата',
                        desiredRole='Инженер по качеству', salaryMin=120000, salaryMax=150000)
            db.execute('UPDATE cp_candidates SET profile=? WHERE id=?', (json.dumps(body), cid))
            db.execute('UPDATE cp_internal_profiles SET body=? WHERE candidate_id=?',
                (json.dumps({'summary': 'Рекрутер подтвердил опыт контроля качества.',
                             'evaluation': 'Есть примеры работы с производственными дефектами.'}), cid))
            db.execute('''INSERT INTO cp_resumes
              (candidate_id,filename,mime,content,text_content,extracted,uploaded_at) VALUES (?,?,?,?,?,?,?)''',
              (cid, 'quality-test.txt', 'text/plain', b'Synthetic CV', 'Резюме: Алёна. Семь лет контроля качества.', '{}', iso(now())))
        aid = self.accepted([cid])[0]
        state = self.state(vacancy_id=self.vacancy['id'])
        person = state['candidates'][aid]
        profile = person['sourceProfile']
        self.assertEqual((person['id'], person['candidateId']), (aid, cid))
        self.assertEqual(profile['name'], 'Алёна Тестовая')
        self.assertEqual(profile['cv'], 'Резюме: Алёна. Семь лет контроля качества.')
        self.assertEqual(profile['shortSummary'], 'Рекрутер подтвердил опыт контроля качества.')
        self.assertEqual(person['summary'], 'Редактируемая справка кандидата')
        self.assertEqual(profile['conclusion'], ['Есть примеры работы с производственными дефектами.'])
        self.assertEqual(profile['interviewId'], 'interview-' + cid)
        self.assertIsNone(profile['video'])
        self.assertEqual(profile['shortChapters'], [])
        self.assertNotIn('must-not-leak', json.dumps(state))
        for invented in ('Анна Миронова', 'Михаил Белов', 'Елена Орлова', 'Сфера'):
            self.assertNotIn(invented, json.dumps(state, ensure_ascii=False))
        # The existing screen contract remains JSON-ready and uses stable scalar
        # fields rather than raw SQLite rows/objects or fabricated measurements.
        json.loads(json.dumps(state, allow_nan=False))
        self.assertIsInstance(state['revision'], int)
        self.assertIsInstance(state['generation'], str)
        self.assertTrue(all(isinstance(profile[k], str) for k in
            ('id', 'name', 'initials', 'role', 'interviewRole', 'interviewDate', 'shortSummary', 'cv')))
        self.assertTrue(all(isinstance(field['label'], str) and isinstance(field['value'], str)
                            for field in profile['profile']))

    def test_empty_workspace_stays_empty_without_sfera_fallback(self):
        self.accepted([self.person()])
        state = self.state(workspace='b')
        self.assertEqual(state['vacancies'], [])
        self.assertEqual(state['candidates'], {})
        self.assertEqual((state['meetings'], state['offerCandidates'], state['pendingFeedback']), ([], [], []))
        self.assertEqual(state['vacancy']['company'], '')
        self.assertEqual(state['vacancy']['role'], '')
        self.assertEqual(state['vacancy']['proposedCount'], 0)

    def test_approval_consent_and_rejection_control_projection_visibility(self):
        approved, waiting, rejected = self.person(), self.person(), self.person()
        result = self.import_people([approved, waiting, rejected])
        aids = result['applicationIds']
        with self.connect() as db:
            for aid, cid in ((aids[0], approved), (aids[2], rejected)):
                self.portal.decide_application(db, cid, {'applicationId': aid, 'decision': 'accepted'})
            store.reject(db, 'a', aids[2])
        state = self.state(vacancy_id=self.vacancy['id'])
        self.assertEqual(set(state['candidates']), {aids[0]})
        self.assertEqual(state['vacancy']['proposedCount'], 1)
        with self.connect() as db:
            profile = json.loads(db.execute('SELECT profile FROM cp_candidates WHERE id=?', (approved,)).fetchone()[0])
            profile.update(consentReuse=False, autoApprove=False)
            db.execute('UPDATE cp_candidates SET profile=? WHERE id=?', (json.dumps(profile), approved))
        state = self.state(vacancy_id=self.vacancy['id'])
        self.assertEqual(state['candidates'], {})
        self.assertEqual(state['vacancy']['proposedCount'], 0)

    def test_free_slots_remove_own_workspace_and_candidate_global_conflicts_only(self):
        candidate, coworker, foreign_person = self.person(), self.person(), self.person()
        aid, own_other = self.accepted([candidate, coworker])
        start = (now() + dt.timedelta(days=3)).replace(hour=13, minute=0, second=0, microsecond=0)
        with self.connect() as db:
            profile = json.loads(db.execute('SELECT profile FROM cp_candidates WHERE id=?', (candidate,)).fetchone()[0])
            profile['availability'] = [{'start': iso(start), 'end': iso(start + dt.timedelta(hours=3))}]
            db.execute('UPDATE cp_candidates SET profile=? WHERE id=?', (json.dumps(profile), candidate))
            foreign_same, _ = self.other_application(db, candidate, workspace='b', label='Другой работодатель')
            foreign_other, _ = self.other_application(db, foreign_person, workspace='b', label='Чужая встреча')
            self.meeting(db, own_other, 'own-busy', start, 'confirmed', workspace='a')
            self.meeting(db, foreign_same, 'candidate-busy', start + dt.timedelta(minutes=30), 'pending', workspace='b')
            self.meeting(db, foreign_other, 'foreign-not-busy', start + dt.timedelta(minutes=60), 'confirmed', workspace='b')
        person = self.state(vacancy_id=self.vacancy['id'])['candidates'][aid]
        self.assertEqual(person['freeSlots'], [iso(start + dt.timedelta(minutes=n)) for n in (60, 90, 120, 150)])
        self.assertEqual(person['slots'], [])
        self.assertEqual(person['busy'], [])

    def test_global_offer_pool_includes_other_vacancies_before_any_offer_exists(self):
        one, two = self.person(), self.person()
        aid = self.accepted([one])[0]
        with self.connect() as db:
            other_aid, other_vid = self.other_application(db, two)
            for current, mid in ((aid, 'pool-one'), (other_aid, 'pool-two')):
                self.meeting(db, current, mid, now() - dt.timedelta(hours=2))
                store.mark_selected(db, 'a', current, mid)
                self.pool(db, current, mid)
        state = self.state(vacancy_id=self.vacancy['id'])
        self.assertEqual(set(state['candidates']), {aid})
        self.assertEqual({p['id'] for p in state['offerCandidates']}, {aid, other_aid})
        self.assertTrue(all(p['offer'] is None and p['decision'] == 'pool' for p in state['offerCandidates']))
        self.assertEqual({p['vacancyId'] for p in state['offerCandidates']}, {self.vacancy['id'], other_vid})

    def test_past_meetings_leave_global_list_but_keep_profile_and_exact_history(self):
        one, two = self.person(), self.person()
        first, second = self.accepted([one, two])
        with self.connect() as db:
            self.meeting(db, first, 'past-first', now() - dt.timedelta(days=2))
            self.meeting(db, first, 'past-second', now() - dt.timedelta(hours=2), revision=2)
            self.meeting(db, second, 'expired-confirmed', now() - dt.timedelta(hours=1), 'confirmed')
            store.mark_selected(db, 'a', first, 'past-second')
            store.mark_selected(db, 'a', second, 'expired-confirmed')
        state = self.state(vacancy_id=self.vacancy['id'])
        self.assertEqual(state['meetings'], [])
        self.assertEqual(state['candidates'][first]['meeting']['id'], 'past-second')
        self.assertEqual(state['candidates'][first]['meeting']['status'], 'completed')
        self.assertEqual([p['id'] for p in state['pendingFeedback']], [first])
        self.assertNotIn('room', state['candidates'][first]['meeting'])
        with self.connect() as db:
            history = self.portal.application(db, first)['meetings']
            self.assertEqual([m['id'] for m in history], ['past-second', 'past-first'])
            self.assertTrue(all(m['status'] == 'completed' for m in history))
            self.assertEqual(self.portal.interview(db, one)['id'], 'interview-' + one)


if __name__ == '__main__':
    unittest.main()
