/** HR Vision presentation boundary. Engine/provider DTOs must not reach React views. */
export type AssessmentEvidence = {
  id: string;
  text: string;
  time: string;
  seconds: number | null;
  sourceId?: string;
  turnIds?: number[];
  sourceKind?: 'interview' | 'resume';
  availability?: 'ready' | 'text_only' | 'unavailable';
  /** Human-readable, source-grounded interpretation; original text stays unchanged. */
  explanation?: string;
  feedbackQuestion?: string;
  endSeconds?: number;
};

export type AssessmentCriterion = {
  id: string;
  label: string;
  state: 'supported' | 'unknown' | 'conflict' | 'not_applicable';
  level: number | null;
  /** Null until a confirmed vacancy requirement revision is joined. */
  target: number | null;
  summary: string;
  missingReason?: string | null;
  evidence: AssessmentEvidence[];
  nextQuestion: string;
  basis: 'observed' | 'narrated' | 'hypothetical' | 'reflected' | null;
  stateLabel: string;
  limitation: string;
  /** Direct model estimate for this prototype, separate from the core-approved level. */
  modelEstimate?: {
    value: number;
    confidence: number;
    probabilities: number[];
    model: string;
    method: string;
    evidenceIds: string[];
  };
};

export type CandidateAssessmentView = {
  candidateId?: string;
  applicationId?: string;
  vacancyId?: string;
  revisionId: string;
  vacancyTitle: string;
  candidate: {
    name: string;
    role: string;
    city: string;
    salary: string;
    experience: string;
    contact: string;
    resumeText: string;
  };
  criteria: AssessmentCriterion[];
};

/** Employer annotation is independent of immutable engine output and hiring decisions. */
export type AssessmentFeedback = {
  criterionId: string;
  revisionId: string;
  evidenceIds: string[];
  kind: 'agree' | 'check' | 'disagree';
  text: string;
};
