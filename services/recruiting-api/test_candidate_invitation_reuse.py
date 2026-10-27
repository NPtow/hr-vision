"""Real endpoint/HH adapter contract, isolated DB and synthetic provider only."""
import unittest
from urllib.parse import parse_qs, urlsplit

import candidate_invites as invites
from domain import Problem
import test_hh_routes as hh_fixture


class InvitationReuseTest(unittest.TestCase):
    tearDown = hh_fixture.HHRouteTest.tearDown
    connect = hh_fixture.HHRouteTest.connect
    call = hh_fixture.HHRouteTest.call
    not_manager = staticmethod(hh_fixture.HHRouteTest.not_manager)
    slot = hh_fixture.HHRouteTest.slot
    ready = hh_fixture.HHRouteTest.ready
    import_cv = hh_fixture.HHRouteTest.import_cv
    rule = hh_fixture.HHRouteTest.rule
    book = hh_fixture.HHRouteTest.book
    http = hh_fixture.HHRouteTest.http
    send_fixture = hh_fixture.HHRouteTest.send_fixture

    def setUp(self):
        hh_fixture.HHRouteTest.setUp(self)
        self.portal.invitation_lookup = self.routes.existing_invitation

    def sent(self):
        connection, cid = self.ready()
        self.rule(connection)
        mid = self.book(cid)
        self.send_fixture()
        self.assertEqual(self.routes.process_pending()['processed'], 1)
        with self.connect() as db:
            iid, url = db.execute('SELECT invitation_id,invitation_url FROM hh_outbox').fetchone()
        token = parse_qs(urlsplit(url).fragment)['invite'][0]
        return cid, mid, iid, url, token

    def test_copy_reuses_sent_link_then_explicit_replace_revokes(self):
        cid, mid, iid, url, token = self.sent()
        original_calls = len(self.provider.calls)
        for _ in range(2):
            result, _ = self.call('recruiter/invitations', {'candidateId': cid, 'scope': 'call'}, manager=True)
            self.assertEqual(result['url'], url)
            self.assertEqual(result['invitationId'], iid)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM cp_invitations').fetchone()[0], 1)
            self.assertEqual(invites.resolve(db, token, 'call')['interviewId'], mid)
        replacement, _ = self.call('recruiter/invitations', {'candidateId': cid, 'scope': 'call', 'replace': True}, manager=True)
        self.assertNotEqual(replacement['url'], url)
        with self.connect() as db:
            with self.assertRaises(Problem) as caught:
                invites.resolve(db, token, 'call')
            self.assertEqual(caught.exception.status, 410)
        self.assertEqual(len(self.provider.calls), original_calls, 'Copy/replacement never sends an HH message.')

    def test_expired_sent_link_is_not_reused(self):
        cid, _, iid, url, _ = self.sent()
        with self.connect() as db:
            db.execute('UPDATE cp_invitations SET expires=0 WHERE id=?', (iid,))
        result, _ = self.call('recruiter/invitations', {'candidateId': cid, 'scope': 'call'}, manager=True)
        self.assertNotEqual(result['url'], url)
        self.assertNotEqual(result['invitationId'], iid)

    def test_completed_call_cannot_be_reissued_or_cross_scope_reused(self):
        cid, mid, _, url, _ = self.sent()
        with self.connect() as db:
            db.execute("UPDATE cp_interviews SET status='completed' WHERE id=?", (mid,))
        with self.assertRaises(Problem) as caught:
            self.call('recruiter/invitations', {'candidateId': cid, 'scope': 'call'}, manager=True)
        self.assertEqual(caught.exception.status, 409)
        result, _ = self.call('recruiter/invitations', {'candidateId': cid, 'scope': 'claim'}, manager=True)
        self.assertNotEqual(result['url'], url)
        self.assertIn('claim=', urlsplit(result['url']).fragment)

    def test_replace_requires_boolean_and_recruiter_auth(self):
        cid, _, _, _, _ = self.sent()
        for body, manager, status in [({'candidateId': cid, 'scope': 'call', 'replace': 'true'}, True, 400),
                                       ({'candidateId': cid, 'scope': 'call'}, False, 401)]:
            with self.assertRaises(Problem) as caught:
                self.call('recruiter/invitations', body, manager=manager)
            self.assertEqual(caught.exception.status, status)


if __name__ == '__main__':
    unittest.main()
