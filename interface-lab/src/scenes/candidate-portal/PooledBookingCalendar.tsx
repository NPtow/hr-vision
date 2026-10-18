import { useEffect, useMemo, useRef, useState } from 'react';
import { DayPicker } from '@daypicker/react';
import { ru } from '@daypicker/react/locale';
import { ArrowRight, Check, Globe2 } from 'lucide-react';
import { bookingDays, calendarKey, slotLabel, type BookingSlot } from './pooled-booking-model';
import '@daypicker/react/style.css';
import './pooled-booking.css';

const zones = [ ['Europe/Moscow', 'Москва · UTC+3'], ['Europe/Kaliningrad', 'Калининград · UTC+2'],
  ['Asia/Yekaterinburg', 'Екатеринбург · UTC+5'] ];
type Props = {
  slots: BookingSlot[]; selectedId: string; onSelect: (id: string) => void;
  timezone: string; onTimezone: (zone: string) => void; onContinue: () => void;
  disabled?: boolean; continueLabel?: string;
};

/** Shared by personal invitations and the signed-in candidate cabinet. */
export function PooledBookingCalendar({ slots, selectedId, onSelect, timezone, onTimezone, onContinue, disabled = false, continueLabel = 'Продолжить' }: Props) {
  const days = useMemo(() => bookingDays(slots, timezone), [slots, timezone]);
  const [dayKey, setDayKey] = useState('');
  const selectedDay = days.find(day => day.slots.some(slot => slot.id === selectedId));
  const [month, setMonth] = useState(() => selectedDay?.date ?? days[0]?.date ?? new Date());
  const monthKey = calendarKey(month).slice(0, 7);
  const visibleDays = days.filter(day => day.key.startsWith(monthKey));
  const day = (selectedDay?.key.startsWith(monthKey) ? selectedDay : undefined)
    ?? visibleDays.find(day => day.key === dayKey) ?? visibleDays[0];
  const timePanel = useRef<HTMLElement>(null);
  const priorZone = useRef(timezone);
  const first = days[0]?.key, last = days.at(-1)?.key;
  useEffect(() => {
    if (!first || !last) return;
    if (priorZone.current !== timezone || calendarKey(month).slice(0, 7) < first.slice(0, 7)
      || calendarKey(month).slice(0, 7) > last.slice(0, 7)) setMonth(selectedDay?.date ?? day?.date ?? days[0].date);
    priorZone.current = timezone;
  }, [first, last, timezone, selectedDay, day, days, month]);
  if (!days.length) return <p className="pbc-empty" role="status">Пока нет свободного времени.</p>;
  const dateLabel = day?.date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });
  const selected = day?.slots.find(slot => slot.id === selectedId);
  const onDay = (date: Date) => {
    setDayKey(calendarKey(date)); onSelect('');
    if (window.matchMedia('(max-width: 700px)').matches) requestAnimationFrame(() => timePanel.current?.scrollIntoView({ block: 'nearest', behavior: 'auto' }));
  };
  return <div className="pbc" aria-busy={disabled}>
    <h1 className="pbc-sr-only">Выберите время интервью</h1>
    <div className="pbc-picker">
      <section className="pbc-calendar" aria-label="Выбор даты">
        <DayPicker mode="single" required selected={day?.date} onSelect={onDay} month={month}
          onMonthChange={date => { setMonth(date); const next = days.find(day => day.key.startsWith(calendarKey(date).slice(0, 7))); setDayKey(next?.key ?? ''); onSelect(''); }}
          startMonth={days[0].date} endMonth={days.at(-1)!.date} locale={ru} weekStartsOn={1}
          disabled={date => disabled || !days.some(day => day.key === calendarKey(date))}
          disableNavigation={disabled} showOutsideDays={false}
          labels={{ labelNext: () => 'Следующий месяц', labelPrevious: () => 'Предыдущий месяц' }}/>
        <label className="pbc-zone"><Globe2 size={17}/><select value={timezone} disabled={disabled} onChange={event => onTimezone(event.target.value)} aria-label="Часовой пояс">
          {!zones.some(([zone]) => zone === timezone) && <option value={timezone}>{timezone}</option>}
          {zones.map(([zone, label]) => <option key={zone} value={zone}>{label}</option>)}
        </select></label>
      </section>
      <section className="pbc-times" ref={timePanel} aria-label={dateLabel ? `Время на ${dateLabel}` : 'Время интервью'}>
        {!day && <p className="pbc-empty" role="status">В этом месяце свободного времени нет.</p>}
        <div className="pbc-time-list" role="group" aria-label={dateLabel ? `Время на ${dateLabel}` : 'Время интервью'}>
          {day?.slots.map(slot => <button type="button" key={slot.id} className={`pbc-time${slot.id === selectedId ? ' selected' : ''}`}
            disabled={disabled} aria-pressed={slot.id === selectedId} onClick={() => onSelect(slot.id)}
            aria-label={`${dateLabel}, ${slotLabel(slot.start, timezone)}–${slotLabel(slot.end, timezone)}`}>
            {slotLabel(slot.start, timezone)}{slot.id === selectedId && <Check size={17}/>}</button>)}
        </div>
        <button type="button" className="pbc-primary" disabled={disabled || !selected} onClick={onContinue}>{continueLabel}<ArrowRight size={17}/></button>
      </section>
    </div>
  </div>;
}
