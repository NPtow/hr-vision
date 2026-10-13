import { Fragment, useEffect, useId, useState, type ReactNode } from 'react';
import { CalendarDays, Video } from 'lucide-react';
import { Button } from '../../components/ui/button';
import type { RecruiterCandidate, Interview } from './api';
import type { ActiveCall } from './RecruiterCall';
import './interview-list.css';

type InterviewRow = { candidate: RecruiterCandidate; interview: Interview; start: number; end: number };
type InterviewListProps = {
  candidates: RecruiterCandidate[];
  busy: boolean;
  videoConfigured: boolean;
  onDetails: (id: string) => void;
  onCall: (call: ActiveCall) => void;
  selectedId?: string;
  renderDetails?: (candidate: RecruiterCandidate) => ReactNode;
  readOnly?: boolean;
};

const minute = 60_000;
const timezone = 'Europe/Moscow';
const dayKey = new Intl.DateTimeFormat('sv-SE', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' });
const dayLabel = new Intl.DateTimeFormat('ru-RU', { timeZone: timezone, weekday: 'long', day: 'numeric', month: 'long' });
const monthLabel = new Intl.DateTimeFormat('ru-RU', { timeZone: timezone, month: 'long', year: 'numeric' });
const dayNumber = new Intl.DateTimeFormat('ru-RU', { timeZone: timezone, day: 'numeric' });
const weekday = new Intl.DateTimeFormat('ru-RU', { timeZone: timezone, weekday: 'short' });
const clock = new Intl.DateTimeFormat('ru-RU', { timeZone: timezone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });

/** Assignment is enforced by the recruiter API, never inferred from a name in the UI. */
export function InterviewList({ candidates, busy, videoConfigured, onDetails, onCall, selectedId, renderDetails, readOnly = false }: InterviewListProps) {
  const instanceId = useId();
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const update = () => setNow(Date.now());
    const timer = window.setInterval(update, 15_000);
    document.addEventListener('visibilitychange', update);
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', update); };
  }, []);

  const rows: InterviewRow[] = candidates.flatMap(candidate => {
    const interview = candidate.interview;
    if (!interview || (interview.status !== 'booked' && interview.status !== 'live')) return [];
    const start = Date.parse(interview.start), end = Date.parse(interview.end);
    if (!Number.isFinite(start) || !Number.isFinite(end)) return [];
    // Keep the existing joining grace period visible. Completed interviews and
    // candidates who have not booked a slot are not part of this work queue.
    if (interview.status !== 'live' && now > end + 90 * minute) return [];
    return [{ candidate, interview, start, end }];
  }).sort((a, b) => a.start - b.start || a.interview.id.localeCompare(b.interview.id));
  const groups = new Map<string, InterviewRow[]>();
  rows.forEach(row => {
    const day = dayKey.format(row.start);
    const group = groups.get(day);
    if (group) group.push(row); else groups.set(day, [row]);
  });

  if (!rows.length) return <section className="rp-interviews-empty" aria-label="Интервью">
    <CalendarDays size={30} strokeWidth={1.5} aria-hidden="true"/>
    <h2>Пока нет назначенных интервью</h2>
    <p>Когда кандидат выберет ваше свободное время, интервью появится здесь.</p>
  </section>;

  return <section className="rp-interviews" aria-label="Интервью по времени">
    {[...groups.entries()].map(([day, interviews], index, days) => <Fragment key={day}>
      {(index === 0 || day.slice(0, 7) !== days[index - 1][0].slice(0, 7)) && <h2 className="rp-agenda-month">{monthLabel.format(interviews[0].start)}</h2>}
      <section className="rp-interview-day" aria-labelledby={`${instanceId}-day-${day}`}>
      <h3 className="rp-agenda-date" id={`${instanceId}-day-${day}`} data-today={day === dayKey.format(now)} aria-label={`${day === dayKey.format(now) ? 'Сегодня, ' : ''}${dayLabel.format(interviews[0].start)}`}>
        <time dateTime={day}>
          <span className="rp-agenda-date-number" aria-hidden="true">{dayNumber.format(interviews[0].start)}</span>
          <span className="rp-agenda-weekday" aria-hidden="true">{weekday.format(interviews[0].start)}</span>
        </time>
      </h3>
      <ol className="rp-interview-rows">
        {interviews.map(row => {
          const { candidate, interview, start, end } = row;
          const name = candidate.name || candidate.email || candidate.intake?.contactEmail || 'Кандидат';
          const available = !readOnly && now >= start - 15 * minute && now <= end + 90 * minute;
          const expanded = selectedId === candidate.id && !!renderDetails;
          const detailId = `${instanceId}-interview-${interview.id}`;
          return <li className="rp-interview-row" key={interview.id} data-joinable={available}>
            <div className="rp-interview-summary">
              <h4><button type="button" onClick={() => onDetails(candidate.id)} aria-label={`${expanded ? 'Скрыть подробности' : 'Подробнее'}: ${name}`} aria-expanded={renderDetails ? expanded : undefined} aria-controls={expanded ? detailId : undefined}>{name}</button></h4>
              <p className="rp-interview-time"><time dateTime={interview.start}>{clock.format(start)}</time><span aria-hidden="true">–</span><time dateTime={interview.end}>{clock.format(end)}</time></p>
            </div>
            {available && <div className="rp-interview-row-actions">
                <Button type="button" disabled={busy || !videoConfigured} onClick={() => onCall({
                  kind: 'interview', candidateId: candidate.id, title: name,
                  accountActive: candidate.accountActive, recording: interview.recording,
                })}><Video size={16} aria-hidden="true"/>{interview.status === 'live' ? 'Продолжить' : 'Подключиться'}</Button>
            </div>}
            {expanded && <div className="rp-interview-expanded" id={detailId}>
              {renderDetails?.(candidate)}
            </div>}
          </li>;
        })}
      </ol>
    </section></Fragment>)}
  </section>;
}
