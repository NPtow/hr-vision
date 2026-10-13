import json
import unittest
from domain import Problem
from hh_outreach_routes import HHOutreachRoutes
import test_hh_outreach as fixture

class OutreachRoutesTest(unittest.TestCase):
    connect = fixture.OutreachTest.connect
    issue = staticmethod(fixture.OutreachTest.issue)
    current = staticmethod(fixture.OutreachTest.current)
    record = staticmethod(fixture.OutreachTest.record)
    new_engine = fixture.OutreachTest.new_engine
    campaign = fixture.OutreachTest.campaign
    ready = fixture.OutreachTest.ready
    tearDown = fixture.OutreachTest.tearDown
    def setUp(self):
        fixture.OutreachTest.setUp(self)
        self.routes = HHOutreachRoutes(self.engine, worker_secret='test-worker-secret')

    def http(self, action, body=None, *, actor='manager', headers=None, worker=False):
        return self.routes.handle('GET' if body is None else 'POST',
            ('/api/hr/outreach-worker/' if worker else '/api/hr/outreach/') + action,
            headers or {}, body, lambda db: actor)

    def test_scope_auth_and_no_token_in_operator_projection(self):
        self.assertTrue(self.routes.handles('/api/hr/outreach/campaigns'))
        self.assertTrue(self.routes.handles('/api/hr/outreach-worker/claim'))
        self.assertFalse(self.routes.handles('/api/hr/booking/state'))
        for actor in ('candidate', 'recruiter', None):
            with self.subTest(actor=actor), self.assertRaises(Problem):
                self.http('campaigns', actor=actor)
        with self.assertRaises(Problem):
            self.http('claim', {'accountId': 'account1', 'workerId': 'worker'}, worker=True)
        cid = self.ready()
        body, headers = self.http('claim', {'accountId': 'account1', 'workerId': 'worker'}, worker=True,
            headers={'Authorization': 'Bearer test-worker-secret'}, actor=None)
        self.assertIsNotNone(body['job'])
        self.assertEqual('no-store', headers['Cache-Control'])
        self.assertEqual('no-referrer', headers['Referrer-Policy'])
        public = json.dumps(self.http('campaigns/' + cid)[0])
        self.assertNotIn(body['job']['bookingUrl'], public)
        self.assertNotIn(body['job']['claimToken'], public)

    def test_lifecycle_and_cross_site(self):
        cid = self.ready()
        self.assertEqual('paused', self.http('campaigns/' + cid + '/pause', {})[0]['campaign']['status'])
        self.assertEqual('running', self.http('campaigns/' + cid + '/resume', {})[0]['campaign']['status'])
        self.assertEqual(1, len(self.http('campaigns')[0]['campaigns']))
        with self.assertRaises(Problem):
            self.http('campaigns/' + cid + '/pause', {}, headers={'Sec-Fetch-Site': 'cross-site'})
        with self.assertRaises(Problem):
            self.http('campaigns?token=secret')
        with self.assertRaises(Problem):
            self.http('jobs/reconcile', {'jobId': 'job'})

    def test_unconfigured_worker_disabled(self):
        self.routes = HHOutreachRoutes(self.engine)
        with self.assertRaises(Problem):
            self.http('claim', {}, worker=True, headers={'Authorization': 'Bearer '})

if __name__ == '__main__':
    unittest.main()
