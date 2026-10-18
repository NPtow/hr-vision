import { useEffect } from 'react';
import { candidateStatus, type Journey } from '../connected-journey/api';
import { CandidateStageWorkspace } from './CandidateStageWorkspace';
import { IntakeDetail } from './CandidateIntake';
import { EmployerCandidateWorkspace, sourceFor } from './EmployerCandidateWorkspace';
import { PeoplePanel } from './PeoplePanel';
import { SelectedCandidatesEmpty } from './SelectedCandidatesEmpty';
import { reviewCandidateKey, selectedReviewCandidates, type ReviewCandidateIdentity } from './candidate-review-queue';
import type { useIntake } from './intake';
import './candidate-review-queue.css';

export function SelectedCandidates({ journey, intake, requested, onSelect, onChooseCandidates, onChat, callActive, onCallChange }: {
  journey: Journey; intake: ReturnType<typeof useIntake>; requested: ReviewCandidateIdentity | null;
  onSelect: (candidate: ReviewCandidateIdentity) => void; onChooseCandidates: () => void;
  onChat: (id: string) => void; callActive: boolean; onCallChange: (active: boolean) => void;
}) {
  const state = journey.state!;
  const candidates = selectedReviewCandidates({ vacancyId: state.vacancy.id!, people: Object.values(state.candidates).filter(person => !!person), intake: intake.people });
  const selected = requested ? candidates.find(candidate => reviewCandidateKey(candidate) === reviewCandidateKey(requested)) : candidates[0];
  useEffect(() => {
    if (!requested && selected) onSelect({ source: selected.source, id: selected.id });
  }, [selected ? reviewCandidateKey(selected) : '', requested?.id, requested?.source]);
  if (!candidates.length) return <SelectedCandidatesEmpty onChoose={onChooseCandidates} error={intake.error}/>;
  const rows = candidates.map(candidate => ({ id: reviewCandidateKey(candidate), name: candidate.person.name,
    meta: candidate.person.role || '', status: candidate.source === 'application' ? candidateStatus(candidate.person) : 'Выбран для встречи' }));
  return <PeoplePanel className="ew-selected-candidates" title={state.vacancy.role} rows={rows}
    selected={selected ? reviewCandidateKey(selected) : ''} initialDetail={!!requested} disabled={journey.busy || callActive}
    onSelect={key => { const candidate = candidates.find(candidate => reviewCandidateKey(candidate) === key); if (candidate) onSelect(candidate); }} onAdd={() => {}}>
    {selected?.source === 'application' ? selected.person.reviewState === 'selected'
      ? <CandidateStageWorkspace embedded key={`${state.generation}:${selected.id}`} journey={journey} person={selected.person}
        source={sourceFor(selected.person)} callActive={callActive} onCallChange={onCallChange} onChat={() => onChat(selected.id)}/>
      : <EmployerCandidateWorkspace key={reviewCandidateKey(selected)} journey={journey} person={selected.person} source={sourceFor(selected.person)}/>
      : selected?.source === 'intake' ? <IntakeDetail key={reviewCandidateKey(selected)} person={selected.person} token={journey.token}
        vacancyId={state.vacancy.id} onArchived={() => { void intake.refresh(); }}/>
      : <section className="esc-unavailable"><h2>Кандидат недоступен</h2><p>Этого кандидата нет среди выбранных по этой вакансии.</p></section>}
    {intake.error && <p className="ew-inline-error" role="alert">{intake.error}</p>}
  </PeoplePanel>;
}
