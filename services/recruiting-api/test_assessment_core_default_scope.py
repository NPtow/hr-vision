"""Offline configuration contract for automatic assessment enrollment.

No clients, sources, credentials, model calls or runtime threads participate.
"""
import os
import unittest
from unittest.mock import patch

from assessment_core_lifecycle import configured_scope
from domain import Problem


class DefaultScopeTests(unittest.TestCase):
    def scope(self, **environment):
        with patch.dict(os.environ, environment, clear=True):
            return configured_scope()

    def test_enabled_core_defaults_to_every_completed_hr_candidate(self):
        scope = self.scope(HR_ASSESSMENT_CORE_ENABLED='1')
        self.assertIsNotNone(scope)
        self.assertTrue(scope.all_completed_hr)
        self.assertEqual(scope.candidate_ids, ())
        self.assertIsNone(scope.created_since)

    def test_explicit_auto_enable_also_defaults_to_all_when_core_is_enabled(self):
        scope = self.scope(HR_ASSESSMENT_CORE_ENABLED='1', HR_ASSESSMENT_CORE_AUTO_ENABLED='1')
        self.assertTrue(scope.all_completed_hr)

    def test_explicit_automatic_disable_overrides_enabled_core_and_all_scope(self):
        self.assertIsNone(self.scope(HR_ASSESSMENT_CORE_ENABLED='1', HR_ASSESSMENT_CORE_AUTO_ENABLED='0',
                                     HR_ASSESSMENT_CORE_AUTO_ALL_COMPLETED_HR='1'))

    def test_core_not_enabled_does_not_implicitly_activate_automatic_assessment(self):
        self.assertIsNone(self.scope())
        self.assertIsNone(self.scope(HR_ASSESSMENT_CORE_ENABLED='0'))

    def test_explicit_id_scope_is_preserved_instead_of_silently_widened(self):
        scope = self.scope(HR_ASSESSMENT_CORE_ENABLED='1',
                           HR_ASSESSMENT_CORE_AUTO_CANDIDATE_IDS='future-b, future-a, future-b')
        self.assertEqual(scope.candidate_ids, ('future-a', 'future-b'))
        self.assertFalse(scope.all_completed_hr)

    def test_explicit_creation_scope_is_preserved(self):
        scope = self.scope(HR_ASSESSMENT_CORE_ENABLED='1',
                           HR_ASSESSMENT_CORE_AUTO_CREATED_SINCE='2026-10-26T00:00:00Z')
        self.assertEqual(scope.created_since, '2026-10-26T00:00:00Z')
        self.assertFalse(scope.all_completed_hr)

    def test_explicit_all_scope_false_still_requires_an_explicit_restricted_scope(self):
        scope = self.scope(HR_ASSESSMENT_CORE_ENABLED='1', HR_ASSESSMENT_CORE_AUTO_ALL_COMPLETED_HR='0',
                           HR_ASSESSMENT_CORE_AUTO_CANDIDATE_IDS='restricted-one')
        self.assertFalse(scope.all_completed_hr)
        with self.assertRaises(Problem):
            self.scope(HR_ASSESSMENT_CORE_ENABLED='1', HR_ASSESSMENT_CORE_AUTO_ALL_COMPLETED_HR='0')

    def test_explicit_all_scope_can_include_candidates_beyond_old_id_scope(self):
        scope = self.scope(HR_ASSESSMENT_CORE_ENABLED='1', HR_ASSESSMENT_CORE_AUTO_ALL_COMPLETED_HR='1',
                           HR_ASSESSMENT_CORE_AUTO_CANDIDATE_IDS='old-pilot')
        self.assertTrue(scope.all_completed_hr)

    def test_explicit_legacy_enable_without_core_or_scope_remains_configuration_error(self):
        with self.assertRaises(Problem):
            self.scope(HR_ASSESSMENT_CORE_AUTO_ENABLED='1')


if __name__ == '__main__':
    unittest.main()
