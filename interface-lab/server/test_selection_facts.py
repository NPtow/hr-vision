"""Explicit facts regressions: real local SQLite, no provider calls."""
import copy
import json
import unittest

import selection
import selection_facts as facts
from candidate_portal import profile_value
from domain import Problem
import test_employer_store as fixture


class FactsTest(unittest.TestCase):
    def setUp(self):
        self.f = fixture.EmployerStoreTest()
        self.f.setUp()
        self.connect, self.portal = self.f.connect, self.f.portal
        self.cid, self.vid = self.f.person(), self.f.vacancy['id']
        with self.connect() as db:
            selection.init(db)

    def tearDown(self):
        self.f.tearDown()

    def profile(self, db, *, _confirmed=True, **values):
        old = json.loads(db.execute('SELECT profile FROM cp_candidates WHERE id=?', (self.cid,)).fetchone()[0])
        updated = {**old, **values}
        if _confirmed:
            facts.confirm_profile(updated, values)
        db.execute('UPDATE cp_candidates SET profile=? WHERE id=?', (json.dumps(updated), self.cid))

    def vacancy(self, db, **values):
        old = json.loads(db.execute('SELECT body FROM cp_vacancies WHERE id=?', (self.vid,)).fetchone()[0])
        db.execute('UPDATE cp_vacancies SET body=? WHERE id=?', (json.dumps({**old, **values}), self.vid))

    def test_one_bad_candidate_field_preserves_valid_neighbors(self):
        with self.connect() as db:
            self.profile(db, work_locations=['ru:moscow'], employment_types=['invalid'], industry_months={'logistics': 14})
            value = facts.read(db, 'candidate', self.cid)
            self.assertEqual(value['fields']['work_locations'], ['ru:moscow'])
            self.assertEqual(value['fields']['industry_months'], {'logistics': 14})
            self.assertIsNone(value['fields']['employment_types'])

    def test_structured_vacancy_values_are_known_without_inventing_mandatory(self):
        with self.connect() as db:
            self.vacancy(db, city='Москва', details={'employmentType': 'full-time'}, format='Гибрид', description='SDR 3 года')
            value = facts.read(db, 'vacancy', self.vid)
            self.assertEqual(value['fields']['location'], {'required': None, 'allowed_ids': ['ru:moscow']})
            self.assertEqual(value['fields']['employment'], {'required': None, 'allowed_ids': ['full-time']})
            self.assertEqual(value['fields']['industry_experience'], facts.VACANCY['industry_experience'])

    def test_manual_edit_does_not_freeze_other_fields(self):
        with self.connect() as db:
            self.profile(db, work_locations=['ru:moscow'], employment_types=['full-time'])
            before = facts.read(db, 'candidate', self.cid)
            facts.update(db, 'candidate', self.cid, {'revision': before['revision'], 'fields': {**before['fields'], 'industry_months': {'logistics': 20}}})
            self.profile(db, work_locations=['kz:almaty'])
            after = facts.read(db, 'candidate', self.cid)
            self.assertEqual(after['fields']['work_locations'], ['kz:almaty'])
            self.assertEqual(after['fields']['industry_months'], {'logistics': 20})


    def test_form_save_confirms_employment_but_city_and_parser_do_not(self):
        with self.connect() as db:
            self.profile(db, _confirmed=False, employmentType='full-time', city='Москва', workMode='remote')
            before = facts.read(db, 'candidate', self.cid)
            self.assertIsNone(before['fields']['employment_types'])
            self.assertIsNone(before['fields']['work_locations'])
            self.portal.profile(db, self.cid, {'employmentType': 'full-time', 'city': 'Москва'})
            value = facts.read(db, 'candidate', self.cid)
            self.assertEqual(value['fields']['employment_types'], ['full-time'])
            self.assertIsNone(value['fields']['work_locations'])
            self.assertEqual(value['fieldSources']['employment_types']['kind'], 'candidate_questionnaire')
            self.assertEqual(value['fieldSources']['employment_types']['sourceFields'], ['employmentType'])
            self.portal.save_resume(db, self.cid, {'fields': {'employmentType': 'part-time', 'workMode': 'remote'},
                'filename': 'synthetic.txt', 'mime': 'text/plain', 'content': b'test', 'text': 'test'})
            after = facts.read(db, 'candidate', self.cid)
            self.assertIsNone(after['fields']['employment_types'])
            self.assertIsNone(after['fields']['work_locations'])
            self.assertNotEqual(value['revision'], after['revision'])
            self.portal.save_resume(db, self.cid, {'fields': {'employmentType': 'full-time'},
                'filename': 'synthetic.txt', 'mime': 'text/plain', 'content': b'test', 'text': 'test'})
            self.assertIsNone(facts.read(db, 'candidate', self.cid)['fields']['employment_types'])
            self.portal.profile(db, self.cid, {'employmentType': 'full-time'})
            self.assertEqual(facts.read(db, 'candidate', self.cid)['fields']['employment_types'], ['full-time'])

    def test_canonical_fields_saved_by_standard_profile_and_not_by_defaults(self):
        with self.connect() as db:
            self.portal.profile(db, self.cid, {'work_locations': ['kz:astana'], 'employment_types': ['project'],
                                             'industry_months': {'logistics': 0, 'it': 18}})
            value = facts.read(db, 'candidate', self.cid)
            self.assertEqual(value['fields'], {'work_locations': ['kz:astana'], 'employment_types': ['project'],
                                              'industry_months': {'logistics': 0, 'it': 18}})
            self.assertTrue(all(item['kind'] == 'candidate_questionnaire' for item in value['fieldSources'].values()))
            self.assertTrue(all(item['sourceRevision'] for item in value['fieldSources'].values()))
            self.portal.profile(db, self.cid, {'about': 'SDR 3 года в логистике'})
            self.assertEqual(value['fields'], facts.read(db, 'candidate', self.cid)['fields'])
            with self.assertRaises(Problem):
                self.portal.profile(db, self.cid, {facts.PROFILE_SOURCE_KEY: {'employmentType': 'forged'}})

    def test_confirmed_remote_and_geographies_map_only_supported_explicit_values(self):
        with self.connect() as db:
            self.portal.profile(db, self.cid, {'geographies': ['Москва'], 'workMode': 'remote'})
            self.assertEqual(facts.read(db, 'candidate', self.cid)['fields']['work_locations'], ['ru:moscow', 'work-mode:remote'])
            self.portal.profile(db, self.cid, {'geographies': ['Неизвестный город'], 'workMode': 'office'})
            self.assertIsNone(facts.read(db, 'candidate', self.cid)['fields']['work_locations'])

    def test_changed_overridden_source_is_conflict_and_cas_rejects_stale(self):
        with self.connect() as db:
            self.profile(db, employment_types=['full-time'])
            before = facts.read(db, 'candidate', self.cid)
            edited = facts.update(db, 'candidate', self.cid, {'revision': before['revision'],
                'fields': {**before['fields'], 'employment_types': ['project']}})
            self.assertEqual(edited['fieldSources']['employment_types']['kind'], 'manual')
            self.profile(db, employment_types=['part-time'])
            after = facts.read(db, 'candidate', self.cid)
            self.assertIsNone(after['fields']['employment_types'])
            self.assertEqual(after['fieldSources']['employment_types']['state'], 'conflict')
            self.assertEqual(after['conflicts']['employment_types']['manualValue'], ['project'])
            self.assertEqual(after['conflicts']['employment_types']['sourceValue'], ['part-time'])
            with self.assertRaises(Problem) as error:
                facts.update(db, 'candidate', self.cid, {'revision': edited['revision'], 'fields': edited['fields']})
            self.assertEqual(error.exception.status, 409)
            resolved = facts.update(db, 'candidate', self.cid, {'revision': after['revision'],
                'fields': {**after['fields'], 'employment_types': ['internship']}})
            self.assertFalse(resolved['conflicts'])
            self.assertEqual(resolved['fields']['employment_types'], ['internship'])

    def test_explicit_unknown_resolves_only_marked_conflict(self):
        with self.connect() as db:
            self.profile(db, employment_types=['full-time'], work_locations=['ru:moscow'])
            before = facts.read(db, 'candidate', self.cid)
            facts.update(db, 'candidate', self.cid, {'revision': before['revision'], 'fields': {
                **before['fields'], 'employment_types': ['project'], 'work_locations': ['kz:astana']}})
            self.profile(db, employment_types=['part-time'], work_locations=['kz:almaty'])
            conflict = facts.read(db, 'candidate', self.cid)
            self.assertEqual(set(conflict['conflicts']), {'employment_types', 'work_locations'})
            # The projected null alone cannot express a choice to keep unknown.
            unchanged = facts.update(db, 'candidate', self.cid, {'revision': conflict['revision'], 'fields': conflict['fields']})
            self.assertEqual(unchanged['conflicts'], conflict['conflicts'])
            resolved = facts.update(db, 'candidate', self.cid, {'revision': conflict['revision'],
                'fields': conflict['fields'], 'resolveConflicts': ['employment_types']})
            self.assertEqual(set(resolved['conflicts']), {'work_locations'})
            self.assertIsNone(resolved['fields']['employment_types'])
            self.assertEqual(resolved['fieldSources']['employment_types']['kind'], 'manual')
            self.assertNotEqual(resolved['revision'], conflict['revision'])
            # Another edit must not implicitly resolve the untouched location.
            next_save = facts.update(db, 'candidate', self.cid, {'revision': resolved['revision'],
                'fields': {**resolved['fields'], 'industry_months': {'logistics': 6}}})
            self.assertEqual(next_save['conflicts'], resolved['conflicts'])

    def test_resolving_to_null_source_removes_override(self):
        with self.connect() as db:
            self.profile(db, employment_types=['full-time'])
            before = facts.read(db, 'candidate', self.cid)
            edited = facts.update(db, 'candidate', self.cid, {'revision': before['revision'],
                'fields': {**before['fields'], 'employment_types': ['project']}})
            self.profile(db, employment_types=None)
            conflict = facts.read(db, 'candidate', self.cid)
            self.assertIn('employment_types', conflict['conflicts'])
            with self.assertRaises(Problem) as stale:
                facts.update(db, 'candidate', self.cid, {'revision': edited['revision'],
                    'fields': conflict['fields'], 'resolveConflicts': ['employment_types']})
            self.assertEqual(stale.exception.status, 409)
            resolved = facts.update(db, 'candidate', self.cid, {'revision': conflict['revision'],
                'fields': conflict['fields'], 'resolveConflicts': ['employment_types']})
            self.assertFalse(resolved['conflicts'])
            self.assertIsNone(resolved['fields']['employment_types'])
            self.assertEqual(resolved['fieldSources']['employment_types']['kind'], 'candidate_questionnaire')
            # A later explicit answer must flow through after removing the override.
            self.profile(db, employment_types=['internship'])
            fresh = facts.read(db, 'candidate', self.cid)
            self.assertEqual(fresh['fields']['employment_types'], ['internship'])
            self.assertFalse(fresh['conflicts'])

    def test_resolution_accepts_only_current_conflict_paths_and_is_atomic(self):
        with self.connect() as db:
            self.profile(db, employment_types=['full-time'])
            before = facts.read(db, 'candidate', self.cid)
            facts.update(db, 'candidate', self.cid, {'revision': before['revision'],
                'fields': {**before['fields'], 'employment_types': ['project']}})
            self.profile(db, employment_types=['part-time'])
            conflict = facts.read(db, 'candidate', self.cid)
            rows = db.execute('SELECT COUNT(*) FROM sl_fact_revisions').fetchone()[0]
            for invalid in ('employment_types', [None], [{}], ['work_locations'],
                            ['employment_types', 'employment_types'], ['employment.allowed_ids']):
                with self.subTest(resolve=invalid), self.assertRaises(Problem) as error:
                    facts.update(db, 'candidate', self.cid, {'revision': conflict['revision'],
                        'fields': conflict['fields'], 'resolveConflicts': invalid})
                self.assertEqual(error.exception.status, 400)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sl_fact_revisions').fetchone()[0], rows)
            self.assertEqual(facts.read(db, 'candidate', self.cid)['revision'], conflict['revision'])

    def test_resolution_supports_canonical_vacancy_leaf_path(self):
        with self.connect() as db:
            self.vacancy(db, required_fields={'employment': {'required': True}})
            before = facts.read(db, 'vacancy', self.vid)
            fields = copy.deepcopy(before['fields'])
            fields['employment']['required'] = False
            facts.update(db, 'vacancy', self.vid, {'revision': before['revision'], 'fields': fields})
            self.vacancy(db, required_fields={'employment': {'required': None}})
            conflict = facts.read(db, 'vacancy', self.vid)
            resolved = facts.update(db, 'vacancy', self.vid, {'revision': conflict['revision'],
                'fields': conflict['fields'], 'resolveConflicts': ['employment.required']})
            self.assertFalse(resolved['conflicts'])
            self.assertIsNone(resolved['fields']['employment']['required'])
            self.assertEqual(resolved['fieldSources']['employment.required']['kind'], 'vacancy_questionnaire')

    def test_vacancy_leaf_override_never_freezes_value_or_infers_required(self):
        with self.connect() as db:
            self.vacancy(db, details={'employmentType': 'full-time'})
            before = facts.read(db, 'vacancy', self.vid)
            fields = copy.deepcopy(before['fields'])
            fields['employment']['required'] = True
            facts.update(db, 'vacancy', self.vid, {'revision': before['revision'], 'fields': fields})
            self.vacancy(db, details={'employmentType': 'part-time'}, city='Алматы')
            after = facts.read(db, 'vacancy', self.vid)
            self.assertEqual(after['fields']['employment'], {'required': True, 'allowed_ids': ['part-time']})
            self.assertEqual(after['fields']['location'], {'required': None, 'allowed_ids': ['kz:almaty']})
            self.assertFalse(after['conflicts'])
            self.vacancy(db, required_fields={'employment': {'required': False, 'allowed_ids': ['part-time']}})
            conflict = facts.read(db, 'vacancy', self.vid)
            self.assertIsNone(conflict['fields']['employment']['required'])
            self.assertIn('employment.required', conflict['conflicts'])

    def test_bad_vacancy_leaf_preserves_valid_siblings_and_strict_contract(self):
        with self.connect() as db:
            self.vacancy(db, required_fields={'employment': {'required': 'yes', 'allowed_ids': ['part-time']},
                'location': {'required': False, 'allowed_ids': ['invalid']},
                'industry_experience': {'required': True, 'industry_id': 'logistics', 'minimum_months': 12}})
            value = facts.read(db, 'vacancy', self.vid)
            self.assertEqual(value['fields']['employment'], {'required': None, 'allowed_ids': ['part-time']})
            self.assertEqual(value['fields']['location'], {'required': False, 'allowed_ids': None})
            self.assertEqual(value['fields']['industry_experience']['minimum_months'], 12)
            self.assertEqual(set(facts.contract(db, 'vacancy', self.vid)), set(facts.VACANCY) | {'revision'})
            self.assertEqual(set(facts.contract(db, 'candidate', self.cid)), set(facts.CANDIDATE) | {'revision', 'candidate_id'})

    def test_unmarked_legacy_canonical_fields_are_not_confirmation(self):
        with self.connect() as db:
            self.profile(db, _confirmed=False, work_locations=['ru:moscow'], employment_types=['full-time'],
                         industry_months={'logistics': 25})
            value = facts.read(db, 'candidate', self.cid)
            self.assertEqual(value['fields'], facts.CANDIDATE)
            self.assertTrue(all(item['kind'] == 'unconfirmed_legacy_source' for item in value['fieldSources'].values()))

    def test_latest_explicit_save_wins_and_canonical_input_breaks_same_save_tie(self):
        with self.connect() as db:
            self.portal.profile(db, self.cid, {'employment_types': ['project'], 'employmentType': 'full-time'})
            self.assertEqual(facts.read(db, 'candidate', self.cid)['fields']['employment_types'], ['project'])
            self.portal.profile(db, self.cid, {'employmentType': 'part-time'})
            self.assertEqual(facts.read(db, 'candidate', self.cid)['fields']['employment_types'], ['part-time'])
            self.portal.profile(db, self.cid, {'employment_types': ['internship']})
            self.assertEqual(facts.read(db, 'candidate', self.cid)['fields']['employment_types'], ['internship'])

    def test_empty_industry_map_is_unknown_but_explicit_zero_is_retained(self):
        with self.connect() as db:
            self.portal.profile(db, self.cid, {'industry_months': {}})
            value = facts.read(db, 'candidate', self.cid)
            self.assertIsNone(value['fields']['industry_months'])
            self.assertEqual(value['fieldSources']['industry_months']['state'], 'unknown')
            self.portal.profile(db, self.cid, {'industry_months': {'logistics': 0}})
            self.assertEqual(facts.contract(db, 'candidate', self.cid)['industry_months'], {'logistics': 0})


if __name__ == '__main__':
    unittest.main()
