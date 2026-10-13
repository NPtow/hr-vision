import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { ArrowRight, CalendarDays, Check, ChevronLeft, ChevronRight, Clock3, Globe2, Plus, Settings2, Video, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { AvailabilityForm } from './AvailabilityForm';
import { applicationGroup, currentApplicationMeeting } from './workspace-model';
import { localDate, timeLabel, timeZone, type Availability, type CandidateApplication, type CandidateMeetingHistory, type CandidateState } from './api';
import './candidate-schedule.css';

export type CandidateScheduleProps = {
  candidate: CandidateState;
  busy: boolean;
  onAvailability: (availability: Availability[]) => Promise<boolean>;
  onMeetingReply: (application: CandidateApplication, action: 'confirm' | 'decline') => Promise<boolean>;
  onOpenVacancy: (applicationId: string) => void;
  onJoin: (applicationId: string, meetingId?: string) => void;
  onInterview: () => void;
  onBookInterview: () => void;
};

type ScheduleEntry = {
  id: string;
  start: string;
  end: string;
  status: CandidateMeetingHistory['status'] | 'booked';
  title: string;
  company: string;
  application?: CandidateApplication;
  meetingId?: string;
  current: boolean;
  archived: boolean;
  history: boolean;
  cancellationReason?: CandidateMeetingHistory['cancellationReason'];
};

const validDate = (value: string) => Number.isFinite(new Date(value).getTime());
const dayStart = (value: Date) => new Date(value.getFullYear(), value.getMonth(), value.getDate());
const addDays = (value: Date, days: number) => new Date(value.getFullYear(), value.getMonth(), value.getDate() + days);
const weekStart = (value: Date) => addDays(dayStart(value), -((value.getDay() + 6) % 7));
const dateText = (value: Date) => new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' }).format(value);
const monthText = (value: Date) => new Intl.DateTimeFormat('ru-RU', { month: 'long', year: 'numeric' }).format(value).replace(' г.', '');
const weekdayText = (value: Date) => new Intl.DateTimeFormat('ru-RU', { weekday: 'short' }).format(value);
const countText = (count: number) => `${count} ${count % 10 === 1 && count % 100 !== 11 ? 'встреча' : count % 10 >= 2 && count % 10 <= 4 && (count % 100 < 12 || count % 100 > 14) ? 'встречи' : 'встреч'}`;
const durationText = (entry: ScheduleEntry) => {
  const minutes = Math.round((new Date(entry.end).getTime() - new Date(entry.start).getTime()) / 60000);
  return Number.isFinite(minutes) && minutes > 0 ? `${minutes} мин` : '';
};

function entriesFor(candidate: CandidateState, now: number): ScheduleEntry[] {
  const entries: ScheduleEntry[] = [];
  for (const application of candidate.applications) {
    const meetings = new Map<string, CandidateMeetingHistory>();
    for (const meeting of application.meetings ?? []) meetings.set(meeting.id, meeting);
    if (application.meeting && !meetings.has(application.meeting.id)) meetings.set(application.meeting.id, application.meeting);
    const currentMeeting = currentApplicationMeeting(application);
    if (currentMeeting) meetings.set(currentMeeting.id, { ...meetings.get(currentMeeting.id), ...currentMeeting });
    for (const meeting of meetings.values()) {
      const current = currentMeeting?.id === meeting.id;
      const archived = applicationGroup(application) === 'history' || candidate.searching === false;
      const history = archived || !current || ['completed', 'cancelled', 'declined'].includes(meeting.status) || (validDate(meeting.end) && new Date(meeting.end).getTime() + (meeting.status === 'pending' ? 0 : 90 * 60000) < now) || (meeting.status === 'pending' && validDate(meeting.start) && new Date(meeting.start).getTime() <= now);
      entries.push({ ...meeting, meetingId: meeting.id, id: `${application.id}:${meeting.id}`, title: application.vacancy.title, company: application.vacancy.company, application, current, archived, history });
    }
  }
  if (candidate.interview) {
    const interview = candidate.interview;
    const archived = candidate.searching === false && interview.status !== 'completed';
    entries.push({ ...interview, id: `recruiter:${interview.id}`, title: 'Интервью с рекрутером', company: interview.recruiterName || 'HR Vision', current: true, archived, history: archived || interview.status === 'completed' || (validDate(interview.end) && new Date(interview.end).getTime() + 90 * 60000 < now) });
  }
  return entries.sort((a, b) => {
    const aTime = validDate(a.start) ? new Date(a.start).getTime() : Number.MAX_SAFE_INTEGER;
    const bTime = validDate(b.start) ? new Date(b.start).getTime() : Number.MAX_SAFE_INTEGER;
    return aTime - bTime || a.id.localeCompare(b.id);
  });
}

function statusText(entry: ScheduleEntry) {
  if (entry.status === 'completed') return 'Встреча завершена';
  if (entry.status === 'cancelled') return entry.cancellationReason === 'rescheduled' ? 'Время изменено' : 'Встреча отменена';
  if (entry.status === 'declined') return 'Время отклонено';
  if (entry.archived) return entry.status === 'pending' ? 'Предложенное время · в архиве' : 'Назначенная встреча · в архиве';
  if (entry.history) return entry.status === 'pending' ? 'Предложенное время' : 'Назначенная встреча';
  if (entry.status === 'pending') return 'Ждёт подтверждения';
  if (entry.status === 'live') return 'Встреча идёт';
  return 'Подтверждено';
}

function ScheduleCard({ entry, busy, onMeetingReply, onOpenVacancy, onJoin, onInterview }: Pick<CandidateScheduleProps, 'busy' | 'onMeetingReply' | 'onOpenVacancy' | 'onJoin' | 'onInterview'> & { entry: ScheduleEntry }) {
  const application = entry.application;
  const active = entry.current && !entry.history && !entry.archived;
  const future = validDate(entry.start) && new Date(entry.start).getTime() > Date.now();
  const initials = entry.company.trim().split(/\s+/).slice(0, 2).map(word => word[0]).join('').toUpperCase();
  return <article className="cp-schedule-card">
    <div className="cp-schedule-card-top"><span className={`cp-schedule-status${active && entry.status === 'pending' ? ' cp-schedule-status-pending' : ''}${entry.status === 'completed' ? ' cp-schedule-status-completed' : ''}`}><i />{statusText(entry)}</span><span className="cp-schedule-duration"><Clock3 size={13} />{durationText(entry)}</span></div>
    <div className="cp-schedule-card-identity"><span className={`cp-schedule-company${!application ? ' cp-schedule-company-recruiter' : ''}`}>{application ? initials || <CalendarDays size={19} /> : <Video size={19} />}</span><div><p>{entry.company}</p><h3>{entry.title}</h3></div></div>
    <div className="cp-schedule-card-actions">
      {application && active && future && entry.status === 'pending' && <><Button disabled={busy} onClick={() => void onMeetingReply(application, 'confirm')}><Check size={16} />Подтвердить время</Button><Button variant="ghost" disabled={busy} onClick={() => void onMeetingReply(application, 'decline')}>Не подходит</Button></>}
      {active && ['confirmed', 'booked', 'live'].includes(entry.status) && <Button disabled={busy} onClick={() => application ? onJoin(application.id, entry.meetingId) : onInterview()}><Video size={16} />Подготовиться и войти</Button>}
      {application && active && future && entry.status === 'confirmed' && <Button variant="ghost" disabled={busy} onClick={() => void onMeetingReply(application, 'decline')}>Не подходит время</Button>}
      {entry.status === 'completed' && <Button variant="secondary" disabled={busy} onClick={() => application ? onJoin(application.id, entry.meetingId) : onInterview()}><Video size={16} />Материалы встречи</Button>}
      {application && <button type="button" className="cp-schedule-link" onClick={() => onOpenVacancy(application.id)}>О вакансии<ArrowRight size={15} /></button>}
    </div>
  </article>;
}

export function CandidateSchedule({ candidate, busy, onAvailability, onMeetingReply, onOpenVacancy, onJoin, onInterview, onBookInterview }: CandidateScheduleProps) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 60000); return () => window.clearInterval(timer); }, []);
  const [section, setSection] = useState<'meetings' | 'availability'>('meetings');
  const [history, setHistory] = useState(false);
  const [period, setPeriod] = useState<'week' | 'month'>('week');
  const [cursor, setCursor] = useState(() => new Date());
  const [editing, setEditing] = useState(false);
  const [saved, setSaved] = useState(false);
  const editButton = useRef<HTMLButtonElement>(null);
  const editor = useRef<HTMLDivElement>(null);
  useEffect(() => { if (editing) editor.current?.querySelector<HTMLElement>('input,button')?.focus(); }, [editing]);
  const id = useId();
  const entries = useMemo(() => entriesFor(candidate, now), [candidate, now]);
  const selectedEntries = entries.filter(entry => entry.history === history);
  const rangeStart = period === 'week' ? weekStart(cursor) : new Date(cursor.getFullYear(), cursor.getMonth(), 1);
  const rangeEnd = period === 'week' ? addDays(rangeStart, 7) : new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1);
  const visibleEntries = selectedEntries.filter(entry => !validDate(entry.start) || (new Date(entry.start) >= rangeStart && new Date(entry.start) < rangeEnd));
  const orderedEntries = history ? [...visibleEntries].reverse() : visibleEntries;
  const groups = new Map<string, ScheduleEntry[]>();
  for (const entry of orderedEntries) {
    const key = validDate(entry.start) ? localDate(new Date(entry.start)) : 'unknown';
    groups.set(key, [...(groups.get(key) ?? []), entry]);
  }
  const timezone = timeZone();
  const today = new Date();
  const periodLabel = period === 'month' ? monthText(cursor) : `${dateText(rangeStart)} — ${dateText(addDays(rangeEnd, -1))}${rangeStart.getFullYear() !== today.getFullYear() ? ` ${rangeStart.getFullYear()}` : ''}`;
  const available = [...candidate.availability].sort((a, b) => new Date(a.start).getTime() - new Date(b.start).getTime());
  const closeEditor = () => { setEditing(false); requestAnimationFrame(() => editButton.current?.focus()); };
  const changeHistory = (next: boolean) => {
    setHistory(next);
    const target = next ? [...entries].reverse().find(entry => entry.history && validDate(entry.start)) : entries.find(entry => !entry.history && validDate(entry.start));
    setCursor(target ? new Date(target.start) : new Date());
  };
  const move = (direction: number) => setCursor(previous => period === 'week' ? addDays(previous, direction * 7) : new Date(previous.getFullYear(), previous.getMonth() + direction, 1));
  const jumpEntry = (history ? [...selectedEntries].reverse() : selectedEntries).find(entry => validDate(entry.start));
  const canBook = candidate.searching !== false && (!candidate.interview || candidate.interview.status === 'completed');

  return <section className="cp-schedule" aria-busy={busy}>
    <header className="cp-schedule-heading"><div><h1>Календарь</h1><p>Встречи и удобное для вас время</p></div><span className="cp-schedule-timezone"><Globe2 size={14} />{timezone}</span></header>
    <div className="cp-schedule-sections" role="group" aria-label="Раздел календаря"><button type="button" aria-pressed={section === 'meetings'} onClick={() => setSection('meetings')}><CalendarDays size={17} />Встречи</button><button type="button" aria-pressed={section === 'availability'} onClick={() => { setSection('availability'); setSaved(false); }}><Clock3 size={17} />Моя доступность</button></div>
    {section === 'meetings' ? <>
      <div className="cp-schedule-viewbar"><div className="cp-schedule-segment" role="group" aria-label="Встречи по статусу"><button type="button" aria-pressed={!history} onClick={() => changeHistory(false)}>Предстоящие<span>{entries.filter(entry => !entry.history).length}</span></button><button type="button" aria-pressed={history} onClick={() => changeHistory(true)}>История<span>{entries.filter(entry => entry.history).length}</span></button></div>{canBook && <button type="button" className="cp-schedule-link" disabled={busy} onClick={onBookInterview}><Plus size={15} />Интервью с рекрутером</button>}</div>
      <div className="cp-schedule-toolbar"><div className="cp-schedule-period"><h2>{periodLabel}</h2><div><button type="button" className="cp-schedule-icon" aria-label={period === 'week' ? 'Предыдущая неделя' : 'Предыдущий месяц'} onClick={() => move(-1)}><ChevronLeft size={18} /></button><button type="button" className="cp-schedule-icon" aria-label={period === 'week' ? 'Следующая неделя' : 'Следующий месяц'} onClick={() => move(1)}><ChevronRight size={18} /></button></div><button type="button" className="cp-schedule-today" onClick={() => setCursor(new Date())}>Сегодня</button></div><div className="cp-schedule-segment cp-schedule-range" role="group" aria-label="Показать период"><button type="button" aria-pressed={period === 'week'} onClick={() => setPeriod('week')}>Неделя</button><button type="button" aria-pressed={period === 'month'} onClick={() => setPeriod('month')}>Месяц</button></div></div>
      <div className="cp-schedule-agenda" aria-label={history ? 'История встреч' : 'Предстоящие встречи'}>
        {[...groups].map(([key, items]) => {
          const date = key === 'unknown' ? null : new Date(`${key}T12:00:00`);
          return <section className="cp-schedule-date-group" key={key} aria-label={date ? dateText(date) : 'Дата не указана'}><div className={`cp-schedule-date${date && localDate(date) === localDate(today) ? ' cp-schedule-date-today' : ''}`}><strong>{date?.getDate() ?? '—'}</strong><span>{date ? `${weekdayText(date)} · ${new Intl.DateTimeFormat('ru-RU', { month: 'long' }).format(date)}` : 'Без даты'}</span>{date && localDate(date) === localDate(today) && <small>Сегодня</small>}</div><div className="cp-schedule-date-entries">{items.map(entry => <div className="cp-schedule-entry" key={entry.id}><div className="cp-schedule-entry-time"><strong>{validDate(entry.start) ? timeLabel(entry.start) : '—'}</strong><span>{validDate(entry.end) ? timeLabel(entry.end) : ''}</span></div><ScheduleCard entry={entry} busy={busy} onMeetingReply={onMeetingReply} onOpenVacancy={onOpenVacancy} onJoin={onJoin} onInterview={onInterview} /></div>)}</div></section>;
        })}
        {!visibleEntries.length && <div className="cp-schedule-empty"><span><CalendarDays size={26} /></span><h3>{history ? 'Здесь пока нет встреч' : 'На этот период встреч нет'}</h3><p>{selectedEntries.length ? 'Выберите другой период, чтобы увидеть встречи.' : history ? 'Здесь сохранятся прошедшие, отменённые и архивные встречи.' : candidate.searching === false ? 'Поиск работы приостановлен. Прошлые встречи доступны в истории.' : 'Когда работодатель предложит время, встреча появится здесь.'}</p>{jumpEntry && <Button variant="secondary" onClick={() => setCursor(new Date(jumpEntry.start))}>{history ? 'К последней встрече' : 'К ближайшей встрече'}<ArrowRight size={15} /></Button>}{!selectedEntries.length && !history && !candidate.interview && canBook && <Button disabled={busy} onClick={onBookInterview}>Выбрать время с рекрутером<ArrowRight size={15} /></Button>}</div>}
      </div>
      {!!visibleEntries.length && <p className="cp-schedule-total">{countText(visibleEntries.length)} · {timezone}</p>}
    </> : <section className="cp-schedule-availability" aria-labelledby={`${id}-availability`}><div className="cp-schedule-availability-heading"><div><h2 id={`${id}-availability`}>Когда вам удобно</h2><p>Работодатель сможет предложить встречу в этих интервалах.</p></div>{!editing && <Button ref={editButton} variant="outline" disabled={busy} onClick={() => { setEditing(true); setSaved(false); }}><Settings2 size={16} />Изменить</Button>}{editing && <button type="button" className="cp-schedule-icon" disabled={busy} aria-label="Отменить изменение доступности" onClick={closeEditor}><X size={18} /></button>}</div>
      {saved && <p className="cp-schedule-saved" role="status"><Check size={16} />Удобное время сохранено</p>}
      {editing ? <div ref={editor} className="cp-schedule-availability-form" onKeyDown={event => { if (event.key === 'Escape' && !busy) { event.stopPropagation(); closeEditor(); } }}><fieldset disabled={busy}><AvailabilityForm initial={candidate.availability} busy={busy} onSubmit={async windows => { if (await onAvailability(windows)) { setSaved(true); closeEditor(); } }} /></fieldset><button type="button" className="cp-schedule-link cp-schedule-cancel" disabled={busy} onClick={closeEditor}>Отмена</button></div> : available.length ? <div className="cp-schedule-availability-list">{available.map((window, index) => <div key={`${window.start}:${window.end}:${index}`}><CalendarDays size={17} /><strong>{validDate(window.start) ? dateText(new Date(window.start)) : 'Дата не указана'}</strong><span>{validDate(window.start) ? timeLabel(window.start) : '—'} — {validDate(window.end) ? `${localDate(new Date(window.start)) !== localDate(new Date(window.end)) ? `${dateText(new Date(window.end))}, ` : ''}${timeLabel(window.end)}` : '—'}</span></div>)}</div> : <div className="cp-schedule-availability-empty"><Clock3 size={24} /><h3>Добавьте удобное время</h3><p>Выберите даты и интервалы. Время каждой встречи вы подтвердите отдельно.</p><Button disabled={busy} onClick={() => setEditing(true)}><Plus size={16} />Добавить время</Button></div>}
      <p className="cp-schedule-availability-foot">Часовой пояс: {timezone}. Изменение доступности не переносит назначенные встречи.</p>
    </section>}
  </section>;
}
