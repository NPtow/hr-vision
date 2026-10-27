import { Fragment, useEffect, useId, useState } from 'react';
import { CalendarDays, ArrowRight } from 'lucide-react';
import { Button } from '../../components/ui/button';
import type { Person } from '../connected-journey/api';
import { agendaClock, agendaDayNumber, agendaFullDate, agendaMonth, agendaWeekday, employerAgendaDays } from './agenda-model';
import './employer-agenda.css';

const statusLabels = { pending: 'Ждём подтверждения', confirmed: 'Подтверждена', live: 'Идёт сейчас', cancelled: '', completed: '' };
const statusDetails = {
  pending: 'Время отправлено кандидату. Встреча появится как подтверждённая после его ответа.',
  confirmed: 'Кандидат подтвердил время. Откройте встречу, чтобы подключиться или выбрать другое время.',
  live: 'Комната открыта. Можно вернуться в текущий разговор.',
  cancelled: '', completed: '',
};

export function EmployerAgenda({ people, busy, fallbackVacancy, onOpen, onChooseCandidate }: {
  people: Person[]; busy: boolean; fallbackVacancy?: string;
  onOpen: (person: Person) => void; onChooseCandidate: () => void;
}) {
  const instanceId = useId();
  const [expanded, setExpanded] = useState('');
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const update = () => setNow(Date.now());
    const timer = window.setInterval(update, 15_000);
    document.addEventListener('visibilitychange', update);
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', update); };
  }, []);
  const days = employerAgendaDays(people, now);

  return <main className="ew-agenda" aria-labelledby={`${instanceId}-heading`}>
    <header className="ew-agenda-heading">
      <div><h1 id={`${instanceId}-heading`}>Встречи</h1><p>Московское время</p></div>
      <Button variant="secondary" disabled={busy} onClick={onChooseCandidate}>Выбрать кандидата<ArrowRight size={16} aria-hidden="true"/></Button>
    </header>
    {!days.length ? <section className="ew-agenda-empty" aria-label="Ближайшие встречи">
      <CalendarDays size={30} strokeWidth={1.5} aria-hidden="true"/>
      <h2>Пока нет предстоящих встреч</h2>
      <p>Здесь появятся встречи с кандидатами по вашим вакансиям.</p>
    </section> : <div className="ew-agenda-days">
      {days.map((day, index) => <Fragment key={day.date}>
        {(index === 0 || day.date.slice(0, 7) !== days[index - 1].date.slice(0, 7)) && <h2 className="ew-agenda-month">{agendaMonth.format(day.meetings[0].start)}</h2>}
        <section className="ew-agenda-day" aria-labelledby={`${instanceId}-day-${day.date}`}>
          <h3 className="ew-agenda-date" id={`${instanceId}-day-${day.date}`} aria-label={agendaFullDate.format(day.meetings[0].start)}>
            <time dateTime={day.date}><span className="ew-agenda-date-number" aria-hidden="true">{agendaDayNumber.format(day.meetings[0].start)}</span><span className="ew-agenda-weekday" aria-hidden="true">{agendaWeekday.format(day.meetings[0].start)}</span></time>
          </h3>
          <ol className="ew-agenda-events">
            {day.meetings.map(({ person, meeting, start, end }) => {
              const open = expanded === meeting.id;
              const panelId = `${instanceId}-meeting-${meeting.id}`;
              return <li className="ew-agenda-event" key={meeting.id} data-status={meeting.status}>
                <div className="ew-agenda-event-top">
                  <div className="ew-agenda-summary">
                    <h4><button type="button" className="ew-agenda-name" aria-expanded={open} aria-controls={panelId} onClick={() => setExpanded(open ? '' : meeting.id)}>{person.name || 'Кандидат'}</button></h4>
                    <p className="ew-agenda-vacancy">{person.vacancyRole || fallbackVacancy || 'Вакансия'}</p>
                    <p className="ew-agenda-time"><time dateTime={meeting.start}>{agendaClock.format(start)}</time><span aria-hidden="true">–</span><time dateTime={new Date(end).toISOString()}>{agendaClock.format(end)}</time><span className="ew-agenda-status">{statusLabels[meeting.status]}</span></p>
                  </div>
                  <Button variant={meeting.status === 'live' ? 'default' : 'secondary'} disabled={busy} className="ew-agenda-open" aria-label={`Открыть встречу: ${person.name || 'Кандидат'}`} onClick={() => onOpen(person)}>{meeting.status === 'live' ? 'Вернуться во встречу' : 'Открыть встречу'}</Button>
                </div>
                <div id={panelId} hidden={!open} className="ew-agenda-details">
                  <p>{statusDetails[meeting.status]}</p>
                  {person.summary && <p>{person.summary}</p>}
                </div>
              </li>;
            })}
          </ol>
        </section>
      </Fragment>)}
    </div>}
  </main>;
}
