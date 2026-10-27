import threading
import unittest
from unittest.mock import patch

from candidate_portal import CandidatePortal
from meeting_lab import MeetingLab
from domain import Problem


class CallOperationLocksTest(unittest.TestCase):
    def test_timeout_retains_owner_lock_and_cleans_reference_counts(self):
        for cls, method, guard, entries in (
            (CandidatePortal, 'media_operation', '_media_guard', '_media_locks'),
            (MeetingLab, 'operation', 'operations_lock', 'operations'),
        ):
            with self.subTest(service=cls.__name__):
                obj = cls.__new__(cls)
                setattr(obj, guard, threading.Lock())
                setattr(obj, entries, {})
                with getattr(obj, method)('meeting'):
                    original = getattr(obj, entries)['meeting'][0]
                    with patch('media_runtime.LOCK_WAIT_SECONDS', 0.01), self.assertRaises(Problem) as error:
                        with getattr(obj, method)('meeting'):
                            self.fail('must not acquire held lock')
                    self.assertEqual(error.exception.status, 503)
                    self.assertIs(getattr(obj, entries)['meeting'][0], original)
                    self.assertEqual(getattr(obj, entries)['meeting'][1], 1)
                self.assertEqual(getattr(obj, entries), {})
                with getattr(obj, method)('meeting'):
                    pass
                self.assertEqual(getattr(obj, entries), {})
