"""Real GET transport + query-only SQLite for popup scope and saved materials.

Synthetic canonical Core responses; no provider/model/capture/delivery calls.
"""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sqlite3
import tempfile
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from core_source_client import CoreClient, ServiceHTTP
from candidate_portal import ClosingConnection
from domain import Problem
from selection_interviews import Reader
import test_employer_acceptance as http_fixture


def interview(iid, parts=2):
    sources = [{'id': iid+'-source', 'binding_id': iid+'-source', 'generation': 1, 'revoked': False,
                'source_ref': {'private': 'DO NOT EXPOSE SOURCE SECRET'}}]
    data = {'owner_kind': 'interview', 'owner_id': iid, 'sources': sources, 'assets': [], 'transcripts': [],
            'channels': [{'video_status': 'ready', 'transcript_status': 'ready'}],
            'transcript_selections': [], 'transcript_selection_required_recording_ids': []}
    for number in range(parts):
        rid, tid = iid+'-recording-'+str(number), iid+'-transcript-'+str(number)
        data['assets'].extend([{'id': rid, 'source_id': sources[0]['id'], 'kind': 'video', 'state': 'ready',
                                'metadata': {'part_index': number, 'duration': 30}},
                              {'id': tid+'-asset', 'source_id': sources[0]['id'], 'kind': 'transcript', 'state': 'ready'}])
        data['transcript_selections'].append({'recording_id': rid, 'transcript_id': tid, 'status': 'ready',
                                               'revision': 4, 'generation': 1})
        data['transcript_selection_required_recording_ids'].append(rid)
        data['transcripts'].append({'id': tid, 'asset_id': tid+'-asset', 'version': 1,
            'segments': [{'text': 'Saved exact part '+str(number), 'start_ms': 1000, 'end_ms': 2000, 'speaker': '0'}],
            'metadata': {'normalized': {'vtt': 'WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nSaved exact part '+str(number),
                                      'full_text': 'Saved exact part '+str(number)}}, 'content_sha256': 'a'*64})
    return {'id': iid, 'status': 'completed', 'created_at': '2026-10-25T10:00:00Z',
            'completed_at': '2026-10-25T11:00:00Z', 'materials': data}


class CoreServer:
    def __init__(self, cid='c1'):
        self.cid = cid
        self.owners = [interview('i1'), interview('i2', 1)]
        self.calls, self.on_access, self.fail_access = [], None, False
        self.bad_dossier, self.bad_owner = False, False
        self.link_revision = 1
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                fixture.calls.append(('GET', self.path))
                status = 200
                if self.path.endswith('/dossier'):
                    value = {'candidate_id': 'foreign' if fixture.bad_dossier else fixture.cid,
                             'interviews': fixture.owners, 'meetings': [interview('NOT-HR')]}
                elif self.path.endswith('/materials'):
                    iid = self.path.split('/')[-2]
                    value = deepcopy(next(o['materials'] for o in fixture.owners if o['id'] == iid))
                    if fixture.bad_owner:
                        value['owner_id'] = 'foreign'
                else:
                    if fixture.on_access:
                        fixture.on_access()
                    if fixture.fail_access:
                        status, value = 503, {'error': 'PRIVATE PROVIDER ERROR'}
                    else:
                        value = {'storage': 'owned', 'url': 'https://owned.example.test/play?signature=PRIVATE-PLAYBACK-'+str(fixture.link_revision), 'expires': 1800000000}
                raw = json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.client = CoreClient(ServiceHTTP('http://127.0.0.1:'+str(self.server.server_port), 'fixture-only'))

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'hr.db'
        sqlite3.connect(self.path).close()
        self.core = CoreServer()
        self.addCleanup(self.core.close)
        self.allowed = True
        self.reader = Reader(self.core.client, self.connect, self.authorize)

    def connect(self):
        db = sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True, factory=ClosingConnection)
        db.execute('PRAGMA query_only=ON')
        return db

    def authorize(self, cid):
        if not self.allowed or cid != 'c1':
            raise Problem('denied', 403)

    def test_all_interviews_and_parts_saved_text_and_normal_playback_no_writes(self):
        listed = self.reader.list('c1')
        self.assertEqual([i['id'] for i in listed['interviews']], ['i1', 'i2'])
        self.assertEqual([i['recordingCount'] for i in listed['interviews']], [2, 1])
        self.assertNotIn('DO NOT EXPOSE', json.dumps(listed))
        self.assertNotIn('signature', json.dumps(listed))
        value = self.reader.materials('c1', 'i1')
        self.assertEqual((value['candidateId'], value['interviewId']), ('c1', 'i1'))
        self.assertEqual(len(value['recordings']), 2)
        self.assertEqual([t['fullText'] for t in value['partTranscripts']], ['Saved exact part 0', 'Saved exact part 1'])
        for part in value['parts']:
            self.assertTrue(part['mediaAvailable'] and part['selectedTranscriptAvailable'])
            linked = next(t for t in value['partTranscripts'] if t['recordingId'] == part['id'])
            self.assertEqual(linked['id'], part['transcriptId'])
            self.assertEqual(linked['selectionRevision'], 4)
        self.assertNotIn('transcriptVariants', value)
        self.assertTrue(all(method == 'GET' and not any(command in path for command in ('collect', 'assess', 'asr', 'meetings'))
                            for method, path in self.core.calls))

    def test_partial_ready_text_is_exact_primary_only_and_missing_part_remains_visible(self):
        data = self.core.owners[0]['materials']
        data['transcript_selections'][1].update(status='processing', transcript_id=None)
        data['assets'][2]['state'] = 'missing'
        # A newer unselected variant must never replace the selected first part.
        other = deepcopy(data['transcripts'][0])
        other.update(id='unselected-newer', version=9)
        other['metadata']['normalized']['full_text'] = 'WRONG VARIANT'
        data['transcripts'].append(other)
        value = self.reader.materials('c1', 'i1')
        self.assertEqual(value['transcription']['status'], 'processing')
        self.assertEqual(value['transcripts'], [])
        self.assertEqual([t['id'] for t in value['partTranscripts']], ['i1-transcript-0'])
        self.assertEqual(len(value['parts']), 2)
        self.assertFalse(value['parts'][1]['mediaAvailable'])
        self.assertFalse(value['parts'][1]['selectedTranscriptAvailable'])
        self.assertEqual(value['parts'][1]['state'], 'missing')
        self.assertNotIn('WRONG VARIANT', json.dumps(value))

    def test_no_interviews_is_honest_missing_and_unrelated_interview_is_not_accessible(self):
        with self.assertRaises(Problem) as error:
            self.reader.materials('c1', 'foreign-candidate-interview')
        self.assertEqual(error.exception.status, 404)
        self.assertFalse(any('/access' in p for _, p in self.core.calls))
        self.core.owners = []
        self.assertEqual(self.reader.list('c1'), {'candidateId': 'c1', 'state': 'missing', 'interviews': []})

    def test_access_revoked_during_link_issue_withholds_every_link_and_text(self):
        self.core.on_access = lambda: setattr(self, 'allowed', False)
        with self.assertRaises(Problem) as error:
            self.reader.materials('c1', 'i1')
        self.assertEqual(error.exception.status, 403)

    def test_core_candidate_and_owner_mismatch_and_revoked_binding_are_rejected(self):
        for attr, status in [('bad_dossier', 502), ('bad_owner', 502)]:
            setattr(self.core, attr, True)
            with self.assertRaises(Problem) as error:
                self.reader.materials('c1', 'i1')
            self.assertEqual(error.exception.status, status)
            setattr(self.core, attr, False)
        self.core.owners[0]['materials']['sources'][0]['revoked'] = True
        with self.assertRaises(Problem) as error:
            self.reader.materials('c1', 'i1')
        self.assertEqual(error.exception.status, 410)

    def test_playback_failure_keeps_saved_text_and_explicit_media_error(self):
        self.core.fail_access = True
        value = self.reader.materials('c1', 'i1')
        self.assertEqual(value['video']['status'], 'error')
        self.assertEqual(value['recordings'], [])
        self.assertEqual(len(value['partTranscripts']), 2)
        self.assertFalse(any(p['mediaAvailable'] for p in value['parts']))
        self.assertNotIn('PRIVATE PROVIDER ERROR', json.dumps(value))

    def test_new_get_reissues_expiring_link_without_mutating_sources(self):
        before = deepcopy(self.core.owners)
        first = self.reader.materials('c1', 'i1')
        self.core.link_revision = 2
        again = self.reader.materials('c1', 'i1')
        self.assertNotEqual(first['recordings'][0]['url'], again['recordings'][0]['url'])
        self.assertEqual(first['partTranscripts'], again['partTranscripts'])
        self.assertEqual(self.core.owners, before)

    def test_primary_selection_changed_during_link_issue_is_rejected(self):
        def change():
            self.core.owners[0]['materials']['transcript_selections'][0]['revision'] += 1
        self.core.on_access = change
        with self.assertRaises(Problem) as error:
            self.reader.materials('c1', 'i1')
        self.assertEqual(error.exception.status, 409)

    def test_noncompleted_and_completed_without_any_materials_are_honest(self):
        self.core.owners[0]['status'] = 'live'
        with self.assertRaises(Problem) as error:
            self.reader.materials('c1', 'i1')
        self.assertEqual(error.exception.status, 409)
        self.core.owners[0]['status'] = 'completed'
        data = self.core.owners[0]['materials']
        for key in ('sources', 'assets', 'channels', 'transcripts', 'transcript_selections', 'transcript_selection_required_recording_ids'):
            data[key] = []
        result = self.reader.materials('c1', 'i1')
        self.assertEqual(result['status'], 'missing')
        self.assertEqual(result['parts'], [])


class RoutesHTTPTests(unittest.TestCase):
    setUpClass = classmethod(http_fixture.EmployerAcceptanceTest.setUpClass.__func__)
    tearDownClass = classmethod(http_fixture.EmployerAcceptanceTest.tearDownClass.__func__)
    setUp = http_fixture.EmployerAcceptanceTest.setUp
    request = http_fixture.EmployerAcceptanceTest.request
    ok = http_fixture.EmployerAcceptanceTest.ok
    employer = http_fixture.EmployerAcceptanceTest.employer
    candidate = http_fixture.EmployerAcceptanceTest.candidate

    def test_normal_manager_api_rejects_anonymous_candidate_employer_cross_site_and_post(self):
        candidate = self.candidate('Interview popup')
        cid = candidate['id']
        core = CoreServer(cid)
        self.addCleanup(core.close)
        def configured(connect, authorize):
            return Reader(core.client, connect, authorize)
        route = 'selection/candidates/'+cid+'/interviews'
        with patch('selection_interviews.configured', side_effect=configured):
            for auth in ({}, {'cookie': candidate['cookie']}, {'token': self.employer()['token']}):
                self.assertIn(self.request(route, **auth)[0], (401, 403))
            self.assertEqual(core.calls, [])
            self.assertEqual(self.request(route, token=self.manager, headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
            self.assertEqual(self.request(route, {}, token=self.manager)[0], 404)
            with self.app.connect() as db:
                outbox = db.execute('SELECT count(*) FROM cn_outbox').fetchone()[0]
            status, value, headers = self.request(route, token=self.manager)
            self.assertEqual(status, 200, value)
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
            status, value, _ = self.request(route+'/i1/materials', token=self.manager)
            self.assertEqual(status, 200, value)
            self.assertEqual(value['candidateId'], cid)
            self.assertEqual(len(value['partTranscripts']), 2)
            self.assertEqual(self.request(route+'/not-my-interview/materials', token=self.manager)[0], 404)
            with self.app.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM cn_outbox').fetchone()[0], outbox)


if __name__ == '__main__':
    unittest.main()
