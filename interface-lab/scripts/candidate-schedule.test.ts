import assert from 'node:assert/strict';
import { test } from 'node:test';
import { scheduleDateKey, scheduleDayStart, scheduleInstant, scheduleTime } from '../src/scenes/candidate-portal/schedule-time.ts';
import { availabilityForDate, validateDayIntervals } from '../src/scenes/candidate-portal/availability-model.ts';
import type { AvailabilitySchedule } from '../src/scenes/candidate-portal/api.ts';

const weekly: AvailabilitySchedule = { weekly: [{ id: 'workdays', startDate: '2026-10-19', weekdays: [0, 1, 2, 3, 4], startTime: '09:00', endTime: '18:00', timezone: 'Europe/Moscow' }], dates: [] };
const range = (startTime: string, endTime: string) => ({ startTime, endTime });

test('selected zone controls both displayed dates and instants, including fractional offsets', () => {
  assert.equal(scheduleInstant('2026-10-21', '09:00', 'Europe/Moscow'), '2026-10-21T06:00:00.000Z');
  assert.equal(scheduleInstant('2026-10-21', '09:00', 'Asia/Kathmandu'), '2026-10-21T03:15:00.000Z');
  assert.equal(scheduleDateKey('2026-12-31T22:30:00Z', 'Asia/Tokyo'), '2027-01-01');
  assert.equal(scheduleTime('2026-12-31T22:30:00Z', 'America/New_York'), '17:30');
  assert.equal(scheduleInstant('2026-10-21', '24:00', 'Europe/Moscow'), '2026-10-21T21:00:00.000Z');
});

test('invalid, missing and repeated wall times cannot be silently shifted', () => {
  for (const args of [
    ['2026-02-30', '10:00', 'UTC'], ['2026-10-21', '25:00', 'UTC'],
    ['2026-03-29', '02:30', 'Europe/Berlin'], ['2026-10-25', '02:30', 'Europe/Berlin'],
  ]) assert.throws(() => scheduleInstant(...args as [string, string, string]));
});

test('midnight DST changes and a skipped date do not crash the date picker', () => {
  assert.equal(scheduleDayStart('2026-03-08', 'America/Havana'), '2026-03-08T05:00:00.000Z');
  assert.equal(scheduleDayStart('2026-11-01', 'America/Havana'), '2026-11-01T04:00:00.000Z');
  assert.equal(scheduleDayStart('2011-12-30', 'Pacific/Apia'), scheduleDayStart('2011-12-31', 'Pacific/Apia'));
  assert.deepEqual(availabilityForDate([], weekly, '2011-12-30', 'Pacific/Apia'), []);
});

test('weekly availability remains anchored to the authored zone after display-zone changes', () => {
  assert.deepEqual(availabilityForDate([], weekly, '2026-10-21', 'Europe/Moscow'), [range('09:00', '18:00')]);
  assert.deepEqual(availabilityForDate([], weekly, '2026-10-21', 'Asia/Tokyo'), [range('15:00', '24:00')]);
  assert.deepEqual(availabilityForDate([], weekly, '2026-10-24', 'Europe/Moscow'), []);
  assert.deepEqual(availabilityForDate([], weekly, '2026-10-18', 'Europe/Moscow'), []);
});

test('date overrides replace weekly hours, support closed days and preserve the rule for restore', () => {
  const override = { date: '2026-10-21', timezone: 'Europe/Moscow', intervals: [range('12:00', '14:00'), range('18:00', '20:00')] };
  assert.deepEqual(availabilityForDate([], { ...weekly, dates: [override] }, override.date, override.timezone), override.intervals);
  assert.deepEqual(availabilityForDate([], { ...weekly, dates: [{ ...override, intervals: [] }] }, override.date, override.timezone), []);
  assert.deepEqual(availabilityForDate([], weekly, override.date, override.timezone), [range('09:00', '18:00')]);
});

test('closing one local date clips legacy overnight windows rather than removing adjacent dates', () => {
  const manual = [{ start: '2026-10-20T20:00:00Z', end: '2026-10-21T02:00:00Z' }];
  const closed = { weekly: [], dates: [{ date: '2026-10-21', timezone: 'Europe/Moscow', intervals: [] }] };
  assert.deepEqual(availabilityForDate(manual, closed, '2026-10-20', 'Europe/Moscow'), [range('23:00', '24:00')]);
  assert.deepEqual(availabilityForDate(manual, closed, '2026-10-21', 'Europe/Moscow'), []);
});

test('overrides authored in a different zone replace only their actual local day', () => {
  const schedule = { ...weekly, dates: [{ date: '2026-10-21', timezone: 'America/Los_Angeles', intervals: [] }] };
  assert.deepEqual(availabilityForDate([], schedule, '2026-10-21', 'Europe/Moscow'), [range('09:00', '10:00')]);
});

test('range validation rejects empty, too short, overlapping and reversed intervals', () => {
  for (const ranges of [[range('', '')], [range('09:00', '09:15')], [range('18:00', '09:00')], [range('09:00', '11:00'), range('10:00', '12:00')]]) {
    assert.throws(() => validateDayIntervals(ranges, '2026-10-21', 'Europe/Moscow'));
  }
  assert.deepEqual(validateDayIntervals([range('22:00', '24:00')], '2026-10-21', 'Europe/Moscow'), [range('22:00', '24:00')]);
  assert.deepEqual(validateDayIntervals([], '2026-10-21', 'Europe/Moscow'), []);
});
