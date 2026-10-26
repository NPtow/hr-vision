"""HTTP readiness reflects storage and worker; operator output excludes sources."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from assessment_core.api import handler
from assessment_core.engine import prepare
from assessment_core.runstore import RunStore


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = RunStore(Path(self.tmp.name) / 'runs.db')
        examples = Path(__file__).resolve().parents[1] / 'examples'
        corpus = json.loads((examples / 'corpus.synthetic.json').read_text())
        corpus['sources'][0]['units'][0]['text'] = 'PRIVATE SOURCE MUST NOT LEAK'
        self.plan = prepare(corpus, json.loads((examples / 'rubric.synthetic.json').read_text()))
        self.store.submit(self.plan)
        self.runtime = {'worker': threading.current_thread(), 'stopping': threading.Event()}
        self.server = ThreadingHTTPServer(('127.0.0.1', 0),
            handler(self.store, 'jev-1.14.0', 'a1' * 32, self.runtime))
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, path, *, auth=True):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        connection.request('GET', path, headers={'Authorization': 'Bearer ' + 'a1' * 32} if auth else {})
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_ready_and_operator_are_protected_and_never_expose_corpus(self):
        for path in ('/readyz', '/v1/operator'):
            self.assertEqual(self.request(path, auth=False)[0], 401)
        self.assertEqual(self.request('/readyz')[0], 200)
        status, value = self.request('/v1/operator')
        self.assertEqual(status, 200)
        self.assertEqual(value['storage']['counts'], {'queued': 1})
        self.assertEqual(value['storage']['pending'], 1)
        self.assertTrue(value['worker_running'])
        self.assertNotIn('PRIVATE SOURCE', json.dumps(value))
        self.assertNotIn(self.plan['corpus']['candidate_id'], json.dumps(value))
        self.assertEqual(self.store.get(self.plan['run_id'])['status'], 'queued')

    def test_readiness_fails_for_dead_worker_stopping_or_unwritable_store(self):
        for key, value in [('worker', threading.Thread()), ('stopping', threading.Event())]:
            if key == 'stopping':
                value.set()
            previous = self.runtime[key]
            self.runtime[key] = value
            self.assertEqual(self.request('/readyz')[0], 503)
            self.runtime[key] = previous
        with patch.object(self.store, 'ready', side_effect=OSError('PRIVATE DB PATH')):
            code, value = self.request('/readyz')
            self.assertEqual((code, value), (503, {'error': 'service_unavailable'}))
        self.assertEqual(self.request('/healthz', auth=False)[0], 200)


if __name__ == '__main__':
    unittest.main()
