import { Fragment, useEffect, useId, useMemo, useState } from 'react';
import { ArrowRight, CalendarDays, Check, Clock3, Globe2, Plus, Video } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { CandidateAvailability } from './CandidateAvailability';
import { applicationGroup, currentApplicationMeeting } from './workspace-model';
import type { AvailabilitySchedule, CandidateApplication, CandidateMeetingHistory, CandidateState } from './api';
import { effectiveTimezone, scheduleDateKey, scheduleLabel, scheduleTime, timezoneOptions } from './schedule-time';
import './candidate-schedule.css';

export type CandidateScheduleProps = {
  candidate: CandidateState;
  section: 'meetings' | 'availability';
  onSectionChange: (section: 'meetings' | 'availability') => void;
  busy: boolean;
  onAvailability: (schedule: AvailabilitySchedule) => Promise<boolean>;
  onTimezone: (timezone: string) => Promise<boolean>;
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

function ScheduleCard({ entry, timezone, busy, onMeetingReply, onOpenVacancy, onJoin, onInterview }: Pick<CandidateScheduleProps, 'busy' | 'onMeetingReply' | 'onOpenVacancy' | 'onJoin' | 'onInterview'> & { entry: ScheduleEntry; timezone: string }) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const application = entry.application;
  const active = entry.current && !entry.history && !entry.archived;
  const future = validDate(entry.start) && new Date(entry.start).getTime() > Date.now();
  return <article className="cp-schedule-card">
    <h3><button type="button" aria-expanded={expanded} aria-controls={id} onClick={() => setExpanded(value => !value)}>{entry.title}</button></h3>
    <p className="cp-schedule-event-time">{validDate(entry.start) ? scheduleTime(entry.start, timezone) : '—'}–{validDate(entry.end) ? scheduleTime(entry.end, timezone) : '—'} · {entry.company}</p>
    {!expanded && active && entry.status === 'pending' && <p className="cp-schedule-attention">Подтвердите время</p>}
    {expanded && <div id={id} className="cp-schedule-details">
      <p className="cp-schedule-status">{statusText(entry)} · {durationText(entry)}</p>
      <div className="cp-schedule-card-actions">
        {application && active && future && entry.status === 'pending' && <><Button disabled={busy} onClick={() => void onMeetingReply(application, 'confirm')}><Check size={16} />Подтвердить время</Button><Button variant="ghost" disabled={busy} onClick={() => void onMeetingReply(application, 'decline')}>Не подходит</Button></>}
        {active && ['confirmed', 'booked', 'live'].includes(entry.status) && <Button disabled={busy} onClick={() => application ? onJoin(application.id, entry.meetingId) : onInterview()}><Video size={16} />Войти во встречу</Button>}
        {application && active && future && entry.status === 'confirmed' && <Button variant="ghost" disabled={busy} onClick={() => void onMeetingReply(application, 'decline')}>Не подходит время</Button>}
        {entry.status === 'completed' && <Button variant="secondary" disabled={busy} onClick={() => application ? onJoin(application.id, entry.meetingId) : onInterview()}><Video size={16} />Материалы встречи</Button>}
        {application && <button type="button" className="cp-schedule-link" onClick={() => onOpenVacancy(application.id)}>О вакансии<ArrowRight size={15} /></button>}
      </div>
    </div>}
  </article>;
}

export function CandidateSchedule({ candidate, section, onSectionChange, busy, onAvailability, onTimezone, onMeetingReply, onOpenVacancy, onJoin, onInterview, onBookInterview }: CandidateScheduleProps) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 30000); return () => window.clearInterval(timer); }, []);
  const [history, setHistory] = useState(false);
  const [editing, setEditing] = useState(false);
  // Browser back can leave the editor without its Cancel action.
  useEffect(() => { setEditing(false); }, [section]);
  const [zoneSaving, setZoneSaving] = useState(false);
  const [zoneMessage, setZoneMessage] = useState('');
  const timezone = effectiveTimezone(candidate.timezone);
  const zones = useMemo(() => timezoneOptions(timezone), [timezone]);
  const entries = useMemo(() => entriesFor(candidate, now), [candidate, now]);
  const ordered = entries.filter(entry => entry.history === history);
  if (history) ordered.reverse();
  const groups = new Map<string, ScheduleEntry[]>();
  for (const entry of ordered) {
    const key = validDate(entry.start) ? scheduleDateKey(entry.start, timezone) : 'unknown';
    groups.set(key, [...(groups.get(key) ?? []), entry]);
  }
  const today = scheduleDateKey(now, timezone);
  const canBook = candidate.searching !== false && (!candidate.interview || candidate.interview.status === 'completed');
  async function changeZone(value: string) {
    if (zoneSaving || busy || editing) return;
    setZoneSaving(true); setZoneMessage('');
    try { if (await onTimezone(value)) setZoneMessage('Часовой пояс сохранён'); }
    finally { setZoneSaving(false); }
  }
  return <section className="cp-schedule" aria-busy={busy || zoneSaving}>
    <header className="cp-schedule-heading"><h1>Календарь</h1><label className="cp-schedule-timezone"><Globe2 size={17} aria-hidden="true"/><select aria-label="Часовой пояс" value={timezone} disabled={busy || editing || zoneSaving} onChange={event => void changeZone(event.target.value)}>{zones.map(zone => <option value={zone.value} key={zone.value}>{zone.label}</option>)}</select></label></header>
    <span className="cp-schedule-sr-only" role="status">{zoneMessage}</span>
    <div className="cp-schedule-sections" role="group" aria-label="Раздел календаря"><button type="button" disabled={editing} aria-pressed={section === 'meetings'} onClick={() => onSectionChange('meetings')}><CalendarDays size={17} />Встречи</button><button type="button" aria-pressed={section === 'availability'} onClick={() => onSectionChange('availability')}><Clock3 size={17} />Моя доступность</button></div>
    {section === 'meetings' ? <>
      <div className="cp-schedule-viewbar"><div className="cp-schedule-segment" role="group" aria-label="Встречи по статусу"><button type="button" aria-pressed={!history} onClick={() => setHistory(false)}>Предстоящие<span>{entries.filter(entry => !entry.history).length}</span></button><button type="button" aria-pressed={history} onClick={() => setHistory(true)}>История<span>{entries.filter(entry => entry.history).length}</span></button></div>{canBook && <button type="button" className="cp-schedule-link" disabled={busy} onClick={onBookInterview}><Plus size={16} />Интервью с рекрутером</button>}</div>
      <div className="cp-schedule-agenda" aria-label={history ? 'История встреч' : 'Предстоящие встречи'}>
        {[...groups].map(([key, items], index, days) => <Fragment key={key}>
          {(index === 0 || key.slice(0, 7) !== days[index - 1][0].slice(0, 7)) && <h2 className="cp-schedule-month">{key === 'unknown' ? 'Без даты' : scheduleLabel(items[0].start, timezone, { month: 'long', year: 'numeric' })}</h2>}
          <section className="cp-schedule-date-group" aria-label={key === 'unknown' ? 'Дата не указана' : scheduleLabel(items[0].start, timezone, { day: 'numeric', month: 'long', year: 'numeric' })}>
            <h3 className="cp-schedule-date" data-today={key === today}><time dateTime={key === 'unknown' ? undefined : key}><strong>{key === 'unknown' ? '—' : Number(key.slice(-2))}</strong><span>{key === 'unknown' ? '' : scheduleLabel(items[0].start, timezone, { weekday: 'short' })}</span></time></h3>
            <div className="cp-schedule-date-entries">{items.map(entry => <ScheduleCard key={entry.id} entry={entry} timezone={timezone} busy={busy} onMeetingReply={onMeetingReply} onOpenVacancy={onOpenVacancy} onJoin={onJoin} onInterview={onInterview}/>)}</div>
          </section>
        </Fragment>)}
        {!ordered.length && <div className="cp-schedule-empty"><CalendarDays size={30} strokeWidth={1.5} aria-hidden="true"/><h2>{history ? 'Прошедших встреч пока нет' : 'Пока нет назначенных встреч'}</h2>{!history && !candidate.interview && canBook && <Button disabled={busy} onClick={onBookInterview}>Выбрать время<ArrowRight size={16}/></Button>}</div>}
      </div>
    </> : <CandidateAvailability key={timezone} initial={candidate.availabilityManual ?? candidate.availability} schedule={candidate.availabilitySchedule} timezone={timezone} busy={busy} onSave={onAvailability} onEditing={setEditing}/>}
  </section>;
}
