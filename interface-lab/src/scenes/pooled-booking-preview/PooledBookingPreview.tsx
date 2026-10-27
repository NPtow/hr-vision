import { useMemo, useRef, useState } from 'react';
import { DayPicker } from '@daypicker/react';
import { ru } from '@daypicker/react/locale';
import { ArrowLeft, ArrowRight, CalendarDays, Check, Globe2 } from 'lucide-react';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';
import '@daypicker/react/style.css';
import './pooled-booking.css';

// Isolated design prototype. No API, storage, real bookings or messages.
const firstDay = new Date(2026, 9, 17, 12);
const lastDay = new Date(2026, 10, 13, 12);
const keyOf = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
const dateLabel = (date: Date) => {
  const label = date.toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' });
  return label.charAt(0).toUpperCase() + label.slice(1);
};
const longDate = (date: Date) => date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });

// Overlapping fixture availability intentionally becomes ONE public time.
function pooledTimes(date: Date) {
  if (date < firstDay || date > lastDay || date.getDay() === 0) return [];
  const pools = date.getDay() === 6
    ? [['12:00', '13:00', '14:00', '16:00'], ['13:00', '16:00', '16:30', '17:00']]
    : [['10:00', '11:30', '14:00', '16:00'], ['11:30', '15:00', '16:00', '16:30']];
  return [...new Set(pools.flat())].sort();
}

type Stage = 'time' | 'details' | 'success';
export function PooledBookingPreview({ simulateConflict = false }: { simulateConflict?: boolean }) {
  const [stage, setStage] = useState<Stage>('time');
  const [date, setDate] = useState<Date>(firstDay);
  const [month, setMonth] = useState<Date>(new Date(2026, 9, 1));
  const [time, setTime] = useState('');
  const [zone, setZone] = useState('Europe/Moscow');
  const [email, setEmail] = useState('');
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState('');
  const [taken, setTaken] = useState<string[]>([]);
  const conflictUsed = useRef(false);
  const timePanel = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const times = useMemo(() => pooledTimes(date).filter(value => !taken.includes(`${keyOf(date)}T${value}`)), [date, taken]);
  const displayTime = (value: string) => {
    const shift = zone === 'Asia/Yekaterinburg' ? 2 : zone === 'Europe/Kaliningrad' ? -1 : 0;
    const [hours, minutes] = value.split(':');
    return `${String(Number(hours) + shift).padStart(2, '0')}:${minutes}`;
  };
  const summary = `${longDate(date)}, ${displayTime(time || '12:00')}`;
  function move(next: Stage) { setStage(next); requestAnimationFrame(() => heading.current?.focus()); }
  function reset() {
    setStage('time'); setDate(firstDay); setMonth(new Date(2026, 9, 1)); setTime('');
    setEmail(''); setConsent(false); setError(''); setTaken([]); conflictUsed.current = false;
  }
  function confirm() {
    if (!email.trim() || !consent) return;
    if (simulateConflict && !conflictUsed.current) {
      conflictUsed.current = true;
      setTaken(old => [...old, `${keyOf(date)}T${time}`]); setTime('');
      setError('Это время только что заняли. Выберите другое — ваши данные сохранены.');
      move('time'); return;
    }
    setError(''); move('success');
  }
  return <div className="pbp-root">
    <div className="pbp-lab"><span><i/>Макет · без реальной записи и отправки писем</span><button onClick={reset}>Начать заново</button></div>
    <header className="pbp-header"><img src={logo} alt="HR Vision"/></header>
    <main className={`pbp-main pbp-stage-${stage}`}>
      <h1 className="pbp-sr-only" tabIndex={-1} ref={heading}>{stage === 'time' ? 'Выберите время интервью' : stage === 'details' ? 'Подтвердите запись' : 'Вы записаны на интервью'}</h1>
      {stage !== 'success' ? <section className="pbp-surface">
        {stage === 'time' ? <div className="pbp-picker">
          <section className="pbp-calendar" aria-label="Выбор даты">
            <DayPicker mode="single" required selected={date} onSelect={next => {
              setDate(new Date(next.getFullYear(), next.getMonth(), next.getDate(), 12)); setTime('');
              if (window.matchMedia('(max-width: 700px)').matches) requestAnimationFrame(() => timePanel.current?.scrollIntoView({ block: 'start', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }));
            }} month={month} onMonthChange={setMonth} startMonth={new Date(2026, 9, 1)} endMonth={new Date(2026, 10, 1)} locale={ru} weekStartsOn={1}
              disabled={day => pooledTimes(new Date(day.getFullYear(), day.getMonth(), day.getDate(), 12)).length === 0}
              today={new Date(2026, 9, 16)} showOutsideDays={false}
              labels={{ labelNext: () => 'Следующий месяц', labelPrevious: () => 'Предыдущий месяц' }}/>
            <label className="pbp-zone"><Globe2 size={17}/><select value={zone} onChange={event => setZone(event.target.value)} aria-label="Часовой пояс"><option value="Europe/Moscow">Москва · UTC+3</option><option value="Asia/Yekaterinburg">Екатеринбург · UTC+5</option><option value="Europe/Kaliningrad">Калининград · UTC+2</option></select></label>
          </section>
          <section className="pbp-times" ref={timePanel} aria-label={`Время: ${dateLabel(date)}`}>
            {error && <p className="pbp-error" role="alert">{error}</p>}
            <div className="pbp-time-list" role="group" aria-label={`Время на ${longDate(date)}`}>
              {times.map(value => <button key={value} className={time === value ? 'pbp-time selected' : 'pbp-time'} aria-pressed={time === value} onClick={() => setTime(value)}>{displayTime(value)}{time === value && <Check size={17}/>}</button>)}
              {!times.length && <p>На этот день свободного времени нет. Выберите другую дату.</p>}
            </div>
            <button className="pbp-primary" disabled={!time} onClick={() => { setError(''); move('details'); }}>Продолжить<ArrowRight size={17}/></button>
          </section>
        </div> : <form className="pbp-form" onSubmit={event => { event.preventDefault(); confirm(); }}>
          <button className="pbp-back" type="button" onClick={() => move('time')}><ArrowLeft size={17}/>Изменить время</button>
          <div className="pbp-selection"><CalendarDays size={22}/><div><strong>{summary}</strong><span>30 минут · {zone === 'Europe/Moscow' ? 'московское время' : zone === 'Asia/Yekaterinburg' ? 'время Екатеринбурга' : 'время Калининграда'}</span></div></div>
          <label className="pbp-field">Ваша почта<input autoComplete="email" type="email" required placeholder="name@example.com" value={email} onChange={event => setEmail(event.target.value)}/></label>
          <p className="pbp-field-hint">На созвоне рекрутер отправит сюда доступ к вашему профилю.</p>
          <label className="pbp-consent"><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} required/><span>Согласен на интервью и подготовку моего профиля HR Vision из резюме.</span></label>
          <button className="pbp-primary" disabled={!email.trim() || !consent}>Подтвердить запись<ArrowRight size={18}/></button>
        </form>}
      </section> : <section className="pbp-success pbp-surface"><div className="pbp-success-icon"><Check size={29}/></div><h2>Вы записаны на интервью</h2><p className="pbp-success-date">{summary}</p><p>30 минут · видеовстреча</p><div className="pbp-success-note">Информация о встрече и ссылка для подключения придут на <strong>{email}</strong>.</div><button className="pbp-back" onClick={() => move('time')}><ArrowLeft size={17}/>Выбрать другое время</button></section>}
    </main>
  </div>;
}
