import { useEffect, useId, useState } from 'react';
import { DayPicker, TZDate } from '@daypicker/react';
import { ru } from '@daypicker/react/locale';
import { CalendarDays, ChevronDown } from 'lucide-react';
import { Popover } from 'radix-ui';
import { Button } from '../../components/ui/button';
import type { EmployerCandidateMeetingOptions } from './employer-candidate-profile-types';
import '@daypicker/react/style.css';
import './employer-meeting-time-picker.css';

const zone = 'Europe/Moscow';
const horizon = 180 * 86_400_000;
const dayKey = (date: Date) => new Intl.DateTimeFormat('en-CA', {
  timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit',
}).format(date);
const zonedDay = (day: string) => new TZDate(`${day}T12:00:00+03:00`, zone);
const dateLabel = (day: string) => new Intl.DateTimeFormat('ru-RU', {
  day: 'numeric', month: 'long', timeZone: zone,
}).format(zonedDay(day));
const timeLabel = (start: string) => new Intl.DateTimeFormat('ru-RU', {
  hour: '2-digit', minute: '2-digit', timeZone: zone,
}).format(new Date(start));

function proposedTimes(day: string, now: number) {
  if (!day) return [];
  return Array.from({ length: 48 }, (_, index) => {
    const time = `${String(Math.floor(index / 2)).padStart(2, '0')}:${index % 2 ? '30' : '00'}`;
    return new Date(`${day}T${time}:00+03:00`).toISOString();
  }).filter(start => Date.parse(start) > now && Date.parse(start) <= now + horizon);
}

/** The calendar changes only a local draft; the response form owns confirmation. */
export function EmployerMeetingTimePicker({ options, value, onChange, disabled }: {
  options: EmployerCandidateMeetingOptions; value: string; onChange: (start: string) => void; disabled?: boolean;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [day, setDay] = useState(() => value ? dayKey(new Date(value)) : '');
  const now = Date.now();
  const today = zonedDay(dayKey(new Date(now)));
  const last = zonedDay(dayKey(new Date(now + horizon)));
  const available = options.mode === 'available'
    ? [...new Set(options.freeSlots)].filter(start => Date.parse(start) > now && Date.parse(start) <= now + horizon).sort()
    : [];
  const availableDays = new Set(available.map(start => dayKey(new Date(start))));
  const times = options.mode === 'available'
    ? available.filter(start => dayKey(new Date(start)) === day)
    : proposedTimes(day, now);
  const initialDay = day || (available[0] ? dayKey(new Date(available[0])) : dayKey(today));
  const unavailable = options.mode === 'available' && !available.length;
  const valuesKey = times.join('|');

  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  // A refresh can withdraw a slot while this disclosure is open.
  useEffect(() => { if (value && !times.includes(value)) onChange(''); }, [value, valuesKey]);

  return <div className="ecp-meeting-picker">
    <div className="ecp-meeting-fields">
      <div className="ecp-meeting-field">
        <span id={`${id}-date-label`}>Дата</span>
        <Popover.Root open={open} onOpenChange={setOpen}>
          <Popover.Trigger asChild><Button type="button" variant="secondary" className="ecp-meeting-date"
            disabled={disabled || unavailable} autoFocus aria-label={`Дата встречи${day ? `: ${dateLabel(day)}` : ''}`}>
            <CalendarDays size={16} aria-hidden="true"/><span>{day ? dateLabel(day) : 'Выбрать дату'}</span><ChevronDown size={16} aria-hidden="true"/>
          </Button></Popover.Trigger>
          <Popover.Portal><Popover.Content className="ecp-meeting-calendar" align="start" sideOffset={8} collisionPadding={16}
            aria-label="Выбор даты встречи" onEscapeKeyDown={event => event.stopPropagation()}>
            <DayPicker mode="single" required selected={day ? zonedDay(day) : undefined} defaultMonth={zonedDay(initialDay)}
              locale={ru} timeZone={zone} today={today} startMonth={today} endMonth={last} navLayout="around" fixedWeeks autoFocus
              disabled={[{ before: today }, { after: last }, date => options.mode === 'available'
                ? !availableDays.has(dayKey(date)) : !proposedTimes(dayKey(date), now).length]}
              labels={{ labelDayButton: date => new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', weekday: 'long', timeZone: zone }).format(date) }}
              onSelect={date => { setDay(dayKey(date)); onChange(''); setOpen(false); }}/>
          </Popover.Content></Popover.Portal>
        </Popover.Root>
      </div>
      <label className="ecp-meeting-field" htmlFor={`${id}-time`}>Время · МСК
        <span className="ecp-meeting-time"><select id={`${id}-time`} aria-label="Время встречи, МСК" required
          disabled={disabled || !day || !times.length} value={times.includes(value) ? value : ''} onChange={event => onChange(event.target.value)}>
          <option value="" disabled>Выбрать время</option>
          {times.map(start => <option key={start} value={start}>{timeLabel(start)}</option>)}
        </select><ChevronDown size={16} aria-hidden="true"/></span>
      </label>
    </div>
    {unavailable ? <p className="ecp-meeting-note" role="status">Кандидат пока не указал свободное время.</p>
      : <p className="ecp-meeting-note">{options.mode === 'proposal' ? 'Для согласования · 30 минут' : '30 минут'}</p>}
  </div>;
}
