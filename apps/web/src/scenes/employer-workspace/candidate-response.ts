import { request, type EmployerReviewResponse, type RejectionAnswers, type SharedState } from '../connected-journey/api';
import type { IntakePerson } from './intake';
import type { EmployerCandidateDecision, EmployerCandidateResponseValues } from './employer-candidate-profile-types';

type ResponseIdentity = { source: 'application'; id: string; vacancyId: string; generation: string }
  | { source: 'intake'; id: string; vacancyId: string };
export type CandidateResponseResult = {
  response: EmployerReviewResponse;
  reviewRejection?: (RejectionAnswers & { createdAt?: string }) | null;
  state?: SharedState;
  candidates?: IntakePerson[];
};

export function candidateDecision(response?: EmployerReviewResponse | null,
  feedback?: (RejectionAnswers & { createdAt?: string }) | null): EmployerCandidateDecision | null {
  if (response) return { ...response, feedback };
  return feedback ? { decision: 'reject', feedback, createdAt: feedback.createdAt } : null;
}

export function respondToCandidate(token: string, identity: ResponseIdentity, values: EmployerCandidateResponseValues) {
  const { decision, expectedRevision, requestId, reason, missing, improve, meetingStart } = values;
  return request<CandidateResponseResult>('employer/review/respond', token, {
    ...identity, decision, expectedRevision, requestId,
    ...(decision === 'invite' && meetingStart ? { meetingStart } : {}),
    ...(decision === 'reject' ? { rejectionAnswers: { reason, missing, improve } } : {}),
  });
}
