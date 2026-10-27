/**
 * Calendar export helpers: Google Calendar URL and RFC 5545 .ics generator.
 * Standard format without heavy dependencies.
 */

export interface CalendarEventDetails {
  title: string;
  description: string;
  location?: string;
  start: Date;
  end: Date;
}

function formatUtcDate(date: Date): string {
  return date.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');
}

export function googleCalendarUrl(event: CalendarEventDetails): string {
  const url = new URL('https://calendar.google.com/calendar/render');
  url.searchParams.set('action', 'TEMPLATE');
  url.searchParams.set('text', event.title);
  url.searchParams.set('dates', `${formatUtcDate(event.start)}/${formatUtcDate(event.end)}`);
  url.searchParams.set('details', event.description);
  if (event.location) {
    url.searchParams.set('location', event.location);
  }
  return url.toString();
}

export function generateIcsContent(event: CalendarEventDetails): string {
  const now = formatUtcDate(new Date());
  const start = formatUtcDate(event.start);
  const end = formatUtcDate(event.end);
  const uid = `interview-${start}-${Math.random().toString(36).slice(2, 9)}@mashadasha.tech`;

  const cleanDescription = event.description.replace(/\n/g, '\\n');

  return [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//MashaDasha//HR Vision Calendar//RU',
    'CALSCALE:GREGORIAN',
    'METHOD:PUBLISH',
    'BEGIN:VEVENT',
    `UID:${uid}`,
    `DTSTAMP:${now}`,
    `DTSTART:${start}`,
    `DTEND:${end}`,
    `SUMMARY:${event.title}`,
    `DESCRIPTION:${cleanDescription}`,
    event.location ? `LOCATION:${event.location}` : '',
    'STATUS:CONFIRMED',
    'BEGIN:VALARM',
    'TRIGGER:-PT60M',
    'ACTION:DISPLAY',
    'DESCRIPTION:Напоминание о встрече через 1 час',
    'END:VALARM',
    'END:VEVENT',
    'END:VCALENDAR',
  ].filter(Boolean).join('\r\n');
}

export function downloadIcsFile(event: CalendarEventDetails, filename = 'interview-mashadasha.ics'): void {
  const content = generateIcsContent(event);
  const blob = new Blob([content], { type: 'text/calendar;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}
