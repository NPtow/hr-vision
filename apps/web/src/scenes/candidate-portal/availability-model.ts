import type { Availability, AvailabilitySchedule, CandidateProfile, DayAvailability } from './api';
import { scheduleDateKey, scheduleDayStart, scheduleInstant, scheduleTime } from './schedule-time.ts';

/** First-time setup, not a check for free slots today. A saved closed day is also a deliberate choice. */
export function hasConfiguredAvailability(profile: Pick<CandidateProfile, 'availability' | 'availabilityManual' | 'availabilitySchedule'>): boolean {
  return (profile.availabilityManual ?? profile.availability).length > 0
    || (profile.availabilitySchedule?.weekly.length ?? 0) > 0
    || (profile.availabilitySchedule?.dates.length ?? 0) > 0;
}

export const shiftScheduleDay = (day: string, offset: number) => new Date(Date.parse(`${day}T12:00:00Z`) + offset * 86400_000).toISOString().slice(0, 10);
export const scheduleWeekday = (day: string) => (new Date(`${day}T12:00:00Z`).getUTCDay() + 6) % 7;

function makeWindow(day: string, range: DayAvailability, zone: string): Availability | null {
  try {
    const start = scheduleInstant(day, range.startTime, zone), end = scheduleInstant(day, range.endTime, zone);
    const minutes = (Date.parse(end) - Date.parse(start)) / 60000;
    return minutes >= 30 && minutes <= 720 ? { start, end } : null;
  }
  catch { return null; } // Same rule as backend: skip nonexistent/repeated DST slots.
}

/** Preview an arbitrary date without materializing a year's worth of slots in every profile response. */
export function availabilityForDate(manual: Availability[], schedule: AvailabilitySchedule, day: string, timezone: string): DayAvailability[] {
  const start = scheduleDayStart(day, timezone), end = scheduleDayStart(shiftScheduleDay(day, 1), timezone);
  if (start === end) return [];
  let windows = [...manual];
  for (const rule of schedule.weekly) {
    const anchor = scheduleDateKey((Date.parse(start) + Date.parse(end)) / 2, rule.timezone);
    for (const offset of [-1, 0, 1]) {
      const key = shiftScheduleDay(anchor, offset);
      if (key >= rule.startDate && rule.weekdays.includes(scheduleWeekday(key))) {
        const window = makeWindow(key, rule, rule.timezone);
        if (window) windows.push(window);
      }
    }
  }
  for (const override of schedule.dates) {
    const a = scheduleDayStart(override.date, override.timezone), b = scheduleDayStart(shiftScheduleDay(override.date, 1), override.timezone);
    if (b <= start || a >= end) continue;
    windows = windows.flatMap(window => {
      const left = new Date(window.start).toISOString(), right = new Date(window.end).toISOString();
      if (right <= a || left >= b) return [window];
      return [...(left < a ? [{ start: left, end: a }] : []), ...(right > b ? [{ start: b, end: right }] : [])];
    });
    windows.push(...override.intervals.flatMap(range => { const window = makeWindow(override.date, range, override.timezone); return window ? [window] : []; }));
  }
  const merged: Availability[] = [];
  for (const window of windows.map(w => ({ start: new Date(w.start).toISOString(), end: new Date(w.end).toISOString() }))
    .filter(w => w.start < end && w.end > start).map(w => ({ start: w.start < start ? start : w.start, end: w.end > end ? end : w.end }))
    .sort((a, b) => a.start.localeCompare(b.start))) {
    const last = merged.at(-1);
    if (last && window.start <= last.end) { if (window.end > last.end) last.end = window.end; }
    else merged.push({ ...window });
  }
  return merged.map(window => ({ startTime: scheduleTime(window.start, timezone), endTime: window.end === end ? '24:00' : scheduleTime(window.end, timezone) }));
}

export function validateDayIntervals(intervals: DayAvailability[], day: string, timezone: string): DayAvailability[] {
  const sorted = [...intervals].sort((a, b) => a.startTime.localeCompare(b.startTime));
  for (const [index, row] of sorted.entries()) {
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(row.startTime)) throw new Error('Проверьте время начала.');
    const wallMinutes = (time: string) => Number(time.slice(0, 2)) * 60 + Number(time.slice(3));
    const wallDuration = wallMinutes(row.endTime) - wallMinutes(row.startTime);
    const minutes = (Date.parse(scheduleInstant(day, row.endTime, timezone)) - Date.parse(scheduleInstant(day, row.startTime, timezone))) / 60000;
    if (minutes < 30 || minutes > 720 || wallDuration < 30 || wallDuration > 720) throw new Error('Интервал должен длиться от 30 минут до 12 часов.');
    if (index > 0 && sorted[index - 1].endTime > row.startTime) throw new Error('Интервалы не должны пересекаться.');
  }
  return sorted;
}
