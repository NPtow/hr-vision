import assert from 'node:assert/strict';
import test from 'node:test';
import { bookingDays, dateFromKey, calendarKey, slotLabel } from '../src/scenes/candidate-portal/pooled-booking-model.ts';

const now = Date.parse('2026-10-31T19:00:00Z');
const slot = (id: string, start: string, end = new Date(Date.parse(start) + 1800000).toISOString()) => ({ id, start, end });
test('one time per interval, ordered days, excludes expired or invalid intervals', () => {
  const rows = [slot('b', '2026-10-31T22:00:00Z'), slot('a', '2026-10-31T20:00:00Z'),
    slot('duplicate', '2026-10-31T23:00:00+03:00'), slot('past', '2026-10-30T12:00:00Z'),
    { id: 'invalid', start: 'broken', end: 'broken' }, slot('reversed', '2026-10-31T21:00:00Z', '2026-10-31T20:00:00Z')];
  const days = bookingDays(rows, 'Europe/Moscow', now);
  assert.deepEqual(days.map(day => [day.key, day.slots.map(slot => slot.id)]), [['2026-10-31', ['a']], ['2026-11-01', ['b']]]);
});
test('timezone changes both the day and clock while preserving the server identity', () => {
  const rows = [slot('window-1', '2026-12-31T21:30:00Z')];
  assert.equal(bookingDays(rows, 'Europe/Moscow', now)[0].key, '2027-01-01');
  assert.equal(bookingDays(rows, 'Europe/Kaliningrad', now)[0].key, '2026-12-31');
  assert.equal(slotLabel(rows[0].start, 'Europe/Moscow'), '00:30');
  assert.equal(slotLabel(rows[0].start, 'Europe/Kaliningrad'), '23:30');
  assert.equal(bookingDays(rows, 'Europe/Moscow', now)[0].slots[0].id, 'window-1');
  assert.equal(calendarKey(dateFromKey('2027-01-01')), '2027-01-01');
});
test('daylight saving uses the actual offset at the appointment', () => {
  assert.equal(slotLabel('2026-10-25T00:30:00Z', 'Europe/Berlin'), '02:30');
  assert.equal(slotLabel('2026-10-25T02:30:00Z', 'Europe/Berlin'), '03:30');
  assert.deepEqual(bookingDays([], 'Europe/Moscow', now), []);
});
