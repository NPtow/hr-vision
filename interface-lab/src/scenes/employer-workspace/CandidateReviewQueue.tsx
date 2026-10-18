import { useEffect, useReducer, useRef } from 'react';
import { Users } from 'lucide-react';
import type { Journey } from '../connected-journey/api';
import { IntakeDetail } from './CandidateIntake';
import { EmployerCandidateWorkspace, sourceFor } from './EmployerCandidateWorkspace';
import type { useIntake } from './intake';
import { candidateReviewQueue, currentReviewCandidate, emptyReviewQueueSession, reviewCandidateKey, reviewQueueSession,
  type ReviewCandidateIdentity } from './candidate-review-queue';
import './candidate-review-queue.css';

export function CandidateReviewQueue({ journey, intake, requested, onCurrentChange }: {
  journey: Journey; intake: ReturnType<typeof useIntake>; requested: ReviewCandidateIdentity | null;
  onCurrentChange: (candidate: ReviewCandidateIdentity | null) => void;
}) {
  const [session, dispatch] = useReducer(reviewQueueSession, emptyReviewQueueSession);
  const root = useRef<HTMLElement>(null);
  const previous = useRef<string | null>(null);
  const state = journey.state!;
  const queue = candidateReviewQueue({ vacancyId: state.vacancy.id!, people: Object.values(state.candidates).filter(person => !!person),
    intake: intake.people, receipts: state.reviewResponses });
  const active = currentReviewCandidate(queue, requested, session);
  const activeKey = active ? reviewCandidateKey(active) : '';
  useEffect(() => {
    onCurrentChange(active ? { source: active.source, id: active.id } : null);
    if (previous.current !== null && previous.current !== activeKey) {
      root.current?.focus({ preventScroll: true });
      window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
    }
    previous.current = activeKey;
  }, [activeKey]);

  function confirmed() {
    if (!active) return;
    dispatch({ type: 'confirmed', candidate: active });
    void intake.refresh(); void journey.refresh();
  }
  return <main className="ew-review-queue" ref={root} tabIndex={-1} aria-label="Просмотр кандидата">
    <section className="ew-review-card">
      {active?.source === 'application' ? <EmployerCandidateWorkspace key={activeKey} journey={journey} person={active.person}
        source={sourceFor(active.person)} onResponseStart={() => dispatch({ type: 'submitting', candidate: active })} onResponded={confirmed}/>
        : active?.source === 'intake' ? <IntakeDetail key={activeKey} person={active.person} token={journey.token}
          vacancyId={state.vacancy.id} onArchived={() => { void intake.refresh(); }}
          onResponseStart={() => dispatch({ type: 'submitting', candidate: active })} onResponded={confirmed}/>
        : <div className="ew-review-empty" role="status"><Users size={30} aria-hidden="true"/><h2>Пока нет новых кандидатов</h2></div>}
      {intake.error && <p className="ew-inline-error" role="alert">{intake.error}</p>}
    </section>
  </main>;
}
