import json
import unittest
from unittest.mock import patch
import observability as obs

class ObservabilityTest(unittest.TestCase):
    def test_sensitive_values_never_enter_telemetry_and_trace_is_correlated(self):
        records = []
        with patch.object(obs, '_ENDPOINT', 'http://127.0.0.1:8418'), patch.object(obs, '_emit', side_effect=lambda kind,item: records.append((kind,item))):
            with obs.span('GET /api/hr/candidate/:id', parent='00-'+'a'*32+'-'+'b'*16+'-01', fields={'http.route':'/api/hr/candidate/:id'}) as current:
                try:
                    private = 'PRIVATE CV mail@example.com secret-token'
                    raise ValueError(private)
                except ValueError as error:
                    obs.capture_exception(error)
                with obs.span('daily.request') as child:
                    self.assertEqual(child['traceId'], current['traceId'])
                obs.response_status(500)
        encoded = json.dumps(records)
        self.assertNotIn('PRIVATE', encoded)
        self.assertNotIn('mail@example.com', encoded)
        self.assertNotIn('secret-token', encoded)
        self.assertIn('test_observability.py:', encoded)
        self.assertEqual(records[0][1]['traceId'], 'a'*32)
        root = records[-1][1]
        self.assertEqual(root['parentSpanId'], 'b'*16)
        self.assertEqual(root['status']['code'], 2)
        self.assertEqual(records[0][1]['spanId'], root['spanId'])

    def test_route_and_parent_are_not_a_secret_side_channel(self):
        self.assertEqual(obs.route_template('/api/hr/candidate/secret@example.com/resume?token=secret'), '/api/hr/candidate/:id/resume')
        self.assertEqual(obs.route_template('/private/secret'), '/unknown')
        with patch.object(obs, '_emit'):
            with obs.span('test', parent='00-'+'0'*32+'-'+'0'*16+'-01') as current:
                self.assertNotEqual(current['traceId'], '0'*32)

    def test_disabled_and_full_queue_do_not_change_product_behavior(self):
        with patch.object(obs, '_ENDPOINT', ''):
            @obs.operation('test')
            def product(): return 42
            self.assertEqual(product(), 42)
        with patch.object(obs, '_ENDPOINT', 'http://127.0.0.1'), patch.object(obs, '_STARTED', True), patch.object(obs._QUEUE, 'put_nowait', side_effect=obs.queue.Full):
            obs._emit('logs', {})  # must not raise or block

    def test_status_only_5xx_is_error(self):
        records=[]
        with patch.object(obs, '_emit', side_effect=lambda kind,item: records.append(item)):
            with obs.span('GET /test'):
                obs.response_status(404)
        self.assertEqual(records[0]['status']['code'], 1)
