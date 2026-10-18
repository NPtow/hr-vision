/** Calendar projections use instants from the server, never recruiter identities. */
export type BookingSlot = { id: string; start: string; end: string };
export type BookingDay = { key: string; date: Date; slots: BookingSlot[] };
export const calendarKey = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
export const dateFromKey = (key: string) => new Date(`${key}T12:00:00`);
export function slotLabel(value: string, timezone: string) {
  return new Intl.DateTimeFormat('ru-RU', { timeZone: timezone, hour: '2-digit', minute: '2-digit' }).format(new Date(value));
}
export function bookingDays(slots: BookingSlot[], timezone: string, now = Date.now()): BookingDay[] {
  const formatter = new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' });
  const groups = new Map<string, BookingDay>();
  const seen = new Set<string>();
  for (const slot of [...slots].sort((a, b) => Date.parse(a.start) - Date.parse(b.start) || a.id.localeCompare(b.id))) {
    const start = Date.parse(slot.start), end = Date.parse(slot.end);
    if (!Number.isFinite(start) || !Number.isFinite(end) || start <= now || end <= start) continue;
    const interval = `${start}/${end}`;
    if (seen.has(interval)) continue;
    seen.add(interval);
    const parts = Object.fromEntries(formatter.formatToParts(start).map(part => [part.type, part.value]));
    const key = `${parts.year}-${parts.month}-${parts.day}`;
    const group = groups.get(key) ?? { key, date: dateFromKey(key), slots: [] };
    group.slots.push(slot); groups.set(key, group);
  }
  return [...groups.values()].sort((a, b) => a.key.localeCompare(b.key));
}
