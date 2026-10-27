"""Manager-only runtime diagnostics; no source, model or delivery requests."""
import unittest

from domain import Problem
import selection
import test_employer_store as fixture


class Runtime:
    def __init__(self, **value):
        self.value, self.reads = value, 0

    def status(self):
        self.reads += 1
        return dict(self.value)


class RuntimeStatusTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.EmployerStoreTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.routes = selection.SelectionRoutes(self.fixture.connect,
            self.fixture.portal.lock, self.fixture.portal)

    def read(self, role='manager', method='GET'):
        return self.routes.handle(method, selection.PREFIX + 'status', {}, None, lambda db: role)

    def test_disabled_runtimes_are_explicit_and_response_is_not_cached(self):
        result, headers = self.read()
        self.assertEqual(result['materials'], {'enabled': False, 'running': False})
        self.assertEqual(result['assessment'], {'enabled': False, 'running': False})
        self.assertTrue(result['ok'])
        self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_authorization_and_method_precede_runtime_inspection(self):
        runtime = Runtime(enabled=True, running=True)
        self.fixture.portal.assessment_lifecycle = runtime
        for role in ('candidate', 'recruiter', 'employer'):
            with self.assertRaises(Problem) as caught:
                self.read(role)
            self.assertEqual(caught.exception.status, 403)
        with self.assertRaises(Problem) as caught:
            self.read(method='POST')
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(runtime.reads, 0)

    def test_running_thread_without_recent_progress_is_not_healthy(self):
        runtime = Runtime(enabled=True, running=True, healthy=False, error=None)
        self.fixture.portal.assessment_lifecycle = runtime
        self.assertFalse(self.read()[0]['ok'])
        runtime.value['healthy'] = True
        self.assertTrue(self.read()[0]['ok'])
        runtime.value['error'] = 'automatic_assessment_reconcile_failed'
        self.assertFalse(self.read()[0]['ok'])


if __name__ == '__main__':
    unittest.main()
