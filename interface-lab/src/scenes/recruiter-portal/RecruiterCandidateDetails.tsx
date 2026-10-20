import type { RecruiterCandidate } from './api';
import { useInterviewCoach } from './interview-coach';
import { InterviewContacts, InterviewPreparation, ResumeAction } from './InterviewPreparation';
import './interview-coach.css';

export function RecruiterCandidateDetails({ candidate, token, onOpen }: {
  candidate: RecruiterCandidate; token: string; onOpen: () => void;
}) {
  const coach = useInterviewCoach({ candidateId: candidate.id, interviewId: candidate.interview!.id, token });
  const time = (value: string) => new Date(value).toLocaleTimeString('ru-RU', { timeZone: 'Europe/Moscow', hour: '2-digit', minute: '2-digit' });
  return <article className="ric-candidate-card" aria-label={`Интервью: ${candidate.name}`}>
    <header className="ric-candidate-heading">
      <div><h3>{candidate.name || 'Кандидат'}</h3><p>{new Date(candidate.interview!.start).toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow', day: 'numeric', month: 'long' })} · {time(candidate.interview!.start)}–{time(candidate.interview!.end)} МСК</p></div>
      <div className="ric-actions"><InterviewContacts candidate={candidate}/><ResumeAction coach={coach} name={candidate.name}/></div>
    </header>
    <InterviewPreparation coach={coach} onOpen={onOpen}/>
  </article>;
}
