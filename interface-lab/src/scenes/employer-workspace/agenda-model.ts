import type { Meeting, Person } from '../connected-journey/api';

export const employerAgendaTimezone = 'Europe/Moscow';
export const agendaClock = new Intl.DateTimeFormat('ru-RU', { timeZone: employerAgendaTimezone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
export const agendaMonth = new Intl.DateTimeFormat('ru-RU', { timeZone: employerAgendaTimezone, month: 'long', year: 'numeric' });
export const agendaFullDate = new Intl.DateTimeFormat('ru-RU', { timeZone: employerAgendaTimezone, weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
export const agendaDayNumber = new Intl.DateTimeFormat('ru-RU', { timeZone: employerAgendaTimezone, day: 'numeric' });
export const agendaWeekday = new Intl.DateTimeFormat('ru-RU', { timeZone: employerAgendaTimezone, weekday: 'short' });
const dayKey = new Intl.DateTimeFormat('sv-SE', { timeZone: employerAgendaTimezone, year: 'numeric', month: '2-digit', day: '2-digit' });

export type AgendaMeeting = { person: Person; meeting: Meeting; start: number; end: number };
export type AgendaDay = { date: string; meetings: AgendaMeeting[] };

/** The API authorizes and aggregates the workspace; the view never scopes by the open vacancy. */
export function employerAgendaDays(people: Person[], now = Date.now()): AgendaDay[] {
  const rows = people.flatMap(person => {
    const meeting = person.meeting;
    if (!meeting || !['pending', 'confirmed', 'live'].includes(meeting.status)) return [];
    const start = Date.parse(meeting.start);
    // Legacy journeys do not carry end; their agreed interview length is 30 minutes.
    const end = meeting.end ? Date.parse(meeting.end) : start + 30 * 60_000;
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return [];
    // A running call must remain reachable even if its booked time has elapsed.
    if (meeting.status !== 'live' && end <= now) return [];
    return [{ person, meeting, start, end }];
  }).sort((a, b) => a.start - b.start || a.meeting.id.localeCompare(b.meeting.id));

  const groups = new Map<string, AgendaMeeting[]>();
  rows.forEach(row => {
    const key = dayKey.format(row.start);
    const group = groups.get(key);
    if (group) group.push(row); else groups.set(key, [row]);
  });
  return [...groups].map(([date, meetings]) => ({ date, meetings }));
}
