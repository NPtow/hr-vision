import type { FeedbackAnswer, FeedbackQuestion, Meeting, Offer } from '../connected-journey/api';

export type CandidateStageKind = 'interview' | 'meeting' | 'test' | 'offer';
export type CandidateStageDocument = { id: string; title: string; url: string };
export type CandidateStageDocumentInput = { id?: string; title: string; url: string };
export type CandidateTestStatus = 'assigned' | 'in_progress' | 'completed' | 'cancelled';
export type CandidateTest = {
  id: string; title: string; instructions: string; status: CandidateTestStatus;
  result: string; note: string; dueAt?: string | null; createdAt: string; updatedAt: string;
};
export type CandidateStage = {
  id: string; kind: CandidateStageKind; title: string; status: string; date?: string;
  notes: string; documents: CandidateStageDocument[]; meetingId?: string;
  meeting?: Meeting; offer?: Offer; test?: CandidateTest;
  feedback?: { example?: string; doubts?: string; check?: string; at: string; questions?: FeedbackQuestion[]; answers?: Record<string, FeedbackAnswer> } | null;
};
export type CandidateStagePerson = {
  id: string; name: string; role?: string; photo?: string | null; meta?: string; preferences?: string;
};
export type CandidateStageNotes = { notes: string; result?: string; status?: CandidateTestStatus; expectedRevision: number };
export type CandidateTestValues = {
  title: string; instructions: string; dueAt: string; note: string; documents: CandidateStageDocumentInput[];
};
export type CandidateOfferValues = {
  role: string; compensation: string; startDate: string; format: string; expectations: string;
  documents: CandidateStageDocumentInput[];
};
