/** Calendar dates are wall dates in the chosen IANA zone, never the device zone. */
const formatters = new Map<string, Intl.DateTimeFormat>();
const parts = (value: string | number | Date, timezone: string) => {
  if (!formatters.has(timezone)) formatters.set(timezone, new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit',
    minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  }));
  return Object.fromEntries(formatters.get(timezone)!.formatToParts(new Date(value)).map(part => [part.type, part.value]));
};

export function scheduleDateKey(value: string | number | Date, timezone: string): string {
  const p = parts(value, timezone);
  return `${p.year}-${p.month}-${p.day}`;
}
export function scheduleTime(value: string | number | Date, timezone: string): string {
  const p = parts(value, timezone);
  return `${p.hour}:${p.minute}`;
}
export const scheduleLabel = (value: string | number | Date, timezone: string, options: Intl.DateTimeFormatOptions) =>
  new Intl.DateTimeFormat('ru-RU', { ...options, timeZone: timezone }).format(new Date(value));

export function effectiveTimezone(value?: string): string {
  try {
    const zone = value || Intl.DateTimeFormat().resolvedOptions().timeZone;
    new Intl.DateTimeFormat('ru-RU', { timeZone: zone }).format();
    return zone;
  } catch { return 'Europe/Moscow'; }
}

/** Reject DST gaps and repeated wall times instead of silently booking another time. */
function instantMatches(day: string, time: string, timezone: string): number[] {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || !/^([01]\d|2[0-3]):[0-5]\d$/.test(time)) throw new Error('Проверьте дату и время.');
  const wall = Date.parse(`${day}T${time}:00Z`);
  if (!Number.isFinite(wall) || new Date(wall).toISOString().slice(0, 10) !== day) throw new Error('Проверьте дату.');
  const offsets = new Set<number>();
  for (const hours of [-36, -12, 0, 12, 36]) {
    const tick = wall + hours * 3600_000;
    const p = parts(tick, timezone);
    offsets.add(Date.parse(`${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}Z`) - tick);
  }
  return [...offsets].map(offset => wall - offset).filter(tick =>
    scheduleDateKey(tick, timezone) === day && scheduleTime(tick, timezone) === time);
}

export function scheduleInstant(day: string, time: string, timezone: string): string {
  if (time === '24:00') {
    instantMatches(day, '00:00', timezone); // Validate the date before shifting it.
    day = new Date(Date.parse(`${day}T12:00:00Z`) + 86400_000).toISOString().slice(0, 10);
    time = '00:00';
  }
  const matches = instantMatches(day, time, timezone);
  if (!matches.length) throw new Error('В это время переводят часы. Выберите другое время.');
  if (matches.length > 1) throw new Error('Из-за перевода часов это время повторяется. Выберите другое время.');
  return new Date(matches[0]).toISOString();
}

/** Day boundaries use the first instant of a date, including midnight DST changes. */
export function scheduleDayStart(day: string, timezone: string): string {
  const matches = instantMatches(day, '00:00', timezone);
  if (matches.length) return new Date(Math.min(...matches)).toISOString();
  // Midnight may be skipped. A wholly skipped date has the same boundary as the next day.
  let low = Date.parse(`${day}T00:00:00Z`) / 1000 - 172800, high = low + 345600;
  while (high - low > 1) {
    const mid = Math.floor((low + high) / 2);
    if (scheduleDateKey(mid * 1000, timezone) < day) low = mid;
    else high = mid;
  }
  return new Date(high * 1000).toISOString();
}

const names: Record<string, string> = {
  'Europe/Kaliningrad': 'Калининград', 'Europe/Moscow': 'Москва', 'Europe/Samara': 'Самара',
  'Asia/Yekaterinburg': 'Екатеринбург', 'Asia/Omsk': 'Омск', 'Asia/Novosibirsk': 'Новосибирск',
  'Asia/Krasnoyarsk': 'Красноярск', 'Asia/Irkutsk': 'Иркутск', 'Asia/Yakutsk': 'Якутск',
  'Asia/Vladivostok': 'Владивосток', 'Asia/Magadan': 'Магадан', 'Asia/Kamchatka': 'Камчатка',
  'Europe/Minsk': 'Минск', 'Asia/Almaty': 'Алматы', 'Asia/Tashkent': 'Ташкент', 'Asia/Tbilisi': 'Тбилиси',
  'Asia/Yerevan': 'Ереван', 'Asia/Dubai': 'Дубай', 'Europe/Berlin': 'Берлин', 'Europe/London': 'Лондон',
  'America/New_York': 'Нью-Йорк', 'America/Los_Angeles': 'Лос-Анджелес', 'Asia/Tokyo': 'Токио', UTC: 'UTC',
};
export function timezoneOptions(current: string) {
  const supported = typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : [];
  return [...new Set([...Object.keys(names), current, ...supported])].map(zone => {
    const offset = new Intl.DateTimeFormat('en', { timeZone: zone, timeZoneName: 'shortOffset' }).formatToParts(new Date())
      .find(part => part.type === 'timeZoneName')?.value.replace('GMT', 'UTC');
    return { value: zone, label: `${names[zone] || zone.replaceAll('_', ' ')}${zone === 'UTC' ? '' : ` · ${offset}`}` };
  });
}
