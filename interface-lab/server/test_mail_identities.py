"""Role-specific SMTP identities through real dispatchers, with fake providers."""
import json
import unittest
from unittest.mock import MagicMock, patch

import candidate_notifications as delivery
import recruiter_notifications as recruiter
import test_meeting_notifications as lifecycle_fixture
import test_meeting_reminders as reminder_fixture


def environment():
    values = {'HR_NOTIFICATIONS_ENABLED': '1', 'HR_SMTP_ROLE_ROUTING': '1',
              'HR_ORIGIN': 'https://fixture.test', 'HR_SMTP_HOST': 'smtp.fixture.test',
              'HR_SMTP_PORT': '465', 'HR_SMTP_SECURITY': 'ssl',
              'HR_SMTP_FROM': 'legacy@fixture.test', 'HR_SMTP_USER': 'legacy@fixture.test',
              'HR_SMTP_PASSWORD': 'legacy-test-only'}
    for role in ('CANDIDATE', 'RECRUITER', 'HIRINGMANAGER'):
        values['HR_SMTP_' + role + '_FROM'] = role.lower() + '@fixture.test'
        values['HR_SMTP_' + role + '_USER'] = role.lower() + '@fixture.test'
        values['HR_SMTP_' + role + '_PASSWORD'] = role.lower() + '-test-only'
    return values


def smtp_mock():
    server = MagicMock()
    server.__enter__.return_value = server
    server.send_message.return_value = {}
    return server


class ConfigurationTest(unittest.TestCase):
    def test_each_identity_uses_own_login_from_and_reply_to_for_same_recipient(self):
        config = delivery.settings(environment())
        for role, address, name in [('candidate', 'candidate', 'Маша'), ('booking', 'candidate', 'Маша'),
                                    ('recruiter', 'recruiter', 'Rec'), ('employer', 'hiringmanager', 'Даша')]:
            with self.subTest(role=role):
                server = smtp_mock()
                with patch.object(delivery.smtplib, 'SMTP_SSL', return_value=server):
                    delivery.Transport(config, email_role=role).send_email('same-person@fixture.test', 'Meeting', 'Body', role)
                server.login.assert_called_once_with(address + '@fixture.test', address + '-test-only')
                msg = server.send_message.call_args.args[0]
                self.assertEqual(msg['From'].addresses[0].addr_spec, address + '@fixture.test')
                self.assertIn(name, msg['From'].addresses[0].display_name)
                self.assertEqual(str(msg['Reply-To']), address + '@fixture.test')
                self.assertEqual(str(msg['To']), 'same-person@fixture.test')

    def test_missing_role_never_uses_legacy_or_another_account(self):
        env = environment()
        del env['HR_SMTP_CANDIDATE_PASSWORD']
        config = delivery.settings(env)
        self.assertFalse(config.email_configured)
        self.assertTrue(config.email_configured_for('recruiter'))
        self.assertTrue(config.email_configured_for('employer'))
        with patch.object(delivery.smtplib, 'SMTP_SSL') as smtp:
            with self.assertRaises(delivery.DeliveryFailure) as caught:
                delivery.Transport(config).send_email('candidate@fixture.test', 'Meeting', 'Body', 'test')
        self.assertEqual(caught.exception.code, 'smtp_not_configured')
        smtp.assert_not_called()
        self.assertNotIn('test-only', repr(config))

    def test_duplicate_from_and_mismatched_login_disable_role(self):
        env = environment()
        env['HR_SMTP_CANDIDATE_FROM'] = env['HR_SMTP_RECRUITER_FROM']
        config = delivery.settings(env)
        self.assertFalse(config.email_configured_for('candidate'))
        self.assertFalse(config.email_configured_for('recruiter'))
        self.assertTrue(config.email_configured_for('employer'))
        with self.assertRaises(ValueError):
            delivery.Transport(config, email_role='unknown')
        env = environment()
        env['HR_SMTP_CANDIDATE_USER'] = env['HR_SMTP_RECRUITER_USER']
        config = delivery.settings(env)
        self.assertFalse(config.email_configured_for('candidate'))
        self.assertTrue(config.email_configured_for('recruiter'))

    def test_wrong_outbox_owner_cannot_select_recruiter_or_employer_sender(self):
        for role in ('recruiter', 'employer'):
            row = {'event_type': 'meeting.reminder', 'candidate_id': 'candidate',
                   'payload_json': json.dumps({'role': role, 'person': 'person'})}
            with self.assertRaises(delivery.DeliveryFailure) as caught:
                delivery._outbox_email_role(row)
            self.assertEqual(caught.exception.code, 'email_role_mismatch')


class ReminderRoutingTest(unittest.TestCase):
    connect = reminder_fixture.RemindersTest.connect
    change = reminder_fixture.RemindersTest.change
    meeting = reminder_fixture.RemindersTest.meeting
    booking_only = reminder_fixture.RemindersTest.booking_only

    def setUp(self):
        reminder_fixture.RemindersTest.setUp(self)
        self.config = delivery.settings(environment())

    def dispatch(self):
        server = smtp_mock()
        with patch.object(delivery.smtplib, 'SMTP_SSL', return_value=server), patch('employer_notifications.collect'):
            delivery.process_pending(self.connect, self.config, 100, self.tick)
            recruiter.process_pending(self.connect, self.config, 100, self.tick)
        return [(str(c.args[0]['To']), c.args[0]['From'].addresses[0].addr_spec)
                for c in server.send_message.call_args_list]

    def test_interview_reminders_choose_candidate_and_recruiter(self):
        self.assertEqual(set(self.dispatch()), {('candidate@fixture.test', 'candidate@fixture.test'),
                                               ('recruiter@fixture.test', 'recruiter@fixture.test')})

    def test_employer_meeting_reminders_choose_candidate_and_hiringmanager(self):
        self.meeting()
        self.assertEqual(set(self.dispatch()), {('candidate@fixture.test', 'candidate@fixture.test'),
                                               ('employer@fixture.test', 'hiringmanager@fixture.test')})

    def test_pre_account_booking_uses_candidate_sender(self):
        self.booking_only()
        self.assertIn(('consent@fixture.test', 'candidate@fixture.test'), self.dispatch())

    def test_missing_candidate_sender_does_not_disable_recruiter(self):
        env = environment(); del env['HR_SMTP_CANDIDATE_PASSWORD']
        self.config = delivery.settings(env)
        self.assertEqual(self.dispatch(), [('recruiter@fixture.test', 'recruiter@fixture.test')])


class LifecycleRoutingTest(unittest.TestCase):
    connect = reminder_fixture.RemindersTest.connect
    change = reminder_fixture.RemindersTest.change
    meeting = reminder_fixture.RemindersTest.meeting

    def setUp(self):
        lifecycle_fixture.LifecycleTest.setUp(self)
        self.config = delivery.settings(environment())

    def test_confirmation_routes_each_participant_and_still_sends_only_once(self):
        with self.connect() as db:
            delivery.enqueue(db, 'candidate', 'meeting.confirmed:meeting', 'meeting.confirmed',
                             settings=self.config, now=self.tick)
        server = smtp_mock()
        with patch.object(delivery.smtplib, 'SMTP_SSL', return_value=server), \
             patch('employer_notifications.collect'), patch('meeting_reminders.collect'):
            for _ in range(2):
                delivery.process_pending(self.connect, self.config, 100, self.tick)
        pairs = {(str(c.args[0]['To']), c.args[0]['From'].addresses[0].addr_spec)
                 for c in server.send_message.call_args_list}
        self.assertEqual(pairs, {('candidate@fixture.test', 'candidate@fixture.test'),
                                 ('employer@fixture.test', 'hiringmanager@fixture.test')})
        self.assertEqual(server.send_message.call_count, 2)


if __name__ == '__main__':
    unittest.main()
