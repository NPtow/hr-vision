import { useEffect, useState } from 'react';
import { Video } from 'lucide-react';
import { formatDate, type RecruiterCandidate } from './api';
import { useInterviewCoach } from './interview-coach';
import { RecruiterMediaActions } from './RecruiterMediaActions';
import { RecruiterInterviewShell } from '../recruiter-coach/RecruiterInterviewShell';
import type { CoachDraft, CoachPerson } from '../recruiter-coach/coach-types';
import { RecruiterCoachNotesAdapter, RecruiterOriginalResume } from './RecruiterCoachNotesAdapter';
import './recruiter-coach-live.css';

/** Opening a scheduled interview never creates a room or enables capture. */
export function RecruiterInterviewWorkspace({ candidate, token, videoConfigured, onJoin, onClose, onUpdated }: {
  candidate: RecruiterCandidate; token: string; videoConfigured: boolean;
  onJoin: () => void; onClose: () => void; onUpdated: () => void | Promise<void>;
}) {
  const interview = candidate.interview!;
  const coach = useInterviewCoach({ candidateId: candidate.id, interviewId: interview.id, token });
  const [currentId, setCurrentId] = useState('');
  const [now, setNow] = useState(Date.now);
  const [joining, setJoining] = useState(false);
  const questions = coach.data?.active?.questions || [];
  const selectedId = currentId || coach.progress.currentId;
  const current = Math.max(0, questions.findIndex(q => q.id === selectedId));
  const completed = (coach.data?.interviewStatus || interview.status) === 'completed';
  const available = !completed && videoConfigured && now >= Date.parse(interview.start) - 15 * 60_000 && now <= Date.parse(interview.end) + 90 * 60_000;
  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const timer = window.setInterval(() => setNow(Date.now()), 15000);
    return () => { clearInterval(timer); document.body.style.overflow = previousOverflow; };
  }, []);
  const person: CoachPerson = { id: candidate.id, name: candidate.name, sourceSummary: '', promptSource: '', questions };
  const draft: CoachDraft = {
    current, status: {}, general: coach.progress.general || '', noteDocument: coach.progress.noteDocument,
    notes: Object.fromEntries(Object.entries(coach.progress.answers || {}).map(([id, answer]) => [id, answer.note])),
  };
  async function close() { if (await coach.flush()) onClose(); }
  async function join() {
    if (!available || joining) return;
    setJoining(true);
    if (await coach.flush()) onJoin();
    else setJoining(false);
  }
  const availabilityMessage = !videoConfigured ? 'Видеосвязь недоступна' : now < Date.parse(interview.start) - 15 * 60_000 ? 'Вход откроется за 15 минут до встречи' : 'Время подключения истекло';
  return <div className="ric-approved-shell" role="dialog" aria-modal="true" aria-label={`Интервью · ${candidate.name}`}>
    <RecruiterInterviewShell
      person={person} draft={draft}
      onChange={patch => { if (patch.current !== undefined && questions[patch.current]) setCurrentId(questions[patch.current].id); }}
      writable={false} phase={completed ? 'completed' : 'preview'}
      onClose={() => void close()} onFinish={() => void close()} onResume={() => void close()}
      finishLabel="Вернуться к интервью" finishDisabled={joining} closeDisabled={joining}
      mediaSlot={<div className="ric-approved-prejoin">
        <Video size={30} aria-hidden="true"/>
        <p>{completed ? 'Интервью завершено' : formatDate(interview.start)}</p>
        {!completed && <><button className="ric-approved-join" disabled={!available || joining} onClick={() => void join()}>{joining ? 'Подключаемся…' : interview.status === 'live' ? 'Вернуться в звонок' : 'Подключиться к звонку'}</button>{!available && <span>{availabilityMessage}</span>}</>}
      </div>}
      notice={coach.error ? <span role="alert">{coach.error}<button onClick={() => void (coach.dirty ? coach.flush() : coach.refresh())}>Повторить</button></span> : undefined}
      saveError={coach.error}
      resumeContent={<RecruiterOriginalResume token={token} candidateId={candidate.id} interviewId={interview.id}/>}
      stageToolbar={completed ? <div className="ric-approved-materials"><RecruiterMediaActions kind="interview" candidateId={candidate.id} status="completed" token={token} onUpdated={onUpdated}/></div> : undefined}
      notesContent={onSelectQuestion => <RecruiterCoachNotesAdapter coach={coach} token={token} candidateId={candidate.id} interviewId={interview.id} questions={questions} writable={completed} onSelectQuestion={onSelectQuestion}/>}
    />
  </div>;
}
