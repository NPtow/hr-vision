import { useEffect, useId, useState, type ReactNode } from 'react';
import { CalendarDays, Video } from 'lucide-react';
import { Button } from '../../components/ui/button';
import type { RecruiterCandidate, Interview } from './api';
import type { ActiveCall } from './RecruiterCall';
import './interview-list.css';

type InterviewCandidate = RecruiterCandidate & {
  source?: { kind: 'hh' | 'manual' | 'registration'; label: string };
  sourceApplications?: { id: string; title: string; company: string; url?: string }[];
};
type InterviewRow = { candidate: InterviewCandidate; interview: Interview; start: number; end: number };
type InterviewListProps = {
  candidates: RecruiterCandidate[];
  busy: boolean;
  videoConfigured: boolean;
  onDetails: (id: string) => void;
  onCall: (call: ActiveCall) => void;
  selectedId?: string;
  renderDetails?: (candidate: RecruiterCandidate) => ReactNode;
};

const minute = 60_000;
const timezone = 'Europe/Moscow';
const dayKey = new Intl.DateTimeFormat('sv-SE', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' });
const dayLabel = new Intl.DateTimeFormat('ru-RU', { timeZone: timezone, weekday: 'long', day: 'numeric', month: 'long' });
const shortDate = new Intl.DateTimeFormat('ru-RU', { timeZone: timezone, day: 'numeric', month: 'long' });
const clock = new Intl.DateTimeFormat('ru-RU', { timeZone: timezone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });

function sourceLabel(candidate: InterviewCandidate) {
  if (candidate.source?.label) return candidate.source.label;
  if (candidate.source?.kind === 'hh' || candidate.intake?.source === 'hh') return 'HH';
  if (candidate.source?.kind === 'manual' || candidate.intake?.source === 'manual') return 'Добавлен рекрутером';
  if (candidate.source?.kind === 'registration') return 'Регистрация в HR Vision';
  return 'Источник не указан';
}

/** Assignment is enforced by the recruiter API, never inferred from a name in the UI. */
export function InterviewList({ candidates, busy, videoConfigured, onDetails, onCall, selectedId, renderDetails }: InterviewListProps) {
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
    {[...groups.entries()].map(([day, interviews]) => <section className="rp-interview-day" key={day} aria-labelledby={`rp-interview-day-${day}`}>
      <h2 id={`rp-interview-day-${day}`}>{day === dayKey.format(now) ? `Сегодня, ${shortDate.format(interviews[0].start)}` : dayLabel.format(interviews[0].start)}</h2>
      <ol className="rp-interview-rows">
        {interviews.map(row => {
          const { candidate, interview, start, end } = row;
          const name = candidate.name || candidate.email || candidate.intake?.contactEmail || 'Кандидат';
          const available = now >= start - 15 * minute && now <= end + 90 * minute;
          const sources = candidate.sourceApplications || [];
          const expanded = selectedId === candidate.id && !!renderDetails;
          const detailId = `${instanceId}-interview-${interview.id}`;
          return <li className="rp-interview-row" key={interview.id} data-joinable={available}>
            <div className="rp-interview-time">
              <time dateTime={interview.start}>{clock.format(start)}</time>
              <span>до {clock.format(end)}</span>
            </div>
            <div className="rp-interview-summary">
              <h3><button type="button" onClick={() => onDetails(candidate.id)} aria-label={`${expanded ? 'Скрыть подробности' : 'Подробнее'}: ${name}`} aria-expanded={renderDetails ? expanded : undefined} aria-controls={expanded ? detailId : undefined}>{name}</button></h3>
            </div>
            {available && <div className="rp-interview-row-actions">
                <Button type="button" disabled={busy || !videoConfigured} onClick={() => onCall({
                  kind: 'interview', candidateId: candidate.id, title: name,
                  accountActive: candidate.accountActive, recording: interview.recording,
                })}><Video size={16} aria-hidden="true"/>{interview.status === 'live' ? 'Продолжить' : 'Подключиться'}</Button>
            </div>}
            {expanded && <div className="rp-interview-expanded" id={detailId}>
              <div className="rp-interview-origin">
                <p className="rp-interview-source">{sourceLabel(candidate)}</p>
                {sources.length > 0 && <ul className="rp-interview-vacancies" aria-label="Исходные вакансии">
                  {sources.map((source, index) => <li key={`${source.id}-${index}`}>{[source.title, source.company].filter(Boolean).join(' · ') || 'Вакансия не указана'}</li>)}
                </ul>}
              </div>
              {renderDetails?.(candidate)}
            </div>}
          </li>;
        })}
      </ol>
    </section>)}
  </section>;
}
