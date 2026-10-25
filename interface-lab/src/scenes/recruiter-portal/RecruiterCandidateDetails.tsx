import type { RecruiterCandidate } from './api';
import { CallProviderSelector } from '../../components/call/CallProviderSelector';
import type { CallProvider } from '../../components/call/call-provider';
import { useInterviewCoach } from './interview-coach';
import { InterviewContacts, InterviewPreparation, ResumeAction } from './InterviewPreparation';
import './interview-coach.css';

export function RecruiterCandidateDetails({ candidate, token, onOpen, onMaterials, onProvider }: {
  candidate: RecruiterCandidate; token: string; onOpen: () => void; onMaterials?: () => void; onProvider?: (provider: CallProvider) => Promise<boolean>;
}) {
  const coach = useInterviewCoach({ candidateId: candidate.id, interviewId: candidate.interview!.id, token });
  const time = (value: string) => new Date(value).toLocaleTimeString('ru-RU', { timeZone: 'Europe/Moscow', hour: '2-digit', minute: '2-digit' });
  return <article className="ric-candidate-card" aria-label={`Интервью: ${candidate.name}`}>
    <header className="ric-candidate-heading">
      <div><h3>{candidate.name || 'Кандидат'}</h3><p>{new Date(candidate.interview!.start).toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow', day: 'numeric', month: 'long' })} · {time(candidate.interview!.start)}–{time(candidate.interview!.end)} МСК</p></div>
      <div className="ric-actions"><InterviewContacts candidate={candidate}/><ResumeAction coach={coach} name={candidate.name}/></div>
    </header>
    {onProvider && candidate.interview!.status !== 'completed' && <CallProviderSelector
      value={candidate.interview!.callProvider || 'daily'} locked={candidate.interview!.callProviderLocked}
      options={candidate.interview!.callProviderOptions} onChange={provider => { void onProvider(provider); }}/ >}
    <InterviewPreparation coach={coach} onOpen={onOpen}/>
    {onMaterials && <button className="ric-link" onClick={onMaterials}>Материалы и заметки</button>}
  </article>;
}
