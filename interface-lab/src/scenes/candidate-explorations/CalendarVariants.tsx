import { useEffect, useId, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { ArrowRight, CalendarDays, Check, ChevronLeft, ChevronRight, Clock3, Globe2, Settings2, Video, X } from 'lucide-react';
import type { Availability, CalendarProps, Vacancy } from './types';
import './calendar.css';

const BASE_YEAR = 2026;
const BASE_MONTH = 9;
const TODAY = new Date(BASE_YEAR, BASE_MONTH, 19);
const meetingCount = (count: number) => { const form = new Intl.PluralRules('ru').select(count); return `${count} ${form === 'one' ? 'встреча' : form === 'few' ? 'встречи' : 'встреч'}`; };
const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const EDIT_DAYS = [19, 20, 21, 22, 23];
const dateOf = (day: number) => new Date(BASE_YEAR, BASE_MONTH, day);
const addDays = (date: Date, days: number) => new Date(date.getFullYear(), date.getMonth(), date.getDate() + days);
const dateKey = (date: Date) => `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
const sameDay = (a: Date, b: Date) => dateKey(a) === dateKey(b);
const isBaseMonth = (date: Date) => date.getFullYear() === BASE_YEAR && date.getMonth() === BASE_MONTH;
const startOfWeek = (date: Date) => addDays(date, -((date.getDay() + 6) % 7));
const dayLabel = (date: Date, long = false) => new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: long ? 'long' : 'short' }).format(date);
const weekday = (date: Date, long = false) => new Intl.DateTimeFormat('ru-RU', { weekday: long ? 'long' : 'short' }).format(date);
const monthLabel = (date: Date) => new Intl.DateTimeFormat('ru-RU', { month: 'long', year: 'numeric' }).format(date).replace(' г.', '');
const timeMinutes = (value: string) => { const [hours, minutes] = value.split(':').map(Number); return hours * 60 + minutes; };
const meetingEnd = (value: string) => { const minutes = timeMinutes(value) + 30; return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`; };
const meetingsFor = (items: Vacancy[], date: Date) => isBaseMonth(date) ? items.filter(item => item.meetingDay === date.getDate()) : [];
const availabilityFor = (items: Availability[], date: Date) => isBaseMonth(date) ? items.find(item => item.day === date.getDate()) : undefined;
const completedStage = (vacancy: Vacancy) => ['completed', 'followup', 'offer', 'accepted'].includes(vacancy.stage);
const dayRange = (first: Date, count: number) => Array.from({ length: count }, (_, index) => addDays(first, index));
const statusLabel = (vacancy: Vacancy) => vacancy.archived && ['proposed', 'confirmed'].includes(vacancy.stage) ? (vacancy.stage === 'proposed' ? 'Предложенное время · в архиве' : 'Назначенная встреча · в архиве') : completedStage(vacancy) ? 'Встреча завершена' : vacancy.stage === 'proposed' ? 'Ждёт подтверждения' : 'Подтверждено';

type MeetingActions = Pick<CalendarProps, 'act' | 'openVacancy'>;
type HistoryRecord = { key: string; vacancy: Vacancy; day?: number; time?: string };

function MeetingCard({ vacancy, act, openVacancy, compact = false, featured = false, historyView = false, historyDate }: MeetingActions & { vacancy: Vacancy; compact?: boolean; featured?: boolean; historyView?: boolean; historyDate?: { day?: number; time?: string } }) {
  const displayedTime = historyView ? historyDate?.time : vacancy.meetingTime;
  return <article className={`cex-calendar-meeting${compact ? ' cex-calendar-meeting-compact' : ''}${featured ? ' cex-calendar-meeting-featured' : ''}`}>
    <div className="cex-calendar-meeting-meta"><span className={`cex-calendar-status${vacancy.stage === 'proposed' && !vacancy.archived && !historyView ? ' cex-calendar-status-pending' : ''}`}><span />{statusLabel(vacancy)}</span><span>{displayedTime ? `${displayedTime}–${meetingEnd(displayedTime)}` : 'В истории вакансии'}</span></div>
    <div className="cex-calendar-meeting-identity"><span className="cex-calendar-company-mark" style={{ '--cex-calendar-company': vacancy.color } as CSSProperties}>{vacancy.initials}</span><div><p>{vacancy.company}</p><h3>{vacancy.role}</h3></div></div>
    {historyView && historyDate?.day && <p className="cex-calendar-featured-date"><CalendarDays size={15} />{dayLabel(dateOf(historyDate.day), true)}</p>}
    {featured && <p className="cex-calendar-featured-date"><CalendarDays size={18} />{dayLabel(dateOf(vacancy.meetingDay), true)}<span>·</span>30 минут</p>}
    <div className="cex-calendar-meeting-actions">
      {vacancy.stage === 'proposed' && !vacancy.archived && !historyView && <button type="button" className="cex-calendar-button cex-calendar-button-primary" onClick={() => act(vacancy.id, 'confirm')}><Check size={15} />Подтвердить</button>}
      {vacancy.stage === 'confirmed' && !vacancy.archived && !historyView && <button type="button" className="cex-calendar-button cex-calendar-button-primary" onClick={() => act(vacancy.id, 'join')}><Video size={15} />Войти</button>}
      {!vacancy.archived && !historyView && ['proposed', 'confirmed'].includes(vacancy.stage) && <button type="button" className="cex-calendar-button cex-calendar-button-quiet" onClick={() => act(vacancy.id, 'reschedule')}>Другое время</button>}
      <button type="button" className="cex-calendar-link" onClick={() => openVacancy(vacancy.id)}>О вакансии<ArrowRight size={14} /></button>
    </div>
  </article>;
}

function EmptyDay({ date, compact = false }: { date: Date; compact?: boolean }) {
  return <div className={`cex-calendar-empty${compact ? ' cex-calendar-empty-compact' : ''}`}><CalendarDays size={compact ? 20 : 27} /><h3>Встреч нет</h3>{!compact && <p>На {dayLabel(date, true)} ничего не назначено.</p>}</div>;
}

function DayAgenda({ date, items, act, openVacancy, heading = true }: MeetingActions & { date: Date; items: Vacancy[]; heading?: boolean }) {
  return <section className="cex-calendar-day-agenda">{heading && <div className="cex-calendar-section-heading"><div><p>{weekday(date, true)}</p><h2>{dayLabel(date, true)}</h2></div><span className="cex-calendar-count">{items.length}</span></div>}<div className="cex-calendar-card-stack">{items.length ? items.map(item => <MeetingCard key={item.id} vacancy={item} act={act} openVacancy={openVacancy} compact />) : <EmptyDay date={date} />}</div></section>;
}

function DateRail({ days, selected, choose, meetings, vertical = false }: { days: Date[]; selected: Date; choose: (date: Date) => void; meetings: Vacancy[]; vertical?: boolean }) {
  const root = useRef<HTMLDivElement>(null);
  function navigate(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const next = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? index + 1 : event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? index - 1 : event.key === 'Home' ? 0 : event.key === 'End' ? days.length - 1 : null;
    if (next === null || !days[next]) return;
    event.preventDefault(); choose(days[next]); root.current?.querySelectorAll<HTMLButtonElement>('button')[next]?.focus();
  }
  return <div ref={root} className={`cex-calendar-date-rail${vertical ? ' cex-calendar-date-rail-vertical' : ''}`} role="group" aria-label="Выбрать день">{days.map((date, index) => <button type="button" key={dateKey(date)} aria-pressed={sameDay(date, selected)} className={sameDay(date, selected) ? 'cex-calendar-selected' : ''} onClick={() => choose(date)} onKeyDown={event => navigate(event, index)}><span>{weekday(date)}</span><strong>{date.getDate()}</strong><span className="cex-calendar-rail-dots" aria-label={meetingCount(meetingsFor(meetings, date).length)}>{meetingsFor(meetings, date).slice(0, 3).map(item => <i key={item.id} />)}</span></button>)}</div>;
}

function MonthGrid({ cursor, selected, choose, meetings, small = false, onMeeting }: { cursor: Date; selected: Date; choose: (date: Date) => void; meetings: Vacancy[]; small?: boolean; onMeeting: (vacancy: Vacancy) => void }) {
  const grid = useRef<HTMLDivElement>(null);
  const days = dayRange(startOfWeek(new Date(cursor.getFullYear(), cursor.getMonth(), 1)), 35 + ((new Date(cursor.getFullYear(), cursor.getMonth(), 1).getDay() + 6) % 7 + new Date(cursor.getFullYear(), cursor.getMonth() + 1, 0).getDate() > 35 ? 7 : 0));
  function navigate(event: KeyboardEvent<HTMLButtonElement>, date: Date) {
    let target: Date | undefined;
    const offsets: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
    if (event.key in offsets) target = addDays(date, offsets[event.key]);
    if (event.key === 'Home') target = startOfWeek(date);
    if (event.key === 'End') target = addDays(startOfWeek(date), 6);
    if (event.key === 'PageUp' || event.key === 'PageDown') target = new Date(date.getFullYear(), date.getMonth() + (event.key === 'PageUp' ? -1 : 1), Math.min(date.getDate(), 28));
    if (!target) return;
    event.preventDefault(); choose(target);
    const key = dateKey(target);
    requestAnimationFrame(() => grid.current?.querySelector<HTMLButtonElement>(`[data-calendar-date="${key}"]`)?.focus());
  }
  return <div ref={grid} className={`cex-calendar-month${small ? ' cex-calendar-month-small' : ''}`}>
    <div className="cex-calendar-weekday-head">{WEEKDAYS.map(day => <span key={day}>{day}</span>)}</div>
    <div className="cex-calendar-month-cells" role="group" aria-label={monthLabel(cursor)}>{days.map(date => {
      const entries = meetingsFor(meetings, date);
      return <div key={dateKey(date)} className={`cex-calendar-month-cell${date.getMonth() !== cursor.getMonth() ? ' cex-calendar-outside' : ''}${sameDay(date, selected) ? ' cex-calendar-cell-selected' : ''}`}>
        <button type="button" className={`cex-calendar-day-number${sameDay(date, TODAY) ? ' cex-calendar-today' : ''}`} data-calendar-date={dateKey(date)} aria-label={`${dayLabel(date, true)}, ${meetingCount(entries.length)}`} aria-pressed={sameDay(date, selected)} tabIndex={sameDay(date, selected) ? 0 : -1} onClick={() => choose(date)} onKeyDown={event => navigate(event, date)}>{date.getDate()}{small && !!entries.length && <i />}</button>
        {!small && <div className="cex-calendar-month-events">{entries.slice(0, 2).map(item => <button type="button" key={item.id} className={`cex-calendar-month-event${item.stage === 'proposed' ? ' cex-calendar-month-event-pending' : ''}`} onClick={() => { choose(date); onMeeting(item); }}><span>{item.meetingTime}</span><strong>{item.company}</strong></button>)}{entries.length > 2 && <button type="button" className="cex-calendar-extra" onClick={() => choose(date)}>Ещё {entries.length - 2}</button>}</div>}
      </div>;
    })}</div>
  </div>;
}

function TimeGrid({ days, selected, choose, meetings, availability, onMeeting, compact = false }: { days: Date[]; selected: Date; choose: (date: Date) => void; meetings: Vacancy[]; availability: Availability[]; onMeeting: (vacancy: Vacancy) => void; compact?: boolean }) {
  const scheduledTimes = meetings.filter(item => days.some(day => isBaseMonth(day) && day.getDate() === item.meetingDay)).map(item => timeMinutes(item.meetingTime));
  const firstHour = Math.min(9, ...scheduledTimes.map(value => Math.floor(value / 60)));
  const lastHour = Math.max(18, ...scheduledTimes.map(value => Math.ceil((value + 30) / 60)));
  const hourHeight = compact ? 42 : 48;
  const hours = Array.from({ length: lastHour - firstHour + 1 }, (_, index) => firstHour + index);
  const height = (lastHour - firstHour + 1) * hourHeight;
  return <div className={`cex-calendar-time-scroll${compact ? ' cex-calendar-time-scroll-compact' : ''}`}><div className="cex-calendar-time-grid" style={{ '--cex-calendar-columns': days.length, '--cex-calendar-grid-height': `${height}px`, '--cex-calendar-hour-height': `${hourHeight}px` } as CSSProperties}>
    <div className="cex-calendar-time-corner">UTC+3</div>{days.map(day => <button type="button" className={`cex-calendar-time-day${sameDay(day, selected) ? ' cex-calendar-time-day-selected' : ''}`} key={dateKey(day)} onClick={() => choose(day)} aria-pressed={sameDay(day, selected)}><span>{weekday(day)}</span><strong>{day.getDate()}</strong></button>)}
    <div className="cex-calendar-time-labels">{hours.map(hour => <span key={hour}>{String(hour).padStart(2, '0')}:00</span>)}</div>
    {days.map(day => {
      const free = availabilityFor(availability, day);
      const entries = meetingsFor(meetings, day);
      return <div className="cex-calendar-time-column" key={dateKey(day)} aria-label={dayLabel(day, true)}>{free && <div className="cex-calendar-time-availability" aria-label={`Удобно с ${free.from} до ${free.to}`} style={{ top: Math.max(0, (timeMinutes(free.from) / 60 - firstHour) * hourHeight), height: Math.max(0, Math.min(timeMinutes(free.to) / 60, lastHour + 1) - Math.max(timeMinutes(free.from) / 60, firstHour)) * hourHeight }} />}{entries.map((item, index) => <button type="button" key={item.id} className={`cex-calendar-time-event${item.stage === 'proposed' ? ' cex-calendar-time-event-pending' : ''}`} style={{ top: Math.max(0, (timeMinutes(item.meetingTime) / 60 - firstHour) * hourHeight), zIndex: index + 1 }} onClick={() => { choose(day); onMeeting(item); }} aria-label={`${item.meetingTime}, ${item.company}, ${statusLabel(item)}`}><span>{item.meetingTime}</span><strong>{item.company}</strong><small>{item.stage === 'proposed' ? 'Подтвердить' : '30 минут'}</small></button>)}</div>;
    })}
  </div></div>;
}

function AvailabilityEditor({ availability, save, close }: { availability: Availability[]; save: (value: Availability[]) => void; close: () => void }) {
  const [draft, setDraft] = useState<Availability[]>(availability.map(item => ({ ...item })));
  const [error, setError] = useState('');
  const titleId = useId();
  const firstInput = useRef<HTMLInputElement>(null);
  useEffect(() => { firstInput.current?.focus(); }, []);
  const edit = (day: number, field: 'from' | 'to', value: string) => { setError(''); setDraft(previous => previous.map(item => item.day === day ? { ...item, [field]: value } : item)); };
  return <section className="cex-calendar-availability-editor" aria-labelledby={titleId} onKeyDown={event => { if (event.key === 'Escape') close(); }}><div className="cex-calendar-section-heading"><div><h2 id={titleId}>Когда вам удобно</h2><p>19–23 октября · Москва, UTC+3</p></div><button type="button" className="cex-calendar-icon-button" onClick={close} aria-label="Закрыть настройки доступности"><X size={18} /></button></div>
    <p className="cex-calendar-availability-description">Работодателям будет проще предложить подходящее время. Уже назначенные встречи сохранятся.</p>
    <div className="cex-calendar-availability-days">{EDIT_DAYS.map((day, index) => { const item = draft.find(value => value.day === day); return <div className="cex-calendar-availability-row" key={day}><label><input ref={index === 0 ? firstInput : undefined} type="checkbox" checked={!!item} onChange={event => { setError(''); setDraft(previous => event.target.checked ? [...previous, { day, from: '10:00', to: '18:00' }].sort((a, b) => a.day - b.day) : previous.filter(value => value.day !== day)); }} /><span>{weekday(dateOf(day))}, {day} окт.</span></label>{item ? <div><input type="time" aria-label={`Начало доступности ${day} октября`} value={item.from} onInput={event => edit(day, 'from', event.currentTarget.value)} /><span>—</span><input type="time" aria-label={`Конец доступности ${day} октября`} value={item.to} onInput={event => edit(day, 'to', event.currentTarget.value)} /></div> : <span className="cex-calendar-muted">Не предлагаю время</span>}</div>; })}</div>
    {error && <p className="cex-calendar-form-error" role="alert">{error}</p>}
    <div className="cex-calendar-editor-actions"><button type="button" className="cex-calendar-button cex-calendar-button-primary" onClick={() => { if (draft.some(item => !item.from || !item.to || timeMinutes(item.to) <= timeMinutes(item.from))) { setError('Время окончания должно быть позже начала.'); return; } save(draft); }}>Сохранить</button><button type="button" className="cex-calendar-button" onClick={close}>Отмена</button></div>
  </section>;
}

export function CalendarVariants({ variant, vacancies, availability, setAvailability, act, openVacancy }: CalendarProps) {
  const reducedMotion = useReducedMotion();
  const initialDay = vacancies.filter(item => !item.archived && ['proposed', 'confirmed'].includes(item.stage)).sort((a, b) => a.meetingDay - b.meetingDay)[0]?.meetingDay ?? 19;
  const [selected, setSelected] = useState(() => dateOf(initialDay));
  const [cursor, setCursor] = useState(() => dateOf(initialDay));
  const [comparison, setComparison] = useState(() => dateOf(initialDay + 1));
  const [showAvailability, setShowAvailability] = useState(false);
  const [selectedMeetingId, setSelectedMeetingId] = useState<string | null>(null);
  const [allMonth, setAllMonth] = useState(true);
  const [showHistory, setShowHistory] = useState(false);
  const [notice, setNotice] = useState('');
  const availabilityButton = useRef<HTMLButtonElement>(null);
  const meetings = useMemo(() => vacancies.filter(item => !item.archived && (item.stage === 'proposed' || item.stage === 'confirmed')).sort((a, b) => a.meetingDay - b.meetingDay || a.meetingTime.localeCompare(b.meetingTime)), [vacancies]);
  const history = useMemo<HistoryRecord[]>(() => vacancies.flatMap(vacancy => {
    const completedEvents = vacancy.history?.filter(event => event.kind === 'meeting_completed' || event.id === 'meeting-completed' || event.label === 'Встреча состоялась') ?? [];
    const records: HistoryRecord[] = completedEvents.map(event => ({ key: `${vacancy.id}:${event.id}`, vacancy: { ...vacancy, stage: 'completed' }, day: event.day ?? (completedStage(vacancy) ? vacancy.meetingDay : undefined), time: event.time ?? (completedStage(vacancy) ? vacancy.meetingTime : undefined) }));
    if (!records.length && completedStage(vacancy)) records.push({ key: `${vacancy.id}:completed`, vacancy, day: vacancy.meetingDay, time: vacancy.meetingTime });
    if (vacancy.archived && ['proposed', 'confirmed'].includes(vacancy.stage)) records.push({ key: `${vacancy.id}:archived`, vacancy, day: vacancy.meetingDay, time: vacancy.meetingTime });
    return records;
  }).sort((a, b) => (b.day ?? 0) - (a.day ?? 0) || (b.time ?? '').localeCompare(a.time ?? '')), [vacancies]);
  const shownMeetings = meetings;
  const week = dayRange(startOfWeek(cursor), 7);
  const selectedItems = meetingsFor(shownMeetings, selected);
  const weekItems = shownMeetings.filter(item => week.some(day => isBaseMonth(day) && day.getDate() === item.meetingDay));
  const activeMeeting = vacancies.find(item => item.id === selectedMeetingId && ['proposed', 'confirmed', 'completed', 'followup', 'offer', 'accepted'].includes(item.stage));
  const actions = { act, openVacancy };
  const choose = (date: Date) => { setSelected(date); if (variant !== 5 || date < week[0] || date > addDays(week[0], 13)) setCursor(date); setAllMonth(false); setSelectedMeetingId(null); };
  const openMeeting = (vacancy: Vacancy) => setSelectedMeetingId(vacancy.id);
  const closeAvailability = () => { setShowAvailability(false); requestAnimationFrame(() => availabilityButton.current?.focus()); };
  const move = (direction: number) => {
    const target = variant === 0 || variant === 4 ? new Date(cursor.getFullYear(), cursor.getMonth() + direction, 1) : addDays(cursor, direction * (variant === 5 ? 14 : 7));
    setCursor(target); setSelected(target); setSelectedMeetingId(null);
    if (variant === 7) setComparison(addDays(target, 1));
  };
  const period = variant === 0 || variant === 4 ? monthLabel(cursor) : `${dayLabel(week[0])} — ${dayLabel(addDays(week[0], variant === 5 ? 13 : 6))}`;
  const nextMeeting = selectedItems[0] ?? (sameDay(selected, TODAY) ? meetings[0] : undefined);
  const goToday = () => { setSelected(TODAY); setCursor(TODAY); setComparison(addDays(TODAY, 1)); setSelectedMeetingId(null); };
  let content;
  switch (variant) {
    case 0:
      content = <div className="cex-calendar-month-agenda"><MonthGrid cursor={cursor} selected={selected} choose={choose} meetings={meetings} onMeeting={openMeeting} /><DayAgenda date={selected} items={selectedItems} {...actions} /></div>;
      break;
    case 1:
      content = <><TimeGrid days={week} selected={selected} choose={choose} meetings={meetings} availability={availability} onMeeting={openMeeting} /><div className="cex-calendar-grid-caption"><span><i />Удобное время</span><span><i />Встреча</span><span>Нажмите на встречу, чтобы открыть детали</span></div></>;
      break;
    case 2:
      content = <div className="cex-calendar-agenda-list">{weekItems.length ? week.filter(day => meetingsFor(meetings, day).length).map(day => <section className="cex-calendar-agenda-group" key={dateKey(day)}><button type="button" className={`cex-calendar-agenda-date${sameDay(day, selected) ? ' cex-calendar-selected' : ''}`} aria-pressed={sameDay(day, selected)} onClick={() => choose(day)}><strong>{day.getDate()}</strong><span>{weekday(day)} · октябрь</span></button><div>{meetingsFor(meetings, day).map(item => <div className="cex-calendar-agenda-line" key={item.id}><span>{item.meetingTime}<small>30 минут</small></span><MeetingCard vacancy={item} {...actions} compact /></div>)}</div></section>) : <EmptyDay date={week[0]} />}</div>;
      break;
    case 3:
      content = <div className="cex-calendar-day-rail-layout"><DateRail days={week} selected={selected} choose={choose} meetings={meetings} vertical /><div><div className="cex-calendar-day-title"><p>{weekday(selected, true)}</p><h2>{dayLabel(selected, true)}</h2><span>{selectedItems.length ? meetingCount(selectedItems.length) : 'День без встреч'}</span></div><DayAgenda date={selected} items={selectedItems} {...actions} heading={false} /><div className="cex-calendar-day-availability"><Clock3 size={17} /><span>{availabilityFor(availability, selected) ? `Удобно с ${availabilityFor(availability, selected)!.from} до ${availabilityFor(availability, selected)!.to}` : 'Удобное время не указано'}</span><button type="button" className="cex-calendar-link" onClick={() => setShowAvailability(true)}>Изменить</button></div></div></div>;
      break;
    case 4: {
      const items = allMonth ? (isBaseMonth(cursor) ? shownMeetings : []) : selectedItems;
      content = <div className="cex-calendar-compact-layout"><aside><MonthGrid cursor={cursor} selected={selected} choose={choose} meetings={meetings} small onMeeting={openMeeting} /><button type="button" className={`cex-calendar-month-filter${allMonth ? ' cex-calendar-selected' : ''}`} onClick={() => { setAllMonth(true); setSelectedMeetingId(null); }}>Все встречи месяца<span>{isBaseMonth(cursor) ? meetings.length : 0}</span></button><div className="cex-calendar-side-note"><Clock3 size={18} /><p>Интервью занимает 30 минут</p></div></aside><section><div className="cex-calendar-section-heading"><h2>{allMonth ? 'Встречи месяца' : dayLabel(selected, true)}</h2><span className="cex-calendar-count">{items.length}</span></div><div className="cex-calendar-meeting-cards">{items.map(item => <MeetingCard key={item.id} vacancy={item} {...actions} featured />)}</div>{!items.length && <EmptyDay date={selected} />}</section></div>;
      break;
    }
    case 5:
      content = <div className="cex-calendar-fortnight">{[0, 1].map(offset => <section key={offset}><div className="cex-calendar-planner-weeklabel">{dayLabel(addDays(week[0], offset * 7))} — {dayLabel(addDays(week[0], offset * 7 + 6))}</div><div className="cex-calendar-planner-row">{dayRange(addDays(week[0], offset * 7), 7).map(day => { const entries = meetingsFor(meetings, day); return <div key={dateKey(day)} className={`cex-calendar-planner-day${sameDay(day, selected) ? ' cex-calendar-planner-day-selected' : ''}`}><button type="button" className="cex-calendar-planner-date" onClick={() => choose(day)} aria-pressed={sameDay(day, selected)}><span>{weekday(day)}</span><strong>{day.getDate()}</strong></button><div className="cex-calendar-planner-events">{entries.map(item => <button type="button" key={item.id} onClick={() => { setSelected(day); openMeeting(item); }}><span>{item.meetingTime}</span><strong>{item.company}</strong></button>)}</div>{availabilityFor(availability, day) && <span className="cex-calendar-planner-available">{availabilityFor(availability, day)!.from}–{availabilityFor(availability, day)!.to}</span>}</div>; })}</div></section>)}</div>;
      break;
    case 6: {
      const timelineItems: HistoryRecord[] = showHistory ? history : weekItems.map(vacancy => ({ key: vacancy.id, vacancy, day: vacancy.meetingDay, time: vacancy.meetingTime }));
      content = <><div className="cex-calendar-segment" aria-label="Период встреч"><button type="button" aria-pressed={!showHistory} className={!showHistory ? 'cex-calendar-selected' : ''} onClick={() => { setShowHistory(false); setSelectedMeetingId(null); }}>Предстоящие<span>{meetings.length}</span></button><button type="button" aria-pressed={showHistory} className={showHistory ? 'cex-calendar-selected' : ''} onClick={() => { setShowHistory(true); setSelectedMeetingId(null); }}>История<span>{history.length}</span></button></div><div className="cex-calendar-timeline">{timelineItems.map(item => <div className="cex-calendar-timeline-entry" key={item.key}><div className="cex-calendar-timeline-date"><strong>{item.day ? dayLabel(dateOf(item.day)) : 'Ранее'}</strong><span>{item.time}</span></div><div className="cex-calendar-timeline-node"><i /></div><MeetingCard vacancy={item.vacancy} {...actions} historyView={showHistory} historyDate={item} /></div>)}{!timelineItems.length && <EmptyDay date={selected} />}</div></>;
      break;
    }
    case 7:
      content = <div className="cex-calendar-compare">{[selected, comparison].map((day, index) => <section key={index}><div className="cex-calendar-compare-heading"><button type="button" className="cex-calendar-icon-button" aria-label={`Предыдущий день в ${index + 1} колонке`} onClick={() => index ? setComparison(addDays(comparison, -1)) : setSelected(addDays(selected, -1))}><ChevronLeft size={17} /></button><div><span>{weekday(day, true)}</span><h2>{dayLabel(day, true)}</h2></div><button type="button" className="cex-calendar-icon-button" aria-label={`Следующий день в ${index + 1} колонке`} onClick={() => index ? setComparison(addDays(comparison, 1)) : setSelected(addDays(selected, 1))}><ChevronRight size={17} /></button></div><div className="cex-calendar-compare-summary">{meetingsFor(meetings, day).length ? `${meetingCount(meetingsFor(meetings, day).length)} · ${meetingsFor(meetings, day).length * 30} минут` : 'Весь день свободен от встреч'}</div><TimeGrid days={[day]} selected={day} choose={value => index ? setComparison(value) : setSelected(value)} meetings={meetings} availability={availability} onMeeting={openMeeting} compact /></section>)}</div>;
      break;
    case 8:
      content = <div className="cex-calendar-hours-layout"><section className="cex-calendar-hours-panel"><div className="cex-calendar-hour-scale"><span /><div>{[9, 12, 15, 18, 21].map(hour => <span key={hour}>{hour}:00</span>)}</div></div>{week.slice(0, 5).map(day => { const free = availabilityFor(availability, day); return <div key={dateKey(day)} className={`cex-calendar-hours-row${sameDay(selected, day) ? ' cex-calendar-hours-row-selected' : ''}`}><button type="button" className="cex-calendar-hours-date" onClick={() => choose(day)}><strong>{weekday(day)}</strong><span>{day.getDate()}</span></button><div className="cex-calendar-hours-track">{free && <button type="button" className="cex-calendar-hours-range" onClick={() => setShowAvailability(true)} aria-label={`Изменить доступность ${dayLabel(day)}: ${free.from}–${free.to}`} style={{ left: `${Math.max(0, (timeMinutes(free.from) - 540) / 720 * 100)}%`, width: `${Math.max(0, (Math.min(timeMinutes(free.to), 1260) - Math.max(timeMinutes(free.from), 540)) / 720 * 100)}%` }}>{free.from}–{free.to}</button>}{meetingsFor(meetings, day).map(item => <button type="button" key={item.id} className="cex-calendar-hours-meeting" style={{ left: `${Math.max(0, Math.min(96, (timeMinutes(item.meetingTime) - 540) / 720 * 100))}%` }} onClick={() => { setSelected(day); openMeeting(item); }} aria-label={`${item.company}, ${item.meetingTime}`}><span>{item.meetingTime}</span></button>)}{!free && !meetingsFor(meetings, day).length && <span className="cex-calendar-hours-none">Время не указано</span>}</div></div>; })}<div className="cex-calendar-grid-caption"><span><i />Удобно для интервью</span><span><i />Назначенная встреча</span></div></section><DayAgenda date={selected} items={selectedItems} {...actions} /></div>;
      break;
    default:
      content = <div className="cex-calendar-focus-layout"><div className="cex-calendar-next-meeting"><p className="cex-calendar-eyebrow">{nextMeeting ? 'Ближайшая встреча' : dayLabel(selected, true)}</p>{nextMeeting ? <MeetingCard vacancy={nextMeeting} {...actions} featured /> : <EmptyDay date={selected} />}<p className="cex-calendar-focus-note"><Video size={17} />Подключиться можно прямо из кабинета</p></div><aside><h2>Ваша неделя</h2><DateRail days={week} selected={selected} choose={choose} meetings={meetings} /><div className="cex-calendar-up-next">{weekItems.filter(item => item.id !== nextMeeting?.id).map(item => <button type="button" key={item.id} onClick={() => { setSelected(dateOf(item.meetingDay)); openMeeting(item); }}><span>{dayLabel(dateOf(item.meetingDay))}<strong>{item.meetingTime}</strong></span><span><strong>{item.company}</strong><small>{item.role}</small></span><ChevronRight size={17} /></button>)}{!weekItems.length && <p>На этой неделе встреч пока нет.</p>}</div></aside></div>;
  }
  return <div className={`cex-calendar cex-calendar-variant-${variant}`}>
    <div className="cex-calendar-heading"><div><h1>Календарь</h1><p>Встречи с работодателями</p></div><button ref={availabilityButton} type="button" className="cex-calendar-button" aria-expanded={showAvailability} onClick={() => { setNotice(''); setShowAvailability(value => !value); }}><Settings2 size={16} />Когда мне удобно</button></div>
    <AnimatePresence initial={false}>{showAvailability && <motion.div key="availability" initial={{ opacity: 0, y: reducedMotion ? 0 : -5 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: reducedMotion ? 0 : 0.16 }}><AvailabilityEditor availability={availability} close={closeAvailability} save={value => { setAvailability(value); setNotice('Удобное время сохранено'); closeAvailability(); }} /></motion.div>}</AnimatePresence>
    {notice && <p className="cex-calendar-save-notice" role="status"><Check size={15} />{notice}<button type="button" aria-label="Закрыть уведомление" onClick={() => setNotice('')}><X size={14} /></button></p>}
    <div className="cex-calendar-toolbar"><div className="cex-calendar-period"><h2>{period}</h2><div><button type="button" className="cex-calendar-icon-button" aria-label={variant === 0 || variant === 4 ? 'Предыдущий месяц' : 'Предыдущая неделя'} onClick={() => move(-1)}><ChevronLeft size={18} /></button><button type="button" className="cex-calendar-icon-button" aria-label={variant === 0 || variant === 4 ? 'Следующий месяц' : 'Следующая неделя'} onClick={() => move(1)}><ChevronRight size={18} /></button></div><button type="button" className="cex-calendar-today-button" onClick={goToday}>Сегодня</button></div><span className="cex-calendar-timezone"><Globe2 size={14} />Москва, UTC+3</span></div>
    <motion.div className="cex-calendar-content" key={variant} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: reducedMotion ? 0 : 0.16 }}>{content}</motion.div>
    {variant !== 6 && history.length > 0 && <section className="cex-calendar-history"><button type="button" className="cex-calendar-history-toggle" aria-expanded={showHistory} onClick={() => setShowHistory(value => !value)}>История встреч<span>{history.length}</span><ChevronRight size={14} style={{ transform: showHistory ? 'rotate(90deg)' : undefined }} /></button>{showHistory && <div className="cex-calendar-meeting-cards">{history.map(item => <MeetingCard key={item.key} vacancy={item.vacancy} {...actions} compact historyView historyDate={item} />)}</div>}</section>}
    <AnimatePresence initial={false}>{activeMeeting && <motion.section key={activeMeeting.id} className="cex-calendar-selected-meeting" initial={{ opacity: 0, y: reducedMotion ? 0 : 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: reducedMotion ? 0 : 0.16 }} aria-label="Выбранная встреча"><div className="cex-calendar-section-heading"><h2>{dayLabel(dateOf(activeMeeting.meetingDay), true)} · {activeMeeting.meetingTime}</h2><button type="button" className="cex-calendar-icon-button" aria-label="Закрыть детали встречи" onClick={() => setSelectedMeetingId(null)}><X size={18} /></button></div><MeetingCard vacancy={activeMeeting} {...actions} /></motion.section>}</AnimatePresence>
  </div>;
}
