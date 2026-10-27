"""Search pause/archive regressions through the existing local HTTP bot harness."""
import hashlib
import json
import sqlite3
import unittest
from unittest.mock import patch

import test_candidate_bot_http as bot_http


class CandidateSearchBotTest(unittest.TestCase):
    def setUp(self):
        # Reuse its isolated HTTP server, canonical fixtures and provider guards
        # without inheriting and rerunning the base test methods in this module.
        self.h = bot_http.CandidateBotHTTPTest(methodName='runTest')
        self.addCleanup(self.h.doCleanups)
        self.h.setUp()
        self.candidate = self.h.register(1001)
        self.h.sent.clear()

    def search(self, searching):
        status, body, _ = self.h.request('candidate/search', {'searching': searching}, cookie=self.candidate['cookie'])
        self.assertEqual(status, 200, body)
        self.assertEqual(self.h.state(self.candidate)['searching'], searching)

    def outbox(self):
        with self.h.app.connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute('SELECT * FROM cn_outbox ORDER BY rowid')]

    def text(self):
        return '\n'.join(payload.get('text', '') for method, payload in self.h.sent if method == 'sendMessage')

    def test_old_application_and_meeting_buttons_stay_blocked_after_pause_and_resume(self):
        application = self.h.application(self.candidate, 'Old application')
        meeting_application = self.h.application(self.candidate, 'Old meeting')
        meeting = self.h.proposed_meeting(self.candidate, meeting_application)
        self.h.drain()
        accept = next(button['callback_data'] for button in self.h.buttons() if button['text'] == 'Согласиться')
        confirm = next(button['callback_data'] for button in self.h.buttons() if button['text'] == 'Подтвердить это время')
        self.search(False)
        for searching in (False, True):
            if searching:
                self.search(True)
            self.assertEqual(self.h.callback(1001, accept)['status'], 'stale_callback')
            self.assertEqual(self.h.callback(1001, confirm)['status'], 'stale_callback')
            self.h.drain()
            state = self.h.application_state(self.candidate, application)
            self.assertTrue(state['archivedAt'])
            self.assertEqual(state['status'], 'pending')
            with self.h.app.connect() as db:
                self.assertEqual(db.execute('SELECT status FROM cp_meetings WHERE id=?', (meeting['id'],)).fetchone()[0], 'cancelled')
                for token in (accept, confirm):
                    digest = hashlib.sha256(token[3:].encode()).hexdigest()
                    self.assertIsNone(db.execute('SELECT used_at FROM cb_actions WHERE digest=?', (digest,)).fetchone()[0])

    def test_archived_cards_are_hidden_but_new_applications_after_resume_are_visible(self):
        archived = self.h.application(self.candidate, 'Archived vacancy title')
        self.h.proposed_meeting(self.candidate, archived)
        self.h.drain()
        self.search(False)
        self.h.sent.clear()
        self.h.message(1001, '/vacancies')
        self.h.message(1001, '/meetings')
        self.h.message(1001, '/status')
        self.h.drain()
        self.assertIn('Поиск работы приостановлен', self.text())
        self.assertNotIn('Archived vacancy title', self.text())
        self.assertFalse(any(button.get('callback_data', '').startswith('cb:') for button in self.h.buttons()))
        self.search(True)
        active = self.h.application(self.candidate, 'New active vacancy title')
        self.h.sent.clear()
        self.h.message(1001, '/vacancies')
        self.h.message(1001, '/meetings')
        self.h.message(1001, '/status')
        self.h.drain()
        self.assertNotIn('Archived vacancy title', self.text())
        self.assertIn('New active vacancy title', self.text())
        self.assertIn('Активных предложений вакансий: 1', self.text())
        self.assertTrue(any(button['text'] == 'Согласиться' for button in self.h.buttons()))
        self.assertTrue(self.h.application_state(self.candidate, archived)['archivedAt'])
        self.assertIsNone(self.h.application_state(self.candidate, active)['archivedAt'])
        self.assertFalse(any('application_id' in payload for method, payload in self.h.sent))

    def test_queued_metadata_and_legacy_bot_cards_never_wake_after_pause_resume_or_retry(self):
        application = self.h.application(self.candidate, 'Undelivered old vacancy')
        self.h.message(1001, '/vacancies')
        self.h.message(1001, '/vacancies')
        queued = [row for row in self.outbox() if row['event_type'] == 'telegram.reply' and row['status'] == 'pending']
        self.assertEqual(len(queued), 2)
        with self.h.app.connect() as db:
            payload = json.loads(queued[0]['payload_json'])
            self.assertEqual(payload.pop('application_id'), application['id'])
            db.execute('UPDATE cn_outbox SET payload_json=? WHERE id=?', (json.dumps(payload), queued[0]['id']))
        self.search(False)
        self.search(True)
        self.h.sent.clear()
        self.h.drain()
        self.assertNotIn('Undelivered old vacancy', self.text())
        self.assertEqual(self.h.sent, [])
        for row in self.outbox():
            if row['id'] in {item['id'] for item in queued}:
                self.assertEqual((row['status'], row['last_error']), ('error', 'application_archived'))
                with self.h.app.connect() as db:
                    retried = self.h.app.candidate_notifications.retry(db, row['id'], candidate_id=self.candidate['id'])
                self.assertEqual(retried['status'], 'error')
        self.h.drain()
        self.assertEqual(self.h.sent, [])

    def test_pending_application_meeting_and_workflow_lifecycle_stop_on_all_channels(self):
        application = self.h.application(self.candidate, 'Pending lifecycle old vacancy')
        meeting = self.h.proposed_meeting(self.candidate, application)
        with self.h.app.connect() as db:
            event_key = 'offer.sent:fixture-offer'
            db.execute('INSERT INTO cp_workflow_events VALUES (?,?,?,?,?)',
                       (event_key, self.candidate['id'], application['id'], '{}', '2026-10-13T00:00:00Z'))
            self.h.app.candidate_notifications.enqueue(db, self.candidate['id'], event_key, 'offer.sent')
            # Enable existing email rows as pending fixtures. Guarding happens
            # before channel/provider availability, so none may reach adapters.
            db.execute("UPDATE cn_outbox SET status='pending' WHERE candidate_id=? AND event_type IN ('application.proposed','meeting.proposed','offer.sent')",
                       (self.candidate['id'],))
        event_keys = {'application.proposed:' + application['id'], 'meeting.proposed:' + meeting['id'], event_key}
        self.search(False)
        self.search(True)
        self.h.sent.clear()
        with patch.object(self.h.app.candidate_notifications.Transport, 'send_email') as email:
            self.h.drain()
        email.assert_not_called()
        self.assertEqual(self.h.sent, [])
        rows = [row for row in self.outbox() if row['event_key'] in event_keys]
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(row['status'] == 'error' and row['last_error'] == 'application_archived' for row in rows))

    def test_success_receipt_and_ack_are_record_bound_but_stale_answer_still_arrives(self):
        application = self.h.application(self.candidate, 'Receipt old vacancy')
        self.h.drain()
        button = next(item['callback_data'] for item in self.h.buttons() if item['text'] == 'Согласиться')
        self.assertEqual(self.h.callback(1001, button)['status'], 'updated')
        queued = [row for row in self.outbox() if row['status'] == 'pending' and row['event_type'] in ('telegram.reply', 'telegram.callback')]
        self.assertEqual(len(queued), 2)
        self.assertTrue(all(json.loads(row['payload_json'])['application_id'] == application['id'] for row in queued))
        self.search(False)
        self.search(True)
        self.h.sent.clear()
        self.h.drain()
        self.assertEqual(self.h.sent, [])
        self.assertEqual(self.h.callback(1001, button)['status'], 'invalid_callback')
        self.h.drain()
        self.assertEqual([method for method, _payload in self.h.sent], ['answerCallbackQuery'])

    def test_paused_candidate_can_use_settings_and_role_menu_without_archived_actions(self):
        self.search(False)
        self.h.message(1001, '/settings')
        self.h.drain()
        enable = next(button['callback_data'] for button in self.h.buttons() if button['text'] == 'Включить автосогласование')
        self.assertEqual(self.h.callback(1001, enable)['status'], 'updated')
        self.h.drain()
        state = self.h.state(self.candidate)
        self.assertTrue(state['autoApprove'])
        self.assertFalse(state['searching'])
        self.h.sent.clear()
        self.h.message(1001, '/start')
        self.h.drain()
        self.h.assert_role_menu()

    def test_search_stopped_dispatch_is_terminal_even_if_record_has_no_archive(self):
        application = self.h.application(self.candidate, 'Search stopped fixture')
        self.h.message(1001, '/vacancies')
        with self.h.app.connect() as db:
            db.execute('UPDATE cp_candidates SET searching=0 WHERE id=?', (self.candidate['id'],))
        self.h.sent.clear()
        self.h.drain()
        self.assertEqual(self.h.sent, [])
        blocked = [row for row in self.outbox() if row['last_error'] == 'search_stopped']
        self.assertTrue(blocked)
        with self.h.app.connect() as db:
            db.execute('UPDATE cp_candidates SET searching=1 WHERE id=?', (self.candidate['id'],))
            for row in blocked:
                self.assertEqual(self.h.app.candidate_notifications.retry(db, row['id'])['status'], 'error')
        self.h.drain()
        self.assertEqual(self.h.sent, [])
        self.assertIsNone(self.h.application_state(self.candidate, application)['archivedAt'])


if __name__ == '__main__':
    unittest.main()
