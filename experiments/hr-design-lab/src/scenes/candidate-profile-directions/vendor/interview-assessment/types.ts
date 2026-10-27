/** Presentation data from the authorised recording adapter, never provider DTOs. */
export type AssessmentEvidence = {
  id: string;
  text: string;
  time: string;
  seconds: number | null;
  endSeconds?: number;
  sourceId?: string;
  /** Clock and voice identity are local to this immutable recording/version. */
  recordingId?: string;
  transcriptId?: string;
  turnIds?: number[];
  sourceKind?: 'interview' | 'resume';
  availability?: 'ready' | 'text_only' | 'unavailable';
  explanation?: string;
  /** A source-grounded question supplied by the assessment revision. */
  feedbackQuestion?: string;
};

export type AssessmentCriterion = {
  id: string;
  label: string;
  state: 'supported' | 'unknown' | 'conflict' | 'not_applicable';
  level: number | null;
  target: number | null;
  summary: string;
  evidence: AssessmentEvidence[];
  missingReason?: string | null;
  nextQuestion?: string;
  basis?: 'observed' | 'narrated' | 'hypothetical' | 'reflected' | null;
  stateLabel?: string;
  limitation?: string;
  /** A model score and distribution are distinct from a confirmed level. */
  modelEstimate?: {
    value: number;
    confidence: number;
    probabilities: number[];
    model: string;
    method: string;
    evidenceIds: string[];
  };
};

export type FeedbackTarget = { criterionId: string; evidenceId: string };
export type FragmentOpinion = { answer?: 'yes' | 'no' | 'unknown'; comment: string };
export type FragmentFeedback = FeedbackTarget & FragmentOpinion & { version: number };
export type AssessmentPresentation = {
  revisionId: string;
  criteria: AssessmentCriterion[];
  feedback: FragmentFeedback[];
  vacancyMatch?: { revisionId: string; requiresReview: true; criteria: { id: string; label: string; status: 'evidenced' | 'clarify' | 'not_required' }[] };
  saveFeedback: (target: FeedbackTarget, opinion: FragmentOpinion, expectedVersion: number) => Promise<FragmentFeedback>;
};

/** The adapter throws this on HTTP 409 with the current server version. */
export class AssessmentFeedbackConflictError extends Error {
  readonly status = 409;
  readonly current: FragmentFeedback;
  constructor(current: FragmentFeedback) {
    super('Ответ был изменён в другом окне');
    this.name = 'AssessmentFeedbackConflictError';
    this.current = current;
  }
}
