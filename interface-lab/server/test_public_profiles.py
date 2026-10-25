"""Capability boundary using the real candidate/session/schema implementation."""
import hashlib
import json
import unittest
from urllib.parse import urlsplit, parse_qs
from unittest.mock import patch

from domain import Problem
from public_profiles import PublicProfiles, PUBLIC, OWNER
import test_candidate_portal as fixture


class PublicProfileTest(unittest.TestCase):
    def setUp(self):
        self.flow = fixture.CandidatePortalTest()
        self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)
        self.addCleanup(self.flow.tearDown)
        self.shares = PublicProfiles(self.flow.portal)
        self.person, self.cookie = self.flow.completed_person(name='Synthetic Candidate')

    def call(self, path=PUBLIC, body=None, method='POST', cookie='', **headers):
        return self.shares.handle(method, path, {'Content-Type': 'application/json', 'Cookie': cookie,
            'Origin': 'https://portal.example', **headers}, body, '127.0.0.1')

    def issue(self, cid=None):
        with self.flow.connect() as db:
            result = self.shares.issue(db, cid or self.person['id'], issuer='operator:test', origin='https://portal.example')
        return parse_qs(urlsplit(result['url']).fragment)['profile'][0]

    def problem(self, status, action):
        with self.assertRaises(Problem) as caught:
            action()
        self.assertEqual(caught.exception.status, status, caught.exception.message)

    def test_anonymous_profile_is_allowlisted_and_token_is_only_hashed(self):
        token = self.issue()
        data, headers = self.call(body={'token': token})
        self.assertEqual(data['name'], 'Synthetic Candidate')
        self.assertEqual(set(data), {'id', 'name', 'role', 'resume'})
        self.assertEqual(set(data['resume']), {'name', 'mime', 'size'})
        self.assertIn('noindex', headers['X-Robots-Tag'])
        with self.flow.connect() as db:
            row = db.execute('SELECT token_hash,issuer FROM cp_public_profiles').fetchone()
        self.assertEqual(row, (hashlib.sha256(token.encode()).hexdigest(), 'operator:test'))
        self.assertNotIn(token, json.dumps(row))

    def test_owner_authentication_explicit_consent_and_csrf(self):
        self.problem(401, lambda: self.call(OWNER, {'action': 'create', 'consent': True}))
        self.problem(400, lambda: self.call(OWNER, {'action': 'create'}, cookie=self.cookie))
        self.problem(400, lambda: self.call(OWNER, {'action': 'create', 'consent': False}, cookie=self.cookie))
        self.problem(403, lambda: self.call(OWNER, {'action': 'create', 'consent': True}, cookie=self.cookie, Origin='https://attacker.example'))
        self.problem(403, lambda: self.call(OWNER, {'action': 'revoke'}, cookie=self.cookie, **{'Sec-Fetch-Site': 'cross-site'}))
        issued, _ = self.call(OWNER, {'action': 'create', 'consent': True}, cookie=self.cookie)
        self.assertTrue(issued['url'].startswith('https://portal.example/iframe.html?'))
        state, _ = self.call(OWNER, method='GET', cookie=self.cookie)
        self.assertTrue(state['active'])
        self.assertNotIn('url', state)

    def test_candidate_cannot_publish_another_person(self):
        self.problem(400, lambda: self.call(OWNER, {'action': 'create', 'consent': True, 'candidateId': 'other'}, cookie=self.cookie))
        token = self.issue()
        self.problem(400, lambda: self.call(body={'token': token, 'candidateId': 'other'}))
        self.problem(400, lambda: self.call(PUBLIC + '/materials', {'token': token, 'meetingId': 'other'}))

    def test_revocation_rotation_and_owner_isolation(self):
        first = self.issue()
        second = self.issue()
        self.problem(410, lambda: self.call(body={'token': first}))
        other, other_cookie = self.flow.register()
        self.call(OWNER, {'action': 'revoke'}, cookie=other_cookie)
        self.assertEqual(self.call(body={'token': second})[0]['name'], 'Synthetic Candidate')
        self.call(OWNER, {'action': 'revoke'}, cookie=self.cookie)
        for suffix in ('', '/resume', '/materials'):
            self.problem(410, lambda: self.call(PUBLIC + suffix, {'token': second}))

    def test_only_completed_interview_can_be_shared(self):
        person, _ = self.flow.register()
        self.problem(409, lambda: self.issue(person['id']))
        with self.flow.connect() as db:
            db.execute("UPDATE cp_interviews SET status='live' WHERE candidate_id=?", (self.person['id'],))
        self.problem(409, lambda: self.issue())

    def test_operator_grant_does_not_forge_candidate_hiring_consent(self):
        self.flow.call('candidate/profile', {'consentReuse': False, 'autoApprove': False}, self.cookie)
        self.issue()
        with self.flow.connect() as db:
            profile = self.flow.portal.person(db, self.person['id'])
        self.assertFalse(profile['consentReuse'])
        self.assertFalse(profile['autoApprove'])

    def test_link_stops_if_underlying_recording_or_status_changes(self):
        token = self.issue()
        with self.flow.connect() as db:
            db.execute('UPDATE cp_interviews SET room=? WHERE candidate_id=?', (json.dumps({'name': 'different-room'}), self.person['id']))
        self.problem(410, lambda: self.call(body={'token': token}))

    def test_materials_are_bound_rechecked_and_strip_provider_details(self):
        token = self.issue()
        with self.flow.connect() as db:
            meeting = self.flow.portal.interview(db, self.person['id'])
        def materials(cid, *, meeting_id, access_check):
            self.assertEqual(cid, self.person['id'])
            self.assertEqual(meeting_id, meeting['id'])
            with self.flow.connect() as db:
                access_check(db)
            return {'room': {'secret': 'private'}, 'recordings': [{'id': 'recording', 'url': 'https://media.example/file', 'expires': 2000, 'storage': {'key': 'private'}}],
                'transcripts': [{'id': 'text', 'text': 'Synthetic transcript', 'storage': {'bucket': 'private'}}], 'video': {'status': 'ready'}}
        with patch.object(self.flow.portal, 'materials', side_effect=materials):
            result, _ = self.call(PUBLIC + '/materials', {'token': token})
        self.assertNotIn('private', json.dumps(result))
        self.assertEqual(result['transcripts'][0]['text'], 'Synthetic transcript')

    def test_revocation_during_provider_request_prevents_returning_materials(self):
        token = self.issue()
        def revoke_during_io(*args, **kwargs):
            with self.flow.connect() as db:
                self.shares.revoke(db, self.person['id'])
            return {'recordings': [{'id': 'private', 'url': 'https://media.example/private'}]}
        with patch.object(self.flow.portal, 'materials', side_effect=revoke_during_io):
            self.problem(410, lambda: self.call(PUBLIC + '/materials', {'token': token}))

    def test_resume_requires_capability_and_returns_only_original_document(self):
        token = self.issue()
        result, _ = self.call(PUBLIC + '/resume', {'token': token})
        self.assertEqual(set(result), {'filename', 'mime', 'contentBase64'})
        self.assertEqual(result['filename'], 'resume.txt')
        self.problem(404, lambda: self.call(PUBLIC + '/resume', {'token': 'invalid'}))

    def test_url_tokens_and_malformed_requests_are_rejected(self):
        token = self.issue()
        self.problem(400, lambda: self.call(PUBLIC + '?token=' + token, {'token': token}))
        self.problem(405, lambda: self.call(method='GET'))
        self.problem(415, lambda: self.call(body={'token': token}, **{'Content-Type': 'text/plain'}))
        for value in (None, 123, {}, 'x' * 42):
            self.problem(404, lambda: self.call(body={'token': value}))
        self.problem(410, lambda: self.call(body={'token': 'x' * 43}))
