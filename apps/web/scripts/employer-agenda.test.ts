import assert from 'node:assert/strict';
import test from 'node:test';
import type { Meeting, Person } from '../src/scenes/connected-journey/api.ts';
import { agendaClock, agendaFullDate, agendaMonth, employerAgendaDays } from '../src/scenes/employer-workspace/agenda-model.ts';

const NOW = Date.parse('2026-10-13T12:00:00Z');
function person(id: string, start: string, status: Meeting['status'] = 'confirmed', extra: Partial<Meeting> = {}, vacancyId = 'first'): Person {
  return {
    id, applicationId: id, candidateId: 'candidate-' + id, vacancyId, vacancyRole: vacancyId,
    name: id, interest: 'accepted', auto: false, freeSlots: [], afterInterest: 'pending',
    decision: 'review', offer: null, messages: [],
    meeting: { id: 'meeting-' + id, start, status, testSkip: false, joined: [], ...extra },
  };
}

test('global agenda retains people and exact identities from different vacancies in chronological order', () => {
  const one = person('one', '2026-10-14T12:00:00Z');
  const two = person('two', '2026-10-14T11:00:00Z', 'pending', {}, 'second');
  const days = employerAgendaDays([one, two], NOW);
  assert.equal(days.length, 1);
  assert.deepEqual(days[0].meetings.map(row => [row.person.id, row.person.vacancyId, row.meeting.id]), [
    ['two', 'second', 'meeting-two'], ['one', 'first', 'meeting-one'],
  ]);
  assert.equal(days[0].meetings[0].person, two);
});

test('past, cancelled and completed appointments leave the agenda without losing an active call', () => {
  const rows = [
    person('past', '2026-10-13T10:00:00Z'),
    person('cancelled', '2026-10-14T10:00:00Z', 'cancelled'),
    person('completed', '2026-10-14T10:00:00Z', 'completed'),
    person('live', '2026-10-13T10:00:00Z', 'live'),
    person('pending', '2026-10-13T11:50:00Z', 'pending'),
    person('future', '2026-10-14T10:00:00Z'),
  ];
  assert.deepEqual(employerAgendaDays(rows, NOW).flatMap(day => day.meetings.map(row => row.person.id)), ['live', 'pending', 'future']);
});

test('server end controls expiry and legacy meetings have a 30-minute duration', () => {
  const legacy = person('legacy', '2026-10-13T11:30:00Z');
  const longer = person('longer', legacy.meeting!.start, 'confirmed', { end: '2026-10-13T12:15:00Z' });
  const rows = employerAgendaDays([legacy, longer], NOW).flatMap(day => day.meetings);
  assert.deepEqual(rows.map(row => row.person.id), ['longer']);
  assert.equal(rows[0].end, Date.parse('2026-10-13T12:15:00Z'));
});

test('Moscow midnight and month/year labels agree even when the browser uses a different timezone', () => {
  const rows = [
    person('january', '2026-12-31T21:10:00Z'),
    person('november', '2026-10-31T21:10:00Z'),
    person('october', '2026-10-31T20:10:00Z'),
    person('december', '2026-12-31T20:10:00Z'),
  ];
  const days = employerAgendaDays(rows, NOW);
  assert.deepEqual(days.map(day => day.date), ['2026-10-31', '2026-11-01', '2026-12-31', '2027-01-01']);
  assert.equal(agendaClock.format(days[1].meetings[0].start), '00:10');
  assert.match(agendaMonth.format(days[3].meetings[0].start), /январь 2027/);
  assert.match(agendaFullDate.format(days[3].meetings[0].start), /1 января 2027/);
});

test('invalid time data and no appointment produce an empty state instead of a broken date', () => {
  const absent = { ...person('absent', ''), meeting: null };
  assert.deepEqual(employerAgendaDays([absent, person('invalid', 'not-a-date'), person('reversed', '2026-10-14T12:00:00Z', 'confirmed', { end: '2026-10-14T11:00:00Z' })], NOW), []);
});
