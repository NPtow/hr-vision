import type { EmployerReviewReceipt, Person } from '../connected-journey/api';
import type { IntakePerson } from './intake';

export type ReviewCandidate = { source: 'application'; id: string; person: Person }
  | { source: 'intake'; id: string; person: IntakePerson };
export type ReviewCandidateIdentity = Pick<ReviewCandidate, 'source' | 'id'>;
export const reviewCandidateKey = (candidate: ReviewCandidateIdentity) => `${candidate.source}:${candidate.id}`;

type ReviewSources = {
  vacancyId: string; people: readonly Person[]; intake: readonly IntakePerson[];
  receipts?: Readonly<Record<string, EmployerReviewReceipt>>;
};

/** Intake is already scoped by useIntake; application snapshots carry their own vacancy. */
export function candidateReviewQueue({ vacancyId, people, intake, receipts = {} }: ReviewSources): ReviewCandidate[] {
  if (!vacancyId) return [];
  const scoped = people.filter(person => person.vacancyId === vacancyId);
  const answered = new Set(Object.values(receipts).filter(receipt => receipt.vacancyId === vacancyId)
    .flatMap(receipt => [receipt.id, receipt.applicationId]));
  return [
    ...scoped.filter(person => person.reviewState === 'proposed' && !person.reviewResponse && !person.reviewRejection &&
      person.decision !== 'declined' && !answered.has(person.id) && !answered.has(person.applicationId || person.id))
      .map(person => ({ source: 'application' as const, id: person.id, person })),
    ...intake.filter(person => !person.archived && !person.reviewResponse && !person.reviewRejection &&
      !scoped.some(application => application.id === person.id || application.candidateId === person.id))
      .map(person => ({ source: 'intake' as const, id: person.id, person })),
  ];
}

export type ReviewQueueSession = { held: ReviewCandidate | null; acknowledged: readonly string[] };
export const emptyReviewQueueSession: ReviewQueueSession = { held: null, acknowledged: [] };
export function reviewQueueSession(session: ReviewQueueSession, event:
  | { type: 'submitting'; candidate: ReviewCandidate }
  | { type: 'confirmed'; candidate: ReviewCandidateIdentity }): ReviewQueueSession {
  const key = reviewCandidateKey(event.candidate);
  if (session.acknowledged.includes(key)) return session;
  if (event.type === 'submitting') return { ...session, held: event.candidate };
  return { held: session.held && reviewCandidateKey(session.held) !== key ? session.held : null,
    acknowledged: [...session.acknowledged, key] };
}

export function currentReviewCandidate(queue: readonly ReviewCandidate[], requested: ReviewCandidateIdentity | null,
  session: ReviewQueueSession = emptyReviewQueueSession): ReviewCandidate | undefined {
  // Keep the original response form after a failed/lost acknowledgement, even if polling
  // has already seen the committed decision. Its request ID and revision remain retryable.
  if (session.held) return queue.find(candidate => reviewCandidateKey(candidate) === reviewCandidateKey(session.held!)) || session.held;
  const remaining = queue.filter(candidate => !session.acknowledged.includes(reviewCandidateKey(candidate)));
  return (requested ? remaining.find(candidate => reviewCandidateKey(candidate) === reviewCandidateKey(requested)) : undefined)
    || remaining[0];
}
