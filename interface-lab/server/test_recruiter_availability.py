"""Recurring calendar checks against SQLite; no seeded people or provider calls."""
import datetime as dt
import sqlite3
import unittest
from unittest.mock import patch

from domain import Problem, timestamp
import recruiter_availability as availability


class RecruiterAvailabilityTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('CREATE TABLE cp_candidates (id TEXT PRIMARY KEY)')
        self.db.execute('''CREATE TABLE cp_recruiter_slots (
          id TEXT PRIMARY KEY, start TEXT NOT NULL, end TEXT NOT NULL,
          recruiter_name TEXT NOT NULL, candidate_id TEXT UNIQUE REFERENCES cp_candidates(id))''')
        self.db.execute('CREATE TABLE rc_accounts (id TEXT PRIMARY KEY)')
        self.db.execute('''CREATE TABLE cp_interviews (
          id TEXT PRIMARY KEY, slot_id TEXT REFERENCES cp_recruiter_slots(id))''')
        availability.init(self.db)
        self.db.commit()
        self.clock = patch('recruiter_availability.now', return_value=timestamp('2026-10-11T12:00:00Z'))
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.addCleanup(self.db.close)

    @staticmethod
    def payload(**changes):
        return {'recruiterName': 'Никита', 'startDate': '2026-10-12',
                'weekdays': [0, 1, 2, 3, 4], 'startTime': '16:00', 'endTime': '17:00',
                'timezone': 'Europe/Moscow', **changes}

    def add(self, **changes):
        return availability.add_block(self.db, self.payload(**changes))

    def slots(self):
        return self.db.execute('SELECT id,start,end,recruiter_name,candidate_id FROM cp_recruiter_slots ORDER BY start,id').fetchall()

    def assert_problem(self, status, payload):
        with self.assertRaises(Problem) as caught:
            availability.add_block(self.db, payload)
        self.assertEqual(caught.exception.status, status)

    def test_initialization_does_not_seed_or_modify_existing_slots(self):
        before = self.db.execute('PRAGMA table_info(cp_recruiter_slots)').fetchall()
        availability.init(self.db)
        self.assertEqual(availability.blocks(self.db), [])
        self.assertEqual(availability.materialize(self.db), 0)
        self.assertEqual(self.slots(), [])
        self.assertEqual(before, self.db.execute('PRAGMA table_info(cp_recruiter_slots)').fetchall())

    def test_rejects_invalid_or_mutable_block_inputs(self):
        invalid = [None, {}, {**self.payload(), 'id': 'client-chosen'},
                   self.payload(recruiterName='  '), self.payload(recruiterName=7),
                   self.payload(startDate='2026-02-30'), self.payload(startDate='2026-1-2'),
                   self.payload(startDate=None), self.payload(startDate='9999-12-31'),
                   self.payload(timezone='UTC'), self.payload(weekdays=[]),
                   self.payload(weekdays=[True]), self.payload(weekdays=[0, 7]),
                   self.payload(weekdays='0'), self.payload(weekdays=[0, 1.5]),
                   self.payload(startTime='16:15'), self.payload(endTime='24:00'),
                   self.payload(startTime='16:30', endTime='16:00'),
                   self.payload(startTime='16:00', endTime='16:00'),
                   self.payload(startTime='00:00', endTime='12:30')]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assert_problem(400, payload)
        self.assertEqual(availability.blocks(self.db), [])

    def test_exact_semantic_repeat_returns_original_immutable_block(self):
        first = self.add(recruiterName='  Алёна   Иванова ', weekdays=[2, 0, 1])
        second = self.add(recruiterName='АЛЕНА ИВАНОВА', weekdays=[1, 2, 0])
        self.assertEqual(first, second)
        self.assertEqual(first['recruiterName'], 'Алёна Иванова')
        self.assertEqual(first['weekdays'], [0, 1, 2])
        self.assertEqual(len(availability.blocks(self.db)), 1)

    def test_future_recurrences_cannot_overlap_same_normalized_recruiter(self):
        self.add(recruiterName='Алёна Иванова')
        self.assert_problem(409, self.payload(recruiterName=' АЛЕНА   ИВАНОВА ',
                                              startDate='2026-12-01', weekdays=[0],
                                              startTime='16:30', endTime='18:00'))
        self.assertEqual(len(availability.blocks(self.db)), 1)

    def test_adjacent_time_other_weekdays_and_other_recruiters_are_allowed(self):
        self.add()
        self.add(startTime='17:00', endTime='18:00')
        self.add(weekdays=[5, 6])
        self.add(recruiterName='Другой рекрутер')
        self.assertEqual(len(availability.blocks(self.db)), 4)

    def test_per_recruiter_block_limit_still_allows_duplicate_and_other_recruiter(self):
        for index in range(20):
            start = dt.datetime(2026, 10, 12) + index * availability.STEP
            self.add(weekdays=[0], startTime=start.strftime('%H:%M'),
                     endTime=(start + availability.STEP).strftime('%H:%M'))
        duplicate = self.add(weekdays=[0], startTime='00:00', endTime='00:30')
        self.assertIsNotNone(duplicate['id'])
        self.assert_problem(409, self.payload(weekdays=[0], startTime='10:00', endTime='10:30'))
        self.add(recruiterName='Другой рекрутер')
        self.assertEqual(len(availability.blocks(self.db)), 21)

    def test_weekday_block_expands_to_twenty_business_days_in_28_calendar_days(self):
        block = self.add()
        self.assertEqual(availability.materialize(self.db), 40)
        slots = self.slots()
        self.assertEqual(slots[0][1:3], ('2026-10-12T13:00:00Z', '2026-10-12T13:30:00Z'))
        self.assertEqual(slots[-1][1:3], ('2026-11-06T13:30:00Z', '2026-11-06T14:00:00Z'))
        for slot in slots:
            self.assertIn(timestamp(slot[1]).astimezone(availability.MOSCOW).weekday(), range(5))
            self.assertEqual(timestamp(slot[2]) - timestamp(slot[1]), availability.STEP)
            self.assertIsNone(slot[4])
        associated = self.db.execute('SELECT COUNT(*) FROM cp_generated_slots WHERE block_id=?', (block['id'],)).fetchone()[0]
        self.assertEqual(associated, 40)

    def test_today_uses_moscow_midnight_and_started_slots_are_not_offered(self):
        # UTC Sunday is already Monday locally. The 00:00 slot has started.
        self.now.return_value = timestamp('2026-10-11T21:15:00Z')
        self.add(startDate='2026-10-01', weekdays=[0], startTime='00:00', endTime='01:00')
        self.assertEqual(availability.materialize(self.db), 7)
        slots = self.slots()
        self.assertEqual(slots[0][1], '2026-10-11T21:30:00Z')
        self.assertEqual(slots[-1][1], '2026-11-01T21:30:00Z')
        self.assertTrue(all(timestamp(slot[1]) > self.now.return_value for slot in slots))

    def test_slot_at_exactly_now_is_excluded(self):
        self.now.return_value = timestamp('2026-10-12T13:00:00Z')
        self.add(weekdays=[0])
        self.assertEqual(availability.materialize(self.db), 7)
        self.assertEqual(self.slots()[0][1], '2026-10-12T13:30:00Z')

    def test_future_start_has_its_own_bounded_28_day_horizon(self):
        self.add(startDate='2026-10-20', weekdays=list(range(7)))
        self.assertEqual(availability.materialize(self.db), 56)
        slots = self.slots()
        self.assertEqual(slots[0][1], '2026-10-20T13:00:00Z')
        self.assertEqual(slots[-1][1], '2026-11-16T13:30:00Z')

    def test_repeated_materialization_keeps_ids_and_rolls_forward_without_deletion(self):
        self.now.return_value = timestamp('2026-10-12T00:00:00Z')
        self.add(weekdays=list(range(7)))
        self.assertEqual(availability.materialize(self.db), 56)
        original = self.slots()
        self.assertEqual(availability.materialize(self.db), 0)
        self.assertEqual(self.slots(), original)
        self.now.return_value += dt.timedelta(days=7)
        self.assertEqual(availability.materialize(self.db), 14)
        later = self.slots()
        self.assertEqual(len(later), 70)
        self.assertEqual(later[:56], original)
        self.assertEqual(later[-1][1], '2026-11-15T13:30:00Z')

    def test_booked_and_manual_overlap_are_preserved_even_off_half_hour_grid(self):
        self.db.execute("INSERT INTO cp_candidates VALUES ('candidate-a')")
        self.db.execute('INSERT INTO cp_recruiter_slots VALUES (?,?,?,?,?)',
                        ('booked', '2026-10-12T13:15:00Z', '2026-10-12T13:45:00Z', ' НИКИТА ', 'candidate-a'))
        self.db.execute('INSERT INTO cp_recruiter_slots VALUES (?,?,?,?,NULL)',
                        ('manual', '2026-10-13T13:00:00Z', '2026-10-13T13:30:00Z', 'Никита'))
        self.db.execute('INSERT INTO cp_recruiter_slots VALUES (?,?,?,?,NULL)',
                        ('history', '2026-10-01T13:00:00Z', '2026-10-01T13:30:00Z', 'Никита'))
        before = self.slots()
        self.add()
        self.assertEqual(availability.materialize(self.db), 37)
        slots = self.slots()
        self.assertTrue(all(row in slots for row in before))
        self.assertEqual(self.db.execute("SELECT candidate_id FROM cp_recruiter_slots WHERE id='booked'").fetchone()[0], 'candidate-a')
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM cp_generated_slots WHERE slot_id IN ('booked','manual','history')").fetchone()[0], 0)

    def test_newly_booked_generated_slot_is_never_replaced(self):
        self.add()
        availability.materialize(self.db)
        slot = self.slots()[0]
        self.db.execute("INSERT INTO cp_candidates VALUES ('candidate-a')")
        self.db.execute('UPDATE cp_recruiter_slots SET candidate_id=? WHERE id=?', ('candidate-a', slot[0]))
        self.assertEqual(availability.materialize(self.db), 0)
        self.assertEqual(self.slots()[0], (*slot[:4], 'candidate-a'))

    def test_different_recruiters_can_have_the_same_time(self):
        self.add()
        self.add(recruiterName='Мария')
        self.assertEqual(availability.materialize(self.db), 80)
        self.assertEqual(len({row[0] for row in self.slots()}), 80)

    def test_init_add_and_materialize_leave_transaction_to_caller(self):
        self.db.execute("INSERT INTO cp_candidates VALUES ('uncommitted')")
        availability.init(self.db)
        self.add()
        availability.materialize(self.db)
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM cp_candidates').fetchone()[0], 0)
        self.assertEqual(availability.blocks(self.db), [])
        self.assertEqual(self.slots(), [])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM cp_generated_slots').fetchone()[0], 0)

    def test_block_and_slot_ids_are_stable_across_equivalent_name_spellings(self):
        self.add(recruiterName='Алёна Иванова')
        availability.materialize(self.db)
        before = [(row[0], row[1]) for row in self.slots()]
        self.db.rollback()
        self.add(recruiterName='АЛЕНА  ИВАНОВА')
        availability.materialize(self.db)
        self.assertEqual([(row[0], row[1]) for row in self.slots()], before)

    def owned(self, owner='one', **changes):
        self.db.execute('INSERT OR IGNORE INTO rc_accounts VALUES (?)', (owner,))
        return availability.add_block(self.db, self.payload(**changes), recruiter={'id': owner, 'name': 'Никита'})

    def edit(self, block, owner='one', **changes):
        return availability.add_block(
            self.db, {**{key: block[key] for key in availability.FIELDS}, 'blockId': block['id'], **changes},
            recruiter={'id': owner, 'name': 'Никита'})

    def snapshot(self):
        return {table: self.db.execute('SELECT * FROM ' + table + ' ORDER BY 1').fetchall()
                for table in ('cp_availability_blocks', 'cp_generated_slots', 'cp_recruiter_slots',
                              'cp_recruiter_block_owners', 'cp_recruiter_slot_owners', 'cp_interviews')}

    def test_owned_edit_and_repeated_request_keep_block_and_matching_slot_ids(self):
        block = self.owned()
        availability.materialize(self.db)
        original = self.snapshot()
        self.assertEqual(self.edit(block), block)
        self.assertEqual(availability.materialize(self.db), 0)
        self.assertEqual(self.snapshot(), original)
        edited = self.edit(block, startTime='16:30', endTime='17:30')
        availability.materialize(self.db)
        self.assertEqual(edited['id'], block['id'])
        self.assertEqual(edited['createdAt'], block['createdAt'])
        old_matching = {row for row in original['cp_recruiter_slots'] if row[1][11:16] == '13:30'}
        self.assertTrue(old_matching.issubset(set(self.slots())))
        self.assertTrue(all(row[1][11:16] in ('13:30', '14:00') for row in self.slots()))
        after = self.snapshot()
        self.edit(block, startTime='16:30', endTime='17:30')
        self.assertEqual(availability.materialize(self.db), 0)
        self.assertEqual(self.snapshot(), after)
        self.assertEqual(self.db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_edit_preserves_bookings_history_manual_slots_and_other_recruiters(self):
        block = self.owned()
        other = self.owned('two')
        availability.materialize(self.db)
        # A first interview already happened; the second time remains booked.
        mine = self.db.execute('''SELECT s.id FROM cp_recruiter_slots s JOIN cp_recruiter_slot_owners o
          ON o.slot_id=s.id WHERE o.recruiter_id='one' ORDER BY s.start''').fetchall()
        past, booked = mine[0][0], mine[1][0]
        self.db.execute("INSERT INTO cp_candidates VALUES ('candidate-a')")
        self.db.execute('UPDATE cp_recruiter_slots SET candidate_id=? WHERE id=?', ('candidate-a', booked))
        self.db.execute('INSERT INTO cp_interviews VALUES (?,?)', ('interview-a', booked))
        self.db.execute('INSERT INTO cp_recruiter_slots VALUES (?,?,?,?,NULL)',
                        ('manual-owned', '2026-10-13T16:00:00Z', '2026-10-13T16:30:00Z', 'Никита'))
        self.db.execute('INSERT INTO cp_recruiter_slot_owners VALUES (?,?)', ('manual-owned', 'one'))
        self.now.return_value = timestamp('2026-10-12T13:15:00Z')
        before = self.snapshot()
        self.edit(block, startTime='18:00', endTime='19:00')
        availability.materialize(self.db)
        protected_ids = {past, booked, 'manual-owned'} | {row[0] for row in before['cp_recruiter_slot_owners'] if row[1] == 'two'}
        self.assertTrue({row for row in before['cp_recruiter_slots'] if row[0] in protected_ids}.issubset(set(self.slots())))
        self.assertIn((booked, block['id']), self.snapshot()['cp_generated_slots'])
        self.assertIn((other['id'], 'two'), self.snapshot()['cp_recruiter_block_owners'])
        self.assertEqual(self.db.execute('PRAGMA foreign_key_check').fetchall(), [])
        # Releasing that booking after the edit must not expose retired times.
        self.db.execute('DELETE FROM cp_interviews WHERE id=?', ('interview-a',))
        self.db.execute('UPDATE cp_recruiter_slots SET candidate_id=NULL WHERE id=?', (booked,))
        availability.materialize(self.db)
        self.assertNotIn(booked, {row[0] for row in self.slots()})
        self.assertIn(past, {row[0] for row in self.slots()})

    def test_edit_conflict_or_foreign_owner_leaves_every_row_unchanged(self):
        block = self.owned()
        self.owned(startTime='18:00', endTime='19:00')
        self.owned('two')
        availability.materialize(self.db)
        before = self.snapshot()
        for owner, changes, status in [('two', {'startTime': '17:00', 'endTime': '18:00'}, 404),
                                       ('one', {'startTime': '18:00', 'endTime': '19:00'}, 409),
                                       ('one', {'weekdays': []}, 400)]:
            with self.subTest(owner=owner, changes=changes):
                with self.assertRaises(Problem) as raised:
                    self.edit(block, owner, **changes)
                self.assertEqual(raised.exception.status, status)
                self.assertEqual(self.snapshot(), before)
        with self.assertRaises(Problem) as raised:
            availability.add_block(self.db, self.payload(blockId=block['id']))
        self.assertEqual(raised.exception.status, 401)
        self.assertEqual(self.snapshot(), before)

    def test_recreating_original_schedule_after_edit_does_not_reuse_edited_block_id(self):
        block = self.owned()
        self.edit(block, startTime='18:00', endTime='19:00')
        recreated = self.owned()
        self.assertNotEqual(recreated['id'], block['id'])
        self.assertEqual(self.owned(), recreated)
        availability.materialize(self.db)
        self.assertEqual(len(self.slots()), 80)

    def test_transaction_rollback_restores_edit_and_reconciled_slots(self):
        block = self.owned()
        availability.materialize(self.db)
        self.db.commit()
        before = self.snapshot()
        self.db.execute('BEGIN IMMEDIATE')
        self.edit(block, startTime='18:00', endTime='19:00')
        availability.materialize(self.db)
        self.db.rollback()
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
