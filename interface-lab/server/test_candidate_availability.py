import copy
import datetime as dt
import unittest

import candidate_availability as availability
from domain import Problem, timestamp


class CandidateAvailabilityTest(unittest.TestCase):
    def setUp(self):
        self.tick = timestamp('2026-10-21T00:00:00Z')
        self.rule = {'id': 'workdays', 'startDate': '2026-10-19', 'weekdays': [0, 1, 2, 3, 4],
                     'startTime': '09:00', 'endTime': '18:00', 'timezone': 'Europe/Moscow'}
        self.schedule = {'weekly': [self.rule], 'dates': []}

    def projected(self, schedule=None, manual=None):
        return availability.project({'availability': manual or [], 'availabilitySchedule': schedule or self.schedule}, self.tick)['availability']

    def test_rolling_rules_are_persisted_not_a_fixed_batch_of_slots(self):
        result = self.projected()
        self.assertEqual(len(result), 20)
        self.assertEqual(timestamp(result[0]['start']), timestamp('2026-10-21T06:00:00Z'))
        self.assertEqual(timestamp(result[0]['end']), timestamp('2026-10-21T15:00:00Z'))
        profile = {'availability': [], 'availabilitySchedule': self.schedule}
        next_month = availability.project(profile, self.tick + dt.timedelta(days=35))['availability']
        self.assertGreater(timestamp(next_month[0]['start']), timestamp(result[-1]['end']))
        self.assertEqual(profile['availability'], [])

    def test_overrides_replace_rules_and_restoring_keeps_weekly_hours(self):
        override = {'date': '2026-10-21', 'timezone': 'Europe/Moscow', 'intervals': [{'startTime': '12:00', 'endTime': '14:00'}]}
        changed = self.projected({**self.schedule, 'dates': [override]})
        self.assertEqual(timestamp(changed[0]['start']), timestamp('2026-10-21T09:00:00Z'))
        self.assertEqual(len(changed), 20)
        closed = self.projected({**self.schedule, 'dates': [{**override, 'intervals': []}]})
        self.assertEqual(len(closed), 19)
        self.assertEqual(len(self.projected()), 20)

    def test_manual_overnight_intervals_survive_closing_an_adjacent_date(self):
        manual = [{'start': '2026-10-20T20:00:00Z', 'end': '2026-10-21T02:00:00Z'}]
        result = self.projected({'weekly': [], 'dates': [{'date': '2026-10-21', 'timezone': 'Europe/Moscow', 'intervals': []}]}, manual)
        self.assertEqual(timestamp(result[0]['start']), timestamp(manual[0]['start']))
        self.assertEqual(timestamp(result[0]['end']), timestamp('2026-10-20T21:00:00Z'))
        self.assertEqual(manual[0]['end'], '2026-10-21T02:00:00Z')

    def test_display_zone_does_not_change_the_stored_schedule(self):
        profile = {'availability': [], 'availabilitySchedule': self.schedule}
        before = availability.project({**profile, 'timezone': 'Europe/Moscow'}, self.tick)
        after = availability.project({**profile, 'timezone': 'Asia/Tokyo'}, self.tick)
        self.assertEqual(before['availability'], after['availability'])
        self.assertEqual(after['availabilityManual'], [])
        self.assertEqual(availability.project(after, self.tick)['availability'], before['availability'])

    def test_cross_zone_override_replaces_only_its_day(self):
        result = self.projected({**self.schedule, 'dates': [{'date': '2026-10-21', 'timezone': 'America/Los_Angeles', 'intervals': []}]})
        self.assertEqual(timestamp(result[0]['end']), timestamp('2026-10-21T07:00:00Z'))

    def test_dst_slots_are_rejected_or_skipped_not_silently_moved(self):
        for date in ('2026-03-29', '2026-10-25'):
            interval = {'startTime': '02:30', 'endTime': '04:00'}
            self.assertIsNone(availability.window(date, interval, 'Europe/Berlin'))
            with self.assertRaises(Problem):
                availability.validate({'weekly': [], 'dates': [{'date': date, 'timezone': 'Europe/Berlin', 'intervals': [interval]}]})
        schedule = {'weekly': [{**self.rule, 'timezone': 'Europe/Berlin', 'weekdays': [6], 'startTime': '02:30', 'endTime': '04:00'}], 'dates': []}
        result = availability.project({'availability': [], 'availabilitySchedule': schedule}, timestamp('2026-10-25T00:00:00Z'))
        self.assertEqual(len(result['availability']), 3)
        valid = availability.window('2026-10-21', {'startTime': '22:00', 'endTime': '24:00'}, 'Europe/Moscow')
        self.assertEqual(timestamp(valid['end']), timestamp('2026-10-21T21:00:00Z'))

    def test_invalid_payloads_fail_as_client_errors(self):
        changes = [{'timezone': 'No/SuchZone'}, {'weekdays': [True]}, {'weekdays': [1, 1]}, {'startDate': '2026-02-30'}, {'endTime': '08:00'}, {'id': 'bad id'}, {'startTime': '24:00'}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(Problem) as caught:
                availability.validate({'weekly': [{**self.rule, **change}], 'dates': []})
            self.assertEqual(caught.exception.status, 400)
        with self.assertRaises(Problem):
            availability.validate({'weekly': [self.rule, self.rule], 'dates': []})
        good = copy.deepcopy(self.schedule)
        self.assertEqual(availability.validate(good), self.schedule)
        self.assertEqual(good, self.schedule)


if __name__ == '__main__':
    unittest.main()
